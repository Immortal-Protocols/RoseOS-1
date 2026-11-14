#!/usr/bin/env bash
# Offline tests only: no live phone, provider, VM, or signing access.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Nov 10 1F/1G: bounded private life and real background-task lifecycle.
bash scripts/test-background-life.sh
# Shared Codex voice adapter: synthetic account/model/turn replies, no provider access.
node --test workspace/rose-agent/server/codex-voice.test.mjs workspace/rose-agent/server/codex-provider-start.test.mjs workspace/rose-agent/server/app-build.test.mjs
# Rose Agent page voice state, loaded with its real assets and a fake provider.
node --test workspace/rose-agent/tests/voice-state.test.mjs workspace/rose-agent/tests/app-delivery.test.mjs workspace/rose-agent/tests/task-continuity.test.mjs workspace/rose-agent/tests/spoken-progress.test.mjs workspace/rose-agent/tests/renderer-recovery.test.mjs
# Every Native.* call in the app's pages names a method its Java bridge exposes.
node --test workspace/rose-agent/tests/native-bridge-contract.test.mjs
node --test workspace/rose-agent/tests/local-rose-auth.test.mjs
# Nov 7 plan 1O: the customization CLI drives the same catalog operations as conversation.
node --test workspace/rose-agent/customization/rose-customize.test.mjs
python3 -I workspace/rose-agent/customization/test_conversation_tools.py
# #744 deferred controls and per-activity conversation theme acknowledgment.
python3 -I workspace/rose-agent/customization/test_deferred_theme.py
# Local Rose task events from the real PR318 producer, through the local() provider bridge.
node --test workspace/rose-agent/voice-providers/local-tasks.test.cjs
# #410 item 6: microphone-input mute on the capture side, and the provider contract it relies on.
node --test workspace/rose-agent/tests/mic-input-capture.test.mjs workspace/rose-agent/voice-providers/providers.test.cjs
# Local Rose task list from the producer through the page and bridge into the native overview feed (LOCAL-TASK-FEED-002).
node --test workspace/rose-agent/tests/local-task-feed.test.mjs
node --test workspace/rose-agent/tests/local-task-card.test.mjs
# App-tree files that build.sh replaces must match the module source it ships.
node --test workspace/rose-agent/tests/staged-copies.test.mjs
# Current-page replacements for the retired WebRTC-page tests, and the EventDelivery server check.
node --test workspace/rose-agent/tests/page-regressions.test.mjs workspace/rose-agent/server/event-delivery.test.mjs
# APP-REMOVE-001: retired DeepSeek, Luna-first and ARTEMIS trial controls stay removed; shared paths stay.
node --test workspace/rose-agent/tests/trial-removal.test.mjs
node --test workspace/rose-agent/tests/trial-menu-removal.test.mjs
node --test workspace/rose-agent/tests/rose-presence.test.mjs workspace/rose-agent/tests/assistant-name.test.mjs
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
 workspace/rose-agent/server/phone-origin-pairing.test.mjs \
 workspace/rose-agent/tests/home-launcher.test.mjs \
 workspace/rose-agent/tests/exported-components.test.mjs \
 workspace/experiments/phone-command-push/push.test.mjs \
 workspace/experiments/phone-command-push/delivery.test.cjs \
 workspace/experiments/phone-control-metrics/metrics.test.mjs \
 workspace/experiments/guardian-voice/bridge.test.mjs
