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


LEAF_LABELS = ("Name", "Brain", "Go")


def leaves(done: int, total: int = 3) -> str:
    """Leaf meter: 🍀 Name ━━ ☘ Brain ━━ ☘ Go   leaf 1 of 3."""
    if not should_use_color():
        return f"[Leaf {done} of {total}]"
    parts = []
    for i in range(total):
        label = LEAF_LABELS[i] if i < len(LEAF_LABELS) else f"{i + 1}"
        if i < done:
            parts.append("🍀 " + gradient(label))
        else:
            parts.append(color("☘ " + label, Colors.DIM))
    joiner = color(" ━━ ", Colors.DIM)
    return "  " + joiner.join(parts) + color(f"     Leaf {done} of {total}", Colors.DIM)


def leaf_step(done: int, total: int = 3) -> None:
    if done > 1 and animation_ok():
        # Fresh screen per step so each one fits 80x26 on a phone.
        sys.stdout.write("\033[2J\033[H")
    else:
        print()
    print(leaves(done, total))
    print(rule())


def banner() -> None:
    """Welcome: the clover mark next to the CLOVER block letters, grown in row by row."""
    wide = _cols() >= 80
    side = _wordmark_lines() if wide else [gradient("C L O V E R")]
    lines = art(side)
    print()
    _reveal(lines, 0.045)
    print()
    print("  " + gradient("Feeling lucky?") + color("  Let's grow your Clover. Three quick leaves and you're in.", Colors.DIM))
    print(rule())


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
    """Under 1.5s seed -> ☘ -> 🍀. Skippable with Enter. No-op off a TTY."""
    if not animation_ok():
        print(color(f"  {message}", Colors.DIM))
        return
    for frame in ("·", "☘", "🍀"):
        sys.stdout.write(f"\r\033[K  {gradient(frame)} {color(message, Colors.DIM)}")
        sys.stdout.flush()
        if _wait(0.4):
            break
    sys.stdout.write("\n")
    sys.stdout.flush()


def flourish(message: str, extra: list[str] | None = None) -> None:
    """Finish moment: confetti leaves, then the mark beside the win message."""
    if not should_use_color():
        print(message)
        for line in extra or []:
            print(line)
        return
    side = [gradient(message.upper()), ""] + list(extra or [])
    if animation_ok():
        row = ""
        for _ in range(7):
            row += "🍀 "
            sys.stdout.write(f"\r\033[K  {row}")
            sys.stdout.flush()
            if _wait(0.08):
                break
        sys.stdout.write("\r\033[K")
    _reveal(art(side), 0.03)


def _reveal(lines: list[str], delay: float) -> None:
    """Print lines one by one on a TTY (Enter skips the rest of the delay)."""
    animate = animation_ok()
    for line in lines:
        print(line)
        if animate:
            sys.stdout.flush()
            if _wait(delay):
                animate = False
