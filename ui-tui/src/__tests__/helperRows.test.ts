import { describe, expect, it } from 'vitest'

import { cutTask, fmtHelperTime, HELPER_ROWS_SHOWN, helperRow, helperRows, helperSummary } from '../lib/helperRows.js'
import type { SubagentProgress } from '../types.js'

const item = (o: Partial<SubagentProgress> & Pick<SubagentProgress, 'id' | 'index'>): SubagentProgress => ({
  depth: 0,
  goal: o.id,
  notes: [],
  parentId: null,
  status: 'running',
  taskCount: 1,
  thinking: [],
  toolCount: 0,
  tools: [],
  ...o
})

const three = [
  item({ durationSeconds: 40, goal: 'nice & tidy site', id: 'a', index: 0, status: 'completed' }),
  item({ goal: 'silas studios site', id: 'b', index: 1, startedAt: 1_000_000 }),
  item({
    durationSeconds: 12,
    goal: 'pizzeria bros pamphlet',
    id: 'c',
    index: 2,
    status: 'failed',
    summary: 'the page was down'
  })
]

describe('helper rows: summary + one row per helper', () => {
  it('summary line counts states', () => {
    expect(helperSummary(three)).toBe('3 helpers · 1 working · 1 done · 1 failed')
    expect(helperSummary([three[1]!])).toBe('1 helper · working')
  })

  it('three helpers, mixed states (golden)', () => {
    const { more, shown } = helperRows(three, 1_072_000)
    expect(more).toBe('')
    expect(shown.map(r => r.text)).toEqual([
      '✓ nice & tidy site · 40s',
      '● silas studios site · 1m12s',
      '✕ pizzeria bros pamphlet · failed · 12s'
    ])
    expect(shown[2]!.reason).toBe('the page was down')
  })

  it('failed is the only row with ✕ and a word; no colour is needed to tell', () => {
    const rows = helperRows(three, 1_072_000).shown
    expect(rows.filter(r => r.text.includes('✕'))).toHaveLength(1)
    expect(rows.filter(r => r.text.includes('failed'))).toHaveLength(1)
  })

  it('cuts a long task on a word with an ellipsis', () => {
    const long = 'Make the terminal welcome look polished with a real logo bigger and clean CLOVER letters'
    const row = helperRow(item({ goal: long, id: 'x', index: 0, startedAt: 1000 }), 6000)
    expect(row.text).toBe('● Make the terminal welcome look… · 5s')
    expect(cutTask('short', 34)).toBe('short')
  })

  it('formats timers', () => {
    expect([5, 72, 612, 3700].map(fmtHelperTime)).toEqual(['5s', '1m12s', '10m', '1h01m'])
  })

  it('a crowd folds finished rows, keeps failures and live ones', () => {
    const many = Array.from({ length: HELPER_ROWS_SHOWN + 5 }, (_, i) =>
      item({ goal: `t${i}`, id: `i${i}`, index: i, status: i < 5 ? 'completed' : i === 5 ? 'failed' : 'running' })
    )

    const { more, shown } = helperRows(many, 0)
    expect(more).toBe('+5 done')
    expect(shown).toHaveLength(HELPER_ROWS_SHOWN)
    expect(shown.some(r => r.kind === 'failed')).toBe(true)
  })

  it('uses only plain glyphs (NO_COLOR safe) and stays on one narrow row', () => {
    const { shown } = helperRows(three, 1_072_000)

    for (const r of shown) {
      expect(r.text.includes(String.fromCharCode(27))).toBe(false)
      expect(r.text.length).toBeLessThanOrEqual(60)
    }
  })
})
