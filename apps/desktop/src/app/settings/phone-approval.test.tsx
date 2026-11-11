import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.fn()
const notify = vi.fn()

vi.mock('@/api/client', () => ({ cloverApi: (req: unknown) => api(req) }))
vi.mock('@/store/notifications', () => ({ notifyError: vi.fn() }))

import { PhoneApprovalHost } from './phone-approval'

const pendingDevice = (over: Record<string, unknown> = {}) => ({
  capabilities: [],
  connected: true,
  created_at: 1,
  id: 'dev1',
  last_seen_at: 2,
  name: 'Ant’s iPhone',
  pending_request: { capabilities: ['crew_read', 'control_drive', 'control_view'], created_at: 3, match_code: '4821', request_id: 'r1' },
  status: 'pending',
  ...over
})

beforeEach(() => {
  api.mockReset()
  notify.mockReset()
  ;(window as unknown as { cloverDesktop: unknown }).cloverDesktop = { notify }
})
afterEach(cleanup)

describe('PhoneApprovalHost', () => {
  it('raises one native popup and shows the sheet with the code, five switches, Approve and Deny', async () => {
    api.mockResolvedValue({ devices: [pendingDevice()] })
    render(<PhoneApprovalHost />)

    expect(await screen.findByText('Approve this iPhone?', { selector: '[data-slot="dialog-title"]' })).toBeTruthy()
    expect(screen.getByTestId('match-code').textContent).toBe('4821')

    for (const label of ['Chat', 'Crew view', 'Crew chat', 'Control view', 'Control drive (1 h)']) {
      expect(screen.getByRole('switch', { name: label })).toBeTruthy()
    }

    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy()
    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1))
    expect(JSON.stringify(notify.mock.calls[0])).toContain('4821')
  })

  it('starts the powerful rights off even when the phone asked for them', async () => {
    api.mockResolvedValue({ devices: [pendingDevice()] })
    render(<PhoneApprovalHost />)
    await screen.findByTestId('match-code')

    expect(screen.getByRole('switch', { name: 'Crew view' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('switch', { name: 'Control view' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('switch', { name: 'Crew chat' }).getAttribute('aria-checked')).toBe('false')
    expect(screen.getByRole('switch', { name: 'Control drive (1 h)' }).getAttribute('aria-checked')).toBe('false')
  })

  it('Approve posts the chosen rights with the request id', async () => {
    api.mockImplementation(async (req: { method?: string }) => (req.method === 'POST' ? { device: {} } : { devices: [pendingDevice()] }))
    render(<PhoneApprovalHost />)
    await screen.findByTestId('match-code')
    fireEvent.click(screen.getByRole('switch', { name: 'Crew view' })) // off
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    })

    const post = api.mock.calls.map(c => c[0]).find((r: { method?: string }) => r.method === 'POST')

    expect(post.path).toBe('/api/mobile/devices/dev1/approve')
    expect(post.body.request_id).toBe('r1')
    expect(post.body.capabilities).toContain('chat')
    expect(post.body.capabilities).toContain('control_view')
    expect(post.body.capabilities).not.toContain('crew_read')
    expect(post.body.capabilities).not.toContain('control_drive')
  })

  it('trust is off by default and is sent when switched on', async () => {
    api.mockImplementation(async (req: { method?: string }) => (req.method === 'POST' ? { device: {} } : { devices: [pendingDevice()] }))
    render(<PhoneApprovalHost />)
    await screen.findByTestId('match-code')

    const toggle = screen.getByRole('switch', { name: 'Trust this phone' })

    expect(toggle.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(toggle)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    })

    const post = api.mock.calls.map(c => c[0]).find((r: { method?: string }) => r.method === 'POST')

    expect(post.body.trust).toBe(true)
  })

  it('Deny posts deny', async () => {
    api.mockImplementation(async (req: { method?: string }) => (req.method === 'POST' ? { device: {} } : { devices: [pendingDevice()] }))
    render(<PhoneApprovalHost />)
    await screen.findByTestId('match-code')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    })
    expect(api.mock.calls.map(c => c[0].path)).toContain('/api/mobile/devices/dev1/deny')
  })

  it('shows nothing when no phone is asking', async () => {
    api.mockResolvedValue({ devices: [pendingDevice({ pending_request: undefined })] })
    render(<PhoneApprovalHost />)
    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(screen.queryByText('Approve this iPhone?')).toBeNull()
    expect(notify).not.toHaveBeenCalled()
  })
})
