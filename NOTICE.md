# RoseOS licensing and third-party notices

Copyright (c) 2026 RoseOS contributors.

## Original RoseOS material

Unless a file or component carries a different license notice, the original
RoseOS source code, scripts, tests, documentation and original artwork in this
repository are licensed under the MIT License. The SPDX identifier is `MIT`.
The full license text is in [LICENSE](LICENSE). It permits commercial use,
modification and redistribution, including in closed-source products, while
retaining its required copyright and license notices.

This software is provided without warranty. See LICENSE for the complete terms
and conditions.

## Separately licensed material

Existing file and component notices take precedence over the default above.
Third-party code, patch context, fonts, data and branding retain their original
copyright and license terms. This notice does not relicense them or claim
ownership of them. In particular:

| Material | Applicable terms and notices |
| --- | --- |
| Android/AOSP source excerpts, patches, fixtures and files marked `Apache-2.0` | Preserve the notices in those files and their upstream source. The full Apache License 2.0 text is in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt). |
| GrapheneOS and other imported source | Preserve the upstream notices and per-component licenses. [scripts/rose-import.json](scripts/rose-import.json), [scripts/calculator-import.json](scripts/calculator-import.json) and [os/pinned-manifest.xml](os/pinned-manifest.xml) record source provenance. A manifest entry is a source reference, not a license grant for the referenced project. |
| Astronomy calculations in [WeatherAstronomy.java](workspace/rose-agent/app/src/dev/pixel/voice/WeatherAstronomy.java) | Portions adapted from Stardroid v2, copyright 2026 Penterakt LLC, `GPL-3.0-or-later`. Preserve [the imported notice and its additional permission](workspace/rose-agent/app/assets/licenses/weather-astronomy.txt). The full GPL text is in [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt). That notice's third-party brand reservations remain in force. |
| Nunito font files | SIL Open Font License 1.1. See [the app notice](workspace/rose-agent/app/assets/licenses/Nunito-OFL.txt) and [the SystemUI notice](os/overlays/frameworks/base/packages/SystemUI/assets/licenses/Nunito-OFL.txt). |
| ZXing core | Apache-2.0; see [the existing attribution and license](workspace/rose-agent/app/assets/licenses/zxing-core-Apache-2.0.txt). |
| adevtool test fixtures | MIT; retain the accompanying LICENSE files under [os/tests/fixtures/adevtool-17df3b79/adevtool](os/tests/fixtures/adevtool-17df3b79/adevtool/LICENSE), [os/tests/fixtures/frankel-lunch-inputs/vendor/adevtool](os/tests/fixtures/frankel-lunch-inputs/vendor/adevtool/LICENSE) and [scripts/tests/fixtures/fbpack-adevtool-17df3b79](scripts/tests/fixtures/fbpack-adevtool-17df3b79/LICENSE). |
| Other upstream test fixtures | Preserve their accompanying notices, including [hardened_malloc](os/tests/fixtures/yogi-port/hardened-malloc-LICENSE), [libselinux](os/tests/fixtures/yogi-port/libselinux-LICENSE) and [platform_system_core](scripts/tests/fixtures/factory-package/platform_system_core-322ff512/LICENSE). |
| Firefox, Thunderbird and VLC names and prototype icons | Third-party branding, excluded from the default RoseOS artwork license. Source provenance is recorded in [the prototype README](prototypes/rose-hifi/README.md#provenance). This project grants no additional rights to those names or icons. |
| Dependencies obtained by package managers, Android source tools or build scripts | Their own upstream licenses apply. Retain their notices when redistributing them; this repository's default license does not replace their terms. |

The MIT grant covers original RoseOS material under that license; it does not
remove obligations on borrowed code. In particular, the existing Stardroid-derived
WeatherAstronomy component remains GPL-3.0-or-later. Distributing an application
that includes that component requires following its GPL terms; the root MIT
license does not make that component permissive. Historical revisions likewise
retain applicable third-party notices.

The optional sharp/libvips dependency recorded in workspace/package-lock.json
includes LGPL-licensed libraries. Those libraries retain their own license
requirements when redistributed. Android/GrapheneOS projects reconstructed from
the pinned manifest also retain their component licenses.

## Names and branding

Software copyright licenses do not grant trademark rights. GrapheneOS, Android,
Firefox, Thunderbird, VLC and other third-party names and marks remain the
property of their respective owners. Preserve attribution without implying
upstream endorsement of RoseOS.
