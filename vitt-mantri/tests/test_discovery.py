import json, random
from datetime import date, datetime, timedelta, timezone
import pytest
from engine import fundamentals as FD, screener as SC, signal_log as L
from engine import instruments as ins
from engine.movers import Quote
from engine.store import Store
from engine.technicals import Bar

SECT = ["information technology", "automobile and auto components", "financial services"]


def pm_for(trend=True, rel6=0.05, r12=0.10, vol=0.25, mdd=0.2, tv=2e8):
    return {"price": 100.0, "ret6": rel6 + 0.05, "rel6": rel6, "ret12_1": r12, "trend_ok": trend, "above200": trend, "vol": vol, "maxdd": mdd, "tv": tv, "pos52": 0.7}


def fund(roe=15, de=0.8, rev=10, earn=10, pe=25, pb=3, peg=1.5, opm=15, pm=10, sector=""):
    return {"roe": roe, "debt_equity": de, "rev_growth": rev, "earn_growth": earn, "pe": pe, "pb": pb, "peg": peg, "op_margin": opm, "profit_margin": pm, "sector": sector}


def universe(n_per=14, seed=1):
    """3 sectors x n_per mediocre stocks (random metrics) + one quiet standout in IT."""
    rnd, items, pm, funds = random.Random(seed), [], {}, {}
    for s in SECT:
        for i in range(n_per):
            t = f"{s[:2].upper()}{i}"; items.append({"ticker": t, "name": t + " Ltd", "sector": s})
            pm[t] = pm_for(trend=rnd.random() < 0.6, rel6=rnd.gauss(0, 0.1), r12=rnd.gauss(0.05, 0.2), vol=rnd.uniform(0.2, 0.5), mdd=rnd.uniform(0.1, 0.5))
            funds[t] = fund(roe=rnd.uniform(5, 20), de=rnd.uniform(0.2, 2.0), rev=rnd.uniform(-5, 20), earn=rnd.uniform(-10, 25), pe=rnd.uniform(10, 60), pb=rnd.uniform(1, 8), peg=rnd.uniform(0.5, 3), opm=rnd.uniform(5, 25))
    items.append({"ticker": "GEM", "name": "Hidden Gem Ltd", "sector": SECT[0]})
    pm["GEM"] = pm_for(trend=True, rel6=0.30, r12=0.60, vol=0.18, mdd=0.08)
    funds["GEM"] = fund(roe=38, de=0.05, rev=35, earn=45, pe=14, pb=2.0, peg=0.5, opm=30, pm=22)
    return items, pm, funds


def test_a_stock_nobody_writes_about_is_found_from_fundamentals_and_strength():
    items, pm, funds = universe()
    res = SC.screen(items, pm, funds, news_symbols={"IT0", "IT1"})
    top = res["rows"][0]
    assert top["ticker"] == "GEM" and top["tier"] == "PICK" and top["score_pct"] >= 80 and top["coverage_pct"] == 100
    assert top["no_news"] is True and "no headlines in the current news window" in top["flags"] and "barely in the headlines" in top["why"]
    assert "returns on equity (38%)" in top["why"] and "low debt" in top["why"] and "beating the Nifty" in top["why"]
    assert res["eligible"] == len(items) == res["screened"]


def test_sector_neutral_ranking_and_banks_not_penalised_for_leverage():
    items, pm, funds = [], {}, {}
    for i in range(10):                                         # 10 banks: structurally high D/E, ROE 10-19
        t = f"BK{i}"; items.append({"ticker": t, "name": t, "sector": "financial services"}); pm[t] = pm_for(); funds[t] = fund(roe=10 + i, de=7 + i * 0.1)
    for i in range(10):                                         # 10 industrials: low D/E, ROE 20-29
        t = f"IN{i}"; items.append({"ticker": t, "name": t, "sector": "capital goods"}); pm[t] = pm_for(); funds[t] = fund(roe=20 + i, de=0.3 + i * 0.05)
    res = {r["ticker"]: r for r in SC.screen(items, pm, funds, min_tv=0)["rows"] + []}
    comps = {}
    from engine.screener import peer_ranks
    groups = {i["ticker"]: i["sector"] for i in items}
    r = peer_ranks({t: funds[t]["roe"] for t in groups}, groups)
    assert r["BK9"] == pytest.approx(1.0) and r["IN0"] == pytest.approx(0.0)                    # best bank = top of banks; worst industrial = bottom of industrials
    best_bank = SC.screen(items, pm, funds, min_tv=0)
    assert best_bank["eligible"] == 20


