# rose-workspace environment

This directory documents the agent environment; it is not an applied cloud deployment.

Expected platform configuration: `rose-workspace`, ID
`4c344351-e7ee-45c8-92ee-f5895085a111~asenvcfg_d955b1f6f2b88191a6a79d19ce593836`.
Expected published version:
`3b9c8c3d-6c95-4d0f-b2f1-d7ecd8b7eaf6~cecfgver_6adfe7e3ebb481a293945817f4d3d1f7`.
These IDs were supplied by the coordinator; this shell does not expose platform
metadata to independently attest them. The repository mount and actual tools are checked separately.

## Startup

Mount `https://github.com/Immortal-Protocols/RoseOS-1.git` at `/workspace/RoseOS-1`.
Read its README and any applicable AGENTS.md. Inspect `git status --short --branch`,
`git remote get-url origin`, and `git rev-parse HEAD`. Preserve existing work.
If the mount has no commits and no working files, fetch main and check out the
verified revision. Do not reset an existing checkout to fix startup.

Run `bash scripts/bootstrap.sh` from the repository. Run it again to check that
setup is repeatable. Platform startup hooks are not configured by this repository.

Baseline observed here: x86_64 Linux; Java 21.0.12.1 (compiler module available),
Node 24.19.0, Android platform 35 revision 2, Android Build Tools 35.0.0.
The SDK archives and digests are pinned in `scripts/setup-android-sdk.sh`; npm
uses the imported lockfile. Java/Node base-image version pins remain a platform
configuration responsibility. `javac` need not be on PATH: app builds invoke
Java's compiler module. The SDK archive extracts Build Tools under `android-15`;
that is the expected path, not a second version of the tools.

## Files and persistence

| Location | Contents | Treatment |
| --- | --- | --- |
| `workspace/`, `os/`, `scripts/`, `docs/`, `infra/` | Source and instructions | Commit reviewed changes |
| `.tools/` | SDK archives, SDK, npm cache | Ignored, replaceable |
| `out/` | Isolated per-build outputs and receipts | Ignored; explicitly retain approved deliverables |
| Private directory outside Git | Keys, passwords, CLI cache, backend enrollment | Owner-only; never archive into a release |
| Existing OS builder | Large source, build caches, target files | Inventory first; preserve existing work |

Treat restart persistence as unverified until tested in the actual platform. A
checkout can be reconstructed from Git; a local artifact is not durable merely
because it has a path. Do not share credentials or writable source trees across
concurrent agents. Central logging and retention enforcement are not deployed.

## Network and cloud access

Unrestricted internet is off; package domains are enabled. The supplied runtime
allowlist includes `googleapis.com` and `*.googleapis.com`. Successful calls here
include OAuth, Compute Engine and access to the two named app-signing secrets.
GitHub and Notion connector reads succeeded. SDK downloads from `dl.google.com`
also succeeded.

IAP SSH requires `tunnel.cloudproxy.app` (HTTPS/WebSocket). It returned **proxy
CONNECT 403** in this environment. Google API reachability does not imply IAP
reachability. Resolve through supported environment policy; do not use direct SSH
or another endpoint to bypass this denial. The older design environment's Notion
upload denial is a separate issue.

Cloud Resource Manager project reads returned a destination HTTP 403 indicating
that API is disabled. Do not enable it as an incidental preflight repair; Compute
instance operations can be checked independently.

GCP project: `protocols-465723`; approved Dev Infra account:
`dev-infra@protocols-465723.iam.gserviceaccount.com`. The injected
`GCP_SERVICE_ACCOUNT_JSON` is not for ordinary app builds. Activation requires
owner authorization, private validation, temporary owner-only input outside Git,
an isolated owner-only CLI cache, and deletion of temporary input. Never print
credentials, tokens, assertions, environment dumps or raw sensitive tool errors.

CLI observed: `/workspace/.tools/google-cloud-sdk/bin/gcloud`, version **541.0.0**.
The older handoff cited 590.0.1; do not treat that as this runtime's version.
The current isolated cache is `/workspace/.rose-dev-infra-auth/gcloud`; it is not
part of the repository and must not be assumed to exist in another session.

The container's system SSH include had an owner/permission error. An isolated
SSH client invocation with `-F /dev/null` resolved that local configuration error;
IAP then hit the independent proxy denial. This does not authorize changing host
security settings or disabling SSH host-key verification.
