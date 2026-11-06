import json, time
import pytest, requests
from engine import scraper as S
from engine.resilience import Breaker

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Brent crude jumps 4% as Hormuz tensions flare</title><link>http://x/1</link><description>d1</description><pubDate>Mon, 20 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>RBI holds repo rate steady at 6.5%</title><link>http://x/2</link><description>d2</description></item></channel></rss>"""


class R:
    def __init__(self, status=200, content=b"", headers=None, text=None):
        self.status_code, self.content, self.headers = status, content, headers or {}
        self.text = text if text is not None else content.decode("utf-8", "ignore")
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))
    def json(self): return json.loads(self.text)


class Sess:
    def __init__(self, *responses): self.q, self.calls = list(responses), []
    def get(self, url, **kw):
        self.calls.append((url, kw))
        r = self.q.pop(0) if len(self.q) > 1 else self.q[0]
        if isinstance(r, Exception): raise r
        return r


def test_get_retries_with_backoff_then_succeeds():
    sleeps, s = [], Sess(R(503), R(503), R(200, b"ok"))
    r = S._get(s, "http://u", sleep=sleeps.append)
    assert r.status_code == 200 and len(s.calls) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]
    assert all(c[1]["timeout"] == S.TIMEOUT for c in s.calls)                    # EVERY call has a timeout


def test_get_gives_up_on_network_errors_and_returns_last_bad_status():
    with pytest.raises(requests.ConnectionError):
        S._get(Sess(requests.ConnectionError("x")), "http://u", sleep=lambda d: None)
    assert S._get(Sess(R(503)), "http://u", sleep=lambda d: None).status_code == 503


def test_get_honours_retry_after():
    sleeps = []
    S._get(Sess(R(429, headers={"Retry-After": "4"}), R(200)), "http://u", sleep=sleeps.append)
    assert 4 <= sleeps[0] < 5


def test_feed_parsed_from_bytes_with_conditional_get(monkeypatch):
    monkeypatch.setattr(S, "_FEED_CACHE", {})
    s = Sess(R(200, RSS, {"ETag": "abc", "Last-Modified": "Mon"}), R(304))
    first = S._from_feed("http://feed", "ET", "India", s)
    assert [i["title"][:5] for i in first] == ["Brent", "RBI h"] and first[0]["published_at"] and first[1]["published_estimated"]
    again = S._from_feed("http://feed", "ET", "India", s)
    assert again == first and s.calls[1][1]["headers"] == {"If-None-Match": "abc", "If-Modified-Since": "Mon"}   # 304 -> cached items kept


def test_robots_rfc9309_rules(monkeypatch):
    monkeypatch.setattr(S, "_ROBOTS", {}); monkeypatch.setattr(S, "RESPECT_ROBOTS", True)
    disallow = Sess(R(200, text="User-agent: *\nDisallow: /markets/"))
    assert S._robots_ok(disallow, "https://a.com/markets/x") is False and S._robots_ok(disallow, "https://a.com/news/x") is True
    assert S._robots_ok(Sess(R(404)), "https://b.com/anything") is True               # 4xx -> allowed
    assert S._robots_ok(Sess(R(503)), "https://c.com/anything") is False              # 5xx -> disallowed
    assert S._robots_ok(Sess(requests.ConnectionError("x")), "https://d.com/a") is False


def test_nse_api_uses_real_link_and_parses_date():
    rows = {"data": [{"symbol": "ABC", "desc": "Board meeting outcome", "an_dt": "20-Oct-2026 10:15:00"}]}
    items = S._nse_api(Sess(R(200, b"{}"), R(200, text=json.dumps(rows))))
    assert items[0]["url"] == S.NSE_PAGE and items[0]["published_at"].startswith("2026-10-20")          # url was previously the DATE


def fake_sources(names): return [{"name": n, "region": "India"} for n in names]


def fake_task(delays, fail=()):
    def task(spec, make_session=None):
        time.sleep(delays.get(spec["name"], 0))
        if spec["name"] in fail: return [], ["boom"]
        return [{"id": spec["name"], "source": spec["name"], "title": f"{spec['name']} headline", "url": "u", "fetched_at": "2026-10-20T00:00:00+00:00", "published_at": None}], []
    return task


def test_sources_run_in_parallel_and_breaker_opens(monkeypatch):
    monkeypatch.setattr(S, "_scrape_source", fake_task({"A": .3, "B": .3, "C": .3}, fail={"C"}))
    br, t0 = Breaker(threshold=2), time.time()
    items, errors, cov = S.scrape_all(fake_sources("ABC"), breaker=br)
    assert time.time() - t0 < 0.8 and {c["source"]: c["state"] for c in cov} == {"A": "ok", "B": "ok", "C": "empty"}     # 3x0.3s in ~0.3s
    S.scrape_all(fake_sources("ABC"), breaker=br)                                                                        # second failure -> trips
    _, _, cov3 = S.scrape_all(fake_sources("ABC"), breaker=br)
    c = next(x for x in cov3 if x["source"] == "C")
    assert c["state"] == "open" and c["retry_in_s"] > 0 and len(items) == 2


def test_hanging_source_cannot_stall_the_cycle(monkeypatch):
    monkeypatch.setattr(S, "_scrape_source", fake_task({"SLOW": 3.0}))
    br, t0 = Breaker(), time.time()
    items, errors, cov = S.scrape_all(fake_sources(["FAST", "SLOW"]), breaker=br, deadline_s=0.4)
    assert time.time() - t0 < 1.5
    assert {c["source"]: c["state"] for c in cov} == {"FAST": "ok", "SLOW": "deadline"} and any("deadline" in e for e in errors) and len(items) == 1


def test_breaker_cooldown_escalates_and_half_opens():
    now = [1000.0]; b = Breaker(threshold=2, clock=lambda: now[0])
    b.failure("s"); assert b.allow("s"); b.failure("s"); assert not b.allow("s") and b.retry_in("s") == 300
    now[0] += 301; assert b.allow("s")                                   # half-open probe
    b.failure("s"); b.failure("s"); assert b.retry_in("s") == 900        # escalates
    b.success("s"); assert b.allow("s") and b.retry_in("s") == 0


def test_honest_user_agent_by_default_browser_ua_only_for_nse_and_paywalled_sites_are_rss_only():
    assert S._session().headers["User-Agent"].startswith("VittaMantri/") and S._session(browser=True).headers["User-Agent"].startswith("Mozilla/")
    by = {s["name"]: s for s in S.SOURCES}
    assert by["NSE"].get("browser_ua") and [n for n, s in by.items() if s.get("browser_ua")] == ["NSE"]
    assert all(by[n]["pages"] == [] and by[n]["feeds"] for n in ("Bloomberg", "WSJ", "Financial Times"))          # headlines via RSS only
    used = []
    S._scrape_source(by["NSE"], make_session=lambda browser=False: used.append(browser) or Sess(R(404)))
    S._scrape_source(by["Reuters"], make_session=lambda browser=False: used.append(browser) or Sess(R(404)))
    assert used == [True, False]
