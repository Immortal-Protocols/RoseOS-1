# Yogi replacement-builder recovery

Source: `scripts/yogi_replacement_recover.py`, guest template `scripts/yogi_replacement_recover_guest.py`, tests
`scripts/tests/test_yogi_replacement_recover.py`.

This is a fail-closed plan for one stopped replacement VM. It is not the direct build route
(`scripts/yogi_direct.py`, `docs/YOGI-DIRECT.md`) and it does not record a host-key pin. A guest receipt is evidence
the procedure asked for. It is not authentication of the guest.

Nothing in this repository has executed this procedure against GCP. The commands below are the operator runbook.
`plan`, and `preflight` / `apply` / `cleanup` without `--execute`, make no cloud call.

## Fixed target

| Role | Value |
| --- | --- |
| Project | `protocols-465723` |
| Replacement | `us-west2-b` / `roseos-yogi-builder-20261105-b` / `2720796557371812468` |
| Boot disk | same name / `938743673401685238` / 2000 GB |
| Account already active on the operator computer | `grok-bots@protocols-465723.iam.gserviceaccount.com` |

Read-only, and never started, stopped, or rewritten by this tool:

| Role | Value |
| --- | --- |
| Original VM | `us-west2-a` / `roseos-yogi-builder-20261103-a` / `8812446529823929967` |
| Original disk | `6647020458376394351` |
| Snapshot | `yogi-preserve-20261105-2252` / `8970504972743521862` |
| Snapshot | `yogi-b-prekey-20261107-030026` / `1721727283888335524` |

There is no snapshot restore in this tool. Project metadata and IAM are out of scope. The tool has no flag for a
service-account key, and it does not read one.

## What one operation does

`plan` mints a new 128-bit operation id and a different 128-bit nonce. It renders the guest template with those values
and writes `plan.json`, `binding.json`, `guest.py`, `startup-script.sh`, and `SHA256SUMS`. Historical proposal hashes
are copied into the plan as reference identifiers. They are not the payload hash.

The live read-only check is `preflight --execute`. It describes the replacement, the original VM and disk, and both
snapshots, lists firewall rules with no server-side `sourceRanges` filter, reads the active gcloud account, and calls
`testIamPermissions` on the replacement instance, the original instance, and the project. It does not start the VM and
it does not call `setMetadata`. Without `--execute`, preflight only rechecks the local plan file.

