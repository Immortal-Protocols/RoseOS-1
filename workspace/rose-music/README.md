# Rose Music

An Android music app whose library is hosted on a Chariot agent. People browse
the album, stream any song, and download songs to keep and play offline.

- `android/`: the app (Kotlin, Jetpack Compose, Media3), package `dev.rose.music`.
- `hosting/publish.py`: builds the library site and publishes it to the agent.
- `songs/generate.py`: the launch album, ten instrumentals generated with
  ACE-Step 1.5 XL Turbo. `songs/covers.py` makes the cover art with Z-Image Turbo.

Audio, covers and build outputs stay out of Git.

## How it works

The Chariot agent `rose-music` serves a static site with BusyBox `httpd` on
`127.0.0.1:8090`. Chariot's agent-app gateway publishes it at
`https://agent-<agent id>.apps.chariots.sh`:

| Path | What |
|---|---|
| `/health` | `ok`, Chariot's health check |
| `/catalog.json` | the library, schema `rose-music-catalog/v1` |
| `/tracks/NN.mp3` | audio, with HTTP range support for streaming and resumable downloads |
| `/covers/NN.jpg`, `/covers/NN.thumb.jpg` | 1024 px art and 320 px thumbnails |

Each catalog track has its byte size, SHA-256, BPM, key and 160 loudness peaks.
The app draws the waveform scrubber from those peaks.

The app:

- Fetches the catalog, caches it, and keeps working offline with saved songs.
- Streams any song. A saved song plays from its local file.
- Downloads two songs at a time. Downloads resume with `Range` and are kept
  only when the SHA-256 matches the catalog.
- Rejects catalog paths that are absolute or that leave the library.
- Plays in the background through a Media3 `MediaSessionService`, with a
  notification, lock-screen and headset controls.
- Shows two panes (library and player) when the window is at least 720 dp
  wide, as on an unfolded Galaxy Z Fold. On a narrow screen it uses a mini
  player and a full-screen player.

### Agent sleep

Chariot hibernates idle agents, and an HTTP request does not wake an agent or
restart its server. The `rose-music` agent's idle window is therefore set to
the maximum, 90 days (`chariot hibernate-after set 90:00:00 --agent rose-music`).
If the agent sleeps anyway, the app shows "Library is waking up", keeps
playing saved songs, and retries every 15 seconds. Running `publish.py` again
wakes the agent and restarts its server.

## Publish the library

Needs the Chariot CLI (logged in), `ffmpeg` and `ffprobe`.

```sh
python3 workspace/rose-music/hosting/publish.py --songs ~/rose-music-songs --agent rose-music
```

The script uploads the site to an incoming folder and swaps it in whole. It
restarts the server with Chariot's published launcher (the same text and
SHA-256 the app builder pins), republishes `app.json`, and then checks the
public URL. `--dry-run` builds the site locally without uploading it.

## Generate the album

On an Apple-silicon Mac with an ACE-Step 1.5 checkout (`uv sync` done):

```sh
cd ~/ACE-Step-1.5
uv run python /path/to/workspace/rose-music/songs/generate.py --out ~/rose-music-songs --only 1
```

Run one track per process (`--only N`). Generating several XL Turbo tracks in
one process, or alongside other large models, ran out of memory on a 64 GB M4
Pro during VAE decode. The script records the model, ACE-Step commit, prompts
and seeds in `generation.json`. The style prompt describes the sound
(technical clean-guitar math rock: tapping, hybrid picking, trap drums) and
does not name a band.

## Build and install

```sh
cd workspace/rose-music/android
JAVA_HOME=/path/to/jdk-21 ./gradlew :app:testDebugUnitTest :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The library address is built in from `roseMusic.catalogBase`. It defaults to
the `rose-music` agent; override it with `-ProseMusic.catalogBase=https://…`.
Debug and release builds are signed with the local debug key; no release key
is involved.

Fonts: Space Grotesk and Inter, both under the SIL Open Font License
(`android/third_party/fonts/`).
