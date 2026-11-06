from engine.cluster import cluster_items, similarity, tokens
from engine.decoder import aggregate_board, corroboration, decode_item, grade_of

NOW = "2026-10-20T08:00:00+00:00"
def art(src, title, at=NOW, summary=""): return {"id": src + title[:6], "source": src, "title": title, "url": f"http://{src}/x", "summary": summary, "published_at": at, "fetched_at": at, "region": "Global"}
BRENT = "Brent crude jumps 4% as Hormuz tensions flare"


def test_same_story_from_five_outlets_is_one_event_with_best_source_as_representative():
    items = [art(s, BRENT + sfx) for s, sfx in [("Moneycontrol", ""), ("Reuters", ""), ("Economic Times", " - Economic Times"), ("MSN Money", ""), ("Yahoo Finance", "")]]
    ev = cluster_items(items, grade_of)
    assert len(ev) == 1 and ev[0]["source"] == "Reuters" and ev[0]["n_sources"] == 5 and ev[0]["cluster_size"] == 5 and len(ev[0]["also"]) == 4


def test_paraphrases_with_shared_numbers_merge_but_different_stories_do_not():
    ev = cluster_items([art("Reuters", "RBI keeps repo rate unchanged at 6.5%"), art("Economic Times", "RBI holds repo rate steady at 6.5%"),
                        art("Moneycontrol", "Infosys wins large cloud deal from European bank"), art("Reuters", "Nifty falls"), art("Moneycontrol", "Nifty rises")], grade_of)
    assert sorted(e["cluster_size"] for e in ev) == [1, 1, 1, 2]
    assert similarity(tokens("Nifty falls"), tokens("Nifty rises")) == 0.0                 # too few shared tokens to trust


def test_outside_48h_window_is_a_new_event():
    ev = cluster_items([art("Reuters", BRENT, NOW), art("Reuters", BRENT, "2026-10-17T08:00:00+00:00")], grade_of)
    assert len(ev) == 2


def test_wire_copy_counts_as_one_origin():
    wire = "(PTI) RBI keeps repo rate unchanged at 6.5 per cent, says governor"
    ev = cluster_items([art("Economic Times", "RBI keeps repo rate unchanged at 6.5% amid inflation worries", summary=wire),
                        art("Business Standard", "RBI keeps repo rate unchanged at 6.5% amid inflation worries", summary=wire),
                        art("Moneycontrol", "RBI keeps repo rate unchanged at 6.5% amid inflation worries", summary=wire),
                        art("Reuters", "RBI keeps repo rate unchanged at 6.5% amid inflation worries")], grade_of)
    assert len(ev) == 1 and ev[0]["cluster_size"] == 4 and ev[0]["n_sources"] == 2          # PTI x3 = one origin, Reuters = another


def test_board_weight_is_capped_not_multiplied_by_outlet_count():
    sig = lambda: [{"ticker": "ONGC", "confidence": 1.0, "hop": "direct", "action": "BUY", "why": "w"}]
    one = aggregate_board([{"signals": sig(), "title": "t", "source": "Reuters", "sources": ["Reuters"], "n_sources": 1, "region": "G"}])[0]["net"]
    five = aggregate_board([{"signals": sig(), "title": "t", "source": "Reuters", "sources": ["Reuters"] * 5, "n_sources": 5, "region": "G"}])[0]["net"]
    old_behaviour = aggregate_board([{"signals": sig(), "title": f"t{i}", "source": f"S{i}", "n_sources": 1, "region": "G"} for i in range(5)])[0]["net"]
    assert one < five <= one * 1.75 + 0.01 and old_behaviour == pytest.approx(5 * one, rel=0.01)
    assert corroboration(1) == 1.0 and corroboration(2) == 1.25 and corroboration(9) == 1.75


def test_end_to_end_five_headlines_no_longer_inflate_the_board():
    raw = [art(s, BRENT) for s in ("Reuters", "Moneycontrol", "Economic Times", "MSN Money", "Yahoo Finance")]
    from datetime import datetime, timezone
    t = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)                       # the headlines' own clock: no decay between them and "now"
    board_old = aggregate_board([d for d in map(decode_item, raw) if d], t)
    board_new = aggregate_board([d for d in map(decode_item, cluster_items(raw, grade_of)) if d], t)
    old = {r["ticker"]: abs(r["net"]) for r in board_old}; new = {r["ticker"]: abs(r["net"]) for r in board_new}
    assert old and all(new[t] < old[t] for t in new) and all(new[t] <= old[t] / 5 * 1.75 + 0.01 for t in new)
    assert board_new[0]["events"] == 1 and len(board_new[0]["sources"]) == 5


import pytest


def test_old_news_counts_less_and_untimed_news_is_capped():
    from datetime import datetime, timedelta, timezone
    from engine.decoder import HALF_LIFE_H, freshness
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc); at = lambda h: (now - timedelta(hours=h)).isoformat()
    assert freshness({"published_at": at(0)}, now) == pytest.approx(1.0)
    assert freshness({"published_at": at(HALF_LIFE_H)}, now) == pytest.approx(0.5) and freshness({"published_at": at(3 * HALF_LIFE_H)}, now) == pytest.approx(0.125)
    assert freshness({"published_at": None, "fetched_at": at(0), "published_estimated": True}, now) == pytest.approx(0.5)      # no real timestamp: capped
    assert freshness({}, now) == pytest.approx(1.0)                                                                                # missing data never crashes
    sig = lambda: [{"ticker": "ONGC", "confidence": 1.0, "hop": "direct", "action": "BUY", "why": "w"}]
    mk = lambda h: [{"signals": sig(), "title": "t", "source": "Reuters", "sources": ["Reuters"], "n_sources": 1, "region": "G", "published_at": at(h)}]
    fresh, stale = aggregate_board(mk(0), now)[0]["net"], aggregate_board(mk(24), now)[0]["net"]
    assert stale == pytest.approx(fresh / 4, rel=0.01)                                                                             # 24 h = two half-lives
