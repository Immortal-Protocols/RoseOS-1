# Run Pixel Voice on Linux

The current backend runs on a headless Ubuntu VM. The phone connects directly to
the VM over pinned HTTPS; a Mac does not forward the request. The agent controls
Android's virtual display on the phone, not a Linux desktop. No Linux GUI or GPU
is needed for this setup.

This is the same backend and pairing mechanism as [BACKEND.md](BACKEND.md).
Keep the server under your own Linux account with your own provider credentials,
TLS key, Codex sign-in, and device enrollment. This repository does not contain a
copy of the original deployment's credentials or conversations.

## Prepare the server

The live Linux test used Ubuntu 24.04 amd64, Node.js 26.12.1, and Codex CLI 0.163.0.
These are tested versions, not a promise of compatibility with every later CLI.
Use a supported patched Node release. Install Git, Python 3, OpenSSL, FFmpeg, and
`libatomic1` with the distribution package manager, plus Node/npm and Codex CLI.
The [official CLI guide](https://developers.openai.com/codex/cli) covers installation;
the [authentication guide](https://developers.openai.com/codex/auth) covers headless
sign-in. Run Codex once under the service account before starting Pixel Voice.

From this repository's root:

```sh
npm ci --prefix workspace
npm ci --prefix workspace/pixel-voice/server
python3 scripts/configure-local.py \
  --config-dir "$HOME/.config/pixel-voice" \
  --endpoint https://YOUR_SERVER_NAME:9443
python3 scripts/prepare-model-catalog.py
```

The last command creates a private catalog from **your own** Codex model cache,
without its account metadata. It never uploads the cache. If the CLI stores that
cache elsewhere, pass `--cache`; do not use another person's catalog.

Add your own `openai-api-key` and `openai-podcast-api-key` files to the private
configuration directory using a local editor or your secret manager. The current
server initializes both clients at startup. Keep the files readable only by the
service account. For API-authenticated task work, put your task key in a separate
private file and set `PIXEL_CODEX_API_KEY_FILE` to its absolute path. To use the
CLI's existing account instead, omit that variable; verify the account before
running tasks. Live voice and the task model are separate connections.

Start in the foreground first:

```sh
export PIXEL_CODEX_BIN="$(command -v codex)"
export PIXEL_VOICE_CONFIG_DIR="$HOME/.config/pixel-voice"
export PIXEL_FFMPEG_BIN="$(command -v ffmpeg)"
node workspace/pixel-voice/server/server.mjs
```

The default HTTPS listener is port 9443. Make that port reachable from the phone
using your chosen private network or a narrowly configured cloud firewall. Use a
stable address. Do not expose Codex app-server, ADB, or the local voice helper
ports to the internet. Pixel Voice starts Codex app-server locally over standard
input/output; it is not a public remote shell service.

## Connect the phone

Build Pixel Voice with the generated public endpoint/certificate assets, or use
its **Server connection** settings to enter this server's address and certificate
fingerprint. That screen retains the previous connection as a fallback. The
fingerprint must come from your server certificate, not the original deployment.

Create an invite with `node workspace/pixel-voice/security/pair.mjs invite` on the
server, then enter it on the phone's pairing screen. Complete a normal Pixel Voice
voice or typed request and check both its actual result and server logs. To use
the PiP/split background workspace, finish [BACKGROUND_CONTROL.md](BACKGROUND_CONTROL.md)
and select this phone's own enrollment ID in the private background configuration.
Do not copy someone else's pairing state.

## Keep it running

After foreground validation, copy [the example user service](../deploy/pixel-voice.service.example)
to your systemd user directory. Replace the two executable/repository paths.
Create `~/.config/pixel-voice/service.env` with the same absolute-path variables
used above, then enable the user service. The service's environment file contains
paths, not inline secret values. For a service that survives logout, enable user
lingering through the VM administrator. Verify a VM reboot before relying on it
unattended; that complete reboot test has not been claimed for this export.

## Optional DeepSeek app worker

The current live cloud test used DeepSeek Flash through Baseten for app tasks and
OpenAI for live voice. The gateway code is included, but it needs your own Baseten
key in `baseten-live-api-key`, a private `baseten-trial.json` containing
`{"enabled":true}`, and a catalog containing the worker template model. Select
the worker in Pixel Voice after restarting and verify a normal phone task. No
local model weights or GPU are used. Provider calls have their own costs.

## What still belongs elsewhere

BlueBubbles/iMessage still needs its configured Mac server. Mac screen control,
the native Mac Messages bridge, clipboard helpers, Calendar/Gmail CLI adapters,
and macOS launch agents are not made Linux services by this update. Android app
control and voice have been exercised on Linux; those optional Mac integrations
must be configured separately or left unused.

Fast native tools may finish without opening an app window. The viewer appears
when a task opens a background app. This is deliberate current behavior; the
proposed change to show every fast-tool app is not enabled in this export.
