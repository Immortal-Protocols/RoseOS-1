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
    Agent, AgentConfig, AllowedApp, Input, Limits, Outcome, Output, RenderEvent, ToolStatus,
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
