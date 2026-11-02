"""Recommendation strength as a PERCENTAGE (0-100) with a visible breakdown.

What the number means: how much independent, agreeing evidence stands behind the call - NOT the probability that the
price will move that way. It has not been calibrated against realised outcomes (that needs the signals log + backtest);
rows carry calibrated=False so the UI says so. Once outcomes exist, replace `strength` with the observed hit rate of the
matching bucket.

Components (each 0..1), blended with fixed weights and renormalised over what is actually available:
  news 30%         tanh(|net news score| / 3)
  consistency 10%  share of evidence pointing the same way (conflicting news lowers it)
  corroboration 15% independent outlets and distinct events behind the call
  source 10%       best source grade (A wire/exchange > B major outlet > C other)
  chart 25%        technical verdict for the call (confirm > neutral > wait > contradict)
  market 10%       how price reacted today (agrees, flat, priced-in, disagrees)
Missing components are not guessed: data coverage is shown, and the strength is pulled toward 50% in proportion to what is
missing (50 + (raw - 50) x coverage), so an unconfirmed call can never look as strong as a fully confirmed one, and with
under 70% coverage the label never exceeds "Strong".
A BUY/SELL whose strength is below VM_MIN_SIGNAL_PCT (default 60) is shown as WATCH, keeping the original call."""
from __future__ import annotations

import math
import os

SCORING_VERSION = "s1"          # bump when weights/formula change: the log never mixes versions
W = {"news": 0.30, "consistency": 0.10, "corroboration": 0.15, "source": 0.10, "chart": 0.25, "market": 0.10}
LABEL = {"news": "News strength", "consistency": "Evidence agrees", "corroboration": "Independent confirmation", "source": "Source quality",
         "chart": "Chart check", "market": "Market reaction"}
GRADE_Q = {"A": 1.0, "B": 0.7, "C": 0.4}
LIMITED_COVERAGE = 0.70


def min_pct() -> float:
    return float(os.getenv("VM_MIN_SIGNAL_PCT", "60"))


def label(p) -> str:
    return "Very strong" if p >= 80 else "Strong" if p >= 60 else "Moderate" if p >= 40 else "Weak"


def _chart_value(ch):
    if not ch or ch.get("verdict") in (None, "UNAVAILABLE"):
        return None, None
    v = ch["verdict"]
    val = {"CONFIRM": 0.75 + 0.25 * max(0.0, min(1.0, float(ch.get("score") or 0))), "NEUTRAL": 0.5, "WAIT": 0.3, "CONTRADICT": 0.1}.get(v, 0.5)
    return val, f"{v.lower()}" + (f": {ch['reasons'][0]}" if ch.get("reasons") else "")


def _market_value(row, d):
    m = row.get("market")
    if not m or m.get("change_pct") is None:
        return None, None
    chg = float(m["change_pct"]); aligned = chg * d
    if row.get("priced_in"):
        val, why = 0.2, "already moved in this direction (priced in)"
    elif aligned >= 0.5:
        val, why = 0.8, "price is moving the same way"
    elif aligned > -0.5:
        val, why = 0.5, "price is flat"
    elif aligned > -2:
        val, why = 0.35, "price is leaning against the call"
    else:
        val, why = 0.15, "price is moving against the call"
    return val, f"{chg:+.1f}% today - {why}"


def _finish(row, comps, notes, lean):
    avail = {k: v for k, v in comps.items() if v is not None}
    wsum = sum(W[k] for k in avail)
    pct = 100 * sum(W[k] * v for k, v in avail.items()) / wsum if wsum else 0.0
    limited = wsum < LIMITED_COVERAGE
    pct = 50 + (pct - 50) * wsum
    pct = int(round(max(0, min(100, pct))))
    row.update(score_pct=pct, strength=label(min(pct, 79) if limited else pct), lean=lean, data_coverage_pct=int(round(wsum * 100)), limited_data=limited, calibrated=False, scoring_version=SCORING_VERSION,
               conviction=max(1, min(5, math.ceil(pct / 20))),
               signal_breakdown=[{"key": k, "label": LABEL[k], "weight_pct": int(W[k] * 100), "available": comps.get(k) is not None,
                                  "value_pct": None if comps.get(k) is None else int(round(comps[k] * 100)), "note": notes.get(k, "")} for k in W])
    return row


