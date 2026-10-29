package dev.pixel.models;

import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * LOCAL FAKE for UI and policy work only. It is not a model and not the agent engine: Core's
 * local-agent-v1 engine replaces it. It scripts the same effect sequence the real loop uses
 * (inference, optional open_app, inference after the tool result, final text) so the shell,
 * Stop, background and allowlist paths can be exercised without a server.
 */
public final class FakeEngine implements AgentEngine {
    public static final String LABEL = "FAKE ENGINE (local fixture), not a model";
    private static final Pattern OPEN = Pattern.compile("(?i)\\bopen\\s+([a-z0-9_-]+)");

    private static final class TurnState {
        final String text;
        int inferences;
        LaunchResult toolResult;

        TurnState(String text) {
            this.text = text;
        }
    }

    private final Map<String, TurnState> turns = new HashMap<>();

    @Override
    public String label() {
        return LABEL;
    }

    @Override
    public Effect startTurn(String turnId, String userText) {
        turns.put(turnId, new TurnState(userText));
        return Effect.needInference(turnId + "-r1", "{\"fake\":true}");
    }

    @Override
    public Effect onInference(String requestId, String body, String error) {
        String turnId = requestId.substring(0, requestId.lastIndexOf('-'));
        TurnState t = turns.get(turnId);
        if (t == null) {
            return Effect.error("fake engine: unknown turn");
        }
        if (error != null) {
            turns.remove(turnId);
            return Effect.error("model request failed: " + error);
        }
        t.inferences++;
        if (t.toolResult != null) {
            turns.remove(turnId);
            return Effect.done("[fake] " + t.toolResult);
        }
        Matcher m = OPEN.matcher(t.text);
        if (m.find()) {
            return Effect.needTool(turnId + "-c1", m.group(1).toLowerCase());
        }
        turns.remove(turnId);
        return Effect.done("[fake] You said: " + t.text);
    }

    @Override
    public Effect onToolResult(String toolCallId, LaunchResult result) {
        String turnId = toolCallId.substring(0, toolCallId.lastIndexOf('-'));
        TurnState t = turns.get(turnId);
        if (t == null) {
            return Effect.error("fake engine: unknown turn");
        }
        t.toolResult = result;
        return Effect.needInference(turnId + "-r2", "{\"fake\":true,\"tool_result\":\"" + result.status.wire + "\"}");
    }

    @Override
    public void cancel(String turnId) {
        turns.remove(turnId);
    }
}
