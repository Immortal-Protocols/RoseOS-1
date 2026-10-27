# Conversation home experiment

The baseline before this experiment is in `baseline-2026-10-11-before-chat/`.
It contains the exact APK pulled from the Pixel (version 79 / 0.71), a private source archive, and SHA-256 hashes. The archive excludes credentials, generated output, dependencies, and conversation data. The installed APK itself contains the existing pinned phone connection configuration; keep this directory private.

## Instant UI rollback

Open the three-dot **Home options** menu and select **Classic home**. This changes only the local `conversation-home` preference. It restores the prior launcher layout without removing app data or conversation history. To re-enable the experiment, long-press the **Pixel** heading on Classic home and choose **Try conversation home**.

The classic screen uses the original UI branch; the experiment removes the heading/icon, opens a conversation when the message field is used, and displays the shared text/voice timeline. Android Back leaves the conversation for the simple home view. Normal text use does not request or activate the microphone.

## Full implementation rollback

Do not uninstall the app or overwrite conversation/history data. Android may reject installing an older version number, so use the preserved source to produce a new version-numbered APK with the same signing key if a full binary rollback is needed. Restore the archived server source only after preserving the new files and coordinating a server restart. The instant UI toggle should be used for visual comparisons and ordinary experimentation.

## New data

`conversation-timeline.json` in the existing private server directory is a bounded shared journal for typed messages and recognized voice text. It starts with recent existing voice history; older typed history and diagnostic transcripts are preserved separately. Text exchanges are passed as context to an active ElevenLabs session, and a new voice connection receives the shared recent history. No audio is stored in this journal.

Tests: `node rose-agent/server/test-conversation-timeline.mjs` validates shared context, disk restoration, duplicate identity handling, bounded retention and private file permissions. Live UI/rollback checks are recorded below after completion.

## Verified on Pixel

Installed version 80 / 0.72. The same Home intent opens the new screen. Submitted a harmless text test through its visible Send button and received the requested reply in the journal and chat view with no active voice session. Fixed dark-mode bubble contrast and delayed scrolling until after layout; latest request/reply are visible above the keyboard. Switched to Classic home via Home options, confirmed the original heading/voice button/composer layout, then long-pressed its heading and switched back to Conversation home. Message history survived both switches. Live mid-call text/voice transitions still need a user conversation test; shared-context/persistence tests pass locally.

Camera work is separate: Pixel Camera Preview is installed alongside the stock camera; the live Fill layout is verified, with the Full framing toggle still awaiting an uninterrupted device check. Status-bar waveform work remains emulator-only; it has not been installed on the physical phone.

Typography iteration: bundled existing OFL-licensed Nunito from Pixel Podcasts, using 800 weight for 22sp message/composer text. Added simple 24dp raised message surfaces, 70dp composer and 54dp yellow Send button. Classic home keeps its prior typography. Phone screenshot verified the new weight, dark-mode contrast, current replies, and composer. Keyboard/layout anchoring now tracks layout changes while following the latest message and preserves deliberate user scrolling.
