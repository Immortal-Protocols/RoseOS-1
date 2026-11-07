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

With `--execute`, `preflight` is a read of the replacement, the original, both disks, both snapshots, the IAP SSH
firewall, and the active gcloud account. `apply` continues only when that read matches the contract below and its
metadata fingerprint is still the preflight fingerprint.

Contract the live read must show before any write:

- replacement `TERMINATED`, no external address, no attached service account
- instance metadata exactly `enable-oslogin=FALSE` and `block-project-ssh-keys=TRUE`
- `automaticRestart` true, `maxRunDuration` 2700 seconds, termination action `STOP`
- original VM `TERMINATED` and its boot disk id `6647020458376394351`
- both snapshots `READY`
- an enabled ingress allow for tcp/22 from `35.235.240.0/20`
- the active account above, and no other active account

`apply --execute` then:

1. Writes `started.json` and sends **one** `setMetadata` for this instance, with the fingerprint just read. The body
   keeps the two existing keys and adds `startup-script` plus `roseos-yogi-recover-binding`.
2. Reads the instance back once. The guest starts only when that read shows the intended items, the fingerprint
   changed, and the operation is `DONE` for this numeric id. `INTERNAL`, a timeout, an unparseable body, a pending
   operation, or any other mismatch does not get a second attempt of the same write. A read that shows our keys were
   stored anyway is restored once, when the key set is still only the recovery set, and the VM is not started. If the
   read itself fails, there is no second write.
3. Records the serial-port cursor, then starts the replacement. The work budget is 35 minutes. The last 10 minutes of
   the 45-minute stop policy stay available for cleanup. Serial output is read at most twice (after 180s, then after
   another 300s if the first read has no receipt). That is a fixed schedule, not a poll loop.
4. Accepts one receipt line, `ROSEOS-YOGI-RECOVER-RECEIPT`, only from bytes after that cursor, only when it matches
   this binding. A second line is accepted only as a reprint of the same public fingerprints with `reexecution` true.
   Anything else is malicious output: restore metadata, stop, exit 1. A guest attribute, if present, must parse to the
   same receipt. Absence is expected (`enable-guest-attributes` is not turned on). A different value stops the success
   path.
5. Restores the two original metadata keys with one `setMetadata`, then stops the instance. If the instance is
   `RUNNING` again after that stop, one more stop is sent for `automaticRestart`. There is no third stop.

`cleanup --execute` is the same restore-and-stop path when `apply` was interrupted after `started.json`. It does not
start the VM. It does not restore a snapshot.

The guest, if the metadata server identity matches, may:

- chmod the runner unit to `0644` only when the path is a root:root regular file, mode `0664`, size 347, link count 1,
  and the unit is inactive and disabled with no `Runner.Listener` or `Runner.Worker`
- inventory the named `ssh_host_*` files under `/etc/ssh` and rotate `ed25519`, `ecdsa`, and `rsa` by renaming a new
  key over each named pair after the new public fingerprint differs from the pre-rotation fingerprint
- delete an `authorized_keys` line only when it is exactly three fields and the comment is
  `roseos-yogi-recover-<this operation id>`, on `/root/.ssh/authorized_keys` or
  `/home/pixelbuild/.ssh/authorized_keys`
- delete the account `roseos-yogi-recover` only when its uid and gid are at least 60000, its home is
  `/var/lib/roseos-yogi-recover/account`, and its shell is `nologin` or `false`

A marker for this operation makes a second boot reprint or refuse. It does not chmod or rotate again. Unknown
`ssh_host_*` names, a symlink, a DSA key, and any other authorized_keys line stay in place. The receipt counts
unidentified authorized_keys lines and cannot be `complete` while that count is nonzero. Exit 0 also requires the
metadata restore and a confirmed stop. Exit 0 still has `hostKeyPinned: false` and `guestAuthenticated: false`.

The pre-rotation public fingerprints are the cloned baseline on this replacement disk. The original VM is not started
and its keys are not read. Distinct means the new public fingerprints differ from that baseline.

## Operator commands

On the operator computer, outside GitHub Actions, in a clean checkout of the reviewed commit. gcloud is already
authenticated as the account above. Do not pass a key file.

```sh
python3 scripts/yogi_replacement_recover.py plan \
  --operator "NAME" \
  --out ~/yogi-ops/recover-LABEL

python3 scripts/yogi_replacement_recover.py preflight \
  --op ~/yogi-ops/recover-LABEL \
  --confirm-instance 2720796557371812468

python3 scripts/yogi_replacement_recover.py preflight \
  --op ~/yogi-ops/recover-LABEL \
  --confirm-instance 2720796557371812468 \
  --execute

python3 scripts/yogi_replacement_recover.py apply \
  --op ~/yogi-ops/recover-LABEL \
  --confirm-instance 2720796557371812468 \
  --execute
```

Review `plan.json`, `guest.py`, `startup-script.sh`, and `SHA256SUMS` before `--execute`. If apply is interrupted
after `started.json`:

```sh
python3 scripts/yogi_replacement_recover.py cleanup \
  --op ~/yogi-ops/recover-LABEL \
  --confirm-instance 2720796557371812468 \
  --execute
```

Keep the operation directory. Do not reuse it. A stale fingerprint, an expired preflight (20 minutes), or a second
apply needs a new `plan`.

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

## Residual limits

- The earlier `setMetadata` `INTERNAL` / gRPC code 13 failure has no known cause. One successful call the same day does
  not clear it. This tool sends one write and then reads the instance back. It does not retry that write.
- There is no trusted measurement of the guest. A serial line is guest-written. The cursor, nonce, boot id, and
  instance id bind the line to this operation's output window. They do not prove the guest is honest. Do not commit
  the receipt as `builder.direct.hostKey`.
- The cloned baseline is the replacement disk's own pre-rotation public keys. It is not a read of the original VM.
- Authorized_keys lines whose comment is not this operation's fresh marker are preserved and counted. Exit 0 requires
  that count to be zero. This tool cannot name the earlier temporary key: that material is not in reviewed source.
- If chmod succeeds and a later step stops, a new operation still requires mode `0664`. It will not rotate on a unit
  that is already `0644`. Rolling the disk back is a separate snapshot decision and is not performed here.
- A crash between the two renames of one host-key pair can leave a mismatched pair. The pre-key snapshot remains the
  restore point, unused by this tool.
- `automaticRestart` can boot the guest again until metadata is removed and the instance stays `TERMINATED`. The
  marker blocks a second rotation. The stopper sends at most two stop requests.
- If `maxRunDuration` is not 2700 seconds with action `STOP`, preflight stops. This tool does not change scheduling.
