"""Owner's personal to-do list: durable store, text splitting and date parsing.

Storage is one JSON file (``CLOVER_HOME/todos.json``) written atomically, so it
needs no state.db migration.  Everything here is pure/stdlib and testable
without a gateway.  Triage and auto-start live in ``todo_triage``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OWNERS = ("clover", "you", "together")
RISKS = ("low", "medium", "high")
STATUSES = ("", "queued", "working", "needs_you", "done", "failed")
MAX_ITEMS = 500
MAX_TEXT = 300

_lock = threading.RLock()


def store_path(home: Path) -> Path:
    return Path(home) / "todos.json"


def _empty() -> Dict[str, Any]:
    return {"version": 1, "items": [], "settings": {"auto_start": True, "enabled_at": time.time()}}


def load(home: Path) -> Dict[str, Any]:
    try:
        data = json.loads(store_path(home).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data.setdefault("settings", {"auto_start": True, "enabled_at": 0.0})
            return data
    except (OSError, ValueError):
        pass
    return _empty()


def save(home: Path, data: Dict[str, Any]) -> None:
    p = store_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- splitting

_BULLET = re.compile(r"^\s*(?:[-*•–—]|\d{1,2}[.)]|\[[ xX]?\])\s+")


def split_items(raw: str) -> List[str]:
    """Turn pasted/typed text into item texts.

    Newlines and bullets always split.  A single line splits on commas,
    semicolons and `` and `` only when it has 2+ commas/semicolons (so
    "dinner with Sam, Alex" stays one item but "milk, dentist, monitors" is three).
    """
    if not isinstance(raw, str):
        return []
    lines = [_BULLET.sub("", ln).strip() for ln in raw.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    out: List[str] = []
    for ln in lines:
        parts = [p.strip() for p in re.split(r"[;,]", ln)]
        if len(lines) == 1 and len([p for p in parts if p]) >= 3 or (len(parts) >= 3 and ";" in ln):
            out.extend(p for p in parts if p)
        else:
            out.append(ln)
    seen, res = set(), []
    for t in out:
        t = re.sub(r"^(and|then)\s+", "", t, flags=re.I).strip(" .")[:MAX_TEXT]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            res.append(t)
    return res


# ---------------------------------------------------------------- date parsing

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_ABBR = {d[:3]: i for i, d in enumerate(_DAYS)}
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_TIME = r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)"
_TIME24 = r"(?:at\s+)(\d{1,2})(?::(\d{2}))?\b"


def _apply_time(base: datetime, h: int, m: int, ap: Optional[str]) -> datetime:
    if ap:
        ap = ap[0].lower()
        if ap == "p" and h < 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
    return base.replace(hour=min(h, 23), minute=min(m, 59), second=0, microsecond=0)


def parse_when(text: str, now: Optional[datetime] = None) -> Tuple[str, Optional[float]]:
    """Strip a trailing/embedded date phrase from ``text``.

    Returns ``(clean_text, due_epoch_or_None)`` in local time.  Handles today /
    tonight / tomorrow, weekday names (next occurrence), ``in N minutes|hours|days|weeks``,
    ``Nov 5`` / ``5 Nov``, ``6:30pm`` / ``at 6``.  A date without a time is
    due at end of day marker 09:00?  No: date-only items carry 00:00 and
    ``has_time`` is implied by the epoch not being midnight.
    """
    now = now or datetime.now()
    s = f" {text} "
    low = s.lower()
    day: Optional[datetime] = None
    span_cuts: List[Tuple[int, int]] = []

    def cut(m: "re.Match[str]") -> None:
        span_cuts.append((m.start(), m.end()))

    rel = re.search(r"\bin\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?|days?|weeks?)\b", low)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2)
        delta = (timedelta(minutes=n) if unit.startswith("min") else timedelta(hours=n) if unit.startswith(("hour", "hr"))
                 else timedelta(days=n) if unit.startswith("day") else timedelta(weeks=n))
        due = now + delta
        cut(rel)
        return _finish(s, span_cuts), due.timestamp()

    hit = re.search(r"\b(today|tonight|tomorrow|tmrw)\b", low)
    if hit:
        w = hit.group(1)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1 if w in ("tomorrow", "tmrw") else 0)
        cut(hit)
    else:
        wd = re.search(r"\b(?:(?:on|next|this)\s+)?(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|rsday|urday|sday)?\b", low)
        if wd:
            idx = _DAY_ABBR[wd.group(1)[:3]]
            ahead = (idx - now.weekday()) % 7
            if ahead == 0:
                ahead = 7
            day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=ahead)
            cut(wd)
        else:
            md = re.search(r"\b(?:on\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
            dm = re.search(r"\b(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", low)
            g = md or dm
            if g:
                mon_s, d_s = (g.group(1), g.group(2)) if md else (g.group(2), g.group(1))
                try:
                    cand = now.replace(month=_MONTHS.index(mon_s) + 1, day=int(d_s), hour=0, minute=0, second=0, microsecond=0)
                    if cand.date() < now.date():
                        cand = cand.replace(year=cand.year + 1)
                    day = cand
                    cut(g)
                except ValueError:
                    day = None

    tm = re.search(_TIME, low) or None
    h, m, ap = 0, 0, None
    if tm:
        h, m, ap = int(tm.group(1)), int(tm.group(2) or 0), tm.group(3)
        if not 1 <= h <= 12:
            tm = None
    if tm is None:
        t24 = re.search(_TIME24, low)
        if t24 and 0 <= int(t24.group(1)) <= 23:
            h, m, ap = int(t24.group(1)), int(t24.group(2) or 0), None
            tm = t24
            if h < 7 and ap is None and not t24.group(2):
                h += 12  # "at 6" reads as evening
    if tm:
        cut(tm)
    if day is None and tm is None:
        return text.strip(), None
    if day is None:  # time only: next occurrence
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cand = _apply_time(day, h, m, ap)
        if cand <= now:
            cand += timedelta(days=1)
        return _finish(s, span_cuts), cand.timestamp()
    if tm:
        day = _apply_time(day, h, m, ap)
    elif re.search(r"\btonight\b", low):
        day = day.replace(hour=20)
    return _finish(s, span_cuts), day.timestamp()


def _finish(s: str, cuts: List[Tuple[int, int]]) -> str:
    for a, b in sorted(cuts, reverse=True):
        s = s[:a] + " " + s[b:]
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    s = re.sub(r"\b(on|at|by|for)$", "", s, flags=re.I).strip(" ,.-")
    return s


# ---------------------------------------------------------------- items

def new_item(text: str, *, order: float, now: Optional[float] = None, parse_dates: bool = True) -> Optional[Dict[str, Any]]:
    now = now if now is not None else time.time()
    clean, due = parse_when(text) if parse_dates else (text, None)
    clean = (clean or text).strip()[:MAX_TEXT]
    if not clean:
        return None
    return {
        "id": uuid.uuid4().hex[:12], "text": clean, "notes": "", "done": False, "done_at": None,
        "created_at": now, "updated_at": now, "order": order, "due_at": due, "remind_at": None,
        "owner": None, "risk": None, "plan": "", "status": "", "session_id": None,
        "result_summary": "", "triaged": False, "auto_started": False, "done_by": None,
    }


def add(home: Path, raw: str, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    with _lock:
        data = load(home)
        items = data["items"]
        base = max([i.get("order", 0) for i in items] or [0])
        created = []
        for k, t in enumerate(split_items(raw)):
            if len(items) + len(created) >= MAX_ITEMS:
                break
            it = new_item(t, order=base + 1 + k, now=now)
            if it:
                created.append(it)
        items.extend(created)
        save(home, data)
        return created


_EDITABLE = ("text", "notes", "due_at", "remind_at", "owner")


def update(home: Path, item_id: str, patch: Dict[str, Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = now if now is not None else time.time()
    with _lock:
        data = load(home)
        for it in data["items"]:
            if it["id"] != item_id:
                continue
            if "done" in patch:
                done = bool(patch["done"])
                if done != it["done"]:
                    it["done"] = done
                    it["done_at"] = now if done else None
                    it["done_by"] = "you" if done else None
                    if done and it["status"] in ("queued", "working", "needs_you"):
                        it["status"] = "done"
                    if not done and it["status"] == "done":
                        it["status"] = ""
            for k in _EDITABLE:
                if k in patch:
                    v = patch[k]
                    if k == "text":
                        v = str(v or "").strip()[:MAX_TEXT]
                        if not v:
                            continue
                    if k == "owner" and v not in OWNERS:
                        continue
                    if k in ("due_at", "remind_at") and v is not None:
                        try:
                            v = float(v)
                        except (TypeError, ValueError):
                            continue
                    if k == "notes":
                        v = str(v or "")[:2000]
                    it[k] = v
                    if k == "owner":
                        it["owner_set_by_user"] = True
            it["updated_at"] = now
            save(home, data)
            return it
    return None


def delete(home: Path, item_id: str) -> bool:
    with _lock:
        data = load(home)
        n = len(data["items"])
        data["items"] = [i for i in data["items"] if i["id"] != item_id]
        if len(data["items"]) != n:
            save(home, data)
            return True
        return False


def reorder(home: Path, ids: List[str]) -> List[Dict[str, Any]]:
    """Give ``ids`` the leading order slots in the given sequence; the rest keep relative order after."""
    with _lock:
        data = load(home)
        by = {i["id"]: i for i in data["items"]}
        seq = [by[i] for i in ids if i in by]
        rest = [i for i in sorted(data["items"], key=lambda x: x.get("order", 0)) if i["id"] not in {s["id"] for s in seq}]
        for n, it in enumerate(seq + rest):
            it["order"] = float(n + 1)
        save(home, data)
        return sorted(data["items"], key=lambda x: x["order"])


def patch_system(home: Path, item_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Server-side field update (triage/status); never touches user-editable guards."""
    with _lock:
        data = load(home)
        for it in data["items"]:
            if it["id"] == item_id:
                it.update(fields)
                it["updated_at"] = time.time()
                save(home, data)
                return it
    return None


def listing(home: Path) -> Dict[str, Any]:
    data = load(home)
    return {"items": sorted(data["items"], key=lambda x: x.get("order", 0)), "settings": data.get("settings", {})}
