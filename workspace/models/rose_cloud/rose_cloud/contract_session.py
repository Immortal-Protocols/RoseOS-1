"""The proposed Qwen serving contract, checked on ONE loaded server (MODELS-QWEN-CONTRACT-001).

bootstrap's cloud-qwen-contract-once diagnostic starts `vllm serve` inside its isolated container with the PROPOSED
profile of serving/qwen-fp8.json (served id ``rose-qwen``, the model's own chat template, ``--reasoning-parser qwen3``,
``--max-model-len 8192``) on the container's loopback, then runs these fixed cases against it with `docker exec`:

* ``/v1/models`` lists exactly ``rose-qwen`` with ``max_model_len`` 8192;
* every request is a body the Rose Models client (rose_chat_core::agent::Agent) sends byte for byte, pinned in
  qwen_contract_requests.json by rose-chat-core's tests/qwen_contract.rs: a chat turn (``chat``), an ``open_app``
  proposal and the request after its tool result (``tool``), and a request whose history is far past the proven
  2048-token context and asks for a code word given at its start (``long``);
* each reply gets the verdict that agent would reach (``verdict``: identity, truncation, empty, length, then the
  closed local-agent-v1 envelope), checked against the agent itself on the shared fixture
  tests/fixtures/qwen_contract_verdicts.json, plus the exact response identity and where the reasoning went.

Deadlines (MODELS-QWEN-CONTRACT-002): each request waits exactly as long as the phone does, the client's own
``inference_timeout_ms`` (rose_chat_core Limits::default(), generated into the fixture's ``client``: 120 s). A request
still unanswered then is a reply with the client's own verdict ``failed:timeout_response``, ``timedOut`` and its
elapsed seconds; the case goes on. A case stays inside bootstrap's 300 s per run (CASE_BUDGET_S): a request that could
not get a full client timeout in what is left is not sent and is listed in ``notRun`` (``budget``), as is a tool
follow-up whose proposal did not come (``no_proposal``). ``allExpected`` is false whenever anything timed out or did
not run; nothing is dropped from the result.

Thinking (MODELS-QWEN-CONTRACT-NOTHINK-001): ``run(..., thinking=False)`` is the no-thinking profile's rule. Every
other check is the same, but instead of reasoning arriving in its separate field (``reasoningSeparated``) it requires
that no reply carries any reasoning and none has thinking in its content (``noThinking``). An answer the parser moved
into the reasoning field leaves the content empty, which is the client's ``failed:empty_completion``, never a pass.

The result holds fixed tokens, booleans, counts and seconds only: never a prompt, a reply, reasoning text or the
code word. Standard library only; it runs in the serving image.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

REQUESTS = json.loads((Path(__file__).resolve().parent / 'qwen_contract_requests.json').read_text())
SERVED = 'rose-qwen'
MAX_MODEL_LEN = 8192
PROVEN_MAX_MODEL_LEN = 2048
CONTRACT = 'local-agent-v1'
CLIENT = REQUESTS['client']  # rose_chat_core Limits::default(), generated with the bodies: the app sends no limits
MAX_REPLY_CHARS = CLIENT['max_reply_chars']
REQUEST_TIMEOUT_S = CLIENT['inference_timeout_ms'] / 1000  # the phone's own wait for one reply (120 s)
MODELS_TIMEOUT_S = 10  # the /v1/models check (not a client request)
CASE_BUDGET_S = 285  # one case, inside bootstrap's QWEN_WARM_SESSION_S (300 s) with room for the runner's start
REPLIES = ('chat', 'tool', 'toolAfter', 'long')
READ_MAX = 1 << 20  # the client's response cap
FAILED = ('model_identity_mismatch', 'truncated_reply', 'empty_completion', 'reply_too_long', 'malformed_action',
          'unknown_action', 'action_not_allowed', 'malformed_response', 'http_status', 'timeout_response')
VERDICTS = ('final',) + tuple('open_app:' + a for a in REQUESTS['allowlist']) + tuple('failed:' + k for k in FAILED)
CASE_REQUESTS = {'chat': ('chat',), 'tool': ('tool',), 'long': ('long',), 'contract': ('chat', 'tool', 'long')}
CASES = tuple(CASE_REQUESTS)
FIELDS = ('rose_action', 'type', 'text', 'app_id')


class _Malformed(Exception):
    pass


def _strict(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _Malformed('duplicate field')
    return dict(pairs)


def _no_constant(_):
    raise _Malformed('NaN or Infinity')


def _valid_text(value):
    try:
        value.encode('utf-8')  # a lone surrogate escape is not text (serde_json refuses it)
    except UnicodeEncodeError:
        return False
    return True


def parse_action(content: str, allowlist) -> str:
    """rose_chat_core::agent::parse_action: 'final', 'open_app:<id>' or 'failed:<kind>'."""
    content = content.strip()
    if not content.startswith('{'):
        return 'failed:malformed_action'
    try:
        envelope = json.loads(content, object_pairs_hook=_strict, parse_constant=_no_constant)
    except (ValueError, _Malformed):
        return 'failed:malformed_action'
    if not isinstance(envelope, dict) or set(envelope) - set(FIELDS):
        return 'failed:malformed_action'
    kind, action, text, app_id = (envelope.get(k) for k in ('type', 'rose_action', 'text', 'app_id'))
    if not isinstance(action, str) or not isinstance(kind, str) or not all(
            v is None or isinstance(v, str) for v in (text, app_id)) or not all(
            _valid_text(v) for v in (action, kind, text, app_id) if v is not None):
        return 'failed:malformed_action'
    if action != CONTRACT:
        return 'failed:malformed_action'
    if kind == 'final':
        return 'final' if text is not None and app_id is None and text.strip() else 'failed:malformed_action'
    if kind == 'open_app':
        if text is not None or app_id is None:
            return 'failed:malformed_action'
        return f'open_app:{app_id}' if app_id in allowlist else 'failed:action_not_allowed'
    return 'failed:unknown_action'


def decode(body: bytes):
    """rose_chat_core::wire::decode_success: (message, choice, root) or None for malformed_response."""
    try:
        root = json.loads(body, parse_constant=_no_constant)
    except (ValueError, _Malformed):
        return None
    if not isinstance(root, dict):
        return None
    choices = root.get('choices')
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get('message') if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        return None
    for obj, key in ((message, 'content'), (message, 'reasoning'), (message, 'reasoning_content'),
                     (choice, 'finish_reason'), (root, 'model'), (root, 'id')):
        if obj.get(key) is not None and not isinstance(obj.get(key), str):
            return None
    if root.get('usage') is not None and not isinstance(root.get('usage'), dict):
        return None
    return message, choice, root


def verdict(status: int, body: bytes, model: str = SERVED, allowlist=tuple(REQUESTS['allowlist'])) -> str:
    """The verdict rose_chat_core::agent::Agent reaches on one inference response (on_inference's order)."""
    if not 200 <= status <= 299:
        return 'failed:http_status'
    decoded = decode(body)
    if decoded is None:
        return 'failed:malformed_response'
    message, choice, root = decoded
    if root.get('model') is not None and root['model'] != model:
        return 'failed:model_identity_mismatch'
    if choice.get('finish_reason') == 'length':
        return 'failed:truncated_reply'
    content = message.get('content')
    if content is None or not content.strip():
        return 'failed:empty_completion'
    if len(content) > MAX_REPLY_CHARS:
        return 'failed:reply_too_long'
    return parse_action(content, allowlist)


def observe(status: int, body: bytes, expect: str, recall: str | None = None) -> dict:
    """What one reply showed: fixed tokens, flags and counts only."""
    v = verdict(status, body)
    out = {'status': status, 'verdict': v, 'expectMet': v == expect, 'timedOut': False, 'identityExact': False,
           'finish': None,
           'reasoningField': None, 'reasoningChars': None, 'contentChars': None, 'thinkInContent': None,
           'promptTokens': None, 'completionTokens': None}
    decoded = decode(body) if 200 <= status <= 299 else None
    if decoded is None:
        return out
    message, choice, root = decoded
    fields = [k for k in ('reasoning', 'reasoning_content') if message.get(k)]
    content = message.get('content') or ''
    usage = root.get('usage') or {}
    count = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None  # noqa: E731
    finish = choice.get('finish_reason')
    out.update(identityExact=root.get('model') == SERVED,
               finish=finish if finish in ('stop', 'length') else None if finish is None else 'other',
               reasoningField='both' if len(fields) == 2 else fields[0] if fields else None,
               reasoningChars=max((len(message[k]) for k in fields), default=0),
               contentChars=len(content), thinkInContent='<think>' in content or '</think>' in content,
               promptTokens=count(usage.get('prompt_tokens')), completionTokens=count(usage.get('completion_tokens')))
    if recall is not None:
        out['recalled'] = False
        if v == 'final':
            text = json.loads(content.strip())['text']
            out['recalled'] = recall in text.lower()  # a flag; the text never leaves
        out['beyondProven'] = out['promptTokens'] is not None and out['promptTokens'] > PROVEN_MAX_MODEL_LEN
    return out


def timed_out(seconds: float, recall: str | None = None) -> dict:
    """A request the client would have given up on: its own verdict, never a pass, with the time it was given."""
    out = {'status': None, 'verdict': 'failed:timeout_response', 'expectMet': False, 'timedOut': True,
           'identityExact': False, 'finish': None, 'reasoningField': None, 'reasoningChars': None, 'contentChars': None,
           'thinkInContent': None, 'promptTokens': None, 'completionTokens': None, 'seconds': round(seconds, 2)}
    if recall is not None:
        out.update(recalled=False, beyondProven=None)
    return out


class Timeout(Exception):
    pass


def _http(opener, method, url, body=None, timeout=None):
    """(status, body); raises Timeout when nothing completes within ``timeout`` (urllib raises TimeoutError while
    waiting for the response, or a URLError around one while sending)."""
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={'Content-Type': 'application/json'} if body is not None else {})
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_S if timeout is None else timeout) as r:
            return r.status, r.read(READ_MAX)
    except urllib.error.HTTPError as e:
        return e.code, b''
    except (TimeoutError, socket.timeout) as e:
        raise Timeout() from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise Timeout() from e
        raise


