# Rango build-only route on the isolated builder

`rango.json` binds the Rango (Pixel 10 Pro Fold) build-only route to the Frankel builder's
tree. `rango-isolated.json` binds it to the isolated builder under `/srv/rose-rango`
(OS-RANGO-BUILD-002). `rango-reconcile.json` is the `host-reconcile.py --config` for that
builder, and it pins the already-downloaded public `frankel-2026102901` inputs
(OS-RANGO-BUILD-003).

Nothing here downloads, signs, publishes or dispatches. The output is an unsigned test-keys
build, and it is not release-ready.

## What each step proves

| Step | Tool | Proves | Does not prove |
| --- | --- | --- | --- |
| 1. Local inputs | `scripts/rango-local-inputs.py prepare` | The two local files match the release 428513880 pins. The three APKs are copied byte-for-byte from the OTA's verified payload. Package, version and signer match the pins. The policy is written from those bytes. | Anything about Rango. The OTA is a Frankel OTA, used only as a source of app bytes. It is never installed on Rango or offered to it. |
| 2. Policy | inside step 1 (`prepare-background-policy.py`, SDK 37) | The policy maps the controller to `--agent-apk`, voice to `--voice-apk` and Calculator to `--target-apk`. `prototypeControl` is false, as in the delivered product. | |
| 3. Integration | `scripts/host-reconcile.py apply --config os/build-only/rango-reconcile.json`, run under `flock` on the isolated lock | The tree carries the product's Rose changes and the exact signed APKs. Owner changes become conflicts, so nothing is written. Backups and an immutable receipt are kept. | |
| 4. Generate | `scripts/rango-build-only.py run --stage generate` | `vendor/google_devices/rango` was generated from the pinned factory image and adevtool revision. Generation needs no integration receipt. | |
| 5. Compile | `scripts/rango-build-only.py run --stage compile --inputs-receipt …` | An unsigned build ran on a tree whose latest reconcile receipt satisfies all of these: it is in `/srv/rose-rango/reconcile`; it still matches the tree; it installed the APK bytes and policy pins from step 1; it came from this checkout's trusted patcher. A receipt copied from the Frankel builder names other bytes, so compile refuses it. | Signing, boot, device acceptance or publication. |

Source tests (`.github/workflows/rango-build-only-tests.yml`) exercise these steps on
synthetic trees, OTAs and tools. A passing run does not show that a real extraction or build
happened.

## Product, recipe and what stays unresolved

- **Product:** `02b21bc69f9afd281422d42e3dcf8a456e471de1`, the delivered frankel 2026102901
  product with apps voice 310, Calculator 4 and controller 28. `host-reconcile.py` reads the
  overlays and patches from this product's `os/` bundle.
- **Recipe:** the checkout that runs steps 3 to 5. Its `apply-os-changes.py`,
  `background_apks.py` and `integration_copy.py` are the trusted patcher. Each reconcile
  receipt records their hashes, and compile requires the same hashes. Run every step from one
  checkout.
- **No installed-identities gate:** the Frankel 2701 identities describe Frankel phones, not
  the Fold. `--locked-control` is not passed either.
- **Updater channel (unresolved):** no Rango channel is published. The Updater stays upstream
  (`--keep-upstream-updater`) and no URL is configured. Never offer this build as an update to
  an existing RoseOS phone. Never feed it a Frankel OTA or the Frankel channel.
- **Cover display address (unresolved):** not measured, so `--reference-display-address` is
  not passed.
- **Signed target-files `3c8f86fd…`:** provenance only. It is compared with
  `verification.json` and never read.
- **Capacity:** `allowanceRequired` is false on the isolated host. A null allowance is
  recorded as unmeasured, and preflight requires only that available bytes stay at or above
  the 300 GiB reserve. A measured allowance, once set, is still subtracted.

## Commands

Infra allocates the build number and the time of each step. The variables below are the
operator's:

- `R` is a checkout of the recipe commit.
- `DL` is the directory that already holds the two downloaded files.
- `B` is the allocated ten-digit build number.

The aapt and apksigner paths must be measured local paths. Step 1 refuses bare names (it never
searches `PATH`) and records each tool's sha256. The adevtool host tools are the ones
`rango-build-only.py` preflight already requires.

