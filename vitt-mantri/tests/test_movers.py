import json
from engine import movers as m

NSE = json.dumps({"data": [
    {"symbol": "NIFTY 500", "priority": 0, "lastPrice": 20000, "pChange": 0.3},
    *[{"symbol": s, "lastPrice": p, "pChange": c, "totalTradedVolume": v, "totalTradedValue": v * p, "yearHigh": h, "yearLow": l, "meta": {"companyName": s + " Ltd"}}
      for s, p, c, v, h, l in [("AAA", 100, 6.2, 9e6, 101, 60), ("BBB", 200, -5.1, 8e6, 300, 199), ("CCC", 50, 0.4, 7e6, 90, 40), ("DDD", 80, 1.1, 6e6, 85, 50),
                               ("EEE", 90, -0.2, 5e6, 120, 70), ("FFF", 10, 0.1, 4e6, 20, 8), ("GGG", 30, 2.2, 3e6, 31, 20), ("HHH", 60, -1.0, 2e6, 80, 55),
                               ("III", 70, 0.9, 1.5e6, 99, 60), ("JJJ", 40, 12.0, 1e3, 41, 20)]]]})
YAHOO = json.dumps({"finance": {"result": [{"quotes": [{"symbol": "NVDA", "shortName": "NVIDIA", "regularMarketPrice": 900, "regularMarketChangePercent": 4.2,
    "regularMarketVolume": 5e7, "averageDailyVolume3Month": 2e7, "fiftyTwoWeekHigh": 905, "fiftyTwoWeekLow": 400}, {"symbol": "TCS.NS", "regularMarketPrice": 4000, "regularMarketChangePercent": -1}]}]}})


def test_parsers():
    q = m.parse_nse_snapshot(NSE)
    assert len(q) == 10 and q[0].symbol == "AAA" and q[0].mkt == "IN" and q[0].year_high == 101           # index row skipped
    y = m.parse_yahoo_screener(YAHOO)
    assert y[0].mkt == "US" and y[1].symbol == "TCS" and y[1].mkt == "IN"
    c = m.parse_quotes_csv("Symbol,Price,Change_Pct,Volume\nxyz,10,-3.5,1000\nbad,0,1,1")
    assert [(x.symbol, x.change_pct) for x in c] == [("XYZ", -3.5)]


def test_lists_ignore_illiquid_spikes_and_flag_extremes():
    q = m.parse_nse_snapshot(NSE) + m.parse_yahoo_screener(YAHOO)
    L = m.derive_lists(q)
    assert L["gainers"][0]["symbol"] == "AAA"                                  # JJJ +12% is illiquid -> excluded
    assert L["losers"][0]["symbol"] == "BBB"
    assert "BBB" in [x["symbol"] for x in L["near_low"]] and "AAA" in [x["symbol"] for x in L["near_high"]]
    assert L["volume_shock"][0]["symbol"] == "NVDA"
    assert m.derive_lists([])["gainers"] == []


def row(t, a): return {"ticker": t, "action": a, "net": 2, "conviction": 3, "why": "w", "whys": ["w"], "headlines": ["h"], "sources": ["Reuters"]}


def test_merge_annotates_priced_in_disagree_and_adds_unexplained_movers():
    q = m.parse_nse_snapshot(NSE); L = m.derive_lists(q)
    board = m.merge_into_board([row("AAA", "BUY"), row("BBB", "BUY"), row("CCC", "BUY"), row("NOQUOTE", "SELL")], q, L)
    by = {r["ticker"]: r for r in board}
    assert by["AAA"]["priced_in"] and by["AAA"]["action"] == "WATCH" and by["AAA"]["news_action"] == "BUY" and "priced in" in by["AAA"]["why"]
    assert "market disagrees" in by["BBB"]["market"]["flags"] and by["BBB"]["action"] == "BUY"
    assert by["CCC"]["market"]["change_pct"] == 0.4 and "priced_in" not in by["CCC"]
    assert "market" not in by["NOQUOTE"]
    movers = [r for r in board if r.get("origin") == "market-mover"]
    assert movers and all(r["action"] == "WATCH" and r["conviction"] == 1 for r in movers)      # never BUY/SELL on momentum alone
    assert len({r["ticker"] for r in board}) == len(board)                                       # no duplicates


def test_fetch_quotes_isolates_failures_and_caches(monkeypatch):
    class R:
        def __init__(s, t): s.text = t
        def raise_for_status(s): pass
    class S:
        calls = 0
        def get(s, url, **k):
            S.calls += 1
            if "nseindia" in url and "api" in url: return R(NSE)
            if "nseindia" in url: return R("")
            raise OSError("yahoo blocked")
    monkeypatch.setattr(m, "_CACHE", {"at": 0.0, "quotes": [], "coverage": []})
    q, cov = m.fetch_quotes(force=True, session=S())
    assert len(q) == 10 and {"source": "Market: NSE", "raw": 10} in cov
    assert any(c["source"] == "Market: Yahoo" and c["raw"] == 0 and c.get("error") == "OSError" for c in cov)
    n = S.calls; m.fetch_quotes(session=S()); assert S.calls == n               # served from cache