python3 os/background-control/tests/test_policy.py
python3 os/background-control/tests/test_stop_wiring.py
python3 os/background-control/tests/test_watch_wiring.py
python3 os/background-control/tests/test_anyapp_wiring.py
python3 os/background-control/tests/test_notification_wording.py
python3 os/background-control/tests/test_preview_wiring.py
python3 os/background-control/tests/test_current_apps_wiring.py
python3 os/background-control/tests/test_current_apps_parity.py
python3 os/background-control/tests/test_replacement_wiring.py
python3 os/background-control/tests/test_multi_session_wiring.py
python3 os/background-control/tests/test_vision_input_wiring.py
python3 os/background-control/tests/test_preinstall.py
python3 os/tests/test_apply_os_changes.py
python3 os/tests/test_yogi_port.py
python3 os/tests/test_integration_copy.py
python3 os/tests/test_tray_contract.py
python3 os/tests/test_installed_precedence.py
python3 os/tests/test_updater_channels.py
python3 os/tests/test_updater_inclusion.py
python3 os/tests/test_updater_releases.py
python3 os/tests/test_rose_home_role.py
python3 os/tests/test_rose_agent_self_update.py
python3 os/tests/test_rose_switcher.py
python3 os/tests/test_rose_edge_resize_staged.py
python3 os/tests/test_launcher3_compile_contract.py
python3 os/tests/test_frameworks_base_wmshell_contract.py
python3 os/tests/test_frankel_lunch_inputs.py
python3 os/tests/test_automated_devices_contract.py
python3 os/tests/test_rose_edge_shell.py
python3 os/tests/test_background_control_prototype.py
python3 os/tests/test_anyapp_takeover_platform.py
python3 os/tests/test_rose_shade.py
# 1M: the Rose keyboard (package boundaries, build inclusion, and the design's 37 model checks).
python3 os/tests/test_rose_keyboard.py
python3 scripts/tests/test_systemui_component_update.py
python3 os/tests/persistent-app-experiment/test_systemui_stages.py
python3 os/tests/persistent-app-experiment/test_setup_emulator.py
python3 os/tests/persistent-app-experiment/test_report.py
# Isolated SystemUI trial: exact installed SDK revisions against the QA manifest pins (parses given tables, installs nothing).
python3 os/tests/persistent-app-experiment/test_sdk_revisions.py
python3 scripts/tests/test_release_manifest.py
python3 scripts/tests/test_release_stage.py
python3 scripts/tests/test_release_pipeline.py
python3 scripts/tests/test_edge_artifact.py
python3 scripts/tests/test_app_tests_paths.py
python3 scripts/tests/test_permission_apex.py
python3 scripts/tests/test_release_candidate_contract.py
python3 scripts/tests/test_release_allocate.py
python3 scripts/tests/test_frankel_draft_recovery.py
python3 scripts/tests/test_frankel_draft_promotion.py
python3 scripts/tests/test_frankel_allocation_recovery.py
python3 scripts/tests/test_release_chain.py
# Mustang one-button chain wiring (canned run metadata; no network, token, host or dispatch).
python3 scripts/tests/test_mustang_ota_chain_workflow.py
# Yogi OTA route: contract, guards, allocation, packaged-Updater/trust and publication checks (synthetic; no host, key or network).
python3 scripts/tests/test_yogi_ota.py
python3 scripts/tests/test_yogi_full.py
# Yogi direct operator route: plan/start/run/status/cancel/cleanup and the guest agent (fake gcloud/ssh; no VM, SSH or key).
python3 scripts/tests/test_yogi_direct.py
# Yogi replacement recovery: dry-run plan, fake control plane, in-memory guest (no GCP, SSH, or key).
python3 scripts/tests/test_yogi_replacement_recover.py
# Mustang raised-edge gate (#570): the shared checker on synthetic archives; no signer, host or dispatch.
python3 scripts/tests/test_mustang_edge_gate.py
python3 scripts/tests/test_mustang_hosttools.py
python3 scripts/tests/test_mustang_hostdeps.py
python3 scripts/tests/test_mustang_prepare_lock.py
python3 scripts/tests/test_mustang_lock_admin.py
python3 scripts/tests/test_mustang_factory_input.py
python3 scripts/tests/test_agent_beta_auto.py
# 1F daily release dispatcher (canned API answers; no network, token, dispatch or host).
python3 scripts/tests/test_daily_release.py
# 1A lane capacity map against the committed route (os-release.yml, BUILDER, host configs; no runner, VM or API).
python3 scripts/tests/test_lane_capacity.py
python3 scripts/tests/test_release_transport.py
python3 scripts/tests/test_release_acceptance.py
python3 scripts/tests/test_signing_job.py
python3 scripts/tests/test_signing_acceptance.py
python3 scripts/tests/test_os_sign_workflow.py
python3 scripts/tests/test_rango_retained.py
python3 scripts/tests/test_github_publisher.py
python3 scripts/tests/test_host_adapters.py
python3 scripts/tests/test_host_build_reconcile.py
python3 scripts/tests/test_builder_reclaim.py
python3 scripts/tests/test_app_inputs_export.py
python3 scripts/tests/test_actions_gcp_login.py
python3 scripts/tests/test_app_release.py
python3 scripts/tests/test_app_release_publisher.py
python3 scripts/tests/test_hosted_app_signing_reuse.py
python3 scripts/tests/test_agent_hosted_release.py
python3 workspace/rose-agent/tests/test_portable_build.py
python3 scripts/tests/test_app_reuse.py
python3 scripts/tests/test_device_contract.py
python3 scripts/tests/test_device_readiness.py
python3 scripts/tests/test_signing_tool_observation.py
python3 scripts/tests/test_blazer_preparation_preflight.py
# Blazer (Pixel 10 Pro) flavor contract and unsigned build-only recipe (synthetic tree, fake runner).
python3 os/tests/test_blazer_flavor.py
python3 scripts/tests/test_blazer_build_only.py
python3 scripts/tests/test_blazer_integration.py
python3 scripts/tests/test_blazer_candidate_bundle.py
python3 scripts/tests/test_blazer_reconcile_seed.py
python3 scripts/tests/test_blazer_integrate_wiring.py
python3 scripts/tests/test_blazer_allocate.py
python3 scripts/tests/test_blazer_binding_observe.py
# Factory package verifier (pinned fixtures; the signature cases need OpenSSH ssh-keygen and skip without it).
python3 scripts/tests/test_factory_package.py
# Retained Rango (Fold) factory observer (synthetic packages and fake fastboot; no device, download or host).
python3 scripts/tests/test_rango_factory_observe.py
python3 scripts/tests/test_rango_factory_observe_workflow.py
# Blazer (Pixel 10 Pro) preparation workflow wiring (stand-ins and the fake builder adapter; no host, download or vendor run).
python3 scripts/tests/test_blazer_prep_workflow.py
python3 scripts/tests/test_blazer_compile_workflow.py
# Fold factory package workflow wiring (stand-ins and mocks; no host, lock, generation or inspection run).
python3 scripts/tests/test_rango_factory_package_workflow.py
# Frankel factory installer workflow wiring (synthetic builder, stand-ins; no host, lock, packaging or signing run).
python3 scripts/tests/test_frankel_factory_package_workflow.py
python3 scripts/tests/test_rango_factory_inspect_workflow.py
# Launcher3 compile-only adapter (synthetic trees, stand-in processes; no host, copy, lunch or build).
python3 scripts/tests/test_launcher3_compile.py
python3 scripts/tests/test_launcher3_compile_workflow.py
python3 scripts/tests/test_launcher3_bundle_report.py
python3 scripts/tests/test_signing_bootstrap.py
python3 scripts/tests/test_fold_bootstrap_proof.py
python3 scripts/tests/test_fold_tool_proof.py
python3 scripts/tests/test_ssh_keygen_observe.py
python3 scripts/tests/test_factory_delivery.py
python3 scripts/tests/test_factory_targets.py
python3 scripts/tests/test_models_app_not_bundled.py
python3 scripts/tests/test_alpha_runtime.py
python3 scripts/tests/test_emulator_alpha.py
python3 scripts/tests/test_alpha_source.py
python3 scripts/tests/test_alpha_apk_diagnostic.py
python3 scripts/tests/test_alpha_image_package_scan.py
python3 scripts/tests/test_alpha_verify_retained.py
python3 scripts/tests/test_rose_verify.py
# MODELS-QA: loopback chat-completions fixture self-tests (fixture behaviour only, no client or model).
python3 qa/models/test_fixture_server.py
# EXP006 isolated SystemUI trial: QA pins, licence basis and offline setup-plan validation (no VM, network or SDK).
python3 qa/systemui-trial/test_trial_plan.py
# Models serving draft: pins, Agent Platform container shape and derived capacity stay consistent.
python3 workspace/models/serving/tests/test_serving_config.py
# Models rose_cloud (stand-in brain and speech, no model or audio). The PR288 cases need ROSE_PR288_DIR (PR288 head
# e1b702e prototypes/voice-phone-demo) and its numpy/pillow/fastapi/cryptography; without them they skip and say why.
python3 -I workspace/models/rose_cloud/tests/test_vllm_brain.py
python3 -I workspace/models/rose_cloud/tests/test_contract.py
python3 -I workspace/models/rose_cloud/tests/test_pr288_tasks.py
python3 -I workspace/models/rose_cloud/tests/test_coop.py
python3 -I workspace/models/rose_cloud/tests/test_speech_wire.py
python3 -I workspace/models/rose_cloud/tests/test_preflight.py
python3 -I workspace/models/rose_cloud/tests/test_listen.py
python3 -I workspace/models/rose_cloud/tests/test_vision.py
# Staging and its HEAD-only refused-destination probe against local 127.0.0.1 fixture servers (no Hub, PyPI or VM).
python3 -I workspace/models/rose_cloud/tests/test_stage.py
python3 -I workspace/models/rose_cloud/tests/test_head_probe.py
# Offline dependency/config validation helper against a synthetic wheel and stand-in modules (no model, no network).
python3 -I workspace/models/rose_cloud/tests/test_validate.py
# One-shot Qwen text GPU diagnostic helper against a stand-in vllm package (no GPU, model or network).
python3 -I workspace/models/rose_cloud/tests/test_qwen_gpu.py
# One bounded live adapter session (multi-turn, image history, cancellation) against a stand-in vLLM server.
python3 -I workspace/models/rose_cloud/tests/test_adapter_session.py
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
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/VisionInput" \
 os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/VisionInput.java os/background-control/tests/VisionInputTest.java
