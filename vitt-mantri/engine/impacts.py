"""Theme -> instrument impacts resolved at runtime from the live universe (no fixed ticker lists).

A theme carries *rules*: {action, hop, tags[], mkt?, why, pins[[symbol, why]], limit}
  pins   = curated names (always included when they exist in the universe)
  tags   = sector/asset tags; matching instruments are added dynamically (top `limit` by liquidity tier)
Dynamic additions are flagged origin="sector-rule" so the signal engine can weight them lower.
`python -m engine.impacts migrate` writes config/themes.json + config/tag_overrides.json from the legacy dicts.
"""
from __future__ import annotations

import json
import os
import sys

from .instruments import CFG, get_universe

SECTOR_RULE_WEIGHT = 0.75
BUCKETS = ("direct_buy", "direct_sell", "indirect_buy", "indirect_sell")


def legacy_to_rules(themes, companies):
    sector = {m["ticker"]: (m["sector"], m.get("mkt", "IN")) for m in companies.values()}
    out = {}
    for tid, t in themes.items():
        groups = {}
        for b in BUCKETS:
            hop, action = b.split("_")
            for sym, why in t[b]:
                sec, mkt = sector.get(sym, (None, None))
                g = groups.setdefault((hop, action.upper(), sec, mkt), {"why": why, "pins": []})
                g["pins"].append([sym, why])
        out[tid] = {"label": t["label"], "meaning": t["meaning"], "keywords": t["keywords"], "negations": t.get("negations", []),
                    "rules": [{"hop": h, "action": a, "tags": [s] if s else [], "mkt": m, "why": g["why"], "pins": g["pins"], "limit": 3}
                              for (h, a, s, m), g in groups.items()]}
    return out


def load_theme_config():
    try:
        with open(os.path.join(CFG, "themes.json")) as fh: cfg = json.load(fh)
    except (OSError, ValueError):
        from .universe import COMPANIES, THEMES
        cfg = legacy_to_rules(THEMES, COMPANIES)
    try:                                                    # asset-class rules (gold, bonds, REITs, crypto, FX ...)
        with open(os.path.join(CFG, "assets.json")) as fh: assets = json.load(fh).get("theme_rules", {})
        for tid, rules in assets.items():
            if tid in cfg: cfg[tid]["rules"] = cfg[tid]["rules"] + rules
    except (OSError, ValueError): pass
    return cfg


def resolve(theme: dict, universe=None) -> dict:
    """-> {'direct_buy': [(symbol, why, origin)], ...} for one theme config."""
    u = universe or get_universe()
    out = {b: [] for b in BUCKETS}
    seen = set()
    for r in theme["rules"]:
        bucket = f"{r['hop']}_{r['action'].lower()}"
        pinned = set()
        for sym, why in r.get("pins", []):
            if u.has(sym) and (bucket, sym) not in seen:
                out[bucket].append((sym, why, "curated")); seen.add((bucket, sym)); pinned.add(sym)
        if r.get("tags"):
            for inst in u.select(r["tags"], r.get("mkt"), r.get("limit", 3), exclude=pinned):
                if (bucket, inst.symbol) not in seen:
                    out[bucket].append((inst.symbol, r["why"], "sector-rule")); seen.add((bucket, inst.symbol))
    return out


def migrate(dest=CFG):
    from .universe import COMPANIES, THEMES
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "themes.json"), "w") as fh: json.dump(legacy_to_rules(THEMES, COMPANIES), fh, indent=1)
    ov = {}
    for m in COMPANIES.values(): ov.setdefault(m["ticker"], []).append(m["sector"])
    with open(os.path.join(dest, "tag_overrides.json"), "w") as fh: json.dump(ov, fh, indent=1, sort_keys=True)
    print(f"wrote {len(THEMES)} themes, {len(ov)} tag overrides to {dest}")


if __name__ == "__main__" and sys.argv[1:] == ["migrate"]:
    migrate()
