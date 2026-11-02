import json, os, random
from datetime import date
import pytest
from engine import bars as B, chartgate as G, assets_feed as A, ipo_feed as F
from engine.technicals import Bar


def series(n=300, drift=0.0015, noise=0.008, seed=7, start=100.0):
    r, c, out = random.Random(seed), start, []
    for _ in range(n):
        c *= 1 + drift + r.gauss(0, noise); out.append(c)
    return out


def chart_json(closes, price=None, sym="X"):
    ts = [1_700_000_000 + 86400 * i for i in range(len(closes))]
    q = {"open": [c * 0.998 for c in closes], "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes], "close": closes, "volume": [1e6] * len(closes)}
    return json.dumps({"chart": {"result": [{"meta": {"symbol": sym, "currency": "USD", "regularMarketPrice": price or closes[-1]}, "timestamp": ts, "indicators": {"quote": [q]}}]}})


def bars_of(closes): return [Bar(date(2024, 1, 1), c * 0.998, c * 1.01, c * 0.99, c, 1e6) for c in closes]
def fake_fetch(closes_by_sym):
    def f(sym):
        c = closes_by_sym.get(sym)
        return None if c is None else {"bars": bars_of(c), "price": c[-1], "prev_close": c[-2], "change_pct": (c[-1] / c[-2] - 1) * 100, "symbol": sym, "currency": ""}
    return f


def test_parse_chart_drops_holes_and_computes_change():
    c = series(80); body = json.loads(chart_json(c)); body["chart"]["result"][0]["indicators"]["quote"][0]["close"][10] = None
    d = B.parse_yahoo_chart(json.dumps(body))
    assert len(d["bars"]) == 79 and d["change_pct"] == pytest.approx((c[-1] / c[-2] - 1) * 100)
    assert B.parse_yahoo_chart("{\"chart\":{\"result\":null}}") is None and B.yahoo_symbol("TCS") == "TCS.NS" and B.yahoo_symbol("AAPL", "US") == "AAPL"


def test_fetch_bars_caches_success_and_failure(monkeypatch):
    monkeypatch.setattr(B, "_CACHE", {})
    class R:
        text = chart_json(series(80)); status = 200
        def raise_for_status(s): pass
    class S:
        n = 0
        def get(s, url, **k):
            S.n += 1
            if "BAD" in url: raise OSError("x")
            return R()
    s = S(); assert B.fetch_bars("OK", s) and B.fetch_bars("OK", s) and S.n == 1
    assert B.fetch_bars("BAD", s) is None and B.fetch_bars("BAD", s) is None and S.n == 2


def row(t, a, conv=4): return {"ticker": t, "action": a, "conviction": conv, "why": "w", "net": 2}


def test_enrich_board_confirms_downgrades_and_degrades():
    up, down = series(300, 0.002), series(300, -0.003, seed=3)
    board = G.enrich_board([row("UPCO", "BUY"), row("DOWNCO", "BUY"), row("NODATA", "SELL"), row("WATCHER", "WATCH")],
                           fetch=fake_fetch({"UPCO.NS": up, "DOWNCO.NS": down}))
    by = {r["ticker"]: r for r in board}
    assert by["UPCO"]["chart"]["verdict"] in ("CONFIRM", "NEUTRAL", "WAIT")
    assert by["DOWNCO"]["action"] == "WATCH" and by["DOWNCO"]["news_action"] == "BUY" and by["DOWNCO"]["chart"]["verdict"] == "CONTRADICT"
    assert by["NODATA"]["chart"]["verdict"] == "UNAVAILABLE" and by["NODATA"]["action"] == "SELL"
    assert "chart" not in by["WATCHER"]


def test_unavailable_chart_can_be_made_blocking():
    b = G.enrich_board([row("NODATA", "BUY")], fetch=fake_fetch({}), require=True)
    assert b[0]["action"] == "WATCH" and b[0]["news_action"] == "BUY"


def test_asset_bias_needs_corroboration_and_inverts_yields():
    cfg = {"gold_bid": {"label": "Gold bid", "rules": [{"hop": "direct", "action": "BUY", "tags": ["gold"], "why": "w"}]},
           "yields_up": {"label": "Yields up", "rules": [{"hop": "direct", "action": "SELL", "tags": ["bond"], "why": "w"}]}}
    assert A.news_bias({"gold_bid": 1}, cfg, ["gold"])[1] == "WATCH"                      # one headline is not enough
    assert A.news_bias({"gold_bid": 2}, cfg, ["gold"])[1] == "BUY"
    assert A.news_bias({"yields_up": 2}, cfg, ["bond"])[1] == "SELL"                       # bond price falls...
    assert A.news_bias({"yields_up": 2}, cfg, ["bond"], invert=True)[1] == "BUY"           # ...so the yield series rises


def test_build_assets_end_to_end_with_gate_and_missing_feed():
    cfg = {"gold_bid": {"label": "Gold bid", "rules": [{"hop": "direct", "action": "BUY", "tags": ["gold"], "why": "Safe haven"}]}}
    items = [{"key": "gold", "label": "Gold", "cls": "Commodity", "yahoo": "GC=F", "tags": ["gold"]}, {"key": "x", "label": "Missing", "cls": "Index", "yahoo": "NOPE", "tags": []}]
    rows, cov = A.build_assets([{"theme_id": "gold_bid"}] * 2, fetch=fake_fetch({"GC=F": series(300, 0.002)}), cfg=cfg, items=items)
    g = next(r for r in rows if r["key"] == "gold"); m = next(r for r in rows if r["key"] == "x")
    assert g["available"] and len(g["spark"]) == 40 and g["chart"]["verdict"] and g["news_score"] >= 1.5
    assert m["available"] is False and cov == [{"source": "Assets: Yahoo", "raw": 1}]


def test_ipo_parsers_and_merge_precedence():
    nse = json.dumps([{"companyName": "Acme Fasteners Limited", "issueStartDate": "20-Oct-2026", "issueEndDate": "23-Oct-2026", "issuePrice": "Rs.100 to Rs.105", "lotSize": "142", "noOfTimeSubscribed": "12.5"}])
    n = F.parse_nse_ipo(nse, today=date(2026, 10, 21))[0]
    assert n.status == "open" and n.price_band == (100.0, 105.0) and n.lot_size == 142 and n.sub_total == 12.5
    f = F.parse_ipo_file('[{"name":"Acme Fasteners","pe":18,"peer_pe":25,"profitable":"yes","roe":"19%","rev_growth_pct":22,"debt_equity":0.4,"issue_size_cr":1000,"ofs_cr":200,"promoter_post_pct":68}]')[0]
    news = F.ipo_from_news([{"title": "Acme Fasteners IPO day 3: subscribed 45 times, GMP Rs 20 at price band Rs 100-105", "url": "http://n/1"}, {"title": "Nifty ends higher"}])
    assert len(news) == 1 and news[0].sub_total == 45 and news[0].gmp_pct == pytest.approx(19.0, abs=0.1) and news[0].status == "open"
    merged = F.merge(("file", [f]), ("nse", [n]), ("news", news))
    assert len(merged) == 1
    ipo, srcs = next(iter(merged.values()))
    assert srcs == ["file", "news", "nse"] and ipo.pe == 18 and ipo.lot_size == 142 and ipo.sub_total == 12.5 and ipo.price_band == (100.0, 105.0) and ipo.url == "http://n/1"


def test_build_ipos_isolates_nse_failure_and_scores_with_files(tmp_path, monkeypatch):
    (tmp_path / "ipos.json").write_text(json.dumps([{"name": "GoodCo", "status": "closed", "pe": 18, "peer_pe": 25, "profitable": True, "rev_growth_pct": 22, "roe": 19, "debt_equity": 0.4,
                                                     "issue_size_cr": 1000, "ofs_cr": 200, "promoter_post_pct": 68, "sub_total": 45, "sub_qib": 60}]))
    class S:
        def get(s, *a, **k): raise OSError("blocked")
    out, cov = F.build_ipos([{"title": "Zed Tech IPO to open on Monday, price band Rs 50-52"}], session=S(), ipo_dir=str(tmp_path))
    by = {d["name"]: d for d in out}
    assert by["GoodCo"]["verdict"] == "FAVOURABLE" and by["Zed Tech"]["verdict"] == "INSUFFICIENT DATA" and by["Zed Tech"]["status"] == "upcoming"
    assert {"source": "IPO: NSE", "raw": 0, "error": "OSError"} in cov and {"source": "IPO: files", "raw": 1} in cov


def test_app_endpoints(monkeypatch):
    import app
    c = app.app.test_client()
    monkeypatch.delenv("VM_ADMIN_TOKEN", raising=False)
    assert c.get("/api/refresh").status_code == 405                                         # no more unauthenticated GET scrape
    monkeypatch.setenv("VM_ADMIN_TOKEN", "s3cret")
    assert c.post("/api/refresh").status_code == 401 and c.post("/api/refresh", headers={"X-Admin-Token": "bad"}).status_code == 401
    monkeypatch.setattr(app, "chart_for", lambda t, a, m: {"close": [1, 2], "dates": ["a", "b"]} if t == "OK" else None)
    assert c.get("/api/chart/ok?action=SELL").get_json()["close"] == [1, 2] and c.get("/api/chart/zzz").status_code == 404
    snap = c.get("/api/briefing").get_json(); assert "assets" in snap and "ipos" in snap
