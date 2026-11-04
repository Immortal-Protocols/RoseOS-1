# Pixel 11 Pro Fold experimental port

These are the additional source patches for the experimental Pixel 11 Pro Fold
(`yogi`). The older "original Fold" description was incorrect. They are not for
Pixel 10 Pro Fold (`rango`), the original Pixel Fold (`felix`) or Samsung phones,
and are not applied by the
normal `apply-os-changes.py` command. The base manifest is not a complete recipe
for this unsupported port: matching vendor sources, stock-kernel inputs, and the
device product configuration are separate requirements.

- `allocator-va39.patch` selects a smaller hardened allocator region on the
  stock kernel's 39-bit virtual address range. It keeps the allocator checks but
  reduces address-space randomization compared with the ordinary build.
- `stock-kernel-flags.patch` tolerates the missing Graphene-specific kernel flags
  interface only on this device. Standard SELinux remains, but those additional
  Graphene kernel restrictions are unavailable. This does not provide security
  parity with a supported, fully hardened GrapheneOS build.
- `product-tray.patch` enables the existing Rose Agent tray in the Fold product.

The main background framework patch includes an optional physical reference
display address. On this Fold, selecting its cover display makes the virtual
workspace phone-sized, including the coordinates used for taps. Discover the
actual physical display address on the target build and pass it to
`apply-os-changes.py --reference-display-address`; no original device address is
embedded in this repository.

Play Store's newer data-loader path failed on this experimental port. The live
prototype used `debug.inc.disable_dataloader=true` in its product properties to
select the compatible path. `product-tray.patch` now retains that workaround
alongside the tray enablement. It is not applied to supported Pixels.
The temporary pre-unlock debugging experiment is deliberately not included.

The live Fold reached setup, ran apps, supported the left-side viewer, and kept
the same background session when closed. A clean reproduction of this port from
the exported repository has not been completed. These patches preserve the work
for review; do not flash a supported Pixel or a Samsung with a Fold image.

## Source preparation

See [the Yogi build guide](../../docs/YOGI-BUILD.md). `source.json` binds the
public upstream preimages and matching vendor metadata. The allocator patch's
BoardConfig context has been rebased onto the actual pinned adevtool template;
the former `BUILD_BROKEN_ELF_PREBUILT_PRODUCT_COPY_FILES` context did not apply.

`scripts/prepare-yogi-port.py check SOURCE` checks project revisions, local
changes, exact preimages and ordered patch application without changing source.
`apply SOURCE --lock-file LOCK` repeats those checks under the existing builder
lock before applying. This is source preparation only: it does not generate
vendor files, prove stock-kernel integrity, build, sign, publish or flash.
