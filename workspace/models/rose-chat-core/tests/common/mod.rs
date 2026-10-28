#![allow(dead_code)]

use rose_chat_core::config::Config;

pub const MODEL: &str = "test-model-placeholder";

/// Minimal valid config for an in-process fixture. The model id is a test label, not a model.
pub fn config() -> Config {
    config_with("")
}

/// Same as `config()` with extra TOML tables appended.
pub fn config_with(extra: &str) -> Config {
    let text = format!(
        "[endpoint]\nbase_url = \"http://127.0.0.1:9\"\nprotocol = \"openai-chat-candidate\"\n\n\
         [model]\nid = \"{MODEL}\"\n\n{extra}"
    );
    Config::from_toml(&text).expect("test config is valid")
}
