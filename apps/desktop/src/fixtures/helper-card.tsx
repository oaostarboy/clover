import '../../src/styles.css'
import '../../src/components/assistant-ui/thread/transcript.css'

/// <reference types="vite/client" />
import React from 'react'
import { createRoot } from 'react-dom/client'

import { SubagentStrip } from '../../src/app/chat/composer/status-stack/subagent-strip'
import { $subagentsBySession, upsertSubagent } from '../../src/store/subagents'
import { ThemeProvider } from '../../src/themes/context'

// FIXTURE: made-up helpers (working / done / failed) to look at the card.
const S = 'fixture'
const t0 = Date.now() - 134_000

upsertSubagent(S, { subagent_id: 'a', goal: 'Reply to Béa about Friday’s order', model: 'claude-sonnet', status: 'running', tool_count: 6, started_at: t0 })
upsertSubagent(S, { subagent_id: 'a', text: 'Found her email, checking the calendar for Friday 4:30.' }, false, 'subagent.thinking')
upsertSubagent(S, { subagent_id: 'a', text: 'Read inbox: 3 threads from Alimentari' }, false, 'subagent.progress')
upsertSubagent(S, { subagent_id: 'b', goal: 'Summarize this week’s invoices', model: 'claude-sonnet', status: 'running', tool_count: 11, started_at: t0 - 60_000 })
upsertSubagent(S, { subagent_id: 'b', status: 'completed', summary: 'Seven invoices, 4,210 dollars, two overdue.', duration_seconds: 47 }, false, 'subagent.complete')
upsertSubagent(S, { subagent_id: 'c', goal: 'Book the 7:30 pickup with the courier', model: 'claude-sonnet', status: 'running', tool_count: 2, started_at: t0 - 30_000 })
upsertSubagent(S, { subagent_id: 'c', status: 'failed', summary: 'The courier site timed out twice.', duration_seconds: 22 }, false, 'subagent.complete')

// Spread the start times so the live timers read like real work.
const starts: Record<string, number> = { a: t0, b: t0 - 60_000, c: t0 - 30_000 }
$subagentsBySession.set({ [S]: $subagentsBySession.get()[S].map(h => ({ ...h, startedAt: starts[h.id] })) })

const open = new URLSearchParams(location.search).get('open')

createRoot(document.getElementById('root')!).render(
  <ThemeProvider>
    <div style={{ background: 'var(--ui-chat-surface-background)', minHeight: '100vh', padding: 40, display: 'flex', justifyContent: 'center', alignItems: 'flex-start' }}>
      <div data-open={open} style={{ width: 640 }}>
        <SubagentStrip sessionId={S} />
      </div>
    </div>
  </ThemeProvider>
)
