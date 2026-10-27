#!/usr/bin/env bash
# READ-ONLY logcat capture for RT-3/RT-4 (OWNER_STOP broadcast, controller Stop/refusals) with epoch
# timestamps for correlation with backend logs. Usage: logcat-capture.sh <adb-serial> <output-file>
# Start before the scenario; stop with Ctrl-C after it. Clears nothing on the device.
set -euo pipefail
adb -s "$1" logcat -v epoch -v uid PixelOwnerStop:V BackgroundControl:V PixelVoice:V ActivityTaskManager:I AndroidRuntime:E '*:S' | tee "$2"
