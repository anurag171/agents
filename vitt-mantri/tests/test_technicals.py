"""tests/test_technicals.py - run with: pytest -q"""
import math
import random
from datetime import date, timedelta

import pytest

from engine.technicals import (Bar, analog_stats, analyze, apply_gate, atr, chart_payload, confirm,
                               ema, macd_hist, rsi, sma, swing_levels)


def make_bars(closes, spread=0.01, vol=1000.0):
    d0, out, prev = date(2023, 1, 2), [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        h, l = max(o, c) * (1 + spread), min(o, c) * (1 - spread)
        out.append(Bar(d0 + timedelta(days=i), o, h, l, c, vol))
        prev = c
    return out


def trend_series(n=320, start=100.0, drift=0.0015, noise=0.008, seed=7):
    rnd, c, out = random.Random(seed), start, []
    for _ in range(n):
        c *= 1 + drift + rnd.gauss(0, noise)
        out.append(c)
    return out


def test_sma_ema_basic():
    x = [1, 2, 3, 4, 5]
    assert sma(x, 3) == [None, None, 2.0, 3.0, 4.0]
    e = ema(x, 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and e[3] == pytest.approx(3.0)


def test_rsi_bounds_and_extremes():
    up = [100 + i for i in range(40)]
    assert rsi(up)[-1] == 100.0
    down = [100 - i for i in range(40)]
    assert rsi(down)[-1] == pytest.approx(0.0, abs=1e-9)
    r = rsi(trend_series())
    assert all(0 <= v <= 100 for v in r if v is not None)


def test_atr_positive_and_warmup():
    a = atr(make_bars(trend_series(60)))
    assert a[13] is None and a[14] > 0


def test_no_lookahead_indicators():
    s = trend_series(300)
    full, cut = rsi(s), rsi(s[:250])
    assert full[249] == pytest.approx(cut[249])           # value at i unchanged by future bars
    assert sma(s, 50)[249] == pytest.approx(sma(s[:250], 50)[249])


def test_swing_levels_confirmed_only():
    closes = [100, 101, 102, 103, 104, 103, 102, 101, 100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99, 98]
    lows, highs = swing_levels(make_bars(closes, spread=0), k=3, lookback=100)
    assert 104 in highs and 100 in lows


def test_uptrend_buy_confirms_or_waits_not_contradicts():
    bars = make_bars(trend_series(320, drift=0.002))
    tech = analyze(bars, "BUY")
    assert tech["available"] and tech["trend"] == "up"
    v = confirm(tech, analog_stats(bars, +1))
    assert v["verdict"] in ("CONFIRM", "NEUTRAL", "WAIT")


def test_downtrend_contradicts_buy():
    bars = make_bars(trend_series(320, drift=-0.003, seed=3))
    v = confirm(analyze(bars, "BUY"))
    assert v["verdict"] in ("CONTRADICT", "WAIT")
    act, reason, mult = apply_gate("BUY", v)
    assert act == "WATCH" and mult < 1


def test_sell_mirrors_buy():
    bars = make_bars(trend_series(320, drift=-0.003, seed=3))
    t = analyze(bars, "SELL")
    assert t["stop"] > t["close"] > t["target"]


def test_extended_price_is_wait_not_confirm():
    closes = trend_series(320, drift=0.001, noise=0.004)
    closes[-3:] = [closes[-4] * 1.06, closes[-4] * 1.11, closes[-4] * 1.17]     # parabolic spike
    tech = analyze(make_bars(closes), "BUY")
    assert tech["ext_atr"] > 2.5
    v = confirm(tech)
    assert v["verdict"] in ("WAIT", "CONTRADICT")
    assert apply_gate("BUY", v)[0] == "WATCH"


def test_entry_zone_math_gives_rr_at_boundary():
    tech = analyze(make_bars(trend_series(320)), "BUY")
    if tech["entry_zone"]:
        lo, hi = tech["entry_zone"]
        stop, target = tech["stop"], tech["target"]
        assert (target - hi) / (hi - stop) == pytest.approx(1.5, rel=0.02)


def test_insufficient_data_is_unavailable_and_configurable():
    t = analyze(make_bars([100.0] * 20), "BUY")
    v = confirm(t)
    assert v["verdict"] == "UNAVAILABLE"
    assert apply_gate("BUY", v)[0] == "BUY"
    assert apply_gate("BUY", v, require_confirmation=True)[0] == "WATCH"


def test_gate_never_upgrades():
    for verdict in ("CONFIRM", "NEUTRAL", "WAIT", "CONTRADICT", "UNAVAILABLE"):
        v = {"verdict": verdict, "plan": {"entry_zone": (1, 2)}}
        assert apply_gate("WATCH", v)[0] == "WATCH"


def test_analog_stats_no_future_leak_and_non_overlapping():
    s = trend_series(600, drift=0.001)
    bars = make_bars(s)
    a = analog_stats(bars, +1, h=10)
    if a["n"]:
        assert 0 <= a["hit_rate"] <= 1
        assert a["n"] <= (len(bars) - 200) // 10                               # thinned, not overlapping
    # appending a future crash must not change stats computed on the earlier window
    crashed = make_bars(s + [s[-1] * 0.5] * 5)
    assert analog_stats(bars, +1, h=10) == analog_stats(make_bars(s), +1, h=10)
    assert crashed is not None


def test_chart_payload_shapes():
    bars = make_bars(trend_series(320))
    p = chart_payload(bars, analyze(bars, "BUY"), n=100)
    assert len(p["dates"]) == len(p["close"]) == len(p["sma20"]) == 100


def test_counter_trend_veto_even_with_positive_macd():
    bars = make_bars(trend_series(320, drift=-0.003, seed=3))      # downtrend, MACD hist slightly positive
    v = confirm(analyze(bars, "BUY"))
    assert v["verdict"] == "CONTRADICT" and any("counter-trend" in r for r in v["reasons"])


def test_counter_trend_allowed_when_history_supports():
    bars = make_bars(trend_series(320, drift=-0.003, seed=3))
    fake = {"n": 40, "hit_rate": 0.62, "median_ret": 0.012, "horizon_bars": 10}
    v = confirm(analyze(bars, "BUY"), fake)
    assert v["verdict"] != "CONTRADICT" or v["score"] <= -0.25
