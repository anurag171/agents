import json
import os
import re

import requests

from .universe import COMPANIES

KNOWN = sorted({m["ticker"] for m in COMPANIES.values()})


def configured():
    return bool(os.getenv("USER_LLM_API_KEY"))


def _endpoint():
    base = (os.getenv("USER_LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base + "/chat/completions"


def _model():
    return os.getenv("USER_LLM_MODEL") or "deepseek-chat"


def _extract_json(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def interpret_batch(decoded):
    if not configured() or not decoded:
        return {}, "rules"
    payload = []
    for item in decoded[:24]:
        payload.append({
            "id": item["id"],
            "source": item.get("source"),
            "region": item.get("region"),
            "title": item.get("title"),
            "summary": (item.get("summary") or "")[:280],
            "theme": item.get("theme"),
            "named": [n["ticker"] for n in item.get("named") or []],
        })
    system = (
        "You are Vitta-Mantri, a forensic equity decoder. "
        "Read each headline. First explain what it means financially. "
        "Then emit BUY, SELL or WATCH for listed tickers only. "
        "Direct = issuer cash-flow. Indirect = suppliers, customers, FX, rates, competitors, flows. "
        "Do not invent filings or prices. Educational map, not personal advice. "
        "Return JSON only: {\"items\":[{\"id\":\"...\",\"meaning\":\"one or two sentences\","
        "\"signals\":[{\"ticker\":\"TCS\",\"hop\":\"direct|indirect\",\"action\":\"BUY|SELL|WATCH\","
        "\"why\":\"short mechanism\"}]}]}. "
        "Tickers must be from this list: " + ", ".join(KNOWN) + ". Max 8 signals per headline."
    )
    body = {
        "model": _model(),
        "temperature": 0.15,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"headlines": payload}, ensure_ascii=False)},
        ],
    }
    try:
        r = requests.post(
            _endpoint(),
            headers={
                "Authorization": "Bearer " + os.getenv("USER_LLM_API_KEY"),
                "Content-Type": "application/json",
            },
            json=body,
            timeout=45,
        )
        if r.status_code >= 400:
            return {}, "rules"
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        parsed = _extract_json(text) or {}
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return {}, "rules"
        out = {}
        allowed = set(KNOWN)
        for row in items:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            signals = []
            for s in row.get("signals") or []:
                if not isinstance(s, dict):
                    continue
                ticker = str(s.get("ticker") or "").upper().strip()
                hop = s.get("hop") if s.get("hop") in ("direct", "indirect") else "indirect"
                action = s.get("action") if s.get("action") in ("BUY", "SELL", "WATCH") else "WATCH"
                why = str(s.get("why") or "AI mapped financial impact.").strip()[:180]
                if ticker in allowed:
                    signals.append({"ticker": ticker, "hop": hop, "action": action, "why": why})
            out[row["id"]] = {
                "meaning": str(row.get("meaning") or "").strip()[:420],
                "signals": signals[:8],
            }
        return out, "ai"
    except (requests.RequestException, KeyError, ValueError, TypeError, IndexError):
        return {}, "rules"


def apply_ai(decoded, ai_map, mode):
    if not ai_map:
        for item in decoded:
            item["interpreted_by"] = "rules"
            item["ai_meaning"] = ""
        return decoded
    for item in decoded:
        row = ai_map.get(item["id"])
        item["interpreted_by"] = mode if row else "rules"
        item["ai_meaning"] = (row or {}).get("meaning") or ""
        if not row:
            continue
        if row.get("meaning"):
            item["meaning"] = row["meaning"]
        extra = []
        seen = {(s["ticker"], s["hop"], s["action"]) for s in item.get("signals") or []}
        for s in row.get("signals") or []:
            key = (s["ticker"], s["hop"], s["action"])
            if key in seen:
                continue
            seen.add(key)
            extra.append({
                "ticker": s["ticker"],
                "name": s["ticker"],
                "hop": s["hop"],
                "action": s["action"],
                "why": s["why"],
                "theme": item.get("theme"),
                "theme_id": item.get("theme_id"),
                "confidence": 3.6,
            })
        if extra:
            signals = (item.get("signals") or []) + extra
            item["signals"] = signals[:16]
            item["direct_buy"] = [s for s in signals if s["hop"] == "direct" and s["action"] == "BUY"]
            item["direct_sell"] = [s for s in signals if s["hop"] == "direct" and s["action"] == "SELL"]
            item["indirect_buy"] = [s for s in signals if s["hop"] == "indirect" and s["action"] == "BUY"]
            item["indirect_sell"] = [s for s in signals if s["hop"] == "indirect" and s["action"] == "SELL"]
            item["watch"] = [s for s in signals if s["action"] == "WATCH"]
    return decoded
