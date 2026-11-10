package dev.pixel.rosienotes;

import java.util.Map;

/** Checks the actual existing controller reply, never a model's workspace claim. */
final class WorkspaceBinding {
 static final String TARGET = "dev.pixel.rosienotes", CONTROLLER = "dev.pixel.backgroundcontrol";
 static boolean ready(Map<?, ?> state, String task, String workspace, String controllerTask) {
  if (state == null || !task.equals(controllerTask) || !"ready".equals(state.get("status"))
      || !workspace.equals(state.get("sessionId")) || !controllerTask.equals(state.get("taskId"))
      || !TARGET.equals(state.get("targetPackage")) || !Boolean.FALSE.equals(state.get("blocked"))
      || !Boolean.FALSE.equals(state.get("foregroundFallback"))) return false;
  Object elapsed = state.get("elapsedMs");
  return elapsed instanceof Number && Double.isFinite(((Number) elapsed).doubleValue())
      && ((Number) elapsed).doubleValue() >= 0 && ((Number) elapsed).doubleValue() < 600000;
 }
}
