#!/usr/bin/env bash
# One os-release builder stage: release-stage.py receipt around one release-pipeline.py step.
# Usage: os-release-stage.sh STAGE PIPELINE_COMMAND [extra pipeline args...]; needs ROSE_BASE and the
# workflow's candidate environment (CANDIDATE_SHA, BUILD_NUMBER, BUILD_TIMESTAMP, CHANNEL, APPS). Every
# command gets the same candidate, including the dispatched apps that build checks against the version baselines.
set -euo pipefail
stage="$1" command="$2"
shift 2
exec python3 scripts/release-stage.py run --state "$ROSE_BASE/stages" --source-revision "$CANDIDATE_SHA" "$stage" -- \
  python3 scripts/release-pipeline.py "$command" --candidate-sha "$CANDIDATE_SHA" --build-number "$BUILD_NUMBER" \
  --build-timestamp "$BUILD_TIMESTAMP" --channel "$CHANNEL" --apps "${APPS:?APPS is required}" \
  --release-dir "$ROSE_BASE/release" "$@"
