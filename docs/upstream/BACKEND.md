# Backend and pairing

This server runs on macOS or headless Linux. See [Linux cloud setup](LINUX_BACKEND.md)
for the tested VM path and optional systemd service. Native Mac integrations remain optional.

## What you need

The source lives in `workspace/pixel-voice/`. Its HTTPS server, task-agent integration, phone tools, memory modules, and shared clipboard code are included. Modules under `workspace/experiments/` that the server imports are also included.

The service expects a private directory (default `~/.config/pixel-voice`). API keys, TLS private keys, local helper tokens, device enrollment records, transcripts, memories, and provider/account state live there. No original private directory is included.

Install npm dependencies in the workspace and the Pixel Voice server folder. Set `PIXEL_CODEX_BIN` to your compatible Codex executable; the prototype uses the Codex app-server protocol. Protocol compatibility must be verified on your installation. `PIXEL_CODEX_API_KEY_FILE` selects API-key authentication for the task worker; a missing/invalid key must not silently switch accounts. Run `scripts/prepare-model-catalog.py` after your own Codex sign-in to generate the private catalog used by API/provider startup.

## Provider setup

Supply your own OpenAI key. Optional narration/transcription and other integrations may use separate keys. Mail has its own on-phone key entry and model settings. The current implementation expects several private configuration files; the initializer creates safe local defaults and local random helper tokens but does not provide provider credentials.

Optional Mac Gmail/Calendar adapters read `google-accounts.json`, an explicit alias-to-address mapping, and use your own separately authorized Google Workspace CLI configuration. Pixel app actions remain the default; Mac alternatives are explicit.

## Pairing

Generate your own TLS certificate and set its public endpoint/pin in the phone's connection assets before building. Keep TLS and Android private keys private. Use `node workspace/pixel-voice/security/pair.mjs invite` to create a one-time code, then pair through Pixel Voice's normal screen. See the module's security README for the authorization model.

Test the connection and a harmless read-only action before enabling additional integrations. Do not copy the original developer's paired-device list or replace the secure connection with a hardcoded token.

## Optional local integrations

`workspace/pixel-messages/mac/` contains the native Messages bridge. BlueBubbles remains a separate upstream application/service to install and configure independently. Other optional integrations may require their own macOS permissions and accounts. Those permissions are not granted by downloading this repository.

## First-start checklist

Run the setup commands in SETUP.md. Add your own provider keys to the private directory: `openai-api-key`, `openai-podcast-api-key`, and any optional service keys you intend to use. The current server initializes the podcast client on startup, so that key file must exist even before podcast use. Never put key values in a shell command saved to shared history or in Git.

Set `PIXEL_VOICE_CONFIG_DIR` to that private directory and `PIXEL_CODEX_API_KEY_FILE` to your task API key file. Set `PIXEL_CODEX_BIN` to the compatible executable. Start with `node workspace/pixel-voice/server/server.mjs` from the repository root. This is a foreground development process; installing a launch service is a separate deployment choice. Verify pairing, a read-only phone request, and the desired provider/account before relying on unattended operation.

The checked-in defaults are source templates, not a copy of the original deployment. Current voice configuration and all account data are intentionally absent.

Use the default private directory for a full runtime deployment: some companion command-line tools still resolve it from the home directory. Alternate initializer directories are useful for isolated build verification, but are not yet a fully portable multi-instance deployment configuration.

## Background app execution

Use [Background app control](BACKGROUND_CONTROL.md) for the additional OS/controller requirements and the private `background-phone.json` setting. Default configuration remains off until the recipient pairs and explicitly selects their own compatible phone. The phone, controller and backend updates are all included; the recipient supplies their own keys, provider configuration and device-specific OS build.
