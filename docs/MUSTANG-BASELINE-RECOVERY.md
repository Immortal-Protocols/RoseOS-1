# Mustang factory-image baseline evidence

This source increment adds a focused baseline observer for the existing
`2026110302` factory installer. It performs no retrieval, transfer, signing,
credential generation, cloud action, mount, installer-script execution or phone
action. It is independent of the private app-input transport work in PR #592.

The dedicated lane and runtime remain **unbound**. The new optional runtime
field `baselineFactoryEvidence` is null. The existing `baselineTargetFiles`
path remains supported; exactly one kind must be explicitly selected. A missing
target-files archive does not select recovery or bypass the guard. Unresolved
allocation and every existing controller/signing/access hold still block a run.

## Provenance and actual bindings

`os/ota/mustang-baseline-factory.json` is an **unbound source contract**, not a
retrieval approval or proof that the archive/tools exist. Its immutable facts
come from the existing committed delivery, package and artifact records:

- Actions artifact `13280698927`, run `41738359336`: 2,108,061,108 bytes,
  SHA-256 `c6aea80e23b5aeb775454692ff2da28ca8ff27495d79603e67ec2c0dfc1a6dae`.
- Inner `mustang-install-2026110302.zip`: 2,108,057,621 bytes,
  SHA-256 `4eae7e7e54191f30168649e75f2cfefb10fdbff1bffbfc2655ec079a4a8cce5e`.
- The historical baseline observation is hash-bound by its stage receipt,
  which is hash-bound by the publication record. These records are unchanged.

Before binding, the existing authorized data owner must resolve retrieval scope,
perform an approved transfer and record its actual readback. The local archive
path is exactly `<Mustang data home>/inputs/baseline/13280698927.zip`. The
hash-bound committed `transferEvidence` JSON has schema
`roseos.mustang-baseline-transfer.v1`, `approved: true`, `completed: true`,
nonempty `approvalRef`, `retrievalApprovalRef` and `evidenceRef`, the exact
`archive` descriptor, destination `builder` and `dataFilesystemUuid`.
These fields record real actions/authority; filling them grants nothing.

Configure actual `tools` entries `{path, sha256}` for `simg2img`, `lpunpack`,
`avbtool`, `aapt`, `apksigner`, and at least one filesystem reader (`debugfs`
for ext4, `fsckErofs` for EROFS). The reader for the **observed** format must
exist. SDK paths must match the admitted runtime. Executables/scripts are
re-hashed before use; missing dependencies refuse. The old otatools archive is
not part of this delivered artifact and is not assumed available. No tool path
on Frankel or another flavor may be borrowed.

`limits` explicitly supplies positive `seconds`, `workBytes`, `reserveBytes`.
Own-copy loops check time/work/free bytes/inodes. External tools use the existing
process-group supervisor with a watched scratch directory, timeout and reserve;
its disk enforcement is sampled and can overshoot between samples. This is not
a quota, spending approval or provisioning operation.

## Verification and receipt

The existing dedicated baseline step invokes `scripts/mustang-baseline-image.py`
only after source admission and the guest instance/UUID check. Under Mustang's
own lock, the observer creates one fresh run scratch directory and:

1. Copies the regular, no-link input on the admitted data filesystem. Verifies
   outer size/hash, the exact artifact inventory, every recorded member's
   size/hash and `SHA256SUMS`; verifies inner installer size/hash.
2. Validates installer paths/types/uniqueness and the exact Mustang board
   requirement. Never runs `flash-all.sh`, `script.txt` or fastboot. Checks
   contiguous sparse super segments and geometry, reconstructs them and unpacks
   logical partitions. Rejects ambiguous/nonempty inactive-slot images,
   duplicates, unsupported entries and missing system/vendor/vbmeta images.
3. Pins the AVB public-key bytes to the established Mustang public identity.
   Bounds-checks the signed descriptor graph and requires authenticated hash or
   hashtree coverage of **both system and vendor**, following chained keys.
   Rejects disabled/unsigned vbmeta, cycles, missing referenced images and
   persistent digests that cannot be verified offline. Runs hash-pinned
   `avbtool verify_image --follow_chain_partitions`, with no zeroed-hashtree
   exception. Image digests in the receipt cover that authenticated graph.
4. Reads vendor `/build.prop`, system
   `/system/priv-app/Updater/Updater.apk` and
   `/system/etc/security/otacerts.zip` using the detected filesystem reader.
   It reuses the original component checks: Mustang/2026110302/1793742435,
   init-compatible property handling, exact historical Updater bytes, discovery
   classes, package/resources/signer and the established historical OTA trust
   set. The old baseline remains exempt from the newer raised-edge gate.

The write-once `roseos.mustang-factory-baseline-observation.v1` receipt identifies
`evidenceKind: factory-images`, source/run, destination, archive and provenance,
tool/checker hashes, authenticated image and component hashes and check results.
It never supplies a fabricated `targetFiles` descriptor. Failures write a failed
receipt and retain their own scratch for diagnosis; success removes only that
scratch and records actual cleanup. Original inputs remain unchanged.

Staging embeds the complete nonsecret proof and its observation hash. Both
staging and staged-publication verification require the selected contract,
complete checks, source/run match, exact historical baseline and proof hash.
Verification still precedes publisher credentials. Existing target-files output
and all new-candidate raised-edge/signature/version checks remain intact.

## Validation limits

The new regression cases use real ZIP, AVB-header/descriptor and receipt parsing
with **synthetic images/public-key bytes and controlled external-tool responses**.
They do not prove real AVB cryptography, sparse/image extraction, SDK operation
on this installer or any phone result. Actual installer inspection remains
unperformed and subject to its retrieval/transfer hold and real tool admission.
The factory archive's detached signature and the original full factory-content
comparison remain omitted; this observer does not declare the installer
factory-ready, complete firmware coverage or relock/boot safety.

## Separate numbering compatibility assessment

No numbering migration or allocation is implemented here. The old allocator uses
UTC Gregorian `%Y%m%d` plus a sequence in 01..99. With Python's maximum year 9999,
its formal largest output is 9999123199, regardless of the failed host clock.
A clock-independent migration could retire that entire legacy namespace and
use a write-once monotonic counter above it, retaining the old exact allocation
as unknown. This requires proving which uncertain operations used that generator
and auditing all consumers; it is not permission to pick a new number.

The follow-up [numbering audit](MUSTANG-NUMBERING-RECOVERY.md) records the captured
failed-run generator source, pinned native/Updater assumptions, executable
synthetic boundary probes, remaining device rollback and counter dependencies,
and a minimal migration proposal. The Java compiler module is available even
though no `javac` executable is on PATH, so the follow-up Java probes ran.
The date allocator still refuses the new namespace with `clock_behind`;
switching only the next ID is not a migration. A different day or empty disk
cannot prove the lost allocation's absence. Actual reservations, signed state,
timestamps and publication ordering remain explicit; no `unknown: false` is
fabricated, and bounded source tests do not prove a real candidate or phone.
