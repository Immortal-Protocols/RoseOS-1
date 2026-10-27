# Validation — 2026-10-13

- 17 automated tests passed, including a browser-accurate timer binding regression test.
- Headless Chrome ran the default polling timers and disconnect callback: three polls, no rejected promises.
- Android build and signature verification passed; version 146 is installed on the physical Pixel.
- Wireless control_status reported version 146 and connected accessibility. Diagnostics confirmed Wi-Fi false, cellular true, and a live push stream.
- Live server authentication rejected unauthenticated push with HTTP 401. Disabling push closed an established event stream.
- 13 live inspect_ui commands passed over cellular: five push, five polling with push disabled, and three push after reconnect.
- First push batch median server-create-to-result: 156 ms; polling: 814 ms; reconnected push: 186 ms. Phone execution medians were 33, 29 and 29 ms respectively. These are small warm screen-read samples, not end-to-end voice tasks or predictions for every app.
- All five rollback commands reported deliveryChannel=poll, and all eight push commands reported push. No app reinstall or voice restart was required to switch modes.
- Push left enabled. Unit tests cover duplicate IDs, serial execution, expired server epochs and lost result replies. No new local batching or screen-read policy is enabled.
- App installation ended the previous voice session. The tests above verify phone control independently; they do not claim a newly connected voice session.
