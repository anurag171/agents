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
    "Accept-Language": "en-US,en;q=0.9",
}

YAH00_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://finance.yahoo.com/rss/topstories",
    "https://www.yahoo.com/news/rss/finance",
]

ET_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
]

YAH00_PAGES = [
    "https://finance.yahoo.com/topic/stock-market-news/",
    "https://finance.yahoo.com/topic/latest-news/",
    "https://finance.yahoo.com/news/",
]

ET_PAGES = [
    "https://economictimes.indiatimes.com/markets/stocks/news",
    "https://economictimes.indiatimes.com/markets",
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


def _from_feed(url, source):
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
        items.append(
            {
                "id": _uid(source, title, link),
                "source": source,
                "title": title,
                "url": link,
                "summary": summary[:500],
                "published_at": published.isoformat() if published else None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return items


def _yahoo_html(session):
    items = []
    for page in YAH00_PAGES:
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
            if "/news/" not in href and "/article/" not in href and "/economy/" not in href:
                continue
            if href.startswith("/"):
                href = "https://finance.yahoo.com" + href
            if "finance.yahoo.com" not in href and "yahoo.com" not in href:
                continue
            href = href.split("?")[0]
            items.append(
                {
                    "id": _uid("Yahoo Finance", title, href),
                    "source": "Yahoo Finance",
                    "title": title,
                    "url": href,
                    "summary": "",
                    "published_at": None,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    return items


def _et_html(session):
    items = []
    for page in ET_PAGES:
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
            if "articleshow" not in href and "liveblog" not in href and "slideshow" not in href:
                continue
            if href.startswith("/"):
                href = "https://economictimes.indiatimes.com" + href
            if "economictimes.indiatimes.com" not in href:
                continue
            href = href.split("?")[0]
            items.append(
                {
                    "id": _uid("Economic Times", title, href),
                    "source": "Economic Times",
                    "title": title,
                    "url": href,
                    "summary": "",
                    "published_at": None,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    return items


def scrape_all():
    session = _session()
    bag = []
    errors = []

    for url in YAH00_FEEDS:
        try:
            bag.extend(_from_feed(url, "Yahoo Finance"))
        except Exception as exc:
            errors.append(f"yahoo-feed {url}: {exc}")

    for url in ET_FEEDS:
        try:
            bag.extend(_from_feed(url, "Economic Times"))
        except Exception as exc:
            errors.append(f"et-feed {url}: {exc}")

    try:
        bag.extend(_yahoo_html(session))
    except Exception as exc:
        errors.append(f"yahoo-html: {exc}")

    try:
        bag.extend(_et_html(session))
    except Exception as exc:
        errors.append(f"et-html: {exc}")

    seen = set()
    unique = []
    for item in bag:
        key = (item["source"], item["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    unique.sort(key=lambda x: x.get("published_at") or x["fetched_at"], reverse=True)
    return unique[:80], errors
