import { test, expect, _electron, type ElectronApplication, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { createSandbox, buildAppEnv, writeMockProviderConfig, PACKAGED_BINARY_PATH } from './fixtures'

// Labelled LOCAL RELAY NETWORK fixture. A private copy of the packaged ASAR
// substitutes only connector network dependencies. Production code, TLS rules,
// owner authentication, QR generation, renderer and gateway are not changed.
// This is native integration evidence, NOT a public deployment/physical-phone test.
const require = createRequire(import.meta.url)
const { extractAll, createPackage } = require('@electron/asar')
const { PNG } = require('pngjs')
const jsQR = require(process.env.CLOVER_QA_JSQR || 'jsqr')
const root = path.resolve(import.meta.dirname, '../../..')
const relay = path.join(root, 'services/managed-relay')
const { Miniflare } = createRequire(path.join(relay, 'package.json'))('miniflare')

test.use({ trace: 'off', screenshot: 'off', video: 'off' })
test.skip(process.platform !== 'linux', 'Linux packaged native fixture only')

test('LOCAL RELAY fixture: packaged QR authorizes real gateway and identity persists', async () => {
  test.setTimeout(240_000)
  const sandbox = createSandbox('native-pairing-ready-local-relay')
  const { build } = createRequire(path.join(relay, 'package.json'))('esbuild')
  const worker = await build({ entryPoints: [path.join(relay, 'src/worker.mjs')], bundle: true, format: 'esm', platform: 'browser', external: ['cloudflare:workers'], write: false })
  const mf = new Miniflare({ modules: true, script: worker.outputFiles[0].text,
    compatibilityDate: '2026-07-30', bindings: { ENROLLMENT_ENABLED: 'true' },
    durableObjects: { INSTALLATIONS: { className: 'Installation', useSQLite: true }, DIRECTORY: { className: 'Directory', useSQLite: true }, GUARD: { className: 'Guard', useSQLite: true } } })
  let app: ElectronApplication | undefined
  try {
    const origin = (await mf.ready).origin
    const packageDir = path.dirname(PACKAGED_BINARY_PATH)
    const copied = path.join(sandbox.root, 'packaged-local-relay-fixture')
    fs.cpSync(packageDir, copied, { recursive: true })
    const archive = path.join(copied, 'resources/app.asar')
    const extracted = path.join(sandbox.root, 'asar-source')
    extractAll(archive, extracted)
    const mainPath = path.join(extracted, 'dist/electron-main.mjs')
    const source = fs.readFileSync(mainPath, 'utf8')
    const marker = 'var desktopPhonePairing = createDesktopPairing({'
    expect(source.split(marker)).toHaveLength(2)
    fs.writeFileSync(mainPath, source.replace(marker, marker + `\n// LOCAL RELAY NETWORK FIXTURE ONLY\n  enroll: (_origin, file) => ensureEnrolled(${JSON.stringify(origin)}, file, { allowInsecureLocalhost: true }),\n  connect: options => openConnector({ ...options, relayOrigin: ${JSON.stringify(origin)}, allowInsecureLocalhost: true }),`))
    fs.rmSync(archive)
    await createPackage(extracted, archive)
    const home = path.join(sandbox.root, 'home')
    const runtime = path.join(sandbox.root, 'runtime')
    fs.mkdirSync(home, { recursive: true }); fs.mkdirSync(runtime, { recursive: true, mode: 0o700 })
    writeMockProviderConfig(sandbox.cloverHome, 'http://127.0.0.1:1')
    const env = buildAppEnv(sandbox, {
      HOME: home, XDG_CONFIG_HOME: path.join(home, '.config'), XDG_CACHE_HOME: path.join(home, '.cache'),
      XDG_DATA_HOME: path.join(home, '.local/share'), XDG_RUNTIME_DIR: runtime,
      DISPLAY: process.env.DISPLAY || ':199', XAUTHORITY: process.env.XAUTHORITY || '',
      CLOVER_DESKTOP_PYTHON: process.env.CLOVER_FIXTURE_PYTHON || 'python',
    })
    delete env.CLOVER_DESKTOP_BOOT_FAKE; delete env.CLOVER_DESKTOP_DEV_SERVER
    const binary = path.join(copied, process.platform === 'linux' ? 'Clover' : path.basename(PACKAGED_BINARY_PATH))
    async function launch(): Promise<Page> {
      app = await _electron.launch({ executablePath: binary, args: ['--disable-gpu'], env, chromiumSandbox: true, timeout: 45_000 })
      const page = await app.firstWindow()
      await expect.poll(() => {
        const log = path.join(sandbox.cloverHome, 'logs/desktop.log')
        return fs.existsSync(log) && fs.readFileSync(log, 'utf8').includes('Clover backend is ready')
      }, { timeout: 60_000 }).toBe(true)
      await page.waitForTimeout(5000)
      const skip = page.getByText('Skip for now', { exact: true }); if (await skip.isVisible()) await skip.click()
      await page.getByRole('button', { name: 'Open settings' }).click()
      await page.getByText('Gateways', { exact: true }).click()
      await expect(page.getByTestId('phone-pairing-qr')).toBeVisible({ timeout: 30_000 })
      return page
    }
    async function scanAndConnect(page: Page) {
      // Decode actual rendered pixels, not a data attribute. Never log the URI.
      const png = PNG.sync.read(await page.getByTestId('phone-pairing-qr').screenshot())
      const result = jsQR(new Uint8ClampedArray(png.data), png.width, png.height)
      expect(Boolean(result)).toBe(true)
      const url = new URL(result.data)
      expect(url.protocol).toBe('clover:')
      const token = url.searchParams.get('token') || ''
      expect(/^cdt_[A-Za-z0-9_-]+$/.test(token)).toBe(true)
      expect(url.searchParams.get('origin')).toBe('https://relay.cloverai.ca')
      const response = await fetch(origin + '/api/sessions', { headers: { authorization: `Bearer ${token}` } })
      expect(response.status).toBe(200)
      await expect(page.getByTestId('phone-connected')).toBeVisible({ timeout: 15_000 })
      return token
    }
    let page = await launch()
    const token = await scanAndConnect(page)
    const identityDir = path.join(sandbox.userDataDir, 'mobile-relay')
    const files = fs.readdirSync(identityDir).filter(n => n.endsWith('.json'))
    expect(files).toHaveLength(1)
    const identity = fs.readFileSync(path.join(identityDir, files[0]), 'utf8')
    await app!.close(); app = undefined
    fs.rmSync(path.join(sandbox.cloverHome, 'logs/desktop.log'), { force: true })
    page = await launch()
    expect(fs.readdirSync(identityDir).filter(n => n.endsWith('.json'))).toEqual(files)
    // Compare booleans so failed assertions never print credentials.
    expect(fs.readFileSync(path.join(identityDir, files[0]), 'utf8') === identity).toBe(true)
    expect((await fetch(origin + '/api/sessions', { headers: { authorization: `Bearer ${token}` } })).status).toBe(200)
    await scanAndConnect(page)
    console.log('PASS LOCAL RELAY: native QR pixels decoded; registered device reaches real gateway; connected UI; same relay identity and prior phone authorization after relaunch. Production network was not tested.')
  } finally {
    await app?.close().catch(() => {})
    await mf.dispose()
    sandbox.cleanup()
  }
})
