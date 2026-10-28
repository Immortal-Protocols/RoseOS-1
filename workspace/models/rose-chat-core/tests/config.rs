//! Unit tests: configuration parsing and validation. No I/O beyond in-memory strings.

use std::time::Duration;

use rose_chat_core::ChatError;
use rose_chat_core::auth::AuthMode;
use rose_chat_core::config::{Config, Overrides};

const ENDPOINT: &str =
    "[endpoint]\nbase_url = \"https://example.invalid/v1\"\nprotocol = \"openai-chat-candidate\"\n";

fn parse(text: &str) -> Result<Config, ChatError> {
    Config::from_toml(text)
}

fn config_error(text: &str) -> String {
    match parse(text) {
        Err(ChatError::Config(m)) => m,
        other => panic!("expected a config error, got {other:?}"),
    }
}

#[test]
fn c02_missing_model_id_is_an_error_with_no_default() {
    let m = config_error(ENDPOINT);
    assert!(m.contains("model.id is required"), "{m}");
    assert!(m.contains("no default"), "{m}");
}

#[test]
fn c02_blank_or_padded_model_id_is_rejected() {
    for id in ["", "   ", " padded", "padded\t"] {
        let text = format!("{ENDPOINT}[model]\nid = \"{id}\"\n");
        assert!(matches!(parse(&text), Err(ChatError::Config(_))), "{id:?}");
    }
}

#[test]
fn model_id_is_kept_verbatim() {
    let id = "org/Some-Model_30B@rev:abc/123";
    let c = parse(&format!(
        "{ENDPOINT}[model]\nid = \"{id}\"\nexpected_revision = \"abc123\"\n"
    ))
    .unwrap();
    assert_eq!(c.model.as_str(), id);
    assert_eq!(c.expected_revision.as_deref(), Some("abc123"));
}

#[test]
fn defaults_for_non_identity_settings() {
    let c = parse(&format!("{ENDPOINT}[model]\nid = \"m\"\n")).unwrap();
    assert_eq!(c.auth, AuthMode::None);
    assert_eq!(c.connect_timeout, Duration::from_millis(10_000));
    assert_eq!(c.request_timeout, Duration::from_millis(120_000));
    assert_eq!(c.max_tokens, 512);
    assert_eq!(c.context_budget_chars, 32_000);
    assert_eq!(c.temperature, None);
    assert_eq!(c.system_prompt, None);
}

#[test]
fn protocol_is_required_and_only_the_candidate_is_accepted() {
    let missing = config_error("[endpoint]\nbase_url = \"http://x\"\n[model]\nid = \"m\"\n");
    assert!(
        missing.contains("endpoint.protocol is required"),
        "{missing}"
    );
    let other = config_error(
        "[endpoint]\nbase_url = \"http://x\"\nprotocol = \"vertex\"\n[model]\nid = \"m\"\n",
    );
    assert!(other.contains("not supported"), "{other}");
}

#[test]
fn base_url_validation() {
    for (url, expect) in [
        ("ftp://host", "must start with http"),
        ("https://", "no host"),
        ("https://user:pw@host/v1", "must not embed credentials"),
        ("https://host/a b", "whitespace"),
    ] {
        let text = format!(
            "[endpoint]\nbase_url = \"{url}\"\nprotocol = \"openai-chat-candidate\"\n[model]\nid = \"m\"\n"
        );
        let m = config_error(&text);
        assert!(m.contains(expect), "{url}: {m}");
        assert!(!m.contains("pw"), "error must not echo the URL: {m}");
    }
}

#[test]
fn overrides_apply_cli_over_env_over_file() {
    let text = format!("{ENDPOINT}[model]\nid = \"file-model\"\n");
    let env = Overrides {
        base_url: Some("http://env-host".into()),
        model_id: Some("env-model".into()),
    };
    let cli = Overrides {
        base_url: None,
        model_id: Some("cli-model".into()),
    };
    let c = Config::load(&text, &env, &cli).unwrap();
    assert_eq!(c.model.as_str(), "cli-model");
    assert_eq!(c.base_url, "http://env-host");

    let c = Config::load(&text, &Overrides::default(), &Overrides::default()).unwrap();
    assert_eq!(c.model.as_str(), "file-model");
    assert_eq!(c.base_url, "https://example.invalid/v1");
}

