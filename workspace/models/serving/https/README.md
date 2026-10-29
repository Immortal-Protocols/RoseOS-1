# Rose Models HTTPS edge (MODELS-HTTPS-SOURCE-001, MODELS-TLS-ALT-001)

**Deployment mode (`--front tls`):** `models.a-list.com` → **Nginx** terminating TLS itself on `:443` → **vLLM** (`127.0.0.1:8000`, from `../render_local.py`).
Caddy acquisition is held, so the Caddy front (`--front loopback`: Caddy :443 → Nginx `127.0.0.1:8080`) is kept only as unvalidated reference; its output is byte-identical to PR132.
This directory holds source and local synthetic tests only.
- No certificate was issued.
- No DNS, GCP or live endpoint was contacted.
- No real token exists here.

## Files

| File | Purpose |
| --- | --- |
| `render_proxy.py render` | Writes `nginx.conf`, `caddy.json` and `manifest.json` (input values plus sha256 of each file) from closed, validated inputs. It never takes arbitrary config text. |
| `render_proxy.py token-map` | Reads a protected token file and writes the 0600 Nginx include that holds the bearer match. The token never reaches argv, stdout, the rendered configs or the manifest. |
| `render_proxy.py check-tls` | Before start, refuses a certificate/key pair unless all hold: the key is 0600 and owned by the caller; the fullchain has the leaf plus an issuer; the leaf SAN names the hostname exactly; the leaf is not self-signed, not expired, and matches the key. It prints nonsecret facts only. |
| `tests/test_edge.py` | Real Nginx against a loopback fixture backend with fake credentials, plus negative controls and renderer checks (loopback mode). |
| `tests/test_tls.py` | Real Nginx in TLS mode with a **disposable synthetic** root → intermediate → leaf chain made by openssl per run, and a client that trusts only that root. Not real certificate issuance. |

## Policy (enforced in Nginx in both modes)

**Routes:** exactly `POST /v1/chat/completions` and `GET /v1/models`.
- The raw request URI must equal the path exactly, so encoded, doubled-slash, dot and case variants get 404.
- Any query string gets 400.
- A wrong method gets 405.

**Auth:** `Authorization: Bearer <token>` with an exact, **case-sensitive** match, otherwise 401.
- The match is an anchored `~^Bearer <token>$` regex, because plain Nginx `map` keys compare case-insensitively.
- Trailing optional whitespace is stripped by HTTP rules, so it is the same credential.
- The token must be 256 bits: 43 base64url characters or 64 hex.
- The token is never accepted from the query string.
- Nginx rejects a request carrying two `Authorization` headers before routing (400).
- `Authorization` is stripped before vLLM.

**Limits:**
- body ≤ 256 KiB (413 above);
- global 6 requests/min with burst 2 (429);
- one request in flight (429);
- 120 s upstream timeout (504).

Only authenticated requests count toward the rate and in-flight limits.

**Privacy:**
- no access log;
- the error log is at `crit`, so request lines and rate-limit notices are not written;
- no disk buffering: bodies stay in memory, and proxy temp files are disabled;
- temp paths sit in a task-private runtime dir.

**Network:** Nginx listens on `127.0.0.1` only. Ports are validated as 1024–65535, and the public listener exists only in Caddy.

**Caddy** (`caddy.json`):
- admin API disabled;
- one server on `:443` and the configured hostname only; any other host gets 421;
- one explicit Let's Encrypt ACME issuer with TLS-ALPN-01, the HTTP challenge disabled and automatic HTTP redirects disabled;
- reverse proxy to `127.0.0.1:8080`;
- no access log.

## TLS front (`--front tls`)

**Listeners:** one public listener, `<address>:443 ssl`, with no port 80 and no redirect. A plain-HTTP request to the TLS port gets 400.

**Handshake:**
- a default server with `ssl_reject_handshake on` fails the handshake for any SNI other than the hostname, or no SNI;
- a request whose Host names another server gets 421;
- TLS 1.2 and 1.3 only; session tickets off.

