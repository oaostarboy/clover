"""Lucky clover theme for the terminal setup: gradient, leaf meter, tiny animations.

Everything degrades to plain text when colour is off (NO_COLOR, TERM=dumb,
non-TTY). Animations only run on a real TTY and never under CI.
"""

from __future__ import annotations

import os
import sys
import time

from clover_cli.colors import Colors, color, should_use_color

# Logo gradient (light -> deep green). The darker #196d31 is a UI accent only.
GRADIENT_START = (0x92, 0xF2, 0xBA)
GRADIENT_END = (0x24, 0xAA, 0x6D)


# Traced from the official mark (assets/clover-mark.svg): four petals + dot.
MARK = (
    '     ▄██▄  ▄██▄     ',
    '    ████████████    ',
    ' ▄██▄██████████▄██▄ ',
    '████████████████████',
    ' ▀██████▄▀▀▄██████▀ ',
    ' ▄██████▀▄▄▀██████▄ ',
    '████████████████████',
    ' ▀██▀██████████▀██▀ ',
    '    ████████████    ',
    '     ▀██▀  ▀██▀     ',
)

# "CLOVER" in block letters (figlet ansi_shadow).
WORDMARK = (
    ' ██████╗██╗      ██████╗ ██╗   ██╗███████╗██████╗',
    '██╔════╝██║     ██╔═══██╗██║   ██║██╔════╝██╔══██╗',
    '██║     ██║     ██║   ██║██║   ██║█████╗  ██████╔╝',
    '██║     ██║     ██║   ██║╚██╗ ██╔╝██╔══╝  ██╔══██╗',
    '╚██████╗███████╗╚██████╔╝ ╚████╔╝ ███████╗██║  ██║',
    ' ╚═════╝╚══════╝ ╚═════╝   ╚═══╝  ╚══════╝╚═╝  ╚═╝',
)

def _rgb_at(t: float) -> tuple[int, int, int]:
    t = min(max(t, 0.0), 1.0)
    return tuple(round(a + (z - a) * t) for a, z in zip(GRADIENT_START, GRADIENT_END))


def _paint(line: str, row: int, rows: int, width: int) -> str:
    """Diagonal logo gradient across a block of art (top-left light, bottom-right deep)."""
    if not should_use_color():
        return line
    if not truecolor_ok():
        return color(line, Colors.GREEN, Colors.BOLD)
    out = []
    for x, ch in enumerate(line):
        if ch == " ":
            out.append(ch)
            continue
        r, g, b = _rgb_at((x / max(width - 1, 1) + row / max(rows - 1, 1)) / 2)
        out.append(f"\033[38;2;{r};{g};{b}m{ch}")
    return "".join(out) + Colors.RESET


def _cols() -> int:
    import shutil

    return shutil.get_terminal_size((80, 24)).columns


