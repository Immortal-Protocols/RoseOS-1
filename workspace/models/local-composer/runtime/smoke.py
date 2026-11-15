#!/usr/bin/env python3
"""Opt-in: actual local model through Rose's signed ASGI route; public fixtures only, no listening server."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'prototypes/voice-phone-demo'))
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from rose.remote import Remote, build_remote_app
from rose.workspace_model import WorkspaceModel

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads((ROOT / 'workspace/models/local-composer/contract/examples/snapshot-demo.json').read_text())
    prompts = ["I'm cooking dinner. Show recipes, a timer and music.",
               'Help me focus on work, my report and meeting.', 'I want to unwind with music and podcasts.', None,
               'Show recipes in a carousel, notifications in a list and the timer button.',
               'Show tomato pasta as a card.']
    model = WorkspaceModel(args.config)
    results = []
    async def unused(ws, **kw): await ws.close()
    try:
        model.preload()
        assert model.wait_ready(60), 'local model preload did not become ready'
        with tempfile.TemporaryDirectory(prefix='rose-model-smoke-') as temp:
            remote = Remote(directory=Path(temp), name='synthetic-model-test', hosts=lambda: [])
            key = ec.generate_private_key(ec.SECP256R1())
            public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            device = hashlib.sha256(public).hexdigest(); remote.devices.enroll(public, 'test', 'synthetic', '100.64.0.2')
            server = SimpleNamespace(phone=None, serve_audio=unused, serve_events=unused, serve_phone=unused, workspace_model=model)
            with TestClient(build_remote_app(server, remote)) as client:
                for index, prompt in enumerate(prompts):
                    path = '/workspace/v1/compose'; stamp = str(int(time.time() * 1000))
                    nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b'=').decode()
                    message = ('rose-auth-v1\nPOST\n' + path + '\n' + stamp + '\n' + nonce + '\n' + remote.identity.pin).encode()
                    headers = {'X-Rose-Device': device, 'X-Rose-Time': stamp, 'X-Rose-Nonce': nonce,
                               'X-Rose-Signature': base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()}
                    body = dict(contract='rose-layout-v1', requestId='smoke-' + str(index), trigger='button' if prompt is None else 'direct_text',
                                prompt=prompt, catalogVersion='cat-1', snapshot=snapshot)
                    reply = client.post(path, json={'schemaVersion': 1, 'requestId': body['requestId'], 'request': body}, headers=headers)
                    result = reply.json(); results.append(result)
                    assert reply.status_code == 200 and result.get('status') == 'ok', result
                    ids = {item for section in result['layout']['sections'] for item in section['items'] + section.get('actions', [])}
                    if index == 0: assert {'app.recipes', 'app.music', 'act.timer10'} <= ids and 'app.mail' not in ids
                    if index == 1: assert {'app.mail', 'app.docs', 'ntf.2', 'ntf.3'} <= ids and 'app.recipes' not in ids
                    if index == 2: assert {'app.music', 'app.podcasts'} <= ids and 'app.mail' not in ids
                    if index == 4: assert {'carousel', 'list', 'button'} <= {s['component'] for s in result['layout']['sections']}
                    if index == 5: assert 'card' in {s['component'] for s in result['layout']['sections']}
                    print('case', index, 'ok; server compose ms:', result['timing']['composeMs'])
    finally:
        model.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results))
    print('Six real-model signed-route checks passed; no phone or display execution.')

if __name__ == '__main__': main()
