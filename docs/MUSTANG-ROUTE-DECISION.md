# Mustang established-route correction

The new `.github/workflows/mustang-candidate.yml` context failed its controller
token exchange with `attributeConditionDenied` in diagnostic run
[41713153197](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41713153197).
No compute request was made by that diagnostic. Provider inspection was denied,
and no provider condition or IAM binding has been changed.

The approved and merged PR #492 removes the two new workflow entry files and wires the same
Mustang adapters into the established `.github/workflows/os-release.yml` route:

- `mode=mustang-build`: prepare, integrate and compile the pinned Mustang target;
  no signing credentials, public publisher or phone action.
- `mode=mustang-bootstrap`: one admitted `pro-mustang-v1` journaled bootstrap.
- `mode=mustang-delivery`: `mustang_compile_run` and `mustang_bootstrap_run` bind
  the exact successful main producer runs, then sign, package, export privately
  and verify delivered-download checksums.

The existing controller identity, WIF provider, runner, shared FIFO/lock and
ownership-aware STOP are reused. No WIF allowlist, service-account binding,
project IAM or other-device artifact/key changes were part of this route correction. The normal
Frankel default and its public publisher are unchanged; the Mustang modes
hardcode their own target and bypass that publisher. The approved reduced
checklist still applies, with all omitted suites/checks recorded honestly.

Nathan separately approved four-hour signer grants on the two Mustang containers
and one new-key bootstrap. An existing IAM admin applied the four grants, with
expiry 2026-11-04T00:50:06Z; policy readback confirmed their exact scope. The
corrected bootstrap run `41719073037` subsequently completed once, committing
version 1 in both containers with no recovery needed and workspace removed.
The [delivery guide](MUSTANG-DELIVERY.md) records the public evidence. Do not
repeat the bootstrap or extend access automatically.

The [plan](https://app.notion.com/p/40d7c646c81b81198e60cb76ca2ea603) says:
“No cancellation of healthy work, duplicate run, blind retry, new VM, new IAM
or alternate route after a denial.” Nathan explicitly approved merging the
reviewed correction and retrying through the established `os-release.yml` route.
PR #492 merged as `216184cba518c3d2f5e86aedfdddfa3b1fa27834`; its first Mustang
controller passed. Subsequent source-level admission corrections are recorded
in [the failure evidence](MUSTANG-ADMISSION-FIX.md). The rejected standalone
context and denied IAM operations were not repeated or routed around.
