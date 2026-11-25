import { useState } from 'react'

import { formatElapsed, useElapsedSeconds } from '@/components/chat/activity-timer'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { useSessionSlice } from '@/lib/use-session-slice'
import { cn } from '@/lib/utils'
import { $gateway } from '@/store/gateway'
import { requestForOwnedSession } from '@/store/session-states'
import { $subagentsBySession, type SubagentProgress } from '@/store/subagents'
import { openSessionInNewWindow } from '@/store/windows'

async function ambientRequest<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  const gateway = $gateway.get()

  if (!gateway) {
    throw new Error('Gateway unavailable')
  }

  return gateway.request<T>(method, params)
}

type Tone = 'done' | 'failed' | 'idle' | 'queued' | 'working'

const TONE: Record<SubagentProgress['status'], Tone> = {
  completed: 'done',
  failed: 'failed',
  interrupted: 'idle',
  queued: 'queued',
  running: 'working'
}

const isActive = (agent: SubagentProgress) => agent.status === 'running' || agent.status === 'queued'

/** Plain words for the one-line summary: "3 helpers · 1 working · 1 done · 1 failed". */
export function helperSummary(agents: readonly SubagentProgress[]): string {
  const n = (status: SubagentProgress['status']) => agents.filter(agent => agent.status === status).length

  const parts = [
    `${agents.length} ${agents.length === 1 ? 'helper' : 'helpers'}`,
    [n('running'), 'working'],
    [n('queued'), 'waiting'],
    [n('completed'), 'done'],
    [n('failed'), 'failed'],
    [n('interrupted'), 'stopped']
  ].flatMap(part => (typeof part === 'string' ? [part] : part[0] ? [`${part[0]} ${part[1]}`] : []))

  return parts.join(' · ')
}

/** Status dot: working = mint and breathing, done = green, failed = red. */
export function HelperDot({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden
      className={cn(
        'helper-dot size-2.5 shrink-0 rounded-full',
        tone === 'working' && 'helper-dot-working bg-[#7be0a8]',
        tone === 'done' && 'bg-[#2fb36f]',
        tone === 'failed' && 'bg-[#ef5350] ring-4 ring-[#ef5350]/20',
        tone === 'queued' && 'border-[1.5px] border-[#7be0a8]/70',
        tone === 'idle' && 'bg-(--ui-text-tertiary)'
      )}
      data-tone={tone}
    />
  )
}

function Timer({ agent }: { agent: SubagentProgress }) {
  const active = isActive(agent)
  const elapsed = useElapsedSeconds(active, undefined, agent.startedAt)

  return <span className="shrink-0 text-[0.8125rem] tabular-nums text-(--ui-text-secondary)">{formatElapsed(agent.durationSeconds ?? elapsed)}</span>
}

