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


# "CLOVER" in solid block letters (only full / half blocks, no strokes).
_GLYPHS = {
    "C": ("▄████▄", "██    ", "██    ", "██    ", "▀████▀"),
    "L": ("██    ", "██    ", "██    ", "██    ", "██████"),
    "O": ("▄████▄", "██  ██", "██  ██", "██  ██", "▀████▀"),
    "V": ("██  ██", "██  ██", "██  ██", "▀█▄▄█▀", " ▀██▀ "),
    "E": ("██████", "██    ", "████  ", "██    ", "██████"),
    "R": ("█████▄", "██  ██", "█████▀", "██ ▀█▄", "██  ▀█"),
}
WORDMARK = tuple(
    "  ".join(_GLYPHS[ch][r] for ch in "CLOVER").rstrip() for r in range(5)
)

def _rgb_at(t: float) -> tuple[int, int, int]:
    t = min(max(t, 0.0), 1.0)
    return tuple(round(a + (z - a) * t) for a, z in zip(GRADIENT_START, GRADIENT_END))


def _paint(line: str, row: int, rows: int, width: int) -> str:
    """Diagonal logo gradient across a block of art (top-left light, bottom-right deep)."""
    if not should_use_color():
        return line
    if color_depth() < 256:
        return color(line, Colors.GREEN, Colors.BOLD)
    out = []
    for x, ch in enumerate(line):
        if ch == " ":
            out.append(ch)
            continue
        r, g, b = _rgb_at((x / max(width - 1, 1) + row / max(rows - 1, 1)) / 2)
        out.append(f"{_fg(r, g, b)}{ch}")
    return "".join(out) + Colors.RESET


def _cols() -> int:
    import shutil

    return shutil.get_terminal_size((80, 24)).columns


def _wordmark_lines() -> list[str]:
    w = max(len(l) for l in WORDMARK)
    return [_paint(l, i, len(WORDMARK), w) for i, l in enumerate(WORDMARK)]


def rule(width: int = 56) -> str:
    """A thin gradient divider."""
    return "  " + gradient("─" * width) if should_use_color() else "  " + "-" * width


def color_depth() -> int:
    """0 = none, 8 = basic ANSI, 256 = xterm-256, 1<<24 = truecolor."""
    if not should_use_color():
        return 0
    env = os.environ
    term = env.get("TERM", "").lower()
    prog = env.get("TERM_PROGRAM", "")
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") or "direct" in term:
        return 1 << 24
    if env.get("WT_SESSION") or prog in ("vscode", "WezTerm", "ghostty"):
        return 1 << 24
    if prog == "iTerm.app" or "kitty" in term or "alacritty" in term:
        return 1 << 24
    if os.name == "nt" and _modern_conhost():
        return 1 << 24
    # Plain TERM=xterm (what xterm itself exports) handles 256 colours on every
    # xterm built this century; rxvt/screen/tmux-256color say so in the name.
    if prog == "Apple_Terminal" or "256color" in term or term.startswith("xterm"):
        return 256
    return 8


def truecolor_ok() -> bool:
    return color_depth() == 1 << 24


def _to256(r: int, g: int, b: int) -> int:
    """Nearest xterm-256 colour (6x6x6 cube or grey ramp)."""
    lv = (0, 95, 135, 175, 215, 255)
    ci = [min(range(6), key=lambda i: abs(lv[i] - c)) for c in (r, g, b)]
    cube = 16 + 36 * ci[0] + 6 * ci[1] + ci[2]
    cd = sum((lv[i] - c) ** 2 for i, c in zip(ci, (r, g, b)))
    gi = min(23, max(0, round((sum((r, g, b)) / 3 - 8) / 10)))
    gv = 8 + 10 * gi
    gd = sum((gv - c) ** 2 for c in (r, g, b))
    return 232 + gi if gd < cd else cube


def _fg(r: int, g: int, b: int) -> str:
    if color_depth() == 256:
        return f"\033[38;5;{_to256(r, g, b)}m"
    return f"\033[38;2;{r};{g};{b}m"


