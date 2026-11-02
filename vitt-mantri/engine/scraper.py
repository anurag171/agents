import hashlib
import os
import random
import re
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout, as_completed
from urllib.parse import urlparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import feedparser
import requests
from bs4 import BeautifulSoup

from .resilience import Breaker

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US,en;q=0.9",
}

HEADERS["User-Agent"] = os.getenv("VM_USER_AGENT", HEADERS["User-Agent"])    # set an honest UA (with contact) where sources allow it
TIMEOUT = (3.05, 10)                                                          # (connect, read) seconds, on EVERY request
RETRIES, RETRY_STATUS = 2, {429, 500, 502, 503, 504}
MAX_WORKERS = int(os.getenv("VM_SCRAPE_WORKERS", "8"))
DEADLINE_S = float(os.getenv("VM_SCRAPE_DEADLINE_S", "45"))
RESPECT_ROBOTS = os.getenv("VM_RESPECT_ROBOTS", "1") != "0"
BREAKER = Breaker(threshold=3)
_sleep = time.sleep

SOURCES = [
    {
        "name": "Yahoo Finance",
        "region": "Global",
        "feeds": [
            "https://finance.yahoo.com/news/rssindex",
            "https://finance.yahoo.com/rss/topstories",
            "https://www.yahoo.com/news/rss/finance",
        ],
        "pages": [
            "https://finance.yahoo.com/topic/stock-market-news/",
            "https://finance.yahoo.com/news/",
        ],
        "host": "yahoo.com",
        "path_any": ("/news/", "/article/", "/economy/"),
        "base": "https://finance.yahoo.com",
    },
    {
        "name": "Economic Times",
        "region": "India",
        "feeds": [
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
            "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
        ],
        "pages": [
            "https://economictimes.indiatimes.com/markets/stocks/news",
            "https://economictimes.indiatimes.com/markets",
        ],
        "host": "economictimes.indiatimes.com",
        "path_any": ("articleshow", "liveblog", "slideshow"),
        "base": "https://economictimes.indiatimes.com",
    },
    {
        "name": "Moneycontrol",
        "region": "India",
        "feeds": [
            "https://www.moneycontrol.com/rss/latestnews.xml",
            "https://www.moneycontrol.com/rss/MCtopnews.xml",
            "https://www.moneycontrol.com/rss/marketreports.xml",
            "https://www.moneycontrol.com/rss/business.xml",
        ],
        "pages": [
            "https://www.moneycontrol.com/news/business/markets/",
            "https://www.moneycontrol.com/news/business/",
        ],
        "host": "moneycontrol.com",
        "path_any": ("/news/", "article"),
        "base": "https://www.moneycontrol.com",
    },
    {
        "name": "CNBC-TV18",
        "region": "India",
        "feeds": [
            "https://www.cnbctv18.com/commonfeeds/v1/eng/rss/latest.xml",
            "https://www.cnbctv18.com/market/rss/",
        ],
        "pages": [
            "https://www.cnbctv18.com/market/",
            "https://www.cnbctv18.com/business/",
        ],
        "host": "cnbctv18.com",
        "path_any": ("/market/", "/business/", "/economy/", "/photos/"),
        "base": "https://www.cnbctv18.com",
    },
    {
        "name": "Business Standard",
        "region": "India",
        "feeds": [
            "https://www.business-standard.com/rss/markets-106.rss",
            "https://www.business-standard.com/rss/home_page_top_stories.rss",
            "https://www.business-standard.com/rss/latest.rss",
        ],
        "pages": [
            "https://www.business-standard.com/markets",
            "https://www.business-standard.com/markets/news",
        ],
        "host": "business-standard.com",
        "path_any": ("/markets/", "/finance/", "/companies/", "/economy/"),
        "base": "https://www.business-standard.com",
    },
    {
        "name": "MSN Money",
        "region": "India",
        "feeds": [
            "https://rss.msn.com/en-in",
        ],
        "pages": [
            "https://www.msn.com/en-in/money",
            "https://www.msn.com/en-in/money/markets",
            "https://www.msn.com/en-in/money/top-stories",
        ],
        "host": "msn.com",
        "path_any": ("/money/", "/news/"),
        "base": "https://www.msn.com",
    },
    {
        "name": "NSE",
        "region": "India",
        "feeds": [
            "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
            "https://nsearchives.nseindia.com/content/RSS/Corporate_announcements.xml",
        ],
        "pages": [
            "https://www.nseindia.com/resources/exchange-communication-press-releases",
            "https://www.nseindia.com/resources/exchange-communication-circulars",
        ],
        "host": "nseindia.com",
        "path_any": ("/resources/", "/companies-listing/", "/market-data/", "announcement"),
        "base": "https://www.nseindia.com",
        "bootstrap": "https://www.nseindia.com",
    },
    {
        "name": "MarketWatch",
        "region": "Global",
        "feeds": [
            "https://feeds.marketwatch.com/marketwatch/topstories/",
            "https://feeds.marketwatch.com/marketwatch/marketpulse/",
            "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/",
        ],
        "pages": [
            "https://www.marketwatch.com/latest-news",
            "https://www.marketwatch.com/markets",
        ],
        "host": "marketwatch.com",
        "path_any": ("/story/", "/articles/", "/markets/"),
        "base": "https://www.marketwatch.com",
    },
    {
        "name": "Investing.com",
        "region": "Global",
        "feeds": [
            "https://www.investing.com/rss/news.rss",
            "https://www.investing.com/rss/news_25.rss",
            "https://www.investing.com/rss/news_285.rss",
        ],
        "pages": [
            "https://www.investing.com/news/stock-market-news",
            "https://www.investing.com/news/latest-news",
        ],
        "host": "investing.com",
        "path_any": ("/news/", "/pro/"),
        "base": "https://www.investing.com",
    },
    {
        "name": "Seeking Alpha",
        "region": "Global",
        "feeds": [
            "https://seekingalpha.com/market_currents.xml",
            "https://seekingalpha.com/feed.xml",
            "https://seekingalpha.com/tag/market-outlook.xml",
        ],
        "pages": [
            "https://seekingalpha.com/market-news",
        ],
        "host": "seekingalpha.com",
        "path_any": ("/news/", "/article/", "/market-news/"),
        "base": "https://seekingalpha.com",
    },
    {
        "name": "Bloomberg",
        "region": "Global",
        "feeds": [
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://feeds.bloomberg.com/economics/news.rss",
        ],
        "pages": [
            "https://www.bloomberg.com/markets",
            "https://www.bloomberg.com/economics",
        ],
        "host": "bloomberg.com",
        "path_any": ("/news/", "/articles/"),
        "base": "https://www.bloomberg.com",
    },
    {
        "name": "Reuters",
        "region": "Global",
        "feeds": [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.reuters.com/news/wealth",
            "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        ],
        "pages": [
            "https://www.reuters.com/markets/",
            "https://www.reuters.com/business/",
        ],
        "host": "reuters.com",
        "path_any": ("/markets/", "/business/", "/world/"),
        "base": "https://www.reuters.com",
    },
    {
        "name": "Financial Times",
        "region": "Global",
        "feeds": [
            "https://www.ft.com/rss/home",
            "https://www.ft.com/markets?format=rss",
        ],
        "pages": [
            "https://www.ft.com/markets",
        ],
        "host": "ft.com",
        "path_any": ("/content/", "/markets"),
        "base": "https://www.ft.com",
    },
    {
        "name": "WSJ",
        "region": "Global",
        "feeds": [
            "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
            "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
            "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        ],
        "pages": [
            "https://www.wsj.com/finance",
            "https://www.wsj.com/news/markets",
        ],
        "host": "wsj.com",
        "path_any": ("/articles/", "/finance/", "/markets/"),
        "base": "https://www.wsj.com",
    },
]


def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _clean(text):
    if not text:
        return ""
    text = unescape(re.sub(r"<[^>]+>", " ", str(text)))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _uid(source, title, url):
    raw = f"{source}|{title}|{url}".encode("utf-8", "ignore")
    return hashlib.sha1(raw).hexdigest()[:16]


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    text = str(value).strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y",
        "%d %b %Y, %I:%M %p IST",
        "%d %b, %Y, %I:%M %p IST",
        "%b %d, %Y, %I:%M %p IST",
    ):
        try:
            dt = datetime.strptime(text.replace("Z", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _item(source, region, title, url, summary="", published=None):
    return {
        "published_estimated": published is None,
        "id": _uid(source, title, url),
        "source": source,
        "region": region,
        "title": title,
        "url": url,
        "summary": (summary or "")[:500],
        "published_at": published.isoformat() if published else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _get(session, url, *, headers=None, params=None, timeout=TIMEOUT, retries=RETRIES, sleep=None):
    """GET with timeout, jittered exponential backoff, Retry-After support. Returns the last response (even a bad
    status, after retries) or raises the last network error."""
    sleep, last_exc = sleep or _sleep, None
    for attempt in range(retries + 1):
        delay = 0.0
        try:
            r = session.get(url, headers=headers, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if r.status_code not in RETRY_STATUS or attempt == retries:
                return r
            ra = (getattr(r, "headers", None) or {}).get("Retry-After", "")
            delay = float(ra) if str(ra).isdigit() else 0.0
            last_exc = None
        if attempt < retries:
            sleep(min(10.0, max(delay, 0.5 * 2 ** attempt)) + random.random() * 0.3)
    raise last_exc or requests.RequestException(f"{url}: retries exhausted")


_FEED_CACHE, _CACHE_LOCK = {}, threading.Lock()      # url -> {"etag","lm","items"}  (conditional GET)
_ROBOTS, _ROBOTS_TTL = {}, 6 * 3600


def _robots_ok(session, url):
    """RFC 9309: 200 -> obey rules; 4xx -> everything allowed; 5xx/unreachable -> treat as disallowed."""
    if not RESPECT_ROBOTS:
        return True
    host = "{0.scheme}://{0.netloc}".format(urlparse(url))
    with _CACHE_LOCK:
        hit = _ROBOTS.get(host)
    if not hit or time.time() - hit[0] > _ROBOTS_TTL:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = _get(session, host + "/robots.txt", timeout=(3.05, 5), retries=0)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif 400 <= r.status_code < 500:
                rp.parse([])
            else:
                rp.parse(["User-agent: *", "Disallow: /"])
        except requests.RequestException:
            rp.parse(["User-agent: *", "Disallow: /"])
        hit = (time.time(), rp)
        with _CACHE_LOCK:
            _ROBOTS[host] = hit
    return hit[1].can_fetch("VittaMantri", url)


def _from_feed(url, source, region, session=None):
    session = session or _session()
    with _CACHE_LOCK:
        cached = _FEED_CACHE.get(url)
    cond = {}
    if cached:
        if cached["etag"]: cond["If-None-Match"] = cached["etag"]
        if cached["lm"]: cond["If-Modified-Since"] = cached["lm"]
    r = _get(session, url, headers=cond or None)
    if r.status_code == 304 and cached:
        return list(cached["items"])
    r.raise_for_status()
    items = []
    parsed = feedparser.parse(r.content)                 # parse bytes: no hidden network call, so no hang
    for e in parsed.entries[:40]:
        title = _clean(e.get("title"))
        link = (e.get("link") or "").strip()
        summary = _clean(e.get("summary") or e.get("description") or "")
        published = None
        if e.get("published_parsed"):
            try:
                published = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
            except (OverflowError, ValueError, OSError):
                published = None
        if not published:
            published = _parse_time(e.get("published") or e.get("updated"))
        if not title or not link:
            continue
        items.append(_item(source, region, title, link, summary, published))
    with _CACHE_LOCK:
        _FEED_CACHE[url] = {"etag": r.headers.get("ETag"), "lm": r.headers.get("Last-Modified"), "items": items}
    return items


def _html_links(session, spec):
    items = []
    bootstrap = spec.get("bootstrap")
    if bootstrap:
        try:
            _get(session, bootstrap, retries=0)
        except requests.RequestException:
            pass
    for page in spec.get("pages") or []:
        try:
            if not _robots_ok(session, page):
                continue
            r = _get(session, page)
            if r.status_code != 200:
                continue
        except requests.RequestException:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a"):
            href = a.get("href") or ""
            title = _clean(a.get_text())
            if not title or len(title) < 28:
                continue
            if href.startswith("/"):
                href = spec["base"] + href
            href = href.split("?")[0]
            if spec["host"] not in href:
                continue
            if not any(p in href for p in spec["path_any"]):
                continue
            if href.startswith("javascript:"):
                continue
            items.append(_item(spec["name"], spec["region"], title, href))
    return items


NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"


def _nse_api(session):
    items = []
    try:
        _get(session, "https://www.nseindia.com", retries=0)
        r = _get(session, "https://www.nseindia.com/api/corporate-announcements", params={"index": "equities"}, headers={"Referer": NSE_PAGE})
        if r.status_code != 200:
            return items
        payload = r.json()
        rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("announcements") or []
        for row in rows[:40]:
            if not isinstance(row, dict):
                continue
            title = _clean(row.get("desc") or row.get("subject") or row.get("attchmntText") or "")
            company = _clean(row.get("symbol") or row.get("sm_name") or "")
            if company and title:
                title = f"{company}: {title}"
            url = row.get("attchmntFile") or NSE_PAGE                 # (was: an_dt, a DATE, used as the link)
            if isinstance(url, str) and url.startswith("/"):
                url = "https://www.nseindia.com" + url
            if not title:
                continue
            items.append(_item("NSE", "India", title, url, "", _parse_time(row.get("an_dt") or row.get("sort_date"))))
    except (requests.RequestException, ValueError, TypeError):
        return items
    return items


GOOGLE_NEWS = {
    "Moneycontrol": "https://news.google.com/rss/search?q=site:moneycontrol.com+(markets+OR+stocks+OR+nse)&hl=en-IN&gl=IN&ceid=IN:en",
    "Reuters": "https://news.google.com/rss/search?q=site:reuters.com+(markets+OR+stocks)&hl=en-US&gl=US&ceid=US:en",
}

PER_SOURCE_CAP = 16


def _scrape_source(spec, make_session=_session):
    """All feeds + HTML fallback (+ NSE API / Google News) for ONE source. Returns (items, errors)."""
    name, session, items, errors = spec["name"], make_session(), [], []
    for url in spec.get("feeds") or []:
        try:
            items += _from_feed(url, name, spec["region"], session)
        except Exception as exc:
            errors.append(f"{name} feed {url}: {type(exc).__name__}: {exc}")
    try:
        items += _html_links(session, spec)
    except Exception as exc:
        errors.append(f"{name} html: {type(exc).__name__}: {exc}")
    if name == "NSE":
        try:
            items += _nse_api(session)
        except Exception as exc:
            errors.append(f"NSE api: {type(exc).__name__}: {exc}")
    if not items and name in GOOGLE_NEWS:
        try:
            items += _from_feed(GOOGLE_NEWS[name], name, spec["region"], session)
        except Exception as exc:
            errors.append(f"{name} gnews: {type(exc).__name__}: {exc}")
    return items, errors


def scrape_all(sources=None, make_session=_session, breaker=None, deadline_s=None):
    """Sources run in parallel, each with timeouts/retries, behind a per-source circuit breaker and a global deadline.
    A slow or dead source can no longer stall the cycle; cross-outlet duplicates are handled later by engine.cluster."""
    sources, breaker, deadline_s = sources or SOURCES, breaker or BREAKER, deadline_s or DEADLINE_S
    bag, errors, cov, skipped = [], [], {}, []
    active = []
    for spec in sources:
        if breaker.allow(spec["name"]):
            active.append(spec)
        else:
            cov[spec["name"]] = {"source": spec["name"], "region": spec["region"], "raw": 0, "state": "open", "retry_in_s": breaker.retry_in(spec["name"])}
    pool = ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(active))))
    futs = {pool.submit(_scrape_source, spec, make_session): spec for spec in active}
    done = set()
    try:
        for f in as_completed(futs, timeout=deadline_s):
            spec = futs[f]; done.add(f)
            try:
                items, errs = f.result()
            except Exception as exc:
                items, errs = [], [f"{spec['name']}: {type(exc).__name__}: {exc}"]
            bag += items; errors += errs
            (breaker.success if items else breaker.failure)(spec["name"])
            cov[spec["name"]] = {"source": spec["name"], "region": spec["region"], "raw": len(items), "state": "ok" if items else "empty"}
    except FutTimeout:
        pass
    for f, spec in futs.items():
        if f not in done:
            f.cancel(); breaker.failure(spec["name"])
            errors.append(f"{spec['name']}: exceeded {deadline_s:g}s deadline")
            cov[spec["name"]] = {"source": spec["name"], "region": spec["region"], "raw": 0, "state": "deadline"}
    pool.shutdown(wait=False, cancel_futures=True)       # never block the cycle on a straggler

    seen, unique, per = set(), [], {}
    bag.sort(key=lambda x: x.get("published_at") or x["fetched_at"], reverse=True)
    for item in bag:
        key = (item["source"], item["title"].lower())    # same outlet, same title. Cross-outlet is engine.cluster's job
        if key in seen:
            continue
        src = item["source"]
        if per.get(src, 0) >= PER_SOURCE_CAP:
            continue
        seen.add(key)
        per[src] = per.get(src, 0) + 1
        unique.append(item)
    return unique[:160], errors, [cov[s["name"]] for s in sources if s["name"] in cov]
