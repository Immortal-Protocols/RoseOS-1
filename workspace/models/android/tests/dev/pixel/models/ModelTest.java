package dev.pixel.models;

import static dev.pixel.models.HostTest.check;

import java.util.Arrays;
import java.util.Map;

/**
 * The fixed model choice (ModelsService.Model) over Core's real agent, mirroring Core's Qwen client contract
 * (`rose-chat-core/tests/qwen_contract.rs`). Replies are FIXTURES shaped like vLLM's chat route, not model output.
 */
final class ModelTest {
    static final String THINKING = "SECRET-QWEN-THINKING about the request";

    private ModelTest() {
    }

    static void all() {
        defaultIsQwenAndMuseIsUnchanged();
        qwenIsExactWithoutTheMusePrompt();
        onlyExactKnownIdsSelectAModel();
        qwenReplyWithSeparateReasoningCompletesAndIsNeverReplayed();
        qwenToolLoopCompletesWithReasoning();
        replyFromTheOtherModelFailsBothWays();
        inlineThinkingIsRefused();
    }

    /** 0.4 demo default: Qwen with no setting. Choosing Muse still sends exactly the 0.2/0.3 Muse config. */
    static void defaultIsQwenAndMuseIsUnchanged() {
        check(ModelsService.DEFAULT == ModelsService.Model.QWEN && ModelsService.Model.fromId(null)
                == ModelsService.Model.QWEN, "Qwen is the default, also with no stored choice");
        check(AgentConfigJson.build(ModelsService.Model.MUSE, HostTest.ALLOW).equals(
                AgentConfigJson.build("muse-glimmer", "Reasoning strength: low", HostTest.ALLOW)),
                "explicit Muse config is the 0.2 Muse config");
        HostTest.Rig r = rig(ModelsService.Model.MUSE);
        r.host.send("hi");
        Map<String, Object> body = Json.object(Json.parse(r.client.bodies.get(0)));
        check("muse-glimmer".equals(Json.string(body, "model"))
                && r.messages(0).get(0).startsWith("system:Reasoning strength: low\n\n"), "Muse request");
    }

    static void qwenIsExactWithoutTheMusePrompt() {
        String config = AgentConfigJson.build(ModelsService.Model.QWEN, HostTest.ALLOW);
        Map<String, Object> c = Json.object(Json.parse(config));
        check("rose-qwen".equals(c.get("model")) && !c.containsKey("system_prompt"), "Qwen config " + config);
        HostTest.Rig r = rig(ModelsService.Model.QWEN);
        r.host.send("hi");
        check("rose-qwen".equals(Json.string(Json.object(Json.parse(r.client.bodies.get(0))), "model"))
                && !r.client.bodies.get(0).contains("Reasoning strength"), "Qwen request names rose-qwen only");
    }

    static void onlyExactKnownIdsSelectAModel() {
        check(ModelsService.Model.values().length == 2, "two fixed models");
        check(ModelsService.Model.fromId("rose-qwen") == ModelsService.Model.QWEN
                && ModelsService.Model.fromId("muse-glimmer") == ModelsService.Model.MUSE, "exact ids select");
        for (String other : new String[] {null, "", "qwen", "Rose-Qwen", " rose-qwen", "rose-qwen ", "rose-qwen\n",
                "QWEN", "rose-qwen2", "gpt-4o", "../rose-qwen", "muse-glimmer,rose-qwen"}) {
            check(ModelsService.Model.fromId(other) == ModelsService.DEFAULT, "not a model id: " + other);
        }
    }

    static void qwenReplyWithSeparateReasoningCompletesAndIsNeverReplayed() {
        for (String field : new String[] {"reasoning", "reasoning_content"}) {
            HostTest.Rig r = rig(ModelsService.Model.QWEN);
            r.host.send("hi");
            reply(r, 0, "rose-qwen", HostTest.finalText("Hello"), field);
            r.host.send("again");
            check(r.view.transcript.equals(Arrays.asList("you: hi", "rose: Hello", "you: again")),
                    field + " transcript " + r.view.transcript);
            check(!r.client.bodies.get(1).contains("SECRET-QWEN-THINKING")
                    && !String.join(" ", r.view.statuses).contains("SECRET-QWEN-THINKING"), field + " never replayed");
        }
    }

    static void qwenToolLoopCompletesWithReasoning() {
        HostTest.Rig r = rig(ModelsService.Model.QWEN);
        r.host.send("Open the calculator");
        reply(r, 0, "rose-qwen", HostTest.openApp("calculator"), "reasoning");
        r.delay.runAll();
        check(r.platform.started.size() == 1 && r.client.bodies.size() == 2
                && !r.client.bodies.get(1).contains("SECRET-QWEN-THINKING"), "launch, then a clean second request");
        reply(r, 1, "rose-qwen", HostTest.finalText("Opening was requested."), "reasoning");
        check(!r.host.hasActiveTurn() && r.view.transcript.contains("rose: Opening was requested."), "tool loop done");
    }

    static void replyFromTheOtherModelFailsBothWays() {
        String[][] cases = {{"MUSE", "rose-qwen"}, {"QWEN", "muse-glimmer"}};
        for (String[] c : cases) {
            HostTest.Rig r = rig(ModelsService.Model.valueOf(c[0]));
            r.host.send("hi");
            reply(r, 0, c[1], HostTest.finalText("Hello"), null);
            check(!r.host.hasActiveTurn() && r.view.transcript.contains("error: model_identity_mismatch")
                    && !r.view.transcript.contains("rose: Hello"), c[0] + " refuses " + c[1] + ": " + r.view.transcript);
        }
    }

    /** The runtime-proven Qwen flags have no reasoning parser: thinking inline in content is refused. */
    static void inlineThinkingIsRefused() {
        HostTest.Rig r = rig(ModelsService.Model.QWEN);
        r.host.send("hi");
        reply(r, 0, "rose-qwen", "<think>\n" + THINKING + "\n</think>\n\n" + HostTest.finalText("Hello"), null);
        String all = String.join(" ", r.view.transcript) + " " + String.join(" ", r.view.statuses);
        check(!r.host.hasActiveTurn() && !r.view.transcript.contains("rose: Hello")
                && r.view.transcript.get(r.view.transcript.size() - 1).startsWith("error: "), "refused: " + all);
        check(!all.contains("SECRET-QWEN-THINKING"), "thinking not shown");
    }

    // ---- harness ----

    static HostTest.Rig rig(ModelsService.Model model) {
        return new HostTest.Rig(new HostTest.View(), AgentConfigJson.build(model, HostTest.ALLOW));
    }

    /** A 200 reply as `served`, with thinking in a separate `reasoningField` when it is not null. */
    static void reply(HostTest.Rig r, int index, String served, String content, String reasoningField) {
        String reasoning = reasoningField == null ? "" : "," + Json.quote(reasoningField) + ":" + Json.quote(THINKING);
        r.client.callbacks.get(index).onResponse(200, null, "{\"id\":\"fixture\",\"model\":" + Json.quote(served)
                + ",\"choices\":[{\"index\":0,\"finish_reason\":\"stop\",\"message\":{\"role\":\"assistant\","
                + "\"content\":" + Json.quote(content) + reasoning + "}}]}");
    }
}
