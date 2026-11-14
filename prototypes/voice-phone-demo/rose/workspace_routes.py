"""``POST /workspace/v1/tasks`` and ``GET /workspace/v1/tasks/<taskId>`` on the remote TLS listener
(docs/APP-BUILDER.md §4.3).

Mounted only when the server has workspace tasks enabled; a signed rose-auth-v1 request from a paired device,
checked like ``/hello``. Bodies are small JSON objects (integrity from the pinned TLS link).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .workspace_tasks import WorkspaceTasks

BODY_MAX = 4 * 1024


def add_routes(app: FastAPI, remote: Any, tasks: WorkspaceTasks, peer: Callable[[Any], str | None],
               refusal_headers: Callable[[Any], dict], request_path: Callable[[dict], str]) -> None:
    headers = {'Cache-Control': 'no-store'}

    @app.post('/workspace/v1/tasks')
    async def start(request: Request):
        auth = remote.authenticate('POST', request_path(request.scope), request.headers, peer(request))
        if auth.device is None:
            return JSONResponse({'schemaVersion': 1, 'error': auth.reason, 'final': auth.final}, 401,
                                headers=refusal_headers(auth))
        length = request.headers.get('content-length')
        refused = {'schemaVersion': 1, 'status': 'refused'}
        if length is not None and (not (length.isascii() and length.isdigit()) or int(length) > BODY_MAX):
            return JSONResponse({**refused, 'reason': 'body too large'}, 400, headers=headers)
        raw = b''
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > BODY_MAX:
                return JSONResponse({**refused, 'reason': 'body too large'}, 400, headers=headers)
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            return JSONResponse({**refused, 'reason': 'body must be JSON'}, 400, headers=headers)
        reply = await asyncio.to_thread(tasks.submit, auth.device.id, body)
        status = 400 if reply['status'] == 'refused' else 503 if reply['status'] == 'unavailable' else 200
        return JSONResponse(reply, status, headers=headers)

    @app.get('/workspace/v1/tasks/{task_id}')
    async def status(task_id: str, request: Request):
        """A page task's authoritative state (docs/APP-BUILDER.md §4.3): 200 with the state, 404 when unknown."""
        auth = remote.authenticate('GET', request_path(request.scope), request.headers, peer(request))
        if auth.device is None:
            return JSONResponse({'schemaVersion': 1, 'error': auth.reason, 'final': auth.final}, 401,
                                headers=refusal_headers(auth))
        task = await asyncio.to_thread(tasks.get, task_id)
        if task is None:
            return JSONResponse({'schemaVersion': 1, 'status': 'unknown'}, 404, headers=headers)
        return JSONResponse(task, headers=headers)
