# service-auth-v1: Models app ↔ Muse Glimmer endpoint credentials (selected 17:13; live use pending)

**Status: selected by the Models Manager at 17:13 for implementation; live issuance is not authorized.** Core
implements and tests it against synthetic fixtures only. Live issuance waits on Chief's action-time security decision
(see Decision). Core owns the server side and this text; Product owns `dev.pixel.models`.

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
| `POST /v1/auth/token` (enroll) | `{"challenge", "attestation": ["<b64 DER leaf>", …, "<root>"], "signature"}` | `{"access_token", "token_type": "Bearer", "expires_in": 3600, "key_id"}` |
| `POST /v1/auth/token` (renew) | `{"challenge", "key_id", "signature"}` | same |

**Signature.** `signature` is base64 DER SHA256withECDSA by the device key over the UTF-8 bytes of `challenge`.
**Enrollment.** The attestation leaf must carry the same key, and an `attestationChallenge` equal to those bytes.

**Inference.** `GET /v1/models` and `POST /v1/chat/completions` take `Authorization: Bearer <access_token>`.
The routes, limits and wire format are unchanged (`../serving/https/README.md`).

## Errors and client behavior

| Status | Meaning | Client |
| --- | --- | --- |
| 400 | malformed | no retry; show an error |
| 401 on inference | token missing, expired or invalid | renew once, then redispatch at most once, within the existing two-dispatched-attempt turn limit (every dispatched attempt counts). A further 401 is shown as an error |
| 401 on `/v1/auth/token` | bad signature, challenge or attestation | no retry |
| 403 | key revoked or enrollment closed | terminal. Clear the bearer; keep the key and a local refused state. No automatic deletion, re-enrollment or new key on restart; only an explicit enrollment reset recovers |
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
   - `verifiedBootState` is Verified, or SelfSigned with a `verifiedBootKey` in the operator's pinned list (the
     approved RoseOS boot key). Unverified and Failed are refused.
3. **Registry:** the key is stored as `key_id → {enrolled_at, revoked}` in a 0600 state file.

**Renewal** needs a registered, unrevoked `key_id` and a valid signature over a fresh challenge. A key whose enrollment
chain now carries a revoked serial is revoked automatically.

## Abuse limits
- **Per client IP (Nginx):** challenge 20/min, token 10/min, each with a burst of 5.
- **New enrollments (service):** 50/day globally, and at most 200 registered keys. Over the cap returns 403
  `enrollment_closed`.
- **Inference:** limits are unchanged and global (6/min, burst 2, one in flight). Nginx counts only requests that
  already passed authentication.

**Operator controls:** revoke one key, close enrollment, or rotate the signing secret. Rotation keeps the previous
secret for one TTL, so live tokens survive it.

## Decision for Chief (before any live issuance or exposure)
1. **What this proves.** This authenticates *a genuine Android TEE holding a key that the OS attests to belong to
   the signed `dev.pixel.models`*. It does not identify a person. Anyone with the signed APK and a qualifying device
   can enroll, so access is effectively everyone the APK reaches.
2. **Installs, revocation and rollback.** Each install is a separate enrollment. Revocation is per key; a reinstall
   enrolls again until enrollment is closed. Rollback is to close enrollment (inference still requires a token).
   Pinning the RoseOS boot key limits SelfSigned devices to that OS, but a modified OS signed with the same key could
   still misreport the app identity.
3. **Rejected alternatives:**
   - a bearer embedded in the APK, which is a shared secret;
   - anonymous token issuance, which is public inference and is not offered as authentication;
   - a typed or pasted token, which violates the no-configuration requirement.
4. **Live prerequisites.** These need exact authority:
   - the persistent signing secret and the state file;
   - the trusted roots, the status-list refresh and the pinned RoseOS boot key on the VM;
   - the public auth routes.
