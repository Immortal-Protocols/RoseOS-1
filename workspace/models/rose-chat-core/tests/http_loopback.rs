//! LOOPBACK FIXTURE tests: the real `HttpTransport` over 127.0.0.1 sockets against
//! `qa/models/fixture_server.py` (QA-owned) and small in-test servers. They prove client
//! behaviour over real sockets only, not any model, serving runtime or endpoint.
//! Requires `python3` and the repository checkout.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use rose_chat_core::config::Config;
use rose_chat_core::{
    CancelToken, ChatError, ChatSession, ChatTransport, HttpTransport, Role, Secret, TimeoutPhase,
};
use serde_json::{Value, json};

const MODEL: &str = "loopback-test-label";
const SENTINEL: &str = "QA-SENTINEL-NOT-A-SECRET";

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// QA's loopback fixture on an ephemeral port; killed on drop.
struct Fixture {
    child: Child,
    base: String,
}

impl Fixture {
    fn start(extra: &[&str]) -> Fixture {
        let script = repo_root().join("qa/models/fixture_server.py");
        assert!(
            script.exists(),
            "QA fixture missing at {}",
            script.display()
        );
        let mut child = Command::new("python3")
            .arg("-I")
            .arg(&script)
            .args(["--hold", "3", "--drip", "0.25"])
            .args(extra)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("python3 runs the QA fixture");
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        let info: Value = serde_json::from_str(&line).expect("fixture prints its base URL");
        Fixture {
            child,
            base: info["base"].as_str().unwrap().to_string(),
        }
    }

    fn scenario(&self, name: &str) -> String {
        format!("{}/s/{name}/v1", self.base)
    }

    fn requests(&self) -> Vec<Value> {
        let agent: ureq::Agent = ureq::Agent::config_builder().proxy(None).build().into();
        let mut resp = agent
            .get(&format!("{}/__qa/requests", self.base))
            .call()
            .unwrap();
        let v: Value = serde_json::from_str(&resp.body_mut().read_to_string().unwrap()).unwrap();
        v["requests"].as_array().cloned().unwrap_or_default()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn config(endpoint: &str, extra: &str) -> Config {
    Config::from_toml(&format!(
        "[endpoint]\n{endpoint}\nprotocol = \"openai-chat-candidate\"\n[model]\nid = \"{MODEL}\"\n\
         [timeouts]\nconnect_ms = 500\nrequest_ms = 2000\n{extra}"
    ))
    .unwrap()
}

fn base_config(base: &str) -> Config {
    config(&format!("base_url = \"{base}\""), "")
}

fn session(cfg: &Config) -> ChatSession<HttpTransport> {
    ChatSession::new(cfg, HttpTransport::new(cfg), None)
}

/// A one-connection-per-response server for responses the QA fixture does not script.
/// Records each raw request head and body.
fn raw_server(responses: Vec<Vec<u8>>) -> (String, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let record = seen.clone();
    thread::spawn(move || {
        for response in responses {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut head = String::new();
            let mut length = 0usize;
            loop {
                let mut line = String::new();
                if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
                    break;
                }
                if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                    length = v.trim().parse().unwrap_or(0);
                }
                head.push_str(&line);
            }
            let mut body = vec![0; length];
            let _ = reader.read_exact(&mut body);
            record
                .lock()
                .unwrap()
                .push(format!("{head}\n{}", String::from_utf8_lossy(&body)));
            let _ = stream.write_all(&response);
        }
    });
    (base, seen)
}

fn http_response(status: &str, headers: &str, body: &str) -> Vec<u8> {
    format!(
        "HTTP/1.1 {status}\r\n{headers}Content-Type: application/json\r\nContent-Length: {}\r\n\
         Connection: close\r\n\r\n{body}",
        body.len()
    )
    .into_bytes()
}

fn ok_body(content: Value, extra_message: Value) -> String {
    let mut message = json!({"role": "assistant", "content": content});
    if let Value::Object(m) = extra_message {
        message.as_object_mut().unwrap().extend(m);
    }
    json!({"model": MODEL, "choices": [{"message": message, "finish_reason": "stop"}]}).to_string()
}

#[test]
fn c01_two_turns_over_http_carry_ordered_history() {
    let fx = Fixture::start(&[]);
    let cfg = base_config(&fx.scenario("ok"));
    let mut s = session(&cfg);
    let r1 = s.send("first synthetic message").unwrap();
    let r2 = s.send("second synthetic message").unwrap();
    assert_eq!(r1.content, "fixture reply 1 (received 1 messages)");
    assert_eq!(r2.content, "fixture reply 2 (received 3 messages)");
    assert_eq!(s.transport().last_status, Some(200));

    let reqs = fx.requests();
    assert_eq!(reqs.len(), 2);
    assert_eq!(reqs[1]["path"], "/s/ok/v1/chat/completions");
    let body = &reqs[1]["body"];
    assert_eq!(body["model"], MODEL);
    assert_eq!(body["stream"], false);
    assert_eq!(
        body["messages"],
        json!([
            {"role": "user", "content": "first synthetic message"},
            {"role": "assistant", "content": r1.content},
            {"role": "user", "content": "second synthetic message"},
        ])
    );
    assert_eq!(reqs[1]["auth"], "absent");
    assert_eq!(s.history().len(), 4);
}