#[test]
fn env_override_names() {
    let lookup = |k: &str| match k {
        "ROSE_CHAT_MODEL_ID" => Some("from-env".to_string()),
        "ROSE_CHAT_BASE_URL" => Some("http://127.0.0.1:1".to_string()),
        _ => None,
    };
    let o = Overrides::from_env(&lookup);
    assert_eq!(o.model_id.as_deref(), Some("from-env"));
    assert_eq!(o.base_url.as_deref(), Some("http://127.0.0.1:1"));
    // An override can supply a model the file lacks, but nothing supplies one implicitly.
    let c = Config::load(ENDPOINT, &o, &Overrides::default()).unwrap();
    assert_eq!(c.model.as_str(), "from-env");
}

#[test]
fn retry_other_than_zero_is_rejected_in_this_build() {
    assert!(
        parse(&format!(
            "{ENDPOINT}[model]\nid = \"m\"\n[retry]\nmax_attempts = 0\n"
        ))
        .is_ok()
    );
    let m = config_error(&format!(
        "{ENDPOINT}[model]\nid = \"m\"\n[retry]\nmax_attempts = 2\n"
    ));
    assert!(m.contains("not supported"), "{m}");
}

#[test]
fn timeouts_and_limits_validation() {
    let base = format!("{ENDPOINT}[model]\nid = \"m\"\n");
    for bad in [
        "[timeouts]\nconnect_ms = 0\n",
        "[timeouts]\nrequest_ms = 0\n",
        "[timeouts]\nconnect_ms = 5000\nrequest_ms = 1000\n",
        "[limits]\nmax_tokens = 0\n",
        "[limits]\ncontext_budget_chars = 0\n",
        "[chat]\ntemperature = 3.5\n",
        "[timeouts]\nconnect_ms = -1\n",
    ] {
        assert!(
            matches!(parse(&format!("{base}{bad}")), Err(ChatError::Config(_))),
            "{bad}"
        );
    }
}

#[test]
fn unknown_fields_are_rejected_so_typos_and_token_fields_fail() {
    let m = config_error(&format!("{ENDPOINT}[model]\nid = \"m\"\nrevison = \"x\"\n"));
    assert!(m.contains("unknown field"), "{m}");
    let m = config_error(&format!(
        "{ENDPOINT}[model]\nid = \"m\"\n[auth]\nmode = \"bearer-env\"\ntoken = \"tok-SENSITIVE-123\"\n"
    ));
    assert!(m.contains("unknown field"), "{m}");
    assert!(!m.contains("tok-SENSITIVE-123"), "{m}");
}

#[test]
fn auth_modes() {
    let base = format!("{ENDPOINT}[model]\nid = \"m\"\n");
    let c = parse(&format!(
        "{base}[auth]\nmode = \"bearer-env\"\nenv_var = \"ROSE_CHAT_TOKEN\"\n"
    ))
    .unwrap();
    assert_eq!(
        c.auth,
        AuthMode::BearerEnv {
            var: "ROSE_CHAT_TOKEN".into()
        }
    );
    let c = parse(&format!(
        "{base}[auth]\nmode = \"bearer-command\"\ncommand = [\"token-tool\", \"print\"]\n"
    ))
    .unwrap();
    assert_eq!(
        c.auth,
        AuthMode::BearerCommand {
            argv: vec!["token-tool".into(), "print".into()]
        }
    );

    for bad in [
        "[auth]\nmode = \"bearer-env\"\n",
        "[auth]\nmode = \"bearer-env\"\nenv_var = \"ya29.not-a-variable-name\"\n",
        "[auth]\nmode = \"bearer-command\"\ncommand = []\n",
        "[auth]\nmode = \"bearer-command\"\n",
        "[auth]\nmode = \"none\"\nenv_var = \"X\"\n",
        "[auth]\nmode = \"basic\"\n",
    ] {
        assert!(
            matches!(parse(&format!("{base}{bad}")), Err(ChatError::Config(_))),
            "{bad}"
        );
    }
}

#[test]
fn malformed_toml_error_does_not_quote_the_file() {
    let m = config_error("[auth]\nmode = \"bearer-env\nsecret-looking-value\n");
    assert!(!m.contains("secret-looking-value"), "{m}");
}

#[test]
fn summary_lists_settings_without_values_it_does_not_hold() {
    let c = parse(&format!(
        "{ENDPOINT}[model]\nid = \"m\"\n[auth]\nmode = \"bearer-env\"\nenv_var = \"ROSE_CHAT_TOKEN\"\n[chat]\nsystem_prompt = \"private instructions\"\n"
    ))
    .unwrap();
    let s = c.summary();
    assert!(s.contains("model.id = m"));
    assert!(s.contains("bearer-env (variable ROSE_CHAT_TOKEN)"));
    assert!(s.contains("chat.system_prompt = set"));
    assert!(!s.contains("private instructions"));
}
