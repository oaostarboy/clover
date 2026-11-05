"""Owner-bound, reversible admission drain for the Darwin updater.

This barrier does not stop a process or mutate a Node workspace. Its caller
must additionally inhibit the owned supervisor and wait for owned builders.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid


class LifecycleRefused(RuntimeError):
    """Ownership or quiescence could not be established without data risk."""


def _birth(pid):
    from gateway.status import _get_process_start_time
    return _get_process_start_time(pid)


def _read_status(home):
    return json.loads((home / 'gateway_state.json').read_text(encoding='utf-8'))


def _drained_status(home, pid, birth, requested):
    if birth is None or _birth(pid) != birth:
        raise LifecycleRefused('Gateway identity changed while requesting drain')
    try:
        state = _read_status(home)
        owned = (
            state.get('kind') == 'clover-gateway'
            and state.get('pid') == pid
            and state.get('start_time') == birth
            and isinstance(state.get('clover_home'), str)
            and bool(state['clover_home'])
            and Path(state['clover_home']).resolve() == home
        )
        if not owned:
            raise LifecycleRefused('Gateway status does not belong to the expected process')
        from clover_cli.update_delivery_guard import CAPABILITY
        if state.get('update_restart_capability') != CAPABILITY:
            raise LifecycleRefused(
                'Gateway delivery-through-ack drain is not established; '
                'leave the running service untouched and finish its replies before maintenance')
        updated = datetime.fromisoformat(state['updated_at'])
        if updated.tzinfo is None:
            return False
        count = state.get('active_agents')
        return (updated >= requested and state.get('gateway_state') == 'draining'
                and type(count) is int and count == 0)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LifecycleRefused('Gateway drain status is unavailable') from exc


@contextmanager
def quiescent_services(services, operations, *, timeout=120.0):
    """Own drain -> unload -> builder completion -> mutation -> restoration.

    Platform operations must verify installation, process birth and service
    identity, and restore only the snapshotted launchd registration. Recording
    an attempted unload before executing it covers partially successful calls.
    """
    services = list(services)
    attempted = []
    drains = ExitStack()
    try:
        for service in services:
            operations.validate(service)
        for service in services:
            if service.pid is not None:
                drains.enter_context(owned_external_drain(
                    service.home, service.pid, service.birth, timeout=timeout))
        children = operations.capture_builders(services)
        for service in services:
            operations.validate(service)
            attempted.append(service)
            operations.unload(service)
        operations.wait_quiescent(services, children, timeout)
        yield
    finally:
        failures = []
        try:
            drains.close()
        except Exception as exc:
            failures.append(exc)
        for service in reversed(attempted):
            try:
                operations.restore(service)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise LifecycleRefused('Updater lifecycle restoration failed: ' +
                                   '; '.join(str(exc) for exc in failures)) from failures[0]


@contextmanager
def owned_external_drain(home, pid, birth, *, timeout=120.0):
    """Wait for a fresh owner-bound zero-work drain and preserve it until exit.

    An existing drain belongs to another operation: refuse without replacing
    it. On failure or normal exit remove only this operation's own marker.
    No signal is sent, even when the process identity changes or time runs out.
    """
    from gateway.drain_control import current_instantiation_epoch, drain_request_path
    home = Path(home).resolve()
    if type(pid) is not int or pid <= 0 or birth is None or _birth(pid) != birth:
        raise LifecycleRefused('Gateway process identity is not established')
    path = drain_request_path(home)
    requested = datetime.now(timezone.utc)
    payload = {
        'action': 'drain', 'requested_at': requested.isoformat(),
        'principal': 'macos-update:' + uuid.uuid4().hex,
        'epoch': current_instantiation_epoch(), 'suppress_notification': True,
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LifecycleRefused('Another operation already owns the gateway drain') from exc
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + timeout
        while not _drained_status(home, pid, birth, requested):
            if time.monotonic() >= deadline:
                raise LifecycleRefused('Gateway work did not drain before the deadline')
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        if _birth(pid) != birth:
            raise LifecycleRefused('Gateway identity changed at the drain boundary')
        yield
    finally:
        try:
            if json.loads(path.read_text(encoding='utf-8')) == payload:
                path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise LifecycleRefused('Could not safely release updater-owned drain') from exc
