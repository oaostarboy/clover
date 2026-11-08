# Managed relay — candidate, not deployed

Authenticated outbound desktop connector and Cloudflare Worker relay for mobile HTTP and WebSocket traffic. This branch does not activate a public service, integrate automatic desktop provisioning, or publish an application release.

## Local checks

Run in this directory with a current Node.js runtime:

```
npm ci
npm test
npm run build
npm audit
```

The source candidate passed 75 relay tests and a separate five-test network fixture using the companion phone adapter. Coverage includes an exact-hash 8 MiB request/response, partial-response cancellation and recovery, shared admission under overload, delayed ticket claims, connection replacement, receipt stalls, and no request replay in the tested reconnect interleavings.

## Boundaries

- HTTP body limit: 8 MiB. One potentially large HTTP exchange per installation at a time.
- HTTP and framed transports share a 96 MiB weighted admission ledger. The measured large-transfer high-water mark was 96,469,863 bytes. Weighted accounting is not an actual V8 heap measurement or formal whole-isolate memory guarantee.
- A cancellation that never settles deliberately retains admission rather than freeing potentially owned allocations. Availability under indefinitely unsettled cancellation remains a review item.
- Protocols: connector `clover-relay-connector-v2`, phone `clover-relay-flow-v1`. Legacy transport and shortened deadlines are test-only options, not production configuration.
- Tests use isolated loopback backends. Native iPhone execution, actual Clover gateway authorization, automatic desktop setup/reuse, and production DNS/TLS/HTTP/WebSocket deployment remain separate acceptance gates.
- Enrollment/production resource configuration is not supplied here. Do not deploy by guessing bindings or enabling test bypasses.
