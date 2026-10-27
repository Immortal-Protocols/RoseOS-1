# Optional voice-match trial

Opt-in only: disabled until the owner records three samples, saves a guest passphrase, and turns it on from the unlocked Pixel. The existing Bluetooth setup and provider adapters are unchanged.

## Flow

The current connection exposes reconnect and unfold signals, not reliable worn-state events. This trial therefore checks each new voice session, including automatically started glasses sessions. It does not recheck while an active session continues, and cannot prove who is wearing the glasses merely by hearing a recognized voice nearby.

The isolated SpeechBrain ECAPA worker compares 16 kHz mono audio on the Mac. Enrollment keeps three embeddings, not audio. Incoming comparison audio is processed in memory. Initial experimental thresholds: median reference similarity >=0.65 grants owner; >=0.45 asks once more; otherwise starts the guest flow. These values require validation on the owner's glasses recordings and are not production authentication guarantees.

An unfamiliar person is asked whether the owner lent them the glasses. If yes, they are asked to obtain his passphrase and say it. OpenAI transcribes that brief response separately from the conversation. No passphrase text or audio is written to the conversation, memory or audit log. The server stores a salted scrypt hash. A guest grant opens a fresh conversational session without owner history, memory, or phone/account tools. It expires after four hours or is discarded by the client when ending a call.

Owner Skip is a button on the unlocked Pixel. Management requests require an owner-action HMAC sent only through the native UI bridge, which checks that an owner UI is visible and the device is unlocked. Normal WebView requests cannot call management routes. The device's pre-existing pairing key remains the trust boundary; this is an experimental convenience gate, not an OS lock or anti-spoofing system.

## Runtime

Run `pip install -r requirements.txt` in `.venv` for the Python worker. Node resolves the existing server's OpenAI SDK via the local `node_modules` symlink. On startup the server loads the worker in the background. Model files, private config and audit events live under `~/.config/pixel-voice/voice-match`, excluded from Git. `prepare-prompts.mjs` caches short Marin greetings once so they need no per-check generation.

## Rollback

The **Turn off — normal voice** button cancels the check and restores normal behavior for the next session. `./rollback.sh` also disables it without changing Bluetooth or uninstalling the app. A pre-trial APK and the touched integration sources are preserved in `rollback/` (not committed). A full restore must also restore the prior server integration or leave the gate disabled.

## Verification

- Unit tests exercise off-by-default, enrollment requirements, code hashing, guest scopes, expiry, Skip cancellation, and privacy filtering.
- Actual SpeechBrain sample comparisons: same speaker 0.732, different speaker 0.084; local inference 22–33 ms. This is not a glasses accuracy estimate.
- A synthesized sample verified the actual guest passphrase transcription route. Temporary audio was deleted afterward.
- Live enrollment and wearer recognition require the owner to record their three samples; no profile is silently derived from old transcripts or background speech.
