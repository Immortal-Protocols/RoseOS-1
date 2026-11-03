# Compile-only input contracts (OS Product)

`launcher3-quickstep-lib.json` (`kind` `rose-launcher3-compile-inputs`, `schemaVersion` 2) states the inputs of a
compile-only `Launcher3QuickStepLib` proof of RoseOS's Launcher3 changes (#410). Release owns the adapter that consumes
it; this directory only states inputs. The contract is inert: it registers, applies and copies nothing into any build,
and grants no host, runtime or permission access.

**The candidate** is #440, a draft that stays unregistered and default-off. Its inputs that are not in this bundle sit in
`os/tests/fixtures/launcher3-compile-contract/candidate-pr440/`:
- the edge patch;
- the three `RoseEdge*` overlay classes.

They are byte-identical copies of the reviewed commit `e361d1c`, bound by their digests. They live outside `os/patches`
and `os/overlays`, so nothing applies or copies them.

**`projects`** gives the candidate's final images. For every file that the ordered patches change in frameworks/base,
packages/apps/Launcher3 and build/release, it lists:
- the pinned pre-image (sha256 and git blob), or absent for a new file;
- the post-image;
- the blob chain across the patches.

**`overlays`** lists every overlay the candidate copies into those projects, as required or not an input.

**`base`** is what the patcher at this bundle produces. For Launcher3 that is the switcher patch only; frameworks/base and
build/release are identical to the candidate. It also gives the committed overlays.

**`candidateDelta`** binds the candidate commit, its patch digest, the before and after image of each file it changes, and
the before and after image of each overlay it changes. The delta's files are disjoint from the switcher patch's, so base
plus delta equals an integration with the edge patch registered after the switcher patch. The checker proves this
offline.

Every hash is an expected value derived from pinned public sources and the committed patches. An adapter compares
current bytes with them; it never records the current bytes' hashes as provenance.

The adapter takes exactly one branch:
- **verifyIntegrated:**
  - every affected file equals its post-image, and every required overlay is present with its hash;
  - no patch is applied again.

  `config.xml` is the one file the patcher rewrites after patching, in five elements, from the background policy
  receipt and two flags. It is accepted only by `verify_integrated_config`, which checks all of these:
  - the receipt's sha256 equals the one bound for that tree outside this contract;
  - each element is exactly the text the patcher writes from those inputs;
  - putting exactly those texts back to their markers gives the post-image.

  Nothing else is normalised. A missing input or binding, a mismatched value, or any other difference refuses.
- **verifyBaseApplyCandidate** (a proposal, stated as data only; Release implements it, and any run needs separate
  authority):
  - every affected file and overlay of the source tree equals `base` exactly, with the same `config.xml` rule;
  - every delta file and overlay is at its before image;
  - then, in an isolated copy only, the sha256-checked candidate patch is applied and the bundle overlays are copied;
  - the result must equal `projects` and `overlays` exactly.

  Partial, already applied or unknown state refuses. A tree integrated by any bundle other than this one does not match
  `base`.
- **reconstruct:**
  - every affected file equals its pinned pre-image and blob;
  - new files and every overlay target are absent beforehand;
  - the patches applied in order give exactly the post-images.
- **Anything else is refused:** mixed pre and post, an unknown hash, or a missing file, overlay, input or binding.
  Inside the three projects, every unlisted path must equal the pinned tree, and an extra file is refused. Nothing is
  reset, and no snapshot is trusted.

`scripts/launcher3-compile-contract.py` recomputes every pre-image, post-image and chain offline. It uses the committed
pinned fixtures in `os/tests/fixtures/launcher3-compile-contract/`, kept byte for byte with their upstream notices. Each patch's own
`index` lines confirm the steps that carry one. Two hunks of `locked-control-frameworks-base.patch` were authored on a
different base and apply by context; that is recorded per file.

`--write` regenerates the manifest and fixtures from GrapheneOS's public sources at the pinned SHAs, without
credentials. `os/tests/test_launcher3_compile_contract.py` pins the agreed manifest sha256, so any change needs re-agreement with
Release.

`flagEvidence` names, for each flag the adapter reports, the `aconfig_declarations` module that owns it:
- `android.security.content_uri_permission_apis`: `android.security.flags-aconfig` (frameworks/base `AconfigFlags.bp`,
  container `system`), not Launcher3's flag cache;
- `com.android.launcher3.enable_fallback_overview_in_window`: `com_android_launcher3_flags` (Launcher3
  `aconfig/Android.bp`).

The evidence is the expected line in that module's own decoded `aconfig-flags.txt`, produced by build/soong's
`aconfigTextRule`. The definition files and rules are pinned fixtures; no RoseOS patch touches them.

Not shown by the contract: R8, manifest merge, APK resources, packaging, signing or runtime behaviour. The identity
opt-in's consumer is Home's caller gate for `gesture_nav_contract_v1` (#442). The contract test reads that gate as
merged and never edits it.