def test_small_peer_groups_fall_back_to_the_whole_universe():
    vals = {f"A{i}": float(i) for i in range(5)} | {f"B{i}": 100.0 + i for i in range(10)}
    groups = {k: ("small" if k.startswith("A") else "big") for k in vals}
    r = SC.peer_ranks(vals, groups)
    assert r["A4"] < 0.5 and r["B0"] == pytest.approx(0.0) and r["B9"] == pytest.approx(1.0)      # 5-name group ranked globally, 10-name group ranked within itself


def test_hard_filters_loss_makers_illiquid_and_downtrends():
    items, pm, funds = universe(); good = funds["GEM"]
    for t, f, p in (("LOSS", dict(good, profit_margin=-5, earn_growth=-30), pm["GEM"]), ("THIN", good, dict(pm["GEM"], tv=1e6)), ("DOWN", good, dict(pm["GEM"], trend_ok=False, above200=False))):
        items.append({"ticker": t, "name": t, "sector": SECT[0]}); pm[t], funds[t] = p, f
    res = SC.screen(items, pm, funds); got = {r["ticker"]: r["tier"] for r in res["rows"]}
    assert "LOSS" not in got and "THIN" not in got and got.get("DOWN") != "PICK"                  # a falling knife is never a PICK even with great numbers
    assert res["eligible"] == len(items) - 1                                                        # THIN dropped before ranking


def test_without_fundamentals_a_stock_can_only_be_price_only_watchlist():
    items, pm, funds = universe(); del funds["GEM"]
    row = next(r for r in SC.screen(items, pm, funds)["rows"] if r["ticker"] == "GEM")
    assert row["tier"] == "WATCHLIST" and row["price_only"] and row["coverage_pct"] == 40 and "price-only: fundamentals not available" in row["flags"] and row["strength"] != "Very strong"
    full = next(r for r in SC.screen(*[universe()[i] for i in (0, 1, 2)])["rows"] if r["ticker"] == "GEM")
    assert full["score_pct"] > row["score_pct"]


@pytest.mark.parametrize("seed", range(5))
def test_better_fundamentals_never_lower_the_score(seed):
    items, pm, funds = universe(seed=seed)
    base = next(r for r in SC.screen(items, pm, funds)["rows"] if r["ticker"] == "GEM")
    funds2 = {**funds, "GEM": dict(funds["GEM"], roe=funds["GEM"]["roe"] + 20, rev_growth=funds["GEM"]["rev_growth"] + 20)}
    better = next(r for r in SC.screen(items, pm, funds2)["rows"] if r["ticker"] == "GEM")
    assert better["score_pct"] >= base["score_pct"] and better["factors"]["quality"] >= base["factors"]["quality"]


def bars(n, drift=0.001, noise=0.01, seed=1, start=100.0, vol=2e6):
    rnd, c, d, out = random.Random(seed), start, date(2024, 1, 1), []
    for _ in range(n):
        while d.weekday() >= 5: d += timedelta(days=1)
        c *= 1 + drift + rnd.gauss(0, noise); out.append(Bar(d, c * 0.999, c * 1.01, c * 0.99, c, vol)); d += timedelta(days=1)
    return out


def test_price_metrics_known_values_and_short_history():
    b = bars(300, drift=0.002, noise=0.0)                                   # smooth uptrend, zero noise
    m = SC.price_metrics(b, bars(300, drift=0.0005, noise=0.0))
    assert m["trend_ok"] and m["ret6"] == pytest.approx(b[-1].c / b[-126].c - 1) and m["rel6"] > 0 and m["maxdd"] == pytest.approx(0.0, abs=1e-9) and m["vol"] == pytest.approx(0.0, abs=1e-9)
    crash = bars(300, drift=0.0, noise=0.0); crash = crash[:200] + [Bar(x.d, x.o * .5, x.h * .5, x.l * .5, x.c * .5, x.v) for x in crash[200:]]
    assert SC.price_metrics(crash)["maxdd"] == pytest.approx(0.5, abs=1e-9) and SC.price_metrics(bars(100)) is None


