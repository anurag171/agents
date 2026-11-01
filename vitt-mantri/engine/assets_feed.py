"""Multi-asset board: commodities, currencies, rates, indices, crypto - live quotes + news bias + chart verdict."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

from .bars import fetch_bars
from .chartgate import evaluate
from .impacts import load_theme_config
from .instruments import CFG

DIRECT_W, INDIRECT_W, CAP_ITEMS, ENTER = 1.0, 0.6, 3, 1.5


def catalogue():
    try:
        with open(os.path.join(CFG, "assets.json")) as fh:
            return json.load(fh).get("market", [])
    except (OSError, ValueError):
        return []


def theme_counts(decoded):
    out = {}
    for it in decoded:
        if it.get("theme_id"):
            out[it["theme_id"]] = out.get(it["theme_id"], 0) + 1
    return out


def news_bias(counts, cfg, tags, invert=False):
    """Net news pressure on an asset from the themes active in today's headlines (capped per theme)."""
    score, ev = 0.0, []
    for tid, n in counts.items():
        for r in (cfg.get(tid) or {}).get("rules", []):
            if set(r.get("tags", [])) & set(tags):
                w = (DIRECT_W if r["hop"] == "direct" else INDIRECT_W) * min(n, CAP_ITEMS)
                score += w if r["action"] == "BUY" else -w
                ev.append({"theme": cfg[tid]["label"], "effect": r["action"], "why": r["why"], "items": n})
    score = -score if invert else score
    return score, ("BUY" if score >= ENTER else "SELL" if score <= -ENTER else "WATCH"), ev


def _pct(closes, n):
    return round((closes[-1] / closes[-1 - n] - 1) * 100, 2) if len(closes) > n and closes[-1 - n] else None


def build_assets(decoded, fetch=fetch_bars, cfg=None, items=None):
    items, cfg = items if items is not None else catalogue(), cfg or load_theme_config()
    counts = theme_counts(decoded)
    with ThreadPoolExecutor(max_workers=8) as pool:
        data = list(pool.map(lambda a: fetch(a["yahoo"]), items))
    rows, ok = [], 0
    for a, d in zip(items, data):
        row = {"key": a["key"], "label": a["label"], "cls": a["cls"], "unit": a.get("unit", ""), "note": a.get("note", ""), "available": bool(d)}
        score, action, ev = news_bias(counts, cfg, a.get("tags", []), a.get("invert", False))
        row.update(news_score=round(score, 1), action=action, evidence=ev[:4])
        if d:
            ok += 1
            closes = [b.c for b in d["bars"]]
            sma20 = sum(closes[-20:]) / min(20, len(closes))
            hi, lo = max(closes[-60:]), min(closes[-60:])
            row.update(price=round(d["price"], 4 if d["price"] < 10 else 2), change_pct=round(d["change_pct"], 2), chg_5d=_pct(closes, 5), chg_20d=_pct(closes, 20),
                       trend="up" if d["price"] > sma20 else "down", range_pos=round((d["price"] - lo) / (hi - lo), 2) if hi > lo else 0.5,
                       spark=[round(c, 4) for c in closes[-40:]], as_of=d["bars"][-1].d.isoformat())
            if action in ("BUY", "SELL") and len(d["bars"]) >= 60:
                _, summary, new, reason, _ = evaluate(d["bars"], action)
                row["chart"] = summary
                if new != action:
                    row["news_action"], row["action"] = action, new
        rows.append(row)
    order = {"BUY": 0, "SELL": 0, "WATCH": 1}
    rows.sort(key=lambda r: (order[r["action"]], -abs(r["news_score"]), r["label"]))
    return rows, [{"source": "Assets: Yahoo", "raw": ok}]
