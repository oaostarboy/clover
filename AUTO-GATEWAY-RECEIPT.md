# Automatic gateway acceptance receipt

This candidate is not a release. Public relay, native Windows/macOS and physical iPhone acceptance remain separate gates.

## Local verification

- Linux packaged native offline test passed: real app-spawned gateway, offline error and retry with no QR, stable gateway ID after relaunch.
- Linux packaged native ready test passed: a private copy of the packaged ASAR replaces only connector network dependencies to point at an isolated local Miniflare Worker. It decodes actual QR pixels, authorizes the real gateway, checks connected UI, relaunches with the same home, verifies identical relay identity and confirms the previous phone token still works. This fixture explicitly permits insecure localhost only within the private copy; production code and TLS policy are unchanged. It does not prove public TLS or a physical phone.
- Real gateway/Worker/controller/iOS adapter fixture passed against a disposable local home; REST and ticketed WebSocket operations, restrictions and revocation were exercised.
- Previously recorded suites: controller 15 passed, Python 79 passed, relay 85 passed, desktop 1,941 passed and 6 skipped; desktop build and typecheck passed before the new native fixture.

## Running the Linux native fixtures

Build and package the desktop app and install root and managed-relay dependencies first. Supply a working graphical display and the Python runtime used by the packaged desktop gateway:

```sh
DISPLAY=:99 CLOVER_FIXTURE_PYTHON=/path/to/python npx playwright test e2e/native-pairing-real.spec.ts e2e/native-pairing-ready.spec.ts --workers=1
```

Run from `apps/desktop`; set `XAUTHORITY` when required by the display. The ready test uses the desktop dev dependencies `jsqr` and `pngjs`. Traces, automatic screenshots and video are disabled to avoid retaining pairing credentials. The uppercase Linux packaged binary name is selected explicitly.

## Outstanding

Public deployment and public TLS/HTTP/WebSocket acceptance, cross-platform native testing, physical iPhone acceptance, and installed-update proof have not been performed. The private parent review is excluded from the candidate.
