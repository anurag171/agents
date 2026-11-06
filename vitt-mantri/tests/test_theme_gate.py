import json, random, sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from engine import signal_log as L
from engine import scoring
from engine.store import DDL, SCHEMA_VERSION, Store

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path): return Store(str(tmp_path / "t.sqlite"))


def add_calls(st, theme, kind="stock", days=40, per_day=1, mean=0.0, sd=1.0, seed=1, start=None, ticker="T"):
    """days x per_day settled calls for a theme; excess return ~ N(mean, sd) per DAY (calls on a day share the day's move)."""
    rnd, start = random.Random(seed), start or (NOW - timedelta(days=days + 7))
    with st.conn() as c:
        for i in range(days):
            day = (start + timedelta(days=i)).date().isoformat(); y = rnd.gauss(mean, sd)
            for j in range(per_day):
                cur = c.execute("INSERT INTO signals_log(emitted_at,emitted_date,kind,ticker,yahoo,mkt,direction,news_action,final_action,scoring_version,primary_theme) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (day + "T10:00:00+00:00", day, kind, f"{theme}_{ticker}{i}_{j}", "X", "IN", 1, "BUY", "BUY", scoring.DEFAULT_VERSION, theme))
                c.execute("INSERT INTO signal_outcomes(signal_id,horizon,signed_ret_pct,signed_excess_pct) VALUES(?,?,?,?)", (cur.lastrowid, 5, y + rnd.gauss(0, 0.1), y + rnd.gauss(0, 0.1)))


def status(st, theme, kind="stock"):
    with st.conn() as c: return c.execute("SELECT * FROM theme_gate WHERE theme_id=? AND kind=?", (theme, kind)).fetchone()


def test_v1_database_migrates_to_v2_in_place(tmp_path):
    p = str(tmp_path / "old.sqlite"); c = sqlite3.connect(p); c.executescript(DDL); c.execute("PRAGMA user_version=1")
    c.execute("INSERT INTO signals_log(emitted_at,emitted_date,kind,ticker,direction,news_action,final_action,scoring_version) VALUES('2026-01-01T00:00:00','2026-01-01','stock','OLD',1,'BUY','BUY','s1')"); c.commit(); c.close()
    s = Store(p)
    with s.conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION and c.execute("SELECT ticker, primary_theme FROM signals_log").fetchone()["primary_theme"] is None
        assert {"theme_gate", "theme_gate_log", "tuning_runs"} <= {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    Store(p)                                                                      # idempotent


def test_calls_record_which_theme_drove_them_and_shadow_reason(st):
    r = {"ticker": "ONGC", "action": "WATCH", "news_action": "BUY", "net": 4, "score_pct": 70, "primary_theme": "oil_up", "demoted_theme": True, "themes": [{"id": "oil_up", "label": "Oil up", "weight": 3}], "sources": ["R"], "events": 1}
    assert L.record(st, [r], [], NOW) == 1
    with st.conn() as c: row = c.execute("SELECT * FROM signals_log").fetchone()
    assert (row["primary_theme"], row["gate_reason"], row["final_action"]) == ("oil_up", "theme_demoted", "WATCH") and json.loads(row["themes_json"])[0]["id"] == "oil_up"


def test_same_day_calls_are_one_independent_observation_not_many(st):
    add_calls(st, "oil_up", days=1, per_day=45, mean=-3.0)                       # 45 calls, ONE day: looks decisive, is one data point
    obs = L.theme_observations(st, 5, NOW)[("oil_up", "stock")]
    assert obs["n_calls"] == 45 and len(obs["obs"]) == 1
    L.update_theme_gates(st, now=NOW); assert status(st, "oil_up")["status"] == "building" and L.demoted_themes(st, "stock") == frozenset()


def test_status_classification_poor_unproven_proven_building(st):
    add_calls(st, "bad", mean=-1.5, sd=1.0, seed=1); add_calls(st, "noisy", mean=0.1, sd=3.0, seed=2)
    add_calls(st, "good", mean=1.5, sd=1.0, seed=3); add_calls(st, "young", days=12, mean=-5, seed=4)
    L.update_theme_gates(st, now=NOW)
    got = {t: status(st, t)["status"] for t in ("bad", "noisy", "good", "young")}
    assert got == {"bad": "poor", "noisy": "unproven", "good": "proven", "young": "building"}
    assert L.demoted_themes(st, "stock") == frozenset({"bad"})                  # an UNPROVEN theme is never switched off
    b = status(st, "bad"); assert b["ci_hi"] <= 0 and b["n_independent"] == 40


def test_demotion_needs_strong_evidence_99_percent_interval_is_wider(st):
    xs = [random.Random(5).gauss(-0.3, 1.5) for _ in range(1)]
    obs = [random.Random(i).gauss(-0.35, 1.5) for i in range(40)]
    lo95, hi95 = L.bootstrap_mean_ci(obs, 0.95); lo99, hi99 = L.bootstrap_mean_ci(obs, 0.99)
    assert (hi99 - lo99) > (hi95 - lo95)


def test_window_ignores_old_history(st):
    add_calls(st, "faded", days=40, mean=-1.5, start=NOW - timedelta(days=400))   # a year-old failure must not condemn the theme today
    assert ("faded", "stock") not in L.theme_observations(st, 5, NOW)


def test_hysteresis_dwell_time_and_audit_log(st):
    add_calls(st, "x", days=40, mean=-1.5, sd=1.0, seed=1, start=NOW - timedelta(days=60)); L.update_theme_gates(st, now=NOW - timedelta(days=20))
    assert status(st, "x")["status"] == "poor"
    # later data turn mildly positive, but the upper bound hasn't cleared the margin -> still poor
    add_calls(st, "x", days=40, mean=0.05, sd=1.0, seed=9, start=NOW - timedelta(days=39), ticker="U"); changes = L.update_theme_gates(st, now=NOW)
    s = status(st, "x"); assert s["status"] in ("poor", "unproven")
    # recovery: strongly positive recent run, dwell time long passed
    add_calls(st, "x", days=60, mean=3.0, sd=1.0, seed=10, start=NOW - timedelta(days=59), ticker="V"); L.update_theme_gates(st, now=NOW + timedelta(days=1))
    assert status(st, "x")["status"] in ("unproven", "proven") and L.demoted_themes(st, "stock") == frozenset()
    log = L.theme_health(st, {"x": "Theme X"})["log"]
    assert [e["new_status"] for e in log][-1] == "poor" and log[0]["new_status"] in ("unproven", "proven") and "re-enable" in log[0]["reason"] or "left 'poor'" in log[0]["reason"]
    assert L.theme_health(st, {"x": "Theme X"})["themes"][0]["label"] == "Theme X"


def test_poor_theme_stays_down_until_dwell_time_even_if_data_flips(st):
    add_calls(st, "y", days=40, mean=-1.5, seed=1, start=NOW - timedelta(days=60)); L.update_theme_gates(st, now=NOW - timedelta(days=3))
    add_calls(st, "y", days=80, mean=4.0, seed=2, start=NOW - timedelta(days=79), ticker="W")                  # overwhelming recovery, but only 3 days in 'poor'
    L.update_theme_gates(st, now=NOW); assert status(st, "y")["status"] == "poor"                              # dwell (14 d) not served
    L.update_theme_gates(st, now=NOW + timedelta(days=12)); assert status(st, "y")["status"] != "poor"


def test_demoted_calls_become_watch_but_keep_being_logged_so_the_theme_can_recover(st):
    board = [{"ticker": "ONGC", "action": "BUY", "net": 4, "score_pct": 80, "primary_theme": "oil_up", "why": "w", "sources": ["R"], "events": 1},
             {"ticker": "TCS", "action": "BUY", "net": 4, "score_pct": 80, "primary_theme": "ai_boom", "why": "w", "sources": ["R"], "events": 1}]
    L.apply_theme_demotions(board, frozenset({"oil_up"}))
    assert (board[0]["action"], board[0]["news_action"], board[0]["demoted_theme"]) == ("WATCH", "BUY", True) and "theme demoted" in board[0]["why"] and board[1]["action"] == "BUY"
    assert L.record(st, board, [], NOW) == 2                                                                   # the demoted call is STILL logged (shadow)
    with st.conn() as c: got = {r["ticker"]: (r["final_action"], r["gate_reason"]) for r in c.execute("SELECT * FROM signals_log")}
    assert got == {"ONGC": ("WATCH", "theme_demoted"), "TCS": ("BUY", None)}


def test_assets_are_gated_separately_from_stocks(st):
    add_calls(st, "gold_bid", kind="asset", mean=-1.5, seed=1); add_calls(st, "gold_bid", kind="stock", mean=1.5, seed=2, ticker="S")
    L.update_theme_gates(st, now=NOW)
    assert L.demoted_themes(st, "asset") == frozenset({"gold_bid"}) and L.demoted_themes(st, "stock") == frozenset()


def test_api_theme_health(tmp_path, monkeypatch):
    import app
    monkeypatch.setenv("VM_DB", str(tmp_path / "a.sqlite")); s = Store(); add_calls(s, "oil_up", mean=-1.5); L.update_theme_gates(s, now=NOW)
    j = app.app.test_client().get("/api/theme-health").get_json()
    assert j["themes"][0]["status"] == "poor" and j["themes"][0]["label"] and "upper 99% bound" in j["rule"] and j["log"][0]["new_status"] == "poor"


def test_evaluation_cycle_in_the_app_refreshes_theme_health(tmp_path, monkeypatch):
    import app
    monkeypatch.setenv("VM_DB", str(tmp_path / "e.sqlite")); s = Store()
    add_calls(s, "rbi_pause", mean=-1.5, seed=1, start=datetime.now(timezone.utc) - timedelta(days=47))
    monkeypatch.setattr(app, "evaluate_outcomes", lambda st: {"due": 0, "settled": 0, "symbols": 0})      # settling needs prices; theme check must still run
    res = app._maybe_evaluate(force=True)
    assert [(c["theme"], c["to"]) for c in res["theme_changes"]] == [("rbi_pause", "poor")]
    assert app._maybe_evaluate() is None                                                                    # rate-limited: not re-run on the next cycle
