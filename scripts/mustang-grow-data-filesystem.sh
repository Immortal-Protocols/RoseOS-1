#!/bin/bash
# One bounded maintenance operation after the existing data disk is expanded.
# Never partitions, formats, deletes files, changes access or starts a build.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
readonly expected_instance=5585425566393384049
readonly expected_uuid=4183bff8-6be4-46bb-a641-2193d19813ee
readonly target_bytes=3221225472000 # 3000 GiB, Compute Engine's size unit
readonly mount=/mnt/disks/mustang-data
readonly device=/dev/disk/by-id/google-mustang-data
readonly metadata=http://metadata.google.internal/computeMetadata/v1
readonly operation=${MUSTANG_GROW_OPERATION:?operation identity required}
refuse() { echo "Mustang filesystem growth refused: $1" >&2; exit 4; }
[[ $(id -u) == 0 ]] || refuse 'root maintenance context required'
seen=$(curl --fail --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' "$metadata/instance/id")
[[ $seen == "$expected_instance" ]] || refuse 'instance identity differs'
[[ $(findmnt --noheadings --output TARGET --target "$mount") == "$mount" ]] || refuse 'mount differs'
[[ $(findmnt --noheadings --output FSTYPE --target "$mount") == ext4 ]] || refuse 'filesystem type differs'
[[ $(findmnt --noheadings --output UUID --target "$mount") == "$expected_uuid" ]] || refuse 'filesystem UUID differs'
[[ $(readlink -f "$device") == "$(readlink -f "$(findmnt --noheadings --output SOURCE --target "$mount")")" ]] \
  || refuse 'mounted device is not the admitted data disk'
[[ $(lsblk --nodeps --noheadings --output TYPE "$device") == disk ]] || refuse 'partitioned device needs separate review'
before_bytes=$(blockdev --getsize64 "$device")
[[ $before_bytes == 1610612736000 || $before_bytes == "$target_bytes" ]] || refuse 'unexpected device capacity'
# There is no Actions run or JIT config in this maintenance window. Avoid runner
# polling/diagnostic writes while the old filesystem is full.
systemctl stop mustang-ephemeral-runner.service
receipt=$(mktemp /var/tmp/mustang-disk-growth.XXXXXX)
trap 'rm -f "$receipt"' EXIT
python3 - "$expected_instance" "$expected_uuid" "$operation" "$before_bytes" > "$receipt" <<'PY'
import json,sys
instance,uuid,operation,size=sys.argv[1:]
print(json.dumps(dict(schema='roseos.mustang-data-growth-preflight.v1',operation=operation,
    instanceId=instance,filesystemUuid=uuid,deviceBytes=int(size),wholeDisk=True)))
PY
curl --fail --silent --show-error --max-time 10 -X PUT -H 'Metadata-Flavor: Google' \
  --data-binary @"$receipt" "$metadata/instance/guest-attributes/mustang/disk-growth-preflight"
# The host resizes the cloud disk only after reading the validated whole-disk
# preflight. Wait for the kernel to observe its new capacity; do not repartition.
for (( poll=0; poll<60; poll++ )); do
  [[ $(blockdev --getsize64 "$device") == "$target_bytes" ]] && break
  sleep 5
done
[[ $(blockdev --getsize64 "$device") == "$target_bytes" ]] || refuse '3000GiB cloud growth not observed'
timeout 600 resize2fs "$device"
python3 - "$mount" "$expected_instance" "$expected_uuid" "$target_bytes" "$operation" > "$receipt" <<'PY'
import json,os,sys
mount,instance,uuid,target,operation=sys.argv[1:]
v=os.statvfs(mount);size=v.f_blocks*v.f_frsize
if size < int(target)*0.95 or v.f_bavail<=0 or v.f_favail<=0:
    raise SystemExit('expanded filesystem readback is not usable')
print(json.dumps(dict(schema='roseos.mustang-data-growth.v1',operation=operation,
    instanceId=instance,filesystemUuid=uuid,mount=mount,deviceBytes=int(target),
    filesystemBytes=size,availableBytes=v.f_bavail*v.f_frsize,totalInodes=v.f_files,availableInodes=v.f_favail)))
PY
cat "$receipt"
curl --fail --silent --show-error --max-time 10 -X PUT -H 'Metadata-Flavor: Google' \
  --data-binary @"$receipt" "$metadata/instance/guest-attributes/mustang/disk-growth"
