import type { PanelSection } from '../types.js'

export const SETUP_REQUIRED_TITLE = 'Setup Required'

export const buildSetupRequiredSections = (): PanelSection[] => [
  {
    text: 'No luck yet? Connect an AI service to start chatting.'
  },
  {
    rows: [
      ['/model', 'pick your lucky charm (an AI service) and sign in'],
      ['/setup', 'quick setup (about a minute)'],
      ['Ctrl+C', 'quit, then run `clover setup`']
    ],
    title: 'Actions'
  }
]
