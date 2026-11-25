import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Pure helpers for the welcome logo (kept free of ink imports so they unit-test).
// See logoImage.tsx for how it is drawn.

export interface LogoSpec {
  cols: number
  dir: string
  proto: 'iterm' | 'kitty' | 'sixel'
  rows: number
}

export const logoSpec = (env: NodeJS.ProcessEnv = process.env, isTTY = !!process.stdout.isTTY): LogoSpec | null => {
  const raw = env.CLOVER_LOGO
  const dir = env.CLOVER_LOGO_DIR

  if (!raw || !dir || !isTTY || env.NO_COLOR || env.CI || env.CLOVER_ASCII || env.TERM === 'dumb') {
    return null
  }

  const [proto, c, r] = raw.split(';')
  const cols = Number(c)
  const rows = Number(r)

  if ((proto !== 'kitty' && proto !== 'iterm' && proto !== 'sixel') || !(cols > 0) || !(rows > 0)) {
    return null
  }

  return { cols, dir, proto, rows }
}

export const logoSequence = (spec: LogoSpec): string => {
  try {
    if (spec.proto === 'sixel') {
      return readFileSync(join(spec.dir, 'clover-logo.six'), 'ascii')
    }

    const png = readFileSync(join(spec.dir, 'clover-logo.png'))
    const b64 = png.toString('base64')

    if (spec.proto === 'iterm') {
      return `\x1b]1337;File=inline=1;size=${png.length};width=auto;height=${spec.rows};preserveAspectRatio=1;doNotMoveCursor=1:${b64}\x07`
    }

    const out: string[] = []

    for (let i = 0; i < b64.length; i += 4096) {
      const more = i + 4096 < b64.length ? 1 : 0
      const head = i === 0 ? `a=T,f=100,r=${spec.rows},C=1,q=2,` : ''
      out.push(`\x1b_G${head}m=${more};${b64.slice(i, i + 4096)}\x1b\\`)
    }

    return out.join('')
  } catch {
    return ''
  }
}
