//! local-agent-v1 against the selectable Qwen serving profile (MODELS-QWEN-SERVE-001,
//! `workspace/models/serving/qwen-fp8.json`). FIXTURE responses shaped like vLLM's OpenAI chat route for Qwen; nothing
//! here is evidence about the served model.
//!
//! What it pins: with the configured model id the profile serves (`rose-qwen`) and the proposed qwen3 reasoning
//! parser (thinking returned apart from `content`), a turn and a tool loop complete and the reasoning is never shown or
//! replayed; the Rose Models app's compiled-in `muse-glimmer` id refuses a Qwen reply (the separate Product
//! dependency); and with the runtime-proven flags only (no reasoning parser) inline thinking makes the reply fail
//! instead of being accepted.

use rose_chat_core::agent::{
    Agent, AgentConfig, AllowedApp, Input, Limits, OPEN_APP_POLICY, Outcome, Output, RenderEvent,
    ToolStatus,
};
use serde_json::{Value, json};

const QWEN: &str = "rose-qwen";
const OPEN: &str = r#"{"rose_action":"local-agent-v1","type":"open_app","app_id":"calculator"}"#;
const DONE: &str =
    r#"{"rose_action":"local-agent-v1","type":"final","text":"Opening was requested."}"#;
const HELLO: &str = r#"{"rose_action":"local-agent-v1","type":"final","text":"Hello"}"#;

fn agent(model: &str) -> Agent {
    Agent::new(AgentConfig {
        contract: "local-agent-v1".into(),
        model: model.into(),
        system_prompt: None,
        allowlist: vec![AllowedApp {
            app_id: "calculator".into(),
            label: "Calculator".into(),
        }],
        limits: Limits::default(),
    })
    .unwrap()
}

/// A Qwen reply as vLLM returns it with `--reasoning-parser qwen3`: the envelope in `content`, thinking apart.
fn qwen_body(served: &str, content: &str, field: &str) -> String {
    json!({"model": served, "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": content, field: "SECRET-QWEN-THINKING about the request"}}]})
    .to_string()
}

fn respond(turn: &str, generation: u64, body: String) -> Input {
    Input::InferenceResponse {
        turn_id: turn.into(),
        generation,
        status: 200,
        retry_after: None,
        body,
    }
}

fn start(turn: &str, text: &str) -> Input {
    Input::StartTurn {
        turn_id: turn.into(),
        text: text.into(),
    }
}

fn need_inference(outputs: &[Output]) -> Option<(u64, Value)> {
    outputs.iter().find_map(|o| match o {
        Output::NeedInference {
            generation, body, ..
        } => Some((*generation, serde_json::from_str(body).unwrap())),
        _ => None,
    })
}

fn need_tool(outputs: &[Output]) -> Option<(u64, String)> {
    outputs.iter().find_map(|o| match o {
        Output::NeedTool {
            generation,
            action_id,
            ..
        } => Some((*generation, action_id.clone())),
        _ => None,
    })
}

fn ended(outputs: &[Output]) -> Option<(Outcome, Option<String>)> {
    outputs.iter().find_map(|o| match o {
        Output::TurnEnded { outcome, error, .. } => Some((*outcome, error.clone())),
        _ => None,
    })
}

fn assistant_text(outputs: &[Output]) -> Option<String> {
    outputs.iter().find_map(|o| match o {
        Output::Render {
            event: RenderEvent::AssistantText { text },
            ..
        } => Some(text.clone()),
        _ => None,
    })
}

#[test]
fn qwen_reply_with_separate_reasoning_completes_and_is_never_replayed() {
    for field in ["reasoning", "reasoning_content"] {
        let mut a = agent(QWEN);
        let (g, req) = need_inference(&a.step(start("t1", "hi"))).unwrap();
        assert_eq!(req["model"], QWEN); // the request names the configured Qwen id
        let out = a.step(respond("t1", g, qwen_body(QWEN, HELLO, field)));
        assert_eq!(assistant_text(&out).as_deref(), Some("Hello"), "{field}");
        assert_eq!(ended(&out), Some((Outcome::Completed, None)), "{field}");
        let (_, next) = need_inference(&a.step(start("t2", "again"))).unwrap();
        assert!(
            !next.to_string().contains("SECRET-QWEN-THINKING"),
            "{field}"
        ); // never shown or replayed
        assert!(
            !format!("{out:?}").contains("SECRET-QWEN-THINKING"),
            "{field}"
        );
    }
}

