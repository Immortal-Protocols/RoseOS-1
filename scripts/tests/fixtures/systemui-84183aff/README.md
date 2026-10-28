Unmodified copies of files from GrapheneOS `platform_frameworks_base` at the revision pinned in `os/pinned-manifest.xml`
(`84183aff99be5e156dc0446a6fa2d4a7150f4bc3`, path `frameworks/base`), read from raw.githubusercontent.com (INFRA-CANDIDATE-115).
`scripts/tests/test_release_candidate_contract.py` derives the `systemUiTray` gate in `os/release-pipeline/frankel.json`
from them. They are test fixtures, not build inputs.

| File | Upstream path | sha256 |
| --- | --- | --- |
| `Shell-Android.bp` | `libs/WindowManager/Shell/Android.bp` | `fc3a3c4de8e8ffbfc4832125b6a7b448a50fea1e0b5c6bad8ceb767b55679738` |
| `SystemUI-Android.bp` | `packages/SystemUI/Android.bp` | `758d66bbe5624eb28e05bb1411c57d25b69a3ed8a473a76b9fb3fa6fd69d5412` |
| `SystemUI-proguard_common.flags` | `packages/SystemUI/proguard_common.flags` | `918c97567aa9b7ced57f895446a909d2a399dd2c248c042e9fda7b2e4ca530d2` |

What they establish:

- **Source list.** `filegroup wm_shell-sources` is `src/**/*.java`, feeding `wm_shell_protolog_src` and
  `WindowManager-Shell`. The Agent tray overlays copied to `frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/`
  are therefore compiled with no build-file change.
- **APK.** `android_app SystemUI` is `system_ext_specific`, `privileged` and multi-dex, so it lands at
  `SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk`. `WindowManager-Shell` reaches it through its static libraries.
- **Names in dex.** SystemUI's proguard rules keep `com.android.wm.shell.*` class names and strip only verbose logs
  (`Log.v`, `-maximumremovedandroidloglevel 2`). The class descriptors of `AgentTrayPrototype` and `AgentTrayLink`
  and the `Log.w` literal that OS #85 added therefore survive optimization.