java -cp "$JAVA_TESTS/VisionInput" dev.pixel.backgroundcontrol.VisionInputTest
for pair in LabelTargets:agent-pip MirrorWindowFrame:agent-pip StopLedger:agent-pip WatchBinding:agent-pip AnyAppControl:agent-pip ViewerPresentation:agent-pip PreviewPolicy:agent-pip CurrentApps:agent-pip Replacement:agent-pip \
 PanePolicy:agent-split InstallerRecoveryPolicy:agent-split; do
 name="${pair%%:*}" variant="${pair##*:}"
 java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/$name" \
  "os/background-control/$variant/src/dev/pixel/backgroundcontrol/$name.java" \
  "os/background-control/tests/${name}Test.java"
 java -cp "$JAVA_TESTS/$name" "dev.pixel.backgroundcontrol.${name}Test"
done
# MultiSession (two-app routing) uses CurrentApps' validated per-session answer.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/MultiSession" \
 os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/MultiSession.java \
 os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/CurrentApps.java \
 os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/ViewerStop.java os/background-control/tests/MultiSessionTest.java
java -cp "$JAVA_TESTS/MultiSession" dev.pixel.backgroundcontrol.MultiSessionTest
# F06: the controller never takes the owner's Home or recents (HomeTarget).
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeTarget" \
 os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/HomeTarget.java os/background-control/tests/HomeTargetTest.java