def animation_ok() -> bool:
    """True only for a real, coloured TTY outside CI."""
    if not should_use_color() or os.environ.get("CI") or os.environ.get("CLOVER_NO_ANIM"):
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def gradient(text: str) -> str:
    """Colour *text* with the logo gradient (basic green without truecolor)."""
    if not should_use_color():
        return text
    if color_depth() < 256:
        return color(text, Colors.GREEN, Colors.BOLD)
    n = max(len(text) - 1, 1)
    out = []
    for i, ch in enumerate(text):
        r, g, b = (
            round(a + (z - a) * i / n) for a, z in zip(GRADIENT_START, GRADIENT_END)
        )
        out.append(f"{_fg(r, g, b)}{ch}")
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


def enable_vt() -> bool:
    """Turn on ANSI escape handling in the Windows console (no-op elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        k = ctypes.windll.kernel32
        ok = False
        for h in (-11, -12):  # stdout, stderr
            handle = k.GetStdHandle(h)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(handle, ctypes.byref(mode)):
                ok = bool(k.SetConsoleMode(handle, mode.value | 0x0004)) or ok
        return ok
    except Exception:
        return False


def _modern_conhost() -> bool:
    """Windows 10 1809+ console with ANSI on can draw box glyphs (Consolas has them)."""
    try:
        if sys.getwindowsversion().build < 17763:  # type: ignore[attr-defined]
            return False
        return bool(sys.stdout.isatty()) and enable_vt()
    except Exception:
        return False


def unicode_ok() -> bool:
    """Can this terminal draw box characters? (same idea as is-unicode-supported)"""
    if os.environ.get("CLOVER_ASCII"):
        return False
    if os.name == "nt":
        # Windows Terminal / VS Code / ConEmu draw box glyphs; old conhost does not.
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("TERM_PROGRAM")
            or os.environ.get("ConEmuANSI") == "ON"
            or os.environ.get("TERM") == "xterm-256color"
            or _modern_conhost()
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
        # Box-drawing joints (same font cell family as │) so the rail never breaks.
        "active": _g("├─", "+-"),
        "done": _g("├─", "+-"),
        "tick": _g("✓", "v"),
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
    mark = color(S("active"), Colors.GREEN, Colors.BOLD) if should_use_color() else S("active")
    print(f"{mark} {color(title, Colors.BOLD) if should_use_color() else title}")
    return 1


def step_done(title: str, answer: str = "", *, redraw: int = 0) -> None:
    """Collapse an active step to ◇ title + dim answer. *redraw* = lines to erase first."""
    if redraw and _tty():
        sys.stdout.write(f"\033[{redraw}A\033[J")
    mark = color(S("done"), Colors.DIM) if should_use_color() else S("done")
    print(f"{mark} {color(title, Colors.DIM) if should_use_color() else title}")
    if answer:
        tick = color(S("tick"), Colors.GREEN) if should_use_color() else S("tick")
        print(f"{_bar()}  {tick} {answer}")
    gap()


def ask(title: str, hint: str = "", default: str = "", *, preview=None, back: bool = False) -> str:
    """Text input on the rail. Returns the typed text ('' means keep *default*).

    *preview* is a function text -> str shown live under the input as you type.
    With back=True, Esc raises GoBack.
    """
    n = step_open(title)
    if hint:
        n += rail(hint, dim=True)
    if not _can_key():
        from clover_cli.cli_output import line_input

        raw = line_input(f"{_bar()}  ")
        n += 1
        typed = (raw or "").strip()
        step_done(title, typed or default, redraw=n)
        return typed

    buf: list[str] = []
    top = n  # lines above the input line (title + hint)

    def draw() -> None:
        text = "".join(buf)
        shown = text if buf else (color(default, Colors.DIM) if default else "")
        sys.stdout.write(f"\r\033[2K{_bar()}  {shown}")
        if preview:
            sys.stdout.write("\n\033[2K" + f"{_bar()}  " + color(preview(text.strip() or default), Colors.GREEN))
            sys.stdout.write("\033[1A\r")
            # put the cursor back after the typed text
            sys.stdout.write(f"{_bar()}  {text}")
        sys.stdout.flush()

    enable_vt()
    went_back = False
    with _raw():
        draw()
        while True:
            key = _read_key()
            if key == "enter":
                break
            if key == "ctrl-c":
                sys.stdout.write("\r\033[J\n")
                raise KeyboardInterrupt
            if key in ("esc", "left") and back and not buf:
                went_back = True
                break
            if key == "backspace":
                if buf:
                    buf.pop()
            elif len(key) == 1 and key.isprintable():
                buf.append(key)
            draw()
    sys.stdout.write("\r")
    if went_back:
        sys.stdout.write(f"\033[{top}A\033[J")
        raise GoBack()
    typed = "".join(buf).strip()
    step_done(title, typed or default, redraw=top)
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


class GoBack(Exception):
    """Raised when the user steps back (Esc / left arrow) to the previous step."""


def _use_windows_keys() -> bool:
    return os.name == "nt"


def _read_key() -> str:
    """One key press: up, down, left, enter, esc, ctrl-c, backspace, or the character."""
    if _use_windows_keys():
        import msvcrt  # type: ignore[import-not-found]

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")
        return {"\r": "enter", "\n": "enter", "\x1b": "esc", "\x03": "ctrl-c", "\x08": "backspace"}.get(ch, ch)
    import select as _sel

    ch = os.read(0, 1)
    if ch == b"\x1b":
        if not _sel.select([0], [], [], 0.05)[0]:
            return "esc"
        rest = os.read(0, 2)
        return {b"[A": "up", b"[B": "down", b"[D": "left", b"[C": "right", b"OA": "up", b"OB": "down"}.get(rest, "")
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\x03":
        return "ctrl-c"
    if ch == b"\x04":
        return "esc"
    if ch in (b"\x7f", b"\x08"):
        return "backspace"
    if ch and ch[0] >= 0xC0:  # start of a multi-byte character
        need = 1 if ch[0] < 0xE0 else 2 if ch[0] < 0xF0 else 3
        ch += os.read(0, need)
    return ch.decode("utf-8", "ignore")


class _raw:
    """Keys one at a time, no echo (termios on Unix, msvcrt on Windows)."""

    def __enter__(self):
        self._old = None
        if _use_windows_keys():
            enable_vt()
            return self
        import termios
        import tty

        self._old = termios.tcgetattr(0)
        tty.setcbreak(0)
        return self

    def __exit__(self, *exc):
        if self._old is not None:
            import termios

            termios.tcsetattr(0, termios.TCSADRAIN, self._old)
        return False


def _can_key() -> bool:
    try:
        return _tty() and sys.stdin.isatty()
    except Exception:
        return False


def keys_hint(back: bool = False) -> str:
    if unicode_ok():
        return "↑↓ pick · Enter ok" + (" · Esc back" if back else " · Esc skip")
    return "up/down pick, Enter ok" + (", Esc back" if back else ", Esc skip")


def select(title: str, choices: list[str], default: int = 0, hint: str = "", *, back: bool = False) -> int | None:
    """Rail-style single choice: arrows + Enter. Works on Linux, macOS and Windows.

    Returns the chosen index, or None when skipped. With back=True, Esc/left raises GoBack.
    Numbered fallback only when input is not a real terminal.
    """
    n = step_open(title)
    if not _can_key():
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

    help_line = hint or keys_hint(back)
    idx = default

    def draw(first: bool) -> None:
        if not first:
            sys.stdout.write(f"\033[{len(choices) + 1}A")
        for i, c in enumerate(choices):
            sys.stdout.write("\033[2K" + _row(c, i == idx) + "\n")
        sys.stdout.write("\033[2K" + f"{_bar()}  " + color(help_line, Colors.DIM) + "\n")
        sys.stdout.flush()

    result: int | None = None
    went_back = False
    sys.stdout.write("\033[?25l")
    try:
        with _raw():
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
                elif key in ("esc", "left", "ctrl-c"):
                    went_back = back and key != "ctrl-c"
                    break
                elif key.isdigit() and 1 <= int(key) <= len(choices):
                    idx = int(key) - 1
                draw(False)
    finally:
        sys.stdout.write("\033[?25h")
    n += len(choices) + 1
    if went_back:
        sys.stdout.write(f"\033[{n}A\033[J")
        raise GoBack()
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
            mark = color(S("done"), Colors.DIM)
            print(f"{mark} {color(self.done, Colors.DIM)}")
            gap()
        return False


def leaves(done: int, total: int = 3) -> str:
    return f"Leaf {done} of {total}"


def leaf_step(done: int, total: int = 3) -> None:
    """Legacy hook kept for callers: the rail needs no per-step header."""
    return None


def image_protocol() -> str | None:
    """Inline-image protocol this terminal speaks: kitty, iterm, sixel, or None.

    CLOVER_IMAGE=kitty|iterm|sixel|none overrides detection.
    """
    env = os.environ
    forced = env.get("CLOVER_IMAGE", "").lower()
    if forced:
        return forced if forced in ("kitty", "iterm", "sixel") else None
    if not should_use_color() or env.get("CLOVER_ASCII") or env.get("TERM") == "dumb":
        return None
    prog, term = env.get("TERM_PROGRAM", ""), env.get("TERM", "")
    if term == "xterm-kitty" or env.get("KITTY_WINDOW_ID") or prog == "ghostty":
        return "kitty"
    if prog in ("iTerm.app", "WezTerm"):
        return "iterm"
    if env.get("WT_SESSION") or term in ("foot", "foot-extra") or term.startswith("mlterm"):
        return "sixel"
    return None


def _data(name: str) -> bytes:
    from pathlib import Path

    return (Path(__file__).parent / "data" / name).read_bytes()


def logo_image() -> str:
    """Escape sequence drawing the real Clover logo, or '' when unsupported."""
    proto = image_protocol()
    try:
        if proto == "kitty":
            import base64

            b64 = base64.b64encode(_data("clover-logo.png")).decode()
            chunks = [b64[i : i + 4096] for i in range(0, len(b64), 4096)]
            out = []
            for i, ch in enumerate(chunks):
                head = "a=T,f=100,c=16,r=8,q=2," if i == 0 else ""
                out.append(f"\033_G{head}m={1 if i < len(chunks) - 1 else 0};{ch}\033\\")
            return "".join(out)
        if proto == "iterm":
            import base64

            raw = _data("clover-logo.png")
            b64 = base64.b64encode(raw).decode()
            return f"\033]1337;File=inline=1;size={len(raw)};width=16;height=8;preserveAspectRatio=1:{b64}\a"
        if proto == "sixel":
            return _data("clover-logo.six").decode("ascii")
    except Exception:
        return ""
    return ""


def banner() -> None:
    """Welcome: the real logo where the terminal can show images, then CLOVER, then the rail."""
    print()
    img = logo_image()
    if img:
        sys.stdout.write("  " + img + "\n")
        sys.stdout.flush()
    if unicode_ok() and _cols() >= 50:
        for line in _wordmark_lines():
            print("  " + line)
    else:
        print("  " + gradient("Clover") if should_use_color() else "  Clover")
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
    """Finish: close the rail with the win message, then next steps."""
    outro(message)
    print()
    for line in extra or []:
        print("   " + line)


def offer_hi(name: str) -> bool:
    """Ask 'Say hi now?'. Returns True only on a real terminal when the user says yes."""
    if not _can_key() or os.environ.get("CI"):
        return False
    print(color(f"   Say hi to {name} now? ", Colors.BOLD) + color("Y/n", Colors.DIM), end=" ")
    sys.stdout.flush()
    try:
        with _raw():
            key = _read_key()
    except Exception:
        return False
    print()
    return key.lower() in ("y", "enter", "")


def greeting(name: str) -> str:
    """Live preview line for the name step."""
    shown = name.strip() or "Clover"
    return f"Hi, I'm {shown} {S('clover')}"


def _reveal(lines: list[str], delay: float) -> None:
    """Print lines one by one on a TTY (Enter skips the rest of the delay)."""
    animate = animation_ok()
    for line in lines:
        print(line)
        if animate:
            sys.stdout.flush()
            if _wait(delay):
                animate = False
