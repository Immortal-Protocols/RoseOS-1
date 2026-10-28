//! C14: a credential value never appears in Debug, Display, errors or session state.

mod common;

use rose_chat_core::auth::{AuthMode, Secret};
use rose_chat_core::config::Config;
use rose_chat_core::fixture::{ScriptedTransport, Step};
use rose_chat_core::{ChatError, ChatSession};

const TOKEN: &str = "tok-SENSITIVE-123";

#[test]
fn secret_formatting_is_redacted() {
    let s = Secret::new(TOKEN);
    assert_eq!(format!("{s}"), "<redacted>");
    assert_eq!(format!("{s:?}"), "Secret(<redacted>)");
    assert_eq!(format!("{:?}", Some(&s)), "Some(Secret(<redacted>))");
    assert_eq!(s.expose(), TOKEN);
}

#[test]
fn env_credential_resolves_without_exposure() {
    let mode = AuthMode::BearerEnv {
        var: "ROSE_CHAT_TOKEN".into(),
    };
    let env = |k: &str| (k == "ROSE_CHAT_TOKEN").then(|| format!("  {TOKEN}\n"));
    let secret = mode.resolve(&env).unwrap().unwrap();
    assert_eq!(secret.expose(), TOKEN);
    assert!(!format!("{mode:?} {} {secret:?}", mode.describe()).contains(TOKEN));

    let missing = mode.resolve(&|_| None).unwrap_err();
    assert!(matches!(missing, ChatError::Auth(ref m) if m.contains("ROSE_CHAT_TOKEN")));
    assert!(matches!(
        mode.resolve(&|_| Some("   ".into())),
        Err(ChatError::Auth(_))
    ));
}

#[test]
fn command_credential_resolves_and_failures_hide_output() {
    let ok = AuthMode::BearerCommand {
        argv: vec!["printf".into(), format!("{TOKEN}\n")],
    };
    assert_eq!(ok.resolve(&|_| None).unwrap().unwrap().expose(), TOKEN);

    let failing = AuthMode::BearerCommand {
        argv: vec![
            "sh".into(),
            "-c".into(),
            format!("echo {TOKEN}; echo {TOKEN} >&2; exit 3"),
        ],
    };
    let e = failing.resolve(&|_| None).unwrap_err();
    assert!(matches!(e, ChatError::Auth(_)));
    assert!(!format!("{e} {e:?}").contains(TOKEN), "{e}");

    let silent = AuthMode::BearerCommand {
        argv: vec!["true".into()],
    };
    assert!(matches!(silent.resolve(&|_| None), Err(ChatError::Auth(_))));

    let absent = AuthMode::BearerCommand {
        argv: vec!["/nonexistent/rose-token-tool".into()],
    };
    assert!(matches!(absent.resolve(&|_| None), Err(ChatError::Auth(_))));
}

#[test]
fn config_and_session_debug_output_hold_no_token() {
    let config = Config::from_toml(
        "[endpoint]\nbase_url = \"http://127.0.0.1:9\"\nprotocol = \"openai-chat-candidate\"\n\
         [model]\nid = \"m\"\n[auth]\nmode = \"bearer-env\"\nenv_var = \"ROSE_CHAT_TOKEN\"\n",
    )
    .unwrap();
    assert!(!format!("{config:?}{}", config.summary()).contains(TOKEN));

    let mut session = ChatSession::new(
        &config,
        ScriptedTransport::new([Step::Fail(ChatError::Unauthorized { status: 401 })]),
        Some(Secret::new(TOKEN)),
    );
    let e = session.send("hello").unwrap_err();
    assert!(!format!("{e} {e:?} {:?}", session.transport().requests).contains(TOKEN));
}

#[test]
fn empty_command_built_in_code_is_an_error_not_a_panic() {
    let empty = AuthMode::BearerCommand { argv: vec![] };
    assert!(matches!(empty.resolve(&|_| None), Err(ChatError::Auth(_))));
    assert_eq!(empty.describe(), "bearer-command (program (empty))");
}