def test_fundamentals_parsers_units_and_aliases():
    y = json.dumps({"quoteSummary": {"result": [{"financialData": {"returnOnEquity": {"raw": 0.24}, "debtToEquity": {"raw": 45.0}, "revenueGrowth": {"raw": 0.18}, "earningsGrowth": {"raw": 0.22},
        "operatingMargins": {"raw": 0.21}, "profitMargins": {"raw": 0.15}}, "defaultKeyStatistics": {"priceToBook": {"raw": 4.2}, "pegRatio": {"raw": 1.1}, "heldPercentInsiders": {"raw": 0.52}},
        "summaryDetail": {"trailingPE": {"raw": 28.0}, "marketCap": {"raw": 5e11}, "dividendYield": {"raw": 0.012}}, "assetProfile": {"sector": "Technology"}}]}})
    f = FD.parse_yahoo_summary(y)
    assert f["roe"] == pytest.approx(24.0) and f["debt_equity"] == pytest.approx(0.45) and f["rev_growth"] == pytest.approx(18.0) and f["promoter_pct"] == pytest.approx(52.0) and f["sector"] == "Technology" and f["pe"] == 28.0
    assert FD.parse_yahoo_summary(json.dumps({"quoteSummary": {"result": None}})) is None
    csv_text = "Name,NSE Code,Stock P/E,ROCE %,ROE %,Debt to equity,Sales growth 3Years,Profit growth 3Years,Promoter holding,Industry\nAcme Ltd,ACME.NS,\"1,234.5\",22.4%,18.1,0.35,14.2,19.9,61.5,Capital Goods\nBad,,,,,,,,,\n"
    d = FD.parse_fundamentals_file(csv_text)
    assert set(d) == {"ACME"} and d["ACME"]["pe"] == 1234.5 and d["ACME"]["roce"] == 22.4 and d["ACME"]["rev_growth"] == 14.2 and d["ACME"]["sector"] == "Capital Goods"
    assert FD.parse_fundamentals_file(json.dumps({"XYZ": {"roe": 12, "pe": 20}}), as_csv=False)["XYZ"]["roe"] == 12


def test_load_fundamentals_precedence_batching_cache_and_failure(tmp_path):
    st = Store(str(tmp_path / "f.sqlite")); d = tmp_path / "files"; d.mkdir()
    (d / "x.csv").write_text("symbol,roe,pe\nAAA,30,12\n")
    calls = []
    def fetch(symbols, session=None):
        calls.append(list(symbols)); return {s: {"roe": 10.0, "pe": 20.0, "sector": ""} for s in symbols}, None
    syms = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    data, info = FD.load_fundamentals(st, syms, str(d), batch=2, fetch=fetch)
    assert data["AAA"]["roe"] == 30 and info["file"] == 1 and info["fetched"] == 2 and calls == [["BBB", "CCC"]] and info["missing"] == 2            # file beats fetch; batch limits requests
    data, info = FD.load_fundamentals(st, syms, str(d), batch=2, fetch=fetch)
    assert info["cache"] == 2 and calls[-1] == ["DDD", "EEE"] and info["missing"] == 0                                                            # progressive fill, cached rows not re-fetched
    with st.conn() as c: c.execute("UPDATE fundamentals SET fetched_at='2020-01-01T00:00:00+00:00'")
    assert FD.cache_get(st, syms) == {}                                                                                                             # stale cache ignored
    data, info = FD.load_fundamentals(st, ["ZZZ"], None, fetch=lambda s, sess=None: ({}, "crumb: ConnectionError"))
    assert data == {} and info["error"] == "crumb: ConnectionError" and info["missing"] == 1                                                        # failure is reported, not hidden


def test_universe_grows_from_live_quotes_and_matcher_sees_new_names():
    u = ins.Universe([ins.Instrument("TCS", "Tata Consultancy Services Limited", tier=1)])
    assert u.match("Zenith Tools Ltd shares surge") == []                                                # builds the matcher once
    q = [Quote("ZENITH", "Zenith Tools Limited", "IN", 100, 2, industry="Capital Goods"), Quote("TCS", "TCS", "IN", 4000, 1), Quote("AAPL", "Apple", "US", 190, 1)]
    assert ins.grow_universe(u, q) == 1 and u.has("ZENITH") and not u.has("AAPL")                         # US skipped, existing untouched
    assert [i.symbol for i in u.match("Zenith Tools Ltd shares surge")] == ["ZENITH"]                     # regression: matcher used to stay stale after add()
    assert u.by_symbol["ZENITH"].industry == "Capital Goods" and "capital-goods" in u.by_symbol["ZENITH"].tags


