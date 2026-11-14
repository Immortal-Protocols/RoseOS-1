# rose-signin-v1: sign-in for servers that Rose generates

**Status: draft contract, held for Nathan's decisions.**

- OS Core owns this text and `workspace/rose-signin/`.
- App Core owns integrating it into generated apps.
- Chariot's existing owner owns the hosting it runs on.

This document is source only. Nothing here is built into RoseOS, deployed to a Chariot or exercised on a phone.

**Name.** `rose-signin-v1` is the sign-in protocol for apps Rose generates. It is unrelated to `rose-auth-v1`, the existing phone↔server request-signature wire prefix (`AUTH_PREFIX` in `prototypes/voice-phone-demo/rose/remote.py`, `REMOTE.md`, `LocalRoseAuth.VERSION`, `workspace/models/rose_cloud/rose_cloud/contract.py`). That prefix is installed on phones and stays byte-for-byte unchanged; nothing here renames or migrates it. `workspace/rose-signin/java/test.sh` checks both: the new code never uses the old prefix, and the old prefix is still present where it is defined.

Project 1 Level 2 needs per-user sign-in for the client/server apps Rose generates. Nathan confirmed the factors: **an authenticator-style one-time code plus a PIN/passphrase**. This contract defines them precisely enough that:

- every generated server verifies them the same way;
- a RoseOS phone and a stock Android phone enroll and sign in with equivalent flows.

## What is decided, what is proposed, what is open

| Item | State |
|---|---|
| Factors: one-time code + PIN/passphrase | **Decided** (Nathan) |
| One-time code algorithm: TOTP, RFC 6238 | **Proposed**: what authenticator apps implement; the v1 profile is *(interim, D4)* |
| Server: Rust + SQLite | **Proposed** by the plan's server profile; the library is Rust |
| D1–D10 below | **Open**: each has a recommendation, and none is assumed in shipped behaviour except where marked *(interim)* |

## Factors

**One-time code (possession).**
- Profile v1 *(interim, D4)*: HMAC-SHA-1, 6 digits, 30-second steps.
- The server accepts the current step and one step either side (clock skew).
- **Each accepted step is consumed per account.** A code, or any earlier code, is never accepted twice.
- The secret is 160 random bits, created on the server at enrollment and shown once as an `otpauth://totp/` URI (QR or text).
- The server keeps it **sealed** (encrypted) at rest: it must be recoverable to verify codes, so it cannot be hashed. See D9.

**PIN/passphrase (knowledge).**
- *(interim, D1)* Verified **server-side** against an Argon2id hash: 19 MiB memory, 2 passes, 1 lane, random 16-byte salt, PHC string.
- Length: 8 to 128 Unicode scalar values *(interim, D2)*.
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

All requests go over HTTPS to the generated app's Chariot endpoint.

| Step | Request | Server behaviour |
|---|---|---|
| Admission | App-specific (D7) | The app decides whether this account may be created (open sign-up or creator invite) before enrollment starts. |
| Enroll 1 | `POST /signin/v1/enroll {account, passphrase, handle?}` | Checks the name and length policies and hashes the passphrase. Creates a 160-bit secret, 10 recovery codes and a 256-bit **enrollment handle**. Returns all three **once**, with the `otpauth://` URI. The account stays unconfirmed. For 15 minutes the pending enrollment belongs to whoever began it: a restart without its handle gets `enrollment_pending`, so a second caller cannot swap in their own secret. A confirmed account cannot be re-enrolled this way. |
| Enroll 2 | `POST /signin/v1/enroll/confirm {account, handle, code}` | Needs the enrollment handle. A valid code proves the authenticator holds the secret, consumes that step, and confirms the account. Every failure, including a confirmed or unknown account, is `not_confirmed`. Attempts are rate-limited per account. |
| Sign in | `POST /signin/v1/session {account, code, passphrase}` | Applies rate limits, checks both factors, and on success returns `{token, account}`. The token is 256 random bits, base32 encoded. |
| Use | `Authorization: Bearer <token>` | The server looks up SHA-256(token) and checks absolute and idle expiry. |
| Sign out | `DELETE /signin/v1/session` | Revokes this session. |
| Sign out everywhere | `DELETE /signin/v1/sessions` | Revokes every session of the principal. It also runs after any factor change. |
| Recover | D5 | One-time recovery codes exist; what they may reset is open. |

**Errors.**
- `401 {"error":"sign_in_failed"}` for any factor or account failure.
- `429 {"error":"try_later","retry_after":N}` plus a `Retry-After` header when rate-limited.
- `401 {"error":"session_invalid"}` for an unknown, expired or revoked token.
- Enrollment errors: `passphrase_too_short`, `passphrase_too_long`, `invalid_name`, `enrollment_pending`, `already_enrolled`, `not_confirmed`, and `try_later` (confirmation rate limit).
- `already_enrolled` tells a would-be enroller that the name is taken. That is unavoidable under open sign-up; invite-only admission (D7) hides it from anyone without an invite.

The HTTP shape above is the contract. The library (`rose_signin::flow::Authenticator`) implements the behaviour behind it, and App Core's generated server maps routes to it.

## Limits and sessions

