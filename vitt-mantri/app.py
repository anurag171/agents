import json
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from engine.decoder import aggregate_board, decode_item
from engine.scraper import scrape_all

app = Flask(__name__)

STATE = {
    "news": [],
    "board": [],
    "errors": [],
    "coverage": [],
    "updated_at": None,
    "cycle": 0,
    "lock": threading.Lock(),
}

REFRESH_SECONDS = 90


def _pack(decoded, board, errors, coverage, updated_at, cycle):
    return {
        "updated_at": updated_at,
        "cycle": cycle,
        "count": len(decoded),
        "errors": errors,
        "coverage": coverage,
        "news": decoded,
        "board": board,
        "buys": [r for r in board if r["action"] == "BUY"],
        "sells": [r for r in board if r["action"] == "SELL"],
        "watch": [r for r in board if r["action"] == "WATCH"],
    }


def _build():
    raw, errors, coverage = scrape_all()
    decoded = []
    for item in raw:
        parsed = decode_item(item)
        if parsed:
            decoded.append(parsed)
    board = aggregate_board(decoded)
    with STATE["lock"]:
        STATE["news"] = decoded
        STATE["board"] = board
        STATE["errors"] = errors
        STATE["coverage"] = coverage
        STATE["updated_at"] = datetime.now(timezone.utc).isoformat()
        STATE["cycle"] += 1
        return _pack(decoded, board, errors, coverage, STATE["updated_at"], STATE["cycle"])


def _snapshot():
    with STATE["lock"]:
        return _pack(
            list(STATE["news"]),
            list(STATE["board"]),
            list(STATE["errors"]),
            list(STATE["coverage"]),
            STATE["updated_at"],
            STATE["cycle"],
        )


def _loop():
    while True:
        try:
            _build()
        except Exception as exc:
            with STATE["lock"]:
                STATE["errors"] = [str(exc)]
        time.sleep(REFRESH_SECONDS)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"ok": True, "updated_at": STATE["updated_at"], "cycle": STATE["cycle"]})


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


@app.route("/api/refresh", methods=["POST", "GET"])
def refresh():
    return jsonify(_build())


@app.route("/stream")
def stream():
    def gen():
        last = -1
        while True:
            snap = _snapshot()
            cycle = snap.get("cycle") or 0
            if cycle != last:
                last = cycle
                yield f"data: {json.dumps(snap)}\n\n"
            else:
                yield ": ping\n\n"
            time.sleep(4)

    return Response(stream_with_context(gen()), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


def start_worker():
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


start_worker()

if __name__ == "__main__":
    threading.Thread(target=_build, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
