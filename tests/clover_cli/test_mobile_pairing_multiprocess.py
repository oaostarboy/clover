"""Concurrent processes must not lose paired-device updates."""
import multiprocessing


def _create_device(path, barrier, output):
    from clover_cli.mobile_pairing import DeviceStore
    store = DeviceStore(path)
    barrier.wait(timeout=20)
    device = store.create('concurrent phone')
    output.put((device['id'], device['token']))


def test_device_store_concurrent_processes_preserve_every_device(tmp_path, monkeypatch):
    from clover_cli.mobile_pairing import DeviceStore

    monkeypatch.setenv('CLOVER_HOME', str(tmp_path / 'home'))
    path = tmp_path / 'devices.json'
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(4)
    output = context.Queue()
    processes = [context.Process(target=_create_device, args=(path, barrier, output)) for _ in range(4)]
    for process in processes:
        process.start()
    devices = [output.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    store = DeviceStore(path)
    assert {d['id'] for d in store.list()} == {device_id for device_id, _ in devices}
    for device_id, token in devices:
        principal = store.authenticate(token)
        assert principal is not None
        assert principal.device_id == device_id
    assert store.revoke(devices[0][0])
    assert store.authenticate(devices[0][1]) is None
    assert all(store.authenticate(token) is not None for _, token in devices[1:])
