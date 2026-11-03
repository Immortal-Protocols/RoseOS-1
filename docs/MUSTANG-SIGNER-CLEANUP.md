# Mustang transient signing-key cleanup

Mustang signing is bounded and handles SIGTERM by unwinding the existing private
workspace context. Source review found that the upstream release script creates
its decrypted working copy in a separate `/dev/shm/generate-release.*` directory.
Its Bash EXIT trap normally removes that directory, but a forced process-group
termination can prevent the trap from running. No Mustang signing or key leak
has been observed; this is a source correction before the signing stage.

Enable `managed_decrypted_keys` only for the Mustang signer. The existing host
adapter then requires its private workspace in `/dev/shm` before fetching keys,
and derives the upstream script with the decrypted directory nested in that
workspace. The existing outer context overwrites/removes both copies on unwind,
including when a child cannot run its trap. The upstream trap remains in place;
relative release-key references and signing arguments remain unchanged. The
one expected upstream directory-creation anchor must match exactly.

Other callers keep the default behavior. This changes no secret version, IAM
grant, credential route, cryptography or public output verification. Review
consisted of source inspection against the pinned script and Python parsing.
The sprint's omitted tests and verification remain undone; no interruption or
credential-bearing test was performed.
