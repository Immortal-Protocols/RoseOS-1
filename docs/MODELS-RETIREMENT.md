# Rose Models retirement (plan 1I)

Owner decision, 2026-11-07: Rose Models (`dev.pixel.models`) is fully retired, with no successor. This
lifts the 2026-11-05 hold that kept the app installable and enrollable for the Qwen proof. Models-team
development stays paused. The cloud Models services are not shut down.

## Enrollment and trust
- **Before:** the only phone client the cloud endpoint enrolled was `dev.pixel.models`, signed by
  `066c66d62518d1db6c8a7d40795fcca580412049d2c5a5cce89b7673540108da`, using a hardware-bound key (`service-auth-v1`).
- **Now: no successor.**
  - Rose Agent does not enroll, takes no Models credential, and never calls the app.
  - The trust contract is **not** widened; the F02 S1b design is not pursued.
- **Effect:**
  - Once a phone uninstalls the app, Android deletes that app's Keystore key. The endpoint can no longer be used from that phone.
  - No other client can enroll.
  - The services keep running, unused by phones.
- **Server key records:** they stay listed until an operator revokes them (`revoke --package dev.pixel.models`). No admin route is authorized for that today; see the F02 packet, section I.
  - A listed record cannot be used without its private key, which never leaves the phone and goes with the uninstall.
  - Revoking is deferred cleanup, not an exposure.

## Distribution
- **OS images, OTAs and Rose Agent delivery never carry it.** `scripts/tests/test_models_app_not_bundled.py` (F02) enforces this.
- **Standalone routes, removed:**
  - Release's slice (#658): the `models-sign` mode of `os-release.yml`, `models-publish.yml` (the one-off `rose-models-2` prerelease route), and the Models signing adapter, scripts and tests.
  - Then the app itself: its source (`workspace/models/android/`), its unsigned CI build (`models-android.yml`) and its JNI adapter (`workspace/models/rose-chat-android/`, whose exports bind `dev.pixel.models.NativeAgent`). Nothing can build, sign or publish it any more.
- **Kept, with no live action:**
  - the already-published `rose-models-2` prerelease in pixel-ota (never Latest);
  - the `models-v0` signer identity, key and secrets;
  - `workspace/models/serving`, `rose_cloud`, `contracts`, and Core's Rust chat core (`rose-chat-core`, still tested by `models-rust.yml`).

## Installed copies and upgrades
- **Nothing is removed silently.** Rose Agent holds no permission that could do it: `REQUEST_DELETE_PACKAGES` only opens Android's confirmation (#624).
- **Who counts as the retired app:** only the genuine app, by exact package and Models signer (`AppRemoval.retired`). An app reusing the package name with another signer is ordinary.
- **After a Rose Agent update, Home offers the uninstall once** (`RetiredAppNotice`):
  - **Uninstall** opens Android's uninstall confirmation for `dev.pixel.models`.
    - Confirmed: Home refreshes and says so.
    - Cancelled: the app stays installed.
  - **Not now**, or back, leaves the app installed and records the answer, so the notice isn't repeated.
  - The app stays launchable from the System folder. Its long press is labelled "Retired · no longer updated" and still offers Uninstall.
- **What uninstalling removes:** the app, its preferences and its Keystore key. Its chat history was only ever in memory. Nothing else on the phone changes.
- **OS updates don't touch the app,** because it was never part of an image. Rose Agent never installs, updates, launches or binds it; `tests/home-launcher.test.mjs` pins this.

## Verification limits
- These are source changes, checked by tests, render tests and an unsigned compile.
- No real phone, uninstall, publication, signing, key, secret or service action was taken.
- No live server records were revoked.
