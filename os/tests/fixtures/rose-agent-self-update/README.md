An unmodified copy of `services/core/java/com/android/server/pm/PackageInstallerSession.java` from GrapheneOS
`platform_frameworks_base` at the revision pinned in `os/pinned-manifest.xml` (`84183aff99be5e156dc0446a6fa2d4a7150f4bc3`,
path `frameworks/base`, tag `2026100600`), read from raw.githubusercontent.com, the route used for the other pinned
fixtures. sha256 `5ff683567eef824f49bb0e6f1eb9cee1c506ad6ac688970d20802506564a1216`.

`os/tests/test_rose_agent_self_update.py` applies `os/patches/rose-agent-self-update-frameworks-base.patch` to it and
runs the patched system-app-update check (issue #202) against stand-ins. A test fixture, not a build input.
