import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LandingSuggestions } from './landing-suggestions'

const insert = vi.hoisted(() => vi.fn())

vi.mock('./focus', () => ({ requestComposerInsert: insert }))

afterEach(() => {
  cleanup()
  insert.mockReset()
})

describe('landing suggestions', () => {
  it.each([
    ['Draft a quote', 'Help me draft a quote for'],
    ['Plan my week', 'Help me plan my week:'],
    ['Reply to a client', 'Help me reply to a client:'],
    ['Feeling lucky?', 'Surprise me with an idea for']
  ])('puts %s into the intended composer without submitting', (label, prompt) => {
    render(<LandingSuggestions target="tile:example" />)
    fireEvent.click(screen.getByRole('button', { name: label }))
    expect(insert).toHaveBeenCalledExactlyOnceWith(prompt, { target: 'tile:example' })
  })
})
