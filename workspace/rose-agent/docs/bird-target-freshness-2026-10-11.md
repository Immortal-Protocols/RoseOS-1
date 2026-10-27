# Bird tap freshness — 2026-10-11

Earlier Bird attempts used the manual-touch fallback, but Rose Agent v86 rejected them before dispatch because its full-screen signature changed. Comparing the recorded Reserve observations showed only the live location marker moving from `[488,831,571,931]` to `[493,828,576,928]`; the Reserve target stayed fixed.

## Change and verification

Rose Agent v87 (0.79) permits tap-only geometry changes outside the target neighborhood. It retains the 30-second, single-use snapshot requirement, identical app/window and complete semantic tree, identical target bounds, password checks, and a 24dp exclusion margin. Other operations keep the original whole-screen guard. The implementation is generic, not Bird-specific.

`TargetSnapshotGuardTest` passed: remote map movement allowed; moved/changed/disabled target, nearby geometry changes, new dialogs, changed windows, and incomplete observations rejected. The signed app built successfully and installed through wireless delivery. The phone's control-status endpoint reported version 87 and enabled UI control.

APK SHA-256: `5e5e010d2d99ddc0d3dbc7205949effe1a862d22d74dcff396cde546807c393b`.

## Physical phone results

- A physical-style touch on Bird's menu opened the menu; a screenshot verified it, and Back restored the map.
- User then explicitly requested: “Hit start on this bird.” Fresh inspection identified selected scooter `HGW••`, Start's clickable target 25, and pricing `$1.50 to start and $0.69/minute + tax.`
- `touch_label 302e230f-48b7-45a2-8a8a-094eb75d61b1 Start --node-id 25` dispatched successfully. Follow-up observations showed the scooter-start loading state.
- A subsequent screenshot was viewed and confirmed Bird's **Ride Safe in Los Angeles / Before You Ride** onboarding, with an **I UNDERSTAND** button. No repeated Start tap was sent. The acknowledgement was left to the user, since it includes personal rider eligibility and safety statements.

This verifies a real Start tap reached Bird. It does not confirm an unlocked scooter or completed ride start. No Bluetooth or voice-routing changes were made.
