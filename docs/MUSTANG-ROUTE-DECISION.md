# Mustang established-route correction

The new `.github/workflows/mustang-candidate.yml` context failed its controller
token exchange with `attributeConditionDenied` in diagnostic run
[41713153197](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41713153197).
No compute request was made by that diagnostic. Provider inspection was denied,
and no provider condition or IAM binding has been changed.

The proposed patch removes the two new workflow entry files and wires the same
Mustang adapters into the established `.github/workflows/os-release.yml` route:

- `mode=mustang-build`: prepare, integrate and compile the pinned Mustang target;
  no signing credentials, public publisher or phone action.
- `mode=mustang-bootstrap`: one admitted `pro-mustang-v1` journaled bootstrap.
- `mode=mustang-delivery`: `mustang_compile_run` and `mustang_bootstrap_run` bind
  the exact successful main producer runs, then sign, package, export privately
  and verify delivered-download checksums.

The existing controller identity, WIF provider, runner, shared FIFO/lock and
ownership-aware STOP are reused. No WIF allowlist, service-account binding,
project IAM or other-device artifact/key changes are proposed. The normal
Frankel default and its public publisher are unchanged; the Mustang modes
hardcode their own target and bypass that publisher. The approved reduced
checklist still applies, with all omitted suites/checks recorded honestly.

Nathan has separately approved four-hour signer grants on the two Mustang
containers and one new-key bootstrap. Those grants have not been applied and
no key material exists from this worker. They will be timed at execution after
controller access is resolved, not consumed while waiting on this route choice.

The [plan](https://app.notion.com/p/40d7c646c81b81198e60cb76ca2ea603) says:
“No cancellation of healthy work, duplicate run, blind retry, new VM, new IAM
or alternate route after a denial.” The source correction is prepared for
review. Explicit authorization to merge it and retry through the established
`os-release.yml` route is requested before any such runtime action. No denied
operation is repeated and no alternative identity is tried meanwhile.
