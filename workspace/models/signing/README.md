# Rose Models signing adapter (`dev.pixel.models`)

A separate signing path for the Rose Models APK, callable by Infra's existing signing runtime.
It signs only `dev.pixel.models` with alias `rose-models`, only the exact retained unsigned input
pinned in `profile.json`, and only after the signer and tool identities are bound to genuine values.
It never generates keys, logs in, fetches secrets, downloads artifacts, installs or publishes.
The `dev.pixel.voice` signing profiles and guards are untouched; nothing here widens them.

| File | Role |
| --- | --- |
| `profile.json` | Closed-schema profile: package, alias, version/ABI, required schemes, the expected input binding, and the `signer`/`tool` bindings (both `unbound` today). |
| `models_signing.py` | `plan` / `verify` / `sign` stages and CLI. Reuses `scripts/sign-apk.py` and `workspace/models/android/tests/verify-artifact.sh` unchanged. |
| `test_models_signing.py` | Source tests with synthetic fixtures, a fake SDK, a public fake fingerprint and mocked signer/inspector. |
| `.github/workflows/models-signing-verification.yml` | Hosted source tests only (`contents: read`, no secrets, no signing). |

## Stages

1. **plan** (no side effects): validates the profile (unknown/missing fields, other purpose or
   package, floating source, short or PR-ref bindings and inconsistent signer/tool blocks are
   refused), checks paths (artifact is a real directory; output is a new directory outside the
   repository) and reports what blocks the live call. Opens no credential, runs no tool.
2. **verify** (reads the artifact only): runs `verify-artifact.sh` with the profile's source/run/
   attempt, then checks every file hash, the native library inside the APK, the receipt's
   source/ref/event/run/trees/Cargo.lock values and the package/version/ABI **against the profile**,
   never against the artifact's own `SHA256SUMS` alone.
3. **sign** (the live callable): refuses `not_ready` while the signer or tool is unbound, *before*
   touching any private path or starting any tool. Then: verify the input (with the SDK's `aapt`),
   compare installed `lib/apksigner.jar` and `aapt` hashes with the bound tool identity, check the
   operator's keystore and password file (regular, owner-only, owned by the caller, not a symlink,
   outside the repository), copy the input into the adapter's own `work/`, run
   `scripts/sign-apk.py` (with the bound certificate pin) through the executor, rehash the input,
   and inspect the signed APK independently: verified, exactly the bound signer, schemes v2 and v3,
   the same package/version/ABI and native library, and `sign-apk.py`'s receipt bound to the input
   and output hashes. Only then is `models-signing-receipt.json` written.

## Invocation for Infra (after binding; not run by Models)

```bash
python3 workspace/models/signing/models_signing.py sign \
  --profile workspace/models/signing/profile.json \
  --artifact <directory holding the six retained files> \
  --output <new directory outside the repository> \
  --keystore <owner-only PKCS12 keystore outside the repository> \
  --password-file <owner-only file; used as store and key password> \
  --sdk-root <SDK containing build-tools/android-15>
```

- `--artifact`: exactly `SHA256SUMS`, `apk-metadata.txt`, `artifact-receipt.txt`,
  `build-receipt.txt`, `librose_chat_android.so`, `rose-models-unsigned.apk` from the bound main
  run (today: run 40960062067, artifact 12936165189, expiring 2026-11-05T21:44:00Z). Infra
  acquires these through its own authorized route; this adapter does not download.
- Exit `0`: success; the receipt JSON is printed and written to `<output>/models-signing-receipt.json`
  next to `rose-models-signed.apk`, its `.apk.idsig` and `sign-apk.py`'s `.receipt.json`.
- Exit `2`: refused; a fixed category on stderr. If the output directory was created,
  `models-signing-failure.json` records stage, category and the hashes of removed outputs.
- `plan` exits `3` when blocked (today: `signer_unbound`, `tool_unbound`).

Private values never appear in argv, environment, logs or receipts: the keystore and password
file are passed as paths to `sign-apk.py`, whose config file (alias and password-file paths) lives
in the adapter's owner-only `work/` and is deleted before any receipt is written.

## Binding before a live call (Infra, with genuine evidence)

`profile.json` is changed by a reviewed PR that sets:

- `signer`: `"status": "bound"`, `"certificateSha256"`: the full SHA-256 of the dedicated
  Models certificate, read from its public certificate, not guessed.
