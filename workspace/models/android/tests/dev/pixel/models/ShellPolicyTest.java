package dev.pixel.models;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * JVM tests for the Rose Models shell policy (no Android runtime). They drive TurnController with
 * the labelled FakeEngine or scripted engines, a manual inference client and a fake platform.
 * Run: java -cp <classes> dev.pixel.models.ShellPolicyTest
 */
public final class ShellPolicyTest {
    private static int passed;

    public static void main(String[] args) {
        twoTurnTextChat();
        openAllowlistedAppThenFinalAnswer();
        allowlistRejectsArbitraryTargets();
        allowlistResolvesOnlyInstalledUnambiguousApps();
        lockedAndPlatformErrorsAreTruthful();
        stopDuringRequestFencesLateReply();
        backgroundParksAndContinueResends();
        backgroundBlocksToolDispatchEvenIfClientIgnoresCancel();
        duplicateRepliesReachEngineOnce();
        limitsCapInferencesAndTools();
        destroyedControllerDispatchesNothing();
        lastMomentCheckRefusesLaunchAfterReentrantStopOrBackground();
        endpointPolicyRequiresHttps();
        allowlistRoundTripAndIds();
        System.out.println("ShellPolicyTest: " + passed + " checks passed");
    }

    // ---- harness ----

    static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError(what);
        }
        passed++;
    }

    static final class View implements TurnView {
        final List<String> transcript = new ArrayList<>();
        final List<LaunchResult> results = new ArrayList<>();
        String status = "";
        boolean canSend, canStop, canContinue;

        public void appendTranscript(String speaker, String text) {
            transcript.add(speaker + ": " + text);
        }

        public void setStatus(String s) {
            status = s;
        }

        public void showActionResult(LaunchResult r) {
            results.add(r);
        }

        public void setControls(boolean send, boolean stop, boolean cont) {
            canSend = send;
            canStop = stop;
            canContinue = cont;
        }
    }

    /** Holds requests until the test answers them. cancel() is recorded, optionally ignored. */
    static final class ManualClient implements InferenceClient {
        final List<Callback> pending = new ArrayList<>();
        final List<String> bodies = new ArrayList<>();
        int cancels;

        public Call send(String body, Callback cb) {
            bodies.add(body);
            pending.add(cb);
            return () -> cancels++;
        }

        void answer(int index, String body) {
            pending.get(index).onResult(body, null);
        }
    }

    static final class Platform implements AllowlistLauncher.Platform {
        final List<AppAllowlist.Launchable> installed = new ArrayList<>();
        final List<String> started = new ArrayList<>();
        boolean locked;
        RuntimeException failure;

        public List<AppAllowlist.Launchable> launcherActivities() {
            return installed;
        }

        public boolean isLocked() {
            return locked;
        }

        public void startExplicit(AppAllowlist.Launchable t) {
            if (failure != null) {
                throw failure;
            }
            started.add(t.packageName + "/" + t.activityName);
        }
    }

    static final AppAllowlist.Launchable CALC =
            new AppAllowlist.Launchable("com.android.calculator2", ".Calculator", "Calculator");

    static Platform platformWithCalculator() {
        Platform p = new Platform();
        p.installed.add(CALC);
        p.installed.add(new AppAllowlist.Launchable("com.example.notes", ".Main", "Notes"));
        return p;
    }

    static AllowlistLauncher calculatorOnly(Platform p) {
        return new AllowlistLauncher(AppAllowlist.fromSelection(Collections.singletonList(CALC)), p);
    }

    /** Scripted engine: a fixed list of effects, recording every input. */
    static final class Scripted implements AgentEngine {
        final List<Effect> script;
        final List<String> inputs = new ArrayList<>();
        int next;

        Scripted(Effect... effects) {
            script = new ArrayList<>(Arrays.asList(effects));
        }

        public String label() {
            return "scripted test engine";
        }

        Effect pop() {
            return next < script.size() ? script.get(next++) : Effect.error("script exhausted");
        }

        public Effect startTurn(String turnId, String text) {
            inputs.add("start " + turnId);
            return pop();
        }

        public Effect onInference(String id, String body, String error) {
            inputs.add("inference " + id);
            return pop();
        }

        public Effect onToolResult(String id, LaunchResult r) {
            inputs.add("tool " + id + " " + r.status.wire);
            return pop();
        }

        public void cancel(String turnId) {
            inputs.add("cancel " + turnId);
        }
    }

    static final java.util.concurrent.Executor DIRECT = Runnable::run;

    // ---- tests ----

    static void twoTurnTextChat() {
        View v = new View();
        ManualClient c = new ManualClient();
        TurnController t = new TurnController(new FakeEngine(), c, calculatorOnly(platformWithCalculator()), v, DIRECT);
        check(v.canSend && !v.canStop, "idle controls");
        t.send("hello there");
        check(t.hasActiveTurn() && v.canStop && !v.canSend, "turn active while waiting");
        c.answer(0, "{}");
        check(!t.hasActiveTurn() && v.canSend, "turn finished");
        t.send("second turn");
        c.answer(1, "{}");
        check(v.transcript.equals(Arrays.asList("you: hello there", "rose: [fake] You said: hello there",
                "you: second turn", "rose: [fake] You said: second turn")), "transcript " + v.transcript);
    }

    static void openAllowlistedAppThenFinalAnswer() {
        View v = new View();
        ManualClient c = new ManualClient();
        Platform p = platformWithCalculator();
        TurnController t = new TurnController(new FakeEngine(), c, calculatorOnly(p), v, DIRECT);
        t.send("please open calculator");
        c.answer(0, "{}");
        check(p.started.equals(Collections.singletonList("com.android.calculator2/.Calculator")), "explicit component");
        check(v.results.size() == 1 && v.results.get(0).status == LaunchResult.Status.LAUNCH_REQUESTED, "result card");
        check(c.bodies.size() == 2, "second inference after tool result");
        c.answer(1, "{}");
        check(v.transcript.get(v.transcript.size() - 1).contains("launch_requested"), "final answer cites result");
        check(!t.hasActiveTurn(), "turn done");
    }

    static void allowlistRejectsArbitraryTargets() {
        Platform p = platformWithCalculator();
        AllowlistLauncher l = calculatorOnly(p);
        for (String bad : new String[] {"notes", "com.android.calculator2", "com.example.notes",
                "intent:#Intent;component=com.x/.Y;end", "calculator;rm -rf", "../calculator", "", null,
                "Calculator", "calculator ", "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvw"}) {
            LaunchResult r = l.launch(bad);
            check(r.status == LaunchResult.Status.NOT_ALLOWED, "rejected " + bad + " -> " + r);
        }
        check(p.started.isEmpty(), "nothing launched for rejected ids");
    }

    static void allowlistResolvesOnlyInstalledUnambiguousApps() {
        Platform p = new Platform(); // calculator not installed
        AllowlistLauncher l = calculatorOnly(p);
        check(l.launch("calculator").status == LaunchResult.Status.NOT_FOUND, "missing app");
        p.installed.add(CALC);
        p.installed.add(new AppAllowlist.Launchable("com.android.calculator2", ".Other", "Calculator 2"));
        LaunchResult ambiguous = l.launch("calculator");
        check(ambiguous.status == LaunchResult.Status.NOT_FOUND && ambiguous.detail.contains("not guessing"),
                "ambiguous is reported, not guessed: " + ambiguous);
        check(p.started.isEmpty(), "no launch when missing or ambiguous");
    }

    static void lockedAndPlatformErrorsAreTruthful() {
        Platform p = platformWithCalculator();
        p.locked = true;
        check(calculatorOnly(p).launch("calculator").status == LaunchResult.Status.LOCKED, "locked");
        p.locked = false;
        p.failure = new SecurityException("blocked");
        LaunchResult r = calculatorOnly(p).launch("calculator");
        check(r.status == LaunchResult.Status.PLATFORM_ERROR && r.detail.equals("SecurityException"), "platform error " + r);
        check(p.started.isEmpty(), "no launch recorded");
    }

    static void stopDuringRequestFencesLateReply() {
        View v = new View();
        ManualClient c = new ManualClient();
        Platform p = platformWithCalculator();
        Scripted e = new Scripted(Effect.needInference("r1", "{}"), Effect.needTool("c1", "calculator"));
        TurnController t = new TurnController(e, c, calculatorOnly(p), v, DIRECT);
        t.send("open calculator");
        t.stop();
        check(c.cancels == 1 && !t.hasActiveTurn(), "stop cancels the request");
        check(v.status.contains("already opened stays open"), "stop message is truthful");
        c.answer(0, "{\"late\":true}");
        check(p.started.isEmpty(), "late reply after Stop cannot launch");
        check(e.inputs.equals(Arrays.asList("start t1", "cancel t1")), "engine never saw the late reply: " + e.inputs);
    }

    static void backgroundParksAndContinueResends() {
        View v = new View();
        ManualClient c = new ManualClient();
        Platform p = platformWithCalculator();
        TurnController t = new TurnController(new FakeEngine(), c, calculatorOnly(p), v, DIRECT);
        t.send("open calculator");
        t.onBackground();
        check(c.cancels == 1 && t.isParked(), "background cancels and parks");
        check(!v.canContinue, "no Continue while still backgrounded");
        t.continueTurn();
        check(c.bodies.size() == 1, "Continue ignored while backgrounded");
        t.onForeground();
        check(v.canContinue && v.canStop && !v.canSend, "Continue offered on return");
        t.continueTurn();
        check(c.bodies.size() == 2, "Continue re-sends the parked request");
        c.answer(0, "{\"stale\":true}");
        check(p.started.isEmpty(), "reply to the cancelled dispatch is ignored");
        c.answer(1, "{}");
        check(p.started.size() == 1, "launch after the re-sent request");
    }

    static void backgroundBlocksToolDispatchEvenIfClientIgnoresCancel() {
        View v = new View();
        ManualClient c = new ManualClient();
        Platform p = platformWithCalculator();
        Scripted e = new Scripted(Effect.needInference("r1", "{}"), Effect.needTool("c1", "calculator"));
        TurnController t = new TurnController(e, c, calculatorOnly(p), v, DIRECT);
        t.send("open calculator");
        t.onBackground();
        c.answer(0, "{}"); // a misbehaving transport delivers anyway
        check(p.started.isEmpty(), "backgrounded app cannot dispatch");
        check(t.isParked(), "turn stays parked");
    }

    static void duplicateRepliesReachEngineOnce() {
        View v = new View();
        ManualClient c = new ManualClient();
        Scripted e = new Scripted(Effect.needInference("r1", "{}"), Effect.done("ok"));
        TurnController t = new TurnController(e, c, calculatorOnly(platformWithCalculator()), v, DIRECT);
        t.send("hi");
        c.answer(0, "{}");
        c.answer(0, "{}");
        check(e.inputs.equals(Arrays.asList("start t1", "inference r1")), "duplicate ignored: " + e.inputs);
    }

    static void limitsCapInferencesAndTools() {
        View v = new View();
        ManualClient c = new ManualClient();
        Scripted three = new Scripted(Effect.needInference("r1", "{}"), Effect.needInference("r2", "{}"),
                Effect.needInference("r3", "{}"));
        TurnController t = new TurnController(three, c, calculatorOnly(platformWithCalculator()), v, DIRECT);
        t.send("loop");
        c.answer(0, "{}");
        c.answer(1, "{}");
        check(c.bodies.size() == 2 && !t.hasActiveTurn(), "third inference refused");
        check(three.inputs.contains("cancel t1"), "engine cancelled at the limit");

        Platform p = platformWithCalculator();
        Scripted twoTools = new Scripted(Effect.needInference("r1", "{}"), Effect.needTool("c1", "calculator"),
                Effect.needTool("c2", "calculator"));
        View v2 = new View();
        ManualClient c2 = new ManualClient();
        TurnController t2 = new TurnController(twoTools, c2, calculatorOnly(p), v2, DIRECT);
        t2.send("open twice");
        c2.answer(0, "{}");
        check(p.started.size() == 1, "only one app action per turn");
        check(v2.transcript.get(v2.transcript.size() - 1).contains("more than 1 app action"), "limit reported");
    }

    static void destroyedControllerDispatchesNothing() {
        View v = new View();
        ManualClient c = new ManualClient();
        Platform p = platformWithCalculator();
        TurnController t = new TurnController(new FakeEngine(), c, calculatorOnly(p), v, DIRECT);
        t.send("open calculator");
        t.onDestroy();
        c.answer(0, "{}");
        t.send("again");
        check(p.started.isEmpty() && c.bodies.size() == 1 && !v.canSend, "destroyed: no dispatch, no new turn");
    }

    /** Engine that runs a side effect (Stop or background) while answering, then asks for a tool. */
    static final class Reentrant implements AgentEngine {
        Runnable during;
        final List<String> results = new ArrayList<>();

        public String label() {
            return "reentrant test engine";
        }

        public Effect startTurn(String turnId, String text) {
            return Effect.needInference("r1", "{}");
        }

        public Effect onInference(String id, String body, String error) {
            during.run();
            return Effect.needTool("c1", "calculator");
        }

        public Effect onToolResult(String id, LaunchResult r) {
            results.add(r.status.wire);
            return Effect.done("after tool");
        }

        public void cancel(String turnId) {
        }
    }

    static void lastMomentCheckRefusesLaunchAfterReentrantStopOrBackground() {
        for (String mode : new String[] {"stop", "background", "destroy"}) {
            Platform p = platformWithCalculator();
            ManualClient c = new ManualClient();
            Reentrant e = new Reentrant();
            final TurnController[] t = new TurnController[1];
            e.during = () -> {
                if (mode.equals("stop")) {
                    t[0].stop();
                } else if (mode.equals("background")) {
                    t[0].onBackground();
                } else {
                    t[0].onDestroy();
                }
            };
            t[0] = new TurnController(e, c, calculatorOnly(p), new View(), DIRECT);
            t[0].send("open calculator");
            c.answer(0, "{}");
            check(p.started.isEmpty(), mode + ": no Intent after the turn stopped being active");
        }
    }

    static void endpointPolicyRequiresHttps() {
        check(EndpointPolicy.check("https://inference.example/v1") == null, "https accepted");
        check(EndpointPolicy.check("http://inference.example/v1") != null, "cleartext refused");
        check(EndpointPolicy.check("http://127.0.0.1:8000/v1") != null, "loopback cleartext refused on phone");
        check(EndpointPolicy.check("https://user:pw@host/v1") != null, "embedded credentials refused");
        check(EndpointPolicy.check("") != null && EndpointPolicy.check(null) != null, "empty refused");
        check(EndpointPolicy.check("https://") != null, "no host refused");
    }

    static void allowlistRoundTripAndIds() {
        AppAllowlist a = AppAllowlist.fromSelection(Arrays.asList(CALC,
                new AppAllowlist.Launchable("com.other.calc", ".Main", "Calculator"),
                new AppAllowlist.Launchable("com.x", ".M", "   "),
                CALC));
        List<String> ids = new ArrayList<>();
        for (AppAllowlist.Entry e : a.entries()) {
            ids.add(e.appId);
        }
        check(ids.equals(Arrays.asList("calculator", "calculator-2", "app")), "ids " + ids);
        AppAllowlist back = AppAllowlist.parse(a.serialize());
        check(back.serialize().equals(a.serialize()), "round trip");
        check(AppAllowlist.parse("BAD ID\tcom.x\tX\n").isEmpty(), "invalid stored ids dropped");
    }
}
