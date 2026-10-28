Unmodified copies of six files from GrapheneOS `platform_packages_apps_Seedvault` at the revision pinned in
`os/pinned-manifest.xml` (`7cfffed7ceeda60a10bf2a6d9818d6fda81828ba`, path `packages/apps/Seedvault`), read from
raw.githubusercontent.com, under their own SPDX (Apache-2.0) headers. The same revision's `Android.bp` and
`contactsbackup/Android.bp` are byte-identical to OS's fixtures in `os/tests/fixtures/cuttlefish-alpha/seedvault/`.

| File | sha256 |
| --- | --- |
| `app/src/main/AndroidManifest.xml` | `988628603420cc880898ae4614133fb68fd3f2390f64c57661ed34cbac16be0a` |
| `contactsbackup/src/main/AndroidManifest.xml` | `a18a7b2c5a0b9d796e91041ce334a116c7fb297cf36622353b108a17c70dd135` |
| `allowlist_com.stevesoltys.seedvault.xml` | `f8504e5245c36bdb3c92e00673223389f0e682001d0e566ea75cbef492fe60b6` |
| `default-permissions_com.stevesoltys.seedvault.xml` | `71990ea92b64466254b59f8ebf9de3d61b7b3cc94f4d2e64d03b1bb1b3ef9b52` |
| `permissions/com.stevesoltys.seedvault.xml` | `1d73dfacbbb9a61ef6cac478f6f434013282de2aff9aa745961a4eddc347e4ff` |
| `contactsbackup/default-permissions_app.grapheneos.backup.contacts.xml` | `21d6003e95bfdbcf94d75b613b26a1a8296057a4ca5cc508c23d47dab257847c` |

`scripts/tests/test_emulator_alpha.py` derives the Seedvault closure expectations in
`os/release-pipeline/cuttlefish-alpha.json` (`imageVerification.closure`) from these files and OS's Android.bp fixtures:
the package names from the manifests, the XML root elements and packages from the XML files, and the install paths
from the module definitions. They are test fixtures, not build inputs.