```bash
R=/srv/rose-rango/recipe
DL=/path/holding/frankel-2026102901          # frankel-ota_update-2026102901.zip + verification.json
B=<allocated build number>
T=/srv/rose-rango/src/out_adevtool_deps/host/linux-x86/bin
IN=/srv/rose-rango/inputs/frankel-2026102901-$B   # must not exist yet

# 1+2. Verify, extract unchanged and write the SDK 37 policy (no network; nothing outside $IN is written).
python3 -I $R/scripts/rango-local-inputs.py prepare --config $R/os/build-only/rango-reconcile.json \
  --ota $DL/frankel-ota_update-2026102901.zip --verification $DL/verification.json --out $IN \
  --fsck-erofs $T/fsck.erofs --debugfs $T/debugfs --aapt <measured aapt> --apksigner <measured apksigner>

# 3. Integrate the product under the isolated lock (exit 75 = lock held; nothing ran).
git -C $R worktree add --detach /srv/rose-rango/work/product-02b21bc6 02b21bc69f9afd281422d42e3dcf8a456e471de1
flock --nonblock --conflict-exit-code 75 /srv/rose-rango/rango-build.lock \
  python3 $R/scripts/host-reconcile.py apply /srv/rose-rango/work/product-02b21bc6 \
  /srv/rose-rango/work/reconcile-$B $IN/background-policy.json $IN/signed-apps \
  --config $R/os/build-only/rango-reconcile.json --label rango-$B

# 4. Generate (preflight first; the run holds the same lock and writes write-once receipts).
python3 $R/scripts/rango-build-only.py preflight --stage generate --config $R/os/build-only/rango-isolated.json \
  --build-number $B --download-dir /srv/rose-rango/dl-$B
python3 $R/scripts/rango-build-only.py run --stage generate --config $R/os/build-only/rango-isolated.json \
  --build-number $B --download-dir /srv/rose-rango/dl-$B --receipt-dir /srv/rose-rango/receipts/$B-generate

# 5. Compile, bound to step 1's receipt.
python3 $R/scripts/rango-build-only.py preflight --stage compile --config $R/os/build-only/rango-isolated.json \
  --build-number $B --download-dir /srv/rose-rango/dl-$B --inputs-receipt $IN/local-inputs.json
python3 $R/scripts/rango-build-only.py run --stage compile --config $R/os/build-only/rango-isolated.json \
  --build-number $B --download-dir /srv/rose-rango/dl-$B --inputs-receipt $IN/local-inputs.json \
  --receipt-dir /srv/rose-rango/receipts/$B-compile
```

Step 1 leaves the rebuilt `product` and `system_ext` images in `$IN/work`, and their hashes are
in `local-inputs.json`. Remove them only deliberately. Step 3 refuses to run if an earlier
`reconcile-shadow` or `reconcile-patcher` directory is left over. A conflict exits 1 and
writes nothing. Resolve it with the owner, never with a blanket `adopt`.

The generate stage never regenerates an existing vendor directory. Neither stage erases
outputs or caches.

## Resuming a compile after a natural cutoff (OS-RANGO-RESUME-005, corrected by -F1)

A compile stopped by the supervisor deadline or a VM stop can continue in its own `OUT_DIR`.
The incremental cache is reused, and nothing is renamed, deleted, cleaned, regenerated or
re-synced. `resume` is not an allow-existing switch. Fresh `run` still refuses an existing
`OUT_DIR`.

### What `resume` requires

`resume-check` runs these checks read-only. `resume` repeats all of them while holding the
same nonblocking isolated lock, before it runs anything.

1. **The attempt chain.** The chain is the original `--receipt-dir` (`R`), followed by
   `R.resume-1`, `R.resume-2` and so on. Only the latest attempt is judged:

   | Latest attempt | Result |
   |---|---|
   | Completed with returncode 0 | Already completed: a no-op that runs nothing |
   | Never started | Refused |
   | Ordinary failure | Refused |
   | Malformed receipts, or a gap in the chain | Refused |
   | Started and never completed, or ended by a signal | Resumable only with a bound cutoff proof |

   A missing completion, exit 143/137, or a free lock does not prove a cutoff on its own.

2. **`CUT.json` (`rango-compile-cutoff`), written by the operator at the stop.**
   - It is bound to the latest attempt's `started.json` and `preflight.json` sha256, its build
     number and its `OUT_DIR`.
   - The cause is `supervisor-deadline` or `vm-stop`.
   - `stoppedAt` falls after the start, and `observedAt` falls after the stop.
   - At least one evidence file is listed. The helper re-hashes each one and requires the
     quoted line to appear exactly.

