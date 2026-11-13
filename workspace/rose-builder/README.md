# Rose App Builder (Project 1 client)

The phone side of Project 1 in the Nov 13 plan: describe an app, follow its build, install, open and update it, ask
for changes, and share a download link. It is one ordinary Android app (minSdk 26, targetSdk 35) that needs nothing
from RoseOS. Builds run on the owner's Mac or builder; nothing is compiled on the phone.

Status: **source skeleton only.** It runs against `FakeBuilderApi`, an in-memory stand-in with no network, no APKs
and no share links. The real build/job/artifact contract belongs to App Core, and hosting belongs to the Chariot owner.
`BuilderApi` is the working contract App Core accepted on Nov 13. App Core added `sourceRevision` per version and
`attempt` per job. App Core's contract doc and schema in this repo will be the single source of truth, and this module
mirrors it. The package ID
`dev.pixel.builder` and the name "Rose App Builder" are provisional. They must be settled before the first release,
because the package ID can't change after that.

Rules (host-tested in `tests/BuilderRulesTest.java`, registered in `scripts/test-local.sh`):

- `BuildJob`: build stages, named failure categories, and retry advice only when the server says a retry can help.
  Progress never moves backwards.
- `InstallPlan`: install, update or open only with the same package, the same signer and a higher version. A changed
  signer or a downgrade is refused with its reason, never uninstalled and reinstalled. A downloaded file must match
  the SHA-256, package, version and signer the builder recorded.
- `ShareMessage`: the text the owner sends from Android's share sheet or SMS composer. It carries either the version's
  immutable link or the latest link. The link is a public APK download only.

`Installer` uses Android's `PackageInstaller` session with the owner's confirmation and only the
`REQUEST_INSTALL_PACKAGES` permission. It never uses INSTALL_PACKAGES, root, ADB or developer mode.

Build (unsigned, never reads a signing key): `bash workspace/rose-builder/build.sh`, which writes to `out/`.
