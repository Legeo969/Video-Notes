//! VN-C14N-1 canonical JSON and strict untrusted-JSON parsing.
#![cfg_attr(not(test), allow(dead_code))] // compiler_v3: off-by-default experimental module; items referenced only by conformance tests are unreachable in non-test bin builds

use std::fmt;

use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};

pub const PROFILE: &str = "VN-C14N-1";
pub const SIGNATURE_CONTEXT: &str = "video-notes.exchange-bundle.v0.2";
pub const MAX_BUNDLE_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_DEPTH: usize = 64;
pub const MAX_NODES: usize = 250_000;
pub const MAX_TOTAL_STRING_BYTES: usize = 8 * 1024 * 1024;

struct StrictValue(Value);

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct StrictVisitor;

        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = StrictValue;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON value without duplicate object keys")
            }

            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Bool(value)))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(value.into())))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(value.into())))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                Number::from_f64(value)
                    .map(Value::Number)
                    .map(StrictValue)
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value.to_owned())))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value)))
            }

            fn visit_none<E>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut values = Vec::new();
                while let Some(StrictValue(value)) = sequence.next_element()? {
                    values.push(value);
                }
                Ok(StrictValue(Value::Array(values)))
            }

            fn visit_map<A>(self, mut entries: A) -> Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut values = Map::new();
                while let Some(key) = entries.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(de::Error::custom(format!("duplicate object key: {key}")));
                    }
                    let StrictValue(value) = entries.next_value()?;
                    values.insert(key, value);
                }
                Ok(StrictValue(Value::Object(values)))
            }
        }

        deserializer.deserialize_any(StrictVisitor)
    }
}

fn measure(
    value: &Value,
    depth: usize,
    nodes: &mut usize,
    string_bytes: &mut usize,
) -> Result<(), String> {
    if depth > MAX_DEPTH {
        return Err(format!("JSON depth exceeds {MAX_DEPTH}"));
    }
    *nodes = nodes.saturating_add(1);
    if *nodes > MAX_NODES {
        return Err(format!("JSON node count exceeds {MAX_NODES}"));
    }
    match value {
        Value::Object(object) => {
            for (key, item) in object {
                *string_bytes = string_bytes.saturating_add(key.len());
                measure(item, depth + 1, nodes, string_bytes)?;
            }
        }
        Value::Array(items) => {
            for item in items {
                measure(item, depth + 1, nodes, string_bytes)?;
            }
        }
        Value::String(value) => {
            *string_bytes = string_bytes.saturating_add(value.len());
        }
        _ => {}
    }
    if *string_bytes > MAX_TOTAL_STRING_BYTES {
        return Err(format!("JSON string bytes exceed {MAX_TOTAL_STRING_BYTES}"));
    }
    Ok(())
}

pub fn parse_strict(raw: &str) -> Result<Value, String> {
    if raw.len() > MAX_BUNDLE_BYTES {
        return Err(format!("JSON input exceeds {MAX_BUNDLE_BYTES} bytes"));
    }
    let StrictValue(value) =
        serde_json::from_str::<StrictValue>(raw).map_err(|error| error.to_string())?;
    let mut nodes = 0;
    let mut string_bytes = 0;
    measure(&value, 0, &mut nodes, &mut string_bytes)?;
    Ok(value)
}

