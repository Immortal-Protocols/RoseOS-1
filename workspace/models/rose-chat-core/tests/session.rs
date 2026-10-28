//! FIXTURE tests: session behaviour against the in-process scripted transport.
//! They prove client history/error handling only, not any model or endpoint.

mod common;

use std::time::Duration;

use common::{MODEL, config, config_with};
use rose_chat_core::fixture::{ScriptedTransport, Step};
use rose_chat_core::{
    CancelToken, ChatError, ChatResponse, ChatSession, FinishReason, Message, Role, Secret,
    TimeoutPhase,
};

fn session(steps: impl IntoIterator<Item = Step>) -> ChatSession<ScriptedTransport> {
    ChatSession::new(&config(), ScriptedTransport::new(steps), None)
}

fn msgs(pairs: &[(Role, &str)]) -> Vec<Message> {
    pairs.iter().map(|(r, c)| Message::new(*r, *c)).collect()
}

fn response(content: Option<&str>, finish: FinishReason, served: Option<&str>) -> Step {
    Step::Respond(ChatResponse {
        content: content.map(str::to_string),
        finish_reason: finish,
        served_model: served.map(str::to_string),
        request_id: None,
        usage: None,
    })
}

#[test]
fn c01_second_turn_carries_first_exchange_in_order() {
    let mut s = session([Step::reply("a1"), Step::reply("a2")]);
    assert_eq!(s.send("u1").unwrap().content, "a1");
    assert_eq!(s.send("u2").unwrap().content, "a2");

    let sent = &s.transport().requests;
    assert_eq!(sent[0].messages, msgs(&[(Role::User, "u1")]));
    assert_eq!(
        sent[1].messages,
        msgs(&[
            (Role::User, "u1"),
            (Role::Assistant, "a1"),
            (Role::User, "u2")
        ])
    );
    assert!(sent.iter().all(|r| r.model.as_str() == MODEL));
    assert_eq!(
        s.history(),
        msgs(&[
            (Role::User, "u1"),
            (Role::Assistant, "a1"),
            (Role::User, "u2"),
            (Role::Assistant, "a2")
        ])
        .as_slice()
    );
}

#[test]
fn system_prompt_leads_every_request_and_counts_as_history() {
    let cfg = config_with("[chat]\nsystem_prompt = \"Be brief.\"\n");
    let mut s = ChatSession::new(&cfg, ScriptedTransport::new([Step::reply("a1")]), None);
    s.send("u1").unwrap();
    assert_eq!(
        s.transport().requests[0].messages,
        msgs(&[(Role::System, "Be brief."), (Role::User, "u1")])
    );
    assert_eq!(s.history().len(), 3);
}

#[test]
fn request_carries_configured_limits() {
    let cfg = config_with("[limits]\nmax_tokens = 77\n[chat]\ntemperature = 0.25\n");
    let mut s = ChatSession::new(&cfg, ScriptedTransport::new([Step::reply("a")]), None);
    s.send("u").unwrap();
    let r = &s.transport().requests[0];
    assert_eq!(r.max_tokens, 77);
    assert_eq!(r.temperature, Some(0.25));
}

/// Every failure kind leaves the committed history untouched (C03-C10, C12 at session level).
#[test]
fn every_failure_leaves_history_unchanged() {
    let failures = vec![
        Step::Fail(ChatError::Connect("connection refused".into())),
        Step::Fail(ChatError::Timeout {
            phase: TimeoutPhase::Connect,
        }),
        Step::Fail(ChatError::Timeout {
            phase: TimeoutPhase::Response,
        }),
        Step::Fail(ChatError::Unauthorized { status: 401 }),
        Step::Fail(ChatError::Forbidden { status: 403 }),
        Step::Fail(ChatError::RateLimited {
            retry_after: Some(Duration::from_secs(3)),
        }),
        Step::Fail(ChatError::Server { status: 503 }),
        Step::Fail(ChatError::BadRequest { status: 400 }),
        Step::Fail(ChatError::ServerContextLimit { status: 400 }),
        Step::Fail(ChatError::MalformedResponse("missing choices[0]".into())),
        Step::Fail(ChatError::Cancelled),
        response(None, FinishReason::Stop, None),
        response(Some(""), FinishReason::Stop, None),
        response(Some("  \n"), FinishReason::Stop, None),
        response(Some("hi"), FinishReason::Stop, Some("some-other-model")),
        Step::Wire {
            status: 200,
            retry_after: None,
            body: "not json".into(),
        },
    ];
    for failure in failures {
        let label = format!("{failure:?}");
        let mut s = session([Step::reply("a1"), failure, Step::reply("a2")]);
        s.send("u1").unwrap();
        let before = s.history().to_vec();
        assert!(s.send("u2").is_err(), "{label} should fail");
        assert_eq!(s.history(), before.as_slice(), "{label} changed history");

        // C13: the next turn after a failure works and carries only committed context.
        s.send("u3").unwrap();
        assert_eq!(
            s.transport().requests[2].messages,
            msgs(&[
                (Role::User, "u1"),
                (Role::Assistant, "a1"),
                (Role::User, "u3")
            ]),
            "{label}"
        );
        assert_eq!(s.history().len(), 4);
    }
}

