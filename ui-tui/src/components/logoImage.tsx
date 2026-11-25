
import { Box, useStdout } from '@clover/ink'
import { useEffect, useMemo, useRef } from 'react'

import { logoSequence, type LogoSpec } from '../lib/logoSpec.js'


// The REAL Clover star (same files `clover setup` shows), drawn as an inline
// image where the terminal can do it. The launcher (clover_cli/main.py) runs
// lucky.image_protocol() before the TUI takes the tty and passes the answer:
//   CLOVER_LOGO     = "<kitty|iterm|sixel>;<cols>;<rows>;<sixel px>"
//   CLOVER_LOGO_DIR = clover_cli/data
// No protocol (or NO_COLOR / CI / non-TTY / inline mode) → letters only. We
// never draw the star out of block characters.

// Minimal shape of an ink DOM node (the type isn't exported from @clover/ink).
interface DOMElement {
  parentNode?: DOMElement
  yogaNode?: { getComputedLeft(): number; getComputedTop(): number }
}

const origin = (el: DOMElement) => {
  let x = 0
  let y = 0

  for (let n: DOMElement | undefined = el; n; n = n.parentNode) {
    x += n.yogaNode?.getComputedLeft() ?? 0
    y += n.yogaNode?.getComputedTop() ?? 0
  }

  return { x, y }
}

/** Reserves cols×rows blank cells and paints the star over them. */
export function LogoImage({ spec }: { spec: LogoSpec }) {
  const ref = useRef<DOMElement | null>(null)
  const { write } = useStdout()
  const lastAt = useRef<null | { x: number; y: number }>(null)
  const seq = useMemo(() => logoSequence(spec), [spec])

  useEffect(() => {
    if (!seq) {
      return
    }

    const paint = () => {
      if (!ref.current) {
        return
      }

      const { x, y } = origin(ref.current)
      lastAt.current = { x, y }
      write(`\x1b7\x1b[${y + 1};${x + 1}H${seq}\x1b8`)
    }

    // Ink paints its own frame first; image goes on after, over blank cells.
    const timers = [60, 250, 800].map(ms => setTimeout(paint, ms))

    return () => {
      timers.forEach(clearTimeout)

      // Pixels outlive cells: when the card goes away, blank the image area
      // so no ghost star is left behind.
      const at = lastAt.current

      if (at) {
        const blank = ' '.repeat(spec.cols)
        const rows = Array.from({ length: spec.rows }, (_, i) => `\x1b[${at.y + 1 + i};${at.x + 1}H${blank}`).join('')
        write(`\x1b7${rows}\x1b8`)
      }
    }
  }, [seq, spec.cols, spec.rows, write])

  return <Box flexShrink={0} height={spec.rows} ref={ref} width={spec.cols} />
}
