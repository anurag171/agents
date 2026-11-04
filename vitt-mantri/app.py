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
from engine.instruments import grow_universe
from engine.screener import SCREEN_VERSION, run_screen
from engine.decoder import THEMES, aggregate_board, decode_item, grade_of
from engine.locks import FileLock
from engine.instruments import get_universe
from engine.assets_feed import build_assets
from engine.chartgate import chart_for, enrich_board
from engine.ipo_feed import build_ipos
from engine.scoring import score_board
from engine.signal_log import HORIZONS, record_screen, apply_theme_demotions, attach_history, demoted_themes, evaluate_outcomes, export_csv, performance, record, theme_health, update_theme_gates
from engine.store import Store
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
DISC_PATH = os.path.join(DATA_DIR, "discovery.json")
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
    try:
        uni = get_universe()
        if grow_universe(uni, quotes):                       # every NSE symbol in the live snapshot becomes a known instrument
            uni.save()
    except Exception as exc:
        errors.append(f"universe growth: {type(exc).__name__}: {exc}")
    lists = derive_lists(quotes)
    board = merge_into_board(board, quotes, lists)           # market reaction on news rows + unexplained big movers
    board = enrich_board(board, require=os.getenv("VM_REQUIRE_CHART_CONFIRM") == "1")   # chart verdict + entry range
    STATE["news_symbols"] = {r["ticker"] for r in board if r.get("origin") != "market-mover" and r.get("headlines")}
    board = score_board(board)                                                           # strength % + breakdown + minimum-strength gate
    assets, assets_cov = build_assets(decoded)
    try:                                                                                 # log every call; attach the observed track record to each %
        st = Store()
        dem = demoted_themes(st, "stock")                                                 # themes whose calls have clearly not beaten the market
        board = apply_theme_demotions(board, dem); assets = apply_theme_demotions(assets, demoted_themes(st, "asset"))
        for d in decoded:
            d["theme_demoted"] = d.get("theme_id") in dem
        record(st, board, assets, engine_version=os.getenv("VM_VERSION", "dev"))
        perf = performance(st); attach_history(board, perf); attach_history(assets, perf)
    except Exception as exc:
        errors.append(f"signals log: {type(exc).__name__}: {exc}")                                           # commodities/FX/rates/indices/crypto
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
        out = dict(shared); out["discovery"] = snapfile.read(DISC_PATH)
        return out
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


SCREEN = {"running": False}
SCREEN_FILL_EVERY_S = int(os.getenv("VM_SCREEN_FILL_EVERY_MIN", "30")) * 60       # while fundamentals are still being filled in
SCREEN_EVERY_S = int(os.getenv("VM_SCREEN_EVERY_H", "12")) * 3600                 # once the fill is complete


def _screen_job(news_symbols):
    try:
        st = Store()
        payload = run_screen(st, get_universe(), news_symbols=news_symbols, max_symbols=int(os.getenv("VM_SCREEN_MAX", "600")))
        payload["logged"] = record_screen(st, payload["picks"], SCREEN_VERSION, engine_version=os.getenv("VM_VERSION", "dev"))
        u = payload["universe"]
        st.meta_set("screen_fill", f"{u['with_fundamentals'] / max(1, u['eligible']):.3f}")
        snapfile.write(DISC_PATH, payload)
    except Exception as exc:
        st = Store(); st.meta_set("screen_error", f"{type(exc).__name__}: {exc}")
    finally:
        SCREEN["running"] = False


def _maybe_screen(force=False):
    """Background thread (hundreds of price/fundamentals requests must never stall the 90 s news cycle)."""
    if SCREEN["running"]:
        return False
    st = Store(); last, fill = st.meta_get("last_screen_at"), float(st.meta_get("screen_fill", "0") or 0)
    every = SCREEN_FILL_EVERY_S if fill < 0.9 else SCREEN_EVERY_S
    if not force and last and time.time() - float(last) < every:
        return False
    st.meta_set("last_screen_at", time.time())              # rate-limit attempts, not just successes
    SCREEN["running"] = True
    with STATE["lock"]:
        ns = set(STATE.get("news_symbols") or ())
    threading.Thread(target=_screen_job, args=(ns,), daemon=True, name="vm-screen").start()
    return True


EVAL_EVERY_S = int(os.getenv("VM_EVAL_EVERY_MIN", "180")) * 60


def _maybe_evaluate(force=False):
    """Settle calls whose horizon has passed (needs price history, so it is rate-limited and isolated from the scrape cycle)."""
    st = Store(); last = st.meta_get("last_eval_at")
    if not force and last and time.time() - float(last) < EVAL_EVERY_S:
        return None
    st.meta_set("last_eval_at", time.time())
    res = evaluate_outcomes(st)
    res["theme_changes"] = update_theme_gates(st)                                         # re-check theme health whenever outcomes settle
    st.meta_set("last_eval_result", json.dumps(res))
    return res


def _admin_ok():
    tok = os.getenv("VM_ADMIN_TOKEN", "")
    return not tok or hmac.compare_digest(request.headers.get("X-Admin-Token", ""), tok)


_PERF = {}


@app.route("/api/performance")
def api_performance():
    h = request.args.get("horizon", type=int) or 5
    h = h if h in HORIZONS else 5
    kind = request.args.get("kind") if request.args.get("kind") in ("stock", "asset", "screen") else None
    hit = _PERF.get((h, kind))
    if hit and time.time() - hit[0] < 30:
        return jsonify(hit[1])
    out = performance(Store(), h, scoring_version=SCREEN_VERSION, kind=kind) if kind == "screen" else performance(Store(), h, kind=kind)
    out["last_eval_at"] = Store().meta_get("last_eval_at")
    _PERF[(h, kind)] = (time.time(), out)
    return jsonify(out)


@app.route("/api/theme-health")
def api_theme_health():
    return jsonify(theme_health(Store(), {k: v.get("label", k) for k, v in THEMES.items()}))


@app.route("/api/discovery")
def api_discovery():
    d = snapfile.read(DISC_PATH)
    if d is None:
        return jsonify({"picks": [], "watchlist": [], "pending": True, "message": "The first screen has not finished yet."})
    out = dict(d); perf = performance(Store(), 20, scoring_version=SCREEN_VERSION, kind="screen")
    out["track"] = {"horizon": 20, "settled": perf["settled"], "pending": perf["pending"], "overall": perf["overall"]}
    return jsonify(out)


@app.route("/api/signals.csv")
def api_signals_csv():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    return Response(export_csv(Store()), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=signals_log.csv"})


@app.route("/api/evaluate", methods=["POST"])
def api_evaluate():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_maybe_evaluate(force=True))


def _loop():
    while True:
        try:
            _build_exclusive()
            _maybe_evaluate()
            _maybe_screen()
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
