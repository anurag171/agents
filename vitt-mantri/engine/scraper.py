import hashlib
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import feedparser
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US,en;q=0.9",
}

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
        "id": _uid(source, title, url),
        "source": source,
        "region": region,
        "title": title,
        "url": url,
        "summary": (summary or "")[:500],
        "published_at": published.isoformat() if published else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _from_feed(url, source, region):
    items = []
    parsed = feedparser.parse(url, request_headers=HEADERS)
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
    return items


def _html_links(session, spec):
    items = []
    bootstrap = spec.get("bootstrap")
    if bootstrap:
        try:
            session.get(bootstrap, timeout=12)
        except requests.RequestException:
            pass
    for page in spec.get("pages") or []:
        try:
            r = session.get(page, timeout=12)
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


def _nse_api(session):
    items = []
    try:
        session.get("https://www.nseindia.com", timeout=12)
        r = session.get(
            "https://www.nseindia.com/api/corporate-announcements",
            params={"index": "equities"},
            timeout=12,
            headers={"Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"},
        )
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
            url = row.get("attchmntFile") or row.get("an_dt") or "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
            if isinstance(url, str) and url.startswith("/"):
                url = "https://www.nseindia.com" + url
            if not title:
                continue
            items.append(_item("NSE", "India", title, url or "https://www.nseindia.com"))
    except (requests.RequestException, ValueError, TypeError):
        return items
    return items


GOOGLE_NEWS = {
    "Moneycontrol": "https://news.google.com/rss/search?q=site:moneycontrol.com+(markets+OR+stocks+OR+nse)&hl=en-IN&gl=IN&ceid=IN:en",
    "Reuters": "https://news.google.com/rss/search?q=site:reuters.com+(markets+OR+stocks)&hl=en-US&gl=US&ceid=US:en",
}

PER_SOURCE_CAP = 16


def scrape_all():
    session = _session()
    bag = []
    errors = []
    coverage = []

    for spec in SOURCES:
        got = 0
        for url in spec.get("feeds") or []:
            try:
                chunk = _from_feed(url, spec["name"], spec["region"])
                bag.extend(chunk)
                got += len(chunk)
            except Exception as exc:
                errors.append(f"{spec['name']} feed {url}: {exc}")
        try:
            chunk = _html_links(session, spec)
            bag.extend(chunk)
            got += len(chunk)
        except Exception as exc:
            errors.append(f"{spec['name']} html: {exc}")
        if spec["name"] == "NSE":
            try:
                chunk = _nse_api(session)
                bag.extend(chunk)
                got += len(chunk)
            except Exception as exc:
                errors.append(f"NSE api: {exc}")
        if got == 0 and spec["name"] in GOOGLE_NEWS:
            try:
                chunk = _from_feed(GOOGLE_NEWS[spec["name"]], spec["name"], spec["region"])
                bag.extend(chunk)
                got += len(chunk)
            except Exception as exc:
                errors.append(f"{spec['name']} gnews: {exc}")
        coverage.append({"source": spec["name"], "region": spec["region"], "raw": got})

    seen = set()
    unique = []
    per = {}
    bag.sort(key=lambda x: x.get("published_at") or x["fetched_at"], reverse=True)
    for item in bag:
        key = (item["source"], item["title"].lower())
        if key in seen:
            continue
        src = item["source"]
        if per.get(src, 0) >= PER_SOURCE_CAP:
            continue
        seen.add(key)
        per[src] = per.get(src, 0) + 1
        unique.append(item)

    return unique[:160], errors, coverage
