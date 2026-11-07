# Rose remote link (v1): the phone over Tailscale

Rose Agent reaches this Mac from anywhere on your Tailscale network. Connect
Tailscale on both devices using the same tailnet before pairing. Pair once by
scanning the QR code; subsequent app, network and Mac restarts reconnect over
Tailscale. There is no automatic LAN or USB phone connection.

## Setup

1. Start Rose with the real phone: `bash scripts/rose.sh --phone real`
   (or `.venv/bin/python -m rose.app --phone real`). Besides the dashboard on
   127.0.0.1:8091, the log shows `remote: https://0.0.0.0:8443`.
   `--remote-port N` moves the listener, and `--no-remote` turns it off.
2. Click **Pair phone** in the dashboard header, or run
   `.venv/bin/python -m rose.pair` in a terminal.
3. On the phone, open Rose Agent's menu, choose **Pair with Mac**, scan the
   code and confirm. The dashboard then shows "Paired with <phone>".
4. To remove a phone, use the **Remove** button in the same dialog or
   `python -m rose.pair --revoke <id prefix>`. `python -m rose.pair --list`
   lists paired phones.

The QR code lists only the Mac's full Tailscale MagicDNS name and Tailscale
IPv4 addresses (100.64.0.0/10). The running Tailscale client is authoritative;
when it is missing, signed out or disconnected, code creation reports that
Tailscale must be connected. It does not substitute a LAN address. Explicit
`--pair-host` values must also be full `.ts.net` names or Tailscale IPv4 addresses.

An updated Rose Agent skips LAN and USB addresses in existing saved pairings.
If a saved pairing has a Tailscale endpoint, it keeps working with the same key
and certificate pin. LAN-only pairings need a new code. New codes containing
non-Tailscale hosts are refused. This client cleanup requires an updated APK;
restarting the server does not replace the installed client.

Firewall: macOS may ask whether Python can accept incoming connections. Allow
it, or the phone cannot reach port 8443.

## Security model

- **Two listeners.** The dashboard (127.0.0.1:8091) stays loopback-only and
  unauthenticated. Every dashboard request and websocket (including
  `/ws/phone`, `/ws/audio`, `/ws/events`, `/api/realphone` and `/api/pair/*`)
  must carry a loopback `Host` and an `Origin` that is absent or loopback, so a
  web page in your browser cannot drive the phone, read its screen or create
  codes (this blocks cross-site websockets and DNS rebinding). The dashboard's
  unauthenticated `/ws/phone` is shut whenever remote mode is enabled (close code
  4009), even before pairing or when the remote listener fails. Phone control uses signed TLS.
  An unauthenticated link never replaces a signed one. The remote listener (TLS on
  0.0.0.0:8443) serves only `POST /pair`, `GET /hello`, `/ws/audio`,
  `/ws/events` and `/ws/phone`. It never serves the dashboard, static files or
  `/api/*`. A remote connection must send its request headers within 10 s,
  and one IP address may hold at most 16 connections, so idle sockets from
  another LAN or tailnet host cannot lock the phone out.
- **Server identity: a pinned certificate.** On first start, Rose creates a
  self-signed ECDSA P-256 certificate (valid for 10 years). Its SHA-256 is the
  **pin**, and the first 16 hex characters of the pin are the server id. The QR
  code carries the pin, and the phone trusts only a TLS leaf certificate with
  exactly that hash. No CA or hostname is involved.
- **Pairing code.** The QR code also carries a one-time code: 26 base32
  characters (130 bits) from `secrets`. Only one code is pending at a time. It
  expires after 10 minutes, works once, and is destroyed after 5 failed
  `/pair` attempts. Rose keeps only the code's SHA-256, compares it in constant
  time, and keeps it in memory only, so a restart invalidates it.
- **Device key.** At pairing, the phone sends the public half of a P-256 key
  that it holds in Android Keystore secure hardware (StrongBox or TEE). The
  device id is the SHA-256 of that key's SPKI DER.
