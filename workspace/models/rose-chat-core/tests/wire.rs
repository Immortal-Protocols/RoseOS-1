//! Unit tests for the CANDIDATE wire shape (pure encode/decode), plus FIXTURE session tests
//! that pass through it. They prove client mapping only, not a real endpoint's behaviour.

mod common;

use std::time::Duration;

use common::{MODEL, config};
use rose_chat_core::fixture::{ScriptedTransport, Step};
use rose_chat_core::wire::{decode_response, encode_request};
use rose_chat_core::{
    ChatError, ChatRequest, ChatSession, FinishReason, Message, ModelId, Role, TimeoutPhase,
};
use serde_json::{Value, json};

fn ok_body(content: Value, finish: Value, model: Value) -> String {
    json!({
        "id": "r1",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    })
    .to_string()
}

#[test]
fn golden_request_json() {
    let request = ChatRequest {
        model: ModelId::new("m-1").unwrap(),
        messages: vec![
            Message::new(Role::System, "s"),
            Message::new(Role::User, "u1"),
            Message::new(Role::Assistant, "a1"),
            Message::new(Role::User, "u2"),
        ],
        max_tokens: 64,
        temperature: None,
    };
    let encoded: Value = serde_json::from_str(&encode_request(&request)).unwrap();
    assert_eq!(
        encoded,
        json!({
            "model": "m-1",
            "messages": [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ],
            "max_tokens": 64,
            "stream": false,
        })
    );

    let with_t = ChatRequest {
        temperature: Some(0.5),
        ..request
    };
    let encoded: Value = serde_json::from_str(&encode_request(&with_t)).unwrap();
    assert_eq!(encoded["temperature"], json!(0.5));
}

#[test]
fn decodes_success_fields() {
    let r = decode_response(
        200,
        None,
        ok_body(json!("hi"), json!("stop"), json!("m")).as_bytes(),
    )
    .unwrap();
    assert_eq!(r.content.as_deref(), Some("hi"));
    assert_eq!(r.finish_reason, FinishReason::Stop);
    assert_eq!(r.served_model.as_deref(), Some("m"));
    assert_eq!(r.request_id.as_deref(), Some("r1"));
    assert_eq!(r.usage.unwrap().completion_tokens, Some(3));

    let r = decode_response(
        200,
        None,
        ok_body(json!("x"), json!("length"), Value::Null).as_bytes(),
    )
    .unwrap();
    assert_eq!(r.finish_reason, FinishReason::Length);
    assert_eq!(r.served_model, None);
    let r = decode_response(
        200,
        None,
        ok_body(json!("x"), json!("tool_calls"), json!("m")).as_bytes(),
    )
    .unwrap();
    assert_eq!(r.finish_reason, FinishReason::Other("tool_calls".into()));
    let r = decode_response(
        200,
        None,
        ok_body(json!(null), Value::Null, json!("m")).as_bytes(),
    )
    .unwrap();
    assert_eq!(
        (r.content, r.finish_reason),
        (None, FinishReason::Unreported)
    );
}

#[test]
fn status_mapping() {
    let cases: Vec<(u16, Option<&str>, &str, ChatError)> = vec![
        (401, None, "", ChatError::Unauthorized { status: 401 }),
        (403, None, "", ChatError::Forbidden { status: 403 }),
        (
            408,
            None,
            "",
            ChatError::Timeout {
                phase: TimeoutPhase::Response,
            },
        ),
        (
            429,
            Some("7"),
            "",
            ChatError::RateLimited {
                retry_after: Some(Duration::from_secs(7)),
            },
        ),
        (
            429,
            Some("Wed, 21 Oct 2026 07:28:00 GMT"),
            "",
            ChatError::RateLimited { retry_after: None },
        ),
        (429, None, "", ChatError::RateLimited { retry_after: None }),
        (
            400,
            None,
            r#"{"error":{"message":"bad role"}}"#,
            ChatError::BadRequest { status: 400 },
        ),
        (
            400,
            None,
            r#"{"error":{"code":"context_length_exceeded"}}"#,
            ChatError::ServerContextLimit { status: 400 },
        ),
        (
            400,
            None,
            r#"{"error":{"message":"This model's maximum context length is 8192 tokens"}}"#,
            ChatError::ServerContextLimit { status: 400 },
        ),
        (
            413,
            None,
            "too large",
            ChatError::BadRequest { status: 413 },
        ),
        (
            422,
            None,
            r#"{"message":"input exceeds the context window"}"#,
            ChatError::ServerContextLimit { status: 422 },
        ),
        (500, None, "", ChatError::Server { status: 500 }),
        (
            502,
            None,
            "<html>bad gateway</html>",
            ChatError::Server { status: 502 },
        ),
        (503, None, "", ChatError::Server { status: 503 }),
        (301, None, "", ChatError::UnexpectedStatus { status: 301 }),
        (404, None, "", ChatError::UnexpectedStatus { status: 404 }),
    ];
    for (status, retry_after, body, expected) in cases {
        assert_eq!(
            decode_response(status, retry_after, body.as_bytes()),
            Err(expected),
            "HTTP {status}"
        );
    }
}