Compute Engine rejected `direction=INGRESS AND sourceRanges:35.235.240.0/20` as an invalid list filter. The list is
therefore `gcloud compute firewall-rules list --project=protocols-465723 --format=json --quiet`
([firewalls.list](https://cloud.google.com/compute/docs/reference/rest/v1/firewalls/list)). Direction, disabled,
network, target tags, source, protocol, port, and priority are checked on the returned rules. A missing direction is
not treated as ingress. An empty `targetTags` list applies to every instance on that network; any other tag list must
intersect the instance tags. The winning priority (lower number) must be an allow of tcp/22, or all protocols, whose
source range contains `35.235.240.0/20`. A deny at that priority blocks preflight.

Instance permission checks stay on Compute Engine
`POST https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/<zone>/instances/<name>/testIamPermissions`
([instances.testIamPermissions](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/testIamPermissions)).
The project check is Resource Manager, not Compute:
`POST https://cloudresourcemanager.googleapis.com/v1/projects/protocols-465723:testIamPermissions`
([projects.testIamPermissions](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions)).
A null body, a null `permissions` field, or any HTTP status other than 200 is a failed check. It is not a grant.

Contract the live read must show before any write:

- replacement `TERMINATED`, no external address, no attached service account
- instance metadata exactly `enable-oslogin=FALSE` and `block-project-ssh-keys=TRUE`
- `automaticRestart` true, `maxRunDuration` 2700 seconds, termination action `STOP`
- original VM `TERMINATED` and its boot disk id `6647020458376394351`
- both snapshots `READY`
- an enabled ingress allow for tcp/22 from `35.235.240.0/20`
- the active account above, and no other active account

`apply --execute` then:

1. Writes and fsyncs `started.json` before any metadata write or start. It then sends **one** `instances.setMetadata`
   with the fingerprint just read. `setMetadata` replaces the whole metadata map
   ([instances.setMetadata](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/setMetadata)), so the
   body repeats every key from that read and adds `startup-script` plus `roseos-yogi-recover-binding`. A mismatched
   fingerprint is `412 conditionNotMet` and is not retried
   ([setting custom metadata](https://docs.cloud.google.com/compute/docs/metadata/setting-custom-metadata)).
   The POST returns an Operation. `PENDING` or `RUNNING` means the write was accepted, not that it failed. This tool
   then calls `zoneOperations.wait` on that operation name, with an empty body and a newly fetched token
   ([zoneOperations.wait](https://docs.cloud.google.com/compute/docs/reference/rest/v1/zoneOperations/wait)). The server
   returns within about two minutes even when the operation is still running, so a later wait or, near the deadline,
   one `zoneOperations.get` may follow
   ([zoneOperations.get](https://docs.cloud.google.com/compute/docs/reference/rest/v1/zoneOperations/get)). That follow-up
   is not another `setMetadata`. The stage budget is 180 seconds.
2. A finished operation is usable only when `kind` is `compute#operation`, the name, zone, and `selfLink` are this
   project and `us-west2-b`, `operationType` is `setMetadata`, `targetId` is `2720796557371812468`, `targetLink` is this
   instance, `status` is `DONE`, and `error` is absent. The instance is then read once. The guest starts only when that
   read shows exactly the intended items and a new fingerprint. An HTTP error, a null body, or a body with no operation
   name is a submission failure and is not waited. `INTERNAL` / gRPC 13 is that kind of failure. It did not recur on
   the apply that exposed the pending-operation bug, and waiting for `DONE` does not claim its cause is fixed. A wait
   timeout still reads the instance once and then runs cleanup and STOP. It does not send the mutation again. If the
   read shows our exact values landed, one later removal may drop only those exact values. Any other key is sent back
   unchanged. A different value stored under `startup-script` or the binding key is not removed. If the read itself
   fails, there is no second write.
3. Starts the replacement only after that clean read, with the same wait on `instances.start`
   ([instances.start](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/start)). The POST body is
   empty. The guest procedure runs only when that operation is `DONE` with `operationType` `start` and the following
   read shows `RUNNING`. Serial output is read after the instance is `RUNNING`, because
   `get-serial-port-output` is the running-instance view and keeps about 1 MiB
   ([viewing serial port output](https://docs.cloud.google.com/compute/docs/troubleshooting/viewing-serial-port-output)).
   There is one immediate read and, only if it has no matching receipt, one more read 180 seconds later. The work
   budget is 35 minutes. The last 10 minutes of the 45-minute policy stay available for cleanup. `maxRunDuration.seconds`
   must be the integer 2700, not a gcloud duration flag.
4. Accepts one receipt line, `ROSEOS-YOGI-RECOVER-RECEIPT`, only from bytes after that cursor, only when it matches
   this binding. The guest writes that line once to stdout. The startup-script runner copies stdout to the serial
   console, so the guest does not write the serial device again. A syslog prefix, a CRLF line ending, or a wrapped
   copy of the same line is still one receipt. Two identical receipts are one observation. A second different receipt
   is a sequence failure. A line that is not valid JSON, is over 8000 bytes, or does not match this binding is
   `serial-json`, `serial-oversize`, or `serial-shape`. That is an unparsed window, not a claim that an actor was
   malicious. Before that classification, the tool writes `serial-evidence.json` in the operation directory, mode
   `0600`, with the reason, the window's sha256 and length, and only a shape-valid public receipt. It does not store
   the raw console. Restore metadata, stop, exit 1. A guest attribute, if present, must parse to the same receipt.
   Absence is expected (`enable-guest-attributes` is not turned on). A different value stops the success path.
5. Restores the two original metadata keys with one `setMetadata`, waited the same way, then stops the instance with
   one empty-body `instances.stop`
   ([instances.stop](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/stop)) and the same operation
   wait. If the instance is `RUNNING` again after that stop, one more stop is sent for `automaticRestart`. There is no
   third stop. `cleanup` uses the same restore and stop waits.

`cleanup --execute` always describes the replacement, the original VM and disk, and both snapshots. It does not need
`started.json`. If the replacement is not proved `TERMINATED`, or the describe fails, it stops the replacement by its
constant name. It removes startup metadata only when the values are exactly this operation's. It does not start the VM
and it does not restore a snapshot. `KeyboardInterrupt` during apply still runs that stop and removal before the
interrupt is re-raised. A failed local outcome write does not skip the stop.

The repair guest, if the metadata server identity matches, may:

- chmod the runner unit to `0644` only when it is a root:root regular file, mode `0664`, size 347, link count 1, and the
  runner is inactive and disabled with no `Runner.Listener` or `Runner.Worker`. If that file is already mode `0644`
  with the same size and owner, repair stops before any chmod or rotation and reports `already-0644`. It does not
  change the mode back and it does not generate new host keys.
- inventory the named `ssh_host_*` files under `/etc/ssh` and rotate `ed25519`, `ecdsa`, and `rsa` only on the `0664`
  path, after the new public fingerprint differs from the pre-rotation fingerprint
- delete an `authorized_keys` line only when it is exactly three fields and the comment is
  `roseos-yogi-recover-<this operation id>`. Any other line is preserved and makes a repair receipt fail success
- delete the account `roseos-yogi-recover` only when its uid and gid are at least 60000, its home is
  `/var/lib/roseos-yogi-recover/account`, and its shell is `nologin` or `false`

`plan --mode inspect` writes a different startup script. That script has no `ssh-keygen`, `userdel`, or mode change.
It reports the unit, public fingerprints, and authorized_keys counts, including when the unit is already `0644`.

A marker for this operation makes a second boot reprint or refuse. It does not chmod or rotate again. Unknown
`ssh_host_*` names, a symlink, a DSA key, and any other authorized_keys line stay in place. The receipt counts
unidentified authorized_keys lines and cannot be `complete` while that count is nonzero. Exit 0 also requires the
metadata restore and a confirmed stop. Exit 0 still has `hostKeyPinned: false` and `guestAuthenticated: false`.

The pre-rotation public fingerprints are the cloned baseline on this replacement disk. The original VM is not started
and its keys are not read. Distinct means the new public fingerprints differ from that baseline.

## Operator commands

On the operator computer, outside GitHub Actions. gcloud is already authenticated as the account above. Do not pass,
copy, or print a key file. A shared environment may set `GOOGLE_APPLICATION_CREDENTIALS`. Unset that variable, and
the impersonation variable, for this process only:

```sh
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py plan \
  --mode repair \
  --operator "NAME" \
  --out ~/yogi-ops/recover-LABEL

# Local file check only. This does not contact GCP.
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py preflight \
  --op ~/yogi-ops/recover-LABEL \
  --confirm-instance 2720796557371812468

# Live read-only preflight: describe, unfiltered firewall list, account, and testIamPermissions.
# It does not start the VM and it does not call setMetadata.
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py preflight \
  --op ~/yogi-ops/recover-LABEL \
  --confirm-instance 2720796557371812468 \
  --execute
```

`python3 -B` does not write `__pycache__`. These operation ids are spent and `plan` refuses them:
`e75b0fd782e43f585c621c74f95bfa90`, `5cf6fe9a62880b89ee6e7e822a0379d2`, and `20ef6008b1ef78137d201de13176af42`.
Do not run repair. The guest for `20ef6008b1ef78137d201de13176af42` reached the serial console and the parser rejected
the window without saving the line. Unit mode, host keys, and authorized_keys on that disk are unknown. The next guest
pass is inspect only. It reports the unit, current public fingerprints, authorized_keys counts, and whether a local
recovery marker exists. It does not chmod, generate keys, or delete anything. The control plane still sends one
temporary metadata write and one start:

```sh
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py plan --mode inspect --operator "NAME" --out ~/yogi-ops/inspect-LABEL
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py preflight --op ~/yogi-ops/inspect-LABEL \
  --confirm-instance 2720796557371812468 --execute
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py apply --op ~/yogi-ops/inspect-LABEL \
  --confirm-instance 2720796557371812468 --execute
```

`cleanup --execute` remains the stop path if a previous apply was interrupted.

## Rotation-only continuation

Repair still stops when the unit is already mode `0644`. That guard is unchanged. A later rotation is a new plan,
`--mode rotate`, and it does not change the mode in either direction.

The inspect receipt is guest evidence. It is not a host-key pin, not proof the keys are still the clone, and not proof
a previous rotation finished. A journal phase of `complete`, or an unrecognized marker, does not authorize or skip
the rotation. Unrecognized markers are counted and left in place. Their bytes are not copied into the new receipt.

`plan --mode rotate --inspect-receipt FILE` copies the three public fingerprints from that reviewed inspect receipt
into the new guest. On boot the guest reads the public files again. It generates a key only when all three still
match that bound evidence, the unit is a root-owned regular file mode `0644` size 347, and the runner is inactive,
disabled, and has no listener or worker. A mismatch stops with the mode unchanged.

That match is consistency with the inspect, not trust in the guest and not a comparison with the original VM.
`hostKeyPinned` stays false. A later inspect that reports the new public fingerprints is a second guest report.
The prekey snapshot remains the restore point and is not applied here. `builder.direct.hostKey` stays unset until
a separate explicit pin decision.

```sh
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_replacement_recover.py plan --mode rotate --inspect-receipt INSPECT.json \
  --operator "NAME" --out ~/yogi-ops/rotate-LABEL
```

Do not point `--inspect-receipt` at a repair plan. Do not reuse operation `638e6e9039e41b7531697bb45066a616`.

Keep the operation directory. Do not reuse it. A stale fingerprint, an expired preflight (20 minutes), or a second
apply needs a new `plan`. If apply is interrupted, run `cleanup --execute` even when `started.json` was not written.

The operator bundle is a directory with the two programs, this runbook, the offline tests, `README.md`, and
`SHA256SUMS`. Every file except `SHA256SUMS` is named in that manifest. `self-check` refuses bytecode and any other
extra file. From the unpacked directory, with no repository checkout and no `PYTHONPATH`:

```sh
python3 -B yogi_replacement_recover.py self-check
python3 -B -m unittest test_yogi_replacement_recover
```

SSH, `ssh-keyscan`, and `gcloud compute ssh` are not used. Trust-on-first-use is not used. The direct route's host-key
pin stays unset.

## Historical reference

Chief, relayed by Saba, Slack ts `1794022474.195909`, named files on a former operator Mac that was reported off.
Retrieval of those files is unconfirmed. The hashes below are copied from that relay. They were not recomputed here,
and the file bytes were not reviewed. They are not bindings, not a payload, and not a reason to block this tool.

Base path named in the relay: `/Users/nathanspaun/Documents/Codex/2026-10-27/task-3/yogi-setup-20261105/replacement-b-001/`

| Path | SHA-256 from the relay |
| --- | --- |
| `inspection-proposal-v3/inspect_guest.py` | `c9d612938c1d13f7febd88a3a68c4863adfaee855ce97a1d0657e1b10acc3b5c` |
| `inspection-proposal-v3/inspection_contract.py` | `36b9d271c07b6987ed1a8dc48a3f3d05b7cd89a7dda66236d7a8872f12d6fd1b` |
| `inspection-adapter-v4/adapter.py` | `f842121b35a079697dc3484caca576088f71e00004ec893ba7557d9b5f404ebe` |
| `inspection-adapter-v4/inspection_core.py` | `2ae1d58dd0f212198bb4fc81432c20df7cfbad6619cc8936c4b07d8eda65cfe8` |
| `hostkey-rotation-proposal-v3/rotate_guest.py` | `624dd95c7bfc7f3a671628746257bcf798111f88de78665a8710cc8b1489db73` |
| `yogi-identity-bound-v4.tar.gz` | `8608f2c2c8315f648dc7d3fa0fbcbbff393911d748b4c9a9b264b2e0f3ff58bf` |
| index of that archive (38 files) | `21677d17c97dc9d2d045392dba4d4db703855b0b49e095d96488493fd3495e02` |

## What the API actually guarantees

- [instances.setMetadata](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/setMetadata) replaces
  the entire metadata set. The fingerprint is required. A stale fingerprint fails with `412 conditionNotMet` and this
  tool does not retry that write.
- [Custom metadata](https://docs.cloud.google.com/compute/docs/metadata/setting-custom-metadata) tells you to send back
  every existing key you intend to keep. Removal therefore copies every current key except this operation's exact
  `startup-script` and binding values.
- [Linux startup scripts](https://docs.cloud.google.com/compute/docs/instances/startup-scripts/linux) run
  `startup-script` on each boot after the initial boot, which is why the key has to be removed before the instance can
  be left stopped. The guest has no service account, so it cannot remove that key. Removal is proved only by the
  control-plane readback (`startupScriptRemoved`).
- [Serial port output](https://docs.cloud.google.com/compute/docs/troubleshooting/viewing-serial-port-output) is written
  by the guest OS, BIOS, and other system-level entities. It is not signed. A receipt that matches this operation's
  nonce is bound to this operation, and it is still not a host-key pin (`serialReceiptTrustedForPin` is false).
- [Guest attributes](https://docs.cloud.google.com/compute/docs/metadata/manage-guest-attributes) are data the guest
  writes. A matching attribute is not authentication. This tool does not enable them.
- The 45-minute limit is `scheduling.maxRunDuration.seconds` of `2700` with `instanceTerminationAction` `STOP`
  ([limit VM runtime](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime)).
  `automaticRestart` restarts a crashed guest
  ([scheduling options](https://docs.cloud.google.com/compute/docs/instances/setting-instance-scheduling-options)).
  It is not this tool's stop. The tool still stops the instance, and it sends one more stop if the instance is
  `RUNNING` again. It does not send a third.

`distinctFromClonedBaseline` compares the new public fingerprints with the public keys read from this replacement
disk before the rotation. It is not a comparison with the original VM. `distinctFromOriginalVerified` is false.
No original fingerprint is claimed.

The active gcloud account must be `grok-bots@protocols-465723.iam.gserviceaccount.com`. If
`GOOGLE_APPLICATION_CREDENTIALS` or `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT` is set in the process, the tool
refuses. Unset them for that process with `env -u`. Do not copy a key into the command. Expected permissions are get,
setMetadata, start, stop, and getSerialPortOutput on the replacement, plus disk get, snapshot get, firewall list, and
`compute.zoneOperations.get` on the project, and get on the original instance. The operation wait uses that get
permission. This tool does not change IAM, project metadata, snapshots, or delete anything. The project permission
call is the Resource Manager URL above. An HTTP error or a null permissions list does not count as allowed. `gcloud
compute firewall-rules list --format=json` is a JSON array of Firewall resources. A REST list page object is not
treated as that array. Guest attributes are read from `variableValue` or `queryValue.items[].value`
([getGuestAttributes](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/getGuestAttributes)).
Serial output is a `compute#serialPortOutput` resource when `kind` is present.

## Residual limits

- The earlier `setMetadata` `INTERNAL` / gRPC code 13 failure has no known cause. It did not recur on the later apply.
  This tool still does not claim that cause is fixed. A null body, an HTTP error, or `INTERNAL` is a submission
  failure: no operation handle, no wait, and no second mutation. `PENDING` and `RUNNING` are different. They are
  waited on `zoneOperations.wait` / `get` until `DONE` or the 180-second stage deadline. The same rule covers start,
  stop, and the rollback write. The calls still use `urllib`. That is not a fix for the unknown `INTERNAL` failure.
- The operator reported a later apply, operation `5cf6fe9a62880b89ee6e7e822a0379d2`, exited
  `metadata-ambiguous-applied` after the metadata write and the rollback both reached `DONE` with no error. No start
  occurred. The operator's independent read showed the replacement `TERMINATED`, id `2720796557371812468`, metadata
  only `enable-oslogin` and `block-project-ssh-keys`, fingerprint `pSAPI4eI6q4=`, and `lastStartTimestamp` still
  9:18 AM. Cleanup exited 0 with `restore` `already-base`, `startupScriptRemoved` true, and `preservedIdsMatch` true.
  No guest repair or rotation occurred. The original VM, disk, and both snapshots were unchanged. Zone operations
  `operation-1794025967141-65fa8387d4384-e9bed427-7ed969ab` and
  `operation-1794025969883-65fa838a71873-581e64cf-6e23ae4e` are spent and are not waited on again. This repository did
  not re-read that state.
- A following apply, operation `20ef6008b1ef78137d201de13176af42`, awaited every mutation to `DONE`: metadata
  `operation-1794026872649-65fa86e763554-67c3c575-aa4d2c66`, start
  `operation-1794026874731-65fa86e95fb00-d547145e-45241669`, restore
  `operation-1794027065214-65fa879f086ca-4cf61ef5-4480e200`, and stop
  `operation-1794027069962-65fa87a38f91f-6bc72969-2fdd5f07`. Two serial reads then `parse_serial` raised `Malformed`.
  The tool did not save the window. The post-stop serial API was empty. The final control-plane read was
  `TERMINATED`, the same id, the two base metadata keys, fingerprint `pSAPI4eI6q4=`, and `startupScriptRemoved` true.
  The original VM, disk, and both snapshots were unchanged. The guest's unit, host keys, and authorized_keys are
  unknown. Do not run repair again and do not claim those files are unchanged. Those zone operations are spent. This
  repository did not re-read that state.
- There is no trusted measurement of the guest. A serial line is guest-written. The cursor, nonce, boot id, and
  instance id bind the line to this operation's output window. They do not prove the guest is honest. Do not commit
  the receipt as `builder.direct.hostKey`.
- The cloned baseline is the replacement disk's own pre-rotation public keys. It is not a read of the original VM.
- Authorized_keys lines whose comment is not this operation's fresh marker are preserved and counted. Exit 0 requires
  that count to be zero. This tool cannot name the earlier temporary key: that material is not in reviewed source.
- If chmod succeeds and a later step stops, a new operation still requires mode `0664`. It will not rotate on a unit
  that is already `0644`. Rolling the disk back is a separate snapshot decision and is not performed here.
- Before a live host key is replaced, both the private and public files are copied to
  `ssh_host_<alg>_key.roseos-prev-<operation id>` (and `.pub`). A failed rotation copies those bytes back and does not
  generate another key. The next boot of the same startup script does the same and does not rotate again. The previous
  public file is kept. The previous private file is unlinked after that restore or after success. A crash in the
  middle of the copy can leave the previous private file until that next boot unlinks it.
- If the metadata readback fails, this tool does not guess and does not send another write. A startup script that was
  stored but not visible in that read stays until a later `cleanup` can read the instance. Cleanup stops the
  replacement when the describe fails.
- The serial buffer is about 1 MiB. A receipt that never appears in the two reads ends the procedure and stops the VM.
- `automaticRestart` can boot the guest again until the startup-script key is gone and the instance stays
  `TERMINATED`. The on-disk marker blocks a second rotation. The stopper sends at most two stop requests.
- If `maxRunDuration` is not 2700 seconds with action `STOP`, preflight stops. This tool does not change scheduling.