#[test]
fn request_url_is_posted_verbatim_without_suffix() {
    let fx = Fixture::start(&[]);
    let url = format!(
        "{}/s/ok/v1/projects/p/locations/us-central1/endpoints/e:rawPredict",
        fx.base
    );
    let cfg = config(&format!("request_url = \"{url}\""), "");
    session(&cfg).send("hi").unwrap();
    assert_eq!(
        fx.requests()[0]["path"],
        "/s/ok/v1/projects/p/locations/us-central1/endpoints/e:rawPredict"
    );
}

#[test]
fn status_and_body_scenarios_map_to_error_kinds() {
    let fx = Fixture::start(&[]);
    let cases: &[(&str, &str)] = &[
        ("401", "unauthorized"),
        ("403", "forbidden"),
        ("404", "not_found"),
        ("400-context", "server_context_limit"),
        ("429", "rate_limited"),
        ("429-no-retry-after", "rate_limited"),
        ("500", "server"),
        ("502", "server"),
        ("503", "server"),
        ("504", "server"),
        ("html-502", "server"),
        ("non-json", "malformed_response"),
        ("missing-content", "empty_completion"),
        ("null-content", "empty_completion"),
        ("empty-content", "empty_completion"),
        ("empty-choices", "malformed_response"),
        ("wrong-type", "malformed_response"),
        ("truncated", "malformed_response"),
        ("model-mismatch", "model_identity_mismatch"),
    ];
    for (scenario, kind) in cases {
        let cfg = base_config(&fx.scenario(scenario));
        let mut s = session(&cfg);
        let err = s.send("hi").unwrap_err();
        assert_eq!(err.kind(), *kind, "{scenario}: {err}");
        assert!(s.history().is_empty(), "{scenario} changed history");
    }

    let mut s = session(&base_config(&fx.scenario("429")));
    match s.send("hi") {
        Err(ChatError::RateLimited {
            retry_after: Some(d),
        }) => assert!(d > Duration::ZERO),
        other => panic!("429 should carry Retry-After: {other:?}"),
    }
    let mut s = session(&base_config(&fx.scenario("length")));
    assert!(s.send("hi").unwrap().truncated);
}

/// Routes turn N to transport A or B, so one session sees a failure between successes.
struct Switch {
    ok: HttpTransport,
    failing: HttpTransport,
    plan: Vec<bool>,
}

impl ChatTransport for Switch {
    fn send(
        &mut self,
        request: &rose_chat_core::ChatRequest,
        ctx: &rose_chat_core::SendContext<'_>,
    ) -> Result<rose_chat_core::ChatResponse, ChatError> {
        if self.plan.remove(0) {
            self.failing.send(request, ctx)
        } else {
            self.ok.send(request, ctx)
        }
    }
}

#[test]
fn c13_failed_http_turn_rolls_back_and_the_next_turn_recovers() {
    let fx = Fixture::start(&[]);
    let ok_cfg = base_config(&fx.scenario("ok"));
    let transport = Switch {
        ok: HttpTransport::new(&ok_cfg),
        failing: HttpTransport::new(&base_config(&fx.scenario("503"))),
        plan: vec![false, true, false],
    };
    let mut s = ChatSession::new(&ok_cfg, transport, None);
    s.send("u1").unwrap();
    assert_eq!(s.send("u2").unwrap_err().kind(), "server");
    s.send("u3").unwrap();

    let reqs = fx.requests();
    let roles = |i: usize| -> Vec<String> {
        reqs[i]["body"]["messages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| {
                format!(
                    "{}:{}",
                    m["role"].as_str().unwrap(),
                    m["content"].as_str().unwrap()
                )
            })
            .collect()
    };
    assert_eq!(reqs[1]["path"], "/s/503/v1/chat/completions");
    assert_eq!(
        roles(2),
        vec![
            "user:u1".to_string(),
            "assistant:fixture reply 1 (received 1 messages)".into(),
            "user:u3".into()
        ]
    );
    assert_eq!(s.history().len(), 4);
    assert_eq!(s.history()[2].role, Role::User);
}

#[test]
fn bearer_sentinel_is_sent_and_never_echoed() {
    let fx = Fixture::start(&["--require-auth"]);
    let cfg = base_config(&fx.scenario("ok"));

    let err = session(&cfg).send("hi").unwrap_err();
    assert_eq!(err.kind(), "unauthorized");

    let mut s = ChatSession::new(&cfg, HttpTransport::new(&cfg), Some(Secret::new(SENTINEL)));
    s.send("hi").unwrap();
    let reqs = fx.requests();
    assert_eq!(reqs[0]["auth"], "absent");
    assert_eq!(reqs[1]["auth"], "sentinel");

    let wrong = ChatSession::new(
        &cfg,
        HttpTransport::new(&cfg),
        Some(Secret::new("other-value")),
    )
    .send("hi")
    .unwrap_err();
    assert!(!format!("{wrong} {wrong:?}").contains("other-value"));
    assert!(!format!("{:?}", s.history()).contains(SENTINEL));
}