#[test]
fn c09_malformed_bodies_are_errors_not_panics() {
    let bodies = [
        "",
        "not json",
        "{\"choices\": [",
        "[]",
        "{}",
        r#"{"choices": []}"#,
        r#"{"choices": [{}]}"#,
        r#"{"choices": [{"message": "text"}]}"#,
        r#"{"choices": [{"message": {"content": 5}}]}"#,
        r#"{"choices": [{"message": {"content": "x"}, "finish_reason": 1}]}"#,
        r#"{"model": 7, "choices": [{"message": {"content": "x"}}]}"#,
        r#"{"choices": [{"message": {"content": "x"}}], "usage": []}"#,
    ];
    for body in bodies {
        assert!(
            matches!(
                decode_response(200, None, body.as_bytes()),
                Err(ChatError::MalformedResponse(_))
            ),
            "{body:?}"
        );
    }
    assert!(matches!(
        decode_response(200, None, &[0xff, 0xfe, 0x00]),
        Err(ChatError::MalformedResponse(_))
    ));
}

#[test]
fn errors_never_echo_the_body() {
    let leaky = r#"{"error":{"message":"Authorization: Bearer tok-SENSITIVE-123 rejected"}}"#;
    for status in [400, 401, 403, 429, 500] {
        let e = decode_response(status, None, leaky.as_bytes()).unwrap_err();
        assert!(
            !format!("{e} {e:?}").contains("tok-SENSITIVE-123"),
            "HTTP {status}"
        );
    }
    let e = decode_response(200, None, b"tok-SENSITIVE-123").unwrap_err();
    assert!(!format!("{e} {e:?}").contains("tok-SENSITIVE-123"));
}

#[test]
fn two_turn_session_over_the_candidate_wire_shape() {
    let mut s = ChatSession::new(
        &config(),
        ScriptedTransport::new([Step::wire_ok(MODEL, "a1"), Step::wire_ok(MODEL, "a2")]),
        None,
    );
    s.send("u1").unwrap();
    s.send("u2").unwrap();
    let second: Value = serde_json::from_str(&s.transport().wire_bodies[1]).unwrap();
    assert_eq!(second["model"], json!(MODEL));
    assert_eq!(
        second["messages"],
        json!([
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ])
    );
}

#[test]
fn wire_level_failures_reach_the_caller_with_history_intact() {
    let steps = [
        Step::wire_ok(MODEL, "a1"),
        Step::Wire {
            status: 429,
            retry_after: Some("2".into()),
            body: String::new(),
        },
        Step::Wire {
            status: 200,
            retry_after: None,
            body: ok_body(json!(""), json!("stop"), json!(MODEL)),
        },
        Step::wire_ok("substituted-model", "hi"),
        Step::wire_ok(MODEL, "a2"),
    ];
    let mut s = ChatSession::new(&config(), ScriptedTransport::new(steps), None);
    s.send("u1").unwrap();
    assert_eq!(
        s.send("u2"),
        Err(ChatError::RateLimited {
            retry_after: Some(Duration::from_secs(2))
        })
    );
    assert_eq!(s.send("u2"), Err(ChatError::EmptyCompletion));
    assert!(matches!(
        s.send("u2"),
        Err(ChatError::ModelIdentityMismatch { .. })
    ));
    assert_eq!(s.history().len(), 2);
    assert_eq!(s.send("u2").unwrap().content, "a2");
    assert_eq!(s.history().len(), 4);
}

#[test]
fn retryable_classification() {
    for e in [
        ChatError::Connect("x".into()),
        ChatError::Timeout {
            phase: TimeoutPhase::Connect,
        },
        ChatError::RateLimited { retry_after: None },
        ChatError::Server { status: 503 },
    ] {
        assert!(e.is_retryable(), "{e:?}");
    }
    for e in [
        ChatError::Unauthorized { status: 401 },
        ChatError::BadRequest { status: 400 },
        ChatError::ServerContextLimit { status: 400 },
        ChatError::MalformedResponse("x".into()),
        ChatError::EmptyCompletion,
        ChatError::ModelIdentityMismatch {
            configured: "a".into(),
            served: "b".into(),
        },
        ChatError::Cancelled,
    ] {
        assert!(!e.is_retryable(), "{e:?}");
    }
}
