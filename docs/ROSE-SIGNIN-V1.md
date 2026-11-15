# rose-signin-v1: sign-in for servers that Rose generates

**Status: decided contract.** Nathan directed OS Core to decide the implementation details (2026-11-14); the decisions below are settled, not interim.

- OS Core owns this text and `workspace/rose-signin/`.
- App Core owns integrating it into generated apps.
- Chariot's existing owner owns the hosting it runs on.

This document and the library are source only. Nothing here is built into RoseOS, deployed to a Chariot or exercised on a phone.

**Name.** `rose-signin-v1` is the sign-in protocol for apps Rose generates. It is unrelated to `rose-auth-v1`, the existing phone↔server request-signature wire prefix (`AUTH_PREFIX` in `prototypes/voice-phone-demo/rose/remote.py`, `REMOTE.md`, `LocalRoseAuth.VERSION`, `workspace/models/rose_cloud/rose_cloud/contract.py`). That prefix is installed on phones and stays byte-for-byte unchanged; nothing here renames or migrates it. `workspace/rose-signin/java/test.sh` checks both: the new code never uses the old prefix, and the old prefix is still present where it is defined.

Project 1 Level 2 needs per-user sign-in for the client/server apps Rose generates. Nathan confirmed the factors: **an authenticator-style one-time code plus a PIN/passphrase**. This contract defines them precisely enough that:

- every generated server verifies them the same way;
- a RoseOS phone and a stock Android phone enroll and sign in with equivalent flows.

## What is decided, what is proposed, what is open

