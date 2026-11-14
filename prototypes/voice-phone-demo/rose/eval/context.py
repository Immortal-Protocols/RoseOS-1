"""Opt-in live Terra conversation checks with simulated phone tools, never a real phone.

    python -m rose.eval.context --live

Requires the backend account's existing ChatGPT login. Synthetic transcripts go
outside Git; this module is deliberately excluded from offline test discovery.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import tempfile
import time

from rose.codex_client import CodexClient
from rose.codex_conductor import CONVERSATION_POLICY, CONVERSATION_TOOLS


CASES = {
    'shopping_short_choices': [
        "I'd like to buy some things on Amazon. Can you set up a shopping cart for me?",
        'No.', 'Something fun.', 'You pick.',
    ],
    'shopping_correction': [
        "Help me find a desk lamp on Amazon. Don't buy anything.",
        'Actually, make it a plant.', 'You choose.',
    ],
    'explicit_topic_change': [
        'Can you help me choose something for my Amazon cart?',
        "Forget shopping. Let's play a guessing game instead.",
    ],
}


def replay(name, inputs):
    events, turns, tasks = queue.Queue(), [], []
    client = CodexClient(events.put)
    try:
        with tempfile.TemporaryDirectory(prefix='rose-context-') as cwd:
            selected = client.request('open', name, cwd=cwd, model='gpt-6.1-terra',
                                      instructions=CONVERSATION_POLICY, tools=CONVERSATION_TOOLS)
            for text in inputs:
                turn = {'input': text, 'messages': [], 'tools': []}
                turns.append(turn)
                client.request('submit', name, text=text)
                deadline = time.monotonic() + 60
                while True:
                    event = events.get(timeout=max(.01, deadline-time.monotonic()))
                    if time.monotonic() > deadline:
                        raise TimeoutError('Synthetic conversation turn timed out')
                    if event.get('event') == 'message':
                        turn['messages'].append(event['text'])
                    elif event.get('event') == 'tool':
                        p = event['params']
                        tool = {'name': p['tool'], 'arguments': p['arguments']}
                        turn['tools'].append(tool)
                        if len(turn['tools']) > 20:
                            raise RuntimeError('Synthetic tool limit reached')
                        if p['tool'] == 'phone_task':
                            tasks.append({'id': len(tasks)+1, 'app': p['arguments']['app'],
                                          'goal': p['arguments']['goal'], 'status': 'running'})
                            result = {'ok': True, 'task_id': tasks[-1]['id'], 'status': 'running'}
                        elif p['tool'] == 'update_task':
                            result = {'ok': True, 'task_id': p['arguments']['task_id']}
                        elif p['tool'] == 'list_tasks':
                            result = {'tasks': tasks}
                        else:
                            result = {'ok': False, 'reason': 'Not available in this synthetic fixture'}
                        client.send({'op': 'tool_result', 'callId': event['callId'],
                                     'result': {'success': result.get('ok', True), 'contentItems': [
                                         {'type': 'inputText', 'text': json.dumps(result)}]}})
                    elif event.get('event') == 'done':
                        if event['turn']['status'] != 'completed':
                            raise RuntimeError('Synthetic turn did not complete')
                        break
            calls = [t for turn in turns for t in turn['tools']]
            if name == 'explicit_topic_change':
                passed = not any(t['name'] in ('phone_task', 'update_task') for t in turns[-1]['tools'])
            else:
                keyword = 'plant' if name == 'shopping_correction' else 'fun'
                passed = any(
                    (t['name'] == 'phone_task' and 'amazon' in t['arguments']['app'].lower()
                     and keyword in t['arguments']['goal'].lower())
                    or (t['name'] == 'update_task' and tasks
                        and 'amazon' in tasks[0]['app'].lower()
                        and keyword in t['arguments']['instruction'].lower()) for t in calls)
            return {'case': name, 'passed': passed, 'selected': selected, 'turns': turns}
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='explicitly enable real Codex model requests')
    parser.add_argument('--output', type=Path, default=Path.home()/'.local/share/rose/evals/context.json')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required: these checks use the signed-in Terra account')
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[4]):
        parser.error('store transcripts outside the source checkout')
    results = []
    for name, inputs in CASES.items():
        try:
            result = replay(name, inputs)
        except Exception as exc:
            result = {'case': name, 'passed': False, 'error_type': type(exc).__name__}
        results.append(result)
        print(name, 'PASS' if result['passed'] else 'FAIL', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(results, out, indent=2)
        out.write('\n')
    raise SystemExit(0 if all(r['passed'] for r in results) else 1)


if __name__ == '__main__':
    main()