**Certificate:** `ssl_certificate` is the fullchain and `ssl_certificate_key` the key, both explicit paths. Render never reads the key.
- The public listener applies the same routes, auth, limits and privacy rules as the loopback mode. All 17 loopback tests still pass, and the TLS suite repeats the key ones over TLS.

**What the synthetic TLS tests prove:** Nginx serves the configured chain and completes trusted handshakes for the hostname. It rejects wrong or missing SNI, TLS ≤ 1.1, untrusted clients and plain HTTP. A negative control shows that removing the rejecting default server lets a wrong name be served.

**What they do not prove:** publicly trusted issuance, the Let's Encrypt chain, DNS-01, or the real hostname's reachability.

**Certificate provisioning (not done here):** the selected candidate is Certbot manual DNS-01 for `models.a-list.com`. The operator adds and removes only the `_acme-challenge.models` TXT record. Its blockers are on the Infra page: no installed ACME client, and no existing account verified. Creating a task-scoped ACME account is a separate authority. The certificate key and ACME account stay in the approved protected location and are never relayed. Manual certificates do not auto-renew; that is acceptable for the 2-hour test.

**Operator sequence for TLS mode** (on the admitted VM; not executed here):

```bash
R=/run/rose-edge
install -d -m 700 $R $R/conf $R/client_body $R/proxy $R/fastcgi $R/uwsgi $R/scgi $R/tls
# fullchain.pem and privkey.pem are placed in $R/tls by the approved provisioning route (0600 key).
python3 workspace/models/serving/https/render_proxy.py check-tls --fullchain $R/tls/fullchain.pem --key $R/tls/privkey.pem
python3 workspace/models/serving/https/render_proxy.py render --front tls --out-dir $R/conf --runtime-dir $R \
  --fullchain $R/tls/fullchain.pem --key $R/tls/privkey.pem
python3 workspace/models/serving/https/render_proxy.py token-map --token-file "$TOKEN_FILE" --out $R/token.map
nginx -t -p $R -c $R/conf/nginx.conf
nginx -p $R -c $R/conf/nginx.conf
```

Nginx binds :443, which needs root or `CAP_NET_BIND_SERVICE` for the master process.

**Readiness:**
- `curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}' -H @$R/auth.hdr https://models.a-list.com/v1/models` → `200 0`, using the system trust store and no `-k`;
- the same without the header → 401;
- `openssl s_client -connect <ip>:443 -servername other.example` → handshake failure.

**Stop:** `nginx -s quit -p $R -c $R/conf/nginx.conf`. **Cleanup:** `rm -rf $R`, which removes the key, token map and temp files. It makes no physical-erasure claim.

## Pins and provenance

| Component | Pin | Provenance | Checked by a real binary? |
| --- | --- | --- | --- |
| Nginx | `nginx 1.24.0-2ubuntu7.19` (Ubuntu 24.04 noble, amd64). Package sha256 `b9c3476a504c963d8c3cab6540127fa4100ccf6ddf8ee978780bb32f128968d5`; `/usr/sbin/nginx` sha256 `5bfff15e0a9e0bce5c71be8179d77880f9b17c3f575194ff93c912a415756eed` | `apt-get download nginx` from the Ubuntu archive. The package sha256 equals the `SHA256` field in the archive index | **Yes**: `nginx -t` passes, and every test in `tests/test_edge.py` ran against it |
| Caddy | **Not pinned by Core.** `https://github.com/caddyserver/caddy/releases/latest` returned **HTTP 403** through this session's proxy (2026-10-29 ~21:00 UTC). Per the assignment, I stopped and tried no other host | — | **No.** `caddy.json` is structurally tested but not validated by Caddy |
| vLLM backend | `../muse-glimmer-30b.json` profile `h100x1-bf16-vllm-local`: image `vllm/vllm-openai@sha256:dbb04f5c…`, `meta-models/Muse-Glimmer-30B@cec94fcf…`, TP=1, BF16, 8,192 context, 1 sequence, output 512 | Earlier receipts | — |