| Item | State |
|---|---|
| Factors: one-time code + PIN/passphrase | **Decided** (Nathan) |
| One-time code algorithm: TOTP, RFC 6238, v1 profile | **Decided** (D4) |
| Server: Rust + SQLite, with the reference SQLite store in this library | **Decided** |
| D1–D10 below | **Decided** (D9 key placement is the Chariot owner's; the library defines only the sealing format) |

## Factors

**One-time code (possession).**
- Profile v1: HMAC-SHA-1, 6 digits, 30-second steps.
- The server accepts the current step and one step either side (clock skew).
- **Each accepted step is consumed per account.** A code, or any earlier code, is never accepted twice.
- The secret is 160 random bits, created on the server at enrollment and shown once as an `otpauth://totp/` URI (QR or text).
- The server keeps it **sealed** (encrypted) at rest: it must be recoverable to verify codes, so it cannot be hashed. See **Sealing** below and D9.

**PIN/passphrase (knowledge).**
- Verified **server-side** against an Argon2id hash: 19 MiB memory, 2 passes, 1 lane, random 16-byte salt, PHC string.
- Length: 8 to 128 Unicode scalar values.
- The plaintext travels only inside the TLS sign-in request. It is never stored, logged or returned, and never kept on the device for autofill.
- **It is not a second stored value filled in silently: the user types it, and the server checks it.**

**Both factors are checked on every attempt.**
- A failure never says which factor failed, or whether the account exists. Unknown accounts are checked against a decoy hash, so they cost the same work.
- A valid code is consumed even when the passphrase is wrong, so **one code buys one passphrase guess**.

## Accounts and isolation

- An account is the pair **(app, account)**. Issuer, app and account names are 1 to 128 bytes, with no `:` (the `otpauth` label's separator) and no control characters. Rows, rate counters and sessions are keyed by both.
- A session resolves to a `Principal { app, account }`, and **every data query a generated server makes is scoped by the principal**. App Core's generated servers must carry this through every route.
- Isolation tests show:
  - the same account name in another app is a different account;
  - one account's code cannot open another's;
  - revoking one app's account leaves the other's sessions alone.
- **The app owner's deployment authority is not an account.** Deploy, logs, restart and rollback are Chariot tools held by the creator's Rose server agent. No creator credential and no privileged server secret is embedded in any APK. Recipients get only their own account.

## Flows

All requests go to the generated app's server endpoint over an authenticated, encrypted transport. How a phone reaches a server on a Chariot is the Chariot owner's (today pods accept no network ingress); the library is transport-agnostic.

| Step | Request | Server behaviour |
|---|---|---|
| Invite | Creator's owner tooling only: `create_invite(app, account)` | A single-use invite token for exactly that account, valid 7 days, stored as its SHA-256. It is never a public route: only the app's creator issues invites. |
| Admission | `invite` in Enroll 1, or open sign-up (D7) | `Admission::Invite(token)` (the default) is checked before anything about the account, so without a valid invite every answer is `not_admitted`. It spends the invite at Enroll 1; if the enrollment is then refused (`already_enrolled`, `enrollment_pending`) or fails, the transaction rolls back and the invite survives. `Admission::Open` when the app opted into open sign-up. Restarting one's own pending enrollment with its handle needs no admission. |
| Enroll 1 | `POST /signin/v1/enroll {account, passphrase, invite?, handle?}` | Checks the name and length policies and hashes the passphrase. Creates a 160-bit secret, 10 recovery codes and a 256-bit **enrollment handle**. Returns all three **once**, with the `otpauth://` URI. The account stays unconfirmed. For 15 minutes the pending enrollment belongs to whoever began it: a restart without its handle gets `enrollment_pending`, so a second caller cannot swap in their own secret. A confirmed account cannot be re-enrolled this way. |
| Enroll 2 | `POST /signin/v1/enroll/confirm {account, handle, code}` | Needs the enrollment handle. A valid code proves the authenticator holds the secret, consumes that step, and confirms the account. Every failure, including a confirmed or unknown account, is `not_confirmed`. Attempts are rate-limited per account. |
| Sign in | `POST /signin/v1/session {account, code, passphrase}` | Applies rate limits, checks both factors, and on success returns `{token, account}`. The token is 256 random bits, base32 encoded. |
| Use | `Authorization: Bearer <token>` | The server looks up SHA-256(token) and checks absolute and idle expiry. A server for one app uses `validate_for(app, token)`: another app's token is rejected and left untouched. |
| Sign out | `DELETE /signin/v1/session` | Revokes this session (`revoke_for(app, token)`: only a session of this app). |
| Sign out everywhere | `DELETE /signin/v1/sessions` | Revokes every session of the principal. It also runs after any factor change. |
| Lost authenticator | `POST /signin/v1/recover/authenticator {account, recovery_code, passphrase}` | A recovery code plus the passphrase. On success: a new authenticator secret is **staged** (the account stays confirmed, so nobody can enroll over it), every session is revoked, and the response is like Enroll 1 (`otpauth://` URI, handle, new recovery codes). Enroll 2 with the handle and a code from the new authenticator replaces the old secret and signs out every session again. While the recovery is pending (15 minutes) the old authenticator, declared lost, is suspended: it neither signs in nor counts for passphrase recovery. If the recovery expires unconfirmed, the old authenticator works again. The recovery code is spent even if the passphrase is wrong. |
| Forgotten passphrase | `POST /signin/v1/recover/passphrase {account, recovery_code, code, new_passphrase}` | A recovery code plus a fresh one-time code set a new passphrase. Every session is revoked; the response is the new recovery codes, shown once. Each of the two codes is spent whenever it is valid, even if the other is not. |

**Errors.**
- `401 {"error":"sign_in_failed"}` for any factor or account failure.
- `429 {"error":"try_later","retry_after":N}` plus a `Retry-After` header when rate-limited.
- `401 {"error":"session_invalid"}` for an unknown, expired or revoked token.
- `503 {"error":"auth_unavailable"}` when the sealing key or the store is unavailable (`Unavailable`): nothing was written and no attempt was counted. Servers also report themselves not ready while `Authenticator::ready()` is false.
- Enrollment errors: `passphrase_too_short`, `passphrase_too_long`, `invalid_name`, `enrollment_pending`, `not_admitted` (no valid invite), `already_enrolled`, `not_confirmed`, and `try_later` (confirmation rate limit).
- Recovery errors: `401 {"error":"recovery_failed"}` for a wrong recovery code or second factor, or no such confirmed account (indistinguishable); `try_later` when limited (the client limit always applies; the account limit is skipped when a valid recovery code is presented, as for sign-in); `passphrase_too_short` / `passphrase_too_long` for a new passphrase; `auth_unavailable`.
- `already_enrolled` tells a would-be enroller that the name is taken. That is unavoidable under open sign-up; invite-only admission (D7) hides it from anyone without an invite.

The HTTP shape above is the contract. The library (`rose_signin::flow::Authenticator`) implements the behaviour behind it, and App Core's generated server maps routes to it.

## Limits and sessions

**Rate limits**:
- Counted per account and per client (for example, source address).
- 5 free failures, then the delay doubles from 30 seconds up to 1 hour. Failures are forgotten after 2 hours without another.
- The **client** limit applies to every attempt.
- The **account** limit applies only to attempts **without a valid one-time code**. It slows code guessing against a known account, but an attacker who knows only the name cannot keep out the real user, who presents a valid code.
- Passphrase guessing stays bounded because each code is consumed after one guess.
- There is **no hard lockout**. A success clears the account's counter.

**Sessions**:
- 30 days absolute, 7 days idle.
- The server stores only the token's SHA-256. Revocation is per session or per principal.

## Stock Android and RoseOS

**Every phone.** The same server protocol and the same pure-Java client core (`workspace/rose-signin/java`, no `android.*`). On every phone the generated app:
- shows the enrollment URI as a QR and as copyable text, so any standard authenticator app can enroll it;
- asks the user to type the code and the passphrase.

**Stock Android.** Needs nothing beyond that. No RoseOS capability is assumed.

**Handing the enrollment to an authenticator (D8).** The generated client starts `ACTION_VIEW` on the `otpauth://` URI. On stock Android any installed authenticator app claims it. RoseOS ships a system authenticator (`os/overlays/packages/apps/RoseAuthenticator`, package `dev.pixel.rose.authenticator`) that registers for `otpauth://totp`, asks the user to confirm each new account (naming the sending app only when Android vouches for it; names with control or bidi characters are refused; app overlays are hidden, touches on a partly covered dialog are ignored with a note saying so (a picture-in-picture window is not hidden), and the button waits a second each time the dialog regains focus), never lets a link replace an existing account's secret, keeps every secret sealed by a non-exportable Android Keystore AES-256-GCM key usable only while the phone is unlocked (bound to issuer and account), shows codes in a `FLAG_SECURE` window and marks a copied code sensitive. Its only permission is `HIDE_OVERLAY_WINDOWS`, so it has no network, and nothing goes to backups. There is no dedicated RoseOS intent and no Credential Manager surface, so stock Android is equal by construction. When nothing handles the intent, the QR and copyable text remain. RoseOS screens use plain system UI.

It uses the same protocol, so an account enrolled on either kind of phone signs in from the other.

## Sealing

- `Sealer::seal(ctx, secret)` and `open(ctx, sealed)` take a context of (app, account, purpose `otp-secret/v1`). The context is AEAD associated data: a sealed secret copied into another account's row, another app's, or another purpose does not open.
- `open` fails in two distinct ways:
  - **`KeyUnavailable`**: the key version is not loaded. The operation rolls back and reports `Unavailable` (`503 auth_unavailable`): nothing written, nothing counted.
  - **`Integrity`**: tampered, corrupt or another context's ciphertext. Sign-in answers the generic `sign_in_failed`, the host is alerted (`integrity_faults`), and the attempt is not counted against the user.
- `seal` fails only with `KeyUnavailable`; enrollment then writes nothing.
- `ready()` is true only when the current key is loaded. Servers gate readiness on it. There is never a fallback to plaintext or to a newly generated key.
- **Reference sealer** (`AeadSealer`): AES-256-GCM. Envelope `01 | key version (u32 BE) | 96-bit nonce | ciphertext+tag`; the header and the context are associated data. It is handed 32-byte keys by version and which version seals; older versions open what they sealed. A ciphertext under an older version is re-sealed under the current one after its next successful sign-in, so a version can be retired once no row or backup still needs it.
- **Key placement (D9)** is the Chariot owner's: a dedicated versioned key per app and environment, pinned versions loaded by a narrow runtime identity, versions retained for ciphertext and backups, and a missing key failing readiness rather than being regenerated. The library never fetches or creates keys.

## Storage (SQLite shape for generated servers)

Every `Authenticator` operation is **one store transaction** (`Store::begin`/`commit`, `rollback` on any failure); the reference store's `begin` is `BEGIN IMMEDIATE`. Two concurrent requests can never both spend the same code (tested with two connections), and revocation is monotonic in both stores: no later write un-revokes a session.

Generated servers use the **reference store** (`rose_signin::store::sqlite::SqliteStore`, cargo feature `sqlite`) rather than writing auth SQL. The server owns the connection and the database file; `SqliteStore::new` applies rose-signin's migrations (tables prefixed `auth_`, version in `auth_meta`), refuses a database from a newer library, and runs alongside the app's own migrations. Store conformance tests (`tests/conformance.rs`) run every scenario against both the in-memory and the SQLite store.

```sql
CREATE TABLE auth_account (
  app TEXT NOT NULL, account TEXT NOT NULL,
  knowledge_hash TEXT NOT NULL,           -- Argon2id PHC
  sealed_otp_secret BLOB NOT NULL,        -- Sealer output, never plaintext
  last_used_step INTEGER,                 -- replay rejection
  confirmed INTEGER NOT NULL DEFAULT 0,
  pending_handle_hash BLOB,               -- SHA-256 of the enrollment handle while unconfirmed
  pending_since INTEGER NOT NULL,
  recovery_reissue INTEGER NOT NULL DEFAULT 0, -- set by a restore: a new recovery set at next sign-in
  staged_otp_secret BLOB,                 -- migration 2: a recovered authenticator, until confirmed
  PRIMARY KEY (app, account));
-- 80-bit codes, stored as SHA-256 over (app, account, code), length-prefixed, so a leaked table cannot be
-- attacked across accounts at once.
CREATE TABLE auth_recovery (app TEXT NOT NULL, account TEXT NOT NULL, code_hash BLOB NOT NULL,
  PRIMARY KEY (app, account, code_hash));
CREATE TABLE auth_rate (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, last_failure INTEGER NOT NULL);
CREATE TABLE auth_session (token_hash BLOB PRIMARY KEY, app TEXT NOT NULL, account TEXT NOT NULL,
  created INTEGER NOT NULL, last_seen INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX auth_session_principal ON auth_session (app, account);
-- migration 2: single-use invites (D7)
CREATE TABLE auth_invite (app TEXT NOT NULL, account TEXT NOT NULL, token_hash BLOB NOT NULL,
  expires INTEGER NOT NULL, PRIMARY KEY (app, account, token_hash));
```

## Restoring a backup

Restoring a backup brings back state that later operations had retired, so a restore is an explicit procedure, never a raw file copy: after restoring the database and before serving, the server calls `Authenticator::restore_after_backup(now)`. In one transaction it:

- revokes **every session** (tokens revoked after the backup would otherwise work again); users sign in again;
- moves every account's replay marker past **every step the server could have accepted before the restore** (the current step plus the one-step skew), so no code used just before it, even from a fast clock, can be replayed; new codes work from the step after that (up to about a minute);
- invalidates **every confirmed account's unused recovery codes** (codes spent after the backup would otherwise work again) and flags the account; its next successful sign-in returns a new set (`SignIn::Ok.new_recovery_codes`). A pending enrollment keeps its codes: it cannot have spent one.

Known limitation: a passphrase or authenticator changed after the backup reverts with it. Keep backups frequent and point-in-time, and record the restore so the app can tell users. Sealed secrets from old key versions open only if Chariot retained those versions.

## Decisions

| # | Decision | Decided |
|---|---|---|
| D1 | Where the PIN/passphrase is verified | **Server-side** (Argon2id). It is never stored on, or unlocked by, the device; the server checks and throttles every guess. |
| D2 | Length | At least 8 and at most 128 characters; digit-only values are allowed. The product says "passphrase". |
| D3 | Where codes come from | Any standard authenticator app, or the RoseOS system authenticator. One on the same phone proves "this phone", not a separate device. |
| D4 | Code profile | HMAC-SHA-1, 6 digits, 30 s, one step of skew: what authenticator apps generate. |
| D5 | Recovery | A recovery code plus one remaining factor: code + passphrase re-enrolls the authenticator; code + a fresh one-time code sets a new passphrase. Both sign out every session and issue a new recovery set. **No creator reset**: an account with both factors lost stays locked, its data kept. Implemented (`recover_authenticator`, `recover_passphrase`). |
| D6 | Lockout | Decaying backoff; the account limit is skipped for attempts carrying a valid code; no hard lock. |
| D7 | Account creation | Creator invites by default (single-use, bound to the account, expiring); open sign-up is a per-app opt-in. Implemented (`create_invite`, `Admission`). |
| D8 | Native surface | `ACTION_VIEW` on the `otpauth://` URI; a RoseOS system authenticator claims it; plain system UI. |
| D9 | Sealing key placement | The Chariot owner's (see Sealing). |
| D10 | Session lifetimes | 30 days absolute, 7 days idle. |

## Verification state

| Layer | State |
|---|---|
| Source + host tests | `workspace/rose-signin`: Rust library, `cargo test` with and without `--features sqlite` (shared vectors, flow, sealer, store conformance on memory and SQLite, two-connection concurrency, restore, invites and recovery); Java client core, `java/test.sh` (the same vectors). Workflow: `.github/workflows/rose-signin.yml`. The Java core also runs in `scripts/test-local.sh`. |
| Build into RoseOS | The system authenticator's source compiles against the Android platform jar (host test). It is not yet in the product: adding it to an image is a `build/make` product change through the authorized OS build route, coordinated with Release. |
| Deployment | None. No Chariot endpoint exists for it, and the Rose server stays stopped. |
| Phone proof | None. |

The test vectors (`workspace/rose-signin/vectors/rose-signin-v1.txt`) include RFC 4226 Appendix D, RFC 6238 Appendix B (SHA-1), and skew, replay and base32 cases computed by an independent Python HMAC implementation.
