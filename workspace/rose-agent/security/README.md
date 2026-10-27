# Device pairing

Rose Agent 221 replaces the three credentials previously packaged in the APK
with one device identity generated in Android Keystore. It requires a
hardware-backed, non-exportable P-256 private key. The existing pinned voice
server certificate remains the phone's trust anchor. The screen service has its
own public certificate pin; clipboard keeps normal HTTPS certificate and
hostname verification, so its renewed public certificate continues to work.

The TLS key permits `DIGEST_NONE` as required by Android's TLS implementation,
which hashes handshake messages before asking Keystore to sign. Build 220
omitted this and failed on-device before its pairing request reached the Mac.
Build 221 uses a fresh alias with the correct immutable key authorizations.
Reference: https://developer.android.com/reference/android/security/keystore/KeyGenParameterSpec.Builder#setDigests(java.lang.String...)

All three servers request a TLS client certificate. `rejectUnauthorized: false`
allows a new self-signed device certificate to reach the enrollment endpoint;
it is **not authorization**. Protected HTTP routes must call `DeviceAuth`, which
checks the TLS peer's public-key fingerprint against the locally approved list.
TLS proves possession of the private key; an HTTP header cannot impersonate it.
Existing short-lived LiveKit/OpenAI session credentials remain unchanged.

## Pair and migrate

1. `node rose-agent/security/pair.mjs migration` explicitly permits the old
   remote credentials for at most an hour while installing the first update.
   Without this setting, remote bearer-token access is denied by default.
2. Deploy the auth module and updated server to all three services; publish
   the APK through the existing signed wireless delivery path.
3. `node rose-agent/security/pair.mjs invite` displays a one-use, 12-character
   code on the Mac. It expires in ten minutes, with ten total failed attempts.
   Type it in Rose Agent's **Pair with Mac** screen and tap **Pair this Pixel**.
   A fresh protected request verifies the pairing before the phone saves success.
4. Verify device diagnostics, a normal voice request, and clipboard/screen
   connections. Run `node rose-agent/security/pair.mjs finalize` to remove the
   migration window and rotate all three formerly packaged credentials. Restart
   the services to disconnect old streams and discard cached host credentials.

The local Codex helpers continue using `~/.config/pixel-voice/pair-token`, but it
now works only over loopback and is never copied into the APK. Keep it private.
The clipboard sender's separate credential is unchanged: it is not in this APK
and authorizes only the existing send route.

`pair.mjs status` lists public device IDs. `pair.mjs revoke DEVICE_ID` revokes
one phone. Restart services after revocation to terminate already-open streams.
Uninstalling the Android app deletes its key and requires pairing again; updates
preserve it. A revoked phone can re-pair only with a fresh code from the Mac.

## Runtime copies

The voice server executes directly from this workspace. Clipboard and screen
execute installed copies. Copy `device-auth.mjs` to
`~/.local/share/rose-agent/security/`, clipboard's `server.mjs` to
`~/.local/share/pixel-clipboard/`, and screen's `relay/server.mjs` to
`~/.local/share/rose-agent/mac-screen/relay/` before restarting their LaunchAgents.
The screen installer also installs the shared module.

## Verification

`node --test rose-agent/security/device-auth.test.mjs` exercises real TLS:
unpaired access denied, one-time enrollment, key possession, code reuse denied,
legacy-key retirement, local-only host token, and device revocation.
`build.sh` refuses connection assets containing token/secret/password fields.
Inspect the signed APK for old secret bytes before publication.

These changes protect the connection, not a compromised unlocked Mac or an
attacker executing code inside the authorized Rose Agent process.

## Exceptional download recovery

`pairing-recovery.json` is a manually created, expiring record of one signed,
credential-free Rose Agent release. While present and unexpired, the server
temporarily stops requesting TLS client certificates and serves only that
release's manifest and APK without authentication. Every other route keeps its
normal authentication. This lets a client with a broken TLS signing key install
the corrected build. Older APKs (which contain credentials) are never exposed.
The server exits at expiry so LaunchAgent restores client-certificate requests.
Remove the file and restart immediately after the corrective APK is delivered.
This is not the routine update path, nor a substitute for successful pairing.
