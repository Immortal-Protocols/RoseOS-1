package dev.pixel.models;

/**
 * The local agent engine boundary (local-agent-v1 inputs). Core owns the real engine and its
 * contract; this app supplies only platform effects. Each call returns the next effect.
 */
public interface AgentEngine {
    /** Human-readable identity shown in the UI, e.g. "FAKE ENGINE (fixture)". */
    String label();

    Effect startTurn(String turnId, String userText);

    /** outcome is the raw response body on success, or null with error set. */
    Effect onInference(String requestId, String body, String error);

    Effect onToolResult(String toolCallId, LaunchResult result);

    void cancel(String turnId);
}
