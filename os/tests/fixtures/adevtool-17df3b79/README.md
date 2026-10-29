Pinned public inputs for `os/tests/test_device_contract.py` (FAM-03a), which checks `os/devices/pixel10.json`.

- `adevtool/`: files from [GrapheneOS adevtool](https://github.com/GrapheneOS/adevtool) at
  `17df3b79a73e67ad68387b35c7d41c85e1d8103c`, the `vendor/adevtool` revision in `os/pinned-manifest.xml`.
  adevtool is MIT licensed; its `LICENSE` is copied unchanged.
- `platform_build/`: product makefiles from [GrapheneOS platform_build](https://github.com/GrapheneOS/platform_build) at
  `b0f697df8cc9519408ca0a404e918cd51bde0735`, the `build/make` revision in the same manifest (Apache-2.0, headers kept).

`PROVENANCE.json` lists every file with its repository, revision, upstream path and git blob id. Full copies are
byte-identical to upstream: their blob ids equal `git ls-tree` at the pinned revision. Three generated device product
makefiles (about 830 KB each) and the adevtool build index are kept as `*.excerpt.json`: the selected lines keyed by
their upstream line number, plus the full upstream file's git blob id and sha256. The test recomputes every fixture's
blob id, so any edit fails it.

No factory image, OTA, vendor binary or proprietary dump is included. The adevtool skeleton and build-index files are
public text that name files and record published image digests; the digests here are index values, not proof that
anything was downloaded or verified.
