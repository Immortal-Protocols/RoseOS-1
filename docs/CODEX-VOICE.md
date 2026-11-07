# Terra Fast for the shared Rose voice backend

This implements the November 4 update to the [execution plan](https://app.notion.com/p/40e7c646c81b8187b981e04f5599cbc4): **GPT 6.1 Terra, Fast mode, Sign in with ChatGPT**. Luna is superseded for this path. It is opt-in with `--brain codex`; the existing local Qwen path remains available.

The Mac and Linux entry point is the same Python `rose.app`, with the same signed QR pairing, `/ws/phone`, `/ws/audio` and `/ws/events` contract. The existing Rose Agent client works with either endpoint. Its menu now says **Pair with Rose**. No separate Android app, historical full cloud backend, GPU service deployment, firewall change or credentials are included.

## Pull and authenticate (Nathan's local agent / authorized cloud operator)

From your own checkout, first preserve local changes. Fetch and integrate [PR #508](https://github.com/Immortal-Protocols/RoseOS-1/pull/508) from `main` once merged (or review `models/terra-fast-transcripts` before then). This work never accessed Nathan's active Mac checkout.

Use Node 22+ and Codex CLI with the app-server API inspected here at **0.163.0**. Authenticate under the backend's own OS account and writable Codex home, using the supported owner-interactive flow:

```sh
codex login                 # choose Sign in with ChatGPT
# On a headless host, if device authentication is enabled for the account:
codex login --device-auth
codex login status
```

The owner completes sign-in. Do not copy authentication files/tokens between hosts, use `--with-api-key`, or put login data into phone QR codes. The bridge invokes `account/read` with `refreshToken: true` before starting a thread and before each turn; Codex owns managed token refresh. If login is absent, expired beyond refresh, or is API-key authentication, execution fails and is recorded. Reauthenticate through `codex login`, then restart Rose. No model, mode or authentication fallback occurs.

The exact model is `gpt-6.1-terra`. Startup requires it in `model/list` and requires an advertised Fast tier (`fast`, or its supported `priority` alias). It sets `features.fast_mode=true`, the thread's `serviceTier`, and each new turn's `serviceTierForTurn`; it rejects a thread response reporting another model or Standard tier. The selected model/tier/authentication are logged. This checks the app-server's selected configuration; actual serving/latency still requires acceptance with the signed-in account.

References: [Codex speed settings](https://learn.chatgpt.com/docs/agent-configuration/speed), [authentication](https://learn.chatgpt.com/docs/auth), [Fast/priority naming](https://openai.com/api-fast-mode/). Local 0.163.0 generated JSON schemas confirm `dynamicTools`, `ephemeral`, `serviceTierForTurn` and managed `refreshToken` support. Phone tools use `item/tool/call` responses, not model-authored shell commands.

## Start on the Mac

Reuse the existing voice environment and models described in `prototypes/voice-phone-demo/README.md`. In that directory:

```sh
export PIXEL_CODEX_BIN=/absolute/path/to/codex
export ROSE_TRANSCRIPT_DIR=/absolute/private/path/rose-transcripts
# Keep the existing ROSE_CONFIG_DIR so phone pairing survives restart.
.venv/bin/python -m rose.app --brain codex --phone real
```

Open the loopback dashboard at `http://127.0.0.1:8091`, choose **Pair phone**, and scan its QR using **Pair with Rose** in the existing Rose Agent. Old clients' **Pair with Mac** entry uses the same protocol. Codex conversations transmit selected text/tool context and requested screenshots outside the private system; this is not intranet-only mode. There is no bulk private-data upload.

## Start on Linux/cloud

Use the same command with `--cloud-audio /private/audio.json --pair-host rose.tailnet-name.ts.net`. The advertised Tailscale MagicDNS/IPv4 host must already reach this backend's pinned TLS listener (default 8443). This flag only fills the existing QR host field; it creates no DNS, network route, certificate or proxy configuration. Pairing management/export stays on the loopback dashboard; use an already authorized tunnel for access. Keep the dashboard off the public listener.

Linux voice reuses `workspace/models/rose_cloud`'s `nomlx`, listener and Phonon providers. Supply existing pinned local assets and installed runtime dependencies (NumPy, SciPy, Pillow, SoundFile, FastAPI, Uvicorn, websockets, cryptography, PyTorch, transformers, onnxruntime, ptts). See that directory's README for model revisions and library requirements. Do not install the Mac MLX requirements on Linux. The loader verifies its existing asset pins and fails if they are missing; it does not download weights or revive vLLM/the historical cloud stack.

Example path-only audio config (replace every path with the operator's existing asset paths):

```json
{
  "listener": {
    "vad": "/models/silero", "turn": "/models/smart-turn",
    "final": "/models/parakeet", "stream": "/models/nemotron",
    "device": "cuda"
  },
  "speech": {
    "model": "/models/phonon",
    "voices": {"zoey": "/models/voices/NbpkqMVS3CJeq2j8.safetensors"}
  }
}
```

Mic mute remains input-only. The existing listener, speaker, echo guard and wire transport carry voice. Codex receives committed ASR, without speculative requests. Speech-start ducks then pauses playback; backchannels resume it, committed words interrupt the conversation. App threads continue independently. Linux's dashboard text box sends typed input directly (macOS `say` is unavailable there).

Conversation instructions keep short preferences, corrections and delegated choices within the active goal, while respecting an explicit cancellation or topic change. `python -m rose.eval.context --live` checks shopping follow-ups and a topic change against the signed-in Terra account with simulated phone tools only. It stores synthetic transcripts outside Git; it is not part of the offline suite or real-phone acceptance. Model behavior can vary between runs.

Local task progress also feeds Rose Agent's main native task card through its existing version-1 presentation contract. Parallel running tasks share a summary; when all finish, the latest result is rendered as bounded plain text. The card is read-only and does not call the separate Node backend's Watch/Stop routes. The backend publishes activity before slow phone operations and resends current scoped task snapshots after the events socket reconnects. This client-side card adapter requires an updated Rose Agent APK; restarting only the Python backend does not replace packaged WebView assets.

## Sessions, stop and transcripts

One conversation thread coordinates up to two independent app threads. On the real phone
with `--vision-control` (an opt-in; it needs `--brain codex --phone real`), Terra uses
screenshots and coordinate input through `VisionSession`: no accessibility-tree
inspection, node matching, semantic clicks, or node-bound typing. Each input names the
specific screenshot the model saw; it consumes that frame and returns a new image for
verification. Taps and swipes use the controller's existing `screen_gesture` command.
The additive `screen_text` command inserts literal text through the session's virtual
input device into the field the model focused visually, with an explicit replace/append
flag and commit disabled. Text is a single line: a line break could act as Enter, so
Rose and the controller both refuse one. The controller types only into an input-focused
editable field and refuses a password field. It requires the controller's `visionText=true`
capability; an older controller reports an update requirement and never falls back to
accessibility. Without `--vision-control` (the default) app threads keep the accessibility
tools and their send/purchase guard, with screenshots alongside the UI tree.

Final messages/posts, purchases and security changes remain owner actions under the
existing policy. Vision taps declare their effect and describe their target; the server
rejects final-action effects and, with the same send/purchase rule as the accessibility
path, a target that reads as a send, purchase or other final control. Both rely on the
model's visual interpretation, rather than an independent accessibility-label check.
Passwords and secrets remain owner input.
The OS retains its session, protected-screen and owner-control boundaries.
Task completion must include a structured `complete_task` result: a blocked or failed
goal becomes failed, even if the model turn completed normally. Success requires an
available screenshot and a successful latest observation/action; it remains the model's
visual judgment, not an assertion that a dispatched tap succeeded.

Each app is bound to one target; device-wide intent shortcuts are excluded, so timers and other actions use that app’s workspace UI; reads, commands and stop carry the controller session ID and task ID. Schema-4 `multiSession=true,maxSessions>=2` is required for this mode: older controllers are refused rather than replacing a workspace or falling back to the owner's visible screen. Native owner Stop is observed on that same binding and interrupts only its Codex thread. A command already dispatched can complete before stop; its outcome is retained, and unacknowledged cleanup is recorded as uncertain.

App threads and their workspaces are reused for follow-ups during a backend run. Reconnect rechecks each bound session. Stop latches against late tool calls. Reset starts a new conversation without deleting earlier logs. A backend restart explicitly creates new Codex threads, leaving prior transcripts available. Codex threads are **ephemeral** to avoid native rollout persistence of private reasoning or screenshots; prior transcripts are not silently uploaded or replayed as commands.

When a new app needs a workspace and both slots are reserved, the oldest idle, completed session is closed before starting the new app. Its task result stays in history; a retained session still supports follow-ups. Running tasks are never evicted. If both are running or stopping, Rose reports `app_sessions_busy`. An unconfirmed release reports `session_cleanup_unconfirmed` and blocks replacement until the operator checks the phone and restarts the backend.

If Android refuses a background launch with `target_in_foreground`, Rose says this RoseOS build cannot yet take over an app the owner can see, and that nothing was opened. This is a limit of the current platform patch (it refuses any app visible on the main display, including under Rose Edge), not a product rule: F06 replaces it with a takeover on the owner's request. Rose no longer suggests switching away and retrying, because on a build without that change a later Stop may not return a taken-over app with its state. The refusal stays in the final failed-task result; Rose does not move or control the owner's foreground app to work around it. A platform `target_in_other_session` refusal (the app shows on another display) says the app is open on another screen or workspace; the controller's own launch-time `app_in_other_session` reads like its start-time refusal. These codes count only for the start's own failed launch, never for an earlier error the controller still holds. Rose never takes the owner's Home, recents, assistant or screensaver task into her workspace: the controller refuses Rose Agent and the default Home app (`target_not_allowed`, reason `target_not_standard`) before choosing a slot or stopping anything, and the platform refuses any such task with `target_not_standard`.

**Copy to the phone clipboard (F07).** The conversation tool `copy_to_phone_clipboard(text)` copies exactly the text the owner asked for to the paired phone's clipboard, for pasting into another app; the same code serves the Mac and the cloud. `rose/clipboard.py` sends Rose Agent's `clipboard_write` phone command with the text and its UTF-8 SHA-256; the phone refuses a text that does not match (nothing is trimmed or normalized), writes it as standard plain text marked sensitive through its own default-device clipboard, and replies `copied` only after `setPrimaryClip` returned with its write app-op allowed (`verified: readback` when Android let Rose read back her own marked clip, which it allows only while Rose has focus; otherwise `write_accepted`, and only while no Rose Agent activity is on a virtual display, where Android silently drops such a write: then `clipboard_write_unconfirmed`; with Rose focused and nothing readable afterwards, `clipboard_write_failed`). A readback reads text only from a clip carrying Rose's own marker. If the bridge answered the call as cancelled (an interruption or its 60 s timeout, which it now reports as `tool_cancelled`), the real outcome is added to that conversation's first message after both are known; a result the bridge accepted is never repeated, no turn waits for it, and a reset drops it. Rose says it is copied only for that reply. Refusals (`empty_text`, `text_too_large` over 64 KB, `malformed_text`, `text_mismatch`, `clipboard_write_denied`, `clipboard_wrong_device`, `clipboard_unavailable`, `routine_busy`, `offline`, an older Rose Agent's `clipboard_unsupported`) wrote nothing; `clipboard_replaced`, `clipboard_write_unconfirmed`, `clipboard_write_failed` and a lost or late reply are unconfirmed. It is write-only: the clipboard is never read for Rose, and neither the phone's saved receipt, the tool result nor the transcript records the text (the transcript keeps its length; the owner's request itself is in the conversation as usual). `python -m unittest rose.tests.test_codex` and `ClipboardWriteTest` cover success, each failure, exact text and Mac/cloud/simulated-phone parity. The older Node backend (`workspace/rose-agent/server`) does not offer this tool.

On graceful shutdown, workspace cleanup runs before the HTTP/websocket listeners close and off the transport's event loop, so the phone can acknowledge targeted stops. Cleanup failures remain uncertain in the transcript; an abrupt kill can still leave workspaces requiring the owner's Stop control.

Completed app results retain the original task, thread and turn while waiting for speech. Announcements wait for the preceding utterance to finish, including speech that is still being synthesized. If the conversation reads a completed result through `list_tasks`, it reports that result in its reply and the separate queued announcement is suppressed. An announcement error is contained by the event loop so later conversation and app events can still be processed.

Storage is `ROSE_TRANSCRIPT_DIR/conversations.sqlite3`, default `~/.local/share/rose/transcripts/conversations.sqlite3`. SQLite commits every conversational event with full synchronous durability. Records include UTC timestamps, conversation/session/thread/turn identifiers, selected model and mode, messages, tool calls/results, durations, failures, stop requests and confirmed/uncertain outcomes. Raw reasoning events and audio/images/video are excluded. Known pairing URIs are removed in full. Structured sensitive fields, common credential text, private keys and data URLs are redacted before storage. Avoid dictating arbitrary unlabelled secrets: text redaction cannot identify every possible secret. No automatic expiry or deletion is implemented. Protect and back up this private directory; never commit it. An abrupt kill can leave a turn with no terminal event: its outcome is unknown, not successful.

Retrieve/export from the backend account:

```sh
python -m rose.transcripts --directory "$ROSE_TRANSCRIPT_DIR"
python -m rose.transcripts --directory "$ROSE_TRANSCRIPT_DIR" --conversation CONVERSATION_ID > /private/conversation.jsonl
```

The loopback-only API also exposes `GET /api/transcripts` and `GET /api/transcripts/{id}`. `/api/state` identifies the current conversation. `POST /api/tasks/{id}/stop` requires `{"conversation_id":"CURRENT_ID"}` to reject stale callers. Remote TLS exposes neither transcript exports nor management APIs.

## Validation and remaining acceptance

Automated validation uses synthetic data, simulated Codex/controller replies and temporary pairing identities. It is not model, microphone, GPU or phone acceptance. Run the checks from the repository root (Python environment must have the non-MLX test dependencies):

```sh
node --test workspace/rose-agent/server/codex-voice.test.mjs workspace/rose-agent/server/codex-provider-start.test.mjs
cd prototypes/voice-phone-demo
python -m rose.tests.test_codex
python -m rose.tests.test_vision_phone
python -m rose.tests.test_background
python -m rose.tests.test_remote
```

`test_codex` exercises real Python → Node → existing Codex adapter IPC against a simulated app-server, two controller slots, Calculator/Clock actions, independent cancellation, cancellation during startup and batched actions, native Stop, barge-in isolation, idle replies versus backchannels, queued task announcements across follow-ups, late completion, expired login, failures, restart/export, stale events after reset, follow-up task IDs, lost-start uncertainty, observation integrity and API isolation. The existing cloud speech/listener fixtures exercise audio transport and recognition scheduling without real models. Use the repository's pinned FastAPI 0.162.0, Uvicorn 0.54.0 and websockets 17.4 for the remote suite.

Recorded checks on November 4, from base `434df146289981c93f1ff84db7ab01ce37136f38`: bootstrap passed; 22 Codex Python integration tests passed; 46 adapter/client contract tests passed (including Java/Python signature cross-checks, none skipped); existing background, realphone, remote pairing and 22 cloud speech/listener tests passed. Tools: Node 24.19.0, Python 3.12.14, Java 21, Codex 0.163.0. `scripts/test-local.sh` initially stopped in Debian-package fixtures under inherited `umask 0077`. A rerun with creation mask `0022` scoped to the test subprocess passed those cases and reached a separate missing `/usr/sbin/mke2fs` dependency. No existing file permissions were changed. Hosted CI also exposed stale Mustang release-test fixtures; the missing release-allocation input defaults are corrected in this PR. Both broad hosted checks then stopped at `test_release_chain.Workflow.test_run_name_matches_the_successor_lookup`, which also fails on the untouched base. Other pre-existing release/device-contract failures are tracked in the PR evidence, separately from the voice checks.

Rose Agent compiled/DEXed/aligned successfully using disposable public-only connection fixtures. The unsigned APK SHA-256 is `e9a1730d8dbc8ad01617e410ce071b23d15306f30a4d6228a566cfbfae9f986c`. It has no production configuration or signature and is compilation evidence, not an installable release or device acceptance.

Still required on **each actual Mac/cloud deployment**: owner ChatGPT sign-in; Terra/Fast selection and a real reply; QR pairing on the existing phone; spoken task and barge-in; Calculator and Clock open simultaneously, act in each, stop only one; backend restart and retrieval of the preceding transcript; expired-login handling. Record model/tier, first-text/audio latency, failures and phone-visible outcomes. This implementation agent deployed nothing and created no operational credentials. Live Codex verification here was blocked by its state database location under read-only `/home/agent/.codex`; no permission change was attempted.
