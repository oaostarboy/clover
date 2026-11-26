import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { $subagentsBySession, upsertSubagent } from '@/store/subagents'

import { helperSummary, SubagentStrip } from './subagent-strip'
const request = vi.fn().mockResolvedValue({ found: true })
vi.mock('@/store/session-states', () => ({ requestForOwnedSession: (...args: unknown[]) => request(...args) }))
vi.mock('@/store/windows', () => ({ openSessionInNewWindow: vi.fn() }))
afterEach(() => {
  cleanup()
  $subagentsBySession.set({})
  request.mockClear()
})
it('keeps real thinking and Stop available without animation finishing the job', async () => {
  upsertSubagent('s', {
    subagent_id: 'a',
    goal: 'Check the real source',
    model: 'provider/model',
    status: 'running',
    tool_count: 4
  })
  upsertSubagent('s', { subagent_id: 'a', text: 'Provider supplied summary' }, false, 'subagent.thinking')
  render(<SubagentStrip sessionId="s" />)
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  expect(screen.getByText('provider/model')).toBeTruthy()
  expect(screen.getByText('Provider supplied summary')).toBeTruthy()
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Stop' })))
  expect(request).toHaveBeenCalledWith('s', expect.any(Function), 'subagent.interrupt', { subagent_id: 'a' })
  expect($subagentsBySession.get().s[0].status).toBe('running')
  act(() =>
    upsertSubagent('s', { subagent_id: 'a', status: 'failed', summary: 'Backend failure' }, false, 'subagent.complete')
  )
  expect(screen.getByText('Backend failure')).toBeTruthy()
})
it('summarises the helpers in one plain line', () => {
  upsertSubagent('s', { subagent_id: 'a', goal: 'One', status: 'running' })
  upsertSubagent('s', { subagent_id: 'b', goal: 'Two', status: 'running' })
  upsertSubagent('s', { subagent_id: 'c', goal: 'Three', status: 'running' })
  upsertSubagent('s', { subagent_id: 'b', status: 'failed', summary: 'No access' }, false, 'subagent.complete')
  upsertSubagent('s', { subagent_id: 'c', status: 'completed' }, false, 'subagent.complete')
  render(<SubagentStrip sessionId="s" />)
  expect(screen.getByTestId('helper-summary').textContent).toBe('3 helpers · 1 working · 1 done · 1 failed')
  // Failure is visible without opening anything.
  expect(screen.getByText('No access')).toBeTruthy()
  const dots = [...document.querySelectorAll('.helper-dot')].map(d => d.getAttribute('data-tone'))
  expect(dots).toEqual(['working', 'failed', 'done'])
})

it('formats singular and stopped', () => {
  const base = { filesRead: [], filesWritten: [], goal: 'x', parentId: null, startedAt: 0, stream: [], taskCount: 1, taskIndex: 0, updatedAt: 0 }

  expect(helperSummary([{ ...base, id: 'a', status: 'running' }])).toBe('1 helper · 1 working')
  expect(helperSummary([{ ...base, id: 'a', status: 'interrupted' }])).toBe('1 helper · 1 stopped')
})

it('shows Stop only while a helper is active', () => {
  upsertSubagent('s', { subagent_id: 'a', goal: 'One', status: 'running' })
  upsertSubagent('s', { subagent_id: 'b', goal: 'Two', status: 'completed' })
  render(<SubagentStrip sessionId="s" />)
  expect(screen.getAllByRole('button', { name: 'Stop' })).toHaveLength(1)
})
