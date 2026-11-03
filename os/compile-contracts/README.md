# Compile-only input contracts (OS Product)

`launcher3-quickstep-lib.json` (`kind` `rose-launcher3-compile-inputs`, `schemaVersion` 1) states the inputs of a
compile-only `Launcher3QuickStepLib` proof of RoseOS's Launcher3 changes (#410, #440). Release owns the adapter that
consumes it; this directory only states inputs. No build runs it, and it grants no host, runtime or permission access.

For every file that the candidate's ordered patches change in frameworks/base, packages/apps/Launcher3 and build/release,
it gives:
- the pinned pre-image (sha256 and git blob), or absent for a new file;
- the post-image;
- the blob chain across the patches.

It also lists every overlay copied into those projects, as required or not an input.

Every hash is an expected value derived from pinned public sources and the committed patches. An adapter compares
current bytes with them; it never records the current bytes' hashes as provenance.

The candidate's patches:
- frameworks/base and build/release: `apply-os-changes.py --background-policy` order;
- Launcher3: `rose-switcher-launcher3.patch`, then `rose-edge-launcher3.patch`.

`apply-os-changes.py` does not register the edge patch yet. So a tree it integrates today, with the Launcher3 switcher
patch only, is a mixed state for this contract. It is refused, and it must not be "completed" by applying the edge patch
alone.

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
- **reconstruct:**
  - every affected file equals its pinned pre-image and blob;
  - new files and every overlay target are absent beforehand;
  - the patches applied in order give exactly the post-images.
- **Anything else is refused:** mixed pre and post, an unknown hash, or a missing file, overlay, input or binding.
  Inside the three projects, every unlisted path must equal the pinned tree, and an extra file is refused. Nothing is
  reset, and no snapshot is trusted.

`scripts/launcher3-compile-contract.py` recomputes every pre-image, post-image and chain offline. It uses the committed
pinned fixtures: `os/tests/fixtures/launcher3-compile-contract/` for frameworks/base and build/release, kept byte for
byte with their upstream notices, and the Launcher3 fixtures in `os/tests/fixtures/rose-switcher/`. Each patch's own
`index` lines confirm the steps that carry one. Two hunks of `locked-control-frameworks-base.patch` were authored on a
different base and apply by context; that is recorded per file.

`--write` regenerates the manifest and fixtures from GrapheneOS's public sources at the pinned SHAs, without
credentials. `os/tests/test_rose_switcher.py` pins the agreed manifest sha256, so any change needs re-agreement with
Release.

Not shown by the contract: R8, manifest merge, APK resources, packaging, signing or runtime behaviour. Home's caller
gate for `gesture_nav_contract_v1` is a stated requirement here (`identitySharing.consumerImplemented` is false), not
code in this repository yet.
