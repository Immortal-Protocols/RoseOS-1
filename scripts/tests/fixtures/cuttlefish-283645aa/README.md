Unmodified copies of two files from AOSP `device/google/cuttlefish` at the revision pinned in
`os/pinned-manifest.xml` (`283645aacf6cdb56cc31a9362f54d412dbc132a1`, android-17.0.0_r1), read from
android.googlesource.com, under their own Apache License 2.0 headers:

- `build/cvd-host-package.go` (sha256 `c9f465dbd93b0074e61e34d428567284b262f72c99b0d08e286b61026541066b`): the Soong
  plugin that registers `cvd_host_package`, installs `<module>.tar.gz` into the host install directory and declares the
  `hosttar` phony for the vsoc board platforms.
- `build/Android.bp` (sha256 `c621add7c93adefe07669357abae16a8f4f040779797a5ebb9c8b8398ce705b1`): declares the
  `cvd-host_package` module.

`scripts/tests/test_emulator_alpha.py` copies them into a synthetic tree to test `emulator-alpha.py`'s source checks.
They are test fixtures, not build inputs.