#[test]
fn qwen_tool_loop_completes_with_reasoning_on_both_inferences() {
    let mut a = agent(QWEN);
    let (g, _) = need_inference(&a.step(start("t1", "Open the calculator"))).unwrap();
    let (g, action) =
        need_tool(&a.step(respond("t1", g, qwen_body(QWEN, OPEN, "reasoning")))).unwrap();
    let (g, req) = need_inference(&a.step(Input::ToolResult {
        turn_id: "t1".into(),
        generation: g,
        action_id: action,
        status: ToolStatus::LaunchRequested,
    }))
    .unwrap();
    assert!(!req.to_string().contains("SECRET-QWEN-THINKING"));
    let out = a.step(respond("t1", g, qwen_body(QWEN, DONE, "reasoning")));
    assert_eq!(ended(&out), Some((Outcome::Completed, None)));
    assert!(a.is_idle());
}

#[test]
fn the_apps_compiled_muse_id_refuses_a_qwen_reply() {
    // dev.pixel.models ModelsService.MODEL is the constant "muse-glimmer": serving Qwen to the app needs that
    // Product change; the server never answers as muse-glimmer.
    let mut a = agent("muse-glimmer");
    let (g, _) = need_inference(&a.step(start("t1", "hi"))).unwrap();
    let out = a.step(respond("t1", g, qwen_body(QWEN, HELLO, "reasoning")));
    assert_eq!(
        ended(&out),
        Some((Outcome::Failed, Some("model_identity_mismatch".into())))
    );
    assert!(a.history().is_empty());
}

#[test]
fn inline_thinking_without_the_reasoning_parser_is_refused_not_accepted() {
    // The runtime-proven flags have no --reasoning-parser: a thinking Qwen reply then carries <think> in content.
    let mut a = agent(QWEN);
    let (g, _) = need_inference(&a.step(start("t1", "hi"))).unwrap();
    let inline = format!("<think>\nSECRET-QWEN-THINKING\n</think>\n\n{HELLO}");
    let body = json!({"model": QWEN, "choices": [{"finish_reason": "stop", "message": {"content": inline}}]});
    let out = a.step(respond("t1", g, body.to_string()));
    let (outcome, error) = ended(&out).unwrap();
    assert_eq!(outcome, Outcome::Failed, "{error:?}");
    assert!(assistant_text(&out).is_none());
    assert!(!format!("{out:?}").contains("SECRET-QWEN-THINKING"));
}

// ------------------------------------------------------------------------------------------------
// MODELS-QWEN-CONTRACT-001: the fixed requests of bootstrap's cloud-qwen-contract-once diagnostic are this client's
// exact request bodies (pinned in rose_cloud/rose_cloud/qwen_contract_requests.json), and its envelope verdicts
// (rose_cloud/contract_session.py) agree with this agent's on a shared fixture.

const REQUESTS: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../rose_cloud/rose_cloud/qwen_contract_requests.json"
);
const VERDICTS: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../rose_cloud/tests/fixtures/qwen_contract_verdicts.json"
);
const CODE_WORD: &str = "AMBERFALCON";
const JOURNAL: &str = "Today I walked along the river, watched the light change on the water and wrote down a few \
ideas for the garden: more herbs near the kitchen door, a bench under the old pear tree and a small bed of tulips for \
spring. In the afternoon I repaired the gate, sorted seeds into labelled envelopes and planned the next week of chores \
so that nothing important gets forgotten.";
const JOURNAL_ENTRIES: usize = 26;
/// The existing 20-prompt envelope conformance set (10 chat, 8 calculator launches, 2 not-allowed apps).
const CONFORMANCE_PROMPTS: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../serving/conformance/prompts.json"
);

fn final_body(text: &str) -> String {
    let envelope =
        json!({"rose_action": "local-agent-v1", "type": "final", "text": text}).to_string();
    json!({"model": QWEN, "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": envelope}}]})
    .to_string()
}

fn body_text(v: &Value) -> String {
    v.to_string()
}

