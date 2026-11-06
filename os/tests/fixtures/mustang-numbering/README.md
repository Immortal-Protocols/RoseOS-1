# Mustang numbering source fixtures

These are source excerpts, not build artifacts, runtime receipts, allocation
records or signing evidence. `sources.json` records the upstream repository,
manifest revision, complete-file Git blob and SHA-256, exact line ranges and
excerpt SHA-256. The complete fetched UTF-8 source was checked against the
recorded Git blob before extraction. Original leading notices are retained in
`UPSTREAM-NOTICES.txt` where present; the upstream component's license
continues to apply. Apache-2.0 text is at `LICENSES/Apache-2.0.txt` in the repository.
Unmarked excerpts do not acquire RoseOS's default license through this directory.

`legacy-*.excerpt` comes from RoseOS source
`6be302942f348b58f8a0ce2406c7202f2dd676bf`, captured by failed run
`41884368820`. The record proves source relationships and the fixed generator
limit. It does not attest which guest instructions executed or recover the lost
reservation. The reservation could precede the attempt's `allocation.json`.

The focused tests are already registered through
`scripts/tests/test_mustang_edge_gate.py` and `os/tests/MustangReleasesTest.java`.
They execute the unchanged pinned Python metadata converter and channel script,
the unchanged recovery functions inside `RecoveryProbe.cpp`, and the unchanged
Updater admission block inserted into `UpdaterProbe.java.in`. The C++ and Java
harnesses substitute Android properties, logging and notifications. The Python
converter receives protocol-shaped stand-ins, not generated protobuf objects.
They test identity/timestamp boundaries, not a full native build, cryptography,
UpdateEngine, a bootloader or a phone. Synthetic IDs are never reserved.

The current manifest pins are checked offline. A changed pin requires a new
source review and refreshed evidence, rather than silently reusing this audit.
The optional compiler probes report a skip when the required compiler is absent.
No workflow, shared verifier, runtime binding or publication setting is changed.
