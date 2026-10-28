#!/usr/bin/env bash
# Offline tests only: no live phone, provider, VM, or signing access.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Rose Agent page voice state, loaded with its real assets and a fake provider.
node --test workspace/rose-agent/tests/voice-state.test.mjs workspace/rose-agent/tests/app-delivery.test.mjs workspace/rose-agent/tests/task-continuity.test.mjs workspace/rose-agent/tests/spoken-progress.test.mjs workspace/rose-agent/tests/renderer-recovery.test.mjs
# Every Native.* call in the app's pages names a method its Java bridge exposes.
node --test workspace/rose-agent/tests/native-bridge-contract.test.mjs
# App-tree files that build.sh replaces must match the module source it ships.
node --test workspace/rose-agent/tests/staged-copies.test.mjs
# Current-page replacements for the retired WebRTC-page tests, and the EventDelivery server check.
node --test workspace/rose-agent/tests/page-regressions.test.mjs workspace/rose-agent/server/event-delivery.test.mjs
# APP-REMOVE-001: retired DeepSeek, Luna-first and ARTEMIS trial controls stay removed; shared paths stay.
node --test workspace/rose-agent/tests/trial-removal.test.mjs
node --test \
 workspace/rose-agent/security/device-auth.test.mjs \
 workspace/rose-agent/security/recovery-download.test.mjs \
 workspace/rose-agent/conversation/mode.test.mjs \
 workspace/rose-agent/server/session-lease.test.mjs \
 workspace/rose-agent/server/background-app-policy.test.mjs \
 workspace/rose-agent/server/owner-stop.test.mjs \
 workspace/rose-agent/server/server-stop-gate.test.mjs \
 workspace/rose-agent/tests/home-launcher.test.mjs \
 workspace/experiments/phone-command-push/push.test.mjs \
 workspace/experiments/phone-command-push/delivery.test.cjs \
 workspace/experiments/phone-control-metrics/metrics.test.mjs \
 workspace/experiments/guardian-voice/bridge.test.mjs
python3 os/background-control/tests/test_policy.py
python3 os/background-control/tests/test_stop_wiring.py
python3 os/background-control/tests/test_watch_wiring.py
python3 os/background-control/tests/test_preinstall.py
python3 os/tests/test_apply_os_changes.py
python3 os/tests/test_integration_copy.py
python3 os/tests/test_tray_contract.py
python3 os/tests/test_installed_precedence.py
python3 os/tests/test_updater_channels.py
python3 os/tests/test_updater_inclusion.py
python3 os/tests/test_rose_home_role.py
python3 scripts/tests/test_release_manifest.py
python3 scripts/tests/test_release_stage.py
python3 scripts/tests/test_release_pipeline.py
python3 scripts/tests/test_permission_apex.py
python3 scripts/tests/test_release_candidate_contract.py
python3 scripts/tests/test_github_publisher.py
python3 scripts/tests/test_host_adapters.py
python3 scripts/tests/test_host_build_reconcile.py
python3 scripts/tests/test_actions_gcp_login.py
python3 scripts/tests/test_app_release.py
python3 scripts/tests/test_app_release_publisher.py
python3 scripts/tests/test_emulator_alpha.py
python3 scripts/tests/test_alpha_source.py
python3 scripts/tests/test_rose_verify.py
python3 os/tests/test_cuttlefish_alpha_product.py
node --test workspace/rose-agent/delivery/catalog.test.mjs
# Pure controller logic; each test compiles with only the class it covers.
JAVA_TESTS="$(mktemp -d)"
trap 'rm -rf "$JAVA_TESTS"' EXIT
for pair in LabelTargets:agent-pip MirrorWindowFrame:agent-pip StopLedger:agent-pip WatchBinding:agent-pip \
 PanePolicy:agent-split InstallerRecoveryPolicy:agent-split; do
 name="${pair%%:*}" variant="${pair##*:}"
 java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/$name" \
  "os/background-control/$variant/src/dev/pixel/backgroundcontrol/$name.java" \
  "os/background-control/tests/${name}Test.java"
 java -cp "$JAVA_TESTS/$name" "dev.pixel.backgroundcontrol.${name}Test"
done
# SystemUI tray tap mapping; compiled alone like the controller tests above.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/AgentStatusControls" \
 os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentStatusControls.java \
 os/tests/AgentStatusControlsTest.java
java -cp "$JAVA_TESTS/AgentStatusControls" com.android.wm.shell.AgentStatusControlsTest
# SystemUI tray subscription lifecycle (screen-off suspend, exactly-once resume), same way.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/AgentTrayLink" \
 os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentTrayLink.java \
 os/tests/AgentTrayLinkTest.java