def test_picks_are_logged_once_and_settled_at_20_and_60_days(tmp_path):
    st = Store(str(tmp_path / "s.sqlite")); t0 = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    pick = {"ticker": "GEM", "tier": "PICK", "score_pct": 84, "strength": "Very strong", "coverage_pct": 100, "price": 100.0, "why": "w", "flags": [], "breakdown": [{"key": "quality"}], "chart": {"verdict": "CONFIRM", "score": .5}}
    assert L.record_screen(st, [pick], "q1", t0) == 1 and L.record_screen(st, [pick], "q1", t0 + timedelta(days=1)) == 0
    assert L.record_screen(st, [dict(pick, tier="WATCHLIST")], "q1", t0 + timedelta(days=2)) == 0                      # dropped out: call ended
    assert L.record_screen(st, [pick], "q1", t0 + timedelta(days=10)) == 0                                             # back within 30 days: debounced
    assert L.record_screen(st, [dict(pick, tier="WATCHLIST")], "q1", t0 + timedelta(days=11)) == 0
    assert L.record_screen(st, [pick], "q1", t0 + timedelta(days=45)) == 1                                              # genuinely new later
    with st.conn() as c: r = c.execute("SELECT kind,direction,scoring_version,yahoo,final_action FROM signals_log").fetchone()
    assert tuple(r) == ("screen", 1, "q1", "GEM.NS", "BUY")
    series = {"GEM.NS": bars(400, drift=0.002, noise=0.0, start=100.0), "^NSEI": bars(400, drift=0.0005, noise=0.0, start=20000)}
    s2 = Store(str(tmp_path / "s2.sqlite")); L.record_screen(s2, [dict(pick, price=series["GEM.NS"][250].c)], "q1", datetime.combine(series["GEM.NS"][250].d, datetime.min.time(), timezone.utc))
    res = L.evaluate_outcomes(s2, lambda sym, **k: {"bars": series[sym]}, today=series["GEM.NS"][-1].d)
    assert res["settled"] == 4                                                                                          # 1, 5, 20 AND 60 trading days
    perf = L.performance(s2, 60, scoring_version="q1", kind="screen")
    assert perf["settled"] == 1 and perf["overall"]["hit_rate"] == 1.0 and perf["overall"]["enough"] is False


def test_run_screen_end_to_end_with_fake_network(tmp_path):
    st = Store(str(tmp_path / "r.sqlite")); rnd = random.Random(4)
    inst = [ins.Instrument(f"S{i}", f"Stock {i} Ltd", industry="Capital Goods", tier=3) for i in range(20)] + [ins.Instrument("GEM", "Hidden Gem Ltd", industry="Capital Goods", tier=3)]
    series = {"^NSEI": bars(520, drift=0.0004, noise=0.006, seed=99, start=20000)}
    for i in inst: series[f"{i.symbol}.NS"] = bars(520, drift=0.003 if i.symbol == "GEM" else rnd.uniform(-0.001, 0.0008), noise=0.012, seed=sum(map(ord, i.symbol)), vol=3e6)
    fund_by = {i.symbol: fund(roe=rnd.uniform(5, 18), de=rnd.uniform(0.3, 2), rev=rnd.uniform(-5, 15), earn=rnd.uniform(-5, 15), pe=rnd.uniform(15, 50)) for i in inst}
    fund_by["GEM"] = fund(roe=40, de=0.05, rev=38, earn=50, pe=13, pb=2, peg=0.4, opm=30)
    out = SC.run_screen(st, ins.Universe(inst), fetch=lambda sym, **k: ({"bars": series[sym]} if sym in series else None), load_fund=lambda s, syms: (fund_by, {"file": len(fund_by), "cache": 0, "fetched": 0, "missing": 0, "error": None}), news_symbols={"S0"})
    top = out["picks"][0] if out["picks"] else out["watchlist"][0]
    assert top["ticker"] == "GEM" and top["no_news"] and top["chart"]["verdict"] in ("CONFIRM", "NEUTRAL", "WAIT") and out["universe"]["with_prices"] == 21 and out["universe"]["with_fundamentals"] == 21
    thin = SC.run_screen(st, ins.Universe(inst), fetch=lambda sym, **k: ({"bars": series[sym]} if sym in series else None), load_fund=lambda s, syms: ({}, {"error": "crumb"}))
    assert any("only 0 of" in n for n in thin["notes"]) and all(r["price_only"] for r in thin["watchlist"] + thin["picks"]) and not thin["picks"]


def test_discovery_endpoints(tmp_path, monkeypatch):
    import app
    from engine import snapshot as snapfile
    monkeypatch.setenv("VM_DB", str(tmp_path / "d.sqlite")); monkeypatch.setattr(app, "DISC_PATH", str(tmp_path / "discovery.json")); monkeypatch.setattr(app, "SNAP_PATH", str(tmp_path / "snapshot.json"))
    c = app.app.test_client()
    assert c.get("/api/discovery").get_json()["pending"] is True
    snapfile.write(app.DISC_PATH, {"as_of": "2026-10-05T00:00:00+00:00", "version": "q1", "picks": [{"ticker": "GEM"}], "watchlist": [], "notes": [], "universe": {}})
    j = c.get("/api/discovery").get_json(); assert j["picks"][0]["ticker"] == "GEM" and j["track"]["overall"] == {"n": 0, "n_days": 0, "enough": False}
    assert c.get("/api/performance?kind=screen&horizon=60").get_json()["horizon"] == 60
    snapfile.write(app.SNAP_PATH, {"cycle": 1, "updated_at": "x", "board": [], "news": []}); assert c.get("/api/briefing").get_json()["discovery"]["picks"][0]["ticker"] == "GEM"
