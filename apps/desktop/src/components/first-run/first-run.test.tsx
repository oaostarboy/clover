import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BUSY_LINE, CloverWordmark, COPY, FirstRunScreen, isBusyError, LANDING_LINE, matchCodeLine, previewGreeting } from './index'

describe('first-run copy', () => {
  it('lands with one short luck line', () => {
    expect(LANDING_LINE).toMatch(/luck/i)
    expect(LANDING_LINE).not.toContain('!')
  })

  it('previews the name live, and falls back to Luna', () => {
    expect(previewGreeting('')).toBe('Hi, I’m Luna')
    expect(previewGreeting('  Nova ')).toBe('Hi, I’m Nova')
  })

  it('uses plain words, no exclamation marks, no emoji', () => {
    const text = Object.values(COPY).join(' ')

    for (const jargon of ['api', 'env', 'token', 'oauth', 'toolset', 'gateway']) {
      expect(text.toLowerCase()).not.toContain(jargon)
    }

    expect(text).not.toContain('!')
    expect(text).not.toMatch(/\p{Extended_Pictographic}/u)
  })

  it('shows the phone match code the phone is asking with', () => {
    expect(matchCodeLine({ capabilities: [], created_at: 0, match_code: 'K7QM', request_id: 'r' })).toBe('Check it shows K7QM')
    expect(matchCodeLine(undefined)).toBe('')
  })

  it('draws the wordmark as the solid CLOVER block letters', () => {
    const { container } = render(<CloverWordmark />)

    expect(container.querySelector('svg')?.getAttribute('aria-label')).toBe('Clover')
    expect(container.querySelectorAll('rect').length).toBeGreaterThan(20)
  })
})

describe('first-run screen', () => {
  const ctx = { onCompleted: () => undefined, profile: 'default', requestGateway: (async () => ({})) as never }

  it('is one screen: name, AI, phone, Start', () => {
    render(<FirstRunScreen ctx={ctx} enabled={false} />)
    expect(screen.getByText(COPY.title)).toBeTruthy()
    expect(screen.getByLabelText('Your Clover’s name')).toBeTruthy()
    expect(screen.getByText('Phone')).toBeTruthy()
    expect((screen.getByRole('button', { name: COPY.start }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(COPY.startNeed)).toBeTruthy()
    expect(screen.getByText(COPY.startWithout)).toBeTruthy()
  })
})

describe('phone step busy relay', () => {
  it('spots a relay 429 through the IPC error wrapper', () => {
    expect(isBusyError(new Error("Error invoking remote method 'clover:api': Error: enrollment failed: 429"))).toBe(true)
    expect(isBusyError(new Error('enrollment failed: 503'))).toBe(false)
    expect(isBusyError(undefined)).toBe(false)
  })

  it('has friendly copy without jargon', () => {
    expect(BUSY_LINE).toBe('Lots of people are connecting right now. Try again in a minute.')
  })
})
