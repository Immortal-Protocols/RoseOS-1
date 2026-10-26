# RoseOS: first cloud app build

The first sample is Calculator Agent, imported without source changes from
`Immortal-Protocols/pixel-workspace` revision
`9f1261dd5aac9227ea4b16d3817f394bffa1cf5c`. Paths and package identity are
preserved. `scripts/calculator-import.json` records the original Git blob IDs.
This is an app-build baseline, not an OS image or release.

## Build an unsigned APK

Requirements: x86_64 Linux, Bash, Python 3 (for provenance verification), curl,
unzip, zip, sha256sum, and Java 21 with the `jdk.compiler` module. This run used
OpenJDK 21.0.12.1. No account or signing credentials are needed.

From the repository root:

```sh
bash scripts/setup-android-sdk.sh
bash workspace/pixel-calculator-agent/build.sh
```

The setup script downloads Android platform 35 revision 2 and Build Tools 35.0.0
from Google's SDK repository and verifies pinned SHA-256 hashes. Android SDK
license terms apply. Tools live in ignored `.tools/`; `ANDROID_SDK_ROOT` can
select another location using the same archive layout. `JAVA_HOME` is optional.
No system packages or environment security settings are changed.

Each build writes a fresh ignored `out/calculator.*/` directory containing the
unsigned APK, SHA-256 checksum, APK metadata and Java version. The build compiles
Java, creates DEX, packages resources and checks ZIP alignment. It does not
generate keys, sign, install, start a backend or deploy anything. Bit-for-bit
reproducibility across builds has not been established.

## Verified in rose-workspace

On 2026-10-26, the unsigned build completed with SDK 35, Build Tools 35.0.0 and
OpenJDK 21.0.12.1. ZIP integrity, DEX presence, alignment, application ID
`dev.pixel.calculator.agent`, version 1.2 (3), minimum API 30 and target API 35
were checked. `apksigner verify` rejected the unsigned artifact as expected.
Artifact SHA-256:
`b3aa7769ea9fb0323e921d428eca72d7f3674e3ad467874b448a14dd96217d0d`.

No emulator or physical-device execution has been performed. Signing awaits
Nathan's approved key delivery and signing workflow. The original exported
agent provider is preserved; this build does not certify its authorization
policy or suitability for production installation.

The upstream build script depended on SDK/JDK compatibility links and
`workspace/mono-reminders/development.keystore`. The replacement build uses
explicit tool paths, writes generated files outside source directories and
omits signing entirely. No upstream signing material or generated artifacts
were imported. Existing source notices remain unchanged; no new license grant
is asserted by this import.
