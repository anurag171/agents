"""Cluster articles about the same event across outlets so one story is counted once, with a corroboration bonus.

Similarity = token Jaccard + 0.1 per shared number (paraphrases keep their figures), within a 48 h window, and at
least 3 shared tokens (short headlines must not merge on coincidence). Wire copy (PTI/IANS/ANI) counts as ONE origin.
The representative is the best-graded source (then earliest) and is what gets decoded and sent to the LLM."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

STOP = set("the a an of to in on for and as at by with from is are was be will after over amid says say new has have its their this that".split())
SUFFIX = re.compile(r"\s[-|–]\s(reuters|bloomberg|moneycontrol|economic times|et markets|business standard|yahoo finance|marketwatch|cnbc[- ]?tv18|pti|ians|ani)\s*$", re.I)
WIRE = re.compile(r"^\s*\(?(PTI|IANS|ANI)\)?[:\s-]|\s[-|–]\s*(PTI|IANS|ANI)\s*$")
NUM = re.compile(r"\d")
THRESHOLD, WINDOW_H, MIN_SHARED = 0.55, 48, 3
RANK = {"A": 0, "B": 1, "C": 2}


def tokens(title: str) -> set:
    t = SUFFIX.sub("", (title or "").lower())
    return {w for w in re.findall(r"[a-z0-9&.%$]+", t) if w not in STOP and len(w) > 2}


def similarity(a: set, b: set) -> float:
    inter = a & b
    if len(inter) < MIN_SHARED:
        return 0.0
    return len(inter) / len(a | b) + 0.1 * min(sum(1 for t in inter if NUM.search(t)), 2)


def _when(it) -> datetime:
    for k in ("published_at", "fetched_at"):
        v = it.get(k)
        if v:
            try:
                d = datetime.fromisoformat(v)
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return datetime.now(timezone.utc)


def origin(it) -> str:
    m = WIRE.search(f"{it.get('summary') or ''}"[:80]) or WIRE.search(it.get("title") or "")
    return (m.group(1) or m.group(2)) if m else it.get("source", "")


def cluster_items(items: list, grade_fn=lambda s: "C", threshold: float = THRESHOLD) -> list:
    toks, when = [tokens(i.get("title")) for i in items], [_when(i) for i in items]
    p = list(range(len(items)))

    def find(x):
        while p[x] != x:
            p[x] = p[p[x]]; x = p[x]
        return x

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if abs(when[i] - when[j]) <= timedelta(hours=WINDOW_H) and similarity(toks[i], toks[j]) >= threshold:
                p[find(i)] = find(j)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append((it, when[i]))
    out = []
    for members in groups.values():
        members.sort(key=lambda m: (RANK.get(grade_fn(m[0].get("source", "")), 2), m[1]))
        rep = dict(members[0][0])
        rest = [m[0] for m in members[1:]]
        outlets = list(dict.fromkeys(m[0].get("source", "") for m in members))
        rep.update(n_sources=len({origin(m[0]) for m in members}), sources=outlets, cluster_size=len(members),
                   also=[{"source": r.get("source"), "url": r.get("url"), "title": r.get("title")} for r in rest if r.get("source") != rep.get("source")][:6])
        out.append(rep)
    out.sort(key=lambda r: r.get("published_at") or r.get("fetched_at") or "", reverse=True)
    return out
