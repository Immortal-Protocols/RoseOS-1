# Android app builds and signing

Run commands from the repository root. `bash scripts/bootstrap.sh` installs the
pinned Android SDK and the locked Rose Agent Node dependencies. It uses a writable
repo-local npm cache, since the container's default home cache may not exist.
`ANDROID_SDK_ROOT` may override the SDK root using the same archive layout.
`JAVA_HOME` is optional; Java 21 must expose `jdk.compiler`.

## Calculator baseline

```sh
bash workspace/pixel-calculator-agent/build.sh
```

Output: `out/calculator.*/calculator-unsigned.apk`, metadata and a checksum.
This existing recipe uses API 35, Build Tools 35.0.0, package
`dev.pixel.calculator.agent`, min API 30, target API 35. It reads no signing keys.

## Rose Agent

The launcher contains public HTTPS endpoint and server certificate pins. Create
your own backend configuration **outside Git**; the helper generates local TLS
material and ignored public Android assets. Use a stable private-network hostname
reachable from your phone for an actual deployment. Loopback is useful only for
compilation and local setup, not phone connectivity.

```sh
python3 scripts/configure-local.py \
  --config-dir /absolute/private/path/rose-agent \
  --endpoint https://YOUR_PRIVATE_BACKEND:9443
bash workspace/rose-agent/build.sh
```

The helper does not start a service or fetch provider keys. It preserves an
existing complete TLS key/certificate pair and refuses an incomplete pair.
Protect the private directory with mode 700 and private files with mode 600.
Do not point it at a working deployment merely to prepare a build: generated
public assets must match that deployment's verified certificate and endpoint.

The build stages inputs in `out/rose-agent.*/`, bundles the browser voice client,
compiles Java, creates DEX, packages resources, aligns the APK and checks ZIP
integrity. It does not alter tracked source or sign/install anything. A stale
upstream generated `Manifest.java` is excluded from the staging directory;
`aapt` regenerates resources and manifest constants for this build.

Outputs include `rose-agent-unsigned.apk`, `SHA256SUMS`, `apk-metadata.txt`,
`java-version.txt`, `source-revision.txt` and `source-status.txt`. A nonempty
source-status file means the build includes work outside the recorded commit.
The app is `dev.pixel.voice`, version code 305 (`305-home-beta`, the Rose Agent bundled in
the Frankel 2026102805 OS candidate), min API 29, target API 35. 304 (`304-app-beta`) is reserved
for the queued app-only candidate `a3d839e6`, and no other bytes may carry it. The app-only run
40676568143 built a signed 303, which was delivered through the authenticated catalog (14:39 UTC,
2026-10-28) but has no public GitHub release; 303 is consumed. Build 2026102804 carried the signed 302. The draft 2026102803 carried 301; the verified draft 2026102802 carried 300; the retained,
unpublished P1 build 2026102801 was 299; the published 2026102701 release
carried 298. The imported source and the staged
signed build were 297. Before distributing an
upgrade, compare the installed/released version and increase versionCode; do not
assume the source number is newer than a live APK.

## Signing an already-built APK

Signing does not require embedding cloud credentials in the build. The signer
accepts a private keystore and JSON config outside the repository. Config fields:
`keyAlias`, `storePasswordFile`, `keyPasswordFile`; its old `keystore` field is
ignored in favor of the explicit `--keystore` argument. Password-file overrides
allow resources fetched from Secret Manager to replace old machine-local paths.

```sh
python3 scripts/sign-apk.py out/rose-agent.RUN/rose-agent-unsigned.apk \
  --keystore /absolute/private/path/app.keystore \
  --config /absolute/private/path/signing.json \
  --store-password-file /absolute/private/path/store-password \
  --key-password-file /absolute/private/path/key-password \
  --output out/rose-agent.RUN/rose-agent.apk
```

Use the actual build directory in place of `RUN`. All signing inputs must be
owner-only files outside the repo. The password files' terminating line breaks
are handled by apksigner's `file:` password source; never pass passwords on a
command line. The script refuses existing output, checks alignment, signs with
V4 enabled, verifies the APK, and creates a receipt containing only artifact and
public certificate hashes. Supply `--expected-cert-sha256 FULL_PUBLIC_PIN` to
require a known full signing identity. A short prefix is identification context,
not a replacement for full certificate comparison.

Nathan supplied these resources in `projects/protocols-465723/secrets/`:

| Resource | Use |
| --- | --- |
| `PIXEL_APP_SIGNING_KEYSTORE` | Existing app signing identity |
| `PIXEL_APP_SIGNING_CONFIG` | Alias and historical password-file references |
| `PIXEL_APP_SIGNING_STORE_PASSWORD` | Keystore password file |
| `PIXEL_APP_SIGNING_KEY_PASSWORD` | Key password file |

Only retrieve named authorized resources. Resolve and record the chosen secret
version for reproducible releases. Use `gcloud secrets versions access VERSION
--secret=NAME --project=protocols-465723 --out-file=PRIVATE_FILE`, under `umask 077`;
never print binary payloads or password values. Remove temporary secret files
when signing completes. Do not generate replacement production keys to resolve
an access problem.

A signed APK is not evidence that the app runs correctly or that it can update a
particular installed app. Acceptance requires matching full certificate identity,
compatible package/version, a selected test device, pairing, a real task and
verification of the visible outcome. Installation is a separate step.

## Offline tests

```sh
bash scripts/test-local.sh
```

This selected suite tests device authentication/replay/revocation, recovery
routing, conversation state, session leases, background policy, command delivery,
metrics and guardian bridging. It also checks OS policy inputs with synthetic
pins. It deliberately does not discover/run `test-live-*` scripts, which require
configured devices/providers and can perform real actions. Additional imported
app/native tests require their own dependencies and explicit test targets.
