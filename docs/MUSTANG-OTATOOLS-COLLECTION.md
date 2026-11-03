# Mustang otatools output collection correction

Run [41720536968](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41720536968)
used source `2b521e732fa1537e54ff0a77880810ab1f34cf99` and fresh allocation
**2026110302**. Source completed at 21:43:50Z, vendor at 21:47:03Z and lunch at
21:47:14Z. Integration returned 0 at that same source. Compilation started
**21:47:35Z** and its Make command returned **0** at **23:00:48Z**. The command
requested vendorbootimage, vendorkernelbootimage, target-files-package and
otatools-package with 48 jobs and the allocated OUT_DIR/build/time.

The workflow failed in post-command output collection with:
`make exited 0 but the expected outputs are not all collected`.
The collector looked for `host/linux-x86/otatools.zip`, which was absent.
It recorded target-files (4,340,146,868 bytes, SHA-256
`87892fdabf63374d8afc66f3c986d92c9f2a7f0395e6df2d2eb8db4e9a833c03`)
and both 67,108,864-byte vendor boot images. Actual build identity was not
collected because the old collector required all outputs before reading it.
Neither the workflow nor the original output-validation receipt is successful.

The new allowlisted receipt artifact is
[13277289458](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41720536968/artifacts/13277289458),
18,357 bytes, SHA-256
`47c70d286cd9d35e3ac2e0808ff93122bf19b4f78e73bf5d4fa29ee3347c9de8`,
expiring 2026-11-10T23:00:48Z. This new artifact was retrieved through the
GitHub connector; earlier denied resources were not retried. Cleanup succeeded;
an independent read of instance `685707771105568194` returned `TERMINATED`,
with last stop 23:01:22Z.

## Source correction and real output admission

The [pinned build definition](https://github.com/GrapheneOS/platform_build/blob/b0f697df8cc9519408ca0a404e918cd51bde0735/tools/otatools_package/Android.bp)
defines `otatools-package` as a `java_genrule_host` producing `otatools.zip`.
The corrected collector looks only in that module's Soong intermediates under
the **original allocation's OUT_DIR**, requiring exactly one regular output.
The host variant directory is discovered within that fixed module location;
the actual file location and hash remain unconfirmed until collection runs.
No generic filesystem search, other-device output or renamed archive is used.
Build identity is also read from target-files even when another output is absent.

The existing `mustang-delivery` route now collects outputs **before GCP signer
login**. Normal successful producers remain admitted. The only failed producer
admitted for collection is this exact completed Make=0 case, pinned by run,
source, attempt, workflow, title, artifact ID/digest and the five original public
receipt hashes in `os/build-only/mustang-collection-recovery.json`. Bootstrap
producer admission still requires success. No arbitrary failed/cancelled build
or failed native command is accepted.

Collection checks the retained allocation, original integration/Make source and
outcomes, output existence, actual Mustang device/build/time, sizes and hashes.
All three already-collected file identities must remain exactly equal to the
original record. It writes a **new** same-delivery-run collection receipt while
preserving the original failed report and files. It does not run Make, reserve
another build, overwrite native outputs, change IAM, create keys or read secrets.
Missing/ambiguous otatools or mismatched identity/hashes stop before signer login.
Only complete collection allows the already-authorized signing/package stages.

Signing and delivery receipts retain the original producer's failed conclusion,
the collection-correction flag and the new receipt hash. They never relabel the
old workflow as successful. The existing OS workflow/controller/FIFO/lock and
ownership cleanup remain; there is no new workflow context or execution mode.
The two already-committed Mustang secret versions remain 1, with grants expiring
2026-11-04T00:50:06Z. No bootstrap or grant extension is included.

Source review and Python/JSON/YAML parsing were performed. The sprint's omitted
test suites and verification remain undone. Actual output collection and later
signing/package/delivery results are still required; this correction is source,
not a successful collection or installer claim. The original sprint clock is not
reset. Any delivery retains the required build-and-package candidate label.
