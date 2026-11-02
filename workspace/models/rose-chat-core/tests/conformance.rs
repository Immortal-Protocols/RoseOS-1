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
    let (origin, seen, _) = serve_as(redirect_first, None);
    (origin, seen)
}

/// `serve`, with the replies' served model replaced by `served` when given, also recording each request body.
type Log = Arc<Mutex<Vec<String>>>;
fn serve_as(redirect_first: bool, served: Option<&'static str>) -> (String, Log, Log) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    let bodies = Arc::new(Mutex::new(Vec::new()));
    let body_log = bodies.clone();
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
            body_log.lock().unwrap().push(text.clone());
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
                let mut reply = pass[&prompt.id]["body"].as_str().unwrap().to_string();
                if let Some(model) = served {
                    let mut v: Value = serde_json::from_str(&reply).unwrap();
                    v["model"] = json!(model);
                    reply = v.to_string();
                }
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{reply}",
                    reply.len()
                )
            };
            stream.write_all(response.as_bytes()).unwrap();
        }
    });
    (origin, seen, bodies)
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

/// MODELS-AGENT-OPENAPP-POLICY-001 regression: PR377's no-thinking Qwen answered chat-02 ("What is 17 times 23?")
/// with an unrequested Calculator proposal. Every other reply right: still parsed 20/20, but the bar fails.
#[test]
fn an_unrequested_calculator_on_chat_02_fails_the_bar() {
    let set = set();
    let mut replies = replies("replies-pass.json");
    let open = r#"{"rose_action":"local-agent-v1","type":"open_app","app_id":"calculator"}"#;
    replies["chat-02"]["body"] = json!(
        json!({"model": set.model, "choices": [{"finish_reason": "stop",
               "message": {"role": "assistant", "content": open}}]})
        .to_string()
    );
    let summary = conformance::run_fixture(&set, &replies).unwrap();
    assert_eq!(summary["parsed"], 20);
    assert_eq!(summary["conforming"], 19);
    assert_eq!(summary["wrong_launch_target"], 0);
    assert_eq!(summary["open_app_outside_launch_prompts"], 1);
    assert_eq!(summary["pass"], false);
    assert_eq!(set.prompts[1].id, "chat-02");
    assert_eq!(set.prompts[1].expect, "final"); // the expected category is unchanged
}

// ------------------------------------------------------------------------------------------------
// MODELS-CONFORMANCE-QWEN-PROFILE-001: --profile selects the app's model (served id and system prompt line); the
// prompts, expectations, allowlist and bar stay the prompt set's.

fn profiled(profile: &str) -> conformance::PromptSet {
    let mut s = set();
    conformance::apply_profile(&mut s, profile).unwrap();
    s
}

/// The pass fixtures' replies, answering as `model`.
fn replies_as(model: &str) -> Value {
    let mut r = replies("replies-pass.json");
    for reply in r.as_object_mut().unwrap().values_mut() {
        let mut body: Value = serde_json::from_str(reply["body"].as_str().unwrap()).unwrap();
        body["model"] = json!(model);
        reply["body"] = json!(body.to_string());
    }
    r
}

#[test]
fn profiles_are_the_apps_models() {
    // android ModelsService.Model: MUSE("muse-glimmer", "Muse Glimmer", "Reasoning strength: low"), QWEN(..., null)
    let java = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../android/src/dev/pixel/models/ModelsService.java"
    ))
    .unwrap();
    let entry = |name: &str| -> (String, Option<String>) {
        let line = java
            .lines()
            .map(str::trim)
            .find(|l| l.starts_with(&format!("{name}(")))
            .unwrap();
        let args: Vec<&str> = line[name.len() + 1..line.rfind(')').unwrap()]
            .split(", ")
            .collect();
        let unquote = |a: &str| a.trim_matches('"').to_string();
        (
            unquote(args[0]),
            (args[2] != "null").then(|| unquote(args[2])),
        )
    };
    let expected = [("muse", entry("MUSE")), ("qwen", entry("QWEN"))];
    assert_eq!(conformance::PROFILES.len(), expected.len());
    for ((name, model, system), (want_name, (want_model, want_system))) in
        conformance::PROFILES.iter().zip(expected.iter())
    {
        assert_eq!(name, want_name);
        assert_eq!(*model, want_model.as_str());
        assert_eq!(system.map(str::to_string), *want_system);
    }
}

#[test]
fn muse_profile_is_the_default_set_and_qwen_changes_only_identity() {
    let (default, muse, qwen) = (set(), profiled("muse"), profiled("qwen"));
    assert_eq!(
        (&muse.model, &muse.system_prompt),
        (&default.model, &default.system_prompt)
    );
    assert_eq!(
        (qwen.model.as_str(), qwen.system_prompt.as_deref()),
        ("rose-qwen", None)
    );
    for s in [&muse, &qwen] {
        assert_eq!(s.contract, default.contract);
        assert_eq!(s.allowlist, default.allowlist);
        assert_eq!(s.bar.min_parsed, default.bar.min_parsed);
        assert_eq!(s.prompts.len(), 20);
        for (p, d) in s.prompts.iter().zip(&default.prompts) {
            assert_eq!(
                (&p.id, &p.category, &p.text, &p.expect),
                (&d.id, &d.category, &d.text, &d.expect)
            );
        }
    }
    // the default (Muse) requests are unchanged by the option's existence
    for (p, d) in muse.prompts.iter().zip(&default.prompts) {
        assert_eq!(
            conformance::first_request(&muse, p).1,
            conformance::first_request(&default, d).1
        );
    }
}

