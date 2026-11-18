"""Extra command-approval detectors (Clover 2.1).

Closes four gaps: download-then-run, eval of decoded payloads, destructive
verbs on Clover's own home, and rc-file/credential symlinks. Pure string
analysis; nothing is ever executed. Ideas adapted from upstream hermes-agent
@05348a30 (approval_fetch_exec, approval_hermes_store, a2a5d0778b, fbc65ec989).
"""
from __future__ import annotations

import os
import re
import shlex

_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n|&")
_FETCHERS = {"curl", "wget"}
_INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "source", ".", "python",
                 "python3", "perl", "ruby", "node", "php"}
_DESTRUCTIVE = {"rm", "mv", "shred", "unlink", "rmdir", "srm", "wipe",
                "truncate", "trash", "trash-put"}
_DECODERS = re.compile(r"\b(base64|xxd|openssl\s+(enc|base64)|rev|uudecode|"
                       r"basenc|python3?\s+-c|perl\s+-e|gzip\s+-d|zcat|"
                       r"gunzip)\b|\\x[0-9a-f]{2}|\\[0-7]{3}|printf\s", re.I)
_CRED_RE = re.compile(
    r"(\.aws/(credentials|config)|\.ssh/(id_[a-z0-9]+|authorized_keys)(?!\.pub)|"
    r"\.netrc|\.gnupg/|\.docker/config\.json|\.kube/config|\.npmrc|\.pgpass|"
    r"\.clover/\.env|\.clover/auth\.json|(^|/)\.env$)")
_RC_RE = re.compile(r"(^|/)\.(bashrc|bash_profile|bash_login|profile|zshrc|zshenv|"
                    r"zprofile|zlogin|inputrc|xprofile|xinitrc)$")


def _split(command):
    for seg in _SPLIT_RE.split(command):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        # drop leading env assignments / sudo
        while toks and (re.match(r"^\w+=", toks[0]) or toks[0] in ("sudo", "command", "exec", "nohup", "env")):
            toks = toks[1:]
        if toks:
            yield toks


def _expand(tok, home=None):
    home = home or os.path.expanduser("~")
    t = tok.replace("${HOME}", home).replace("$HOME", home)
    if t.startswith("~"):
        t = home + t[1:]
    ch = clover_home()
    t = t.replace("${CLOVER_HOME}", ch).replace("$CLOVER_HOME", ch)
    return os.path.normpath(t) if t.startswith("/") else t


def clover_home():
    return os.path.normpath(os.environ.get("CLOVER_HOME") or os.path.expanduser("~/.clover"))


def _out_paths(toks):
    paths = []
    for i, t in enumerate(toks[1:], 1):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if toks[0] == "curl":
            if t in ("-o", "--output") and nxt:
                paths.append(nxt)
            elif t.startswith("--output="):
                paths.append(t.split("=", 1)[1])
            elif re.match(r"^-[A-Za-z]*o$", t) and nxt:
                paths.append(nxt)
        else:
            if t in ("-O", "--output-document") and nxt:
                paths.append(nxt)
            elif re.match(r"^-[A-Za-z]*O$", t) and nxt:
                paths.append(nxt)
            elif t.startswith("--output-document="):
                paths.append(t.split("=", 1)[1])
            elif t.startswith("-O") and len(t) > 2:
                paths.append(t[2:])
    for i, t in enumerate(toks):
        if t in (">", ">>") and i + 1 < len(toks):
            paths.append(toks[i + 1])
        elif re.match(r"^>>?[^>&]", t):
            paths.append(t.lstrip(">"))
    return [_expand(p) for p in paths if p != "-"]


_SUBST_FETCH = re.compile(
    r"\b(?:sh|bash|zsh|dash|ksh|source|eval|python3?|perl|ruby|node|php)\b[^;&|\n]*?"
    r"(?:\$\(|`|<\()\s*(?:curl|wget)\b")


def fetch_exec(command):
    if _SUBST_FETCH.search(command):
        return "execution of remote content via command or process substitution"
    downloaded = set()
    for toks in _split(command):
        name = os.path.basename(toks[0])
        if name in _FETCHERS:
            downloaded.update(_out_paths(toks))
            continue
        if not downloaded:
            continue
        args = [_expand(a) for a in toks[1:] if not a.startswith("-")]
        if _expand(toks[0]) in downloaded or (name in _INTERPRETERS and any(a in downloaded for a in args)):
            return "download-then-execute of fetched file"
    return None


def eval_payload(command):
    for toks in _split(command):
        if os.path.basename(toks[0]) == "eval":
            rest = " ".join(toks[1:])
            if _DECODERS.search(rest) or _DECODERS.search(command):
                return "eval of decoded/obfuscated payload"
    return None


def _is_clover_path(p):
    for ch in {clover_home(), os.path.normpath(os.path.expanduser("~/.clover"))}:
        if p == ch or p.startswith(ch + "/"):
            return True
    return False


def clover_store(command):
    cwd = None
    for toks in _split(command):
        name = os.path.basename(toks[0])
        args = [a for a in toks[1:] if not a.startswith("-")]
        if name in ("cd", "pushd"):
            cwd = _expand(args[0]) if args else None
            continue
        paths = [_expand(a) for a in args]
        if cwd and cwd.startswith("/"):
            paths = [os.path.normpath(os.path.join(cwd, p)) if not p.startswith("/") else p
                     for p in paths]
        if name in _DESTRUCTIVE:
            check = paths[:-1] if (name == "mv" and len(paths) > 1) else paths
            if name == "mv" and paths and paths[-1] == "/dev/null":
                check = paths
            if any(_is_clover_path(p) for p in check):
                return "destructive operation on Clover home/store"
        elif name == "dd":
            for a in toks[1:]:
                if a.startswith("of=") and _is_clover_path(_expand(a[3:])):
                    return "destructive operation on Clover home/store"
        elif name == "find" and "-delete" in toks:
            if any(_is_clover_path(p) for p in paths):
                return "destructive operation on Clover home/store"
    return None


def symlink_rc_credential(command):
    for toks in _split(command):
        if os.path.basename(toks[0]) != "ln":
            continue
        if not any(t.startswith("-") and not t.startswith("--") and "s" in t for t in toks[1:]) \
                and "--symbolic" not in toks:
            continue
        paths = [_expand(a) for a in toks[1:] if not a.startswith("-")]
        if len(paths) >= 2:
            src, dst = paths[0], paths[-1]
            if (_CRED_RE.search(src) and _RC_RE.search(dst)) or (_RC_RE.search(src) and _CRED_RE.search(dst)):
                return "symlink between shell rc file and credential file"
    return None


def detect_extra(command):
    """Return a description if *command* matches a closed hole, else None."""
    for fn in (fetch_exec, eval_payload, clover_store, symlink_rc_credential):
        d = fn(command)
        if d:
            return d
    return None
