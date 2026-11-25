import { beforeEach, describe, expect, it } from 'vitest'

import { $tour, advanceTour, backTourStep, closeTour, openTour, skipTourStep, slashTourEvent, tourDid } from '../app/tourStore.js'

describe('tour', () => {
  beforeEach(() => closeTour())

  it('walks welcome → 4 steps → done → closed, by doing', () => {
    openTour()
    advanceTour()
    expect($tour.get()).toBe('say-hi')
    tourDid('message')
    expect($tour.get()).toBe('model')
    tourDid('model')
    expect($tour.get()).toBe('stop')
    tourDid('interrupt')
    expect($tour.get()).toBe('help')
    tourDid('help')
    expect($tour.get()).toBe('done')
    advanceTour()
    expect($tour.get()).toBeNull()
  })

  it('ignores actions for other steps', () => {
    openTour()
    advanceTour()
    tourDid('help')
    expect($tour.get()).toBe('say-hi')
  })

  it('Tab skips one step; Esc goes back, and leaves from the welcome card', () => {
    openTour()
    backTourStep()
    expect($tour.get()).toBeNull()
    openTour()
    advanceTour()
    skipTourStep()
    expect($tour.get()).toBe('model')
    backTourStep()
    expect($tour.get()).toBe('say-hi')
    backTourStep()
    expect($tour.get()).toBe('welcome')
  })

  it('does nothing when closed', () => {
    tourDid('message')
    advanceTour()
    expect($tour.get()).toBeNull()
  })

  it('maps /model and /help to tour events', () => {
    expect(slashTourEvent('model')).toBe('model')
    expect(slashTourEvent('help')).toBe('help')
    expect(slashTourEvent('tour')).toBeNull()
  })
})
