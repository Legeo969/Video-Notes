use crate::native_engine::{NativeProviderProfile, with_optional_bearer};
use serde_json::{json, Value};
use std::time::Duration;

const QUIZ_SYSTEM_PROMPT: &str = r#"你是一个学习助手。根据提供的笔记内容，生成 5 道选择题来检验理解。
要求：
1. 每道题 4 个选项，只有一个正确
2. 正确答案必须直接来自笔记内容，不能编造
3. 提供每道题的简短解析，解释为什么正确答案是对的
4. 用 JSON 格式输出，格式如下（不要额外文字）：
[
  {
    "question": "题目文本",
    "choices": ["A选项", "B选项", "C选项", "D选项"],
    "correctIndex": 0,
    "explanation": "解析文本"
  }
]"#;

/// Generate quiz questions from note content using the configured AI provider.
pub(crate) fn generate_quiz(profile: &NativeProviderProfile, content: &str) -> Result<Value, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| format!("HTTP client init failed: {e}"))?;

    let url = format!(
        "{}/chat/completions",
        profile.base_url.trim_end_matches('/')
    );

    let response = with_optional_bearer(client.post(&url), &profile.api_key)
        .json(&json!({
            "model": profile.model,
            "messages": [
                { "role": "system", "content": QUIZ_SYSTEM_PROMPT },
                { "role": "user", "content": format!("笔记内容：\n\n{}", content) }
            ],
            "temperature": 0.3,
            "max_tokens": 4096
        }))
        .send()
        .map_err(|e| format!("HTTP request failed: {e}"))?;

    let status = response.status();
    let payload: Value = response.json().map_err(|e| format!("Invalid JSON response: {e}"))?;

    if !status.is_success() {
        return Err(format!("chat completion returned {status}: {payload}"));
    }

    let text = payload
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|c| c.first())
        .and_then(|c| c.get("message"))
        .and_then(|m| {
            m.get("content")
                .or_else(|| m.get("reasoning"))
        })
        .and_then(Value::as_str)
        .ok_or_else(|| "chat completion returned no content".to_string())?;

    // Parse the response as JSON
    let parsed: Value = serde_json::from_str(text)
        .map_err(|e| format!("Failed to parse quiz JSON: {e}. Raw: {}", text.chars().take(200).collect::<String>()))?;

    // Accept either a top-level array or {"questions": [...]}
    let questions = parsed.as_array()
        .or_else(|| parsed.get("questions").and_then(Value::as_array))
        .ok_or_else(|| {
            format!("Quiz response is not an array or {{ \"questions\": [...] }}: {}",
                text.chars().take(200).collect::<String>())
        })?;

    if questions.is_empty() {
        return Err("Quiz generated 0 questions".to_string());
    }

    // Always return the raw array for consistent downstream handling
    Ok(Value::Array(questions.clone()))
}
