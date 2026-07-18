//! Explicit signer trust for Spec v0.2 exchange bundles.
#![cfg_attr(not(test), allow(dead_code))]
// compiler_v3: off-by-default experimental module; items referenced only by conformance tests are unreachable in non-test bin builds
//!
//! A cryptographically valid signature is not an authorization decision. The caller
//! must supply an external trust policy that binds a key identifier to an Ed25519 key,
//! allowed purpose, signature context, lifecycle status, and validity window.

use std::collections::BTreeSet;

use base64::Engine as _;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use super::canonical::parse_strict;
use super::ir::SPEC_VERSION;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrustPolicy {
    pub policy_version: String,
    pub policy_id: String,
    pub max_clock_skew_seconds: u32,
    pub keys: Vec<TrustedSigningKey>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrustedSigningKey {
    pub key_id: String,
    pub algorithm: String,
    pub public_key_base64: String,
    pub status: KeyStatus,
    pub purposes: Vec<String>,
    pub signature_contexts: Vec<String>,
    pub not_before: String,
    #[serde(default)]
    pub not_after: Option<String>,
    #[serde(default)]
    pub revocation_reason: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum KeyStatus {
    Trusted,
    Revoked,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TrustError {
    InvalidPolicy(String),
    UntrustedKey,
    AmbiguousKey,
    RevokedKey,
    AlgorithmMismatch,
    PurposeDenied,
    ContextDenied,
    PublicKeyMismatch,
    InvalidTimestamp,
    FutureSignature,
    OutsideValidity,
}

pub struct SignatureAuthorization<'a> {
    pub key_id: &'a str,
    pub algorithm: &'a str,
    pub embedded_public_key_base64: &'a str,
    pub purpose: &'a str,
    pub signature_context: &'a str,
    pub signed_at: &'a str,
    pub verification_time: DateTime<Utc>,
}

impl TrustPolicy {
    pub fn from_json(raw: &str) -> Result<Self, String> {
        let value = parse_strict(raw)?;
        let policy: Self = serde_json::from_value(value).map_err(|error| error.to_string())?;
        policy.validate()?;
        Ok(policy)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.policy_version != SPEC_VERSION {
            return Err("trust policy version does not match active spec".to_string());
        }
        if self.keys.is_empty() {
            return Err("trust policy contains no keys".to_string());
        }
        let mut ids = BTreeSet::new();
        for key in &self.keys {
            if !ids.insert(key.key_id.as_str()) {
                return Err(format!("duplicate trust-policy key_id {}", key.key_id));
            }
            if key.algorithm != "ed25519" {
                return Err(format!(
                    "unsupported trust-policy algorithm for {}",
                    key.key_id
                ));
            }
            let decoded = base64::engine::general_purpose::STANDARD
                .decode(&key.public_key_base64)
                .map_err(|_| format!("invalid trust-policy key encoding for {}", key.key_id))?;
            if decoded.len() != 32 {
                return Err(format!("trust-policy key {} is not 32 bytes", key.key_id));
            }
            let not_before = parse_timestamp(&key.not_before)
                .ok_or_else(|| format!("invalid not_before for {}", key.key_id))?;
            if let Some(not_after) = key.not_after.as_deref() {
                let not_after = parse_timestamp(not_after)
                    .ok_or_else(|| format!("invalid not_after for {}", key.key_id))?;
                if not_after < not_before {
                    return Err(format!("reversed validity window for {}", key.key_id));
                }
            }
            if key.status == KeyStatus::Revoked
                && key.revocation_reason.as_deref().unwrap_or("").is_empty()
            {
                return Err(format!("revoked key {} lacks a reason", key.key_id));
            }
        }
        Ok(())
    }

    pub fn authorize(&self, request: SignatureAuthorization<'_>) -> Result<Vec<u8>, TrustError> {
        let SignatureAuthorization {
            key_id,
            algorithm,
            embedded_public_key_base64,
            purpose,
            signature_context,
            signed_at,
            verification_time,
        } = request;
        self.validate().map_err(TrustError::InvalidPolicy)?;
        let matches: Vec<_> = self
            .keys
            .iter()
            .filter(|key| key.key_id == key_id)
            .collect();
        let key = match matches.as_slice() {
            [] => return Err(TrustError::UntrustedKey),
            [key] => *key,
            _ => return Err(TrustError::AmbiguousKey),
        };
        if key.status == KeyStatus::Revoked {
            return Err(TrustError::RevokedKey);
        }
        if key.algorithm != algorithm {
            return Err(TrustError::AlgorithmMismatch);
        }
        if !key.purposes.iter().any(|value| value == purpose) {
            return Err(TrustError::PurposeDenied);
        }
        if !key
            .signature_contexts
            .iter()
            .any(|value| value == signature_context)
        {
            return Err(TrustError::ContextDenied);
        }
        if key.public_key_base64 != embedded_public_key_base64 {
            return Err(TrustError::PublicKeyMismatch);
        }
        let signed_at = parse_timestamp(signed_at).ok_or(TrustError::InvalidTimestamp)?;
        let not_before = parse_timestamp(&key.not_before).ok_or(TrustError::InvalidTimestamp)?;
        let not_after = key.not_after.as_deref().and_then(parse_timestamp);
        let skew = Duration::seconds(i64::from(self.max_clock_skew_seconds));
        if signed_at > verification_time + skew {
            return Err(TrustError::FutureSignature);
        }
        if signed_at < not_before || not_after.is_some_and(|limit| signed_at > limit) {
            return Err(TrustError::OutsideValidity);
        }
        base64::engine::general_purpose::STANDARD
            .decode(&key.public_key_base64)
            .map_err(|_| TrustError::InvalidPolicy("trusted key encoding is invalid".to_string()))
    }
}

fn parse_timestamp(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|timestamp| timestamp.with_timezone(&Utc))
}
