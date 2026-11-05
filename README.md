# RoseOS

RoseOS is a GrapheneOS-based Android system built around **Rose Agent**, a
voice-first launcher and assistant. Talk to Rose to ask questions, work through
ideas and delegate phone tasks; a server on your Mac supplies local models or
Codex as the assistant's brain. You can install Rose Agent on an existing Android
phone, or use the full RoseOS for OS-level integration and separate background
app workspaces that let you keep using your phone while Rose works. Progress,
a workspace viewer and Stop controls keep the work visible and under your control.

This is the open-source RoseOS source and build repository. Original RoseOS
material is available under the **MIT License**, except where a file or component
states another license. You may use, modify and redistribute MIT-licensed code,
including in commercial closed-source products, while retaining the required
license and copyright notices. See [LICENSE](LICENSE) and
[third-party notices](NOTICE.md).

Public, signed Pixel 10
(`frankel`) OS artifacts belong in
[Immortal-Protocols/pixel-ota](https://github.com/Immortal-Protocols/pixel-ota).
That release channel is separate from source and experimental build records.
Keep credentials and private signing materials outside this repository.

## Install Rose Agent without the full OS

On Android 10 or newer, download the signed `dev.pixel.voice-*.apk` and its
`SHA256SUMS` from the [Rose Agent releases](https://github.com/Immortal-Protocols/pixel-ota/releases?q=rose-agent&expanded=true),
verify the APK's checksum, allow installation from the browser or file manager
you used, and open the APK to install it. Start your Mac server as described
below, open `http://127.0.0.1:8091` on the Mac and choose **Pair phone**; in Rose
Agent, choose **Pair with Rose** (called **Pair with Mac** in older builds) and
scan the QR code. Keep both devices on the same Wi-Fi or your Tailscale network,
grant microphone access for voice, and enable **Rose Agent phone controls** in
Android's Accessibility settings if you want phone actions. Regular Android can
use supported accessibility actions on its visible screen; separate background
workspaces require RoseOS. See [pairing and connectivity](prototypes/voice-phone-demo/REMOTE.md)
and [building your own APK](docs/APP-BUILD.md).

## Set up the Rose server on your Mac

On an Apple-silicon Mac with Python 3 and enough memory for your chosen local
models, clone this repository, run `cd prototypes/voice-phone-demo`, then run
`bash scripts/setup.sh` once to create the Python environment and download roughly
22 GB of model files. For the local Qwen model, start
`bash scripts/rose.sh --phone real`. For Codex, install Node 22+ and a compatible
Codex CLI, run `codex login` and select **Sign in with ChatGPT**, then start
`bash scripts/rose.sh --brain codex --phone real`; this uses the account's supported
Terra Fast model while speech processing stays local. Open `http://127.0.0.1:8091`
and pair your phone. The current Codex phone-task mode requires RoseOS's compatible
multi-session controller; it does not fall back to controlling a stock phone's
visible screen. See the [local-model setup](prototypes/voice-phone-demo/README.md)
for hardware and model details and the [Codex setup](docs/CODEX-VOICE.md) for CLI,
account and model requirements.

## Flash RoseOS onto your Pixel

Use a factory-install release explicitly matching your Pixel's model and codename
from [the OS releases](https://github.com/Immortal-Protocols/pixel-ota/releases),
and follow that release's `FIRST-INSTALL.md`; the documented Pixel 10 target is
`frankel`. Back up your phone first: unlocking and a factory install erase its
data. Install current Android Platform-Tools on your computer, enable Developer
options and OEM unlocking on the phone, connect it by USB in bootloader mode,
confirm its codename with `fastboot getvar product`, and unlock with
`fastboot flashing unlock`, confirming on the phone. Verify the matching factory
archive against its published checksums and authenticity instructions, extract it,
and run its `flash-all.sh` (`flash-all.bat` on Windows). An `*-ota_update-*.zip`
is an update package, not a fresh-install archive; if a build provides only an
OTA, wait for an appropriate factory installer. Factory installation is
experimental and availability varies by build; see the [current Pixel 10 factory-install guide](docs/FRANKEL-FACTORY-INSTALL.md).
Only consider relocking after a successful boot and the exact build's verified-boot
instructions explicitly support it.

## APK builds and sharing

Experimental APK builds are stored in [this repository's Releases](https://github.com/Immortal-Protocols/RoseOS-1/releases).
**Upload from GitHub Actions using its repository-scoped token; download with
the Releases page or `gh release download`.** Direct asset uploads from the current agent
container return HTTP 401 even though repository API calls work. The tested CI
route does not require a personal access token or sharing credentials between agents.
Library copies are optional; approved public OS releases remain in `pixel-ota`.

### Run the verified storage test

The manual workflow builds an **unsigned Calculator APK** without signing secrets:

```sh
gh workflow run apk-storage-test.yml --repo Immortal-Protocols/RoseOS-1 --ref main
gh run list --repo Immortal-Protocols/RoseOS-1 --workflow apk-storage-test.yml
```

Each run creates a unique `apps-storage-test-RUN_ID-ATTEMPT` tag at its source
commit. It uploads the individual APK, APK metadata, Java version, build receipt
and relative-path `SHA256SUMS` to a draft. It downloads all assets and compares
them byte-for-byte before publishing an experimental prerelease (not Latest),
then verifies another download. Failed prepublication verification leaves the
draft unpublished.
The job has only `contents: write`, a 15-minute timeout and no signing credentials.

To retrieve a successful build, replace `TAG` with its exact release tag and use
a fresh destination directory:

```sh
gh release download TAG --repo Immortal-Protocols/RoseOS-1 --dir downloads/TAG
(cd downloads/TAG && sha256sum -c SHA256SUMS)
```

Verified on 2026-10-26: [workflow run 40451235322](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/40451235322)
passed. Its [then-private prerelease](https://github.com/Immortal-Protocols/RoseOS-1/releases/tag/apps-storage-test-40451235322-1)
was independently downloaded into the agent workspace; every checksum passed,
and APK ZIP integrity and alignment passed. APK SHA-256:
`ae07cfa8891ce4e0d626631c4a3f1f23fd2d2cf677a9f6f815984f29fd82157a`.
This proves artifact storage/retrieval, not app installation or runtime behavior.
The unsigned test APK is not a signed installable release.

### Build records

Use a new tag for each build and preserve uploaded bytes. Receipts must record
source commit/worktree state, package/version, tool versions, signing status,
full public certificate fingerprint (or explicitly unsigned), actual checks and
runtime-test limits. Clearly label experimental, tested or approved status.
Signed APK workflows must additionally verify the expected full signing identity;
this unsigned storage test does not implement signing. Never upload private keys,
passwords, tokens or private runtime configuration.

## Start here

On x86_64 Linux, with Git, Python 3, curl, unzip, zip, SHA-256 tools, Java 21 and
Node/npm installed:

```sh
git clone https://github.com/Immortal-Protocols/RoseOS-1.git
cd RoseOS-1
bash scripts/bootstrap.sh
bash scripts/test-local.sh
bash workspace/pixel-calculator-agent/build.sh
```

These commands configure local tools, run offline tests and build Calculator
unsigned. They do not authenticate to Google, read signing secrets, install an app
or start a cloud resource. SDK licensing applies to the Google downloads.

To build **Rose Agent**, configure your own public backend endpoint and certificate
assets first. See [app builds and signing](docs/APP-BUILD.md). For the cloud agent
container, read [rose-workspace setup](infra/rose-workspace/README.md); for the full
OS, read [OS build and release](docs/OS-RELEASE.md). Setup and build commands are
intentionally separate from deployment.

## Repository map

| Path | Purpose |
| --- | --- |
| `workspace/rose-agent/` | Rose Agent Android app, backend, voice, pairing, native workers and tests |
| `workspace/pixel-calculator-agent/` | Existing small app build baseline; package and source provenance preserved |
| `workspace/pixel-clipboard/` | Shared clipboard implementation used by Rose Agent |
| `workspace/experiments/` | Selected live shared dependencies and test fixtures; historical directory name |
| `workspace/pixel-hidden-world/assets/` | Data dependency loaded by the imported backend |
| `os/pinned-manifest.xml` | Exact GrapheneOS/AOSP project revisions for the imported source baseline |
| `os/patches/`, `os/overlays/`, `os/background-control/` | Framework/SystemUI changes and background controller sources |
| `os/reference-scripts/` | Historical original-machine scripts; **not** current Frankel release entry points |
| `scripts/` | Bootstrap, unsigned builds, signing, policy validation and source preparation |
| `infra/rose-workspace/` | Environment identity, tool paths, storage, network and access instructions |
| `docs/` | Current operational instructions and verification record |
| `docs/upstream/` | Historical source-handoff evidence, unchanged; not proof of RoseOS acceptance |
| `.tools/`, `out/` | Ignored disposable tools and isolated build artifacts |

The monorepo owns our product code, integration and recipes. Android's large
multi-repository source tree is reconstructed from the pinned manifest on a
separate OS builder. Do not copy a full `.repo` tree, vendor downloads, build
outputs or signing keys into this Git repository.

## Naming and compatibility

The former `workspace/pixel-voice/` is now `workspace/rose-agent/`, with the app
label and product text changed to **Rose Agent**. Existing Android package IDs,
provider authorities, signature permissions, wire protocol names and `PIXEL_*`
configuration variables remain compatible with the existing signing/update
chain. For example, the launcher remains `dev.pixel.voice` and the controller
remains `dev.pixel.backgroundcontrol`. A namespace migration would need a
coordinated app/OS/backend and data-migration plan; a display-name change does not
silently perform one.

GrapheneOS project names, device codenames and copyright/license notices remain
upstream identities. RoseOS is a derivative; our releases must be labeled and
verified as ours. The imported baseline is GrapheneOS `2026100600` / Android 17,
not a claim that this older baseline is suitable for a current stable release.

## Workflows

- [Build and sign Android apps](docs/APP-BUILD.md): configure public assets, build,
  verify, sign with external private files, and retain receipts.
- [Run the private backend](docs/BACKEND.md): provider setup, pairing and optional
  integrations. The repository does not contain a ready-authenticated service.
- [Build and release RoseOS](docs/OS-RELEASE.md): inspect the existing builder,
  reconstruct exact source, prepare policy, build, sign, validate and publish.
- [Use rose-workspace](infra/rose-workspace/README.md): startup, prerequisites,
  cache paths, vault boundaries and current connectivity limitations.
- [Integrate Rose Agent with the OS](docs/OS-INTEGRATION.md): status tray, agent
  workspace, viewer/Stop contract and existing-phone update compatibility.
- [Check what is actually verified](docs/VALIDATION.md): fresh results versus
  inherited evidence, pending acceptance and concrete blockers.
- [Verify an alpha or beta build](docs/VERIFY.md): artifact and source inspection,
  handback-gated Cuttlefish runtime capture, Fold-reference screenshot comparison
  and the per-build checklist. Records live under `qa/verification/`.

See [structure decisions](docs/ARCHITECTURE.md) for why these boundaries were chosen.

## Source provenance and contribution

Rose Agent and OS changes were imported from
`Immortal-Protocols/pixel-workspace@9f1261dd5aac9227ea4b16d3817f394bffa1cf5c`.
`scripts/rose-import.json` maps each imported path to its original Git blob;
`scripts/calculator-import.json` retains the earlier Calculator import record.
The exact imported OS revisions are in the manifest. Preserve existing source
notices and license files; this repository does not grant a new license to
upstream code.

New contributions must use the applicable license for the files or components
they change. Preserve upstream copyright, attribution and license notices.

Before editing, fetch the current branch and inspect local changes. Use isolated
checkouts for concurrent work. Run the tests relevant to the change and record
source revision, tool versions, target, result and artifact hashes. Never claim
phone execution from compilation, or hardware/verified-boot acceptance from an
emulator boot. Nathan authorized this integration to be committed and pushed to
main; ordinary team work follows the project’s engineering/review workflow.

Keep keys, passwords, tokens, paired-device identities, conversations, personal
memory and cloud CLI state outside Git. Never run historical deployment or live
test scripts just because they were imported. Cloud costs, infrastructure access
and release publication require the scope of the current task, not assumptions
from a previous agent’s credentials.
