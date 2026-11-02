import json, os, subprocess, sys, threading
import pytest
from engine import snapshot as snap
from engine.locks import FileLock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = "import sys; sys.path.insert(0, %r); from engine.locks import FileLock; sys.exit(0 if FileLock(%%r).acquire() else 1)" % ROOT


def test_lock_is_exclusive_within_and_across_processes(tmp_path):
    p = str(tmp_path / "s.lock"); a, b = FileLock(p), FileLock(p)
    assert a.acquire() and a.acquire() and not b.acquire()                    # re-acquire by owner is fine, rival is refused
    assert subprocess.run([sys.executable, "-c", PROBE % p]).returncode == 1  # a different PROCESS is refused too
    a.release()
    assert subprocess.run([sys.executable, "-c", PROBE % p]).returncode == 0  # holder gone -> next process wins
    assert b.acquire()


HOLDER = "import sys, time; sys.path.insert(0, %r); from engine.locks import FileLock; l = FileLock(%%r); assert l.acquire(); print('held', flush=True); time.sleep(60)" % ROOT


def test_lock_is_released_when_holder_process_dies(tmp_path):
    p = str(tmp_path / "s.lock")
    holder = subprocess.Popen([sys.executable, "-c", HOLDER % p], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held" and not FileLock(p).acquire()
    holder.kill(); holder.wait()
    assert FileLock(p).acquire()                                              # crash of the scheduler worker -> takeover possible


def test_snapshot_roundtrip_cache_invalidation_and_bad_files(tmp_path):
    p = str(tmp_path / "s.json")
    assert snap.read(p) is None
    snap.write(p, {"cycle": 1}); assert snap.read(p) == {"cycle": 1}
    snap.write(p, {"cycle": 2, "pad": "x" * 50}); assert snap.read(p)["cycle"] == 2
    open(p, "w").write("{not json"); assert snap.read(p) is None


def test_readers_never_see_a_partial_snapshot_while_writer_runs(tmp_path):
    p, stop, bad = str(tmp_path / "s.json"), threading.Event(), []
    snap.write(p, {"n": 0, "pad": "x" * 200_000})
    def writer():
        for i in range(1, 150): snap.write(p, {"n": i, "pad": "x" * 200_000})
        stop.set()
    t = threading.Thread(target=writer); t.start()
    while not stop.is_set():
        d = snap.read(p)
        if d is None or d["pad"] != "x" * 200_000: bad.append(d and d["n"])
    t.join(); assert bad == []


def test_app_web_worker_serves_the_shared_snapshot_and_reports_role(tmp_path, monkeypatch):
    import app
    path = str(tmp_path / "snapshot.json"); monkeypatch.setattr(app, "SNAP_PATH", path)
    snap.write(path, {"cycle": 7, "updated_at": "2026-10-20T08:00:00+00:00", "board": [{"ticker": "ONGC", "action": "BUY"}], "news": [], "ipos": [], "assets": []})
    c = app.app.test_client()
    assert c.get("/api/briefing").get_json()["board"][0]["ticker"] == "ONGC"
    h = c.get("/health").get_json(); assert h["cycle"] == 7 and h["role"] == "web" and h["scheduler_here"] is False


def test_only_one_process_can_become_scheduler_and_takeover_works(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path)); monkeypatch.setitem(app.SCHED, "active", False); monkeypatch.setitem(app.SCHED, "lock", None)
    rival = FileLock(str(tmp_path / "scheduler.lock")); assert rival.acquire()
    assert app._try_schedule() is False and app.SCHED["active"] is False       # another worker already schedules
    rival.release()
    assert app._try_schedule() is True and app.SCHED["active"] is True         # it died -> we take over
    app.SCHED["lock"].release()


def test_manual_refresh_cannot_overlap_a_running_build(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path)); monkeypatch.setattr(app, "SNAP_PATH", str(tmp_path / "snapshot.json"))
    busy = FileLock(str(tmp_path / "build.lock")); assert busy.acquire()
    out = app._build_exclusive(); assert out["busy"] is True
    busy.release()
    called = []; monkeypatch.setattr(app, "_build", lambda: called.append(1) or {"ok": 1})
    assert app._build_exclusive() == {"ok": 1} and called == [1]