java -cp "$JAVA_TESTS/HomeTarget" dev.pixel.backgroundcontrol.HomeTargetTest
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
# Plan 1E: SystemUI follows Rose Agent's theme, mirrors it to Settings.Global for Launcher3 and the keyboard, and
# acknowledges only what it shows.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseThemeMirror" \
 os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/RoseTheme.java \
 os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/RoseThemeMirror.java \
 os/tests/RoseThemeMirrorTest.java
java -cp "$JAVA_TESTS/RoseThemeMirror" com.android.wm.shell.RoseThemeMirrorTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -encoding UTF-8 -d "$JAVA_TESTS/RoseShade" \
 os/overlays/frameworks/base/packages/SystemUI/src/com/android/systemui/roseshade/RoseShadeModel.java \
 os/overlays/frameworks/base/packages/SystemUI/src/com/android/systemui/roseshade/RoseShadeReplies.java \
 os/overlays/frameworks/base/packages/SystemUI/src/com/android/systemui/roseshade/RoseQuickSettings.java \
 os/overlays/frameworks/base/packages/SystemUI/src/com/android/systemui/roseshade/RoseShadeDrag.java \
 os/tests/roseshade/RoseShadeTest.java
java -cp "$JAVA_TESTS/RoseShade" com.android.systemui.roseshade.RoseShadeTest
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
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/MicInputCapture" \
 workspace/rose-agent/app/src/dev/pixel/voice/MicInputCapture.java workspace/rose-agent/app/tests/MicInputCaptureTest.java
