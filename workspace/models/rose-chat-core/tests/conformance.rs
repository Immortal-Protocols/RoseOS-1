//! Envelope conformance harness: fixture verdicts match the Android agent, and the live path is
//! bounded, header-only, redirect-free and leaves no secret in its summary. FIXTURES ONLY.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};

#[allow(dead_code)]
#[path = "../examples/conformance.rs"]
mod conformance;

const SERVING: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../serving/conformance");
const TOKEN: &str = "synthetic-fixture-token-not-a-secret-000000";

fn set() -> conformance::PromptSet {
    conformance::load_prompts(&std::fs::read_to_string(format!("{SERVING}/prompts.json")).unwrap())
        .unwrap()
}

fn replies(name: &str) -> Value {
    let doc: Value = serde_json::from_str(
        &std::fs::read_to_string(format!("{SERVING}/fixtures/{name}")).unwrap(),
    )
    .unwrap();
    doc["replies"].clone()
}

fn verdicts(summary: &Value) -> Vec<(String, String)> {
    summary["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            (
                r["id"].as_str().unwrap().to_string(),
                r["verdict"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

#[test]
fn prompt_set_is_the_fixed_twenty() {
    let s = set();
    let count = |c: &str| s.prompts.iter().filter(|p| p.category == c).count();
    assert_eq!(
        (count("chat"), count("launch"), count("not_allowed")),
        (10, 8, 2)
    );
    assert_eq!(s.model, "muse-glimmer");
    assert_eq!(s.bar.min_parsed, 19);
    let mut ids: Vec<_> = s.prompts.iter().map(|p| p.id.as_str()).collect();
    ids.sort();
    ids.dedup();
    assert_eq!(ids.len(), 20);
}

#[test]
fn conforming_fixtures_pass() {
    let summary = conformance::run_fixture(&set(), &replies("replies-pass.json")).unwrap();
    assert_eq!(summary["parsed"], 20);
    assert_eq!(summary["conforming"], 20);
    assert_eq!(summary["pass"], true);
    assert!(!summary.to_string().contains("fixture reasoning"));
}

#[test]
fn mixed_fixtures_classify_exactly_like_the_agent() {
    let summary = conformance::run_fixture(&set(), &replies("replies-mixed.json")).unwrap();
    let v: std::collections::BTreeMap<_, _> = verdicts(&summary).into_iter().collect();
    let expect = [
        ("chat-01", "failed:malformed_action"),
        ("chat-02", "failed:malformed_action"),
        ("chat-03", "failed:truncated_reply"),
        ("chat-04", "failed:model_identity_mismatch"),
        ("chat-05", "open_app:calculator"),
        ("launch-01", "failed:action_not_allowed"),
        ("launch-02", "final"),
        ("launch-03", "failed:malformed_action"),
        ("not-allowed-01", "failed:action_not_allowed"),
        ("not-allowed-02", "failed:server"),
        ("chat-06", "final"),
        ("launch-04", "open_app:calculator"),
    ];
    for (id, verdict) in expect {
        assert_eq!(v[id], verdict, "{id}");
    }
    // parsed counts accepted envelopes (allowlisted or not): 20 minus 6 hard failures.
    assert_eq!(summary["parsed"], 14);
    assert_eq!(summary["open_app_outside_launch_prompts"], 1);
    assert_eq!(summary["pass"], false);
    assert_eq!(summary["failures"]["malformed_action"], 3);
}

#[test]
fn origin_and_token_inputs_are_closed() {
    for ok in [
        "https://models.a-list.com",
        "https://models.a-list.com:8443",
    ] {
        assert!(conformance::check_origin(ok, false).is_ok(), "{ok}");
    }
    for bad in [
        "http://models.a-list.com",
        "https://models.a-list.com/v1",
        "https://user:pw@x.com",
        "https://x.com?token=1",
        "ftp://x",
        "http://127.0.0.1:9",
        "http://localhost:9",
    ] {
        assert!(conformance::check_origin(bad, false).is_err(), "{bad}");
    }
    assert!(conformance::check_origin("http://127.0.0.1:9", true).is_ok());
    assert!(conformance::check_origin("http://10.0.0.1:9", true).is_err());

    let dir = std::env::temp_dir().join(format!("rose-conf-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("token");
    std::fs::write(&file, TOKEN).unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert!(
        conformance::read_token(file.to_str().unwrap()).is_err(),
        "group-readable"
    );
    std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(
        conformance::read_token(file.to_str().unwrap()).unwrap(),
        TOKEN
    );
    std::fs::write(&file, "short").unwrap();
    assert!(conformance::read_token(file.to_str().unwrap()).is_err());
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Minimal loopback server: answers from the pass fixtures by prompt, records request heads,
/// and redirects the first request to prove redirects are not followed.
fn serve(redirect_first: bool) -> (String, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    let set = set();
    let pass = replies("replies-pass.json");
    std::thread::spawn(move || {
        for (n, stream) in listener.incoming().enumerate() {
            let mut stream = stream.unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut head = String::new();
            let mut length = 0;
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if line == "\r\n" || line.is_empty() {
                    break;
                }
                if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                    length = v.trim().parse().unwrap();
                }
                head.push_str(&line);
            }
            let mut body = vec![0; length];
            reader.read_exact(&mut body).unwrap();
            log.lock().unwrap().push(head.clone());
            let text = String::from_utf8(body).unwrap();
            let response = if redirect_first && n == 0 {
                "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/elsewhere\r\nContent-Length: 0\r\n\r\n".to_string()
            } else {
                let request: Value = serde_json::from_str(&text).unwrap();
                let last = request["messages"].as_array().unwrap().last().unwrap()["content"]
                    .as_str()
                    .unwrap()
                    .to_string();
                let user: Value = serde_json::from_str(&last).unwrap();
                let prompt = set
                    .prompts
                    .iter()
                    .find(|p| user["text"] == p.text.as_str())
                    .unwrap();
                let reply = pass[&prompt.id]["body"].as_str().unwrap().to_string();
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{reply}",
                    reply.len()
                )
            };
            stream.write_all(response.as_bytes()).unwrap();
        }
    });
    (origin, seen)
}

#[test]
fn live_path_against_a_loopback_fixture_server() {
    let (origin, seen) = serve(true);
    let live = conformance::Live {
        origin: origin.clone(),
        token: TOKEN.into(),
        min_interval: std::time::Duration::from_millis(0),
        timeout: std::time::Duration::from_secs(10),
    };
    let summary = conformance::run_live(&set(), &live);
    let heads = seen.lock().unwrap().clone();
    assert_eq!(
        heads.len(),
        20,
        "exactly one request per prompt; the redirect was not followed"
    );
    for head in &heads {
        assert!(
            head.starts_with("POST /v1/chat/completions HTTP/1.1"),
            "{head}"
        );
        assert!(
            head.contains(&format!("authorization: Bearer {TOKEN}"))
                || head.contains(&format!("Authorization: Bearer {TOKEN}"))
        );
    }
    let results = summary["results"].as_array().unwrap();
    assert_eq!(results[0]["http_status"], 302);
    assert_eq!(results[0]["verdict"], "failed:unexpected_status");
    assert_eq!(summary["parsed"], 19);
    assert_eq!(summary["pass"], true);
    let text = summary.to_string();
    assert!(!text.contains(TOKEN), "token must not reach the summary");
    // launch-05 is just "calculator", which verdict labels legitimately contain.
    for p in set().prompts.into_iter().filter(|p| p.text.len() > 12) {
        assert!(
            !text.contains(&p.text),
            "prompt text must not reach the summary"
        );
    }
    assert!(
        !text.contains("Fixture answer"),
        "reply text must not reach the summary"
    );
    assert_eq!(summary["origin"], json!(origin));
}

#[test]
fn cli_refuses_bad_live_inputs_before_any_request() {
    let prompts = format!("{SERVING}/prompts.json");
    let args = |extra: &[&str]| -> Vec<String> {
        let mut v = vec!["live".to_string(), "--prompts".into(), prompts.clone()];
        v.extend(extra.iter().map(|s| s.to_string()));
        v
    };
    for bad in [
        args(&[
            "--origin",
            "http://models.a-list.com",
            "--model",
            "muse-glimmer",
            "--token-file",
            "/nonexistent",
        ]),
        args(&[
            "--origin",
            "https://models.a-list.com",
            "--model",
            "other",
            "--token-file",
            "/nonexistent",
        ]),
        args(&[
            "--origin",
            "https://models.a-list.com",
            "--model",
            "muse-glimmer",
            "--token-file",
            "/nonexistent",
        ]),
        args(&[
            "--origin",
            "https://models.a-list.com",
            "--model",
            "muse-glimmer",
            "--token-file",
            "/x",
            "--timeout-s",
            "600",
        ]),
        args(&[
            "--origin",
            "https://models.a-list.com",
            "--model",
            "muse-glimmer",
            "--bogus",
            "1",
        ]),
    ] {
        assert!(conformance::cli(&bad).is_err(), "{bad:?}");
    }
}