/// Every request the diagnostic sends, built by the real agent: (case, [(body, expect, recall)]).
fn contract_requests() -> Value {
    // chat: one plain turn.
    let mut a = agent(QWEN);
    let (_, chat) = need_inference(&a.step(start("t1", "Hi! Who are you?"))).unwrap();

    // tool: the open_app proposal, then (when the model proposed exactly that) the request after the tool result.
    let mut a = agent(QWEN);
    let (g, tool) = need_inference(&a.step(start("t1", "Open the calculator, please."))).unwrap();
    let open = json!({"model": QWEN, "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": OPEN}}]})
    .to_string();
    let (g, action) = need_tool(&a.step(respond("t1", g, open))).unwrap();
    let (_, after) = need_inference(&a.step(Input::ToolResult {
        turn_id: "t1".into(),
        generation: g,
        action_id: action,
        status: ToolStatus::LaunchRequested,
    }))
    .unwrap();

    // long: a code word, then journal turns past the proven 2048-token context, then a question about the code word.
    let mut a = agent(QWEN);
    let first = format!("Please remember this code word for later: {CODE_WORD}. Just confirm.");
    let (g, _) = need_inference(&a.step(start("t0", &first))).unwrap();
    let out = a.step(respond(
        "t0",
        g,
        final_body("I will remember the code word."),
    ));
    assert_eq!(ended(&out), Some((Outcome::Completed, None)));
    for i in 1..=JOURNAL_ENTRIES {
        let turn = format!("t{i}");
        let (g, _) = need_inference(&a.step(start(
            &turn,
            &format!("Journal entry {i}, just note it: {JOURNAL}"),
        )))
        .unwrap();
        let out = a.step(respond(&turn, g, final_body("Noted.")));
        assert_eq!(ended(&out), Some((Outcome::Completed, None)));
    }
    let (_, long) = need_inference(&a.step(start(
        "tq",
        "What was the code word I asked you to remember at the start? Answer with the word only.",
    )))
    .unwrap();

    // conformance: each prompt as the first turn of a fresh agent, exactly as the conformance harness runs it, but
    // with this profile's client config (rose-qwen, no system prompt) instead of the Muse one in prompts.json
    let set: Value =
        serde_json::from_str(&std::fs::read_to_string(CONFORMANCE_PROMPTS).unwrap()).unwrap();
    let conformance: Vec<Value> = set["prompts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| {
            let mut a = agent(QWEN);
            let (_, body) =
                need_inference(&a.step(start("t1", p["text"].as_str().unwrap()))).unwrap();
            json!({"id": p["id"], "category": p["category"], "expect": p["expect"],
                   "body": body_text(&body)})
        })
        .collect();

    json!({
        "schema": "rose-qwen-contract-requests/1",
        "basis": "generated by rose-chat-core tests/qwen_contract.rs from rose_chat_core::agent::Agent (model rose-qwen, \
    allowlist calculator, no system_prompt prefix, default limits): the exact bodies this client sends. Regenerate with \
    ROSE_WRITE_QWEN_CONTRACT=1 cargo test --test qwen_contract",
        "model": QWEN,
        "allowlist": ["calculator"],
        // The client's own limits (Limits::default(), what the app runs with: it sends no limits): the driver waits
        // inference_timeout_ms per request, exactly as the phone does, and checks replies against max_reply_chars.
        "client": {
            "inference_timeout_ms": Limits::default().inference_timeout_ms,
            "max_tokens": Limits::default().max_tokens,
            "max_reply_chars": Limits::default().max_reply_chars,
        },
        "cases": {
            "chat": [{"body": body_text(&chat), "expect": "final"}],
            "tool": [{"body": body_text(&tool), "expect": "open_app:calculator"},
                     {"body": body_text(&after), "expect": "final"}],
            "long": [{"body": body_text(&long), "expect": "final", "recall": CODE_WORD.to_lowercase()}],
            "conformance": conformance,
        },
        "conformance_bar": set["bar"]
    })
}