def check_models(opener, base) -> dict:
    out = {'status': None, 'ids': None, 'idsExact': False, 'maxModelLen': None, 'timedOut': False}
    try:
        status, body = _http(opener, 'GET', base + '/v1/models', timeout=MODELS_TIMEOUT_S)
    except Timeout:
        out['timedOut'] = True
        return out
    out['status'] = status
    try:
        data = json.loads(body)['data']
        ids = [m['id'] for m in data]
        out.update(ids=len(ids), idsExact=ids == [SERVED])
        mml = data[0].get('max_model_len') if len(data) == 1 else None
        out['maxModelLen'] = mml if isinstance(mml, int) and not isinstance(mml, bool) else None
    except (ValueError, KeyError, TypeError, IndexError):
        pass
    return out


def run(base: str, case: str, clock=time.monotonic, opener=None, into: dict | None = None,
        thinking: bool = True) -> dict:
    """One fixed case against the loaded server. Fills and returns ``into`` (so a caller keeps what was measured if a
    later request raises)."""
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only, never a proxy
    r: dict = into if into is not None else {}
    r.update(case=case if case in CASES else None, step='models')
    if case not in CASES:
        r['step'] = 'bad_case'
        return r
    deadline = clock() + CASE_BUDGET_S
    r['notRun'] = {}
    r['models'] = check_models(opener, base)
    expected = []
    for name in CASE_REQUESTS[case]:
        r['step'] = name
        steps = REQUESTS['cases'][name]
        for i, req in enumerate(steps):
            key = name if i == 0 else name + 'After'
            expected.append(key)
            if i and name in r['notRun']:
                r['notRun'][key] = r['notRun'][name]  # its first request was not sent either, for the same reason
                continue
            if i and r[name]['verdict'] != steps[0]['expect']:
                r['notRun'][key] = 'no_proposal'  # the follow-up body only exists after the expected first reply
                continue
            if deadline - clock() < REQUEST_TIMEOUT_S:
                r['notRun'][key] = 'budget'  # it could not get the client's full wait inside this run
                continue
            t = clock()
            try:
                status, body = _http(opener, 'POST', base + '/v1/chat/completions', req['body'].encode())
            except Timeout:
                r[key] = timed_out(clock() - t, req.get('recall'))
                continue
            r[key] = observe(status, body, req['expect'], req.get('recall'))
            r[key]['seconds'] = round(clock() - t, 2)
    replies = [r[k] for k in REPLIES if k in r]
    r['thinking'] = bool(thinking)
    r['reasoningSeparated'] = any(x['reasoningChars'] for x in replies) and not any(
        x['thinkInContent'] for x in replies)
    r['noThinking'] = bool(replies) and not any(x['reasoningChars'] or x['thinkInContent'] for x in replies)
    # a request in notRun has no reply, so it fails the first all(); a timed-out reply never meets its expectation
    r['allExpected'] = bool(
        r['models']['idsExact'] and r['models']['maxModelLen'] == MAX_MODEL_LEN
        and (r['reasoningSeparated'] if thinking else r['noThinking']) and all(k in r for k in expected)
        and all(x['expectMet'] and x['identityExact'] for x in replies)
        and (case not in ('long', 'contract') or (r['long']['recalled'] and r['long']['beyondProven'])))
    r['step'] = 'done'
    return r