#[test]
fn c10_empty_completion_is_an_error_not_a_blank_turn() {
    let mut s = session([response(Some(""), FinishReason::Stop, None)]);
    assert_eq!(s.send("u1"), Err(ChatError::EmptyCompletion));
    assert!(s.history().is_empty());
}

#[test]
fn c11_length_finish_is_kept_and_marked_truncated() {
    let mut s = session([response(Some("partial"), FinishReason::Length, None)]);
    let reply = s.send("u1").unwrap();
    assert!(reply.truncated);
    assert_eq!(reply.finish_reason, FinishReason::Length);
    assert_eq!(s.history().len(), 2);
}

#[test]
fn c12_reported_model_must_equal_configured_model_exactly() {
    let mut s = session([response(
        Some("hi"),
        FinishReason::Stop,
        Some("Test-Model-Placeholder"),
    )]);
    assert_eq!(
        s.send("u1"),
        Err(ChatError::ModelIdentityMismatch {
            configured: MODEL.into(),
            served: "Test-Model-Placeholder".into()
        })
    );

    let mut ok = session([response(Some("hi"), FinishReason::Stop, Some(MODEL))]);
    assert_eq!(ok.send("u1").unwrap().served_model.as_deref(), Some(MODEL));

    // An endpoint that reports no model is accepted; evidence records it as unreported.
    let mut silent = session([Step::reply("hi")]);
    assert_eq!(silent.send("u1").unwrap().served_model, None);
}

#[test]
fn blank_input_is_rejected_before_the_transport() {
    let mut s = session([]);
    assert!(matches!(s.send("   "), Err(ChatError::InvalidInput(_))));
    assert!(s.transport().requests.is_empty());
}

#[test]
fn client_context_budget_rejects_before_sending() {
    let cfg = config_with("[limits]\ncontext_budget_chars = 10\n");
    let mut s = ChatSession::new(&cfg, ScriptedTransport::new([Step::reply("abcd")]), None);
    s.send("12345").unwrap(); // 5 chars
    // 5 + 4 + 2 = 11 > 10: rejected, nothing sent, history unchanged.
    assert_eq!(
        s.send("xy"),
        Err(ChatError::ContextLimit {
            used_chars: 11,
            budget_chars: 10
        })
    );
    assert_eq!(s.transport().requests.len(), 1);
    assert_eq!(s.history().len(), 2);
}

#[test]
fn budget_counts_characters_not_bytes() {
    let cfg = config_with("[limits]\ncontext_budget_chars = 3\n");
    let mut s = ChatSession::new(&cfg, ScriptedTransport::new([Step::reply("ok")]), None);
    assert!(s.send("ééé").is_ok()); // 3 chars, 6 bytes
}

#[test]
fn cancelled_before_send_makes_no_request() {
    let mut s = session([]);
    let cancel = CancelToken::new();
    cancel.cancel();
    assert_eq!(s.send_with_cancel("u1", &cancel), Err(ChatError::Cancelled));
    assert!(s.transport().requests.is_empty());
}

#[test]
fn cancel_while_in_flight_discards_the_reply() {
    let mut s = session([Step::CancelDuring(Box::new(Step::reply("late")))]);
    let cancel = CancelToken::new();
    assert_eq!(s.send_with_cancel("u1", &cancel), Err(ChatError::Cancelled));
    assert!(s.history().is_empty());
}

#[test]
fn reply_after_deadline_is_discarded_as_response_timeout() {
    let cfg = config_with("[timeouts]\nconnect_ms = 10\nrequest_ms = 30\n");
    let late = Step::Delayed(Duration::from_millis(80), Box::new(Step::reply("late")));
    let mut s = ChatSession::new(&cfg, ScriptedTransport::new([late]), None);
    assert_eq!(
        s.send("u1"),
        Err(ChatError::Timeout {
            phase: TimeoutPhase::Response
        })
    );
    assert!(s.history().is_empty());
}

#[test]
fn session_never_retries_on_its_own() {
    let mut s = session([
        Step::Fail(ChatError::Server { status: 503 }),
        Step::reply("a"),
    ]);
    assert_eq!(s.send("u1"), Err(ChatError::Server { status: 503 }));
    assert_eq!(s.transport().requests.len(), 1);
    assert_eq!(s.transport().remaining(), 1);
}

#[test]
fn credential_reaches_the_transport_only_as_a_context_value() {
    let mut s = ChatSession::new(
        &config(),
        ScriptedTransport::new([Step::reply("a")]),
        Some(Secret::new("tok-SENSITIVE-123")),
    );
    s.send("u1").unwrap();
    assert_eq!(s.transport().auth_present, vec![true]);
}

#[test]
fn reply_reports_latency_and_metadata() {
    let step = Step::Respond(ChatResponse {
        content: Some("a".into()),
        finish_reason: FinishReason::Stop,
        served_model: Some(MODEL.into()),
        request_id: Some("req-1".into()),
        usage: None,
    });
    let mut s = session([step]);
    let reply = s.send("u").unwrap();
    assert_eq!(reply.request_id.as_deref(), Some("req-1"));
    assert!(reply.latency < Duration::from_secs(5));
}
