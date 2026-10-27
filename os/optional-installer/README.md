# Optional fast-install helper

The normal screen-control workflow can press Android's Install button directly
with the modern background OS patch. This helper is a separate, older path used
by the Play Store fast tool. It is included for source completeness; it is not a
requirement for general app control and is not automatically enabled.

The fast tool's current operation waits for installation, verifies the installed
package, and retires that session's confirmation dialog. Its remaining package
name/label mismatch fix has not passed a fresh end-to-end test. Use normal screen
control if it cannot finish an install. Do not advertise this path as faster or
fully verified based only on its unit tests.

To build the helper, run `scripts/prepare-installer-helper.py --help`. Give it
your signed Rose Agent APK and the target SDK. It produces a local source copy
with your own client signing fingerprint. Apply that copy's `frameworks-base.patch`
to the matching clean framework source and copy its two source files into
`packages/PackageInstaller/src/com/android/packageinstaller/`. Use your normal
OS build and signing process. Do not blindly replace the installed system app
or use another developer's key. The source's upstream Google Play signing
fingerprint is public; the generated Rose Agent fingerprint is your own.

After building and signing PackageInstaller, obtain its SHA-256 signing
fingerprint using `apksigner verify --print-certs`. Pass it to
`scripts/configure-local.py --installer-cert-sha256 ...` while retaining the same
backend endpoint and private directory. Rebuild Rose Agent. Its generated,
ignored `installer-identity.json` identifies your installer; it never embeds the
original developer's signature. Re-run the ordinary install test through Pixel
Voice on your own phone before relying on the helper. Device-owner enrollment
and the helper's explicit enablement are also required by this legacy path.

The helper uses pinned installer internals and has not been rebuilt independently
as part of this export. The code and patch are reference material for the matching
OS source; they are not a portable APK or a substitute for the direct workflow.