#[test]
fn c03_connection_refused_is_fast_and_leaves_history() {
    let port = TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port();
    let cfg = base_config(&format!("http://127.0.0.1:{port}/v1"));
    let mut s = session(&cfg);
    let started = Instant::now();
    let err = s.send("hi").unwrap_err();
    assert_eq!(err.kind(), "connect", "{err}");
    assert!(started.elapsed() < Duration::from_millis(500));
    assert!(s.history().is_empty());
}

#[test]
fn c04_whole_turn_deadline_covers_no_headers_stall_and_slow_drip() {
    let fx = Fixture::start(&[]);
    for scenario in ["no-headers", "stall-after-headers", "slow-body"] {
        let cfg = config(&format!("base_url = \"{}\"", fx.scenario(scenario)), "");
        let cfg = Config {
            request_timeout: Duration::from_millis(700),
            connect_timeout: Duration::from_millis(300),
            ..cfg
        };
        let mut s = session(&cfg);
        let started = Instant::now();
        let err = s.send("hi").unwrap_err();
        let elapsed = started.elapsed();
        assert_eq!(
            err,
            ChatError::Timeout {
                phase: TimeoutPhase::Response
            },
            "{scenario}"
        );
        assert!(
            elapsed >= Duration::from_millis(690) && elapsed < Duration::from_millis(1100),
            "{scenario} took {elapsed:?}"
        );
        assert!(s.history().is_empty());
    }
}

#[test]
fn cancellation_returns_promptly_while_the_socket_waits() {
    let fx = Fixture::start(&[]);
    let cfg = base_config(&fx.scenario("no-headers"));
    let mut s = session(&cfg);
    let cancel = CancelToken::new();
    let trigger = cancel.clone();
    let canceller = thread::spawn(move || {
        thread::sleep(Duration::from_millis(200));
        trigger.cancel();
        Instant::now()
    });
    let err = s.send_with_cancel("hi", &cancel).unwrap_err();
    let returned = Instant::now();
    let cancelled_at = canceller.join().unwrap();
    assert_eq!(err, ChatError::Cancelled);
    let lag = returned.duration_since(cancelled_at);
    assert!(
        lag < Duration::from_millis(150),
        "returned {lag:?} after cancel"
    );
    assert!(s.history().is_empty());
}

#[test]
fn redirects_are_not_followed() {
    let (base, seen) = raw_server(vec![http_response(
        "302 Found",
        "Location: http://127.0.0.1:1/elsewhere\r\n",
        "",
    )]);
    let cfg = base_config(&format!("{base}/v1"));
    let err = session(&cfg).send("hi").unwrap_err();
    assert_eq!(err, ChatError::UnexpectedStatus { status: 302 });
    assert_eq!(seen.lock().unwrap().len(), 1);
}

#[test]
fn oversized_response_is_rejected() {
    let big = ok_body(json!("x".repeat(5000)), Value::Null);
    let (base, _) = raw_server(vec![http_response("200 OK", "", &big)]);
    let cfg = config(
        &format!("base_url = \"{base}/v1\""),
        "[limits]\nmax_response_bytes = 1024\n",
    );
    let mut s = session(&cfg);
    assert_eq!(
        s.send("hi").unwrap_err(),
        ChatError::ResponseTooLarge { limit: 1024 }
    );
    assert!(s.history().is_empty());
}

#[test]
fn reasoning_over_http_is_not_answer_or_history() {
    let (base, seen) = raw_server(vec![
        http_response(
            "200 OK",
            "",
            &ok_body(json!("final answer"), json!({"reasoning": "private chain"})),
        ),
        http_response("200 OK", "", &ok_body(json!("second"), Value::Null)),
    ]);
    let cfg = base_config(&format!("{base}/v1"));
    let mut s = session(&cfg);
    let reply = s.send("q1").unwrap();
    assert_eq!(reply.content, "final answer");
    assert_eq!(reply.reasoning_chars, Some(13));
    s.send("q2").unwrap();
    let second = seen.lock().unwrap()[1].clone();
    assert!(
        second.starts_with("POST /v1/chat/completions HTTP/1.1"),
        "{second}"
    );
    assert!(second.contains("final answer"));
    assert!(!second.contains("private chain"), "reasoning was replayed");
}

#[test]
fn request_headers_are_json_and_identify_the_client() {
    let (base, seen) = raw_server(vec![http_response(
        "200 OK",
        "",
        &ok_body(json!("a"), Value::Null),
    )]);
    let cfg = base_config(&format!("{base}/v1"));
    session(&cfg).send("hi").unwrap();
    let head = seen.lock().unwrap()[0].to_ascii_lowercase();
    assert!(head.contains("content-type: application/json"), "{head}");
    assert!(head.contains("user-agent: rose-chat/"), "{head}");
    assert!(!head.contains("authorization"), "{head}");
}
