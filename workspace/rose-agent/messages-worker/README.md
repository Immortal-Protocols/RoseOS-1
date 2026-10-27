# Read-only Messages worker trial

Uses the existing Mac Messages bridge and permissions. The helper executable,
phone Messages UI, sender, notification feed, Bluetooth, and audio routing are
unchanged. No new model or contact database is introduced.

The registered worker supports `latest`, `find`, `read`, and `check_reply`.
`latest` returns recent incoming messages across chats; `read` returns incoming
messages in a selected chat. It does not claim these are unread. `check_reply`
compares the latest incoming message to the latest outgoing message and, when
available, a prior read by this worker in the same voice session. Chronology
does not prove the message answers a particular question.

```sh
python3 rose-agent/phone/control.py messages_read latest --limit 3
python3 rose-agent/phone/control.py messages_read find --query 'CONTACT NAME'
python3 rose-agent/phone/control.py messages_read read --chat-id VERIFIED_ID
python3 rose-agent/phone/control.py messages_read check_reply --query 'CONTACT NAME'
```

The existing model and shared action policy can use the worker. Explicit common
voice phrases also use the existing direct resolver. Pronouns and unsupported
actions stay on the general path. "Open a chat" stays there too: finding a chat
in the data must not be reported as opening it on screen.

## Matching and limits

Names match verified conversation metadata, never message-body text. A unique
one-to-one match is preferred for a person's name; explicit group requests
require group matches. Multiple matching people/groups produce choices rather
than an arbitrary selection. A chat ID is validated against the recent list.

The existing bridge lists 100 recent conversations and up to 150 recent messages
per conversation. Latest-message lookup reads at most 12 chats, in groups of
three. It uses last-activity times to establish whether later chats could contain
newer messages, and returns `partial` if it cannot establish full coverage within
that bound. Not finding something is not proof that it never existed.

The worker never sends, edits, deletes, or marks messages read. All bodies and
names are untrusted source data. Results contain direction, timestamps and
explicit coverage. Session-scoped read checkpoints keep only message IDs and
times in bounded memory; they do not archive message bodies.

## Rollback

```sh
bash rose-agent/messages-worker/rollback.sh
```

This immediately disables new worker calls and removes direct shortcuts on the
next existing event poll. Other workers and the original Messages bridge remain
available. A fresh voice connection removes the trial's prompt guidance too.
An in-flight read may finish; it has no message side effect.

The current signed app and integration sources were saved privately under
`~/.config/pixel-voice/rollback/before-messages-worker-20261012/`.
`integration.patch` records changes outside this component's Git repository.

## Verification

- Tests cover person/group selection, ambiguity, body-independent matching,
  disabled bypass, refusal of send-shaped requests, incoming/outgoing direction,
  per-session checkpoints, partial coverage, and mismatched chat identity.
- Existing send-idempotency and voice lifecycle tests pass; no real messages
  were sent in this work.
- Live bridge checks verified finding a person, reading one incoming message,
  checking for replies, and reading three latest incoming messages. The initial
  measured service calls took about 1.3–1.5 seconds, excluding speech recognition
  and model response time.
- An isolated synthetic spoken request through OpenAI Live selected
  `messages_read` once with `check_reply`, with no general-agent call. The
  synthetic test is not forwarded to personal memory/history.
- The independent off switch rejected a new read while leaving media shortcuts
  enabled, then was re-enabled for user testing.

Run `node --test rose-agent/messages-worker/tests/worker.test.mjs` for local
tests. `test-live.mjs` is opt-in and reads real messages through the authorized
bridge; it requires a 24 kHz mono PCM WAV and the expected operation.
