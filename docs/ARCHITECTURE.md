# Repository structure decisions

The app and its backend are kept together in `workspace/rose-agent/` because they
share phone commands, voice assets, pairing and native workers. Separating them
into a new services hierarchy in the same import would obscure the source diff
and break the original relative dependencies. Their build/deployment steps are
still separate. Later refactoring should move one module and its consumers/tests
at a time.

Shared clipboard, delivery, metrics and guardian modules remain siblings under
`workspace/`. The historical `experiments` directory contains live dependencies;
its name is not a signal that its contents can be dropped. Only dependencies and
fixtures needed for this import were brought in. Optional larger Android forks,
live-test helpers and external renderers require separate integration; the app
build does not claim those are installed.

The unchanged Calculator baseline remains in its original location. This avoids
breaking the already-proven recipe/provenance while adding the main product app.

GrapheneOS uses many Git projects and a very large generated checkout. `os/`
therefore tracks a pinned manifest, our patches, overlays and controller code.
The physical source/build tree lives on the OS builder. This keeps app clones
small, preserves upstream commit identity and supports review of our changes
without committing vendor/build caches or a synthetic flattened Android history.

`infra/` describes environments; `scripts/` contains executable local workflows;
`docs/` holds current instructions and evidence. Historical upstream documents
are explicitly labeled under `docs/upstream/`, and original-device shell scripts
stay under `os/reference-scripts/` rather than becoming default release commands.

User-facing names become Rose Agent/RoseOS. Android identities remain stable
because package names, permissions, provider authorities and certificates are
part of app update/data and OS policy contracts. Upstream GrapheneOS and device
identities remain attributed. No wholesale namespace or firmware identity
replacement is hidden in the branding change.

The release boundary is signed, validated artifacts. Ordinary app development
has no cloud or signing dependency. Dev Infra can sign using scoped external
private inputs, and publication uses the existing public artifact channel after
acceptance. A source push is not an OS release and a signature is not a runtime
acceptance result.
