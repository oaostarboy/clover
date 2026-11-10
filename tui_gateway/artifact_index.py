"""Artifact index for the phone Library tab (``artifacts.list`` + thumbnails).

Everything Clover generated for the owner is already named in assistant
messages: ``MEDIA:/abs/path`` tags, ``![alt](/abs/path)`` images and
``[name](/abs/path.pdf)`` links.  This module scans those messages, keeps the
references whose file still exists, classifies them and pages them newest
first.  File bytes are served by the existing authenticated
``/api/files/download`` route; thumbnails by ``/api/artifacts/thumb``, which
only serves ids this index handed out (no arbitrary path reads).

Pure helpers (``extract_refs``, ``kind_of``, ``build_artifacts``, ``page``)
take plain data so they test without a database.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

KINDS = ("image", "video", "audio", "doc", "html", "code", "file")
_IMAGE = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif", "svg"}
_VIDEO = {"mp4", "mov", "webm", "mkv", "avi", "m4v"}
_AUDIO = {"mp3", "m4a", "ogg", "opus", "wav", "flac", "aac", "oga"}
_DOC = {"pdf", "docx", "doc", "md", "markdown", "txt", "rtf", "csv", "xlsx", "xls", "pptx", "ppt", "odt", "epub"}
_HTML = {"html", "htm"}
_CODE = {"py", "js", "jsx", "ts", "tsx", "json", "yaml", "yml", "toml", "sh", "swift", "kt", "java", "c", "h",
         "cpp", "rs", "go", "rb", "css", "sql", "diff", "patch", "ini", "xml"}
KNOWN_EXT = _IMAGE | _VIDEO | _AUDIO | _DOC | _HTML | _CODE

_MEDIA_RE = re.compile(r"[`\"'*_]*MEDIA:\s*(`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'|\S+)[`\"'*_]*")
_MD_REF_RE = re.compile(r"!?\[([^\]\n]*)\]\((<[^>\n]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)")

_MAX_FILE_BYTES = 200 * 1024 * 1024
_SENSITIVE_PARTS = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config/gcloud", ".config/gh", ".password-store",
    "mcp-tokens", "pairing", "secrets", "keychains", "cookies",
}
_SENSITIVE_NAMES = {
    ".env", ".envrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "auth.json", "auth.lock", "credentials",
    "credentials.json", "config.yaml", ".git-credentials", ".netrc", ".npmrc", ".pypirc", ".pgpass", ".my.cnf",
    ".anthropic_oauth.json", "google_token.json", "google_oauth.json", "google_oauth_pending.json",
    "webhook_subscriptions.json", "bws_cache.json", "bws_cache.enc.json", "todos.json", "known_hosts",
    "authorized_keys", "shadow", "passwd", "sudoers", "cookies.sqlite", "login data", "state.db",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".jks", ".asc", ".gpg", ".ppk",
                       ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".env")
# System trees that are never "something Clover made for the owner".
_DENY_ROOTS = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/run", "/var/lib", "/var/log", "/usr/lib",
               "/private/etc", "/System", "/Library")
# Files the phone uploaded (or the gateway cached from a chat) are NOT things Clover made.
_UPLOAD_PREFIXES = ("upload_", "clipboard_", "pdf_p", "paste_")


def ext_of(path: str) -> str:
    return os.path.splitext(path.split("?", 1)[0].split("#", 1)[0])[1].lstrip(".").lower()


def kind_of(path: str) -> str:
    e = ext_of(path)
    if e in _IMAGE:
        return "image"
    if e in _VIDEO:
        return "video"
    if e in _AUDIO:
        return "audio"
    if e in _HTML:
        return "html"
    if e in _DOC:
        return "doc"
    if e in _CODE:
        return "code"
    return "file"


def mime_of(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def artifact_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _unquote(raw: str) -> str:
    t = raw.strip()
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'`":
        return t[1:-1]
    return re.sub(r"[`\"'*_]{1,3}$", "", t)


def _clean_path(raw: str) -> Optional[str]:
    p = raw.strip().strip("<>")
    if p.startswith("file://"):
        p = p[7:]
    p = p.split("#", 1)[0]
    if p.startswith("~/"):
        p = os.path.expanduser(p)
    if not p.startswith("/") or "\x00" in p:
        return None
    return os.path.normpath(p)


def extract_refs(text: str) -> List[Tuple[str, str]]:
    """Ordered unique ``(path, label)`` for local files named in ``text``.

    Remote URLs and paths with unknown extensions are ignored; MEDIA tags are
    accepted for any extension (the agent chose to attach them).
    """
    if not isinstance(text, str) or not text:
        return []
    seen: Dict[str, str] = {}
    if "MEDIA:" in text:
        for m in _MEDIA_RE.finditer(text):
            p = _clean_path(_unquote(m.group(1)))
            if p and p not in seen:
                seen[p] = os.path.basename(p)
    if "](" in text:
        for m in _MD_REF_RE.finditer(text):
            p = _clean_path(m.group(2))
            if not p or p in seen or ext_of(p) not in KNOWN_EXT:
                continue
            seen[p] = (m.group(1) or "").strip() or os.path.basename(p)
    return list(seen.items())


def _clover_home_str() -> str:
    try:
        from clover_constants import get_clover_home

        return os.path.realpath(str(get_clover_home()))
    except Exception:
        return ""


def _is_sensitive_one(path: str, home: str) -> bool:
    p = Path(path)
    low = p.name.lower()
    if low in _SENSITIVE_NAMES or low.startswith(".env") or low.endswith(_SENSITIVE_SUFFIXES):
        return True
    s = path.replace("\\", "/").lower()
    if any(f"/{part}/" in s or s.endswith(f"/{part}") for part in _SENSITIVE_PARTS):
        return True
    if any(s == r.lower() or s.startswith(r.lower() + "/") for r in _DENY_ROOTS):
        return True
    # Hidden directories under the user's home (.config, .local, .mozilla, ...) hold app state and
    # keyrings, not creations. CLOVER_HOME (~/.clover) is the one hidden tree Clover writes into.
    try:
        uh = os.path.expanduser("~").replace("\\", "/").lower().rstrip("/")
    except Exception:
        uh = ""
    ch = home.replace("\\", "/").lower().rstrip("/") if home else ""
    if uh and s.startswith(uh + "/") and not (ch and (s == ch or s.startswith(ch + "/"))):
        if any(part.startswith(".") for part in s[len(uh) + 1:].split("/")[:-1]):
            return True
    if home:
        h = home.replace("\\", "/").lower().rstrip("/")
        # Files sitting directly in CLOVER_HOME are config/state/credentials, never creations.
        if os.path.dirname(s) == h:
            return True
        # Per-profile homes keep the same credential files.
        if s.startswith(h + "/profiles/") and s.count("/") == h.count("/") + 3:
            return True
    return False


def is_sensitive(path: str, *, home: Optional[str] = None) -> bool:
    """True when ``path`` or the file it really resolves to is credentials/state/system data.

    Both the lexical path and its symlink-resolved target are screened, so a
    link named ``photo.png`` that points at ``~/.ssh/id_rsa`` is rejected.
    """
    home = _clover_home_str() if home is None else home
    if _is_sensitive_one(path, home):
        return True
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return True
    return real != path and _is_sensitive_one(real, home)


def origin_of(path: str, home: Optional[str] = None) -> str:
    """``uploaded`` for files the owner sent in (phone/clipboard/PDF pages), else ``generated``."""
    home = _clover_home_str() if home is None else home
    name = os.path.basename(path)
    if name.startswith(_UPLOAD_PREFIXES):
        return "uploaded"
    if home:
        real = os.path.realpath(path).replace("\\", "/").lower()
        h = home.replace("\\", "/").lower().rstrip("/")
        if real.startswith(h + "/images/") or "/cache/images/" in real or real.startswith(h + "/cache/documents/"):
            return "uploaded"
    return "generated"


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                out.append(part["text"])
            elif isinstance(part, str):
                out.append(part)
        return "\n".join(out)
    return ""


def merge_rows(best: Dict[str, Dict[str, Any]], rows: Iterable[Dict[str, Any]]) -> None:
    """Fold message rows into ``best`` (path -> newest mention); the newest timestamp wins."""
    for row in rows:
        text = _flatten_content(row.get("content"))
        for path, label in extract_refs(text):
            ts = float(row.get("timestamp") or 0)
            prev = best.get(path)
            if prev is not None and prev["created_at"] >= ts:
                continue
            best[path] = {
                "path": path, "label": label, "created_at": ts, "session_id": row.get("session_id"),
                "message_id": row.get("id"), "session_title": row.get("session_title") or "",
            }


def build_artifacts(rows: Iterable[Dict[str, Any]], *, stat=os.stat) -> List[Dict[str, Any]]:
    """rows: ``{id, session_id, session_title, timestamp, content}`` per assistant message.

    Returns artifacts newest first, one per path (the newest mention wins),
    only for files that exist, are regular files, not sensitive and not huge.
    """
    best: Dict[str, Dict[str, Any]] = {}
    merge_rows(best, rows)
    return finalize_artifacts(best, stat=stat)


def finalize_artifacts(best: Dict[str, Dict[str, Any]], *, stat=os.stat) -> List[Dict[str, Any]]:
    """Turn ``path -> newest mention`` into the listed artifacts (existence/sensitivity checked now)."""
    home = _clover_home_str()
    out: List[Dict[str, Any]] = []
    for path, meta in best.items():
        if is_sensitive(path, home=home):
            continue
        try:
            st = stat(path)
        except OSError:
            continue
        import stat as _stat

        if not _stat.S_ISREG(st.st_mode) or st.st_size > _MAX_FILE_BYTES:
            continue
        kind = kind_of(path)
        name = os.path.basename(path)
        label = meta["label"]
        title = label if label and label != name and not label.lower().startswith("http") else name
        out.append({
            "id": artifact_id(path), "kind": kind, "title": title, "name": name, "path": path,
            "mime": mime_of(path), "size": int(st.st_size), "created_at": meta["created_at"],
            "origin": origin_of(path, home),
            "session_id": meta["session_id"], "message_id": meta["message_id"],
            "session_title": meta["session_title"],
            "thumb_url": f"/api/artifacts/thumb?id={artifact_id(path)}" if kind in ("image", "video") else None,
        })
    out.sort(key=lambda a: (-a["created_at"], a["id"]))
    return out


def page(items: List[Dict[str, Any]], *, cursor: Optional[str] = None, limit: int = 60,
         kind: Optional[str] = None, include_uploads: bool = False) -> Dict[str, Any]:
    """Keyset page over newest-first ``items``. ``cursor`` is ``<created_at>:<id>`` of the last item seen."""
    limit = max(1, min(int(limit or 60), 200))
    kind = (kind or "all").lower()
    if not include_uploads:  # the Library lists what Clover made, not what the owner sent in
        items = [a for a in items if a.get("origin") != "uploaded"]
    groups = {"images": ("image",), "video": ("video",), "audio": ("audio",), "docs": ("doc", "html", "file"), "code": ("code",)}
    if kind in KINDS:
        wanted: Optional[Tuple[str, ...]] = (kind,)
    else:
        wanted = groups.get(kind)
    rows = [a for a in items if wanted is None or a["kind"] in wanted]
    if cursor:
        try:
            ts_s, _, cid = str(cursor).partition(":")
            ts = float(ts_s)
            rows = [a for a in rows if (-a["created_at"], a["id"]) > (-ts, cid)]
        except ValueError:
            pass
    chunk = rows[:limit]
    nxt = f"{chunk[-1]['created_at']!r}:{chunk[-1]['id']}" if len(rows) > limit and chunk else None
    counts: Dict[str, int] = {"all": len(items)}
    for a in items:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    return {"items": [{k: v for k, v in a.items() if k != "name"} for a in chunk], "next_cursor": nxt,
            "total": len(rows), "counts": counts}


# ---------------------------------------------------------------- id registry + thumbnails

def _cache_root(home: Path) -> Path:
    return Path(home) / "cache" / "artifacts"


def save_registry(home: Path, items: List[Dict[str, Any]]) -> None:
    """Persist id->path so ``/api/artifacts/thumb`` serves only indexed files."""
    root = _cache_root(home)
    root.mkdir(parents=True, exist_ok=True)
    data = {a["id"]: a["path"] for a in items if a["kind"] in ("image", "video")}
    tmp = root / "registry.json.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, root / "registry.json")


def lookup(home: Path, aid: str) -> Optional[str]:
    if not re.fullmatch(r"[0-9a-f]{16}", aid or ""):
        return None
    try:
        data = json.loads((_cache_root(home) / "registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = data.get(aid) if isinstance(data, dict) else None
    if not isinstance(path, str) or artifact_id(path) != aid or is_sensitive(path):
        return None
    if not os.path.isfile(path):
        return None
    return os.path.realpath(path)


def thumbnail(home: Path, path: str, size: int = 480) -> Optional[Path]:
    """JPEG thumbnail cached under ``CLOVER_HOME/cache/artifacts/thumbs``; None when no tool can make one."""
    size = max(64, min(int(size), 1024))
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()[:24]
    out = _cache_root(home) / "thumbs" / f"{key}.jpg"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.jpg")
    kind = kind_of(path)
    try:
        if kind == "video":
            ff = shutil.which("ffmpeg")
            if not ff:
                return None
            r = subprocess.run(
                [ff, "-v", "error", "-y", "-ss", "1", "-i", path, "-frames:v", "1",
                 "-vf", f"scale={size}:-2", "-q:v", "4", str(tmp)],
                capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
            if r.returncode != 0 or not tmp.is_file():  # clip shorter than 1s
                r = subprocess.run([ff, "-v", "error", "-y", "-i", path, "-frames:v", "1",
                                    "-vf", f"scale={size}:-2", "-q:v", "4", str(tmp)],
                                   capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
                if r.returncode != 0 or not tmp.is_file():
                    return None
        elif kind == "image":
            try:
                from PIL import Image, ImageOps

                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((size, size))
                    im.convert("RGB").save(tmp, "JPEG", quality=82)
            except Exception:
                ff = shutil.which("ffmpeg")
                if not ff:
                    return None
                r = subprocess.run([ff, "-v", "error", "-y", "-i", path, "-frames:v", "1",
                                    "-vf", f"scale='min({size},iw)':-2", str(tmp)],
                                   capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
                if r.returncode != 0 or not tmp.is_file():
                    return None
        else:
            return None
        os.replace(tmp, out)
        return out
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- database scan
#
# The index is incremental and persistent.  ``cache/artifacts/index.json`` holds, per file path, the
# newest message that mentions it, plus two id frontiers over ``messages.id`` (the primary key):
#   * ``high`` - every message with id > high is still unscanned (the cheap, per-call range scan);
#   * ``low``  - every message with id >= low has been scanned (the one-time backfill walks this down).
# A request only scans ``id > high`` (a PK range, a handful of rows) and never the whole table.  The
# first-ever backfill, and any large catch-up, runs in one background thread in short id-range chunks
# so no request waits on it and no query holds the database; ``artifacts.list`` reports
# ``indexing: true`` until it finishes.
#
# Messages later made inactive (compaction/rewind) are deliberately not reconciled: the listing is of
# files, and ``finalize_artifacts`` stats every path on each rebuild, so a deleted file drops out by
# itself while a file that still exists stays a creation.

_SCAN_LIMIT = 4000
_CHUNK = 20000          # message ids per query
_SEED = 2000            # ids scanned inline on the very first call so it is not empty
_PAUSE = 0.02           # seconds between backfill chunks (yield CPU / the DB)
_INDEX_VERSION = 1
_cache: Dict[str, Any] = {"at": 0.0, "key": None, "items": []}
_states: Dict[str, "_IndexState"] = {}
_states_lock = threading.Lock()

_WHERE = (
    "m.role = 'assistant' AND m.active = 1 AND m.content IS NOT NULL "
    "AND (m.content LIKE '%MEDIA:%' OR m.content LIKE '%](/%' OR m.content LIKE '%](file://%' "
    "OR m.content LIKE '%](~/%' OR m.content LIKE '%](<%')"
)
_SELECT = (
    "SELECT m.id AS id, m.session_id AS session_id, m.content AS content, m.timestamp AS timestamp, "
    "s.title AS session_title FROM messages m LEFT JOIN sessions s ON s.id = m.session_id "
)


def _decode(content: Any) -> Any:
    try:
        from clover_state import SessionDB

        return SessionDB._decode_content(content)
    except Exception:
        return content


def _rows_from(cur) -> List[Dict[str, Any]]:
    return [{"id": r["id"], "session_id": r["session_id"], "content": _decode(r["content"]),
             "timestamp": r["timestamp"], "session_title": r["session_title"]} for r in cur.fetchall()]


def scan_rows(db, limit: int = _SCAN_LIMIT, *, min_id: Optional[int] = None,
              max_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Assistant messages that mention a file (MEDIA tag / markdown link or image), newest first.

    ``min_id``/``max_id`` bound the scan to a primary-key range (inclusive); without them it is the
    legacy newest-``limit`` scan.
    """
    sql = _SELECT + "WHERE " + _WHERE
    args: List[Any] = []
    if min_id is not None:
        sql += " AND m.id >= ?"
        args.append(int(min_id))
    if max_id is not None:
        sql += " AND m.id <= ?"
        args.append(int(max_id))
    sql += " ORDER BY m.id DESC LIMIT ?"
    args.append(int(limit))
    with db._read_ctx() as conn:
        return _rows_from(conn.execute(sql, args))