java -cp "$JAVA_TESTS/MicInputCapture" dev.pixel.voice.MicInputCaptureTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/ClipboardWrite" \
 workspace/rose-agent/app/src/dev/pixel/voice/ClipboardWrite.java workspace/rose-agent/app/tests/ClipboardWriteTest.java
java -cp "$JAVA_TESTS/ClipboardWrite" dev.pixel.voice.ClipboardWriteTest
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
# Project 2: the phone's check of rose-layout-v1 results (catalog cat-1), the page's saved state (pins, undo, recents),
# its snapshot and requests, and the path through Models' composer core (with the labelled starter, not a model) to
# request-to-render timing.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/Workspace" \
 workspace/models/local-composer/java/src/rose/models/composer/*.java \
 workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceLayout.java \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceState.java workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceComposer.java \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceSnapshot.java workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceTiming.java \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceStarter.java workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceEngine.java \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceAgent.java workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceRequests.java \
 workspace/rose-agent/app/tests/WorkspaceTest.java workspace/rose-agent/app/tests/WorkspaceSnapshotTest.java \
 workspace/rose-agent/app/tests/WorkspaceEngineTest.java workspace/rose-agent/app/tests/WorkspaceAgentTest.java \
 workspace/rose-agent/app/tests/WorkspaceReplayTest.java
java -cp "$JAVA_TESTS/Workspace" dev.pixel.voice.WorkspaceTest
java -cp "$JAVA_TESTS/Workspace" dev.pixel.voice.WorkspaceSnapshotTest
java -cp "$JAVA_TESTS/Workspace" dev.pixel.voice.WorkspaceEngineTest
java -cp "$JAVA_TESTS/Workspace" dev.pixel.voice.WorkspaceAgentTest
# The approved HiFi r2 behaviour contracts replayed through the real phone path (scripted composer, not a model).
java -cp "$JAVA_TESTS/Workspace" dev.pixel.voice.WorkspaceReplayTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeOverview" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeOverview.java workspace/rose-agent/app/tests/HomeOverviewTest.java
java -cp "$JAVA_TESTS/HomeOverview" dev.pixel.voice.HomeOverviewTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeAppLayout" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java workspace/rose-agent/app/tests/HomeAppLayoutTest.java
java -cp "$JAVA_TESTS/HomeAppLayout" dev.pixel.voice.HomeAppLayoutTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomePlan" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java workspace/rose-agent/app/src/dev/pixel/voice/HomePlan.java \
 workspace/rose-agent/app/tests/HomePlanTest.java
java -cp "$JAVA_TESTS/HomePlan" dev.pixel.voice.HomePlanTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/AppRemoval" \
 workspace/rose-agent/app/src/dev/pixel/voice/AppRemoval.java workspace/rose-agent/app/tests/AppRemovalTest.java
java -cp "$JAVA_TESTS/AppRemoval" dev.pixel.voice.AppRemovalTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/ReturnModel" \
 workspace/rose-agent/app/src/dev/pixel/voice/ReturnModel.java workspace/rose-agent/app/tests/ReturnModelTest.java
java -cp "$JAVA_TESTS/ReturnModel" dev.pixel.voice.ReturnModelTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/BuilderRules" \
 workspace/rose-builder/src/dev/pixel/builder/BuildJob.java workspace/rose-builder/src/dev/pixel/builder/InstallPlan.java \
 workspace/rose-builder/src/dev/pixel/builder/ShareMessage.java workspace/rose-builder/tests/BuilderRulesTest.java
java -cp "$JAVA_TESTS/BuilderRules" dev.pixel.builder.BuilderRulesTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RequestIds" \
 workspace/rose-builder/src/dev/pixel/builder/RequestIds.java workspace/rose-builder/tests/RequestIdsTest.java
java -cp "$JAVA_TESTS/RequestIds" dev.pixel.builder.RequestIdsTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/BuilderWire" \
 workspace/rose-builder/src/dev/pixel/builder/BuildJob.java workspace/rose-builder/src/dev/pixel/builder/InstallPlan.java \
 workspace/rose-builder/src/dev/pixel/builder/Json.java workspace/rose-builder/src/dev/pixel/builder/BuilderWire.java \
 workspace/rose-builder/src/dev/pixel/builder/BuilderApi.java workspace/rose-builder/tests/BuilderWireTest.java
java -cp "$JAVA_TESTS/BuilderWire" dev.pixel.builder.BuilderWireTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeAppGroups" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeAppGroups.java workspace/rose-agent/app/tests/HomeAppGroupsTest.java
java -cp "$JAVA_TESTS/HomeAppGroups" dev.pixel.voice.HomeAppGroupsTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/HomeRoseLaunch" \
 workspace/rose-agent/app/src/dev/pixel/voice/HomeRoseLaunch.java workspace/rose-agent/app/tests/HomeRoseLaunchTest.java
java -cp "$JAVA_TESTS/HomeRoseLaunch" dev.pixel.voice.HomeRoseLaunchTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseMicInput" \
 workspace/rose-agent/app/src/dev/pixel/voice/RoseMicInput.java workspace/rose-agent/app/tests/RoseMicInputTest.java
java -cp "$JAVA_TESTS/RoseMicInput" dev.pixel.voice.RoseMicInputTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseChin" \
 workspace/rose-agent/app/src/dev/pixel/voice/RoseChin.java workspace/rose-agent/app/tests/RoseChinTest.java
java -cp "$JAVA_TESTS/RoseChin" dev.pixel.voice.RoseChinTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/GestureContract" \
 workspace/rose-agent/app/src/dev/pixel/voice/GestureContract.java workspace/rose-agent/app/tests/GestureContractTest.java
java -cp "$JAVA_TESTS/GestureContract" dev.pixel.voice.GestureContractTest
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
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/WorkspaceOverview" \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverview.java workspace/rose-agent/app/tests/WorkspaceOverviewTest.java
java -cp "$JAVA_TESTS/WorkspaceOverview" dev.pixel.voice.WorkspaceOverviewTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/WorkspaceOverviewFeed" \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverview.java workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverviewFeed.java workspace/rose-agent/app/tests/WorkspaceOverviewFeedTest.java
java -cp "$JAVA_TESTS/WorkspaceOverviewFeed" dev.pixel.voice.WorkspaceOverviewFeedTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/WorkspaceOverviewPreview" \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverview.java workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverviewPreview.java \
 workspace/rose-agent/app/tests/WorkspaceOverviewPreviewTest.java
java -cp "$JAVA_TESTS/WorkspaceOverviewPreview" dev.pixel.voice.WorkspaceOverviewPreviewTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CurrentApps" \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverview.java workspace/rose-agent/app/src/dev/pixel/voice/CurrentApps.java \
 workspace/rose-agent/app/tests/CurrentAppsTest.java
java -cp "$JAVA_TESTS/CurrentApps" dev.pixel.voice.CurrentAppsTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CurrentAppsReply" \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverview.java workspace/rose-agent/app/src/dev/pixel/voice/CurrentApps.java \
 workspace/rose-agent/app/tests/CurrentAppsReplyTest.java
java -cp "$JAVA_TESTS/CurrentAppsReply" dev.pixel.voice.CurrentAppsReplyTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CurrentAppsParity" \
 workspace/rose-agent/app/src/dev/pixel/voice/WorkspaceOverview.java workspace/rose-agent/app/src/dev/pixel/voice/CurrentApps.java \
 workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java workspace/rose-agent/app/tests/CurrentAppsParityTest.java
java -cp "$JAVA_TESTS/CurrentAppsParity" dev.pixel.voice.CurrentAppsParityTest qa/parity/current-apps-parity.json
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RendererRecovery" \
 workspace/rose-agent/app/src/dev/pixel/voice/RendererRecovery.java workspace/rose-agent/app/tests/RendererRecoveryTest.java
java -cp "$JAVA_TESTS/RendererRecovery" dev.pixel.voice.RendererRecoveryTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CallbackOwner" \
 workspace/rose-agent/app/src/dev/pixel/voice/CallbackOwner.java workspace/rose-agent/app/tests/CallbackOwnerTest.java
java -cp "$JAVA_TESTS/CallbackOwner" dev.pixel.voice.CallbackOwnerTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/PhoneCommandGate" \
 workspace/rose-agent/app/src/dev/pixel/voice/PhoneCommandGate.java workspace/rose-agent/app/tests/PhoneCommandGateTest.java
java -cp "$JAVA_TESTS/PhoneCommandGate" dev.pixel.voice.PhoneCommandGateTest
# Project 1 rose-signin-v1 (generated-app sign-in; distinct from the rose-auth-v1 phone<->server signature prefix):
# the pure-Java client core against the shared vectors, and the name guard (the Rust library runs in
# .github/workflows/rose-signin.yml, since cargo needs its locked crates).
bash workspace/rose-signin/java/test.sh
# Plan 1E: the shared theme definition (themes/rose-themes.json), its generated RoseTheme copies, Glacier's assets
# and contrast, and the Java colour map against the approved prototype's own map (golden values).
python3 -I scripts/tests/test_rose_themes.py
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/RoseTheme" \
 workspace/rose-agent/app/src/dev/pixel/voice/OxbloodVariants.java workspace/rose-agent/app/src/dev/pixel/voice/RoseTheme.java \
 workspace/rose-agent/app/tests/RoseThemeTest.java
java -cp "$JAVA_TESTS/RoseTheme" dev.pixel.voice.RoseThemeTest themes/reference/glacier-map-golden.txt
# Nov 7 plan 1O: the shared customization catalog and model, curated Oxblood variants, and the glasses-on hook.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CustomizationConfig" \
 workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java workspace/rose-agent/app/src/dev/pixel/voice/OxbloodVariants.java \
 workspace/rose-agent/app/src/dev/pixel/voice/RoseFonts.java workspace/rose-agent/app/src/dev/pixel/voice/RoseTheme.java workspace/rose-agent/app/src/dev/pixel/voice/CustomizationConfig.java workspace/rose-agent/app/tests/CustomizationConfigTest.java
java -cp "$JAVA_TESTS/CustomizationConfig" dev.pixel.voice.CustomizationConfigTest workspace/rose-agent/app/assets/customization-agent-tools.json
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/OxbloodVariants" \
 workspace/rose-agent/app/src/dev/pixel/voice/OxbloodVariants.java workspace/rose-agent/app/tests/OxbloodVariantsTest.java
java -cp "$JAVA_TESTS/OxbloodVariants" dev.pixel.voice.OxbloodVariantsTest workspace/rose-agent/app/src/dev/pixel/voice/OxbloodField.java
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/GlassesWearHook" \
 workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java workspace/rose-agent/app/src/dev/pixel/voice/OxbloodVariants.java \
 workspace/rose-agent/app/src/dev/pixel/voice/RoseFonts.java workspace/rose-agent/app/src/dev/pixel/voice/RoseTheme.java workspace/rose-agent/app/src/dev/pixel/voice/CustomizationConfig.java workspace/rose-agent/app/src/dev/pixel/voice/GlassesWearHook.java \
 workspace/rose-agent/app/tests/GlassesWearHookTest.java
java -cp "$JAVA_TESTS/GlassesWearHook" dev.pixel.voice.GlassesWearHookTest
# Bluetooth audio adapter: real signal/decision code with synthetic Android callbacks, no phone or launch.
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/BluetoothAudioWear" -sourcepath workspace/rose-agent/app/src \
 workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java workspace/rose-agent/app/src/dev/pixel/voice/OxbloodVariants.java \
 workspace/rose-agent/app/src/dev/pixel/voice/RoseFonts.java workspace/rose-agent/app/src/dev/pixel/voice/CustomizationConfig.java workspace/rose-agent/app/src/dev/pixel/voice/GlassesWearHook.java \
 workspace/rose-agent/app/src/dev/pixel/voice/BluetoothAudioWear.java workspace/rose-agent/app/tests/BluetoothAudioWearHostTest.java \
 workspace/rose-agent/app/tests/bluetooth-audio-wear/stubs/android/content/*.java \
 workspace/rose-agent/app/tests/bluetooth-audio-wear/stubs/android/media/*.java \
 workspace/rose-agent/app/tests/bluetooth-audio-wear/stubs/android/os/*.java \
 workspace/rose-agent/app/tests/bluetooth-audio-wear/stubs/dev/pixel/voice/*.java
java -cp "$JAVA_TESTS/BluetoothAudioWear" dev.pixel.voice.BluetoothAudioWearHostTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/CustomizationOutcome" \
 workspace/rose-agent/app/src/dev/pixel/voice/MiniJson.java workspace/rose-agent/app/src/dev/pixel/voice/HomeAppLayout.java workspace/rose-agent/app/src/dev/pixel/voice/OxbloodVariants.java \
 workspace/rose-agent/app/src/dev/pixel/voice/RoseFonts.java workspace/rose-agent/app/src/dev/pixel/voice/RoseTheme.java workspace/rose-agent/app/src/dev/pixel/voice/CustomizationConfig.java workspace/rose-agent/app/src/dev/pixel/voice/CustomizationOutcome.java \
 workspace/rose-agent/app/tests/CustomizationOutcomeTest.java
java -cp "$JAVA_TESTS/CustomizationOutcome" dev.pixel.voice.CustomizationOutcomeTest
# 1C: the return control's exact-task source over the SystemUI tray link (protocol v1, OS Core's shell side).
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/TrayReturn" \
 workspace/rose-agent/app/src/dev/pixel/voice/ReturnModel.java workspace/rose-agent/app/src/dev/pixel/voice/ReturnSource.java \
 workspace/rose-agent/app/src/dev/pixel/voice/TrayReturn.java workspace/rose-agent/app/tests/TrayReturnTest.java
java -cp "$JAVA_TESTS/TrayReturn" dev.pixel.voice.TrayReturnTest
# 1C: Rose's side and the shell's (when present) speak exactly protocol v1: codes, results, Bundle keys and replyTo.
python3 workspace/rose-agent/app/tests/tray_return_wire_test.py
# Native glasses-button duplicate suppression (media-button handling on the native side).
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/Button" \
 workspace/rose-agent/app/src/dev/pixel/voice/GlassesButtonGate.java workspace/rose-agent/tests/java/dev/pixel/voice/ButtonTest.java
java -cp "$JAVA_TESTS/Button" dev.pixel.voice.ButtonTest
