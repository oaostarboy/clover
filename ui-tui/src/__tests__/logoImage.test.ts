import { describe, expect, it } from 'vitest'

import { $introMode, setIntroMode } from '../app/tourStore.js'
import { logoSequence, logoSpec } from '../lib/logoSpec.js'

const env = (o: Record<string, string>) => ({ CLOVER_LOGO: 'sixel;16;8;160', CLOVER_LOGO_DIR: '../clover_cli/data', ...o }) as NodeJS.ProcessEnv

describe('welcome logo', () => {
  it('uses the real image only when the launcher found a protocol', () => {
    expect(logoSpec(env({}), true)?.proto).toBe('sixel')
    expect(logoSpec({} as NodeJS.ProcessEnv, true)).toBeNull()
  })

  it('falls back to letters on NO_COLOR, CI, non-TTY or junk', () => {
    expect(logoSpec(env({ NO_COLOR: '1' }), true)).toBeNull()
    expect(logoSpec(env({ CI: 'true' }), true)).toBeNull()
    expect(logoSpec(env({}), false)).toBeNull()
    expect(logoSpec(env({ CLOVER_LOGO: 'bogus;1;1;1' }), true)).toBeNull()
  })

  it('emits the real logo bytes per protocol', () => {
    const s = (p: string) => logoSequence(logoSpec(env({ CLOVER_LOGO: `${p};16;8;160` }), true)!)
    expect(s('sixel').startsWith('\x1bP')).toBe(true)
    expect(s('iterm').startsWith('\x1b]1337;File=inline=1')).toBe(true)
    expect(s('kitty').startsWith('\x1b_Ga=T,f=100')).toBe(true)
  })

  it('intro mode starts pending', () => {
    expect($introMode.get()).toBe('pending')
    setIntroMode('first')
    expect($introMode.get()).toBe('first')
  })
})
