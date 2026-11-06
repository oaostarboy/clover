import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
    expect(screen.getByText(/newly paired phones can use dictation and speech playback/i)).toBeTruthy()
    expect(screen.getByText(/pair existing phones again to enable audio/i)).toBeTruthy()
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

  it('has no repeated step copy and no shell command', async () => {
    backend(() => [])
    const { container } = render(<PhonePairingSection />)
    await screen.findByTestId('phone-pairing-qr')
    const text = container.textContent ?? ''
    expect(text.match(/Open Clover/g)?.length).toBe(1)
    expect(text).not.toMatch(/clover config/)
  })

  it('not-set-up: shows an inline https field, no command, and mints nothing', async () => {
    api.mockResolvedValue({ configured: false, origin: null, reason: 'no_public_https_url' })
    const { container } = render(<PhonePairingSection />)
    expect(await screen.findByLabelText("Your gateway's https address")).toBeTruthy()
    expect(container.textContent).not.toMatch(/clover config|mobile\.public_url/)
    expect(screen.queryByTestId('phone-pairing-qr')).toBeNull()
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ method: 'POST' }))
  })

  it('rejects http and saves https through PUT /api/config, then reloads', async () => {
    let saved = false
    api.mockImplementation(async (req: { method?: string; path: string }) => {
      if (req.method === 'PUT') {
        saved = true

        return { ok: true }
      }

      if (req.path === '/api/mobile/pairing' && !req.method) {
        return saved ? CONFIGURED : { configured: false, origin: null, reason: 'no_public_https_url' }
      }

      if (req.path === '/api/mobile/pairing') {
        return {
          ...CONFIGURED,
          device: phone({ connected: false, id: 'p', last_seen_at: null }),
          expires_in: 600,
          uri: URI
        }
      }

      return { devices: [] }
    })
    render(<PhonePairingSection />)
    const input = await screen.findByLabelText("Your gateway's https address")

    fireEvent.change(input, { target: { value: 'http://192.168.1.5:8642' } })
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ method: 'PUT' }))

    fireEvent.change(input, { target: { value: 'https://gateway.example.com/' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith({
        body: { config: { mobile: { public_url: 'https://gateway.example.com' } } },
        method: 'PUT',
        path: '/api/config'
      })
    )
    expect(await screen.findByTestId('phone-pairing-qr')).toBeTruthy()
  })
})
