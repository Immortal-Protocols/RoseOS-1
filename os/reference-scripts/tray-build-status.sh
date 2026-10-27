#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/pixel-os
phase=$(cat "$ROOT/tray-build.phase" 2>/dev/null || echo 'not started')
pid=$(cat "$ROOT/tray-build.pid" 2>/dev/null || true)
boot=$(cat "$ROOT/tray-build.boot-id" 2>/dev/null || true)
if [ -n "$pid" ] && [ "$boot" = "$(cat /proc/sys/kernel/random/boot_id)" ] && kill -0 "$pid" 2>/dev/null; then
  printf 'RUNNING pid=%s phase=%s\n' "$pid" "$phase"
  ps -eo pid,ppid,rss,etime,comm | awk '$5 ~ /soong_build|ninja|siso|javac/ {print}'
else
  printf 'NOT RUNNING; last phase=%s\n' "$phase"
fi
