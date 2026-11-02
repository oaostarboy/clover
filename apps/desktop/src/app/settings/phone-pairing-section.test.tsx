import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import QRCode from 'qrcode'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.fn()
const confirmMock = vi.fn()

vi.mock('@/api/client', () => ({ cloverApi: (req: unknown) => api(req) }))
vi.mock('@/store/confirm', () => ({ confirm: (req: unknown) => confirmMock(req) }))
vi.mock('@/store/notifications', () => ({ notifyError: vi.fn() }))

import { PhonePairingQr, PhonePairingSection } from './phone-pairing-section'

const URI = 'clover://pair?origin=https%3A%2F%2Fgateway.example.com&token=cdt_fake-token-123456'
const CONFIGURED = { configured: true, origin: 'https://gateway.example.com', reason: null }
const NOW = Date.now() / 1000

const phone = (over: Record<string, unknown> = {}) => ({
  connected: true,
  created_at: NOW - 5000,
  id: 'dev1',
  last_seen_at: NOW - 125,
  name: 'iPhone',
  ...over
})

function backend(devices: () => unknown[]) {
  api.mockImplementation(async (req: { method?: string; path: string }) => {
    if (req.path === '/api/mobile/pairing' && req.method === 'POST') {
      return {
        ...CONFIGURED,
        device: phone({ connected: false, id: 'pending', last_seen_at: null }),
        expires_in: 600,
        uri: URI
      }
    }

    if (req.path === '/api/mobile/pairing') {
      return CONFIGURED
    }

    if (req.path === '/api/mobile/devices') {
      return { devices: devices() }
    }

    return { ok: true }
  })
}

beforeEach(() => {
  api.mockReset()
  confirmMock.mockReset()
})
afterEach(cleanup)

describe('PhonePairingQr', () => {
  it('encodes exactly the pairing link, at level H, with the Clover mark in the centre', () => {
    const { container } = render(<PhonePairingQr uri={URI} />)
    const qr = QRCode.create(URI, { errorCorrectionLevel: 'H' })
    const path = container.querySelector('path')!.getAttribute('d')!
    const dark = (path.match(/M/g) ?? []).length

    // Centre is knocked out for the logo, so fewer modules than the full code, but never more.
    let full = 0

    for (let y = 0; y < qr.modules.size; y++) {
      for (let x = 0; x < qr.modules.size; x++) {
        full += qr.modules.get(x, y) ? 1 : 0
      }
    }

    expect(dark).toBeLessThan(full)
    expect(dark).toBeGreaterThan(full * 0.8)
    expect(container.querySelector('image')?.getAttribute('href')).toContain('clover-mark.png')
  })
})

describe('PhonePairingSection', () => {
  it('mints a fresh per-phone code and never asks for or shows a key', async () => {
    backend(() => [])
    render(<PhonePairingSection />)
    expect(await screen.findByTestId('phone-pairing-qr')).toBeTruthy()
    expect(api).toHaveBeenCalledWith({ method: 'POST', path: '/api/mobile/pairing' })
    expect(screen.queryByText(/show key/i)).toBeNull()
    expect(screen.getByText(/Expires in 10:00/)).toBeTruthy()
    expect(screen.getByText(/No phones connected yet/)).toBeTruthy()
  })

  it('swaps the QR for "iPhone connected" once the shown code is used', async () => {
    let devices: unknown[] = []

    backend(() => devices)
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      render(<PhonePairingSection />)
      await screen.findByTestId('phone-pairing-qr')
      devices = [phone({ id: 'pending' })]
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100)
      })
      await waitFor(() => expect(screen.getByTestId('phone-connected')).toBeTruthy())
      expect(screen.queryByTestId('phone-pairing-qr')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('lists paired phones and removes one only after confirmation', async () => {
    backend(() => [phone()])
    render(<PhonePairingSection />)
    await screen.findByText(/Last active/)

    confirmMock.mockResolvedValueOnce(false)
    screen.getByText('Remove').click()
    await waitFor(() => expect(confirmMock).toHaveBeenCalled())
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ method: 'DELETE' }))

    confirmMock.mockResolvedValueOnce(true)
    screen.getByText('Remove').click()
    await waitFor(() => expect(api).toHaveBeenCalledWith({ method: 'DELETE', path: '/api/mobile/devices/dev1' }))
    await waitFor(() => expect(screen.queryByText('Remove')).toBeNull())
  })

  it('explains the not-set-up state with the fix command and mints nothing', async () => {
    api.mockResolvedValue({ configured: false, origin: null, reason: 'no_public_https_url' })
    render(<PhonePairingSection />)
    expect(await screen.findByText(/clover config set mobile.public_url/)).toBeTruthy()
    expect(screen.queryByTestId('phone-pairing-qr')).toBeNull()
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ method: 'POST' }))
  })
})
