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

**`treePin`**, one per project, pins every path the contract does not list:
- `revision` (the pinned commit) and `rootTree` (that commit's tree). Both are pinned values: the root tree is
  recomputed offline by rebuilding every tree object from the committed listing. Neither is a host fact, and an
  adapter never takes either from the tree it checks;
- `objectFormat` `sha1`;
- `listing`: the committed `git ls-tree -r --full-tree` output, bound by the sha256 of its decompressed bytes and by
  `compressedSha256` of the gzip file. Generation reads NUL-terminated (`-z`) records and refuses any path a text
  listing would quote, so every path is its raw bytes;
- `unlisted`: every path in the pinned tree except `excludedPaths`, as records `<mode> SP <oid> TAB <path> LF` with
  project-relative paths sorted by raw bytes. It carries the sha256 of the concatenated records and their count;
- `excludedPaths`: the exact list of this project's affected files and overlay targets, with no globs. A listed path
  that the pinned tree lacks (a new file or an overlay target) must be absent from it and excludes nothing;
- `modes`, and no gitlinks: generation and the checker refuse any mode `160000` entry, and the checker also refuses a
  non-blob or quoted entry;
- `blobHash.argv`: `git hash-object -t blob --no-filters --stdin`. That is git's collision-detecting SHA-1, with no
  attribute filters and no object write. A symlink (mode `120000`) hashes its `readlink` bytes. Never `-w`,
  `--literally`, `--path` or a plain SHA-1 (git itself refuses `--path` with `--no-filters`). The test runs that
  invocation on every pinned pre-image, in a repository whose attributes would rewrite the bytes, and on a real
  symlink. With the same flags it confirms every pre, post, chain, base and delta blob id the offline generator
  records, and it confirms every rebuilt tree object with `git hash-object -t tree`.

Any extra file is refused. Per-path sha256 would need every file's contents, which the bounded fixtures do not hold.
The pin uses the git blob ids from the pinned commit's own tree objects instead.

**`overlayRoles`** lists `required` and `notAnInput` overlays by exact target, plus the `compileGoals`:
`Launcher3QuickStepLib` and the two `aconfig-flags.txt` outputs. Required overlays stay pinned by sha256. The seven
`notAnInput` overlays are an exact allowlist (`NOT_AN_INPUT`) of frameworks/base WindowManager-Shell and SystemUI files;
any other overlay outside Launcher3 and build/release is refused, so its location alone never makes it unpinned. Each
one:
- is excluded from frameworks/base's `treePin` by exact path;
- is never a compile goal or a `flagEvidence` input;
- is never in Launcher3 or build/release.

The checker recomputes `overlayRoles` and refuses any difference. A malformed manifest is reported as a failure, never
a traceback.

**`base`** has the same per-project shape as `projects`. It is bound by **content**: `bundleContent` lists the patches
(by project, name and sha256) and the overlays (by target and sha256). A tree matches `base` only if Infra reports that
the bundle which integrated it had exactly those contents. For Launcher3 the base is the switcher patch only;
frameworks/base and build/release are identical to the candidate.

**`candidateDelta`** binds:
- the candidate commit;
- each patch, by name, sha256, `bundlePath`, project and `strip`;
- an exact application rule: `git apply --check`, then `git apply`, in the isolated copy only, with no three-way merge,
  rejects, reduced context or whitespace fixing;
- the before and after images of each file it changes;
- each overlay it changes, by target, `bundlePath`, before and after. The delta's files are disjoint from the switcher patch's, so base
plus delta equals an integration with the edge patch registered after the switcher patch. The checker proves this
offline.

Paths in `candidateDelta.files` and in overlay targets are relative to the source tree root, project path first. Paths in
`projects[].files` and `base.projects[].files` are relative to their project. Bundle paths are relative to this
repository (`candidateDelta.pathConvention`). The committed os/ must be wholly at the base (this bundle) or wholly at the
candidate (#440's branch); a mix is refused.

Every hash is an expected value derived from pinned public sources and the committed patches. An adapter compares
current bytes with them; it never records the current bytes' hashes as provenance.

The adapter takes exactly one branch:
- **verifyIntegrated:**
  - every affected file equals its post-image, and every required overlay is present with its hash;
  - no patch is applied again.

  `config.xml` is the one file the patcher rewrites after patching, in five elements, from the background policy
  receipt and two flags. The rule is complete as data in `integrationRewrite` (markers, templates, input rules,
  comparison), so an adapter needs nothing from the checker. `verify_integrated_config` is its reference
  implementation, and a test shows the data alone reaches the same verdicts. The file is accepted only if all of these
  hold:
  - the receipt's sha256 equals the one bound for that tree outside this contract;
  - each element is exactly the text the patcher writes from those inputs;
  - putting exactly those texts back to their markers gives the post-image.

  Nothing else is normalised. A missing input or binding, a mismatched value, or any other difference refuses.
- **verifyBaseApplyCandidate** (a proposal, stated as data only; Release implements it, and any run needs separate
  authority):
  - it has four conditions with stable ids: `base-exact`, `delta-preimages`, `isolated-apply` and `final-exact`;
  - it refuses at the first failed condition, naming that condition's id.

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

`--write` regenerates the manifest. It refetches the pinned pre-images and tree listings from GrapheneOS's public sources
at the pinned SHAs, without credentials. It leaves the candidate bundle as committed, and it keeps the recorded base
("before") of the candidate's overlays. It refuses when that base is unknown and os/ already holds the candidate's
bytes. Overlays that are not inputs of the library are listed by target only, so edits to their contents don't touch the
contract; adding or removing one does. `os/tests/test_launcher3_compile_contract.py` pins the agreed manifest sha256, so any change needs re-agreement with
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

## `frankel-lunch-inputs.json`

`frankel-lunch-inputs.json` (`kind` `rose-frankel-lunch-inputs`, `schemaVersion` 1) states what `lunch frankel-cur-user`
needs beyond the three projects above, and the product configuration that `m` runs again. OS Product owns this data.
Release's adapter and observer read it as data; they never run `scripts/frankel-lunch-inputs.py`.

Every value is an expected value from pinned public sources: adevtool `17df3b79`, build/make `b0f697df` and
laguna-kernels `275dad3c`. The one exception is `gitRequirement.genuineSource`, which is marked historical context.
- Each fixture is the whole pinned file, kept with its upstream notices. The two large ones are gzipped.
- Each fixture's git blob, and each tree listing's root, is checked against its pin.
- No value is a host fact, and none may be copied into a receipt, a bundle report or a binding.

- **`environment`.** These are unset before envsetup and lunch: `GIT_*`, `BUILD_ID_frankel`, `TARGET_KERNEL_DIR` and
  `USE_STOCK_KERNEL`. `GIT_CEILING_DIRECTORIES` is set to the copy root. If inherited, these variables would change what
  lunch reads, and lunch would still pass.
  - Lunch then exports `BUILD_ID_frankel` itself for the shell that runs `m`, which needs it.
  - `PLATFORM_SECURITY_PATCH_frankel` is not listed, because the sourced script unsets it.
- **`versionCheck`.** The generated `vendor/google_devices/frankel/frankel.mk` includes
  `adevtool-version-check.mk` at line 4. That file runs `$(shell git -C vendor/adevtool rev-parse HEAD)` in lunch and
  again in `m`. Its exact expected bytes for revision `17df3b79` are given.
  - The warning form (`--noVerify`) is refused.
  - The error form is necessary, not sufficient. It is also written in three cases where the reference spec was not
    checked:
    - `--updateSpec`, where no comparison runs;
    - `collect-state`'s temporary module, where no comparison runs;
    - a working-tree change to `vendor-specs/`, where the comparison runs against the edited spec on disk.

    So the observables must still be compared.
  - No variable skips the check.
- **`gitRequirement`.** git accepts a HEAD plus empty `objects/` and `refs/` as a repository, so a passing lunch proves
  nothing. The acceptance checks are:
  - the HEAD, the commit, and the root tree `f0863541`;
  - nothing redirects outside the copy:
    - `.git` resolves to a directory inside the copy;
    - no gitfile, alternates, `commondir` or outside link;
    - no other filesystem or mount point, and no hard-linked file;
    - the git directories resolve inside the copy root.

  No check can tell a fabricated store from a genuine one; only an approved byte-for-byte copy establishes it. A genuine
  copy is allowed only under a separate approval. Redirect, fabrication and relaxation (`--noVerify`, `--updateSpec`,
  working-tree changes) are refused. In the pinned sources, nothing else needs `.git` for this lunch.
- **`buildId`, `kernelModuleLists`, `finderPrune`, `manifestFiles`.**
  - `cmds-for-envsetup.sh` exports `BUILD_ID_frankel`. With it unset beforehand, a missing script leaves the build/make
    default, and frankel.mk stops lunch.
  - The three laguna `modules.load` lists each state their own rule. Two stop lunch when missing or empty;
    `system_dlkm` has no guard, so an observer checks it.
  - adevtool's `.find-ignore` markers.
  - the pinned manifest's linkfiles and copyfiles.
- **`generatedVendor`.** The five build definitions an observer compares are given with their sha256 from adevtool's
  reference FileTreeSpec, `vendor-specs/google_devices/frankel.yml`. The host's bytes, its flags and its adevtool working
  tree are not established.

`os/tests/test_frankel_lunch_inputs.py` recomputes the manifest. It pins the proposed sha256, which needs agreement
with Release. It also shows the stated behaviour with the installed git and GNU make, in an allowlisted environment and
temporary directories only:
- a copy without `.git` fails;
- the warning form passes;
- a parent repository answers when no ceiling is set;
- a fabricated, objectless gitdir passes lunch but not the acceptance checks;
- alternates, gitfile, link-to-gitfile, outside-link and hard-link redirects pass the basic checks but not the
  structural ones;
- an inherited `BUILD_ID_frankel` hides a missing script.

`--write` refetches the fixtures from GrapheneOS's public sources, without credentials.
