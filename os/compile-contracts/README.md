# Compile-only input contracts (OS Product)

`launcher3-quickstep-lib.json` states the inputs of a compile-only `Launcher3QuickStepLib` proof of RoseOS's Launcher3
changes (#410, #440). Release owns the adapter that consumes it; this directory only states inputs. It is not run by any
build, and it grants no host, runtime or permission access.

For every file the ordered patches change in frameworks/base, packages/apps/Launcher3 and build/release, it gives:
- the pinned pre-image (sha256 and git blob), or absent for a new file;
- the post-image;
- the blob chain across the patches.

It also lists every overlay copied into those projects, as required or not an input.

The adapter takes exactly one branch:
- **verifyIntegrated:**
  - every affected file equals its post-image, and every required overlay is present with its hash;
  - `config.xml` is compared after `normalize_config` reverses the five policy rewrites `apply-os-changes.py` makes;
  - no patch is applied again.
- **reconstruct:**
  - every affected file equals its pinned pre-image and blob, and new files are absent;
  - the patches applied in order give exactly the post-images.
- **Anything else is refused:** mixed pre and post, an unknown hash, or a missing input. Nothing is reset, and no
  snapshot is trusted.

`scripts/launcher3-compile-contract.py` checks the manifest offline against the committed patches, overlays and pinned
Launcher3 fixtures. `os/tests/test_rose_switcher.py` runs it.

`--write` regenerates the manifest from GrapheneOS's public sources at the pinned SHAs, without credentials. Each
patch's own `index` lines independently bind its steps. Steps authored on a different base, such as two hunks of
`locked-control-frameworks-base.patch`, are recorded as applying by context.

Not shown by the contract: R8, manifest merge, APK resources, packaging, signing or runtime behaviour.
