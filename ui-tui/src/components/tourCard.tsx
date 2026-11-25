import { Box, Text } from '@clover/ink'
import { useStore } from '@nanostores/react'

import { $tour, TOUR_STEPS, type TourStep } from '../app/tourStore.js'
import type { Theme } from '../theme.js'

// Block letters + flat 4-point star. Same shapes as `clover setup` so the
// first thing you see in the app matches the first thing setup showed.
const GLYPHS: Record<string, string[]> = {
  C: ['██████', '██    ', '██    ', '██    ', '██████'],
  E: ['██████', '██    ', '████  ', '██    ', '██████'],
  L: ['██    ', '██    ', '██    ', '██    ', '██████'],
  O: ['██████', '██  ██', '██  ██', '██  ██', '██████'],
  R: ['█████ ', '██  ██', '█████ ', '██ ██ ', '██  ██'],
  V: ['██  ██', '██  ██', '██  ██', ' ████ ', '  ██  ']
}

export const WORDMARK = [0, 1, 2, 3, 4].map(r => [...'CLOVER'].map(ch => GLYPHS[ch]![r]).join(' ').trimEnd())

export const STAR = [
  '     ██     ',
  '    ████    ',
  '   ██████   ',
  ' ██████████ ',
  '   ██████   ',
  '    ████    ',
  '     ██     '
]

// Brand greens (same pair `clover setup` paints with), light → deep.
const GREEN_B = '#24AA6D'
const GREENS = ['#92F2BA', '#7BE3A9', '#63D498', '#4DC588', '#38B778', '#24AA6D', '#24AA6D']

const unicodeOk = () =>
  process.env.TERM !== 'linux' &&
  !(process.platform === 'win32' && !process.env.WT_SESSION && !process.env.TERM_PROGRAM && !process.env.ConEmuANSI)

const glyph = (s: string) => (unicodeOk() ? s : s.replace(/█/g, '#'))

export const lockupWidth = STAR[0]!.length + 3 + WORDMARK[0]!.length

interface Copy {
  body: string
  title: string
}

export const STEP_COPY: Record<(typeof TOUR_STEPS)[number], Copy> = {
  help: { body: 'Type /help and press Enter. That is the whole menu.', title: 'Find everything' },
  model: { body: 'Type /model and press Enter to see your AI choices. Esc closes it.', title: 'Pick a brain' },
  'say-hi': { body: 'Type anything, like "hi", and press Enter.', title: 'Say hi' },
  stop: { body: 'Ask for something long ("write a long poem"), then press Ctrl+C to stop it.', title: 'Stop a reply' }
}

export function Lockup({ cols, t }: { cols: number; t: Theme }) {
  const shades = GREENS

  if (cols < lockupWidth + 6) {
    return (
      <Text bold color={GREEN_B}>
        {glyph(WORDMARK.join('\n'))}
      </Text>
    )
  }

  return (
    <Box flexDirection="column">
      {STAR.map((row, i) => {
        const j = i - 1 // letters sit on the star's middle five rows
        const letters = j >= 0 && j < WORDMARK.length ? WORDMARK[j]! : ''

        return (
          <Text key={i}>
            <Text color={shades[i]}>{glyph(row)}</Text>
            <Text color={GREENS[Math.min(i, GREENS.length - 1)]}>{'   ' + glyph(letters)}</Text>
          </Text>
        )
      })}
    </Box>
  )
}

const Leaves = ({ done, t }: { done: number; t: Theme }) => (
  <Text>
    {TOUR_STEPS.map((_, i) => (
      <Text color={i < done ? GREEN_B : t.color.muted} key={i}>
        {i < done ? '● ' : '○ '}
      </Text>
    ))}
  </Text>
)

export function TourCard({ cols, t }: { cols: number; t: Theme }) {
  const step = useStore($tour)

  if (!step) {
    return null
  }

  const bar = <Text color={GREEN_B}>│  </Text>
  const row = (node: React.ReactNode, k: string) => <Text key={k}>{bar}{node}</Text>
  const muted = (s: string) => <Text color={t.color.muted}>{s}</Text>

  if (step === 'welcome') {
    return (
      <Box flexDirection="column" marginBottom={1}>
        <Lockup cols={cols} t={t} />
        <Text color={GREEN_B}>│</Text>
        {row(<Text bold>Welcome to Clover 🍀</Text>, 'a')}
        {row(<Text>Your AI buddy, right in your terminal. It chats, codes and gets things done.</Text>, 'b')}
        <Text color={GREEN_B}>│</Text>
        {row(
          <Text>
            <Text bold color={GREEN_B}>Enter</Text>
            <Text> for a 1-minute hands-on tour</Text>
            {muted('   ·   Esc skip   ·   /tour brings it back')}
          </Text>,
          'c'
        )}
      </Box>
    )
  }

  if (step === 'done') {
    return (
      <Box flexDirection="column" marginBottom={1}>
        {row(<Text bold color={GREEN_B}>You struck gold! 🍀 That is all you need.</Text>, 'a')}
        {row(<Text>Just talk to Clover. Type /tour any time to play this again.</Text>, 'b')}
        {row(muted('Enter to close'), 'c')}
      </Box>
    )
  }

  const n = TOUR_STEPS.indexOf(step)
  const copy = STEP_COPY[step]

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text>
        <Text color={GREEN_B}>├─ </Text>
        <Text bold>{`Leaf ${n + 1} of ${TOUR_STEPS.length} · ${copy.title}`}</Text>
      </Text>
      {row(<Text>{copy.body}</Text>, 'a')}
      {row(
        <Text>
          <Leaves done={n} t={t} />
          {muted('  Esc skip this step')}
        </Text>,
        'b'
      )}
    </Box>
  )
}

export type { TourStep }