def score_row(row: dict) -> dict:
    net = float(row.get("net") or 0)
    b, s = float(row.get("buy_score") or 0), float(row.get("sell_score") or 0)
    if row.get("origin") == "market-mover" or (net == 0 and b + s == 0):
        row.update(score_pct=None, strength="No news signal", lean=None, data_coverage_pct=0, calibrated=False, signal_breakdown=[])
        return row
    d, o, e = (1 if net >= 0 else -1), len(row.get("sources") or []), int(row.get("events") or 0)
    ch_v, ch_n = _chart_value(row.get("chart")); mk_v, mk_n = _market_value(row, 1 if net >= 0 else -1)
    comps = {"news": math.tanh(abs(net) / 3.0), "consistency": abs(b - s) / (b + s) if b + s > 0 else None,
             "corroboration": min(1.0, 0.25 + 0.2 * max(o - 1, 0) + 0.15 * max(e - 1, 0)) if e else None,
             "source": GRADE_Q.get(row.get("best_grade")), "chart": ch_v, "market": mk_v}
    notes = {"news": f"net news score {net:+.1f}", "consistency": f"{b:.1f} supporting vs {s:.1f} opposing evidence" if d > 0 else f"{s:.1f} supporting vs {b:.1f} opposing evidence",
             "corroboration": f"{e} event(s) from {o} outlet(s)", "source": f"best source grade {row.get('best_grade')}", "chart": ch_n or "no chart data", "market": mk_n or "no live quote"}
    return _finish(row, comps, notes, "BUY" if d > 0 else "SELL")


def gate(board, minimum=None):
    minimum = min_pct() if minimum is None else minimum
    for r in board:
        if r["action"] in ("BUY", "SELL") and r.get("score_pct") is not None and r["score_pct"] < minimum:
            r.setdefault("news_action", r["action"])
            r["why"] = f"{r.get('why') or ''} [strength {r['score_pct']}% is below the {int(minimum)}% bar]".strip()
            r["action"], r["gated_by_strength"] = "WATCH", True
    return board


def score_board(board, minimum=None):
    for r in board:
        score_row(r)
    gate(board, minimum)
    board.sort(key=lambda r: (r.get("score_pct") is None, -(r.get("score_pct") or 0), -abs(float(r.get("net") or 0))))
    return board


def score_asset(row: dict) -> dict:
    sc = float(row.get("news_score") or 0)
    if not sc:
        row.update(score_pct=None, strength="No news signal", lean=None, calibrated=False)
        return row
    d = 1 if sc > 0 else -1
    items = sum(int(e.get("items") or 0) for e in row.get("evidence") or [])
    ch_v, ch_n = _chart_value(row.get("chart"))
    trend = row.get("trend")
    comps = {"news": math.tanh(abs(sc) / 2.5), "corroboration": min(1.0, 0.3 + 0.2 * max(items - 1, 0)) if items else None, "chart": ch_v,
             "source": (1.0 if (trend == "up") == (d > 0) else 0.3) if trend else None}
    notes = {"news": f"news pressure {sc:+.1f}", "corroboration": f"{items} supporting headline(s)", "chart": ch_n or "no chart data",
             "source": f"20-day trend is {trend}" if trend else ""}
    out = _finish(row, comps, notes, "BUY" if d > 0 else "SELL")
    out["signal_breakdown"] = [dict(b, label="Trend agrees" if b["key"] == "source" else b["label"]) for b in out["signal_breakdown"] if b["key"] in comps]
    return out
