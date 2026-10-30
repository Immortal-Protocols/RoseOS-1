# service-auth-v1: Models app ↔ Muse Glimmer endpoint credentials (approved by Nathan 17:47; QR-invite enrollment)

**Status: approved by Nathan (17:38–17:47).** Core owns the server side and this text; Product owns `dev.pixel.models`.
The server is implemented and tested with synthetic fixtures. It goes live when Infra installs it.

## Defaults (public, not secrets)
- **Origin:** `https://models.a-list.com`. HTTPS only; no redirects followed.
- **Model:** `muse-glimmer`.
- Nothing secret ships in the APK.

## Credential
**Device key.** On first launch the app creates one non-exportable EC P-256 key in Android Keystore. It is bound to
this install, and the attestation challenge is set to the server challenge.
- `key_id` = lowercase hex SHA-256 of the key's DER SubjectPublicKeyInfo.

**Access token.** An opaque `Bearer` token with a TTL of 3600 s, held in memory only. It is sent only to the origin
and never logged. It is renewed by proof of possession, so no long-lived refresh secret exists. No hardware
identifiers are requested.

## Endpoints
All endpoints take a JSON body (`Content-Type: application/json`), at most 64 KiB, and give a JSON reply.

| Call | Request | 200 reply |
| --- | --- | --- |
| `POST /v1/auth/challenge` | `{}` | `{"challenge": "<string>", "expires_in": 60}`: unpredictable and single-use, including across a service restart |
| `POST /v1/auth/token` (enroll) | `{"challenge", "attestation": ["<b64 DER leaf>", …, "<root>"], "signature", "invite"}` | `{"access_token", "token_type": "Bearer", "expires_in": 3600, "key_id"}` |
| `POST /v1/auth/token` (renew) | `{"challenge", "key_id", "signature"}` | same |

**Signature.** `signature` is base64 DER SHA256withECDSA by the device key over the UTF-8 bytes of `challenge`.

**Invite (Nathan, 17:38).** A new device needs an admin-issued code.
- The admin runs `auth_service.py invite --label <who>` and shows the result as a QR code.
- The QR holds `dev.pixel.models://enroll?invite=rgi1.<43 base64url chars>`. The app sends that code as `invite` with
  its first enrollment.
- **Single use**, expiring after 24 h by default. The server stores only its SHA-256.
- The QR never sets the origin or the model; the app's built-in defaults stay.
- The same install enrolling again needs no new code. Renewal never needs one.
**Enrollment.** The attestation leaf must carry the same key, and an `attestationChallenge` equal to those bytes.

**Inference.** `GET /v1/models` and `POST /v1/chat/completions` take `Authorization: Bearer <access_token>`.
The routes, limits and wire format are unchanged (`../serving/https/README.md`).

## Errors and client behavior

| Status | Meaning | Client |
| --- | --- | --- |
| 400 | malformed | no retry; show an error |
| 401 on inference | token missing, expired or invalid | renew once, then redispatch at most once, within the existing two-dispatched-attempt turn limit (every dispatched attempt counts). A further 401 is shown as an error |
| 401 on `/v1/auth/token` | bad signature, challenge or attestation | no retry |
| 403 | key revoked, enrollment closed, `invite_required` or `invite_invalid` (unknown, used or expired) | terminal. Clear the bearer; keep the key and a local refused state. No automatic deletion, re-enrollment or new key on restart; only an explicit enrollment reset, such as scanning a new code, recovers |
| 429 | rate limited | wait for `Retry-After` (at least 1 s), at most 3 attempts per user action |
| 5xx / timeout | unavailable | bounded backoff, with separate bounded retries for auth calls; Stop cancels auth and inference and fences late results |

## Server checks at enrollment
1. **Chain:** every certificate is signed by the next one, ending at a configured trusted root (Google hardware
   attestation roots, installed by the operator as public files). No certificate in the chain is expired, and none
   has a serial in the operator's revoked-serials file.
   Enrollment also refuses (503) while Google's attestation status list is missing, unreadable or more than 48 h old.
   The operator's timer refreshes it every 6 h.
2. **Key Description** (OID 1.3.6.1.4.1.11129.2.1.17): taken from the certificate *nearest the root* that carries
   it. The signature must come from the key that this certificate certifies.
   - attestation and KeyMint security level is TrustedEnvironment or StrongBox;
   - the challenge matches;
   - `attestationApplicationId` lists exactly package `dev.pixel.models` and signer SHA-256
     `066c66d62518d1db6c8a7d40795fcca580412049d2c5a5cce89b7673540108da`;
   - `rootOfTrust.deviceLocked` is true;
   - `verifiedBootState` is Verified or SelfSigned. SelfSigned covers RoseOS's own boot key, with the bootloader locked.
     Unverified and Failed are refused. An operator may optionally pin SelfSigned to listed `verifiedBootKey` values;
     Nathan did not require this (17:45).
3. **Invite:** a new key needs an unused, unexpired invite. It is consumed together with the enrollment, and a refused
   attestation does not spend it.
4. **Registry:** the key is stored as `key_id → {enrolled_at, revoked, label}` in a 0600 state file.

**Renewal** needs a registered, unrevoked `key_id` and a valid signature over a fresh challenge. A key whose enrollment
chain now carries a revoked serial is revoked automatically.

## Abuse limits
- **Per client IP (Nginx):** challenge 20/min, token 10/min, each with a burst of 5.
- **New enrollments (service):** 50/day globally, and at most 200 registered keys. Over the cap returns 403
  `enrollment_closed`.
- **Inference:** limits are unchanged and global (6/min, burst 2, one in flight). Nginx counts only requests that
  already passed authentication.

**Operator controls:** issue invites; revoke one key, or a label (its devices and its unused codes); close
enrollment; or rotate the signing secret. Rotation keeps the previous
secret for one TTL, so live tokens survive it.

## Decisions (Nathan, 17:38–17:47)
1. **Audience:** only devices given an admin QR code can enroll. Attestation still binds the key to the phone's
   hardware and to the signed app. Access is not anonymous or public.
2. **Boot key:** no pin required; any locked RoseOS or stock device qualifies.
3. **Approved:** persistent spend, the certificate and DNS, server-side keys, and the model terms.

**Rejected alternatives:**
- a bearer embedded in the APK, which is a shared secret;
- anonymous token issuance, which is public inference;
- a typed token, which the QR code replaces.

**Rollback:** close enrollment or revoke a label; inference still requires a token. Live use now waits only on Infra's
operator running `../serving/service/README.md`.
