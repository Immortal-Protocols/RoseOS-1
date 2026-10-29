package dev.pixel.models;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executor;

/**
 * The Android host for Core's local-agent-v1 agent. It forwards user and platform events as
 * inputs and carries out the agent's outputs in order. All turn state, limits, fencing, Stop and
 * background semantics belong to the agent; this class holds only what the contract assigns to
 * the host: the HTTP calls, the last check before an Intent leaves the app, and what is shown.
 *
 * Runs on one thread (the UI thread on Android). Network callbacks are re-posted through
 * {@code uiThread} and ignored once the host is destroyed.
 */
public final class LocalAgentHost {
    private final AgentPort agent;
    private final InferenceClient client;
    private final AppLauncher launcher;
    private final TurnView view;
    private final Executor uiThread;
    private final Map<String, InferenceClient.Call> inflight = new HashMap<>();
    private final String turnPrefix = "t" + Long.toString(System.currentTimeMillis(), 36);

    private String currentTurn;
    private boolean parked;
    private boolean foreground = true;
    private boolean destroyed;
    private long turnSeq;
    private int rejectedCount;

    LocalAgentHost(AgentPort agent, InferenceClient client, AppLauncher launcher, TurnView view,
                   Executor uiThread) {
        this.agent = agent;
        this.client = client;
        this.launcher = launcher;
        this.view = view;
        this.uiThread = uiThread;
        refreshControls();
    }

    public boolean hasActiveTurn() {
        return currentTurn != null;
    }

    public boolean isParked() {
        return parked;
    }

    /** Inputs the agent rejected as stale, duplicate or out of state (late replies land here). */
    public int rejectedCount() {
        return rejectedCount;
    }

    public void send(String text) {
        if (destroyed || !foreground || text == null || text.trim().isEmpty()) {
            return;
        }
        if (currentTurn != null && !parked) {
            return;
        }
        String turnId = turnPrefix + "-" + (++turnSeq);
        view.appendTranscript("you", text.trim());
        currentTurn = turnId;
        parked = false;
        step("{\"type\":\"start_turn\",\"turn_id\":" + Json.quote(turnId) + ",\"text\":" + Json.quote(text.trim()) + "}");
    }

    public void stop() {
        if (currentTurn != null) {
            step("{\"type\":\"cancel\",\"turn_id\":" + Json.quote(currentTurn) + "}");
        }
    }

    public void continueTurn() {
        if (currentTurn != null && parked && foreground) {
            step("{\"type\":\"continue\",\"turn_id\":" + Json.quote(currentTurn) + "}");
        }
    }

    public void onBackground() {
        foreground = false;
        if (currentTurn != null) {
            step("{\"type\":\"backgrounded\"}");
        }
        refreshControls();
    }

    public void onForeground() {
        foreground = true;
        refreshControls();
    }

    /** The activity is going away: cancel requests, release the native agent, dispatch nothing. */
    public void onDestroy() {
        destroyed = true;
        for (InferenceClient.Call call : new ArrayList<>(inflight.values())) {
            call.cancel();
        }
        inflight.clear();
        agent.close();
        currentTurn = null;
        parked = false;
        refreshControls();
    }

    private void step(String inputJson) {
        if (destroyed) {
            return;
        }
        apply(agent.step(inputJson));
        refreshControls();
    }

    private void apply(String stepJson) {
        Map<String, Object> result = Json.object(Json.parse(stepJson));
        if (!"local-agent-v1".equals(result.get("contract"))) {
            throw new IllegalStateException("unexpected agent contract " + result.get("contract"));
        }
        @SuppressWarnings("unchecked")
        List<Object> outputs = (List<Object>) result.get("outputs");
        for (Object item : outputs) {
            if (destroyed) {
                return;
            }
            Map<String, Object> out = Json.object(item);
            String type = Json.string(out, "type");
            switch (type) {
                case "need_inference":
                    dispatchInference(Json.string(out, "turn_id"), Json.number(out, "generation"),
                            Json.string(out, "body"), Json.number(out, "timeout_ms"));
                    break;
                case "cancel_inference": {
                    InferenceClient.Call call = inflight.remove(key(Json.string(out, "turn_id"), Json.number(out, "generation")));
                    if (call != null) {
                        call.cancel();
                    }
                    break;
                }
                case "need_tool":
                    dispatchTool(Json.string(out, "turn_id"), Json.number(out, "generation"),
                            Json.string(out, "action_id"), Json.string(out, "app_id"));
                    break;
                case "render":
                    render(Json.string(out, "turn_id"), Json.object(out.get("event")));
                    break;
                case "turn_ended":
                    ended(Json.string(out, "turn_id"), Json.string(out, "outcome"),
                            out.get("error") instanceof String ? (String) out.get("error") : null);
                    break;
                case "rejected":
                default:
                    rejectedCount++;
            }
        }
    }

