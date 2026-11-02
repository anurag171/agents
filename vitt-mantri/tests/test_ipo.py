from engine.ipo import Ipo, insight, is_ipo_headline, parse_ipo_headline

STRONG = dict(name="GoodCo", status="closed", pe=18, peer_pe=25, profitable=True, rev_growth_pct=22, roe=19, debt_equity=0.4,
              issue_size_cr=1000, ofs_cr=200, fresh_cr=800, promoter_post_pct=68, sub_total=45, sub_qib=60, anchor_cr=350)
WEAK = dict(name="BadCo", status="closed", pe=60, peer_pe=25, profitable=False, rev_growth_pct=2, roe=3, debt_equity=3.1,
            issue_size_cr=1000, ofs_cr=900, promoter_post_pct=35, sub_total=0.6, sub_qib=0.4)


def test_strong_vs_weak_vs_sparse():
    assert insight(Ipo(**STRONG))["verdict"] == "FAVOURABLE"
    w = insight(Ipo(**WEAK)); assert w["verdict"] == "CAUTION" and "under-subscribed" in w["flags"]
    assert insight(Ipo(name="X", status="upcoming"))["verdict"] == "INSUFFICIENT DATA"


def test_gmp_is_flagged_not_scored():
    a, b = insight(Ipo(**STRONG)), insight(Ipo(**STRONG, gmp_pct=40))
    assert a["listing_score"] == b["listing_score"] and any("GMP" in f for f in b["flags"])


def test_scores_bounded_and_missing_data_lowers_coverage():
    full, part = insight(Ipo(**STRONG)), insight(Ipo(name="P", status="closed", pe=18, peer_pe=25, sub_total=45))
    assert 0 <= full["listing_score"] <= 100 and part["coverage"] < full["coverage"]


def test_headline_parsing_and_listing_gain():
    t = "Acme Fasteners IPO opens today, price band Rs 100-105 per share"
    assert is_ipo_headline(t) and parse_ipo_headline(t) == {"name": "Acme Fasteners", "price_band": (100.0, 105.0)}
    assert not is_ipo_headline("Nifty ends higher on banks")
    assert insight(Ipo(name="L", status="listed", issue_price=100, listing_price=118))["listing_gain_pct"] == 18.0
    assert insight(Ipo(name="M", price_band=(95, 100), lot_size=150))["terms"]["min_investment"] == 15000
