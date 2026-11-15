"""Paired phone snapshot -> Rose server local model -> existing rose-layout-v1 result (#818 envelope)."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
import hashlib
import json
import threading
import time
from fastapi.responses import JSONResponse
from fastapi import Request
from .workspace_model import MAX_BODY

def add_routes(app, remote, model, peer, refusal_headers, request_path):
    # No request text is stored. Bounded process-local replay, scoped to device + requestId + body digest.
    cache = OrderedDict()
    @app.post('/workspace/v1/compose')
    async def compose(request: Request):
        auth = remote.authenticate('POST', request_path(request.scope), request.headers, peer(request))
        if auth.device is None:
            return JSONResponse({'error': auth.reason, 'final': auth.final}, 401, headers=refusal_headers(auth))
        headers = {'Cache-Control': 'no-store'}
        refusal = {'schemaVersion': 1, 'status': 'refused', 'reason': 'bad_request'}
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY: return JSONResponse(refusal, 400, headers=headers)
        try: body = json.loads(raw)
        except (ValueError, RecursionError): return JSONResponse(refusal, 400, headers=headers)
        if (not isinstance(body, dict) or set(body) != {'schemaVersion', 'requestId', 'request'}
                or type(body['schemaVersion']) is not int or body['schemaVersion'] != 1
                or not isinstance(body['requestId'], str) or not 0 < len(body['requestId']) <= 64
                or '\0' in body['requestId'] or not isinstance(body['request'], dict)
                or body['requestId'] != body['request'].get('requestId')):
            return JSONResponse(refusal, 400, headers=headers)
        inner = body['request']
        try: digest = hashlib.sha256(json.dumps(inner, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        except (ValueError, RecursionError): return JSONResponse(refusal, 400, headers=headers)
        key = (auth.device.id, body['requestId'], digest)
        now = time.monotonic()
        for old, (expiry, _) in list(cache.items()):
            if expiry <= now: del cache[old]
        if key in cache:
            _, result = cache[key]; cache.move_to_end(key)
            return JSONResponse(result, headers=headers)
        cancelled = threading.Event()
        work = asyncio.create_task(asyncio.to_thread(model.compose, inner, cancelled.is_set))
        try:
            while not work.done():
                if await request.is_disconnected(): cancelled.set(); break
                await asyncio.sleep(0.05)
            result = await asyncio.shield(work)
            if result.get('failure') == 'model_unavailable':
                return JSONResponse({'schemaVersion': 1, 'status': 'unavailable'}, 503, headers=headers)
            cache[key] = (time.monotonic() + 900, result)
            while len(cache) > 128: cache.popitem(last=False)
            return JSONResponse(result, headers=headers)
        except asyncio.CancelledError:
            cancelled.set()
            raise
