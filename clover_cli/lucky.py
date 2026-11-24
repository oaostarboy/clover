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
    print(gradient(f"{'🍀 ' if should_use_color() else ''}{headline}"))
    print(color(f"  {instruction}", Colors.DIM))


def leaves(done: int, total: int = 3) -> str:
    """Leaf meter: filled leaves are 🍀, the rest ☘."""
    if not should_use_color():
        return f"[Leaf {done} of {total}]"
    return f"{'🍀' * done}{'☘ ' * (total - done)}".rstrip() + f"  Leaf {done} of {total}"


def leaf_step(done: int, total: int = 3) -> None:
    if done > 1 and animation_ok():
        # Fresh screen per step so each one fits 80x26 on a phone.
        sys.stdout.write("\033[2J\033[H")
    else:
        print()
    print(color(leaves(done, total), Colors.DIM))


def banner() -> None:
    """Gradient wordmark line."""
    print()
    print("  " + gradient("☘ C L O V E R"))
    print(color("  Feeling lucky? Let's grow your Clover.", Colors.DIM))


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


def flourish(message: str) -> None:
    """Tiny finish moment: a row of leaves grows in, then the message."""
    if not animation_ok():
        print(color(message, Colors.GREEN, Colors.BOLD))
        return
    row = ""
    for _ in range(5):
        row += "🍀 "
        sys.stdout.write(f"\r\033[K  {row}")
        sys.stdout.flush()
        if _wait(0.12):
            break
    sys.stdout.write("\r\033[K  " + gradient(message) + "\n")
    sys.stdout.flush()
