package dev.pixel.models;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executor;

/**
 * JVM tests for the Rose Models host. They load Core's real local-agent-v1 agent through the JNI
 * adapter (librose_chat_android built for the test host, not arm64) and drive LocalAgentHost with
 * scripted model replies, a manual HTTP client and a fake platform. They prove host/adapter
 * behaviour over the real agent; they are not Android runtime, device or model evidence.
 *
 * Run: java -Djava.library.path=<dir with librose_chat_android.so> -cp <classes> dev.pixel.models.HostTest
 */
public final class HostTest {
    private static int passed;
    static final String MODEL = "test-model-label";

    public static void main(String[] args) {
        twoTurnChatCarriesHistory();
        openAllowlistedAppThenFinalAnswer();
        modelCannotOpenAppsOutsideAllowlist();
        malformedEnvelopeExecutesNothing();
        stopDuringRequestCancelsAndFencesLateReply();
        backgroundDuringFirstRequestCancelsTurn();
        backgroundAfterLaunchParksThenContinueFinishes();
        backgroundDuringFinalRequestEndsTurnAtLimit();
        stopAfterLaunchSendsReceiptAndSaysRequested();
        lastMomentCheckRefusesLaunchWhenBackgroundedBeforeDispatch();
        repeatedBackgroundContinueNeverExceedsTwoDispatches();
        stopBetweenProposalAndDispatchOpensNothing();
        launcherExceptionIsReportedFailed();
        lockedPhoneIsReportedNotLaunched();
        httpAndTransportFailuresEndTurn();
        destroyReleasesAgentAndIgnoresLateReplies();
        nonBmpTextSurvivesTheBoundary();
        nativeAgentLifecycle();
        allowlistRejectsArbitraryTargets();
        allowlistResolvesOnlyInstalledUnambiguousApps();
        allowlistIdsMatchContract();
        endpointPolicyRequiresHttps();
        jsonRoundTrip();
        AuthTest.all();
        DeviceAuthTest.all();
        ModelTest.all();
        System.out.println("HostTest: " + passed + " checks passed");
    }

    // ---- harness ----