- **Every request is signed.** This covers every websocket upgrade and
  `/hello`. The phone sends `X-Rose-Device`, `X-Rose-Time` (unix ms),
  `X-Rose-Nonce` (16 random bytes, base64url without padding) and
  `X-Rose-Signature` (base64 DER SHA256withECDSA) over:

  ```
  "rose-auth-v1\n" + METHOD + "\n" + PATH(+?query) + "\n" + TIME + "\n" + NONCE + "\n" + PIN
  ```

  The server checks, in this order: the device is known and not revoked; the
  time is within ±120 s; the nonce was not seen in the last 240 s (a bounded
  cache that refuses requests when it is full); the signature verifies. A
  failed websocket check is refused before accept: **HTTP 403** only for an
  unknown or revoked device (pair again), and **HTTP 409** for a refusal that
  can clear up by itself (clock off by more than 120 s, a replayed nonce, a full
  nonce cache, missing or malformed headers, a bad signature), so the phone
  keeps retrying. A 409 carries `X-Rose-Error` (`stale-time`,
  `replayed-nonce`, `nonce-cache-full`, `missing-or-malformed-auth-headers` or
  `bad-signature`) and `X-Rose-Server-Time` (the Mac's unix ms). A failed
  `/hello` check returns 401 with `{"error", "final"}` (`final` is true for an
  unknown or revoked device), the same two headers, and `X-Rose-Error`
  `unknown-device` or `device-revoked` for the final cases. Including the pin
  in the signed string ties each signature to this Mac.
- **Revocation** takes effect at once: Rose closes the phone's open websockets
  (close code 4401), and its next handshake gets 403. Pairing the same key
  again with a new code restores it. A connected phone's "last seen" is
  refreshed every minute while its websockets stay open and when they close.
- **Storage.** Everything is kept in `~/.config/rose-local/`, which you can
  override with `ROSE_CONFIG_DIR`. The directory is 0700. Its files are
  `tls-key.pem`, `tls-cert.pem` and `devices.json`, each 0600 and written
  atomically. Nothing is stored in Git. Logs show device-id prefixes and
  addresses, never codes, nonces or signatures. Losing the TLS files changes
  the pin, so every phone must pair again.

## Rose's workspace over the link

Rose's tasks drive the phone's background controller
(`dev.pixel.backgroundcontrol`, `rose/background.py`) with the same signed
link: each operation is an ordinary phone command,
`{"action": "app_command", "packageName": "dev.pixel.backgroundcontrol",
"operation": "get_state"|"start"|"command"|"stop", "params": {...}}`, which
Rose Agent passes to the controller unchanged. No new port, endpoint or
permission is involved. While the phone is locked or its screen is off, Rose
Agent refuses `start` and `command` (status `locked`) unless the owner allowed
app control while locked; Rose reports that and waits for the owner. After a
reconnect Rose asks `get_state` again and continues in the workspace that is
still open. `GET /api/realphone` (localhost only) shows the workspace as Rose
sees it under `background`; `?look=workspace` adds the controller's own
`get_state` (read-only).

## Wire reference

- QR / deep link:
  `roseagent://pair?v=1&name=<pct-enc name>&hosts=<pct-enc hosts, comma-separated>&port=8443&pin=<64 hex>&code=<26 base32>`
- `POST /pair` takes `{"code","publicKey","name","model"}` (at most 4096
  bytes). It returns 200 with `{"deviceId","serverName","serverId"}`, 403 for a
  bad, expired or used code, and 400 for a malformed body or key.
- `GET /hello` (signed) returns
  `{"serverName","serverId","version":1,"phone":"real"|"fake"}`.
- Localhost only:
  - `POST /api/pair/new` returns `{uri, png, expiresAt, hosts, serverName, pin}`.
  - `GET /api/pair/status` returns the invite's status.
  - `GET /api/pair/devices` returns the paired phones.
  - `POST /api/pair/revoke` takes `{"deviceId"}`.

The QR encoder (`rose/qr.py`) is pure Python and uses byte mode, level M and
versions 1 to 20. When choosing a mask, it also avoids finder-like crosses in
the data area, which can mislead ZXing's detector.

Tests (no models needed):

- `.venv/bin/python -m rose.tests.test_remote`
- `.venv/bin/python -m rose.tests.test_qr` (decodes every code with ZXing and
  needs `java` and `.tools/zxing/core-3.5.4.jar`)
