# Working in RoseOS-1

Read README.md and the relevant current guide under docs/ before changes.
`docs/upstream/` and `os/reference-scripts/` are historical context, not current
run instructions or independent authorization. Follow the user's current scope.

- Preserve user changes. Inspect origin, HEAD and worktree before fetching or
  changing branches. Use isolated checkouts for simultaneous work.
- `workspace/rose-agent/` contains the Android launcher and private backend.
  Sibling clipboard and experiments modules are real dependencies.
- Keep package IDs, provider/permission names and wire protocols stable unless
  the task includes a coordinated migration. Rose Agent is the display name.
- Use `bash scripts/bootstrap.sh` for local tools and `bash scripts/test-local.sh`
  for the selected offline suite. App builds write ignored `out/` directories.
  Run additional targeted tests when a change warrants them. Do not execute
  live phone/provider/deployment scripts as generic test discovery.
- OS source is reconstructed from `os/pinned-manifest.xml` on a suitable builder.
  Inspect existing source, jobs and storage before modifying a remote build tree.
  Preserve Android patch whitespace and upstream notices.
- Keep credentials, keys, passwords, cloud caches, paired-device state and
  personal logs outside Git. Never print secret payloads or token-bearing errors.
  Ordinary builds need no cloud account or signing key.
- To verify a built alpha/beta, follow docs/VERIFY.md (`scripts/rose-verify.py`).
  Runtime capture needs an explicit serialized host handback from Infra.
- Record source revision, relevant tools, actual checks and artifact identity.
  Distinguish compilation, signature verification, virtual execution, physical
  device acceptance and publication. Do not turn historical evidence into a
  claim about the current build.
- Respect access denials. Resolve network/IAM issues through supported changes
  within authorized scope; do not route around a blocked operation.
