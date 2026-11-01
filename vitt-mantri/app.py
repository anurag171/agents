import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from engine.ai import apply_ai, configured as ai_configured, interpret_batch
from engine import snapshot as snapfile
from engine.cluster import cluster_items
from engine.decoder import aggregate_board, decode_item, grade_of
from engine.locks import FileLock
from engine.instruments import get_universe
from engine.assets_feed import build_assets
from engine.chartgate import chart_for, enrich_board
from engine.ipo_feed import build_ipos
from engine.scoring import score_board
from engine.movers import derive_lists, fetch_quotes, merge_into_board
from engine.scraper import scrape_all


def _load_user_llm_env():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("USER_LLM_") and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


_load_user_llm_env()

app = Flask(__name__)

STATE = {
    "news": [],
    "board": [],
    "errors": [],
    "coverage": [],
    "interpreter": "rules",
    "updated_at": None,
    "cycle": 0,
    "lock": threading.Lock(),
}

REFRESH_SECONDS = 90
DATA_DIR = os.getenv("VM_DATA", "data")
ROLE = os.getenv("VM_ROLE", "all")                      # all | scheduler | web  (web = never scrapes, only serves)
SNAP_PATH = os.path.join(DATA_DIR, "snapshot.json")
SCHED = {"pid": None, "lock": None, "active": False}


def _pack(decoded, board, errors, coverage, updated_at, cycle, interpreter="rules"):
    return {
        "updated_at": updated_at,
        "cycle": cycle,
        "count": len(decoded),
        "errors": errors,
        "coverage": coverage,
        "interpreter": interpreter,
        "movers": STATE.get("movers", {}),
        "assets": STATE.get("assets", []),
        "ipos": STATE.get("ipos", []),
        "ai_ready": ai_configured(),
        "universe": {"origin": get_universe().meta.get("origin"), "size": len(get_universe())},
        "news": decoded,
        "board": board,
        "buys": [r for r in board if r["action"] == "BUY"],
        "sells": [r for r in board if r["action"] == "SELL"],
        "watch": [r for r in board if r["action"] == "WATCH"],
    }


def _http_text(url):
    """Proxy/CA-aware fetch (honours HTTPS_PROXY, NO_PROXY, REQUESTS_CA_BUNDLE). NSE rejects bare clients."""
    import requests
    h = {"User-Agent": "Mozilla/5.0 (compatible; VittaMantri/2.0; research)", "Accept": "text/csv,application/json,*/*"}
    r = requests.get(url, headers=h, timeout=(3.05, 20))
    r.raise_for_status()
    return r.text


def _build():
    get_universe(get=_http_text)      # no-op while fresh; live refresh (daily) with disk-cache/seed fallback
    raw, errors, coverage = scrape_all()
    events = cluster_items(raw, grade_of)                    # same story from N outlets -> ONE event (+ corroboration)
    coverage = list(coverage) + [{"source": "Events", "raw": len(events), "articles": len(raw)}]
    decoded = []
    for item in events:
        parsed = decode_item(item)
        if parsed:
            decoded.append(parsed)
    ai_map, interpreter = interpret_batch(decoded)
    decoded = apply_ai(decoded, ai_map, interpreter)
    board = aggregate_board(decoded)
    quotes, market_cov = fetch_quotes()                      # cached ~2 min, never raises
    lists = derive_lists(quotes)
    board = merge_into_board(board, quotes, lists)           # market reaction on news rows + unexplained big movers
    board = enrich_board(board, require=os.getenv("VM_REQUIRE_CHART_CONFIRM") == "1")   # chart verdict + entry range
    board = score_board(board)                                                           # strength % + breakdown + minimum-strength gate
    assets, assets_cov = build_assets(decoded)                                           # commodities/FX/rates/indices/crypto
    ipos, ipo_cov = build_ipos(raw)                                                      # IPO insights
    coverage = list(coverage) + market_cov + assets_cov + ipo_cov
    with STATE["lock"]:
        STATE["movers"], STATE["assets"], STATE["ipos"] = lists, assets, ipos
        STATE["news"] = decoded
        STATE["board"] = board
        STATE["errors"] = errors
        STATE["coverage"] = coverage
        STATE["interpreter"] = interpreter
        STATE["updated_at"] = datetime.now(timezone.utc).isoformat()
        STATE["cycle"] += 1
        snap = _pack(decoded, board, errors, coverage, STATE["updated_at"], STATE["cycle"], interpreter)
    try:
        snapfile.write(SNAP_PATH, snap)                      # every gunicorn worker serves this one shared snapshot
    except OSError as exc:
        snap.setdefault("errors", []).append(f"snapshot write failed: {exc}")
    return snap