    static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError(what);
        }
        passed++;
    }

    static class View implements TurnView {
        final List<String> transcript = new ArrayList<>();
        final List<LaunchResult> results = new ArrayList<>();
        final List<String> statuses = new ArrayList<>();
        boolean canSend, canStop, canContinue;

        public void appendTranscript(String speaker, String text) {
            transcript.add(speaker + ": " + text);
        }

        public void setStatus(String s) {
            statuses.add(s);
        }

        public void showActionResult(LaunchResult r) {
            results.add(r);
        }

        public void setControls(boolean send, boolean stop, boolean cont) {
            canSend = send;
            canStop = stop;
            canContinue = cont;
        }

        String status() {
            return statuses.isEmpty() ? "" : statuses.get(statuses.size() - 1);
        }
    }

    static final class ManualClient implements InferenceClient {
        final List<String> bodies = new ArrayList<>();
        final List<Callback> callbacks = new ArrayList<>();
        int cancels;
        boolean renewing;
        int resends;

        public Call send(String body, long timeoutMs, Callback cb) {
            bodies.add(body);
            callbacks.add(cb);
            return () -> cancels++;
        }

        public boolean renews() {
            return renewing;
        }

        public Call resend(String body, long timeoutMs, Callback cb) {
            resends++;
            return send(body, timeoutMs, cb);
        }

        void reply(int index, String envelope) {
            callbacks.get(index).onResponse(200, null, completion(envelope));
        }
    }

    static String completion(String content) {
        return "{\"id\":\"fixture\",\"model\":" + Json.quote(MODEL) + ",\"choices\":[{\"index\":0,"
                + "\"finish_reason\":\"stop\",\"message\":{\"role\":\"assistant\",\"content\":"
                + Json.quote(content) + "}}]}";
    }

    static String finalText(String text) {
        return "{\"rose_action\":\"local-agent-v1\",\"type\":\"final\",\"text\":" + Json.quote(text) + "}";
    }

    static String openApp(String appId) {
        return "{\"rose_action\":\"local-agent-v1\",\"type\":\"open_app\",\"app_id\":" + Json.quote(appId) + "}";
    }

    static final class Platform implements AllowlistLauncher.Platform {
        final List<AppAllowlist.Launchable> installed = new ArrayList<>();
        final List<String> started = new ArrayList<>();
        boolean locked;
        RuntimeException startFailure;

        public List<AppAllowlist.Launchable> launcherActivities() {
            return installed;
        }

        public boolean isLocked() {
            return locked;
        }

        public void startExplicit(AppAllowlist.Launchable t) {
            if (startFailure != null) {
                throw startFailure;
            }
            started.add(t.packageName + "/" + t.activityName);
        }
    }

    static final AppAllowlist.Launchable CALC =
            new AppAllowlist.Launchable("com.android.calculator2", ".Calculator", "Calculator");
    static final AppAllowlist ALLOW = AppAllowlist.fromSelection(Collections.singletonList(CALC));
    static final Executor DIRECT = Runnable::run;

    /** Delayed tasks the test runs explicitly (the receipt settle timer). */
    static final class ManualDelay implements LocalAgentHost.Delay {
        final List<Runnable> tasks = new ArrayList<>();
        final List<Long> delays = new ArrayList<>();

        public void after(long millis, Runnable task) {
            delays.add(millis);
            tasks.add(task);
        }

        void runAll() {
            List<Runnable> due = new ArrayList<>(tasks);
            tasks.clear();
            for (Runnable t : due) {
                t.run();
            }
        }
    }

    static Platform platform() {
        Platform p = new Platform();
        p.installed.add(CALC);
        return p;
    }

    static final class Rig {
        final View view;
        final ManualClient client = new ManualClient();
        final Platform platform = platform();
        final NativeAgent agent;
        final ManualDelay delay = new ManualDelay();
        final LocalAgentHost host;

        Rig() {
            this(new View());
        }

        Rig(View view) {
            this(view, AgentConfigJson.build(MODEL, null, ALLOW));
        }

        Rig(View view, String configJson) {
            this.view = view;
            agent = new NativeAgent(configJson);
            host = new LocalAgentHost(agent, client, new AllowlistLauncher(ALLOW, platform), view, DIRECT, delay);
        }

        /** The chat messages (role:content) sent in request n. */
        List<String> messages(int n) {
            Map<String, Object> body = Json.object(Json.parse(client.bodies.get(n)));
            List<String> out = new ArrayList<>();
            for (Object m : (List<?>) body.get("messages")) {
                Map<String, Object> msg = Json.object(m);
                out.add(Json.string(msg, "role") + ":" + Json.string(msg, "content"));
            }
            return out;
        }
    }

    // ---- host over the real agent ----

    static void twoTurnChatCarriesHistory() {
        Rig r = new Rig();
        check(r.view.canSend && !r.view.canStop, "idle controls");
        r.host.send("Remember AMBER-7");
        check(r.host.hasActiveTurn() && r.view.canStop && !r.view.canSend, "turn active");
        r.client.reply(0, finalText("Stored."));
        check(!r.host.hasActiveTurn() && r.view.canSend, "turn completed");
        r.host.send("What was the code?");
        r.client.reply(1, finalText("AMBER-7"));
        check(r.view.transcript.equals(Arrays.asList("you: Remember AMBER-7", "rose: Stored.",
                "you: What was the code?", "rose: AMBER-7")), "transcript " + r.view.transcript);
        List<String> second = r.messages(1);
        check(second.size() == 4 && second.get(0).startsWith("system:"), "system + history in request 2: " + second);
        check(second.get(1).contains("Remember AMBER-7") && second.get(2).startsWith("assistant:")
                && second.get(3).contains("What was the code?"), "ordered history: " + second);
        check(Json.string(Json.object(Json.parse(r.client.bodies.get(1))), "model").equals(MODEL), "exact model id");
    }

    static void openAllowlistedAppThenFinalAnswer() {
        Rig r = new Rig();
        r.host.send("Open the calculator");
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.equals(Collections.singletonList("com.android.calculator2/.Calculator")),
                "explicit component launched: " + r.platform.started);
        check(r.view.results.get(0).status == LaunchResult.Status.LAUNCH_REQUESTED, "result card");
        check(r.client.bodies.size() == 1 && r.delay.delays.equals(Collections.singletonList(1500L)),
                "receipt held for the settle delay: " + r.delay.delays);
        r.delay.runAll(); // still in the foreground after the settle delay
        check(r.client.bodies.size() == 2, "second request after the tool result");
        check(r.client.bodies.get(1).contains("tool_result") && r.client.bodies.get(1).contains("launch_requested"),
                "tool result reported to the model");
        r.client.reply(1, finalText("Opening was requested."));
        check(!r.host.hasActiveTurn() && r.view.status().equals("Done."), "turn done: " + r.view.status());
    }

    static void modelCannotOpenAppsOutsideAllowlist() {
        for (String bad : new String[] {"notes", "com.android.calculator2", "intent:#Intent;end"}) {
            Rig r = new Rig();
            r.host.send("open something");
            r.client.reply(0, openApp(bad));
            check(r.platform.started.isEmpty() && r.view.results.isEmpty(), bad + ": nothing dispatched");
            check(!r.host.hasActiveTurn(), bad + ": turn failed");
            check(r.view.transcript.get(r.view.transcript.size() - 1).startsWith("error:"), bad + ": error shown");
        }
    }

    static void malformedEnvelopeExecutesNothing() {
        for (String content : new String[] {"Sure, opening the calculator!",
                "```json\n" + openApp("calculator") + "\n```", openApp("calculator") + openApp("calculator"),
                "[" + openApp("calculator") + "]"}) {
            Rig r = new Rig();
            r.host.send("open calculator");
            r.client.reply(0, content);
            check(r.platform.started.isEmpty(), "malformed reply launched nothing: " + content);
            check(!r.host.hasActiveTurn(), "turn ended");
        }
    }

    static void stopDuringRequestCancelsAndFencesLateReply() {
        Rig r = new Rig();
        r.host.send("open calculator");
        r.host.stop();
        check(r.client.cancels == 1 && !r.host.hasActiveTurn(), "Stop cancelled the request");
        check(r.view.status().contains("already opened stays open"), "truthful Stop message");
        int before = r.host.rejectedCount();
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.isEmpty(), "late reply after Stop launched nothing");
        check(r.host.rejectedCount() == before + 1, "agent rejected the late reply");
        r.host.send("next");
        check(r.host.hasActiveTurn(), "a new turn can start after Stop");
    }

    static void backgroundDuringFirstRequestCancelsTurn() {
        Rig r = new Rig();
        r.host.send("open calculator");
        r.host.onBackground();
        check(r.client.cancels == 1 && !r.host.hasActiveTurn(), "first request cancelled on background");
        check(r.view.status().contains("left the foreground"), "reason shown: " + r.view.status());
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.isEmpty(), "no launch from a cancelled turn");
    }

    /** Permitted path: the launched app takes the foreground while the launch is out. */
    static void backgroundAfterLaunchParksThenContinueFinishes() {
        Rig r = new Rig();
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.size() == 1 && r.client.bodies.size() == 1, "launched, receipt held");
        r.host.onBackground(); // the opened app takes the foreground
        check(r.host.isParked() && r.client.cancels == 0 && r.client.bodies.size() == 1,
                "parked on the receipt with no request sent");
        r.delay.runAll();
        check(r.client.bodies.size() == 1, "settle timer after background sends nothing");
        check(!r.view.canContinue, "no Continue while backgrounded");
        r.host.continueTurn();
        check(r.client.bodies.size() == 1, "Continue ignored while backgrounded");
        r.host.onForeground();
        check(r.view.canContinue && r.view.canStop, "Continue and Stop offered on return");
        r.host.continueTurn();
        check(r.client.bodies.size() == 2, "Continue: the agent emitted the second and last request");
        check(r.client.bodies.get(1).contains("launch_requested"), "final request carries the receipt");
        r.client.reply(1, finalText("Opening was requested."));
        check(r.view.transcript.get(r.view.transcript.size() - 1).equals("rose: Opening was requested.")
                && r.view.status().equals("Done."), "final answer after Continue: " + r.view.transcript);
        check(r.platform.started.size() == 1 && r.client.bodies.size() == 2, "one launch, two requests");
    }

    /** Exhausted cap: background after the final request was emitted ends the turn. */
    static void backgroundDuringFinalRequestEndsTurnAtLimit() {
        Rig r = new Rig();
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        r.delay.runAll();
        check(r.client.bodies.size() == 2, "final request sent in the foreground");
        int rejected = r.host.rejectedCount();
        r.host.onBackground();
        check(r.client.cancels == 1 && !r.host.hasActiveTurn() && !r.host.isParked(), "cancelled, ended, not parked");
        check(r.view.status().contains("last") && r.view.status().contains("new message"),
                "limit explained: " + r.view.status());
        r.host.onForeground();
        check(!r.view.canContinue && r.view.canSend, "no Continue; Send offered");
        r.host.continueTurn();
        check(r.client.bodies.size() == 2, "Continue sent nothing");
        r.client.reply(1, finalText("late"));
        check(r.host.rejectedCount() == rejected + 1 && !r.view.transcript.contains("rose: late"),
                "late reply to the cancelled request rejected");
        r.host.send("did it open?");
        check(r.client.bodies.size() == 3 && r.client.bodies.get(2).contains("launch_requested"),
                "next turn sees the tool record");
        check(r.platform.started.size() == 1, "still exactly one launch");
    }

    static void lastMomentCheckRefusesLaunchWhenBackgroundedBeforeDispatch() {
        final LocalAgentHost[] holder = new LocalAgentHost[1];
        View v = new View() {
            @Override
            public void setStatus(String s) {
                super.setStatus(s);
                if (s.startsWith("The model asked to open")) {
                    holder[0].onBackground(); // user leaves between proposal and dispatch
                }
            }
        };
        Rig r = new Rig(v);
        holder[0] = r.host;
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.isEmpty(), "no Intent once the activity left the foreground");
        check(!r.view.results.isEmpty() && r.view.results.get(0).status == LaunchResult.Status.CANCELLED,
                "reported cancelled: " + r.view.results);
    }

    /** Negative: no sequence of background/Continue sends a turn past its two requests. */
    static void repeatedBackgroundContinueNeverExceedsTwoDispatches() {
        for (boolean parkFirst : new boolean[] {true, false}) {
            Rig r = new Rig();
            r.host.send("open calculator");
            r.client.reply(0, openApp("calculator"));
            if (!parkFirst) {
                r.delay.runAll();
            }
            for (int i = 0; i < 5; i++) {
                r.host.onBackground();
                r.delay.runAll();
                r.host.onForeground();
                r.host.continueTurn();
            }
            String path = parkFirst ? "park first" : "final request first";
            check(r.client.bodies.size() == 2, path + ": exactly 2 requests dispatched, got " + r.client.bodies.size());
            check(r.platform.started.size() == 1, path + ": one launch");
            check(!r.view.canContinue, path + ": Continue no longer offered");
        }
    }

    /** Stop after the Intent left: the held receipt is still sent and rendered truthfully. */
    static void stopAfterLaunchSendsReceiptAndSaysRequested() {
        Rig r = new Rig();
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        r.host.stop();
        check(!r.host.hasActiveTurn() && r.client.bodies.size() == 1 && r.host.rejectedCount() == 0,
                "turn stopped, receipt accepted, no further request");
        check(r.view.status().equals("Stopped. The app was requested before Stop and stays open."),
                "truthful after-Stop receipt: " + r.view.status());
        r.delay.runAll();
        check(r.client.bodies.size() == 1 && r.host.rejectedCount() == 0, "settle timer sends no second receipt");
    }

    static void stopBetweenProposalAndDispatchOpensNothing() {
        final LocalAgentHost[] holder = new LocalAgentHost[1];
        View v = new View() {
            @Override
            public void setStatus(String s) {
                super.setStatus(s);
                if (s.startsWith("The model asked to open")) {
                    holder[0].stop(); // Stop between proposal and dispatch
                }
            }
        };
        Rig r = new Rig(v);
        holder[0] = r.host;
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.isEmpty(), "no Intent after Stop");
        check(r.view.results.size() == 1 && r.view.results.get(0).status == LaunchResult.Status.CANCELLED,
                "one cancelled receipt: " + r.view.results);
        check(r.host.rejectedCount() == 0, "agent accepted the after-Stop receipt");
        check(!r.host.hasActiveTurn() && r.client.bodies.size() == 1, "turn ended, no further request");
        int stopped = r.view.statuses.indexOf("Stopped. An app that was already opened stays open.");
        check(stopped >= 0 && !r.view.statuses.subList(stopped, r.view.statuses.size()).contains("Opening the app…"),
                "no 'Opening' status after Stop: " + r.view.statuses);
        check(r.view.status().equals("Stopped. No app was opened (cancelled)."), "truthful after-Stop receipt: " + r.view.status());
    }

    static void launcherExceptionIsReportedFailed() {
        Rig r = new Rig();
        r.platform.startFailure = new SecurityException("denied");
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        check(r.view.results.size() == 1 && r.view.results.get(0).status == LaunchResult.Status.FAILED,
                "platform exception shown as failed: " + r.view.results);
        List<String> sent = r.messages(1);
        check(sent.get(sent.size() - 1).contains("\"status\":\"failed\""), "model told failed: " + sent);
    }

    static void lockedPhoneIsReportedNotLaunched() {
        Rig r = new Rig();
        r.platform.locked = true;
        r.host.send("open calculator");
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.isEmpty() && r.view.results.get(0).status == LaunchResult.Status.LOCKED, "locked");
        check(r.client.bodies.get(1).contains("locked"), "locked reported to the model");
    }

    static void httpAndTransportFailuresEndTurn() {
        Rig r = new Rig();
        r.host.send("hi");
        r.client.callbacks.get(0).onResponse(401, null, "{\"error\":\"no\"}");
        check(!r.host.hasActiveTurn() && r.view.transcript.get(r.view.transcript.size() - 1).contains("unauthorized"),
                "401 -> unauthorized: " + r.view.transcript);
        r.host.send("hi again");
        r.client.callbacks.get(1).onFailure("tls");
        check(!r.host.hasActiveTurn() && r.view.transcript.get(r.view.transcript.size() - 1).contains("tls"),
                "tls failure shown: " + r.view.transcript);
    }

    static void destroyReleasesAgentAndIgnoresLateReplies() {
        Rig r = new Rig();
        r.host.send("open calculator");
        r.host.onDestroy();
        check(r.client.cancels == 1 && !r.view.canSend, "destroy cancels and disables");
        r.client.reply(0, openApp("calculator"));
        check(r.platform.started.isEmpty(), "no launch after destroy");
        check(r.agent.step("{\"type\":\"backgrounded\"}").equals(NativeAgent.CLOSED), "native handle released");
    }

    static void nonBmpTextSurvivesTheBoundary() {
        Rig r = new Rig();
        String text = "Hi \uD83C\uDF39 and \u00e9";
        r.host.send(text);
        check(r.client.bodies.get(0).contains("\uD83C\uDF39") || r.client.bodies.get(0).contains("\\ud83c\\udf39"),
                "emoji reached the request");
        r.client.reply(0, finalText("\uD83C\uDF39 ok"));
        check(r.view.transcript.get(1).equals("rose: \uD83C\uDF39 ok"), "emoji back: " + r.view.transcript);
    }

    static void nativeAgentLifecycle() {
        boolean threw = false;
        try {
            new NativeAgent("{\"contract\":\"local-agent-v1\"}");
        } catch (IllegalArgumentException e) {
            threw = true;
        }
        check(threw, "invalid config throws");
        NativeAgent a = new NativeAgent(AgentConfigJson.build(MODEL, null, ALLOW));
        check(a.step("not json").contains("rejected"), "bad input rejected, no crash");
        a.close();
        a.close();
        check(a.step("{}").equals(NativeAgent.CLOSED), "closed agent answers rejected");
        String weird = AgentConfigJson.build(MODEL, null, AppAllowlist.fromSelection(Collections.singletonList(
                new AppAllowlist.Launchable("x.y", ".M", "Tab\there and a very long label that goes on and on past sixty four characters"))));
        new NativeAgent(weird).close();
        passed++;
    }

    // ---- policy units ----

    static void allowlistRejectsArbitraryTargets() {
        Platform p = platform();
        AllowlistLauncher l = new AllowlistLauncher(ALLOW, p);
        for (String bad : new String[] {"notes", "com.android.calculator2", "intent:#Intent;end",
                "calculator;rm", "../calculator", "", null, "Calculator", "calculator "}) {
            check(l.launch(bad).status == LaunchResult.Status.NOT_ALLOWED, "rejected " + bad);
        }
        check(p.started.isEmpty(), "nothing launched");
    }

    static void allowlistResolvesOnlyInstalledUnambiguousApps() {
        Platform p = new Platform();
        AllowlistLauncher l = new AllowlistLauncher(ALLOW, p);
        check(l.launch("calculator").status == LaunchResult.Status.NOT_FOUND, "missing");
        p.installed.add(CALC);
        p.installed.add(new AppAllowlist.Launchable("com.android.calculator2", ".Other", "Calc 2"));
        LaunchResult r = l.launch("calculator");
        check(r.status == LaunchResult.Status.NOT_FOUND && r.detail.contains("not guessing"), "ambiguous " + r);
        check(p.started.isEmpty(), "no guess");
    }

    static void allowlistIdsMatchContract() {
        AppAllowlist a = AppAllowlist.fromSelection(Arrays.asList(CALC,
                new AppAllowlist.Launchable("com.other", ".M", "Calculator"),
                new AppAllowlist.Launchable("com.n", ".M", "9 Notes!"),
                new AppAllowlist.Launchable("com.e", ".M", "   ")));
        List<String> ids = new ArrayList<>();
        for (AppAllowlist.Entry e : a.entries()) {
            ids.add(e.appId);
            check(e.appId.matches("[a-z][a-z0-9_]{0,31}"), "contract id " + e.appId);
        }
        check(ids.equals(Arrays.asList("calculator", "calculator_2", "app_9_notes", "app")), "ids " + ids);
        check(AppAllowlist.parse(a.serialize()).serialize().equals(a.serialize()), "round trip");
        new NativeAgent(AgentConfigJson.build(MODEL, "s", a)).close(); // Core accepts them
        passed++;
    }

    static void endpointPolicyRequiresHttps() {
        check(EndpointPolicy.check("https://inference.example/v1/chat/completions") == null, "https accepted");
        check(EndpointPolicy.check("http://inference.example/v1") != null, "cleartext refused");
        check(EndpointPolicy.check("http://127.0.0.1:8000/v1") != null, "loopback cleartext refused");
        check(EndpointPolicy.check("https://user:pw@host/v1") != null, "credentials refused");
        check(EndpointPolicy.check("") != null && EndpointPolicy.check(null) != null, "empty refused");
    }

    static void jsonRoundTrip() {
        String s = "q\"b\\s\n\t\u0001 \uD83C\uDF39";
        check(Json.parse(Json.quote(s)).equals(s), "string round trip");
        Map<String, Object> o = Json.object(Json.parse("{\"a\":[1,2.5,true,null,{\"b\":\"\\u00e9\"}]}"));
        List<?> a = (List<?>) o.get("a");
        check(a.get(0).equals(1L) && a.get(1).equals(2.5) && a.get(2).equals(Boolean.TRUE) && a.get(3) == null
                && Json.object(a.get(4)).get("b").equals("\u00e9"), "parse " + o);
        boolean threw = false;
        try {
            Json.parse("{\"a\":1} x");
        } catch (IllegalArgumentException e) {
            threw = true;
        }
        check(threw, "trailing data rejected");
    }
}
