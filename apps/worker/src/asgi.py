"""Tiny ASGI app so the worker image can serve /health behind uvicorn."""

from __future__ import annotations

import json
import threading

from src.main import consume_forever, redis_reachable

_stop = threading.Event()
_thread: threading.Thread | None = None


def _body() -> bytes:
    redis_ok = redis_reachable()
    payload = {
        "status": "ok" if redis_ok else "degraded",
        "service": "sybeez-worker",
        "dependencies": {"redis": {"ok": redis_ok}},
    }
    return json.dumps(payload).encode()


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        global _thread
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                _stop.clear()
                _thread = threading.Thread(target=consume_forever, args=(_stop,), daemon=True)
                _thread.start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                _stop.set()
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    if scope["type"] != "http":
        return

    path = scope.get("path") or "/"
    if path != "/health":
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        return

    # Redis being down must not fail the process health check.
    try:
        body = _body()
    except Exception:
        body = b'{"status":"degraded","service":"sybeez-worker","dependencies":{"redis":{"ok":false}}}'
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