def art(side: list[str] | tuple[str, ...] | None = None, *, indent: int = 2) -> list[str]:
    """The clover mark, optionally with lines of text to its right (vertically centred)."""
    w = max(len(l) for l in MARK)
    rows = len(MARK)
    side = list(side or [])
    top = max((rows - len(side)) // 2, 0)
    out = []
    for i, l in enumerate(MARK):
        line = " " * indent + _paint(l.ljust(w), i, rows, w)
        j = i - top
        if 0 <= j < len(side):
            line += "   " + side[j]
        out.append(line)
    return out


def _wordmark_lines() -> list[str]:
    w = max(len(l) for l in WORDMARK)
    return [_paint(l, i, len(WORDMARK), w) for i, l in enumerate(WORDMARK)]


def rule(width: int = 56) -> str:
    """A thin gradient divider."""
    return "  " + gradient("─" * width) if should_use_color() else "  " + "-" * width


def truecolor_ok() -> bool:
    return should_use_color() and (
        os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        or "direct" in os.environ.get("TERM", "")
    )


def animation_ok() -> bool:
    """True only for a real, coloured TTY outside CI."""
    if not should_use_color() or os.environ.get("CI"):
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def gradient(text: str) -> str:
    """Colour *text* with the logo gradient (basic green without truecolor)."""
    if not should_use_color():
        return text
    if not truecolor_ok():
        return color(text, Colors.GREEN, Colors.BOLD)
    n = max(len(text) - 1, 1)
    out = []
    for i, ch in enumerate(text):
        r, g, b = (
            round(a + (z - a) * i / n) for a, z in zip(GRADIENT_START, GRADIENT_END)
        )
        out.append(f"\033[38;2;{r};{g};{b}m{ch}")
    return "".join(out) + Colors.RESET


def screen(headline: str, instruction: str) -> None:
    """A punny headline plus one plain line saying what to do."""
    print()
    print("  " + gradient(headline.upper() if should_use_color() else headline))
    print(color(f"  {instruction}", Colors.DIM))


# --- The side rail ---------------------------------------------------------
# Look borrowed from clack (bombshell-dev/clack, MIT, (c) Bombshell
# contributors): one left rail, active step ◆, finished step ◇ with the
# answer as one dim line, ┌ to open and └ to close. Re-implemented here.


def unicode_ok() -> bool:
    """Can this terminal draw box characters? (same idea as is-unicode-supported)"""
    if os.environ.get("CLOVER_ASCII"):
        return False
    if os.name == "nt":
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("TERM_PROGRAM")
            or os.environ.get("TERM") == "xterm-256color"
        )
    if os.environ.get("TERM") == "linux":
        return False
    enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    return enc in ("utf8", "") or "utf" in enc


def _g(uni: str, ascii_: str) -> str:
    return uni if unicode_ok() else ascii_


def S(name: str) -> str:
    return {
        "bar": _g("│", "|"),
        "start": _g("┌", "T"),
        "end": _g("└", "-"),
        "active": _g("◆", "*"),
        "done": _g("◇", "o"),
        "on": _g("●", ">"),
        "off": _g("○", " "),
        "clover": _g("🍀", "*"),
    }[name]


LEAF_LABELS = ("Name it", "Pick its brain", "Go")


def _tty() -> bool:
    try:
        return bool(sys.stdout.isatty()) and os.environ.get("TERM") != "dumb"
    except Exception:
        return False


def _bar() -> str:
    return color(S("bar"), Colors.GREEN) if should_use_color() else S("bar")


def leaf_title(n: int, text: str, total: int = 3) -> str:
    return f"Leaf {n} of {total} · {text}"


def rail(text: str = "", *, dim: bool = False) -> int:
    """One line on the rail. Returns lines printed."""
    body = color(text, Colors.DIM) if (dim and text) else text
    print(f"{_bar()}  {body}" if text else _bar())
    return 1


def gap() -> int:
    print(_bar())
    return 1


def intro(text: str, *, spacer: bool = True) -> int:
    mark = color(S("start"), Colors.GREEN) if should_use_color() else S("start")
    print(f"{mark}  {gradient(text) if should_use_color() else text}")
    return 1 + (gap() if spacer else 0)


def outro(text: str) -> None:
    # Every finished step already ends with a rail gap line.
    end = color(S("end"), Colors.GREEN) if should_use_color() else S("end")
    print(f"{end}  {S('clover')} {gradient(text) if should_use_color() else text}")


def step_open(title: str) -> int:
    mark = color(S("active"), Colors.GREEN) if should_use_color() else S("active")
    print(f"{mark}  {color(title, Colors.BOLD) if should_use_color() else title}")
    return 1


def step_done(title: str, answer: str = "", *, redraw: int = 0) -> None:
    """Collapse an active step to ◇ title + dim answer. *redraw* = lines to erase first."""
    if redraw and _tty():
        sys.stdout.write(f"\033[{redraw}A\033[J")
    mark = color(S("done"), Colors.GREEN) if should_use_color() else S("done")
    print(f"{mark}  {title}")
    if answer:
        rail(answer, dim=True)
    gap()


