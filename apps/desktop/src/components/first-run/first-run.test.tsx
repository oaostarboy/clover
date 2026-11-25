import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TOUR_FINISH, TOUR_STOPS } from './main-tour'

import { BUSY_LINE, COPY, isBusyError, previewGreeting } from './index'

describe('first-run copy', () => {
  it('previews the name live, and falls back to Luna', () => {
    expect(previewGreeting('')).toBe('Hi, I’m Luna 🍀')
    expect(previewGreeting('  Nova ')).toBe('Hi, I’m Nova 🍀')
  })

  it('keeps the tour to three stops and ends on the gold line', () => {
    expect(TOUR_STOPS.length).toBeLessThanOrEqual(3)
    expect(TOUR_FINISH).toContain('You struck gold')
  })

  it('uses plain words on the main path', () => {
    const text = Object.values(COPY).join(' ').toLowerCase()

    for (const jargon of ['api', 'env', 'token', 'oauth', 'toolset', 'gateway']) {
      expect(text).not.toContain(jargon)
    }
  })

  it('renders without crashing', () => {
    render(<p>{COPY.welcomeTitle}</p>)
    expect(screen.getByText(COPY.welcomeTitle)).toBeTruthy()
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
