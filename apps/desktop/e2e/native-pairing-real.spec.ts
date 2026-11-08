import { test, expect, _electron, type ElectronApplication, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { createSandbox, buildAppEnv, writeMockProviderConfig, PACKAGED_BINARY_PATH } from './fixtures'

test.use({ trace: 'off', screenshot: 'off', video: 'off' })
test.skip(process.platform !== 'linux', 'Linux packaged native fixture only')

test('packaged real gateway: relay offline retry and gateway identity after relaunch', async () => {
  test.setTimeout(180_000)
  const sandbox = createSandbox('native-pairing-real')
  const home = path.join(sandbox.root, 'home')
  const runtime = path.join(sandbox.root, 'runtime')
  fs.mkdirSync(home, { recursive: true })
  fs.mkdirSync(runtime, { recursive: true, mode: 0o700 })
  writeMockProviderConfig(sandbox.cloverHome, 'http://127.0.0.1:1')
  const env = buildAppEnv(sandbox, {
    HOME: home, XDG_CONFIG_HOME: path.join(home, '.config'),
    XDG_CACHE_HOME: path.join(home, '.cache'), XDG_DATA_HOME: path.join(home, '.local/share'),
    XDG_RUNTIME_DIR: runtime, DISPLAY: process.env.DISPLAY || ':199',
    XAUTHORITY: process.env.XAUTHORITY || '',
    CLOVER_DESKTOP_PYTHON: process.env.CLOVER_FIXTURE_PYTHON || 'python',
  })
  delete env.CLOVER_DESKTOP_BOOT_FAKE
  delete env.CLOVER_DESKTOP_DEV_SERVER
  const binary = process.platform === 'linux' ? path.join(path.dirname(PACKAGED_BINARY_PATH), 'Clover') : PACKAGED_BINARY_PATH
  let app: ElectronApplication | undefined
  async function launch(): Promise<Page> {
    app = await _electron.launch({ executablePath: binary, args: ['--disable-gpu'], env, chromiumSandbox: true, timeout: 45_000 })
    const page = await app.firstWindow()
    await expect.poll(() => {
      const log = path.join(sandbox.cloverHome, 'logs', 'desktop.log')
      return fs.existsSync(log) && fs.readFileSync(log, 'utf8').includes('Clover backend is ready')
    }, { timeout: 60_000 }).toBe(true)
    await page.waitForTimeout(5000)
    const skip = page.getByText('Skip for now', { exact: true })
    if (await skip.isVisible()) await skip.click()
    return page
  }
  async function gatewayId(page: Page): Promise<string> {
    const result = await page.evaluate(async () => await (window as unknown as { cloverDesktop: { api: (request: { path: string }) => Promise<{ gatewayId: string }> } }).cloverDesktop.api({ path: '/api/mobile/relay/devices' }))
    expect(result.gatewayId).toMatch(/^[0-9a-f]{64}$/)
    return result.gatewayId
  }
  try {
    let page = await launch()
    const firstId = await gatewayId(page)
    await page.getByRole('button', { name: 'Open settings' }).click()
    await page.getByText('Gateways', { exact: true }).click()
    await expect(page.getByText('Could not load pairing details.')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('phone-pairing-qr')).toHaveCount(0)
    await expect(page.getByLabel("Your gateway's https address")).toHaveCount(0)
    const retry = page.getByRole('button', { name: 'Try again' })
    await retry.click()
    await expect(retry).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('phone-pairing-qr')).toHaveCount(0)
    await app!.close()
    app = undefined
    // Remove the old readiness log so a stale prior boot cannot satisfy readiness.
    fs.rmSync(path.join(sandbox.cloverHome, 'logs', 'desktop.log'), { force: true })
    page = await launch()
    expect(await gatewayId(page)).toBe(firstId)
    console.log('Verified: real backend online; offline UI and retry show no QR/address form; gateway identity stable after desktop relaunch.')
  } finally {
    await app?.close().catch(() => {})
    sandbox.cleanup()
  }
})
