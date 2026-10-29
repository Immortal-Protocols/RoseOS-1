package dev.pixel.models;

/** One local-agent-v1 engine instance: JSON input in, {"contract","outputs"} JSON out. */
interface AgentPort {
    String step(String inputJson);

    /** Releases the engine. Idempotent; step() after close answers with a rejected output. */
    void close();
}
