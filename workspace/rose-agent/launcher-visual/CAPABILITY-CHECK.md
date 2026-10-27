# Launcher capability wiring

The Mac task agent receives the short capability note from capability.mjs at initialization and on current task requests. The voice provider prompt is unchanged. Codex initialization copies the publisher and README into ~/.local/share/rose-agent/launcher-visual/ alongside existing phone tools so background execution does not need Documents access.

Verified on 2026-10-12 through the normal /voice/task entry point using gpt-6-luna: it read the installed README and invoked the installed control.py status command. The request completed and reported the existing card visible. This check did not publish or clear content. All three launcher command tests and JavaScript syntax checks passed.

The server/codex changes are saved in backend-integration.patch. Pre-change originals are in ~/.config/pixel-voice/rollback/before-launcher-capability-20261012-131500. To remove this capability, remove only its import/injection and copier changes, then restart the Mac server. Card display can independently be disabled from Home options without changing the agent or voice provider. No phone APK update was required for capability wiring.