java -cp "$JAVA_TESTS/AgentTrayLink" com.android.wm.shell.AgentTrayLinkTest
# Updater channel rules (Prod/Beta/Alpha, legacy migration, unpublished feeds).
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseChannels" \
 os/overlays/packages/apps/Updater/src/app/seamlessupdate/client/RoseChannels.java os/tests/RoseChannelsTest.java
java -cp "$JAVA_TESTS/RoseChannels" app.seamlessupdate.client.RoseChannelsTest
# Pure Rose Agent app logic, compiled the same way.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/ConversationState" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/tests/ConversationStateTest.java
java -cp "$JAVA_TESTS/ConversationState" dev.pixel.voice.ConversationStateTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeSessionMark" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/src/dev/pixel/voice/HomeSessionMark.java \
 workspace/rose-agent/app/tests/HomeSessionMarkTest.java
java -cp "$JAVA_TESTS/HomeSessionMark" dev.pixel.voice.HomeSessionMarkTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeEntry" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeEntry.java workspace/rose-agent/app/tests/HomeEntryTest.java
java -cp "$JAVA_TESTS/HomeEntry" dev.pixel.voice.HomeEntryTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeReuse" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeReuse.java workspace/rose-agent/app/tests/HomeReuseTest.java
java -cp "$JAVA_TESTS/HomeReuse" dev.pixel.voice.HomeReuseTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/DockLabel" \
 workspace/rose-agent/app/src/dev/pixel/voice/DockLabel.java workspace/rose-agent/app/tests/DockLabelTest.java
java -Djava.awt.headless=true -cp "$JAVA_TESTS/DockLabel" dev.pixel.voice.DockLabelTest workspace/rose-agent/app/assets/fonts/nunito.ttf
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/UpdateCheck" \
 workspace/rose-agent/app/src/dev/pixel/voice/UpdateCheck.java workspace/rose-agent/app/tests/UpdateCheckTest.java
java -cp "$JAVA_TESTS/UpdateCheck" dev.pixel.voice.UpdateCheckTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/SelfUpdate" \
 workspace/rose-agent/app/src/dev/pixel/voice/UpdateCheck.java workspace/rose-agent/app/src/dev/pixel/voice/SelfUpdate.java workspace/rose-agent/app/tests/SelfUpdateTest.java
java -cp "$JAVA_TESTS/SelfUpdate" dev.pixel.voice.SelfUpdateTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/PublicApkDownload" \
 workspace/rose-agent/app/src/dev/pixel/voice/PublicApkDownload.java workspace/rose-agent/app/tests/PublicApkDownloadTest.java
java -cp "$JAVA_TESTS/PublicApkDownload" dev.pixel.voice.PublicApkDownloadTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/DirectUpdate" \
 workspace/rose-agent/app/src/dev/pixel/voice/PublicApkDownload.java workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java \
 workspace/rose-agent/app/src/dev/pixel/voice/DirectUpdate.java workspace/rose-agent/app/tests/DirectUpdateTest.java
java -cp "$JAVA_TESTS/DirectUpdate" dev.pixel.voice.DirectUpdateTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/TaskPresentation" \
 workspace/rose-agent/app/src/dev/pixel/voice/TaskPresentation.java workspace/rose-agent/app/tests/TaskPresentationTest.java
java -cp "$JAVA_TESTS/TaskPresentation" dev.pixel.voice.TaskPresentationTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/TaskControl" \
 workspace/rose-agent/app/src/dev/pixel/voice/TaskControl.java workspace/rose-agent/app/tests/TaskControlTest.java
java -cp "$JAVA_TESTS/TaskControl" dev.pixel.voice.TaskControlTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RendererRecovery" \
 workspace/rose-agent/app/src/dev/pixel/voice/RendererRecovery.java workspace/rose-agent/app/tests/RendererRecoveryTest.java
java -cp "$JAVA_TESTS/RendererRecovery" dev.pixel.voice.RendererRecoveryTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CallbackOwner" \
 workspace/rose-agent/app/src/dev/pixel/voice/CallbackOwner.java workspace/rose-agent/app/tests/CallbackOwnerTest.java
java -cp "$JAVA_TESTS/CallbackOwner" dev.pixel.voice.CallbackOwnerTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/PhoneCommandGate" \
 workspace/rose-agent/app/src/dev/pixel/voice/PhoneCommandGate.java workspace/rose-agent/app/tests/PhoneCommandGateTest.java
java -cp "$JAVA_TESTS/PhoneCommandGate" dev.pixel.voice.PhoneCommandGateTest
# Native glasses-button duplicate suppression (media-button handling on the native side).
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/Button" \
 workspace/rose-agent/app/src/dev/pixel/voice/GlassesButtonGate.java workspace/rose-agent/tests/java/dev/pixel/voice/ButtonTest.java
java -cp "$JAVA_TESTS/Button" dev.pixel.voice.ButtonTest
