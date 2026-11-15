# Rust build host provider (source only)

`prototypes/voice-phone-demo/rose/app_builder/providers/host.py` supplies
`BubblewrapIsolation` and `PinnedRustTools` for the Level 2 build boundary in
[APP-BUILDER.md §7.6](APP-BUILDER.md#76-builder-planned). This new providers
subpackage is distinct from `app_builder/host.py`, the standalone service.
Nothing imports or activates these facilities by default. App Core owns their
injection into the service and build orchestration. This source does not select,
provision or authorize a builder, activate Rose, or populate tools/crates.

## Supported confinement

The implementation targets **Linux x86-64, an unprivileged user, and an existing
non-setuid bubblewrap** supporting `--disable-userns`. It uses the existing
`Isolation.command(argv, env, *, writable, readonly) -> (argv, env)` contract.
Bubblewrap fits that contract without introducing an image registry, container
daemon or separate container lifecycle, as a Podman adapter would. It is not a
macOS adapter. A separate Linux host remains a provisioning decision.

The root starts empty. The provider binds only the canonical absolute job path
writable, and the supplied canonical toolchain/vendor directories read-only.
It binds existing `/usr/bin`, `/usr/lib`, `/usr/lib64`, `/bin`, `/lib` and `/lib64`
read-only for system executables, the dynamic loader and linker. It accepts
merged-`/usr` aliases only within that set. It never mounts the host's whole
`/usr`, home, `/etc`, `/run`, `/sys` or `/proc`. Empty ancestor directories needed
to preserve an absolute job/toolchain path may exist; their host contents do not.
No ancestor Cargo configuration is exposed. Existing `server_build` checks also
refuse a job under host ancestor Cargo configuration before calling the provider.

`/proc` is a fresh procfs inside the mandatory PID namespace; `/dev` has
bubblewrap's minimal synthetic devices. Both and the synthetic root are remounted
read-only. The job is the sole writable host filesystem mount. Overlapping
job/toolchain/vendor/runtime mounts, noncanonical paths, host-home roots and a job
that could replace the bubblewrap executable are refused.

User, PID, network, IPC, UTS and cgroup namespaces are mandatory. There is no
`-try`, network sharing or weaker retry. All capabilities are dropped, further
user namespaces disabled, and the command gets a new terminal session.
`--die-with-parent` and the PID namespace bind descendants, including detached
ones, to the bubblewrap process killed by `toolchain.run` on cancellation or
timeout. A kernel/flag/policy refusal ends the command; it never runs outside the
sandbox. Preflight failures use `BuildFailed(toolchain,
build_isolation_unavailable)`; bubblewrap startup failures subsequently use the
existing command-failure path in `server_build`.

The adapter returns the supplied environment unchanged. Since bubblewrap adds
`PWD`, the inner `/usr/bin/env -i` reconstructs precisely the supplied child
environment before invoking the absolute command. No shell parses arguments or
environment values. The caller supplies `server_build`'s scrubbed environment,
not arbitrary host environment or loader overrides. The inner working directory
is the job; `server_build` uses absolute manifest/config paths.

Host ownership is part of this boundary: the executable, system runtime,
toolchain and vendor trees must be trusted and immutable to jobs, including during
verification/execution; each job must be a fresh private directory without
preexisting host sockets or nested mounts. An attacker who can rewrite host
configuration or its pinned installation is outside the job isolation boundary.
This is filesystem/process/network confinement, not a resource quota or a
defence against kernel vulnerabilities. Crates requiring system headers or
runtime files outside these mounts fail rather than gaining broader mounts.

## Supplied toolchain pin

`PinnedRustTools(toolchain_path, expected_sha256=owner_supplied_digest)` implements
the existing `RustTools` interface. `available()` rechecks the full tree each
time, the requested target's standard-library directory and executable,
unprivileged `bin/cargo` and `bin/rustc`. It never executes them. There is no
download, rustup invocation, automatic version choice or trust-on-first-use.
The expected digest belongs in trusted host configuration outside the toolchain
tree. Deriving it from the installation during each build defeats the pin.

`toolchain_digest` defines the reproducible **v1** format: SHA-256 starts with
`rose-rust-toolchain-v1\n`; then, sorted by POSIX relative path, each root,
directory and file contributes an ASCII JSON line `[path, mode, content_sha256]`
with compact separators and a trailing newline. Root path is `""`; directory
content digest is `null`; mode is the `stat.S_IMODE` integer. All paths, modes,
empty directories and file contents are pinned. Links and special files are
refused; an installation must be an unpacked plain tree, not rustup proxies.
The host owner supplies both the selected toolchain and its independently
recorded expected digest. This change supplies verification, not an actual
approved Rust release, installation or pin.

## Validation limits

`rose.tests.test_app_host_provider` uses synthetic trees and mocked host identity
for its ordinary tests. These prove pin checks and command policy, **not Linux
kernel confinement**. The selected offline suite and app CI run those tests.

The same test module has two explicit execution probes, selected only with
`ROSE_TEST_LINUX_ISOLATION=1` on an already prepared Linux x86-64 unprivileged
host. They use preinstalled `/usr/bin/bwrap` and `/usr/bin/python3`, install
nothing, and fail if namespaces/tools are unavailable. They check denied sibling
job/home/config access, outward symlinks, read-only trees/root, a successful job
write, unchanged environment, denial of a host loopback listener, and detached
descendant termination under the real timeout/cancellation runner. Their
supervisor has a hard deadline so broken cleanup cannot hang the test suite.

Those execution probes have **not been run on the development Mac**. Neither
ordinary argv tests nor App Core's native-host offline Cargo tests establish
Linux isolation, a pinned musl build, service wiring, deployment or phone
acceptance. Host preparation and real Linux acceptance remain separate work.