def ask(title: str, hint: str = "", default: str = "") -> str:
    """Text input on the rail. Returns the typed text, or *default* when empty."""
    n = step_open(title)
    if hint:
        n += rail(hint, dim=True)
    from clover_cli.cli_output import line_input

    raw = line_input(f"{_bar()}  ")
    n += 1
    typed = (raw or "").strip()
    answer = typed or default
    step_done(title, answer, redraw=n)
    return typed


def _split_hint(label: str) -> tuple[str, str]:
    if "·" in label:
        a, b = label.split("·", 1)
        return a.strip(), b.strip()
    return label, ""


def _row(label: str, active: bool) -> str:
    room = max(_cols() - 8, 20)
    if len(label) > room:
        label = label[: room - 1].rstrip() + "…"
    name, hint = _split_hint(label)
    if active:
        mark = color(S("on"), Colors.GREEN) if should_use_color() else S("on")
        text = gradient(name) if should_use_color() else name
    else:
        mark = color(S("off"), Colors.DIM) if should_use_color() else S("off")
        text = color(name, Colors.DIM) if should_use_color() else name
    tail = ("  " + color("· " + hint, Colors.DIM)) if hint else ""
    if hint and not should_use_color():
        tail = "  · " + hint
    return f"{_bar()}  {mark} {text}{tail}"


def _read_key() -> str:
    import select

    ch = os.read(0, 1)
    if ch == b"\x1b":
        if not select.select([0], [], [], 0.05)[0]:
            return "esc"
        rest = os.read(0, 2)
        return {b"[A": "up", b"[B": "down", b"OA": "up", b"OB": "down"}.get(rest, "")
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\x03":
        return "ctrl-c"
    if ch == b"\x04":
        return "esc"
    if ch in (b"k",):
        return "up"
    if ch in (b"j",):
        return "down"
    return ch.decode("utf-8", "ignore")