function HelperRow({ agent, onToggle, open, sessionId }: { agent: SubagentProgress; onToggle: () => void; open: boolean; sessionId: string }) {
  const { t } = useI18n()
  const copy = t.statusStack.delegation
  const active = isActive(agent)
  const tone = TONE[agent.status]
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')
  const thinking = agent.stream.filter(entry => entry.kind === 'thinking')
  const activity = agent.stream.filter(entry => entry.kind !== 'thinking' && !(entry.kind === 'summary' && entry.text === agent.summary))

  const stop = async () => {
    setStopping(true)
    setError('')

    try {
      const result = await requestForOwnedSession<{ found: boolean }>(sessionId, ambientRequest, 'subagent.interrupt', {
        subagent_id: agent.id
      })

      if (!result.found) {
        setError(copy.stopUnavailable)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.stopUnavailable)
    } finally {
      setStopping(false)
    }
  }

  return (
    <div className="delegation-helper" data-helper-status={agent.status}>
      <div className="flex items-center gap-3">
        <button
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-md py-1 text-left [-webkit-app-region:no-drag] hover:bg-(--ui-control-hover-background)"
          onClick={onToggle}
          type="button"
        >
          <HelperDot tone={tone} />
          <span className="min-w-0 flex-1 truncate text-sm text-foreground">{agent.goal}</span>
          {tone === 'failed' && <span className="shrink-0 text-[0.8125rem] font-medium text-[#ef5350]">{copy.failed}</span>}
          {tone === 'idle' && <span className="shrink-0 text-[0.8125rem] text-(--ui-text-secondary)">{copy.interrupted}</span>}
          <Timer agent={agent} />
        </button>
        {active ? (
          <Button disabled={stopping} onClick={() => void stop()} size="sm" variant="outline">
            {t.statusStack.stop}
          </Button>
        ) : (
          <span aria-hidden className="w-[3.25rem] shrink-0" />
        )}
      </div>
      {tone === 'failed' && !open && agent.summary && <p className="ml-[1.375rem] mt-0.5 text-[0.8125rem] text-[#ef5350]">{agent.summary}</p>}
      {open && (
        <div className="delegation-details ml-[1.375rem] mt-1.5 grid gap-2 border-l-2 border-[#24aa6d]/50 pl-3 text-[0.8125rem] leading-5 text-(--ui-text-secondary)">
          <div className="flex flex-wrap gap-x-3">
            {agent.model && <span>{agent.model}</span>}
            <span>{copy[agent.status]}</span>
            {agent.toolCount !== undefined && <span>{copy.tools(agent.toolCount)}</span>}
            {agent.sessionId && (
              <button className="text-foreground underline-offset-2 hover:underline" onClick={() => void openSessionInNewWindow(agent.sessionId!, { watch: true })} type="button">
                {copy.openSession}
              </button>
            )}
          </div>
          {agent.summary && <p className={cn('whitespace-pre-wrap wrap-anywhere', tone === 'failed' ? 'text-[#ef5350]' : 'text-foreground')}>{agent.summary}</p>}
          {error && (
            <p className="text-destructive" role="alert">
              {error}
            </p>
          )}
          {thinking.length > 0 && (
            <details>
              <summary className="cursor-pointer text-foreground">{copy.recentThinking}</summary>
              <div className="delegation-stream mt-1 grid gap-1">
                {thinking.map((entry, index) => (
                  <p className="whitespace-pre-wrap wrap-anywhere" key={`${entry.at}:${index}`}>
                    {entry.text}
                  </p>
                ))}
              </div>
            </details>
          )}
          {activity.length > 0 && (
            <details>
              <summary className="cursor-pointer text-foreground">{copy.activity}</summary>
              <div className="delegation-stream mt-1 grid gap-1">
                {activity.map((entry, index) => (
                  <p className={cn('whitespace-pre-wrap wrap-anywhere', entry.isError && 'text-destructive')} key={`${entry.at}:${index}`}>
                    {entry.text}
                  </p>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

export function SubagentStrip({ sessionId }: { sessionId: string }) {
  const agents = useSessionSlice($subagentsBySession, sessionId)
  const [openId, setOpenId] = useState<null | string>(null)

  if (!agents.length) {
    return null
  }

  const anyFailed = agents.some(agent => agent.status === 'failed')

  return (
    <section className="delegation-strip" data-slot="subagent-strip">
      <div
        className={cn('px-3 pt-2 pb-1 text-[0.8125rem] font-medium', anyFailed ? 'text-[#ef5350]' : 'text-(--ui-text-secondary)')}
        data-testid="helper-summary"
      >
        {helperSummary(agents)}
      </div>
      <div className="px-3 pb-2">
        {agents.map(agent => (
          <HelperRow
            agent={agent}
            key={agent.id}
            onToggle={() => setOpenId(id => (id === agent.id ? null : agent.id))}
            open={openId === agent.id}
            sessionId={sessionId}
          />
        ))}
      </div>
    </section>
  )
}
