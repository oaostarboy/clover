#!/usr/bin/env python3
"""Disposable Darwin-only test of the API server's exclusive bind lifecycle."""
import asyncio
import json
import platform
import socket
import sys

from aiohttp import web


async def probe():
    if sys.platform != "darwin":
        raise RuntimeError("Refusing native socket proof on a non-Darwin host")
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, reuse_address=False)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    competing_runner = web.AppRunner(app)
    await competing_runner.setup()
    exclusive = False
    try:
        competing_site = web.TCPSite(competing_runner, "127.0.0.1", port, reuse_address=False)
        try:
            await competing_site.start()
        except OSError as error:
            if error.errno != 48:
                raise
            exclusive = True
        if not exclusive:
            raise AssertionError("second listener unexpectedly acquired the live port")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await reader.read()
        assert b"200 OK" in response
        writer.close()
        await writer.wait_closed()
        await runner.cleanup()
        restart = web.TCPSite(competing_runner, "127.0.0.1", port, reuse_address=False)
        try:
            await restart.start()
            result = "bound"
        except OSError as error:
            result = f"errno={error.errno}: {error.strerror}"
        print(json.dumps({"platform": platform.platform(), "port": port,
                          "exclusive_live_listener": exclusive,
                          "request_completed": True,
                          "immediate_restart": result}, sort_keys=True))
    finally:
        await runner.cleanup()
        await competing_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(probe())
