import re
from collections import defaultdict

from .impacts import BUCKETS, SECTOR_RULE_WEIGHT, load_theme_config, resolve
from .instruments import get_universe
from .universe import BEARISH_WORDS, BULLISH_WORDS, SKIP_TITLES

THEMES = load_theme_config()            # keywords/negations/rules; impacts resolve against the live universe
_RX = {}
GRADE_A = {"Bloomberg", "Reuters", "Financial Times", "WSJ", "NSE"}
GRADE_B = {"Yahoo Finance", "Economic Times", "Moneycontrol", "CNBC-TV18", "Business Standard", "MarketWatch", "Investing.com", "Seeking Alpha", "MSN Money"}


def grade_of(src):
    return "A" if src in GRADE_A else "B" if src in GRADE_B else "C"


def corroboration(n_sources):
    """One event reported by N independent origins weighs 1.0x .. 1.75x - never N times."""
    return 1.0 + 0.25 * min(max(int(n_sources or 1) - 1, 0), 3)


def _wb(phrases, min_len=3):
    """Word-boundary alternation. Fragments shorter than min_len (e.g. 'spr') are ignored: they matched
    'spread'/'spring' and flipped oil themes."""
    ph = sorted({p.strip().lower() for p in phrases if p and len(p.strip()) >= min_len}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(map(re.escape, ph))) if ph else None


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _skip(title):
    t = _norm(title)
    return any(s in t for s in SKIP_TITLES)


def _named_companies(text):
    """Dynamic: any instrument in the live universe whose name/alias/symbol appears in the text."""
    return [{"ticker": i.symbol, "name": i.name, "sector": (i.tags[0] if i.tags else i.industry or i.asset_class),
             "asset_class": i.asset_class} for i in get_universe().match(text or "")]


def _theme_hits(text):
    blob = _norm(text)
    scored = []
    for tid, theme in THEMES.items():
        if tid not in _RX:
            _RX[tid] = (_wb(theme["keywords"]), _wb(theme.get("negations", []), min_len=4))
        kw, neg = _RX[tid]
        if neg and neg.search(blob):
            continue
        hits = sorted({m.group(0) for m in kw.finditer(blob)}) if kw else []
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
    src = item.get("source") or ""
    source_grade = grade_of(src)

    signals = []
    seen_pair = set()

    def add(ticker, name, hop, action, why, theme_id, theme_score, origin="curated"):
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
                "confidence": round(_conf(theme_score, bool(named), source_grade) * (SECTOR_RULE_WEIGHT if origin == "sector-rule" else 1.0), 2),
                "origin": origin,
            }
        )

    if themes:
        _, tid, kws = themes[0]
        theme = THEMES[tid]
        score = len(kws)
        universe = get_universe()
        resolved = resolve(theme, universe)
        for bucket in BUCKETS:
            hop, act = bucket.split("_")
            for ticker, why, origin in resolved[bucket]:
                add(ticker, universe.by_symbol[ticker].name, hop, act.upper(), why, tid, score, origin)
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
        if meta.get("asset_class", "equity") != "equity":
            continue                                    # asset ETFs/FX/crypto only enter via theme rules
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
        "n_sources": item.get("n_sources", 1),
        "sources": item.get("sources") or [item["source"]],
        "also": item.get("also", []),
        "published_estimated": item.get("published_estimated", False),
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
        "grade": "C",
        "tw": {},
    })
    for news in decoded:
        for s in news["signals"]:
            row = book[s["ticker"]]
            row["ticker"] = s["ticker"]
            weight = s["confidence"] * (1.25 if s["hop"] == "direct" else 0.85) * corroboration(news.get("n_sources", 1))
            if s["action"] == "BUY":
                row["buy"] += weight
                row["direct_buy" if s["hop"] == "direct" else "indirect_buy"] += weight
            elif s["action"] == "SELL":
                row["sell"] += weight
                row["direct_sell" if s["hop"] == "direct" else "indirect_sell"] += weight
            tid = news.get("theme_id")
            if tid and tid != "unmapped":
                row["tw"][tid] = row["tw"].get(tid, 0.0) + (weight if s["action"] == "BUY" else -weight if s["action"] == "SELL" else 0.0)
            if news["title"] not in row["headlines"]:
                row["headlines"].append(news["title"])
            if s["why"] not in row["whys"]:
                row["whys"].append(s["why"])
            for src in news.get("sources") or [news.get("source")]:
                if src and src not in row["sources"]:
                    row["sources"].append(src)
            row["events"] = row.get("events", 0) + 1
            row["grade"] = min(row["grade"], news.get("source_grade") or "C")
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
        sign = 1 if net >= 0 else -1
        ranked = sorted(((t, w * sign) for t, w in row["tw"].items() if w * sign > 0), key=lambda kv: -kv[1])
        board.append(
            {
                "ticker": ticker,
                "primary_theme": ranked[0][0] if ranked else None,
                "themes": [{"id": t, "label": THEMES.get(t, {}).get("label", t), "weight": round(w, 2)} for t, w in ranked[:3]],
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
                "events": row.get("events", 0),
                "best_grade": row["grade"],
                "regions": row["regions"],
            }
        )
    board.sort(key=lambda r: abs(r["net"]), reverse=True)
    return board