#[test]
fn contract_requests_are_this_clients_exact_bodies() {
    let generated = contract_requests();
    let text = serde_json::to_string_pretty(&generated).unwrap() + "\n";
    if std::env::var_os("ROSE_WRITE_QWEN_CONTRACT").is_some() {
        std::fs::write(REQUESTS, &text).unwrap();
    }
    let pinned: Value = serde_json::from_str(&std::fs::read_to_string(REQUESTS).unwrap()).unwrap();
    assert_eq!(
        pinned, generated,
        "regenerate with ROSE_WRITE_QWEN_CONTRACT=1"
    );
    let long: Value =
        serde_json::from_str(generated["cases"]["long"][0]["body"].as_str().unwrap()).unwrap();
    let chars: usize = long["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| m["content"].as_str().unwrap().chars().count())
        .sum();
    // inside the client's own budget (20,000 characters), far past what 2,048 tokens hold
    assert!((12_000..=20_000).contains(&chars), "{chars}");
    // every pinned body carries the client's own max_tokens, and the pinned limits are the client's defaults
    for steps in generated["cases"].as_object().unwrap().values() {
        for step in steps.as_array().unwrap() {
            let body: Value = serde_json::from_str(step["body"].as_str().unwrap()).unwrap();
            assert_eq!(body["max_tokens"], generated["client"]["max_tokens"]);
        }
    }
    let defaults = Limits::default();
    assert_eq!(
        (
            defaults.inference_timeout_ms,
            defaults.max_tokens,
            defaults.max_reply_chars
        ),
        (120_000, 512, 8_000)
    );
    assert_eq!(long["model"], QWEN);
}

fn verdict(body: &str) -> String {
    let mut a = agent(QWEN);
    let (g, _) = need_inference(&a.step(start("t1", "hi"))).unwrap();
    let out = a.step(respond("t1", g, body.to_string()));
    if let Some(app) = outputs_tool_app(&out) {
        return format!("open_app:{app}");
    }
    match ended(&out) {
        Some((Outcome::Completed, None)) => "final".into(),
        Some((Outcome::Failed, Some(kind))) => format!("failed:{kind}"),
        other => panic!("{other:?}"),
    }
}

fn outputs_tool_app(outputs: &[Output]) -> Option<String> {
    outputs.iter().find_map(|o| match o {
        Output::NeedTool { app_id, .. } => Some(app_id.clone()),
        _ => None,
    })
}

#[test]
fn shared_envelope_verdicts_match_this_agent() {
    let cases: Value = serde_json::from_str(&std::fs::read_to_string(VERDICTS).unwrap()).unwrap();
    let cases = cases["cases"].as_array().unwrap();
    assert!(cases.len() >= 20);
    for case in cases {
        assert_eq!(
            verdict(case["body"].as_str().unwrap()),
            case["expect"].as_str().unwrap(),
            "{}",
            case["name"]
        );
    }
}

// MODELS-AGENT-OPENAPP-POLICY-001: every request the client builds states when open_app may be used.
#[test]
fn every_built_request_carries_the_open_app_policy() {
    assert!(
        OPEN_APP_POLICY.contains("only when the user explicitly asks you to open or launch an app")
    );
    let pinned: Value = serde_json::from_str(&std::fs::read_to_string(REQUESTS).unwrap()).unwrap();
    let mut bodies = 0;
    for steps in pinned["cases"].as_object().unwrap().values() {
        for step in steps.as_array().unwrap() {
            let body: Value = serde_json::from_str(step["body"].as_str().unwrap()).unwrap();
            assert_eq!(body["messages"][0]["role"], "system");
            let system = body["messages"][0]["content"].as_str().unwrap();
            assert!(system.contains(OPEN_APP_POLICY), "{system}");
            assert!(system.contains("Allowed app ids: calculator (Calculator). Use open_app only"));
            bodies += 1;
        }
    }
    assert_eq!(bodies, 1 + 2 + 1 + 20); // chat, tool and its follow-up, long, the 20 conformance prompts
    // the golden traces (contracts/local-agent-v1.examples.json) carry it in every request they show
    let traces: Value = serde_json::from_str(
        &std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../contracts/local-agent-v1.examples.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let text = traces.to_string();
    let requests = text.matches("need_inference").count();
    assert!(requests > 0);
    assert_eq!(
        text.matches(
            "Use open_app only when the user explicitly asks you to open or launch an app"
        )
        .count(),
        requests
    );
}

#[test]
fn the_policy_does_not_change_what_the_agent_accepts() {
    // The rule is guidance to the model, not a parser change: the agent still accepts an allowlisted open_app on any
    // turn (as before), so an unrequested proposal is caught by the conformance bar, not hidden by the client.
    let mut a = agent(QWEN);
    let (g, _) = need_inference(&a.step(start("t1", "What is 17 times 23?"))).unwrap();
    let open = json!({"model": QWEN, "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": OPEN}}]})
    .to_string();
    assert!(need_tool(&a.step(respond("t1", g, open))).is_some());
}
