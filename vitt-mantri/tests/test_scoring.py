import pytest
from engine import scoring as S


def row(net=4.0, buy=None, sell=None, sources=("Reuters", "Moneycontrol", "Economic Times"), events=3, grade="A", chart=None, market=None, **kw):
    buy = buy if buy is not None else max(net, 0); sell = sell if sell is not None else max(-net, 0)
    r = {"ticker": "ONGC", "action": "BUY" if net > 0 else "SELL", "net": net, "buy_score": buy, "sell_score": sell, "sources": list(sources),
         "events": events, "best_grade": grade, "why": "w", "headlines": ["h"]}
    if chart: r["chart"] = chart
    if market: r["market"] = market
    r.update(kw); return r


CONFIRM = {"verdict": "CONFIRM", "score": 0.6, "reasons": ["trend up (+0.35)"]}
AGREE = {"change_pct": 1.2, "mkt": "IN", "flags": []}


def test_strong_corroborated_confirmed_call_scores_high_and_is_explained():
    r = S.score_row(row(chart=CONFIRM, market=AGREE))
    assert r["score_pct"] >= 80 and r["strength"] == "Very strong" and r["lean"] == "BUY" and r["data_coverage_pct"] == 100 and r["calibrated"] is False
    assert {b["key"] for b in r["signal_breakdown"]} == set(S.W) and sum(b["weight_pct"] for b in r["signal_breakdown"]) == 100
    assert all(b["note"] for b in r["signal_breakdown"]) and r["conviction"] == 5


def test_one_weak_headline_cannot_stay_a_buy():
    board = S.score_board([row(net=2.4, buy=2.4, sources=("Seeking Alpha",), events=1, grade="C")])
    r = board[0]
    assert r["score_pct"] < 60 and r["action"] == "WATCH" and r["news_action"] == "BUY" and r["gated_by_strength"] and "below the 60% bar" in r["why"]


def test_gate_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("VM_MIN_SIGNAL_PCT", "0")
    assert S.score_board([row(net=2.4, buy=2.4, sources=("X",), events=1, grade="C")])[0]["action"] == "BUY"


def test_conflicting_evidence_scores_lower_than_unanimous_with_same_net():
    clean = S.score_row(row(net=5, buy=5, sell=0, chart=CONFIRM, market=AGREE))
    mixed = S.score_row(row(net=5, buy=8, sell=3, chart=CONFIRM, market=AGREE))
    assert mixed["score_pct"] < clean["score_pct"]
    assert next(b for b in mixed["signal_breakdown"] if b["key"] == "consistency")["value_pct"] < 70


def test_missing_data_is_not_guessed_and_caps_strength():
    r = S.score_row(row(net=9, buy=9))
    assert r["data_coverage_pct"] == 65 and r["limited_data"] and r["score_pct"] < 85
    assert [b["available"] for b in r["signal_breakdown"] if b["key"] in ("chart", "market")] == [False, False]
    full = S.score_row(row(net=9, buy=9, chart=CONFIRM, market=AGREE)); assert full["score_pct"] > r["score_pct"] and not full["limited_data"]


def test_market_and_chart_move_the_number_the_right_way():
    base = S.score_row(row(chart=CONFIRM, market=AGREE))["score_pct"]
    priced = S.score_row(row(chart=CONFIRM, market={"change_pct": 4.5}, priced_in=True))["score_pct"]
    against = S.score_row(row(chart=CONFIRM, market={"change_pct": -3.0}))["score_pct"]
    contra = S.score_row(row(chart={"verdict": "CONTRADICT", "score": -0.4}, market=AGREE))["score_pct"]
    assert base > priced and base > against and base > contra and against < priced + 20


def test_missing_data_preserves_ordering_instead_of_flattening():
    a, b, c = (S.score_row(row(net=n, buy=n))["score_pct"] for n in (3, 6, 12))
    assert a < b < c < 90


def test_sell_is_a_positive_percentage_and_mirrors_buy():
    b = S.score_row(row(net=4, chart=CONFIRM, market=AGREE))
    s = S.score_row(row(net=-4, chart=CONFIRM, market={"change_pct": -1.2}))
    assert s["lean"] == "SELL" and s["score_pct"] == b["score_pct"] and 0 < s["score_pct"] <= 100


def test_monotonic_in_news_strength_bounded_and_label_bands():
    prev = -1
    for net in (0.5, 1, 2, 3, 5, 8, 20):
        p = S.score_row(row(net=net, buy=net))["score_pct"]
        assert prev <= p <= 100; prev = p
    assert [S.label(x) for x in (10, 40, 60, 80)] == ["Weak", "Moderate", "Strong", "Very strong"]


def test_watch_rows_keep_a_lean_and_movers_show_no_fake_percentage():
    w = S.score_row(row(net=1.0, buy=1.0, action="WATCH")); assert w["lean"] == "BUY" and w["score_pct"] is not None
    m = S.score_row({"ticker": "ZOMATO", "action": "WATCH", "net": 0, "origin": "market-mover", "market": {"change_pct": 6}})
    assert m["score_pct"] is None and m["strength"] == "No news signal" and m["lean"] is None


def test_board_sorted_by_strength_and_priced_in_rows_are_low():
    board = S.score_board([row(net=2.5, buy=2.5, sources=("X",), events=1, grade="C"), row(net=6, chart=CONFIRM, market=AGREE, ticker="HI"),
                           {"ticker": "MOVER", "action": "WATCH", "net": 0, "origin": "market-mover"}])
    assert [r["ticker"] for r in board][0] == "HI" and board[-1]["ticker"] == "MOVER"


def test_asset_strength():
    a = {"news_score": 3.2, "trend": "up", "evidence": [{"items": 3}], "chart": CONFIRM, "action": "BUY"}
    S.score_asset(a); assert a["score_pct"] >= 70 and a["lean"] == "BUY" and {b["key"] for b in a["signal_breakdown"]} == {"news", "corroboration", "chart", "source"}
    against = {"news_score": 3.2, "trend": "down", "evidence": [{"items": 3}], "chart": CONFIRM, "action": "BUY"}
    S.score_asset(against); assert against["score_pct"] < a["score_pct"]
    none = {"news_score": 0, "action": "WATCH"}; S.score_asset(none); assert none["score_pct"] is None


def test_unconfirmed_call_is_never_labelled_very_strong():
    r = S.score_row(row(net=20, buy=20))
    assert r["limited_data"] and r["strength"] == "Strong"
    assert S.score_row(row(net=20, buy=20, chart=CONFIRM, market=AGREE))["strength"] == "Very strong"
