Unmodified copies of upstream files at the revisions pinned in `os/pinned-manifest.xml`, read with `git fetch` of
the exact commits (INFRA-HOME-098). `scripts/tests/test_permission_apex.py` derives the `permissionApex` expectations
in `os/release-pipeline/frankel.json` from them. They are test fixtures, not build inputs, and they hold no private
key: the `.avbpubkey` is the module's public AVB key.

| File | Upstream (path, revision) | sha256 |
| --- | --- | --- |
| `Permission/Android.bp` | `packages/modules/Permission` `4d6005dbabf3f2bf96e5215c5c2801809b051bf3` | `c42c9b4a3f0394b7170ee261b1942aad5ceab0f6c45930f27d1a88f5a733eaa6` |
| `Permission/apex_manifest.json` | same | `d6d2468af2a5c5a8edfda093668626aec29160617208721a410e24d7a1a84486` |
| `Permission/com.android.permission.avbpubkey` | same | `1ab7920a9fcfc8611cbb8fdee454ff6d4b4d6d09d70ee0a90aaf05cd4b84e3ab` |
| `build-release/release_config_map.textproto` | `build/release` `0d3e510c9a11b9376b9c155f3425a64182798c22` | `94061b3c7d8000ff38a26345ec6b5a8107216e9754d1f3f0cc15b196c13843ed` |
| `build-release/release_configs/cur.textproto` | same | `c2fdf302ccfe836de3b339b4c3d21c157103c12c3be725df0d0bb66b5acccb0d` |
| `build-release/release_configs/cp2a.textproto` | same | `1a6905eafca4677efbf78a176721ead1e8d8dab832e5f0539371db191ee1c252` |
| `build-release/flag_values/cp2a/RELEASE_DEFAULT_UPDATABLE_MODULE_VERSION.textproto` | same | `12e1b290191116f5ee0a2daf20d844495ae544865d9c3a467dd8c21196583777` |
| `build-release/flag_declarations/RELEASE_APEX_CONTRIBUTIONS_PERMISSION.textproto` | same | `02076c73bef2410a87150ef7c908d116603d9b4b05bea69e5cb725aff4508965` |
| `build-release/flag_declarations/RELEASE_DEFAULT_MODULE_BUILD_FROM_SOURCE.textproto` | same | `1ec607219b61fc3d917f4f1ed884f358977dc944bc51a26db68387941378f1f3` |
| `build-release/gms_mainline/required/flag_values/cp2a/RELEASE_APEX_CONTRIBUTIONS_PERMISSION.textproto` | same | `0fefcfeae555c66b8e2b3149cb394d32c0ad43abb6c323f812ed088ce7927505` |
| `script/generate-release.sh` | `script` `09d651841b55e42b9cee7392f0ce29ebda8c8fb5` | `01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311` |

What they establish, together with files read but not copied (sha256 at the same pins):

- **Module and key.** `apex { name: "com.android.permission" }` uses `apex_key` `com.android.permission.key`, whose public
  key the build copies into the APEX as `apex_pubkey`. An unsigned source build therefore carries this `.avbpubkey`.
  The payload holds `javalib/service-permission.jar` (`systemserverclasspath_fragment` contents `service-permission`,
  `service/Android.bp` `a06c9a0a…1b1149`).
- **Version.** `apex_manifest.json` has the placeholder version 0, which Soong replaces with
  `RELEASE_DEFAULT_UPDATABLE_MODULE_VERSION` (build/soong `250710f7` `apex/builder.go`, `-se version 0 ${default_version}`).
  `frankel-cur-user` resolves `cur` -> `aosp_current` -> `cp2a`, whose value is 370399999; `flag_values/cur` sets
  only kernel directories and `RELEASE_BUILD_USE_VARIANT_FLAGS`.
- **Source, not prebuilt.** `RELEASE_DEFAULT_MODULE_BUILD_FROM_SOURCE` defaults to true and
  `RELEASE_APEX_CONTRIBUTIONS_PERMISSION` has no default. Only the separate `gms_mainline` release-config map selects
  `permission.google.contributions.prebuilt`, and build/make `b0f697df` `core/release_config.mk` adds such maps only
  through `PRODUCT_RELEASE_CONFIG_MAPS`. adevtool `17df3b79` sets none. Its `file-exclusion.yml` (`73370dd3…646e4a`)
  drops the stock `system/apex/com.google.android.permission.apex` and `.capex`. The pinned manifest has no
  `prebuilts/module_sdk` project. `base_system.mk` (`37cd71a2…741634`) lists `com.android.permission`, and
  `updatable_apex.mk` (`01f326df…e7b252`) sets `OVERRIDE_PRODUCT_COMPRESSED_APEX := false`. The gate still accepts a
  `.capex` and checks its `original_apex`. None of this is build evidence: the artifact gate is what proves a given
  build. A prebuilt would carry another name, version or key and no RoleService markers.
- **Signing route.** `generate-release.sh` signs `com.android.permission.apex` with `--extra_apks ...=$KEY_DIR/releasekey`
  and `--extra_apex_payload_key ...=$KEY_DIR/avb.pem`. These are the keys behind the existing OTA certificate pin
  (`releasekey.x509.pem`) and the AVB key pin (`avb_pkmd.bin`). releasetools `apex_utils.py` writes
  `avbtool extract_public_key` of the payload key as `apex_pubkey`.