fn normalize_number_lexeme(raw: &str) -> Result<String, String> {
    let mut text = raw.to_ascii_lowercase();
    let sign = if text.starts_with('-') {
        text.remove(0);
        "-"
    } else {
        ""
    };
    let (mantissa, exponent) = if let Some((left, right)) = text.split_once('e') {
        let exponent = right
            .parse::<i32>()
            .map_err(|_| "invalid number exponent".to_owned())?;
        (left.to_owned(), exponent)
    } else {
        (text, 0)
    };
    let (integer, fraction) = mantissa.split_once('.').unwrap_or((&mantissa, ""));
    let combined = format!("{integer}{fraction}");
    let leading_removed = combined.len() - combined.trim_start_matches('0').len();
    let mut digits = combined
        .trim_start_matches('0')
        .trim_end_matches('0')
        .to_owned();
    if digits.is_empty() {
        return Ok("0".to_owned());
    }
    let decimal_position = integer.len() as i32 + exponent - leading_removed as i32;
    let adjusted_exponent = decimal_position - 1;
    let body = if (-6..=20).contains(&adjusted_exponent) {
        if decimal_position <= 0 {
            format!("0.{}{}", "0".repeat((-decimal_position) as usize), digits)
        } else if decimal_position as usize >= digits.len() {
            let zeros = decimal_position as usize - digits.len();
            format!("{digits}{}", "0".repeat(zeros))
        } else {
            let tail = digits.split_off(decimal_position as usize);
            format!("{digits}.{tail}")
        }
    } else if digits.len() == 1 {
        format!("{}e{adjusted_exponent}", digits)
    } else {
        let tail = digits.split_off(1);
        format!("{digits}.{tail}e{adjusted_exponent}")
    };
    Ok(format!("{sign}{body}"))
}

fn write_canonical(value: &Value, output: &mut Vec<u8>) -> Result<(), String> {
    match value {
        Value::Null => output.extend_from_slice(b"null"),
        Value::Bool(true) => output.extend_from_slice(b"true"),
        Value::Bool(false) => output.extend_from_slice(b"false"),
        Value::Number(number) => {
            let encoded = if let Some(value) = number.as_i64() {
                value.to_string()
            } else if let Some(value) = number.as_u64() {
                value.to_string()
            } else {
                normalize_number_lexeme(&number.to_string())?
            };
            output.extend_from_slice(encoded.as_bytes());
        }
        Value::String(value) => {
            let encoded = serde_json::to_string(value).map_err(|error| error.to_string())?;
            output.extend_from_slice(encoded.as_bytes());
        }
        Value::Array(items) => {
            output.push(b'[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_canonical(item, output)?;
            }
            output.push(b']');
        }
        Value::Object(object) => {
            output.push(b'{');
            let mut entries: Vec<_> = object.iter().collect();
            entries.sort_by_key(|(key, _)| *key);
            for (index, (key, item)) in entries.into_iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                let encoded_key = serde_json::to_string(key).map_err(|error| error.to_string())?;
                output.extend_from_slice(encoded_key.as_bytes());
                output.push(b':');
                write_canonical(item, output)?;
            }
            output.push(b'}');
        }
    }
    Ok(())
}

pub fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
    write_canonical(value, &mut output)?;
    Ok(output)
}

pub fn signature_payload(value: &Value, key_id: &str, signed_at: &str) -> Result<Vec<u8>, String> {
    if key_id.is_empty() || key_id.as_bytes().contains(&0) {
        return Err("invalid signature key_id".to_string());
    }
    if signed_at.is_empty() || signed_at.as_bytes().contains(&0) {
        return Err("invalid signature timestamp".to_string());
    }
    let canonical = canonical_bytes(value)?;
    let mut payload = Vec::with_capacity(
        SIGNATURE_CONTEXT.len() + key_id.len() + signed_at.len() + 3 + canonical.len(),
    );
    payload.extend_from_slice(SIGNATURE_CONTEXT.as_bytes());
    payload.push(0);
    payload.extend_from_slice(key_id.as_bytes());
    payload.push(0);
    payload.extend_from_slice(signed_at.as_bytes());
    payload.push(0);
    payload.extend_from_slice(&canonical);
    Ok(payload)
}

pub fn digest_value(value: &Value) -> Result<String, String> {
    let bytes = canonical_bytes(value)?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

pub fn digest_object_without(
    object: &Map<String, Value>,
    excluded: &str,
) -> Result<String, String> {
    let mut clone = object.clone();
    clone.remove(excluded);
    digest_value(&Value::Object(clone))
}
