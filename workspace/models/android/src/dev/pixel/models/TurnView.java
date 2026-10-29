package dev.pixel.models;

/** What the controller shows. Implemented by the Activity and by tests. */
public interface TurnView {
    void appendTranscript(String speaker, String text);

    void setStatus(String status);

    void showActionResult(LaunchResult result);

    /** Whether Send, Stop and Continue are currently usable. */
    void setControls(boolean canSend, boolean canStop, boolean canContinue);
}
