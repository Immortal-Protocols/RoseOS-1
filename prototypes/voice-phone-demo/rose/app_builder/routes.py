"""Builder API v1 routes on the remote TLS listener (docs/APP-BUILDER.md §3).

Mounted only when the server has an app builder configured; until a host owner enables one, the listener serves
exactly what it did before. Every route is a signed rose-auth-v1 request from a paired device, checked the same way
as ``/hello``. Request bodies are small JSON objects with integrity from the pinned TLS link; the builder validates
them strictly. Responses carry ``schemaVersion: 1`` and may gain fields within that version.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .service import Builder, Refused

BODY_MAX = 16 * 1024
PREFIX = '/builder/v1'


def add_routes(app: FastAPI, remote: Any, builder: Builder, peer: Callable[[Any], str | None],
               refusal_headers: Callable[[Any], dict], request_path: Callable[[dict], str]) -> None:
    headers = {'Cache-Control': 'no-store'}

    def reply(payload: Any, status: int = 200) -> JSONResponse:
        if isinstance(payload, dict):
            payload = {'schemaVersion': 1, **payload}
        return JSONResponse(payload, status, headers=headers)

    def error(message: str, status: int) -> JSONResponse:
        return reply({'error': message}, status)

    def signed(request: Request):
        auth = remote.authenticate(request.method, request_path(request.scope), request.headers, peer(request))
        if auth.device is None:
            return None, JSONResponse({'schemaVersion': 1, 'error': auth.reason, 'final': auth.final}, 401,
                                      headers=refusal_headers(auth))
        return auth.device, None

    async def body(request: Request):
        length = request.headers.get('content-length')
        if length is not None and (not (length.isascii() and length.isdigit()) or int(length) > BODY_MAX):
            raise Refused('body too large')
        raw = b''
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > BODY_MAX:
                raise Refused('body too large')
        try:
            return json.loads(raw)
        except (ValueError, RecursionError):
            raise Refused('body must be JSON') from None

    async def call(request: Request, fn, *args, with_body: bool = True):
        device, denied = signed(request)
        if denied:
            return denied
        try:
            data = await body(request) if with_body else None
            result = await asyncio.to_thread(fn, device.id, *args, *([data] if with_body else []))
        except Refused as refused:
            return reply({'error': str(refused), **refused.details}, 400)
        if result is None:
            return error('not found', 404)
        return reply(result)

    @app.post(PREFIX + '/projects')
    async def create(request: Request):
        return await call(request, builder.create)

    @app.get(PREFIX + '/projects')
    async def projects(request: Request):
        return await call(request, lambda device: {'projects': builder.projects()}, with_body=False)

    @app.post(PREFIX + '/projects/{project_id}/revisions')
    async def revise(project_id: str, request: Request):
        return await call(request, builder.revise, project_id)

    @app.post(PREFIX + '/jobs/{job_id}/cancel')
    async def cancel(job_id: str, request: Request):
        return await call(request, lambda device, data: builder.cancel(job_id, data))

    @app.post(PREFIX + '/jobs/{job_id}/retry')
    async def retry(job_id: str, request: Request):
        return await call(request, lambda device, data: builder.retry(job_id, data))

    @app.get(PREFIX + '/jobs/{job_id}')
    async def job(job_id: str, request: Request):
        return await call(request, lambda device: builder.job(job_id), with_body=False)

    @app.post(PREFIX + '/installs')
    async def install(request: Request):
        return await call(request, builder.install)

    @app.get(PREFIX + '/artifacts/{sha256}')
    async def artifact(sha256: str, request: Request):
        device, denied = signed(request)
        if denied:
            return denied
        path = builder.artifact(sha256)
        if path is None:
            return error('not found', 404)
        return FileResponse(path, media_type='application/vnd.android.package-archive',
                            headers={**headers, 'Content-Length': str(path.stat().st_size)})
