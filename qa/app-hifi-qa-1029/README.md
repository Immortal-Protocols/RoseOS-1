# APP-HIFI-QA-1029-Q1 — native vs HiFi comparison (advisory)

- HiFi reference: `ce126f56c8967008b0a5db889137f224ac9ad36c:prototypes/rose-hifi` (tree `050385d9`).
- Native source: main `77878c590e5602dcd6f6b39300c5b018b72477e0`. It includes App PR127 merge `1de7f97` (`RosePresenceView`/`Model`, `RoseConversationActivity`) and OS #131 `HomeTray`.

`prototype-shots/` holds **prototype pixels only**, from the pinned `browser-check.cjs` (53/53 passed). That run used headless Chromium 153.0.8010.12 with the ANGLE/SwiftShader software renderer, a 1280×900 viewport and DPR 1, with the device frame captured. Software rendering is not device GPU evidence.

No native pixels exist. This container has no Android emulator, system image, `adb` or `/dev/kvm`; the SDK is compile-only. `sample-sphere.py` compares rendered HiFi sphere colours with native gradient stops computed from source (source inference).
