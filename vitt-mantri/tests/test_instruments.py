import json
import pytest
from engine import instruments as ins
from engine.impacts import load_theme_config, resolve

NSE_IDX = "\ufeffCompany Name,Industry,Symbol,Series,ISIN Code\nInfosys Ltd.,Information Technology,INFY,EQ,INE009A01021\nNewco Software Limited,Information Technology,NEWCO,EQ,INE000000001\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
NSE_ALL = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\nTCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1\nJUNK,Junk Debt Ltd,N1,01-JAN-2020,1,1,X,1\n"
SEC = json.dumps({"0": {"cik_str": 1, "ticker": "XOM", "title": "Exxon Mobil Corp"}})


def uni(*extra):
    return ins.Universe(ins.parse_nse_index_csv(NSE_IDX, 1) + ins.parse_nse_equity_list(NSE_ALL) + ins.parse_sec_tickers(SEC) + list(extra))


def test_parsers_handle_bom_spaced_headers_and_series_filter():
    u = uni()
    assert {"INFY", "NEWCO", "HDFCBANK", "TCS", "XOM"} <= set(u.tickers()) and "JUNK" not in u.tickers()
    assert "it" in u.by_symbol["NEWCO"].tags and u.by_symbol["XOM"].mkt == "US"


def test_match_by_name_symbol_and_cashtag_not_common_words():
    u = uni()
    syms = [i.symbol for i in u.match("Infosys Ltd wins deal while TCS slips; $XOM up. Gold and IT stocks mixed")]
    assert syms == ["INFY", "TCS", "XOM"]


def test_ambiguous_aliases_dropped():
    a, b = ins.Instrument("AAA", "Alpha Industries Ltd"), ins.Instrument("BBB", "Alpha Industries Limited")
    assert ins.Universe([a, b]).match("Alpha Industries rallies") == []


def test_more_liquid_tier_wins_on_merge():
    u = ins.Universe([ins.Instrument("TCS", "Tata Consultancy", tier=4), ins.Instrument("TCS", "Tata Consultancy Services", tier=1)])
    assert u.by_symbol["TCS"].tier == 1


def test_new_listing_flows_into_theme_without_code_change():
    cfg = load_theme_config()["it_preview"]
    res = resolve(cfg, uni())
    names = {s for b in res.values() for s, _, _ in b}
    assert "NEWCO" in names                                   # not in any hardcoded list
    assert any(o == "sector-rule" for b in res.values() for _, _, o in b)


def test_curated_pin_absent_from_universe_is_not_emitted():
    res = resolve(load_theme_config()["oil_up"], ins.Universe([ins.Instrument("ONGC", "Oil and Natural Gas Corporation", tags=["upstream"])]))
    assert [s for s, _, _ in res["direct_buy"]] == ["ONGC"] and res["direct_sell"] == []


def test_asset_class_rules_resolve_gold_bonds_reits():
    u = ins.Universe(ins.asset_instruments())
    cfg = load_theme_config()
    gold = {s for b in resolve(cfg["gold_bid"], u).values() for s, _, _ in b}
    yields = resolve(cfg["yields_up"], u)
    assert "GOLDBEES" in gold and "LICNETFGSC" in [s for s, _, _ in yields["direct_sell"]] and {"EMBASSY"} <= {s for s, _, _ in yields["indirect_sell"]}


def test_get_universe_live_then_cache_then_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(ins, "CACHE", str(tmp_path / "u.json")); monkeypatch.setattr(ins, "_U", None)
    pages = {ins.NSE_URLS[1]: NSE_IDX, ins.NSE_URLS[2]: NSE_IDX, ins.NSE_URLS[3]: NSE_IDX, ins.NSE_ALL: NSE_ALL, ins.SEC_URL: SEC}
    live = ins.get_universe(get=lambda url: pages[url], force=True)
    assert live.meta["origin"] == "live" and live.has("NEWCO") and live.has("GOLDBEES")
    def down(url): raise OSError("blocked")
    monkeypatch.setattr(ins, "_U", None)
    cached = ins.get_universe(get=down, force=True)
    assert cached.meta["origin"] == "cache" and cached.has("NEWCO")
    (tmp_path / "u.json").unlink(); monkeypatch.setattr(ins, "_U", None)
    seed = ins.get_universe(get=down, force=True)
    assert seed.meta["origin"] == "seed" and seed.has("ONGC") and len(seed) > 100


def test_decoder_end_to_end_offline(monkeypatch):
    monkeypatch.setattr(ins, "_U", None); monkeypatch.setattr(ins, "CACHE", "/nonexistent/u.json")
    from engine.decoder import decode_item
    item = lambda t: {"id": "1", "source": "Reuters", "url": "u", "title": t, "summary": "", "region": "Global"}
    d = decode_item(item("Brent crude jumps 4% as Hormuz tensions flare"))
    assert d["theme_id"] == "oil_up" and "ONGC" in [s["ticker"] for s in d["direct_buy"]]
    d2 = decode_item(item("Spread widens as spring rally lifts crude oil prices"))
    assert d2["theme_id"] == "oil_up"                          # 'spr' substring bug fixed
    assert all("origin" in s for s in d["signals"])


def test_refresh_backoff_and_no_hammering(monkeypatch, tmp_path):
    monkeypatch.setattr(ins, "CACHE", str(tmp_path / "u.json")); monkeypatch.setattr(ins, "_U", None)
    calls = []
    def down(url): calls.append(url); raise OSError("blocked")
    u1 = ins.get_universe(get=down)                       # first start: tries live once, falls back to seed
    n = len(calls); assert u1.meta["origin"] == "seed" and n > 0
    for _ in range(5): ins.get_universe(get=down)         # scheduler cycles within the retry window
    assert len(calls) == n
    assert ins.get_universe() is ins.get_universe()       # decoder path: no disk/network once loaded