- `tool`: `"status": "bound"`, `"apksignerSha256"` / `"aaptSha256"`: SHA-256 of
  `build-tools/android-15/lib/apksigner.jar` and `aapt` on the signing host.
- `expectedInput`: replaced only by another verified main-run artifact receipt (for example
  after expiry or a version allocation); PR merge-test artifacts are never acceptable.

## Signing diagnostic (MODELS-SIGN-DIAG-001)

`sign_diagnostic.py` defines one closed, nonsecret record, written by `scripts/sign-apk.py` only when the caller
passes `--diagnostic-out <new owner-only file>` (Infra released this narrow opt-in hook; without the flag
`sign-apk.py` behaves exactly as before for every other caller):

```json
{"schema": "rose-models-sign-diagnostic/1", "stage": "invocation|alignment|sign|verify|complete",
 "outcome": "ok|exit|signal|timeout|oversized|not_started|rejected", "childExit": 0-255 or null,
 "childSignal": 1-64 or null, "category": "<fixed enum>", "apksignerJarSha256": "<hex64>" or null}
```

- Each tool starts exactly once with a 300 s bound. Its stdout and stderr are pipes drained together in reads of at
  most 64 KiB, never asking for more than one byte past the cap, so at most 256 KiB **per stream** (512 KiB for both)
  is held in the signing process's memory, only for classification. The cap is enforced while the tool runs: the read
  that would pass it ends collection, its bytes are discarded unstored, and the tool is stopped. The tool is also
  stopped at the deadline or on any exception in the collector: SIGTERM, SIGKILL after 1 s, and reaping within 5 s
  more. Both pipes are then closed, so a leftover writer (for example a grandchild) gets EPIPE instead of filling
  anything; until then it can only fill the kernel's own pipe buffer. A stopped tool reports `oversized` or `timeout`
  with no exit or signal of its own. Captured output is not written to disk and is never part of a record,
  refusal, receipt or log.
- Correction (same primary, after review of PR178 head `9b24e81`): the first version sent output to unnamed temporary
  files and only limited the read-back after the tool exited, so a noisy tool could grow those files until its
  timeout. The earlier statement here that "nothing raw is written anywhere" was therefore inaccurate for that
  version: raw bytes were written to temporary files, though never exported. The current collector replaces it, and
  `test_sign_diagnostic.py` keeps that version as a negative control that must fail the capture regressions.
- `category` is one of a fixed list (`keystore_password_incorrect`, `key_password_incorrect`, `alias_not_found`,
  `keystore_unreadable`, `password_file_unreadable`, `java_runtime_error`, `apk_input_rejected`,
  `output_write_failed`, `signature_not_verified`, `certificate_pin_mismatch`, `tool_missing`, `timeout`,
  `output_oversized`, `invocation_rejected`, `unclassified`, `none`). A match is an observation of the tool's message,
  not proof of the underlying defect.
- `childExit`/`childSignal` are the inner tool's; the wrapper's own exit (`sign-apk.py`, 1 on any failure) is
  recorded separately by the adapter as `signerWrapperExit`.

The adapter adds the flag, checks the fixed call shape (no inline password option), runs `sign-apk.py` once with
its output discarded and a 900 s bound, and accepts only a valid record whose `apksignerJarSha256` equals the bound
tool identity. On failure the refusal reads, for example,
`signer_failed: stage=sign outcome=exit child_exit=1 category=keystore_password_incorrect wrapper_exit=1`, and
`models-signing-failure.json` carries the record (or `signerDiagnosticProblem` such as `missing`, `fields`,
`malformed`, `oversized`, `tool_identity_mismatch`). A zero wrapper exit without a valid `complete` record is
refused (`signer_diagnostic_invalid`). The independent signer/scheme/identity/native checks are unchanged and still
decide success. The record file lives in the adapter's `work/` and is removed with it.

## Ownership and cleanup

The adapter owns only the new `--output` directory. It deletes its own `work/` (input copy and
config) on success and failure. On failure it removes any signed APK, `.idsig` and signer receipt
it produced so no partial output looks complete, keeping their hashes in the failure record. It
never deletes or rewrites the keystore, password file or any other operator file, and makes no
secure-erasure claim. Infra owns the private inputs' lifecycle, locks and the actual execution.

## Evidence levels

The tests prove adapter logic with synthetic fixtures and mocks: refusal order, binding, path
safety and receipt contents. They do not prove a real signature, a real certificate, an installed
`apksigner`, or anything about a device. Mock receipts say `evidenceTier: mock-source-test`.
