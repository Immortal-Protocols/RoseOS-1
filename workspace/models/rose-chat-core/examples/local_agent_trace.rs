//! Print the `local-agent-v1` example traces as JSON.
//!
//! `cargo run --example local_agent_trace > ../contracts/local-agent-v1.examples.json`
//! regenerates the contract examples; `tests/agent.rs` checks the file still matches this output.
//! The inference bodies are FIXTURE responses, not output from any served model.

use rose_chat_core::agent::Agent;
use serde_json::{Value, json};

fn config() -> Value {
    json!({
        "contract": "local-agent-v1",
        "model": "muse-glimmer",
        "system_prompt": "Reasoning strength: low",
        "allowlist": [{"app_id": "calculator", "label": "Calculator"}]
    })
}

/// A fixture chat-completions response whose assistant content is `content`.
fn reply(content: &str) -> String {
    json!({
        "id": "fixture-1",
        "model": "muse-glimmer",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}]
    })
    .to_string()
}

fn run(name: &str, note: &str, inputs: Vec<Value>) -> Value {
    let mut agent =
        Agent::from_config_json(&config().to_string()).expect("example config is valid");
    let steps: Vec<Value> = inputs
        .into_iter()
        .map(|input| {
            let output: Value = serde_json::from_str(&agent.step_json(&input.to_string())).unwrap();
            json!({"input": input, "output": output})
        })
        .collect();
    json!({"name": name, "note": note, "steps": steps})
}

pub fn traces() -> Value {
    let open = r#"{"rose_action":"local-agent-v1","type":"open_app","app_id":"calculator"}"#;
    let done = r#"{"rose_action":"local-agent-v1","type":"final","text":"I asked the phone to open Calculator."}"#;
    let hello =
        r#"{"rose_action":"local-agent-v1","type":"final","text":"Hello! How can I help?"}"#;
    json!({
        "contract": "local-agent-v1",
        "config": config(),
        "traces": [
            run("chat", "Plain two-message chat turn: one inference, final text.", vec![
                json!({"type": "start_turn", "turn_id": "t1", "text": "Hi"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 1, "status": 200, "body": reply(hello)}),
            ]),
            run("open_app_foreground", "Model proposes open_app; the platform reports launch_requested; a second inference gives the final text.", vec![
                json!({"type": "start_turn", "turn_id": "t1", "text": "Open the calculator"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 1, "status": 200, "body": reply(open)}),
                json!({"type": "tool_result", "turn_id": "t1", "generation": 2, "action_id": "act-1", "status": "launch_requested"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 3, "status": 200, "body": reply(done)}),
            ]),
            run("stop_during_inference", "Stop while the request is in flight; the late reply is rejected and the history is unchanged.", vec![
                json!({"type": "start_turn", "turn_id": "t1", "text": "Open the calculator"}),
                json!({"type": "cancel", "turn_id": "t1"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 1, "status": 200, "body": reply(open)}),
            ]),
            run("background_then_continue", "The launched app backgrounds Rose Models while the launch is out: the receipt parks the turn without a request; Continue sends the second and last request.", vec![
                json!({"type": "start_turn", "turn_id": "t1", "text": "Open the calculator"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 1, "status": 200, "body": reply(open)}),
                json!({"type": "backgrounded"}),
                json!({"type": "tool_result", "turn_id": "t1", "generation": 2, "action_id": "act-1", "status": "launch_requested"}),
                json!({"type": "continue", "turn_id": "t1"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 3, "status": 200, "body": reply(done)}),
            ]),
            run("background_after_budget", "Backgrounded after the post-tool request was sent: that cancelled request still counts, so the turn ends with inference_limit and Continue is rejected. The tool record stays in the history for the next Send.", vec![
                json!({"type": "start_turn", "turn_id": "t1", "text": "Open the calculator"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 1, "status": 200, "body": reply(open)}),
                json!({"type": "tool_result", "turn_id": "t1", "generation": 2, "action_id": "act-1", "status": "launch_requested"}),
                json!({"type": "backgrounded"}),
                json!({"type": "continue", "turn_id": "t1"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 3, "status": 200, "body": reply(done)}),
            ]),
            run("rejected_actions", "A not-allowed app id and a prose reply each fail the turn with zero execution.", vec![
                json!({"type": "start_turn", "turn_id": "t1", "text": "Open settings"}),
                json!({"type": "inference_response", "turn_id": "t1", "generation": 1, "status": 200,
                       "body": reply(r#"{"rose_action":"local-agent-v1","type":"open_app","app_id":"settings"}"#)}),
                json!({"type": "start_turn", "turn_id": "t2", "text": "Hi"}),
                json!({"type": "inference_response", "turn_id": "t2", "generation": 2, "status": 200, "body": reply("Sure, opening it now!")}),
            ]),
        ]
    })
}

fn main() {
    println!("{}", serde_json::to_string_pretty(&traces()).unwrap());
}