3. **`PROV.json` (`rango-compile-provenance`), bound to the original `started.json`.** It
   supplies what an old-format start never recorded, each with a recorded source:
   - the config sha256, which must equal the `--config` file now;
   - the recipe (base and overlays);
   - the Android project pins: the retained resolved project map (`repo manifest -r` XML)
     with its sha256, and one resolved now. They are compared as exact (name, path, revision)
     pins, not as bytes, and every project must carry a pinned revision;
   - the `OUT_DIR` owner uid.

   A missing field is reported and refused. Nothing is invented.

4. **The same identity.**
   - Every attempt's `started.json` ran exactly the command, cwd, `OUT_DIR` and `BUILD_NUMBER`
     this config gives now.
   - The original preflight's `OUT_DIR`, reconcile receipt (name and hash), and local-inputs
     (path, hash, release and product) are unchanged.
   - Every compile preflight check still passes, including integration, adevtool, vendor and
     the isolated reserve-only capacity check.

5. **Output.**
   - `SRC/out-rango-B` is an existing ordinary directory directly inside `SRC`, with no linked
     component.
   - It is owned by the recorded uid, and no receipt or proof path lies inside it.
   - Links inside the build are neither walked nor refused.

6. **The old process tree is gone: a boot change, not a process scan.**
   - The witnessed boot is the boot id observed while the latest attempt existed:
     - a resume attempt records its own boot in `started.json`;
     - an old-format attempt (no recorded boot) needs `--boot-witness`
       (`rango-compile-boot-witness`), a genuine observation of the live attempt, bound to its
       `started.json` sha256 and build. It carries `observedBy`, `method` and its actual
       `observedAt`, which must lie between the start and `stoppedAt`. A witness is never
       backdated or presented as a launch receipt.
   - The current boot id must differ from the witnessed one. The current boot time must be
     after that observation and at or after `stoppedAt`.
   - The helper reads only `/proc/sys/kernel/random/boot_id` and the `btime` line of
     `/proc/stat`. It never reads any process's command line, environment, file descriptors or
     working directory, and needs no root or extra privilege.
   - It fails closed. A same-boot stop is refused, because proving termination there would need
     process inspection. A missing or unbound witness, or an unreadable boot identity, is also
     refused.

### What `resume` writes

`resume` creates the next `R.resume-N` (write-once):

- `preflight.json` holds the admission report.
- `started.json` holds the unchanged command, `boot` (this boot's id and time) and `resumeOf`:
  the prior receipts' hashes, the cutoff, provenance and witness hashes, the boot comparison,
  and the helper's hash.
- `completed.json` holds the returncode, with `incremental: true`, `signed: false` and
  `releaseReady: false`.

Earlier receipts are never modified. A later cutoff needs a new proof bound to that
`R.resume-N`.

### Commands

```bash
python3 $R/scripts/rango-build-only.py resume-check --config $R/os/build-only/rango-isolated.json \
  --build-number $B --download-dir /srv/rose-rango/dl-$B --inputs-receipt $IN/local-inputs.json \
  --prior-receipt-dir /srv/rose-rango/receipts/$B-compile --cutoff-proof CUT.json --provenance PROV.json \
  --boot-witness WITNESS.json
python3 $R/scripts/rango-build-only.py resume --config $R/os/build-only/rango-isolated.json \
  --build-number $B --download-dir /srv/rose-rango/dl-$B --inputs-receipt $IN/local-inputs.json \
  --prior-receipt-dir /srv/rose-rango/receipts/$B-compile --cutoff-proof CUT.json --provenance PROV.json \
  --boot-witness WITNESS.json
```

Run both as the ordinary build owner. No root, sudo or other privilege is used or needed.

### Limitations

- **New writers.** A boot change proves only that the attempt's own process tree, including
  new-session descendants, is gone. A writer started in the new boot is excluded only if it
  takes the isolated lock, which `resume` holds throughout.
- **Old-format runs.** These were never recorded, so the helper binds only what was:
  - The environment binding covers only the recorded command, cwd, `OUT_DIR` and
    `BUILD_NUMBER`. The full post-lunch or inherited environment is not covered.
  - The config hash and project map come from the operator's retained files, as attributed
    in `sources`. They are not launch-time hash records unless those records exist.
- **PR173's version.** The first version of this helper scanned every process and is withdrawn.
  Do not run it, including its `resume-check`.

A successful incremental compile is only a compile. It is not signing, verification or
device acceptance.
