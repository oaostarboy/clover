"""Bind fixture trace files to bytes from an exact staged Git tree."""
import hashlib
from pathlib import Path
import re
import subprocess


class Refusal(RuntimeError):
    pass


def pinned_hash(root, commit, tree, relative):
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise Refusal('stage path is not canonical')
    if not all(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', x) for x in (commit, tree)):
        raise Refusal('invalid Git identity')
    path = root / relative
    if path.resolve() != path or root not in path.parents:
        raise Refusal('invalid staged path')
    def git(*args):
        try:
            return subprocess.run(['git', '-C', str(root), *args], check=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=10).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise Refusal('Git identity check failed') from exc
    if git('rev-parse', 'HEAD').strip().decode() != commit:
        raise Refusal('stage commit mismatch')
    if git('rev-parse', 'HEAD^{tree}').strip().decode() != tree:
        raise Refusal('stage tree mismatch')
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise Refusal('source unavailable') from exc
    expected = git('show', f'{commit}:{relative}')
    if actual != expected:
        raise Refusal('source bytes differ from pinned tree')
    return hashlib.sha256(actual).hexdigest()
