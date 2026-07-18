//! Bounded JSON repair. The repairer never invokes another model.

use serde_json::Value;

const MAX_RAW_BYTES: usize = 1_048_576;
const MAX_JSON_DEPTH: usize = 64;
const MAX_ARRAY_ITEMS: usize = 1_000;
const MAX_OBJECT_KEYS: usize = 256;

#[derive(Debug)]
pub enum RepairResult {
    Valid(Value),
    Repaired(Value),
    Broken { diagnosis: String },
}

pub fn repair_mllm_output(raw_text: &str) -> RepairResult {
    if raw_text.len() > MAX_RAW_BYTES {
        return broken("response exceeds the 1 MiB repair limit");
    }
    let cleaned = strip_code_fences(raw_text).trim();
    let candidate = extract_json_object(cleaned).unwrap_or(cleaned);

    if let Ok(value) = serde_json::from_str::<Value>(candidate) {
        return match validate_limits(&value, 0) {
            Ok(()) => RepairResult::Valid(value),
            Err(error) => broken(&error),
        };
    }

    let repaired = complete_truncated_json(&remove_trailing_commas(candidate));
    match serde_json::from_str::<Value>(&repaired) {
        Ok(value) => match validate_limits(&value, 0) {
            Ok(()) => RepairResult::Repaired(value),
            Err(error) => broken(&error),
        },
        Err(error) => broken(&format!("JSON parse error after bounded repair: {error}")),
    }
}

fn broken(diagnosis: &str) -> RepairResult {
    RepairResult::Broken {
        diagnosis: diagnosis.to_string(),
    }
}

fn validate_limits(value: &Value, depth: usize) -> Result<(), String> {
    if depth > MAX_JSON_DEPTH {
        return Err(format!("JSON nesting exceeds {MAX_JSON_DEPTH}"));
    }
    match value {
        Value::Array(values) => {
            if values.len() > MAX_ARRAY_ITEMS {
                return Err(format!("JSON array exceeds {MAX_ARRAY_ITEMS} items"));
            }
            for item in values {
                validate_limits(item, depth + 1)?;
            }
        }
        Value::Object(values) => {
            if values.len() > MAX_OBJECT_KEYS {
                return Err(format!("JSON object exceeds {MAX_OBJECT_KEYS} keys"));
            }
            for item in values.values() {
                validate_limits(item, depth + 1)?;
            }
        }
        Value::String(text) if text.len() > MAX_RAW_BYTES / 2 => {
            return Err("JSON string exceeds bounded field limit".to_string());
        }
        _ => {}
    }
    Ok(())
}

fn strip_code_fences(text: &str) -> &str {
    let text = text.trim();
    let body = text
        .strip_prefix("```json")
        .or_else(|| text.strip_prefix("```JSON"))
        .or_else(|| text.strip_prefix("```"));
    if let Some(rest) = body {
        return rest.strip_suffix("```").unwrap_or(rest).trim();
    }
    text
}

/// Extract the first balanced top-level object while respecting quoted strings.
fn extract_json_object(text: &str) -> Option<&str> {
    let start = text.find('{')?;
    let bytes = text.as_bytes();
    let mut depth = 0usize;
    let mut in_string = false;
    let mut escaped = false;
    for (index, byte) in bytes.iter().copied().enumerate().skip(start) {
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
            continue;
        }
        match byte {
            b'"' => in_string = true,
            b'{' => depth += 1,
            b'}' => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    return text.get(start..=index);
                }
            }
            _ => {}
        }
    }
    text.get(start..)
}

fn remove_trailing_commas(text: &str) -> String {
    let chars = text.chars().collect::<Vec<_>>();
    let mut output = String::with_capacity(text.len());
    let mut in_string = false;
    let mut escaped = false;
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        if in_string {
            output.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            index += 1;
            continue;
        }
        if ch == '"' {
            in_string = true;
            output.push(ch);
            index += 1;
            continue;
        }
        if ch == ',' {
            let mut lookahead = index + 1;
            while lookahead < chars.len() && chars[lookahead].is_whitespace() {
                lookahead += 1;
            }
            if lookahead < chars.len() && matches!(chars[lookahead], '}' | ']') {
                index += 1;
                continue;
            }
        }
        output.push(ch);
        index += 1;
    }
    output
}

fn complete_truncated_json(text: &str) -> String {
    let mut output = text.to_string();
    let mut stack = Vec::new();
    let mut in_string = false;
    let mut escaped = false;
    for ch in text.chars() {
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => in_string = true,
            '{' | '[' => stack.push(ch),
            '}' if stack.last() == Some(&'{') => {
                stack.pop();
            }
            ']' if stack.last() == Some(&'[') => {
                stack.pop();
            }
            _ => {}
        }
    }
    if in_string {
        output.push('"');
    }
    while let Some(open) = stack.pop() {
        output.push(if open == '{' { '}' } else { ']' });
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_valid_json() {
        assert!(matches!(
            repair_mllm_output(r#"{"events":[],"chunk_summary":"ok"}"#),
            RepairResult::Valid(_)
        ));
    }

    #[test]
    fn removes_trailing_comma_without_touching_strings() {
        let result = repair_mllm_output(r#"{"value":"x,}","events":[],}"#);
        assert!(matches!(result, RepairResult::Repaired(_)));
    }

    #[test]
    fn extracts_json_from_non_json_prefix() {
        let result =
            repair_mllm_output("Here is the result: {\"events\":[],\"chunk_summary\":\"ok\"}");
        assert!(matches!(result, RepairResult::Valid(_)));
    }

    #[test]
    fn rejects_oversized_response() {
        let input = "x".repeat(MAX_RAW_BYTES + 1);
        assert!(matches!(
            repair_mllm_output(&input),
            RepairResult::Broken { .. }
        ));
    }
}