#[test]
fn unknown_profiles_are_refused() {
    let mut s = set();
    for bad in ["llama", "Qwen", "", "rose-qwen", "muse-glimmer"] {
        let err = conformance::apply_profile(&mut s, bad).err().unwrap();
        assert!(err.contains("unknown --profile"), "{err}");
    }
    assert_eq!(s.model, "muse-glimmer"); // a refused profile changes nothing
    let args = |p: &str| -> Vec<String> {
        [
            "fixture",
            "--prompts",
            &format!("{SERVING}/prompts.json"),
            "--replies",
            &format!("{SERVING}/fixtures/replies-pass.json"),
            "--profile",
            p,
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    };
    assert!(
        conformance::cli(&args("llama"))
            .err()
            .unwrap()
            .contains("unknown --profile")
    );
    assert_eq!(conformance::cli(&args("muse")).unwrap()["pass"], true);
}

#[test]
fn the_old_muse_qwen_mismatch_fails() {
    // Before --profile, a live Qwen run could not be expressed: --model must equal the set's model (muse-glimmer)
    let live = |extra: &[&str]| -> Result<Value, String> {
        let mut a: Vec<String> = [
            "live",
            "--prompts",
            &format!("{SERVING}/prompts.json"),
            "--origin",
            "http://127.0.0.1:9",
            "--allow-loopback-http",
            "--model",
            "rose-qwen",
            "--token-file",
            "/nonexistent",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        a.extend(extra.iter().map(|s| s.to_string()));
        conformance::cli(&a)
    };
    assert!(live(&[]).err().unwrap().contains("--model must equal"));
    assert!(
        live(&["--profile", "muse"])
            .err()
            .unwrap()
            .contains("--model must equal")
    );
    // with --profile qwen the model check passes and the run stops at the (absent) token file, before any request
    assert!(
        live(&["--profile", "qwen"])
            .err()
            .unwrap()
            .starts_with("token file")
    );
    // and Qwen's replies judged as Muse (the old harness) fail every prompt on identity
    let summary = conformance::run_fixture(&set(), &replies_as("rose-qwen")).unwrap();
    assert_eq!(summary["parsed"], 0);
    assert_eq!(summary["failures"]["model_identity_mismatch"], 20);
    assert_eq!(summary["pass"], false);
}

#[test]
fn qwen_requests_are_the_clients_and_identity_is_checked() {
    let qwen = profiled("qwen");
    // exactly the bodies the client builds for rose-qwen (pinned for the diagnostic by tests/qwen_contract.rs)
    let pinned: Value = serde_json::from_str(
        &std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../rose_cloud/rose_cloud/qwen_contract_requests.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let pinned = pinned["cases"]["conformance"].as_array().unwrap();
    assert_eq!(pinned.len(), 20);
    for (p, want) in qwen.prompts.iter().zip(pinned) {
        assert_eq!(p.id, want["id"].as_str().unwrap());
        let body = conformance::first_request(&qwen, p).1;
        assert_eq!(body, want["body"].as_str().unwrap(), "{}", p.id);
        let v: Value = serde_json::from_str(&body).unwrap();
        assert_eq!(v["model"], "rose-qwen");
        let system = v["messages"][0]["content"].as_str().unwrap();
        assert!(!system.contains("Reasoning strength"));
        assert!(system.contains(rose_chat_core::agent::OPEN_APP_POLICY));
    }
    // replies answering as rose-qwen pass the unchanged bar; replies answering as Muse fail on identity
    let ok = conformance::run_fixture(&qwen, &replies_as("rose-qwen")).unwrap();
    assert_eq!(
        (&ok["model"], &ok["parsed"], &ok["conforming"], &ok["pass"]),
        (&json!("rose-qwen"), &json!(20), &json!(20), &json!(true))
    );
    let muse = conformance::run_fixture(&qwen, &replies_as("muse-glimmer")).unwrap();
    assert_eq!(muse["failures"]["model_identity_mismatch"], 20);
    assert_eq!(muse["pass"], false);
    // the mixed fixture keeps its known failure classes under the Qwen profile too (only identity is swapped)
    let mut mixed = replies("replies-mixed.json");
    for reply in mixed.as_object_mut().unwrap().values_mut() {
        if let Ok(mut body) = serde_json::from_str::<Value>(reply["body"].as_str().unwrap())
            && body["model"] == "muse-glimmer"
        {
            body["model"] = json!("rose-qwen");
            reply["body"] = json!(body.to_string());
        }
    }
    let as_qwen = conformance::run_fixture(&qwen, &mixed).unwrap();
    let as_muse = conformance::run_fixture(&set(), &replies("replies-mixed.json")).unwrap();
    for k in [
        "parsed",
        "conforming",
        "wrong_launch_target",
        "open_app_outside_launch_prompts",
        "failures",
        "pass",
    ] {
        assert_eq!(as_qwen[k], as_muse[k], "{k}");
    }
}

#[test]
fn qwen_live_path_sends_the_clients_bodies_over_loopback() {
    let (origin, heads, bodies) = serve_as(false, Some("rose-qwen"));
    let qwen = profiled("qwen");
    let live = conformance::Live {
        origin,
        token: TOKEN.into(),
        min_interval: std::time::Duration::from_millis(0),
        timeout: std::time::Duration::from_secs(10),
    };
    let summary = conformance::run_live(&qwen, &live);
    assert_eq!(
        (&summary["pass"], &summary["parsed"]),
        (&json!(true), &json!(20))
    );
    assert_eq!(summary["model"], "rose-qwen");
    let bodies = bodies.lock().unwrap().clone();
    assert_eq!((heads.lock().unwrap().len(), bodies.len()), (20, 20));
    for (p, body) in qwen.prompts.iter().zip(&bodies) {
        assert_eq!(*body, conformance::first_request(&qwen, p).1);
    }
    assert!(!summary.to_string().contains(TOKEN));
}
