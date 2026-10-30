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
node --test workspace/rose-agent/tests/trial-menu-removal.test.mjs
node --test workspace/rose-agent/tests/rose-presence.test.mjs
# APP-BUG-OPEN-001: the shell open_app refusal stays fail-closed; registered launches keep their turn's task.
node --test workspace/rose-agent/server/unbound-shell-open.test.mjs
# APP-BUG-OPEN-002: registered phone navigation and input run as the current request and honour Stop.
node --test workspace/rose-agent/server/phone-ui-tools.test.mjs
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
python3 os/tests/test_updater_releases.py
python3 os/tests/test_rose_home_role.py
python3 os/tests/test_rose_agent_self_update.py
python3 scripts/tests/test_systemui_component_update.py
python3 os/tests/persistent-app-experiment/test_systemui_stages.py
python3 os/tests/persistent-app-experiment/test_setup_emulator.py
python3 os/tests/persistent-app-experiment/test_report.py
python3 scripts/tests/test_release_manifest.py
python3 scripts/tests/test_release_stage.py
python3 scripts/tests/test_release_pipeline.py
python3 scripts/tests/test_permission_apex.py
python3 scripts/tests/test_release_candidate_contract.py
python3 scripts/tests/test_release_allocate.py
python3 scripts/tests/test_release_chain.py
python3 scripts/tests/test_release_acceptance.py
python3 scripts/tests/test_signing_job.py
python3 scripts/tests/test_signing_acceptance.py
python3 scripts/tests/test_rango_retained.py
python3 scripts/tests/test_github_publisher.py
python3 scripts/tests/test_host_adapters.py
python3 scripts/tests/test_host_build_reconcile.py
python3 scripts/tests/test_actions_gcp_login.py
python3 scripts/tests/test_app_release.py
python3 scripts/tests/test_app_release_publisher.py
python3 scripts/tests/test_app_reuse.py
python3 scripts/tests/test_device_contract.py
python3 scripts/tests/test_device_readiness.py
python3 scripts/tests/test_signing_tool_observation.py
python3 scripts/tests/test_blazer_preparation_preflight.py
python3 scripts/tests/test_signing_bootstrap.py
python3 scripts/tests/test_fold_bootstrap_proof.py
python3 scripts/tests/test_fold_tool_proof.py
python3 scripts/tests/test_models_signing_bootstrap.py
python3 scripts/tests/test_models_apk_signing.py
python3 scripts/tests/test_alpha_runtime.py
python3 scripts/tests/test_emulator_alpha.py
python3 scripts/tests/test_alpha_source.py
python3 scripts/tests/test_alpha_apk_diagnostic.py
python3 scripts/tests/test_alpha_image_package_scan.py
python3 scripts/tests/test_alpha_verify_retained.py
python3 scripts/tests/test_rose_verify.py
# MODELS-QA: loopback chat-completions fixture self-tests (fixture behaviour only, no client or model).
python3 qa/models/test_fixture_server.py
# Models serving draft: pins, Agent Platform container shape and derived capacity stay consistent.
python3 workspace/models/serving/tests/test_serving_config.py
python3 workspace/models/serving/tests/test_startup_guard.py
python3 workspace/models/serving/tests/test_render_upload.py
python3 workspace/models/serving/tests/test_local_profile.py
python3 os/tests/test_cuttlefish_alpha_product.py
# FAM-03a: Pixel 10 family source contract (frankel, blazer, rango) against pinned public adevtool/build fixtures.
python3 os/tests/test_device_contract.py
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
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseReleases" \
 os/overlays/packages/apps/Updater/src/app/seamlessupdate/client/RoseReleases.java os/tests/RoseReleasesTest.java
java -cp "$JAVA_TESTS/RoseReleases" app.seamlessupdate.client.RoseReleasesTest
# Pure Rose Agent app logic, compiled the same way.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/ConversationState" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/tests/ConversationStateTest.java
java -cp "$JAVA_TESTS/ConversationState" dev.pixel.voice.ConversationStateTest
# Independent Rose visual axes (connection, microphone, voice) derived from ConversationState.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseVisualState" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/src/dev/pixel/voice/RoseVisualState.java \
 workspace/rose-agent/app/tests/RoseVisualStateTest.java
java -cp "$JAVA_TESTS/RoseVisualState" dev.pixel.voice.RoseVisualStateTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeRose" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/src/dev/pixel/voice/RoseVisualState.java \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeRose.java workspace/rose-agent/app/tests/HomeRoseTest.java
java -cp "$JAVA_TESTS/HomeRose" dev.pixel.voice.HomeRoseTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RosePresence" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/src/dev/pixel/voice/RoseVisualState.java \
 workspace/rose-agent/app/src/dev/pixel/voice/RosePresenceModel.java workspace/rose-agent/app/tests/RosePresenceModelTest.java
java -cp "$JAVA_TESTS/RosePresence" dev.pixel.voice.RosePresenceModelTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/ConversationPresentation" \
 workspace/rose-agent/app/src/dev/pixel/voice/ConversationState.java workspace/rose-agent/app/src/dev/pixel/voice/ConversationPresentation.java \
 workspace/rose-agent/app/tests/ConversationPresentationTest.java
java -cp "$JAVA_TESTS/ConversationPresentation" dev.pixel.voice.ConversationPresentationTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeEntry" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeEntry.java workspace/rose-agent/app/tests/HomeEntryTest.java
java -cp "$JAVA_TESTS/HomeEntry" dev.pixel.voice.HomeEntryTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeReuse" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeReuse.java workspace/rose-agent/app/tests/HomeReuseTest.java
java -cp "$JAVA_TESTS/HomeReuse" dev.pixel.voice.HomeReuseTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomePages" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomePages.java workspace/rose-agent/app/tests/HomePagesTest.java
java -cp "$JAVA_TESTS/HomePages" dev.pixel.voice.HomePagesTest
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
# Explicit update channels: legacy check is Beta unchanged; unconfigured Alpha/Prod never fall back.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/DirectUpdateChannel" \
 workspace/rose-agent/app/src/dev/pixel/voice/PublicApkDownload.java workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java \
 workspace/rose-agent/app/src/dev/pixel/voice/DirectUpdate.java workspace/rose-agent/app/tests/DirectUpdateTest.java \
 workspace/rose-agent/app/tests/DirectUpdateChannelTest.java
java -cp "$JAVA_TESTS/DirectUpdateChannel" dev.pixel.voice.DirectUpdateChannelTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/UpdateRecovery" \
 workspace/rose-agent/app/src/dev/pixel/voice/UpdateRecovery.java workspace/rose-agent/app/src/dev/pixel/voice/DirectUpdate.java \
 workspace/rose-agent/app/src/dev/pixel/voice/PublicApkDownload.java workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java \
 workspace/rose-agent/app/tests/UpdateRecoveryTest.java
java -cp "$JAVA_TESTS/UpdateRecovery" dev.pixel.voice.UpdateRecoveryTest
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
