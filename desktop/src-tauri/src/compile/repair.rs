/// JSON Repair Sandbox — Pass 3 preprocessing.
///
/// Attempts to parse MLLM output as JSON, with a repair fallback.
/// If repair fails, the chunk is marked as broken rather than crashing.

use serde_json::Value;

/// Result of the JSON repair attempt.
#[derive(Debug)]
pub enum RepairResult {
    /// Successfully parsed JSON.
    Valid(Value),
    /// Repaired and then successfully parsed.
    Repaired(Value),
    /// Irreparably broken. Contains the original text (truncated) and diagnosis.
    Broken { snippet: String, diagnosis: String },
}

/// Attempt to parse MLLM raw text output as JSON.
///
/// Strategy:
/// 1. Strip markdown code fences (```json ... ```)
/// 2. Try `serde_json::from_str`
/// 3. On failure, apply repair heuristics and retry
/// 4. If still broken, return `RepairResult::Broken`
///    — absolutely NO LLM re-fix to prevent prompt injection.
pub fn repair_mllm_output(raw_text: &str) -> RepairResult {
    let cleaned = strip_code_fences(raw_text);
    let trimmed = cleaned.trim();

    // Try direct parse first
    match serde_json::from_str::<Value>(trimmed) {
        Ok(value) => return RepairResult::Valid(value),
        Err(_) => {} // fall through to repair
    }

    // Attempt repair
    let repaired = apply_repairs(trimmed);
    match serde_json::from_str::<Value>(&repaired) {
        Ok(value) => RepairResult::Repaired(value),
        Err(err) => {
            let snippet = trimmed.chars().take(200).collect::<String>();
            RepairResult::Broken {
                snippet,
                diagnosis: format!("JSON parse error: {err}"),
            }
        }
    }
}

/// Check if the JSON is structurally valid.
#[allow(dead_code)]
pub fn is_valid_json(text: &str) -> bool {
    serde_json::from_str::<Value>(text).is_ok()
}

/// Check if the repair result is usable (valid or repaired).
#[allow(dead_code)]
pub fn is_usable(result: &RepairResult) -> bool {
    matches!(result, RepairResult::Valid(_) | RepairResult::Repaired(_))
}

// ---------------------------------------------------------------------------
// Repair heuristics
// ---------------------------------------------------------------------------

fn apply_repairs(text: &str) -> String {
    let mut s = text.to_string();

    // 1. Remove trailing commas in objects and arrays
    s = remove_trailing_commas(&s);

    // 2. Wrap single-quoted strings in double quotes (for keys and values)
    s = fix_single_quotes(&s);

    // 3. Unquote bare keys (common MLLM issue: { key: "value" })
    s = unquote_bare_keys(&s);

    // 4. Replace Python-style True/False/None with JSON true/false/null
    s = s.replace("True", "true").replace("False", "false").replace("None", "null");

    // 5. Try to complete truncated JSON (append missing closing brackets)
    s = complete_truncated_json(&s);

    s
}

/// Strip markdown code fences like ```json ... ```
fn strip_code_fences(text: &str) -> &str {
    let text = text.trim();
    if let Some(rest) = text.strip_prefix("```json") {
        if let Some(end) = rest.rfind("```") {
            return rest[..end].trim();
        }
        // No closing fence — take everything after ```
        return rest.trim();
    }
    if let Some(rest) = text.strip_prefix("```") {
        if let Some(end) = rest.rfind("```") {
            return rest[..end].trim();
        }
        return rest.trim();
    }
    text
}

fn remove_trailing_commas(text: &str) -> String {
    // Remove comma before }
    let mut s = text.to_string();
    s = s.replace(",}", "}");
    s = s.replace(",] ", "]");
    s
}

fn fix_single_quotes(text: &str) -> String {
    // Replace single-quoted strings with double-quoted
    // This is a best-effort heuristic — doesn't handle escaped quotes
    let mut result = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    let mut in_string = false;
    let mut quote_char = '"';

    while let Some(c) = chars.next() {
        if c == '\'' && !in_string {
            // Start of single-quoted string
            in_string = true;
            quote_char = '\'';
            result.push('"');
        } else if c == '\'' && in_string && quote_char == '\'' {
            // End of single-quoted string — but check for escaped quote
            if chars.peek() == Some(&'\'') {
                // Double single quote → escaped quote
                result.push_str("\\'");
                chars.next();
            } else {
                in_string = false;
                quote_char = '"';
                result.push('"');
            }
        } else if c == '"' && !in_string {
            // Double quote in non-string context — could be from object key
            in_string = true;
            quote_char = '"';
            result.push('"');
        } else if c == '"' && in_string && quote_char == '"' {
            if chars.peek() == Some(&'"') {
                result.push('"');
                chars.next();
            } else {
                in_string = false;
                quote_char = '"';
                result.push('"');
            }
        } else {
            result.push(c);
        }
    }

    if in_string {
        result.push('"'); // close unclosed string
    }
    result
}