def _snapshot():
    """Web workers don't scrape: they serve the snapshot the scheduler process wrote (falls back to local state)."""
    shared = snapfile.read(SNAP_PATH)
    if shared is not None:
        return dict(shared)
    with STATE["lock"]:
        return _pack(list(STATE["news"]), list(STATE["board"]), list(STATE["errors"]), list(STATE["coverage"]),
                     STATE["updated_at"], STATE["cycle"], STATE.get("interpreter") or "rules")


def _build_exclusive():
    """Only one process builds at a time (scheduler tick vs a manual refresh landing on another worker)."""
    lk = FileLock(os.path.join(DATA_DIR, "build.lock"))
    if not lk.acquire():
        snap = _snapshot(); snap["busy"] = True
        return snap
    try:
        return _build()
    finally:
        lk.release()


def _loop():
    while True:
        try:
            _build_exclusive()
        except Exception as exc:
            with STATE["lock"]:
                STATE["errors"] = [str(exc)]
        time.sleep(REFRESH_SECONDS)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    snap = _snapshot()
    return jsonify({"ok": True, "updated_at": snap.get("updated_at"), "cycle": snap.get("cycle"), "role": ROLE, "scheduler_here": SCHED["active"], "pid": os.getpid()})


@app.route("/api/briefing")
def briefing():
    ticker = (request.args.get("ticker") or "").upper().strip()
    snap = _snapshot()
    if ticker:
        snap["board"] = [r for r in snap["board"] if r["ticker"] == ticker]
        snap["news"] = [
            n for n in snap["news"]
            if any(s["ticker"] == ticker for s in n.get("signals", []))
            or any(x.get("ticker") == ticker for x in n.get("named", []))
        ]
        snap["buys"] = [r for r in snap["board"] if r["action"] == "BUY"]
        snap["sells"] = [r for r in snap["board"] if r["action"] == "SELL"]
        snap["watch"] = [r for r in snap["board"] if r["action"] == "WATCH"]
    return jsonify(snap)


@app.route("/api/refresh", methods=["POST"])
def refresh():
    tok = os.getenv("VM_ADMIN_TOKEN", "")
    if tok and not hmac.compare_digest(request.headers.get("X-Admin-Token", ""), tok):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_build_exclusive())


@app.route("/api/chart/<ticker>")
def chart(ticker):
    action = "SELL" if request.args.get("action", "BUY").upper() == "SELL" else "BUY"
    payload = chart_for(ticker.upper()[:20], action, "US" if request.args.get("mkt", "IN").upper() == "US" else "IN")
    return (jsonify(payload), 200) if payload else (jsonify({"error": "no price history"}), 404)


@app.route("/stream")
def stream():
    def gen():
        last = -1
        while True:
            snap = _snapshot()
            token = (snap.get("cycle"), snap.get("updated_at"))      # cycle numbers differ across processes
            if token != last:
                last = token
                yield f"data: {json.dumps(snap)}\n\n"
            else:
                yield ": ping\n\n"
            time.sleep(4)

    return Response(stream_with_context(gen()), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


def _try_schedule():
    """Try to become THE scheduler (non-blocking, process-wide election via flock)."""
    lk = FileLock(os.path.join(DATA_DIR, "scheduler.lock"))
    if lk.acquire():
        SCHED["lock"], SCHED["active"] = lk, True
        return True
    return False


def _supervisor():
    """Every process runs this; exactly one wins and scrapes. If the winner dies the OS frees the lock and another
    worker takes over within ~15 s."""
    while True:
        if not SCHED["active"] and _try_schedule():
            _loop()                                      # never returns
        time.sleep(15)


def _ensure_supervisor():
    """Idempotent per process; also correct after fork (gunicorn --preload) because it keys on the pid."""
    if ROLE == "web" or SCHED["pid"] == os.getpid():
        return
    SCHED.update(pid=os.getpid(), lock=None, active=False)
    threading.Thread(target=_supervisor, daemon=True, name="vm-scheduler").start()


@app.before_request
def _boot():
    _ensure_supervisor()


start_worker = _ensure_supervisor            # backwards-compatible name
_ensure_supervisor()
