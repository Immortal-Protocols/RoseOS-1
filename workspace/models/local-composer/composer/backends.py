"""Model backends behind one call: generate(system, user, grammar, max_tokens, deadline_s) -> Generation.

- HeuristicBackend: deterministic, no model. It reads the alias lines and builds a plausible layout. It exists so the
  contract, grammar, validator and benchmark harness run end to end offline; its numbers are NOT model results.
- ScriptedBackend: returns fixed texts, for tests of malformed and adversarial output.
- LlamaServerBackend: an adapter for a llama.cpp `llama-server` on the same device (loopback only, so it cannot
  become cloud layout generation). UNEXECUTED: no llama.cpp build, weights or phone route exists in this
  workspace, and none is authorized; it is here so the request shape is reviewable, not as evidence it works.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .contract import Refused

LINE = re.compile(r'^([ancx]\d+) (.*?)(?: \[([^\]]*)\])?(?: -> ([ancx]\d+))?$')


@dataclass
class Generation:
    text: str
    stats: dict = field(default_factory=dict)  # runtime-reported numbers (prompt/gen tokens, ms), if any


class HeuristicBackend:
    info = {'id': 'heuristic-fake', 'revision': 'n/a', 'quantization': 'n/a', 'runtime': 'none (no model)'}

    def generate(self, system, user, grammar, max_tokens, deadline_s) -> Generation:
        rows = [m.groups() for m in map(LINE.match, user.splitlines()) if m]
        ask = user.rsplit('Request: ', 1)[-1].lower()
        apps = [r[0] for r in rows if r[0][0] == 'a']
        notes = [r[0] for r in rows if r[0][0] == 'n']
        content = [r[0] for r in rows if r[0][0] == 'c']
        sections = []
        if len(apps) >= 2:
            sections.append({'component': 'grid', 'title': 'Apps', 'items': apps[:6]})
        if notes:
            sections.append({'component': 'list', 'title': 'Updates', 'items': notes[:4]})
        if len(content) >= 2:
            sections.append({'component': 'carousel', 'title': 'For you', 'items': content[:6]})
        words = re.findall(r'[a-z]+', ask)
        title = (words[0].capitalize() if words else 'Now')[:40]
        return Generation(json.dumps({'title': title, 'sections': sections[:4]}, separators=(',', ':')))


class ScriptedBackend:
    info = {'id': 'scripted', 'revision': 'n/a', 'quantization': 'n/a', 'runtime': 'none (no model)'}

    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    def generate(self, system, user, grammar, max_tokens, deadline_s) -> Generation:
        self.calls.append({'system': system, 'user': user, 'grammar': grammar, 'max_tokens': max_tokens})
        item = self.texts.pop(0)
        if isinstance(item, Exception):
            raise item
        return Generation(item)


def _loopback(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ''
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LlamaServerBackend:
    """UNEXECUTED adapter for llama.cpp's `/completion` endpoint with a GBNF grammar (see module docstring)."""

    def __init__(self, base_url: str, info: dict):
        if not _loopback(base_url):
            raise ValueError('the composer only talks to an on-device runtime (loopback); no remote layout service')
        self.base_url, self.info = base_url.rstrip('/'), dict(info)

    def generate(self, system, user, grammar, max_tokens, deadline_s) -> Generation:
        # The chat template is model-specific; the runtime applies it via /apply-template in a bound build. A
        # plain concatenation keeps this adapter model-neutral for review.
        body = {'prompt': f'{system}\n\n{user}\n', 'grammar': grammar, 'n_predict': max_tokens,
                'temperature': 0, 'cache_prompt': True, 'stream': False}
        req = urllib.request.Request(self.base_url + '/completion', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=max(deadline_s, 0.1)) as resp:
                data = json.load(resp)
        except TimeoutError:
            raise Refused('timeout') from None
        except (urllib.error.URLError, OSError, ValueError):
            raise Refused('model_unavailable') from None
        timings = data.get('timings') or {}
        return Generation(data.get('content', ''), {
            'promptTokens': timings.get('prompt_n'), 'promptMs': timings.get('prompt_ms'),
            'outputTokens': timings.get('predicted_n'), 'outputMs': timings.get('predicted_ms')})