**Operator, before any start:**
1. Obtain the official Caddy v2 linux/amd64 release from its official source.
2. Verify it against the release's published checksum file and record the version and sha256 in the run receipt.
3. Run `caddy validate` (step 4 below). If validation fails, stop.

## Local checks (no network, no service started)

```bash
# Real nginx binary without installing or starting the service:
mkdir -p /tmp/rose-nginx && cd /tmp/rose-nginx && apt-get download nginx && dpkg-deb -x nginx_*.deb root
ROSE_NGINX=/tmp/rose-nginx/root/usr/sbin/nginx python3 workspace/models/serving/https/tests/test_edge.py
```

CI runs the same suite in `.github/workflows/models-endpoint.yml`.

## Operator sequence on the admitted VM (not executed here)

Run as the unprivileged service user, with `umask 077`.
Prerequisites:
- vLLM is already healthy on `127.0.0.1:8000`, via `render_local.py`;
- the protected token file exists (0600, owned by this user). It is provided by the token owner, never pasted on the command line.

```bash
R=/run/rose-edge
install -d -m 700 $R $R/conf $R/client_body $R/proxy $R/fastcgi $R/uwsgi $R/scgi $R/caddy-data
python3 workspace/models/serving/https/render_proxy.py render --out-dir $R/conf --runtime-dir $R
python3 workspace/models/serving/https/render_proxy.py token-map --token-file "$TOKEN_FILE" --out $R/token.map
```

1. **Validate:**
   ```bash
   nginx -t -p $R -c $R/conf/nginx.conf
   caddy validate --config $R/conf/caddy.json
   ```
2. **Start:**
   ```bash
   nginx -p $R -c $R/conf/nginx.conf
   XDG_DATA_HOME=$R/caddy-data caddy run --config $R/conf/caddy.json --pidfile $R/caddy.pid &
   ```
   Caddy needs `CAP_NET_BIND_SERVICE` for :443. Its ACME account and certificate key stay under `$R/caddy-data`.
3. **Readiness, local:** keep the header out of argv with a 0600 header file.
   ```bash
   printf 'Authorization: Bearer %s\n' "$(cat "$TOKEN_FILE")" > $R/auth.hdr
   ```
   Then expect:
   - `curl -s -o /dev/null -w '%{http_code}' -H @$R/auth.hdr http://127.0.0.1:8080/v1/models` → `200`;
   - the same without `-H` → `401`.
4. **Readiness, public** (after DNS points at this VM):
   - `curl -sS --fail -o /dev/null -w '%{http_code} %{ssl_verify_result}' -H @$R/auth.hdr https://models.a-list.com/v1/models` → `200 0`;
   - also expect no-token → 401, wrong path → 404, oversize body → 413, burst → 429;
   - check the issued certificate's chain, SAN and expiry independently.
5. **Stop:**
   ```bash
   kill -TERM "$(cat $R/caddy.pid)"
   nginx -s quit -p $R -c $R/conf/nginx.conf
   ```
   The admin API is disabled, so `caddy stop` cannot work; use the pidfile.
6. **Cleanup:**
   ```bash
   rm -rf $R
   ```
   This removes the token map, header file, ACME account, certificate key and temp paths. It makes no physical-erasure claim; deleting the task VM and disk is the real end of life.

## What remains untested offline

- TLS-ALPN-01 issuance, the public certificate chain, and DNS for `models.a-list.com`.
- Caddy itself: binary unavailable, so its config validity is unchecked. Forwarding raw request URIs and headers through Caddy to Nginx is also unchecked, as is HTTP/2-specific duplicate-header handling.
- Real vLLM responses, latency and timeout behaviour under load.
- A 120 s timeout is configured; tests use 2 s through the same renderer option, which caps at 120.
- Effective firewall and network rules on the VM.
