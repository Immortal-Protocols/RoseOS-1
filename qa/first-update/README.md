# First Update — independent QA harness

Test-only evidence for the "RoseOS — First Update to Nathan's Pixel 10" milestone.
Nothing here changes product code, and it is not product acceptance. It produces
offline evidence and an independent Stop gate that App/OS fixes must satisfy.

```sh
npm ci --prefix workspace/rose-agent/server --ignore-scripts --cache "$PWD/.tools/npm-cache" --no-audit --no-fund
bash qa/first-update/run.sh
```

`run.sh` runs the repository's default offline suite, criterion-relevant offline
tests that `scripts/test-local.sh` omits, the `agent-app-trust` Java check with the
system JDK (the original test hardcodes a macOS JDK path), and `stop-gate.test.mjs`.
It writes per-check logs and `receipt.json` (source commit, worktree state, tool
versions, results) under the ignored `out/qa-first-update/`.

`stop-gate.test.mjs` drives the real `BackgroundPhone` against a fake controller
that follows `Controller.java`'s Stop behaviour. After an owner Stop it requires that
no Calculator command, replacement workspace or UI action executes. At 01c5d8f the
Calculator and `open_app` cases fail, which reproduces the Stop gap. The fake reports
`stopReason: "user"` as proposed by OS. Server routes (`/phone/commands`, fast/raw
paths), speech after Stop, and real Android behaviour are outside this harness and
need a VM run.

None of these checks builds Android, uses the Pixel Voice VM, touches a phone,
release channel or signing, or counts as device acceptance.
