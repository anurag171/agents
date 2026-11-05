"""SQLite (WAL) storage for the signals log. Append-only facts (signals_log) + derived facts (signal_outcomes).
One short-lived connection per operation, so it is safe across threads and gunicorn workers. The SQL is plain
ANSI-ish on purpose: moving to Postgres later means swapping this module's connect(), not rewriting queries.
Put VM_DATA on a PERSISTENT volume (an OpenShift PVC); a pod's own filesystem is wiped on restart."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

SCHEMA_VERSION = 4
DDL = """
CREATE TABLE IF NOT EXISTS signals_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  emitted_at TEXT NOT NULL, emitted_date TEXT NOT NULL,
  kind TEXT NOT NULL, ticker TEXT NOT NULL, yahoo TEXT, mkt TEXT,
  direction INTEGER NOT NULL, news_action TEXT NOT NULL, final_action TEXT NOT NULL, gate_reason TEXT,
  score_pct INTEGER, strength TEXT, coverage_pct INTEGER, limited_data INTEGER, net REAL,
  chart_verdict TEXT, chart_score REAL, market_change_pct REAL, priced_in INTEGER,
  best_grade TEXT, n_events INTEGER, n_sources INTEGER, price_at_signal REAL,
  breakdown_json TEXT, evidence_json TEXT, scoring_version TEXT NOT NULL, engine_version TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_day ON signals_log(kind, ticker, direction, emitted_date);
CREATE INDEX IF NOT EXISTS ix_signals_time ON signals_log(emitted_at);
CREATE TABLE IF NOT EXISTS signal_outcomes(
  signal_id INTEGER NOT NULL REFERENCES signals_log(id), horizon INTEGER NOT NULL,
  entry_price REAL, entry_source TEXT, exit_date TEXT, exit_price REAL,
  ret_pct REAL, signed_ret_pct REAL, bench_ret_pct REAL, signed_excess_pct REAL, settled_at TEXT,
  PRIMARY KEY(signal_id, horizon));
CREATE TABLE IF NOT EXISTS signal_state(
  kind TEXT NOT NULL, ticker TEXT NOT NULL, direction INTEGER NOT NULL, since TEXT NOT NULL, PRIMARY KEY(kind, ticker));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


MIGRATIONS = {
    2: """
ALTER TABLE signals_log ADD COLUMN primary_theme TEXT;
ALTER TABLE signals_log ADD COLUMN themes_json TEXT;
CREATE TABLE IF NOT EXISTS theme_gate(
  theme_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, n_calls INTEGER, n_independent INTEGER,
  mean_excess REAL, ci_lo REAL, ci_hi REAL, since TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(theme_id, kind));
CREATE TABLE IF NOT EXISTS theme_gate_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, theme_id TEXT NOT NULL, kind TEXT NOT NULL,
  old_status TEXT, new_status TEXT NOT NULL, reason TEXT, stats_json TEXT);
CREATE TABLE IF NOT EXISTS tuning_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, horizon INTEGER, n_rows INTEGER, train_range TEXT, val_range TEXT, test_range TEXT,
  test_start TEXT, test_end TEXT, base_json TEXT, cand_json TEXT, metrics_json TEXT, decision TEXT, test_look INTEGER, adopted INTEGER DEFAULT 0);
""",
    3: """
CREATE TABLE IF NOT EXISTS fundamentals(symbol TEXT PRIMARY KEY, source TEXT, fetched_at TEXT NOT NULL, json TEXT NOT NULL);
""",
    4: """
ALTER TABLE tuning_runs ADD COLUMN target TEXT DEFAULT 'news';
CREATE TABLE IF NOT EXISTS screen_panel(
  run_date TEXT NOT NULL, ticker TEXT NOT NULL, version TEXT NOT NULL, yahoo TEXT, price REAL, score_pct INTEGER, tier TEXT, coverage_pct INTEGER,
  comps_json TEXT NOT NULL, PRIMARY KEY(run_date, ticker, version));
CREATE TABLE IF NOT EXISTS screen_panel_outcomes(
  run_date TEXT NOT NULL, ticker TEXT NOT NULL, version TEXT NOT NULL, horizon INTEGER NOT NULL, exit_date TEXT, ret_pct REAL, bench_ret_pct REAL,
  excess_pct REAL, settled_at TEXT, PRIMARY KEY(run_date, ticker, version, horizon));
""",
}


def db_path() -> str:
    return os.getenv("VM_DB") or os.path.join(os.getenv("VM_DATA", "data"), "vm.sqlite")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self.conn() as c:
            v = c.execute("PRAGMA user_version").fetchone()[0]
            if v < 1:
                c.executescript(DDL); v = 1
            for target in sorted(MIGRATIONS):
                if v < target:
                    c.executescript(MIGRATIONS[target]); v = target
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=15000"); c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()

    def meta_get(self, k, default=None):
        with self.conn() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def meta_set(self, k, v):
        with self.conn() as c:
            c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
