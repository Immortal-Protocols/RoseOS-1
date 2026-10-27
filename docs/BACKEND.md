# Rose Agent backend

The app and backend are separately built and deployed. Renaming the app does not
supply provider accounts, a server endpoint, Codex authentication or enrollment.
The imported server uses the existing `PIXEL_*` variable names for compatibility.

1. Run `bash scripts/bootstrap.sh`. For optional modules needing the root `sharp`
   dependency, also run `npm ci --prefix workspace --cache "$PWD/.tools/npm-cache"`.
2. Prepare your private configuration/public APK assets as in [APP-BUILD.md](APP-BUILD.md).
3. Install and authenticate your own compatible Codex CLI under the backend service
   account. The historical handoff tested Codex 0.163.0; this import has not run a
   live backend and does not claim compatibility with every CLI version.
4. Prepare the model catalog using your own cache:
   `python3 scripts/prepare-model-catalog.py --config-dir /absolute/private/path/rose-agent`.
5. Supply authorized provider credentials through private files. The imported
   server expects `openai-api-key` and `openai-podcast-api-key` in that config
   directory at startup. Optional providers need their own separate setup.

Start only after configuration and authorization are ready:

```sh
export PIXEL_VOICE_CONFIG_DIR=/absolute/private/path/rose-agent
export PIXEL_CODEX_BIN=/absolute/path/to/codex
export PIXEL_FFMPEG_BIN=/absolute/path/to/ffmpeg
node workspace/rose-agent/server/server.mjs
```

The default HTTPS listener is port 9443. Use an approved private network path;
never expose Codex app-server or ADB directly to the internet. The backend owns
its private state; keep logs and transcripts out of the source tree. No systemd
service, firewall rule, DNS record or provider deployment is created by this repo.

Create a pairing invite with
`node workspace/rose-agent/security/pair.mjs invite`, using the same config
variable. Treat the invite as a credential; do not commit or publish it. On the
phone, enter your own endpoint and verified certificate fingerprint, then pair.
Verify a harmless end-to-end task before relying on the service.

The imported backend contains optional Mac integrations, external app renderers
and provider experiments. Their presence is not evidence that they work on Linux.
The native Android app import includes required clipboard/voice/push dependencies;
it does not import every larger app fork or deploy optional services. See the
historical [Linux handoff](upstream/LINUX_BACKEND.md) for context, translating old
`workspace/pixel-voice/` paths to `workspace/rose-agent/`. Historical paths and
claims are not current deployment instructions or permission to contact old hosts.

Background control requires the matching custom OS/controller and a pinned app
allowlist. The imported broad-control prototype and locked-control options are
not the RoseOS default; see [OS-RELEASE.md](OS-RELEASE.md).
