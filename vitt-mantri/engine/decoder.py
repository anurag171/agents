import re
from collections import defaultdict

from .universe import BEARISH_WORDS, BULLISH_WORDS, COMPANIES, SKIP_TITLES, THEMES


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _skip(title):
    t = _norm(title)
    return any(s in t for s in SKIP_TITLES)


def _named_companies(text):
    blob = _norm(text)
    hits = []
    seen = set()
    for key, meta in sorted(COMPANIES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", blob):
            if meta["ticker"] in seen:
                continue
            seen.add(meta["ticker"])
            hits.append(meta)
    return hits


def _theme_hits(text):
    blob = _norm(text)
    scored = []
    for tid, theme in THEMES.items():
        if any(n in blob for n in theme.get("negations", []) if n):
            continue
        hits = [k for k in theme["keywords"] if k in blob]
        if hits:
            scored.append((len(hits), tid, hits))
    scored.sort(reverse=True)
    return scored


def _sentiment_nudge(text, action):
    blob = _norm(text)
    bull = sum(1 for w in BULLISH_WORDS if w in blob)
    bear = sum(1 for w in BEARISH_WORDS if w in blob)
    if bull > bear + 1 and action == "SELL":
        return "WATCH"
    if bear > bull + 1 and action == "BUY":
        return "WATCH"
    return action


def _conf(theme_score, named, source_grade):
    score = 1.6 + min(theme_score, 3) * 0.7
    if named:
        score += 0.6
    if source_grade == "B":
        score += 0.4
    return round(min(4.7, score), 1)


def decode_item(item):
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    if _skip(title):
        return None

    blob = f"{title}. {summary}"
    named = _named_companies(blob)
    themes = _theme_hits(blob)
    GRADE_A = {"Bloomberg", "Reuters", "Financial Times", "WSJ", "NSE"}
    GRADE_B = {
        "Yahoo Finance", "Economic Times", "Moneycontrol", "CNBC-TV18",
        "Business Standard", "MarketWatch", "Investing.com", "Seeking Alpha", "MSN Money",
    }
    src = item.get("source") or ""
    if src in GRADE_A:
        source_grade = "A"
    elif src in GRADE_B:
        source_grade = "B"
    else:
        source_grade = "C"

    signals = []
    seen_pair = set()

    def add(ticker, name, hop, action, why, theme_id, theme_score):
        action = _sentiment_nudge(blob, action)
        key = (ticker, hop, action)
        if key in seen_pair:
            return
        seen_pair.add(key)
        signals.append(
            {
                "ticker": ticker,
                "name": name,
                "hop": hop,
                "action": action,
                "why": why,
                "theme": THEMES.get(theme_id, {}).get("label", theme_id),
                "theme_id": theme_id,
                "confidence": _conf(theme_score, bool(named), source_grade),
            }
        )

    if themes:
        _, tid, kws = themes[0]
        theme = THEMES[tid]
        score = len(kws)
        for ticker, why in theme["direct_buy"]:
            add(ticker, ticker, "direct", "BUY", why, tid, score)
        for ticker, why in theme["direct_sell"]:
            add(ticker, ticker, "direct", "SELL", why, tid, score)
        for ticker, why in theme["indirect_buy"]:
            add(ticker, ticker, "indirect", "BUY", why, tid, score)
        for ticker, why in theme["indirect_sell"]:
            add(ticker, ticker, "indirect", "SELL", why, tid, score)
        meaning = theme["meaning"]
        theme_label = theme["label"]
    else:
        meaning = (
            "No mapped macro theme. Named issuers are tagged; treat as single-stock news "
            "until a cash-flow, multiple or flow channel is clear."
        )
        theme_label = "Unmapped headline"
        tid = "unmapped"

    for meta in named:
        already = any(s["ticker"] == meta["ticker"] and s["hop"] == "direct" for s in signals)
        if already:
            continue
        blob_l = _norm(blob)
        if any(w in blob_l for w in BEARISH_WORDS):
            add(meta["ticker"], meta["name"], "direct", "SELL", "Issuer named in a negative print.", tid, 1)
        elif any(w in blob_l for w in BULLISH_WORDS):
            add(meta["ticker"], meta["name"], "direct", "BUY", "Issuer named in a positive print.", tid, 1)
        else:
            add(meta["ticker"], meta["name"], "direct", "WATCH", "Issuer named; direction not yet scored.", tid, 1)

    if not signals:
        return None

    net = 0
    for s in signals:
        if s["action"] == "BUY" and s["hop"] == "direct":
            net += 2
        elif s["action"] == "BUY":
            net += 1
        elif s["action"] == "SELL" and s["hop"] == "direct":
            net -= 2
        elif s["action"] == "SELL":
            net -= 1

    if net >= 3:
        tape = "RISK-ON for mapped names"
    elif net <= -3:
        tape = "RISK-OFF for mapped names"
    else:
        tape = "MIXED / relative-value tape"

    return {
        "id": item["id"],
        "source": item["source"],
        "region": item.get("region") or "Global",
        "title": title,
        "url": item["url"],
        "summary": summary,
        "published_at": item.get("published_at"),
        "fetched_at": item.get("fetched_at"),
        "source_grade": source_grade,
        "theme": theme_label,
        "theme_id": tid if themes else "unmapped",
        "meaning": meaning,
        "named": [{"ticker": m["ticker"], "name": m["name"], "sector": m["sector"]} for m in named],
        "tape": tape,
        "signals": signals[:16],
        "direct_buy": [s for s in signals if s["hop"] == "direct" and s["action"] == "BUY"],
        "direct_sell": [s for s in signals if s["hop"] == "direct" and s["action"] == "SELL"],
        "indirect_buy": [s for s in signals if s["hop"] == "indirect" and s["action"] == "BUY"],
        "indirect_sell": [s for s in signals if s["hop"] == "indirect" and s["action"] == "SELL"],
        "watch": [s for s in signals if s["action"] == "WATCH"],
    }


def aggregate_board(decoded):
    book = defaultdict(lambda: {
        "ticker": "",
        "buy": 0.0,
        "sell": 0.0,
        "direct_buy": 0.0,
        "direct_sell": 0.0,
        "indirect_buy": 0.0,
        "indirect_sell": 0.0,
        "headlines": [],
        "whys": [],
        "sources": [],
        "regions": [],
    })
    for news in decoded:
        for s in news["signals"]:
            row = book[s["ticker"]]
            row["ticker"] = s["ticker"]
            weight = s["confidence"] * (1.25 if s["hop"] == "direct" else 0.85)
            if s["action"] == "BUY":
                row["buy"] += weight
                row["direct_buy" if s["hop"] == "direct" else "indirect_buy"] += weight
            elif s["action"] == "SELL":
                row["sell"] += weight
                row["direct_sell" if s["hop"] == "direct" else "indirect_sell"] += weight
            if news["title"] not in row["headlines"]:
                row["headlines"].append(news["title"])
            if s["why"] not in row["whys"]:
                row["whys"].append(s["why"])
            src = news.get("source")
            if src and src not in row["sources"]:
                row["sources"].append(src)
            region = news.get("region")
            if region and region not in row["regions"]:
                row["regions"].append(region)

    board = []
    for ticker, row in book.items():
        net = row["buy"] - row["sell"]
        if net >= 2.2:
            action = "BUY"
        elif net <= -2.2:
            action = "SELL"
        else:
            action = "WATCH"
        board.append(
            {
                "ticker": ticker,
                "action": action,
                "net": round(net, 2),
                "buy_score": round(row["buy"], 2),
                "sell_score": round(row["sell"], 2),
                "direct_buy": round(row["direct_buy"], 2),
                "direct_sell": round(row["direct_sell"], 2),
                "indirect_buy": round(row["indirect_buy"], 2),
                "indirect_sell": round(row["indirect_sell"], 2),
                "headlines": row["headlines"][:4],
                "why": " | ".join(row["whys"][:3]),
                "conviction": min(5, round(abs(net) / 2.0, 1)),
                "sources": row["sources"],
                "regions": row["regions"],
            }
        )
    board.sort(key=lambda r: abs(r["net"]), reverse=True)
    return board
