# Rose App Builder (Project 1 client)

The phone side of Project 1 in the Nov 13 plan: describe an app, follow its build, install, open and update it, ask
for changes, and share a download link. It is one ordinary Android app (minSdk 26, targetSdk 35) that needs nothing
from RoseOS. Builds run on the owner's Mac or builder; nothing is compiled on the phone.

Status: **source only, never run against a live builder.** With no pairing, the app offers to pair or to try a
labelled demo (`FakeBuilderApi`: in memory, no network, no APKs). Once paired it uses `HttpsBuilderApi`: builder API
v1 (`docs/APP-BUILDER.md` §3, App Core's contract) over the owner's Tailscale link. Every request carries this app's
rose-auth-v1 signature, on TLS pinned to the backend's certificate. Pairing follows Rose Agent's flow: paste a
`roseagent://pair` link made on the Mac, POST `/pair` with this app's own Keystore public key, then prove it with a
signed `GET /hello`. The signer and key code are Rose Agent's own `LocalRoseAuth` and `LocalRoseKey`, compiled in
unchanged (`dev.pixel.voice.RoseLink` is the bridge), so both apps sign the same bytes and share Rose Agent's pinned
cross-language vectors. No builder host enables these routes today, and the Rose server is stopped.

Rules (host-tested in `tests/BuilderRulesTest.java`, `RequestIdsTest.java` and `BuilderWireTest.java`, registered
in `scripts/test-local.sh`):

- `Json` and `BuilderWire`: a small strict JSON reader and writer, and v1's wire mapping. Every response must carry
  `schemaVersion: 1`, and unknown fields are ignored. Times convert from epoch seconds to milliseconds. A 400 shows
  the builder's own sentence, never treated as success or retried. A final 401 asks the owner to pair again, and a non-final one is
  signed afresh once.
- `BuildJob`: build stages, named failure categories, and retry advice only when the server says a retry can help.
  Progress never moves backwards.
- `InstallPlan`: install, update or open only with the same package, the same signer and a higher version. A changed
  signer or a downgrade is refused with its reason, never uninstalled and reinstalled. A downloaded file must match
  the SHA-256, package, version and signer the builder recorded.
- `RequestIds`: one `clientRequestId` per request. Retrying the same text after a lost connection reuses it. Changed
  text, or any request after the builder has answered, gets a new id. Host-tested in `tests/RequestIdsTest.java`.
- The builder's refusal sentence and its accepted-request `notes` are shown as plain text only.
- `ShareMessage`: the text the owner sends from Android's share sheet or SMS composer. It carries either the version's
  immutable link or the latest link. The link is a public APK download only.

`Installer` uses Android's `PackageInstaller` session with the owner's confirmation and only the
`REQUEST_INSTALL_PACKAGES` permission. It never uses INSTALL_PACKAGES, root, ADB or developer mode.

Build (unsigned, never reads a signing key): `bash workspace/rose-builder/build.sh`, which writes to `out/`.