def _max_id(conn) -> int:
    row = conn.execute("SELECT MAX(id) FROM messages").fetchone()
    return int((row[0] if row else 0) or 0)


class _IndexState:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.lock = threading.RLock()
        self.best: Dict[str, Dict[str, Any]] = {}
        self.high: Optional[int] = None      # None -> never initialised
        self.low: int = 1
        self.thread: Optional[threading.Thread] = None
        self.dirty = False
        self._load()

    @property
    def path(self) -> Path:
        return _cache_root(self.home) / "index.json"

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == _INDEX_VERSION and isinstance(data.get("entries"), dict):
                self.best = data["entries"]
                self.high = int(data["high"])
                self.low = int(data["low"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save(self) -> None:
        root = _cache_root(self.home)
        try:
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / "index.json.tmp"
            tmp.write_text(json.dumps({"version": _INDEX_VERSION, "high": self.high, "low": self.low,
                                       "entries": self.best}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    @property
    def indexing(self) -> bool:
        with self.lock:
            return self.low > 1 or bool(self.thread and self.thread.is_alive())

    def _fold(self, rows: List[Dict[str, Any]]) -> None:
        with self.lock:
            merge_rows(self.best, rows)
            self.dirty = True

    def _worker(self, db_path: Optional[str]) -> None:
        """Catch up ``id > high`` then walk ``low`` down to 1, one short PK-range query at a time."""
        try:
            try:
                os.nice(5)
            except (AttributeError, OSError):
                pass
            conn = _open_ro(db_path)
            if conn is None:
                return
            try:
                while True:
                    top = _max_id(conn)
                    with self.lock:
                        high = self.high or 0
                    if top <= high:
                        break
                    hi = min(top, high + _CHUNK)
                    rows = _rows_from(conn.execute(
                        _SELECT + "WHERE " + _WHERE + " AND m.id > ? AND m.id <= ? ORDER BY m.id DESC",
                        (high, hi)))
                    with self.lock:
                        self._fold(rows)
                        self.high = hi
                        self.save()
                    time.sleep(_PAUSE)
                while True:
                    with self.lock:
                        low = self.low
                    if low <= 1:
                        break
                    lo = max(1, low - _CHUNK)
                    rows = _rows_from(conn.execute(
                        _SELECT + "WHERE " + _WHERE + " AND m.id >= ? AND m.id < ? ORDER BY m.id DESC",
                        (lo, low)))
                    with self.lock:
                        self._fold(rows)
                        self.low = lo
                        self.save()
                    time.sleep(_PAUSE)
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            with self.lock:
                self.thread = None
                self.dirty = True   # rebuild the listing from the final state

    def ensure_worker(self, db_path: Optional[str]) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._worker, args=(db_path,), name="artifact-index",
                                           daemon=True)
            self.thread.start()


def _open_ro(db_path: Optional[str]):
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=5,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _state_for(home: Path) -> _IndexState:
    key = str(home)
    with _states_lock:
        st = _states.get(key)
        if st is None:
            st = _states[key] = _IndexState(home)
        return st


def is_indexing(home: Path) -> bool:
    """True while the one-time backfill (or a large catch-up) is still running."""
    st = _states.get(str(home))
    return bool(st and st.indexing)


def index_for(db, home: Path, *, ttl: float = 2.0, force: bool = False) -> List[Dict[str, Any]]:
    """Artifacts newest first.  Never scans more than a small id range inline (see module notes)."""
    now = time.monotonic()
    key = str(home)
    st = _state_for(home)
    if not force and _cache["key"] == key and now - _cache["at"] < ttl and not st.dirty:
        return _cache["items"]
    db_path = str(getattr(db, "db_path", "") or "") or None
    with db._read_ctx() as conn:
        top = _max_id(conn)
    if st.high is None:                           # first ever call: seed with the newest id range only
        lo = max(1, top - _SEED + 1)
        rows = scan_rows(db, limit=_SEED, min_id=lo, max_id=top) if top else []
        with st.lock:
            if st.high is None:
                st._fold(rows)
                st.high, st.low = top, lo
                st.save()
    with st.lock:
        high = st.high
    if top - high > _CHUNK:                       # long gap: catch up off the request path
        st.ensure_worker(db_path)
    elif top > high:                              # the normal case: a few new messages
        rows = scan_rows(db, limit=_CHUNK, min_id=high + 1, max_id=top)
        with st.lock:
            if st.high == high:                   # not advanced meanwhile by the worker
                st._fold(rows)
                st.high = top
                st.save()
    if st.low > 1:
        st.ensure_worker(db_path)
    with st.lock:
        st.dirty = False
        best = dict(st.best)
    items = finalize_artifacts(best)
    try:
        save_registry(home, items)
    except OSError:
        pass
    _cache.update(at=now, key=key, items=items)
    return items
