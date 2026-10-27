# Task model switching

Explicit voice or typed commands go through the same authenticated request path.
The Mac interprets the leading switch command before sending work to a model.

- `Use Luna` / `Switch to Astra`: use that model until another switch or a Mac service restart.
- `Use GPT-6.1 Sol low on ultrafast for this task`: switch an active task, or reserve that model for the next task if idle. Later tasks return to the previous selection.
- `Use Luna by default`: persist the default across service restarts; does not interrupt current work.
- `Use Luna medium on fast, then calculate seven plus eight`: combine selection and a task.

Aliases: Luna = GPT-6 Luna; Sol = GPT-6.1 Sol; GPT-6 Sol is explicit; Astra = GPT-6 Astra.
Defaults: Luna medium/Fast, GPT-6.1 Sol low/Ultrafast, GPT-6 Sol low/Fast, Astra medium/Fast.
Explicit low/light, medium, high, xhigh, max (and ultra except Luna) and Standard/Fast/Ultrafast override those presets. Unsupported combinations return an error instead of silently selecting another model.

A current-task switch requests interruption and waits for the old turn's completion before starting a new turn on the same thread. It supplies the original task and tells the new model to inspect current state and reuse completed work. The old interruption is not reported as a task failure. Tool actions already committed before interruption remain committed; switching does not roll them back.

The launcher label above the call button is populated from authenticated Mac state, not voice-model claims. It shows model, reasoning, tier, a `Next task` prefix for a reserved override, and `Switching…` during interruption. Idle shows the selected model for new requests. In Guardian mode it shows the Guardian instead. If the Mac poll fails it shows unavailable, rather than leaving an apparently current model label.

Persistent default: `~/.config/pixel-voice/task-model.json`, owner-only. Does not change voice, screenshots, memory model, or Guardian routing. Rollback copies of server files and version 177 APK are in `rose-agent/backups/model-switch-20261014`.

Validation: `node --test rose-agent/server/model-switch.test.mjs` and `node rose-agent/tests/routing.mjs`. Live checks also exercise `/voice/task`, inspect actual routing, and verify the installed launcher label.

## Spoken-command repair (2026-10-15)

The failed request arrived as three transcript fragments: `Switch to GPT 6.1`, `Sol`, and `Medium on Ultra Fast`. The client previously reset its request buffer after gaps longer than one second, leaving only the settings fragment for the server. The normal task agent then tried to find UI controls instead of using the host switcher.

The voice client now retains model-command continuations across pauses and briefly waits for the remaining model/settings words. It dispatches recognized model commands directly even if the voice model does not delegate. If a user adds a setting after the first acknowledgment, a continuation of a pure switch updates that same selection; it does not replay a bundled action task. Unrelated speech still starts a new request. Status questions such as `Did you switch to GPT 6.1 Sol?` return the host's current model state directly. Parser coverage includes `switch models to`, `switch the model back to`, punctuation from transcription, and `ultra fast` without treating it as the Ultra reasoning level.

Regression coverage includes the real browser client fed paused transcript events, early/late duplicate delegation, backchannels, amended settings, unrelated subsequent requests, and an opt-in real speech test through that same client: `node rose-agent/voice-providers/test-live-model-switch.mjs /path/to/24khz-pcm.wav`.
