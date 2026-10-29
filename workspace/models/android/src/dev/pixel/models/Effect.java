package dev.pixel.models;

/**
 * One output of the local agent engine, mirroring local-agent-v1's NeedInference, NeedTool, Render
 * and terminal outputs. The engine decides; the controller only carries effects out.
 */
public final class Effect {
    public enum Kind { NEED_INFERENCE, NEED_TOOL, RENDER, DONE, ERROR }

    public final Kind kind;
    /** NEED_INFERENCE: engine-issued request id. NEED_TOOL: tool call id. Otherwise null. */
    public final String id;
    /** NEED_INFERENCE: opaque request body built by the engine. NEED_TOOL: app_id. Otherwise text. */
    public final String payload;

    private Effect(Kind kind, String id, String payload) {
        this.kind = kind;
        this.id = id;
        this.payload = payload;
    }

    public static Effect needInference(String requestId, String body) {
        return new Effect(Kind.NEED_INFERENCE, requestId, body);
    }

    public static Effect needTool(String toolCallId, String appId) {
        return new Effect(Kind.NEED_TOOL, toolCallId, appId);
    }

    public static Effect render(String text) {
        return new Effect(Kind.RENDER, null, text);
    }

    public static Effect done(String text) {
        return new Effect(Kind.DONE, null, text);
    }

    public static Effect error(String message) {
        return new Effect(Kind.ERROR, null, message);
    }

    @Override
    public String toString() {
        return kind + (id == null ? "" : "[" + id + "]") + ": " + payload;
    }
}