**Rate limits** *(interim, D6)*:
- Counted per account and per client (for example, source address).
- 5 free failures, then the delay doubles from 30 seconds up to 1 hour. Failures are forgotten after 2 hours without another.
- The **client** limit applies to every attempt.
- The **account** limit applies only to attempts **without a valid one-time code**. It slows code guessing against a known account, but an attacker who knows only the name cannot keep out the real user, who presents a valid code.
- Passphrase guessing stays bounded because each code is consumed after one guess.
- There is **no hard lockout**. A success clears the account's counter.

**Sessions** *(interim, D10)*:
- 30 days absolute, 7 days idle.
- The server stores only the token's SHA-256. Revocation is per session or per principal.

## Stock Android and RoseOS

**Every phone.** The same server protocol and the same pure-Java client core (`workspace/rose-signin/java`, no `android.*`). On every phone the generated app:
- shows the enrollment URI as a QR and as copyable text, so any standard authenticator app can enroll it;
- asks the user to type the code and the passphrase.

**Stock Android.** Needs nothing beyond that. No RoseOS capability is assumed.

**RoseOS adds native support (D8):**
- a system authenticator that can hold the secret and show codes;
- possibly a Credential Manager provider.

It uses the same protocol, so an account enrolled on either kind of phone signs in from the other.

## Storage (SQLite shape for generated servers)

The library's `Authenticator` methods take `&mut self`. A SQLite-backed store must run **each call in one transaction**, which includes `BEGIN IMMEDIATE`. Two concurrent requests can then never both spend the same code, and a sign-in can never overwrite a concurrent revocation.

```sql
CREATE TABLE auth_account (
  app TEXT NOT NULL, account TEXT NOT NULL,
  knowledge_hash TEXT NOT NULL,           -- Argon2id PHC
  sealed_otp_secret BLOB NOT NULL,        -- Sealer output, never plaintext
  last_used_step INTEGER,                 -- replay rejection
  confirmed INTEGER NOT NULL DEFAULT 0,
  pending_handle_hash BLOB,               -- SHA-256 of the enrollment handle while unconfirmed
  pending_since INTEGER NOT NULL,
  PRIMARY KEY (app, account));
-- 80-bit codes, stored as SHA-256 over (app, account, code), length-prefixed, so a leaked table cannot be
-- attacked across accounts at once.
CREATE TABLE auth_recovery (app TEXT NOT NULL, account TEXT NOT NULL, code_hash BLOB NOT NULL,
  PRIMARY KEY (app, account, code_hash));
CREATE TABLE auth_rate (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, last_failure INTEGER NOT NULL);
CREATE TABLE auth_session (token_hash BLOB PRIMARY KEY, app TEXT NOT NULL, account TEXT NOT NULL,
  created INTEGER NOT NULL, last_seen INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
```

Updates to `last_used_step` must be atomic with the check: one transaction per sign-in. Otherwise two concurrent requests could both spend the same code.

## Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Does the PIN/passphrase verify **server-side**, or **unlock a device-held credential**? | **Server-side** (as built, interim). A device-unlock PIN is a local gate the server can neither check nor throttle. With codes likely generated on the same phone, both factors would collapse onto one device. |
| D2 | PIN or passphrase; minimum length | At least 8 characters (NIST SP 800-63B for a memorized secret used with a second factor). A 6-digit PIN is weaker than that guidance. |
| D3 | Where codes are generated: RoseOS system authenticator, any authenticator app, or both | Both. The Java client core can already generate codes, for a device that acts as the authenticator *(interim)*. Any standard app keeps stock Android portable; the RoseOS authenticator is a convenience. If it is on the same phone as the app, the code factor proves the phone, not a separate device; state that in the product. |
| D4 | Code profile | SHA-1 / 6 digits / 30 s / skew 1 (as built, interim). Stronger hashes break common authenticator apps. |
| D5 | Recovery | Recovery code **plus** passphrase may re-enroll the authenticator, and it revokes all sessions. A forgotten passphrase needs a recovery code plus a fresh code. Losing both means the creator resets the account, which deletes nothing. Not yet built into the flow. |
| D6 | Lockout policy | Decaying backoff, with the account limit skipped for attempts that carry a valid code; no hard lock (as built, interim). |
| D7 | Who can create accounts in a generated app | Creator invites by default; open sign-up opt-in per app. An invite should be single-use and bound to the account it admits. |
| D8 | RoseOS native surface | A system authenticator first, then a Credential Manager provider. Native screens need a design owner and approval (OS Product or Designer). |
| D9 | Where the sealing key lives on a Chariot | **Infrastructure decision for the Chariot owner.** It must live outside the SQLite file and outside backups of it. The library ships no production sealer and cannot be built without one. |
| D10 | Session lifetimes | 30 days absolute, 7 days idle (as built, interim). |

## Verification state

| Layer | State |
|---|---|
| Source + host tests | `workspace/rose-signin`: Rust library, `cargo test` (shared vectors, flow and security properties, including regression tests from an adversarial review); Java client core, `java/test.sh` (the same vectors). Workflow: `.github/workflows/rose-signin.yml`. The Java core also runs in `scripts/test-local.sh`. |
| Build into RoseOS | Not started (D8). Needs the authorized OS build route. |
| Deployment | None. No Chariot endpoint exists for it, and the Rose server stays stopped. |
| Phone proof | None. |

The test vectors (`workspace/rose-signin/vectors/rose-signin-v1.txt`) include RFC 4226 Appendix D, RFC 6238 Appendix B (SHA-1), and skew, replay and base32 cases computed by an independent Python HMAC implementation.