    private void dispatchInference(final String turnId, final long generation, String body, long timeoutMs) {
        final String key = key(turnId, generation);
        InferenceClient.Call call = client.send(body, timeoutMs, new InferenceClient.Callback() {
            @Override
            public void onResponse(int status, String retryAfter, String responseBody) {
                uiThread.execute(() -> {
                    inflight.remove(key);
                    step("{\"type\":\"inference_response\",\"turn_id\":" + Json.quote(turnId)
                            + ",\"generation\":" + generation + ",\"status\":" + status
                            + (retryAfter == null ? "" : ",\"retry_after\":" + Json.quote(retryAfter))
                            + ",\"body\":" + Json.quote(responseBody == null ? "" : responseBody) + "}");
                });
            }

            @Override
            public void onFailure(String kind) {
                uiThread.execute(() -> {
                    inflight.remove(key);
                    step("{\"type\":\"inference_failed\",\"turn_id\":" + Json.quote(turnId)
                            + ",\"generation\":" + generation + ",\"kind\":" + Json.quote(kind) + "}");
                });
            }
        });
        inflight.put(key, call);
    }

    /**
     * The host's last check before an Intent leaves the app: this turn is still the live one, the
     * activity is in the foreground and not destroyed. Always answers with exactly one tool_result.
     */
    private void dispatchTool(String turnId, long generation, String actionId, String appId) {
        LaunchResult result;
        if (destroyed || !foreground || !turnId.equals(currentTurn)) {
            result = new LaunchResult(appId, LaunchResult.Status.CANCELLED, "turn no longer active");
        } else {
            try {
                result = launcher.launch(appId);
            } catch (RuntimeException e) {
                result = new LaunchResult(appId, LaunchResult.Status.FAILED, e.getClass().getSimpleName());
            }
        }
        view.showActionResult(result);
        step("{\"type\":\"tool_result\",\"turn_id\":" + Json.quote(turnId) + ",\"generation\":" + generation
                + ",\"action_id\":" + Json.quote(actionId) + ",\"status\":" + Json.quote(result.status.wire) + "}");
    }

    private void render(String turnId, Map<String, Object> event) {
        String kind = Json.string(event, "kind");
        switch (kind) {
            case "status": {
                if (!turnId.equals(currentTurn)) {
                    // Left over from a step whose turn has since ended (Stop pressed while its
                    // outputs were being carried out); showing it would contradict "Stopped".
                    break;
                }
                String state = Json.string(event, "state");
                parked = "parked".equals(state);
                if ("thinking".equals(state)) {
                    view.setStatus("Waiting for the model…");
                } else if ("awaiting_app".equals(state)) {
                    view.setStatus("Opening the app…");
                } else if (parked) {
                    view.setStatus("Paused while Rose Models was in the background. Continue or Stop.");
                } else {
                    view.setStatus(state);
                }
                break;
            }
            case "assistant_text":
                view.appendTranscript("rose", Json.string(event, "text"));
                break;
            case "tool_proposed":
                view.setStatus("The model asked to open " + Json.string(event, "label") + ".");
                break;
            case "tool_result":
                if (Boolean.TRUE.equals(event.get("after_stop"))) {
                    String status = Json.string(event, "status");
                    view.setStatus(LaunchResult.Status.LAUNCH_REQUESTED.wire.equals(status)
                            ? "Stopped. The app was requested before Stop and stays open."
                            : "Stopped. No app was opened (" + status + ").");
                }
                break;
            default:
                break;
        }
    }

    private void ended(String turnId, String outcome, String error) {
        if (turnId.equals(currentTurn)) {
            currentTurn = null;
            parked = false;
        }
        switch (outcome) {
            case "completed":
                view.setStatus("Done.");
                break;
            case "cancelled":
                view.setStatus("backgrounded".equals(error)
                        ? "Cancelled because Rose Models left the foreground."
                        : "Stopped. An app that was already opened stays open.");
                break;
            case "superseded":
                view.setStatus("The paused turn was replaced by your new message.");
                break;
            default:
                view.appendTranscript("error", error == null ? outcome : error);
                view.setStatus("Turn ended with an error.");
        }
    }

    private static String key(String turnId, long generation) {
        return turnId + "#" + generation;
    }

    private void refreshControls() {
        boolean active = currentTurn != null;
        view.setControls(!destroyed && foreground && (!active || parked), !destroyed && active,
                !destroyed && active && parked && foreground);
    }
}