fn unquote_bare_keys(text: &str) -> String {
    // Pattern: { identifier: → { "identifier":
    // This is a simple heuristic using regex-like scanning
    let mut result = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    let mut in_string = false;

    while let Some(c) = chars.next() {
        if c == '"' || c == '\'' {
            in_string = !in_string;
            result.push('"');
        } else if !in_string && (c == '{' || c == ',') {
            result.push(c);
            // Skip whitespace
            while let Some(&ws) = chars.peek() {
                if ws == ' ' || ws == '\t' || ws == '\n' || ws == '\r' {
                    result.push(chars.next().unwrap());
                } else {
                    break;
                }
            }
            // Check if next char is a letter (bare key start)
            if let Some(&next) = chars.peek() {
                if next.is_ascii_alphabetic() || next == '_' {
                    result.push('"');
                    // Read the key until ':' or whitespace
                    while let Some(&k) = chars.peek() {
                        if k == ':' {
                            result.push('"');
                            break;
                        }
                        if k.is_ascii_alphanumeric() || k == '_' {
                            result.push(chars.next().unwrap());
                        } else {
                            break;
                        }
                    }
                }
            }
        } else {
            result.push(c);
        }
    }
    result
}

fn complete_truncated_json(text: &str) -> String {
    let mut s = text.to_string();
    let mut stack: Vec<char> = Vec::new();

    for c in s.chars() {
        match c {
            '{' | '[' => stack.push(c),
            '}' => {
                if stack.last() == Some(&'{') {
                    stack.pop();
                }
            }
            ']' => {
                if stack.last() == Some(&'[') {
                    stack.pop();
                }
            }
            _ => {}
        }
    }

    // Append missing closing brackets in reverse order
    while let Some(&open) = stack.last() {
        match open {
            '{' => s.push('}'),
            '[' => s.push(']'),
            _ => {}
        }
        stack.pop();
    }

    s
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_json_passes_through() {
        let input = r#"{"events":[{"title":"intro"}]}"#;
        match repair_mllm_output(input) {
            RepairResult::Valid(v) => {
                assert_eq!(v["events"][0]["title"], "intro");
            }
            _ => panic!("expected Valid"),
        }
    }

    #[test]
    fn test_code_fences_are_stripped() {
        let input = "```json\n{\"key\": \"value\"}\n```";
        match repair_mllm_output(input) {
            RepairResult::Valid(v) => {
                assert_eq!(v["key"], "value");
            }
            _ => panic!("expected Valid"),
        }
    }

    #[test]
    fn test_trailing_comma_is_repaired() {
        let input = r#"{"events":[{"title":"a"},]}"#;
        match repair_mllm_output(input) {
            RepairResult::Valid(_) | RepairResult::Repaired(_) => {} // ok
            RepairResult::Broken { .. } => panic!("expected repaired"),
        }
    }

    #[test]
    fn test_single_quotes_repaired() {
        let input = "{'key': 'value'}";
        match repair_mllm_output(input) {
            RepairResult::Repaired(v) => {
                assert_eq!(v["key"], "value");
            }
            other => panic!("expected Repaired, got {other:?}"),
        }
    }

    #[test]
    fn test_bare_keys_repaired() {
        let input = "{key: \"value\"}";
        match repair_mllm_output(input) {
            RepairResult::Repaired(v) => {
                assert_eq!(v["key"], "value");
            }
            other => panic!("expected Repaired, got {other:?}"),
        }
    }

    #[test]
    fn test_truncated_json_completed() {
        let input = r#"{"events":[{"title":"a"}"#;
        match repair_mllm_output(input) {
            RepairResult::Repaired(v) => {
                assert_eq!(v["events"][0]["title"], "a");
            }
            other => panic!("expected Repaired, got {other:?}"),
        }
    }

    #[test]
    fn test_broken_json_returns_broken() {
        let input = "this is not json at all";
        match repair_mllm_output(input) {
            RepairResult::Broken { .. } => {} // expected
            other => panic!("expected Broken, got {other:?}"),
        }
    }

    #[test]
    fn test_is_usable() {
        assert!(is_usable(&RepairResult::Valid(serde_json::json!({}))));
        assert!(is_usable(&RepairResult::Repaired(serde_json::json!({}))));
        assert!(!is_usable(&RepairResult::Broken {
            snippet: String::new(),
            diagnosis: String::new(),
        }));
    }

    #[test]
    fn test_strip_code_fences() {
        let fenced = "```json\n{\"a\":1}\n```";
        assert!(is_valid_json(strip_code_fences(fenced).trim()));

        let no_fence = "{\"a\":1}";
        assert!(is_valid_json(strip_code_fences(no_fence).trim()));
    }

    #[test]
    fn test_python_bools_normalized() {
        let input = r#"{"active": True, "enabled": False, "data": None}"#;
        match repair_mllm_output(input) {
            RepairResult::Repaired(v) => {
                assert!(v["active"].as_bool().unwrap());
                assert!(!v["enabled"].as_bool().unwrap());
                assert!(v["data"].is_null());
            }
            other => panic!("expected Repaired, got {other:?}"),
        }
    }
}