def select(title: str, choices: list[str], default: int = 0, hint: str = "") -> int | None:
    """Rail-style single choice: arrows + Enter, Esc to skip. Numbered fallback off a TTY.

    Returns the chosen index, or None when skipped.
    """
    n = step_open(title)
    interactive = False
    try:
        interactive = _tty() and sys.stdin.isatty() and os.name != "nt"
        if interactive:
            import termios  # noqa: F401
            import tty  # noqa: F401
    except Exception:
        interactive = False

    if not interactive:
        for i, c in enumerate(choices, 1):
            print(f"{_bar()}  {i}) {c}")
        n += len(choices)
        while True:
            try:
                val = input(f"{_bar()}  Pick one [1-{len(choices)}] ({default + 1}): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return None
            n += 1
            if not val:
                idx = default
            elif val.isdigit() and 1 <= int(val) <= len(choices):
                idx = int(val) - 1
            else:
                print(f"{_bar()}  Please enter 1-{len(choices)}")
                n += 1
                continue
            step_done(title, _split_hint(choices[idx])[0], redraw=n if _tty() else 0)
            return idx

    import termios
    import tty

    help_line = hint or "↑↓ move · Enter pick · Esc skip"
    if not unicode_ok():
        help_line = "up/down move, Enter pick, Esc skip"
    idx = default
    fd = 0
    old = termios.tcgetattr(fd)

    def draw(first: bool) -> None:
        if not first:
            sys.stdout.write(f"\033[{len(choices) + 1}A")
        for i, c in enumerate(choices):
            sys.stdout.write("\033[2K" + _row(c, i == idx) + "\n")
        sys.stdout.write("\033[2K" + f"{_bar()}  " + color(help_line, Colors.DIM) + "\n")
        sys.stdout.flush()

    result: int | None = None
    sys.stdout.write("\033[?25l")
    try:
        tty.setcbreak(fd)
        draw(True)
        while True:
            key = _read_key()
            if key == "up":
                idx = (idx - 1) % len(choices)
            elif key == "down":
                idx = (idx + 1) % len(choices)
            elif key == "enter":
                result = idx
                break
            elif key in ("esc", "ctrl-c"):
                break
            elif key.isdigit() and 1 <= int(key) <= len(choices):
                idx = int(key) - 1
            draw(False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h")
    n += len(choices) + 1
    step_done(
        title,
        _split_hint(choices[result])[0] if result is not None else "Skipped for now",
        redraw=n,
    )
    return result


_SPIN = "◒◐◓◑"


class working:
    """Spinner on the rail while work runs; ends as ◇ *done*. Plain line off a TTY."""

    def __init__(self, message: str, done: str | None = None):
        self.message = message
        self.done = done or message
        self._stop = None
        self._thread = None

    def __enter__(self):
        if not animation_ok():
            print(f"{_bar()}  {color(self.message, Colors.DIM)}")
            return self
        import threading

        self._stop = threading.Event()

        def spin():
            frames = _SPIN if unicode_ok() else "|/-\\"
            i = 0
            while not self._stop.is_set():
                f = frames[i % len(frames)]
                sys.stdout.write(
                    f"\r\033[K{gradient(f)}  {color(self.message, Colors.DIM)}"
                )
                sys.stdout.flush()
                i += 1
                self._stop.wait(0.09)

        self._thread = threading.Thread(target=spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._stop is not None:
            self._stop.set()
            self._thread.join()
            sys.stdout.write("\r\033[K")
            mark = color(S("done"), Colors.GREEN)
            print(f"{mark}  {self.done}")
            gap()
        return False


def leaves(done: int, total: int = 3) -> str:
    return f"Leaf {done} of {total}"


def leaf_step(done: int, total: int = 3) -> None:
    """Legacy hook kept for callers: the rail needs no per-step header."""
    return None


def banner() -> None:
    """Welcome: the clover mark next to the CLOVER block letters, then the rail opens."""
    if unicode_ok():
        wide = _cols() >= 80
        side = _wordmark_lines() if wide else [gradient("C L O V E R")]
        print()
        _reveal(art(side), 0.045)
    else:
        print()
        print("  " + gradient("C L O V E R"))
    print()
    intro("Feeling lucky? Let's grow your Clover.", spacer=False)


def screen(headline: str, instruction: str) -> None:
    """Kept for callers: a short rail note."""
    rail(headline)
    rail(instruction, dim=True)


def rule(width: int = 56) -> str:
    return "  " + gradient("─" * width) if should_use_color() else "  " + "-" * width


def _wait(seconds: float) -> bool:
    """Sleep up to *seconds*; return True if the user pressed Enter to skip."""
    try:
        import select

        r, _, _ = select.select([sys.stdin], [], [], seconds)
        if r:
            sys.stdin.readline()
            return True
        return False
    except Exception:
        time.sleep(seconds)
        return False


def grow(message: str = "Planting the seed…") -> None:
    """Short seed -> 🍀 beat on the rail. Skippable with Enter. Plain line off a TTY."""
    if not animation_ok():
        rail(message, dim=True)
        return
    frames = ("·", "☘", "🍀") if unicode_ok() else (".", "*", "*")
    for frame in frames:
        sys.stdout.write(f"\r\033[K{_bar()}  {gradient(frame)} {color(message, Colors.DIM)}")
        sys.stdout.flush()
        if _wait(0.35):
            break
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def flourish(message: str, extra: list[str] | None = None) -> None:
    """Finish: close the rail with the win message, then the next step."""
    outro(message)
    for line in extra or []:
        print("   " + line)


def _reveal(lines: list[str], delay: float) -> None:
    """Print lines one by one on a TTY (Enter skips the rest of the delay)."""
    animate = animation_ok()
    for line in lines:
        print(line)
        if animate:
            sys.stdout.flush()
            if _wait(delay):
                animate = False
