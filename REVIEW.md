# Review brief: detached-continue on WS disconnect

Bug: `_schedule_ws_orphan_reap` (tui_gateway/server.py, mid-turn branch, was ~L1408) interrupted a running turn
~20s (`dashboard.ws_orphan_reap_grace_s`) after the socket dropped, then reaped. `turn_isolation` is NOT the
cause/fix: it only picks compute-host vs in-process interrupt; both paths were interrupted.

Change: `dashboard.ws_detached_ttl_s` (default 1800; 0 = legacy). While detached+running: keep polling, never
interrupt. After turn ends: reap only when idle >= TTL (`_detached_idle_since`). Reconnect (`_live_session_payload`
live transport) cancels the reap and clears the idle clock. `event_replay` pins the latest `message.complete`
outside the 512-event ring so the final reply survives delta floods. Explicit `session.interrupt` untouched.

| Risk | Where | Test (tests/tui_gateway/test_detached_turn_continue.py) |
|---|---|---|
| Memory | ring cap 512/session, 64 sessions; +1 pinned final per session, dropped with session eviction | test_buffer_cap_holds_and_final_result_survives_eviction |
| Duplicate delivery | replay is seq-watermarked; pinned final only re-added if seq > last_seen and not already in ring | test_reconnect_within_ttl_gets_final_reply_exactly_once |
| Orphaned agents | timer chain re-arms every grace while running; reaps after TTL; stuck turns no longer force-reaped (was 60 polls) | test_disconnect_mid_turn_does_not_interrupt, test_turn_completes_then_reaps_only_after_ttl |
| Auth on resume | NOT changed; resume still goes through existing session.resume / session.events.since. Reviewer: confirm events.since checks the caller owns sid (it takes any sid string — pre-existing) | none (gap) |
| Explicit stop | session.interrupt path unchanged | test_explicit_interrupt_still_interrupts_immediately |
| Legacy | ttl=0 | test_ttl_zero_keeps_legacy_interrupt |

Known caveats: a genuinely hung turn now lives until it ends on its own (no 60s force-reap) — consider a max
turn-wall-clock. Replay ring is in-memory only: gateway restart loses the buffer (DB transcript still holds
persisted history). Existing orphan-reap tests in tests/test_tui_gateway_server.py pin TTL=0 via an autouse fixture.
Pre-existing failures on baseline (5): test_gui_surface_toolsets, 2x test_projects_rpc, 2x test_tui_gateway_server (state_db/custom_row).
