import { cleanup, render, screen, waitFor } from '@testing-library/react'
import QRCode from 'qrcode'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.fn()

vi.mock('@/api/client', () => ({ cloverApi: (req: unknown) => api(req) }))

import { PhonePairingQr, PhonePairingSection } from './phone-pairing-section'

const FAKE_URI = 'clover://pair?origin=https%3A%2F%2Fgateway.example.com&token=fake-token-123456'

beforeEach(() => api.mockReset())
afterEach(cleanup)

describe('PhonePairingQr', () => {
  it('renders a QR that encodes exactly the pairing link', async () => {
    render(<PhonePairingQr uri={FAKE_URI} />)
    const expected = await QRCode.toString(FAKE_URI, { errorCorrectionLevel: 'M', margin: 2, type: 'svg', width: 240 })

    const modules = (svg: string) => [...svg.matchAll(/ d="([^"]+)"/g)].map(m => m[1]).join('|')

    await waitFor(() => expect(modules(screen.getByTestId('phone-pairing-qr').innerHTML)).toBe(modules(expected)))
  })
})

describe('PhonePairingSection', () => {
  it('shows the QR, hides the key until asked', async () => {
    api.mockResolvedValue({ configured: true, origin: 'https://gateway.example.com', token: 'fake-token-123456' })
    render(<PhonePairingSection />)
    expect(await screen.findByTestId('phone-pairing-qr')).toBeTruthy()
    expect(screen.queryByText('fake-token-123456')).toBeNull()
    screen.getByText('Show key').click()
    expect(await screen.findByText('fake-token-123456')).toBeTruthy()
  })

  it('explains the not-set-up state without a QR', async () => {
    api.mockResolvedValue({ configured: false, origin: null, token: null, reason: 'no_public_https_url' })
    render(<PhonePairingSection />)
    expect(await screen.findByText(/secure https address/)).toBeTruthy()
    expect(screen.queryByTestId('phone-pairing-qr')).toBeNull()
  })
})
