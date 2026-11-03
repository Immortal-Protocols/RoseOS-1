# Mustang host-configuration shape correction

Source inspection while preparation run `41719106957` was active found a
deterministic error in `mustang_integration.other_builder_paths`. The first five
referenced release configs nest path fields under a `host` object, while
`os/build-only/blazer-prep-host.json` uses `host: "immortal-os-builder"` as a label
and stores `sourceRoot` and `downloadDir` at the top level. The adapter called
`.get` on that string. Rendering could not reach reconciliation or compilation
with that source. No compilation success was observed.

Recognize both existing configuration shapes and retain the relevant protected
paths, including the flat preparation config's download directory. Unsupported
host values still refuse. The existing hosted `describe` guard also reads this
configuration metadata before builder startup, as does the unsigned runtime
before creating an attempt. Other devices' configs and paths are unchanged;
no artifact or receipt from another device is consumed.

The affected Mustang preparation attempt must be stopped through GitHub's normal
run cancellation, followed by its existing ownership-aware cleanup. Its partial
data remain preserved; no source tree or allocation is adopted. A fresh attempt
may start only after that run and cleanup finish, using the corrected main
source and the same FIFO. No healthy work belonging to another run is cancelled.
The completed key bootstrap `41719073037` must not be repeated.

Review consists of source/configuration inspection and parsing, plus the retained
read-only configuration admission. Focused/negative tests, existing-device
regression, required CI/full suites and the sprint's other verification omissions
remain undone. The failed configuration attempts do not reset the sprint clock.
