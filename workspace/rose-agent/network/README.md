# Cellular transport workaround

2026-10-09: the Pixel passed wireless controls over Wi-Fi but timed out over Google Fi cellular. The Mac accepted cellular TCP connections; initial TLS replies remained unacknowledged (1289 bytes queued). This indicates a transport-path stall; the exact carrier/router cause is not proven.

A transparent relay now listens on 9443 and forwards to Rose Agent on loopback 19443. The original certificate, pinning, and bearer authentication remain unchanged. Relay uses TCP_MAXSEG 1000 plus 512-byte downstream writes spaced 10 ms apart. MSS alone did not restore communication; paced writes did. This conservative workaround limits downstream throughput (roughly 50 KB/s per connection); bulk app downloads have not been performance-tested.

LaunchAgents: local.pixel-voice.server and local.pixel-voice.mobile-relay. Runtime copy is ~/.config/pixel-voice/tcp_proxy.py because macOS blocked launchd Python access to Documents. Canonical source is tcp_proxy.py here. The pre-relay server plist is preserved alongside its current plist as .pre-mobile-relay.

Rollback: unload both LaunchAgents; restore the saved server plist (original port 9443); reload only the server. Do not leave relay and original server competing for 9443. The anime service already uses 9444 and was not modified.

Initial cellular control_status passed after paced relay activation. No physical phone software or OS update was made.

Subsequent agent app-list request timed out again, with initial TLS replies unacknowledged. The workaround is NOT reliable and was rolled back: relay unloaded and its LaunchAgent renamed .disabled; original voice server restored on 9443. Earlier three successful probes do not establish a fix. Cellular cause remains unresolved.

## Active solution: TLS compatibility setting
The original direct server now caps TLS at 1.2 via PIXEL_TLS_MAX_VERSION in local.pixel-voice.server. TLS certificate, pinning and bearer authentication remain unchanged; no plaintext transport. The paced proxy is disabled. Cellular peer remained 172.56.181.65 during tests. Repeated control_status requests passed, and the real Codex backend independently opened Solitaire and verified the game board with its previous state preserved. This supports a TLS handshake/path compatibility issue; it does not isolate whether the carrier, router or client causes it. No Tailscale changes were made.
