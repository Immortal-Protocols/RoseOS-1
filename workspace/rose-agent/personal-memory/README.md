# Personal memory trial

An isolated Mac worker collects personal preferences, constraints and facts from
new Rose Agent conversation. Collection and use are independently switchable.
No procedural memory, speaker enrollment or automatic phone actions are added.

The extraction/temporary-memory rules are adapted from
[OpenAI's personalization cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization).
This is our adapter, not ChatGPT's internal memory implementation. The current
`personal-useful-relationships-v2` policy uses the published selection criteria,
changes the travel-specific scope to personal assistance, and adds the tested
sentence for explicit, useful relationship context. It does not reject a memory
merely because its evidence is voice. Ambiguous or temporary notes remain
review-only; there is no speaker-recognition filter.

Invalid model output leaves the evidence queued. Original proposals and quotations
are retained. A policy replay is reviewed before installation; repeated extraction
can omit or rephrase valid notes, so previously reviewed notes are not silently
replaced just because a fresh model run excludes them.

## Data flow

- `memory.mjs` polls the existing local timeline every two seconds and preserves new
  source entries in a private journal, starting at the trial's activation time.
- Extraction runs in the background after four pending user messages, 90 seconds
  of quiet, or ten minutes of pending input. Requests are at least two minutes apart,
  including failed attempts, with only one extraction in flight. There are no
  hourly or daily quotas.
- Each request includes at most 40 new messages / 24,000 source characters, two
  preceding context messages and up to 60 earlier proposals. Individual messages
  are clipped to 6,000 characters for extraction; the journal retains the originals.
- Calls use `gpt-6-luna`, high reasoning, Standard processing (`service_tier: default`),
  up to 8,192 output/reasoning tokens, `store:false`, no
  tools, and the existing private OpenAI key. Batch model, reasoning effort, returned service tier, and token counts are saved.
- Every proposal cites an exact quotation from a new user message. Assistant text
  is context, never evidence about the user. Voice identity remains unverified.
- `context.mjs` supplies the full kept list with the same IDs, timestamps, source labels and
  instructions. System-selected durable notes are included by default; a review
  can keep a temporary/uncertain note or remove any note. There is no recency
  count or aggregate text cutoff. Unreviewed temporary and uncertain notes remain
  review-only. Later related
  durable corrections supersede earlier notes in the delivered snapshot without
  deleting their original evidence. This relies on the extraction model correctly
  linking related notes; unrelated contradictions may still need manual review.
- Recent conversation is preserved. The voice frontend gets the current memory
  with initial history and receives changed snapshots through its existing context
  channel. Codex receives the same current personal memory with task requests.
- Current user instructions and recent conversation override memory. Memory is
  fallible data, not permission, commands, or evidence of current phone state.
- `deliveries.jsonl` logs which memory IDs were offered/queued to each voice session
  or task request. Queued context does **not** prove the model read or used it.
- Turning use off delivers a withdrawal of earlier trial memory. Prior text already
  inside a conversation cannot be erased; start fresh sessions for a clean baseline.

Private files live under `~/.config/pixel-voice/personal-memory-trial/` (directory
0700, data files 0600): evidence journal, state/proposals, control flags, context
delivery audit, and a script-free `review.html`. These never belong in Git.

No old transcript import is performed. Text-only extraction cannot reliably tell
TV speech from the owner. The trial deliberately preserves source attribution for
review; this is not biometric speaker recognition. Only new Rose Agent input is
collected, not arbitrary desktop chats. A stopped collector can miss entries if
Rose Agent's rolling timeline prunes them before the next poll.

## Operate

From this directory:

```sh
npm test
npm run test:live             # synthetic OpenAI fixture; one paid API call
node test-voice-live.mjs      # isolated hosted voice-model test; no phone actions
node install.mjs install
node cli.mjs status
node cli.mjs review
node cli.mjs use-on           # enable durable memories in voice/tasks
node cli.mjs use-off          # return to collection only
node cli.mjs pause            # pause collection/processing; use flag unchanged
node cli.mjs resume           # skip input during the paused interval
node cli.mjs reject MEMORY_ID # exclude a proposal from future snapshots
node install.mjs stop         # disable both use and collection, unload worker
```

Pause discards an in-flight extraction result. Pre-pause pending evidence is retained.
Use-on/use-off do not alter collection. The review shows in-use versus review-only
proposals; expand a proposal for exact source and nearby conversation. Flags are
reflected in the report within two seconds while the worker runs.

The installer copies immutable SHA-256-addressed runtime files under
`~/.local/share/rose-agent/personal-memory/releases/` and manages only LaunchAgent
`local.pixel-voice.personal-memory`. It preserves existing flags. Runtime code lives
outside Documents. The main voice server remains a separate service.

## Versioning and rollback

This component has its own Git repository. `v0.1.0` is the collection-only worker;
`integration/baseline/` preserves the voice-server source, HTML and Android manifest
before active-memory wiring. `integration/voice-memory.patch` records the exact
integration diff. The previous signed APK is privately retained at
`~/.config/pixel-voice/personal-memory-trial/pre-live-pixel-voice.apk`.

Immediate behavioral rollback is `node cli.mjs use-off`; it requires no app install,
voice-model change or Bluetooth change. `node install.mjs stop` additionally stops
collection. Preserve `context.mjs` while the main server imports it; do not replace
runtime/source with the old tag without first reversing the integration patch.
For a full source rollback, first disable use, verify the integration diff still
applies cleanly, reverse it, and restart only the voice server. Android downgrade
may require a rebuild with a higher version code; do not wipe phone data.

Only source/tests/docs are versioned. No GitHub remote has been configured.

## Isolated model comparison

`node compare.mjs` previews a replay plan without making API calls. Add `--run`
to compare `gpt-6.1-sol` at low reasoning using the unchanged extraction prompt.
`--limit 1` runs a pilot; `--resume /absolute/private/comparison/directory --run`
continues the saved snapshot without repeating completed or failed requests.
The comparison has a conservative $3 cost guard and at most three requests in
flight. It does not change the worker, flags, live proposals or delivered memories.

Historical completed batches replay their original messages and prior Luna
proposals, keeping model choice as the changed variable. Pending evidence is
processed separately and labeled backlog, with no matched Luna result. Results,
API usage, source evidence, and a review HTML file stay in the private trial
directory. Classification changes are not automatically evidence of improvement;
check usefulness and attribution against the source conversation.

For prompt comparisons, use `--from-snapshot /path/to/snapshot.json` to hold the
conversation fixed, `--prompt-file /path/to/prompt.txt` and `--label variant-name`.
The exact prompt and its SHA-256 are recorded with the result. The same Sol model,
low reasoning, evidence validation, and output schema are retained. This compares
selection only, not a full memory-consolidation implementation. A published
domain-specific prompt and a domain-adjusted variant must be labeled separately.
Use `--model gpt-6-luna` to isolate a prompt change against the original Luna
extractor instead. Reports record the selected model and use its own token rates;
the default remains `gpt-6.1-sol`. All variants keep low reasoning effort.

## Validation

Automated checks cover collection, source retention, exact quotes, corrections,
limits, model failures, pause/resume, concurrency, persistence, HTML escaping,
system selection, explicit keep/remove/undo, disabled behavior, full-list context, delivery audit, and
voice context updates with retained recent conversation. Isolated live tests verify
memory extraction and actual ElevenLabs-agent recall/correction. They do not prove
accuracy on the user's real conversations; that is the purpose of the multi-day trial.

## Swipe review

The Memories app uses Rose Agent’s existing paired connection through a dedicated,
signature-protected provider. GET `/memories/review` returns exact note text; POST
`/memories/review` saves keep/remove and POST `/memories/undo` reverses the last
swipe. Every mutation has an idempotency ID and expected revision. A failed or
uncertain save does not advance the card. `reviews.json` stores decisions and
undo history privately, separately from extraction state and control flags.
Removing a note is a list edit, not a negative preference or a training example.
No semantic blacklist or model feedback is generated from a removal.
