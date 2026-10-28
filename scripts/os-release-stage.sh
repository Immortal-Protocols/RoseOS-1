#!/usr/bin/env bash
# One os-release builder stage: release-stage.py receipt around one release-pipeline.py step.
# Usage: os-release-stage.sh STAGE PIPELINE_COMMAND [extra pipeline args...]; needs ROSE_BASE and the
# workflow's candidate environment.
set -euo pipefail
stage="$1" command="$2"
shift 2
exec python3 scripts/release-stage.py run --state "$ROSE_BASE/stages" --source-revision "$CANDIDATE_SHA" "$stage" -- \
  python3 scripts/release-pipeline.py "$command" --candidate-sha "$CANDIDATE_SHA" --build-number "$BUILD_NUMBER" \
  --build-timestamp "$BUILD_TIMESTAMP" --channel "$CHANNEL" --release-dir "$ROSE_BASE/release" "$@"
