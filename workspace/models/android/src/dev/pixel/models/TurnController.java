package dev.pixel.models;

import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.Executor;

/**
 * Carries one engine's effects out to the platform for one turn at a time.
 *
 * All methods run on one thread (the UI thread on Android). Network callbacks are re-posted to
 * that thread through {@code uiThread} and fenced by turn id and dispatch sequence, so a late,
 * repeated or stale reply cannot change a newer turn. Per turn: at most two inference requests
 * and one tool action, whatever the engine asks for. Stop, background and destroy all prevent an
 * undispatched app launch; a launch already requested is not undone.
 */
public final class TurnController {
    public static final int MAX_INFERENCES_PER_TURN = 2;
    public static final int MAX_TOOLS_PER_TURN = 1;

    enum State { AWAITING_INFERENCE, PARKED, RUNNING }

    private static final class Turn {
        final String id;
        State state = State.RUNNING;
        final Set<String> issuedRequests = new HashSet<>();
        int toolsDispatched;
        Effect pendingInference;
        long dispatchSeq;
        InferenceClient.Call inflight;

        Turn(String id) {
            this.id = id;
        }
    }

    private final AgentEngine engine;
    private final InferenceClient client;
    private final AppLauncher launcher;
    private final TurnView view;
    private final Executor uiThread;

    private Turn current;
    private long turnSeq;
    private long dispatchSeq;
    private boolean foreground = true;
    private boolean destroyed;

    public TurnController(AgentEngine engine, InferenceClient client, AppLauncher launcher,
                          TurnView view, Executor uiThread) {
        this.engine = engine;
        this.client = client;
        this.launcher = launcher;
        this.view = view;
        this.uiThread = uiThread;
        refreshControls();
    }

    public boolean hasActiveTurn() {
        return current != null;
    }

    public boolean isParked() {
        return current != null && current.state == State.PARKED;
    }

    /** Starts a turn. Ignored while another turn is active or the UI is not in the foreground. */
    public void send(String text) {
        if (destroyed || current != null || !foreground || text == null || text.trim().isEmpty()) {
            return;
        }
        current = new Turn("t" + (++turnSeq));
        view.appendTranscript("you", text.trim());
        handle(current, engine.startTurn(current.id, text.trim()));
    }

    /** Ends the active turn. A launch already requested stays requested. */
    public void stop() {
        Turn turn = current;
        if (turn == null) {
            return;
        }
        cancelInflight(turn);
        current = null;
        engine.cancel(turn.id);
        view.setStatus("Stopped. An app that was already opened stays open.");
        refreshControls();
    }

    /** The activity left the foreground: cancel any request and park the turn. */
    public void onBackground() {
        foreground = false;
        Turn turn = current;
        if (turn != null && turn.state == State.AWAITING_INFERENCE) {
            cancelInflight(turn);
            turn.state = State.PARKED;
            view.setStatus("Paused while Rose Models was in the background. Continue or Stop.");
        }
        refreshControls();
    }

    public void onForeground() {
        foreground = true;
        refreshControls();
    }

    /** The activity is going away for good: cancel everything, dispatch nothing more. */
    public void onDestroy() {
        destroyed = true;
        stop();
    }

    /** Re-sends a parked inference request on explicit user action. */
    public void continueTurn() {
        Turn turn = current;
        if (destroyed || turn == null || turn.state != State.PARKED || !foreground) {
            return;
        }
        dispatchInference(turn, turn.pendingInference);
    }

    private void handle(Turn turn, Effect effect) {
        if (turn != current) {
            return;
        }
        switch (effect.kind) {
            case NEED_INFERENCE:
                if (!turn.issuedRequests.contains(effect.id)
                        && turn.issuedRequests.size() >= MAX_INFERENCES_PER_TURN) {
                    fail(turn, "the engine asked for more than " + MAX_INFERENCES_PER_TURN
                            + " model requests in one turn");
                    return;
                }
                turn.issuedRequests.add(effect.id);
                turn.pendingInference = effect;
                if (!foreground) {
                    turn.state = State.PARKED;
                    view.setStatus("Paused while Rose Models was in the background. Continue or Stop.");
                    refreshControls();
                } else {
                    dispatchInference(turn, effect);
                }
                return;
            case NEED_TOOL:
                if (turn.toolsDispatched >= MAX_TOOLS_PER_TURN) {
                    fail(turn, "the engine asked for more than " + MAX_TOOLS_PER_TURN
                            + " app action in one turn");
                    return;
                }
                turn.toolsDispatched++;
                LaunchResult result = dispatchTool(turn, effect.payload);
                view.showActionResult(result);
                handle(turn, engine.onToolResult(effect.id, result));
                return;
            case RENDER:
            case DONE:
                view.appendTranscript("rose", effect.payload);
                finish(turn, "Done.");
                return;
            case ERROR:
            default:
                view.appendTranscript("error", effect.payload);
                finish(turn, "Turn ended with an error.");
        }
    }

    private void dispatchInference(final Turn turn, final Effect effect) {
        final long seq = ++dispatchSeq;
        turn.dispatchSeq = seq;
        turn.state = State.AWAITING_INFERENCE;
        view.setStatus("Waiting for the model…");
        refreshControls();
        turn.inflight = client.send(effect.payload, (body, error) ->
                uiThread.execute(() -> onInferenceResult(turn, seq, effect.id, body, error)));
    }

    private void onInferenceResult(Turn turn, long seq, String requestId, String body, String error) {
        if (turn != current || turn.state != State.AWAITING_INFERENCE || turn.dispatchSeq != seq) {
            return; // late, repeated, stopped or superseded: never reaches the engine
        }
        turn.inflight = null;
        turn.state = State.RUNNING;
        turn.pendingInference = null;
        handle(turn, engine.onInference(requestId, body, error));
    }

    /** The last check before an Intent leaves the app: same turn, not stopped, foreground. */
    private LaunchResult dispatchTool(Turn turn, String appId) {
        if (destroyed || turn != current || !foreground) {
            return new LaunchResult(appId, LaunchResult.Status.CANCELLED, "turn no longer active");
        }
        try {
            return launcher.launch(appId);
        } catch (RuntimeException e) {
            return new LaunchResult(appId, LaunchResult.Status.PLATFORM_ERROR, e.getClass().getSimpleName());
        }
    }

    private void fail(Turn turn, String message) {
        engine.cancel(turn.id);
        view.appendTranscript("error", message);
        finish(turn, "Turn ended: limit reached.");
    }

    private void finish(Turn turn, String status) {
        cancelInflight(turn);
        if (current == turn) {
            current = null;
        }
        view.setStatus(status);
        refreshControls();
    }

    private void cancelInflight(Turn turn) {
        InferenceClient.Call call = turn.inflight;
        turn.inflight = null;
        if (call != null) {
            call.cancel();
        }
    }

    private void refreshControls() {
        boolean active = current != null;
        view.setControls(!destroyed && !active && foreground, active,
                !destroyed && active && current.state == State.PARKED && foreground);
    }
}
