# Send clipboard to Pixel

Text-only, user-triggered transfer from an iPhone Shortcut to an authenticated
Mac HTTPS relay and the Pixel's local clipboard receiver. No model, voice task,
conversation log, memory extraction, or third-party clipboard service handles
the content.

The sender and receiver use separate random credentials. HTTPS uses a publicly
trusted IP certificate; the existing voice server certificate is unchanged.
The relay keeps only one transfer in memory, discards it on delivery, replacement
or a 12-second timeout, and records no clipboard text. Only transfer IDs, status
and request counts are available for diagnostics.

## Standard Android clipboard

The receiver uses `ClipData.newPlainText`, so both direct `Item.getText()` readers
and Android's `coerceToText()` path receive actual text. It marks transfers with
`EXTRA_IS_SENSITIVE` to hide previews where supported. Android manages retention
and clearing; there is no app-specific five-minute expiry or clipboard history.
The transport still rejects an undelivered transfer after 12 seconds, and the
relay discards its memory copy after delivery. That delivery deadline does not
expire text already copied onto the phone.

The user explicitly chose this behavior on 2026-10-17 after the URI-only clipboard
failed in Thunderbird's Compose password field while working in a native search
field. The old expiring ContentProvider was removed instead of adding per-app
paste workarounds. A received clipboard item is not proof that an app pasted it;
validate using the destination's actual text field. No field should be read back
or logged when it contains a secret.

## Installation

- Runtime: `~/.local/share/pixel-clipboard/`.
- Private configuration, certificate, signed Shortcut: `~/.config/pixel-clipboard/`.
- LaunchAgent: `local.pixel-clipboard`.
- Public TLS port: 9447. HTTP port 80 maps to 18080 solely for ACME validation.
- Certificate renewal runs every six hours and loads changed certificates
  without restarting the receiver. The current IP is fixed in the Shortcut;
  if the ISP changes it, the certificate and Shortcut must be regenerated.
- `build_shortcut.py` generates/signs **Send to Pixel** into the private directory.
  After importing it on the iPhone, use Settings → Action Button → Shortcut →
  Send to Pixel. First-run clipboard and destination prompts need approval there.

## Rollback

`python3 pixel-clipboard/toggle.py off` stops new transfers. It does not change
voice or other phone controls. `on` restores them. The original phone APK and
source files are backed up under the directory named by
`~/.config/pixel-clipboard/rollback-path`.

For complete removal, unload `local.pixel-clipboard`, remove only this feature's
port mappings (9447 and the ACME port 80 mapping), remove the Shortcut, restore
the saved source, and build/install a higher Android version code. Do not
replace or remove the independent Mac-screen service on port 9446.

## Validation

`node --test pixel-clipboard/store.test.mjs` checks receipt, replacement, delivery
deadline, input limits and rejection without cancelling a valid pending transfer.
Native phone validation of the standard-text update is recorded in
`experiments/clipboard-standard-text/` after deployment. Use harmless text for
paste tests, not an API key, and do not enable email preparation during the test.
