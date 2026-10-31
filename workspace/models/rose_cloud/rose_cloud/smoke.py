"""One real brain exchange through VllmBrain, shaped like PR288's conductor warm-up and first reply.

  python3 -m rose_cloud.smoke --base-url http://127.0.0.1:8000 --model rose-brain --tokenizer-dir <model dir>

Builds the talker's system prefix with the selected model's own chat template, marks, appends a user turn
("Hello!") with the no-thinking generation prompt, generates greedily up to --max-tokens, checks that the reply
ended with <|im_end|> (or the budget), rewinds to the mark and checks the sequence is exactly the prefix again.
Prints one JSON line: token counts, first-token and total milliseconds, the reply (at most 200 characters) and the
checks. It needs a running vLLM server for the selected model; it starts nothing and downloads nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

from .vllm_brain import IM_END, ServerError, Unsupported, VllmBrain, load_tokenizer

GEN_FAST = '<|im_start|>assistant\n<think>\n\n</think>\n\n'
SYSTEM = 'You are Rose, a voice assistant on the user\'s phone. Answer in one short sentence.'


@dataclass
class Sampling:
    temperature: float = 0.0
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 0.0
    max_tokens: int = 48


def run(brain: VllmBrain, text: str = 'Hello!', max_tokens: int = 48, clock=time.perf_counter) -> dict:
    seq = brain.sequence('talker')
    for _ in seq.append_text(brain.system_prefix(SYSTEM)):
        pass
    prefix = list(seq.tokens)
    mark = seq.mark()
    for _ in seq.append_text(f'<|im_start|>user\n{text}<|im_end|>\n' + GEN_FAST):
        pass
    prompt = len(seq.tokens)
    t0, first, out = clock(), None, []
    for token in seq.generate(Sampling(max_tokens=max_tokens)):
        first = first if first is not None else clock()
        out.append(token)
    total = clock()
    reply = brain.decode([t for t in out if t != brain.im_end])
    seq.rewind(mark)
    return {'promptTokens': prompt, 'replyTokens': len(out), 'endedWithImEnd': bool(out) and out[-1] == brain.im_end,
            'firstTokenMs': None if first is None else round((first - t0) * 1e3, 1),
            'totalMs': round((total - t0) * 1e3, 1), 'reply': reply.replace(IM_END, '')[:200],
            'rewindExact': seq.tokens == prefix, 'requests': seq.requests}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--base-url', required=True)
    p.add_argument('--model', required=True, help='the served model name')
    p.add_argument('--tokenizer-dir', required=True, help="local directory with the selected model's tokenizer")
    p.add_argument('--text', default='Hello!')
    p.add_argument('--max-tokens', type=int, default=48)
    a = p.parse_args(argv)
    tok, template = load_tokenizer(a.tokenizer_dir)
    try:
        result = run(VllmBrain(tok, a.base_url, a.model, template), a.text, a.max_tokens)
    except (ServerError, Unsupported) as e:
        print(json.dumps({'error': type(e).__name__, 'detail': str(e)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result['replyTokens'] and result['rewindExact'] else 1


if __name__ == '__main__':
    sys.exit(main())
