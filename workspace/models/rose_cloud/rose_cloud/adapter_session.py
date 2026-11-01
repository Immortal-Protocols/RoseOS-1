"""One bounded live session through the real adapter (vllm_brain.VllmBrain) against a running vLLM server
(CLOUD-QWEN-ADAPTER-001): multi-turn text and image history, fork/rewind/stale-image, and cancellation, on synthetic
data only.

``run_session(brain, image, server)`` drives exactly the calls PR288's conductor and task manager make
(``sequence``, ``append_text``, ``append_image``, ``generate`` with and without ``cancel``, ``mark``, ``rewind``,
``fork``) and returns counts, booleans and timings only: never prompt, reply or image content.

1. turn 1, text: system prefix (the model's own template) + a user turn, greedy, at most TURN_TOKENS;
2. turn 2, image: a user turn holding the synthetic image (the chat route), at most TURN_TOKENS; the server's prompt
   token ids are compared with the client's (``last_prompt_exact``);
3. turn 3, text with the image in history (still the chat route), at most TURN_TOKENS;
4. fork and rewind: fork after turn 3, rewind the original to before turn 3 and generate there; the fork must be
   untouched and the rewound request exact. Then rewind to before the image: the image is dropped (stale) and the
   next request goes back to the text route;
5. cancellation in flight: a long request (CANCEL_MAX_TOKENS) cancelled through ``cancel`` after CANCEL_AFTER
   tokens. The client side is checked directly (nothing appended after the cancel, the worker joined, how long the
   generator took to stop). The server side is checked by evidence, never assumed: the ``vllm:num_requests_running``
   gauge on ``/metrics`` returning to 0, and the time a 1-token probe takes (the server runs ONE sequence at a time,
   so a request still generating behind the cancel would hold the probe for many seconds). ``semantics`` is
   ``inflight_abort`` only with that evidence, ``client_suppressed_only`` if the server was still busy, and
   ``completed_before_cancel`` if the request finished before the cancel fired;
6. a cancel after completion: a request that finishes, then the same cancel: nothing changes (``lateCancelNoop``).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from dataclasses import dataclass

from .vllm_brain import IM_END, VllmBrain

GEN_FAST = '<|im_start|>assistant\n<think>\n\n</think>\n\n'
SYSTEM = 'You are Rose, a voice assistant on the user\'s phone. Answer in one short sentence.'
TURNS = ('Hello! Reply in five words.', 'What colour is this picture?', 'And what shape is it?',
         'Say one word about the weather.', 'Count from one to two hundred, separated by commas.')
TURN_TOKENS, CANCEL_MAX_TOKENS, CANCEL_AFTER, LATE_TOKENS = 16, 256, 3, 2
IDLE_WAIT_S, PROBE_SLOW_S = 20.0, 15.0
RUNNING = re.compile(rb'^vllm:num_requests_running(?:\{[^}]*\})?\s+([0-9.]+)\s*$', re.M)


@dataclass
class Sampling:  # the fields of PR288 rose/brain.py Sampling (which imports mlx)
    temperature: float = 0.0
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 0.0
    max_tokens: int = TURN_TOKENS


class Server:
    """Read-only observation of the server the brain talks to: GET /metrics (the running-requests gauge)."""

    def __init__(self, base_url: str, timeout_s: float = 5.0):
        self.base_url, self.timeout_s = base_url.rstrip('/'), timeout_s

    def running(self) -> float | None:
        try:
            with urllib.request.urlopen(self.base_url + '/metrics', timeout=self.timeout_s) as r:
                body = r.read(4 << 20)
        except (OSError, ValueError):
            return None
        found = [float(m) for m in RUNNING.findall(body)]
        return sum(found) if found else None


def _user(text: str) -> str:
    return f'<|im_start|>user\n{text}<|im_end|>\n' + GEN_FAST


def _turn(seq, max_tokens: int, clock) -> dict:
    t0 = clock()
    first, out = None, []
    for token in seq.generate(Sampling(max_tokens=max_tokens)):
        first = first if first is not None else clock()
        out.append(token)
    return {'tokens': len(out), 'endedWithImEnd': bool(out) and out[-1] == seq.brain.im_end,
            'firstTokenS': None if first is None else round(first - t0, 2), 'totalS': round(clock() - t0, 2),
            'promptExact': seq.last_prompt_exact, 'tokenSha256': hashlib.sha256(json.dumps(out).encode()).hexdigest()}


def _drain(units):
    try:
        while True:
            next(units)
    except StopIteration as done:
        return done.value


def run_session(brain: VllmBrain, image, server: Server, clock=time.monotonic, sleep=time.sleep,
                into: dict | None = None) -> dict:
    """Fills and returns ``into`` (so a caller keeps what was measured if a later step raises)."""
    r: dict = into if into is not None else {}
    r['step'] = 'turn1'
    seq = brain.sequence('talker')
    _drain(seq.append_text(brain.system_prefix(SYSTEM)))
    _drain(seq.append_text(_user(TURNS[0])))
    r['turn1'] = _turn(seq, TURN_TOKENS, clock)

    r['step'] = 'turn2'
    before_image = seq.mark()
    _drain(seq.append_text(f'<|im_start|>user\n{TURNS[1]}\n'))
    r['imageTokens'] = _drain(seq.append_image(image))
    _drain(seq.append_text(f'{IM_END}\n' + GEN_FAST))
    r['turn2'] = _turn(seq, TURN_TOKENS, clock)

    r['step'] = 'turn3'
    before_turn3 = seq.mark()
    _drain(seq.append_text(_user(TURNS[2])))
    r['turn3'] = _turn(seq, TURN_TOKENS, clock)
    r['imagesInHistory'] = len(seq.images)

    r['step'] = 'fork'
    snapshot = list(seq.tokens)
    fork = seq.fork('task')
    seq.rewind(before_turn3)
    rewind_exact = seq.tokens == snapshot[:before_turn3.tokens] and len(seq.images) == 1
    _drain(seq.append_text(_user(TURNS[3])))
    r['rewound'] = _turn(seq, TURN_TOKENS // 2, clock)
    seq.rewind(before_image)
    stale_dropped = not seq.images and brain.image_pad not in seq.tokens
    requests = seq.requests
    _drain(seq.append_text(_user(TURNS[3])))
    r['staleText'] = _turn(seq, TURN_TOKENS // 2, clock)  # the image is gone: the text route again
    r['history'] = {'rewindExact': rewind_exact, 'forkUntouched': fork.tokens == snapshot,
                    'staleImageDropped': stale_dropped, 'staleRequestSent': seq.requests == requests + 1,
                    'forkImages': len(fork.images)}

    r['step'] = 'cancel'
    r['cancel'] = cancel_inflight(seq, server, clock, sleep)
    r['step'] = 'late_cancel'
    r['lateCancel'] = cancel_after_completion(seq)
    r['step'] = 'done'
    return r


def cancel_inflight(seq, server: Server, clock=time.monotonic, sleep=time.sleep) -> dict:
    """A long request cancelled after CANCEL_AFTER yielded tokens, then evidence of what the server did."""
    _drain(seq.append_text(_user(TURNS[4])))
    start = len(seq.tokens)
    fired: dict = {}

    def cancel() -> bool:
        if 'at' not in fired and len(seq.tokens) - start >= CANCEL_AFTER:
            fired['at'], fired['tokens'] = clock(), len(seq.tokens) - start
        return 'at' in fired

    t0 = clock()
    list(seq.generate(Sampling(max_tokens=CANCEL_MAX_TOKENS), cancel=cancel))
    end = clock()
    appended = len(seq.tokens) - start
    out = {'fired': 'at' in fired, 'tokensAtCancel': fired.get('tokens'), 'appended': appended,
           'noLateAppend': 'at' in fired and appended == fired['tokens'], 'workerJoined': seq.worker_stopped,
           'clientStopS': round(end - fired['at'], 3) if 'at' in fired else None, 'requestS': round(end - t0, 2),
           'runningAfter': None, 'idleAfterS': None, 'probeS': None, 'probeTokens': None}
    if 'at' in fired:  # server side: the running gauge, then a 1-token probe behind it
        deadline = clock() + IDLE_WAIT_S
        while True:
            running = server.running()
            out['runningAfter'] = running
            if running is None or running == 0 or clock() >= deadline:
                break
            sleep(0.25)
        if running == 0:
            out['idleAfterS'] = round(clock() - end, 2)
        probe = seq.fork('probe')
        _drain(probe.append_text(_user('Say yes.')))
        t = clock()
        out['probeTokens'] = len(list(probe.generate(Sampling(max_tokens=1))))
        out['probeS'] = round(clock() - t, 2)
    if 'at' not in fired:
        out['semantics'] = 'completed_before_cancel'
    elif out['runningAfter'] == 0 and out['probeS'] is not None and out['probeS'] < PROBE_SLOW_S:
        out['semantics'] = 'inflight_abort'
    elif (out['runningAfter'] or 0) > 0 or (out['probeS'] is not None and out['probeS'] >= PROBE_SLOW_S):
        out['semantics'] = 'client_suppressed_only'
    elif out['probeS'] is not None and out['probeS'] < PROBE_SLOW_S:
        out['semantics'] = 'inflight_abort_probe_only'  # no gauge; the probe was not held behind the request
    else:
        out['semantics'] = 'unknown'
    return out


def cancel_after_completion(seq) -> dict:
    """A request that finishes, then the cancel: the sequence must not change."""
    _drain(seq.append_text(_user('Say no.')))
    late = {'cancel': False}
    gen = seq.generate(Sampling(max_tokens=LATE_TOKENS), cancel=lambda: late['cancel'])
    out = list(gen)
    after, requests = list(seq.tokens), seq.requests
    late['cancel'] = True  # the cancel arrives after the last token: there is nothing left to stop
    gen.close()
    return {'tokens': len(out), 'lateCancelNoop': seq.tokens == after and seq.requests == requests,
            'workerJoined': seq.worker_stopped}
