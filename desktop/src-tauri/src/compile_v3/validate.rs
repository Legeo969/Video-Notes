//! Deterministic, offline Spec v0.2 validation.
#![cfg_attr(not(test), allow(dead_code))] // compiler_v3: off-by-default experimental module; items referenced only by conformance tests are unreachable in non-test bin builds

use std::collections::{BTreeMap, BTreeSet};

use base64::Engine as _;
use chrono::{DateTime, Utc};
use ring::signature::{UnparsedPublicKey, ED25519};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use super::canonical::{
    canonical_bytes, digest_object_without, parse_strict, signature_payload, PROFILE,
    SIGNATURE_CONTEXT,
};
use super::ir::{ExchangeBundle, SPEC_VERSION};
use super::trust::{SignatureAuthorization, TrustError, TrustPolicy};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationIssue {
    pub code: &'static str,
    pub path: String,
    pub message: String,
}

#[derive(Debug, Clone, Default)]
pub struct ValidationReport {
    pub issues: Vec<ValidationIssue>,
}

impl ValidationReport {
    pub fn is_valid(&self) -> bool {
        self.issues.is_empty()
    }

    fn push(&mut self, code: &'static str, path: impl Into<String>, message: impl Into<String>) {
        self.issues.push(ValidationIssue {
            code,
            path: path.into(),
            message: message.into(),
        });
    }

    pub fn has_code(&self, code: &str) -> bool {
        self.issues.iter().any(|issue| issue.code == code)
    }
}

pub fn parse_and_validate(raw: &str) -> Result<ExchangeBundle, ValidationReport> {
    parse_and_validate_internal(raw, None)
}

pub fn parse_and_validate_with_policy(
    raw: &str,
    policy: &TrustPolicy,
    verification_time: DateTime<Utc>,
) -> Result<ExchangeBundle, ValidationReport> {
    parse_and_validate_internal(raw, Some((policy, verification_time)))
}

fn parse_and_validate_internal(
    raw: &str,
    trust: Option<(&TrustPolicy, DateTime<Utc>)>,
) -> Result<ExchangeBundle, ValidationReport> {
    let value: Value = match parse_strict(raw) {
        Ok(value) => value,
        Err(error) => {
            let mut report = ValidationReport::default();
            report.push("VN_SCHEMA_JSON", "$", error);
            return Err(report);
        }
    };
    let mut report = validate_value_internal(&value, trust);
    if !report.is_valid() {
        return Err(report);
    }
    match serde_json::from_value(value) {
        Ok(bundle) => Ok(bundle),
        Err(error) => {
            report.push("VN_SCHEMA_DESERIALIZE", "$", error.to_string());
            Err(report)
        }
    }
}

pub fn validate_value(root: &Value) -> ValidationReport {
    validate_value_internal(root, None)
}

pub fn validate_value_with_policy(
    root: &Value,
    policy: &TrustPolicy,
    verification_time: DateTime<Utc>,
) -> ValidationReport {
    validate_value_internal(root, Some((policy, verification_time)))
}

fn validate_value_internal(
    root: &Value,
    trust: Option<(&TrustPolicy, DateTime<Utc>)>,
) -> ValidationReport {
    let mut report = ValidationReport::default();
    let Some(bundle) = root.as_object() else {
        report.push("VN_SCHEMA_ROOT", "$", "bundle must be an object");
        return report;
    };

    if string_at(bundle, "bundle_version") != Some(SPEC_VERSION) {
        report.push(
            "VN_VERSION",
            "$.bundle_version",
            "unsupported bundle version",
        );
    }

    validate_exchange_signature(root, bundle, trust, &mut report);
    validate_execution_plan(bundle, &mut report);
    validate_compile_report(bundle, &mut report);
    validate_reviews_and_extensions(bundle, &mut report);
    validate_completeness(bundle, &mut report);
    validate_anchors(bundle, &mut report);
    validate_ranges(bundle, &mut report);
    validate_artifact_lineage(bundle, &mut report);
    validate_policy(bundle, &mut report);
    validate_cross_references(bundle, &mut report);
    validate_external_references(bundle, &mut report);
    validate_provider_manifests(bundle, &mut report);
    report
}

fn string_at<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object.get(key)?.as_str()
}

fn array_at<'a>(object: &'a Map<String, Value>, key: &str) -> &'a [Value] {
    object
        .get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

fn valid_digest(value: Option<&str>) -> bool {
    let Some(value) = value else { return false };
    let Some(hex) = value
        .strip_prefix("sha256:")
        .or_else(|| value.strip_prefix("blake3:"))
    else {
        return false;
    };
    hex.len() == 64
        && hex
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn validate_exchange_signature(
    root: &Value,
    bundle: &Map<String, Value>,
    trust: Option<(&TrustPolicy, DateTime<Utc>)>,
    report: &mut ValidationReport,
) {
    let Some(manifest) = bundle.get("exchange_manifest").and_then(Value::as_object) else {
        report.push(
            "VN_SIGNATURE_MISSING",
            "$.exchange_manifest",
            "exchange manifest is required",
        );
        return;
    };
    let mut unsigned = root.clone();
    if let Some(object) = unsigned.as_object_mut() {
        object.remove("exchange_manifest");
    }
    if string_at(manifest, "canonicalization_profile") != Some(PROFILE) {
        report.push(
            "VN_SIGNATURE_PROFILE",
            "$.exchange_manifest.canonicalization_profile",
            "unsupported canonicalization profile",
        );
    }
    if string_at(manifest, "signature_context") != Some(SIGNATURE_CONTEXT) {
        report.push(
            "VN_SIGNATURE_CONTEXT",
            "$.exchange_manifest.signature_context",
            "signature context mismatch",
        );
    }
    let Ok(canonical) = canonical_bytes(&unsigned) else {
        report.push(
            "VN_SIGNATURE_CANONICAL",
            "$",
            "bundle cannot be canonicalized",
        );
        return;
    };
    let actual_digest = format!("sha256:{:x}", Sha256::digest(&canonical));
    if string_at(manifest, "content_digest") != Some(actual_digest.as_str()) {
        report.push(
            "VN_SIGNATURE_DIGEST",
            "$.exchange_manifest.content_digest",
            "canonical content digest mismatch",
        );
    }
    let Some(signature) = manifest.get("signature").and_then(Value::as_object) else {
        report.push(
            "VN_SIGNATURE_MISSING",
            "$.exchange_manifest.signature",
            "signature is required",
        );
        return;
    };
    let algorithm = string_at(signature, "algorithm").unwrap_or("");
    let key_id = string_at(signature, "key_id").unwrap_or("");
    let embedded_key = string_at(signature, "public_key_base64").unwrap_or("");
    let signed_at = string_at(signature, "signed_at").unwrap_or("");
    if algorithm != "ed25519" {
        report.push(
            "VN_SIGNATURE_ALGORITHM",
            "$.exchange_manifest.signature.algorithm",
            "only ed25519 is supported",
        );
        return;
    }
    let decoded_signature = string_at(signature, "signature_base64")
        .and_then(|value| base64::engine::general_purpose::STANDARD.decode(value).ok());
    let Some(signature_bytes) = decoded_signature else {
        report.push(
            "VN_SIGNATURE_VALUE",
            "$.exchange_manifest.signature.signature_base64",
            "invalid signature encoding",
        );
        return;
    };
    let key_bytes = if let Some((policy, verification_time)) = trust {
        match policy.authorize(SignatureAuthorization {
            key_id,
            algorithm,
            embedded_public_key_base64: embedded_key,
            purpose: "exchange_bundle",
            signature_context: string_at(manifest, "signature_context").unwrap_or(""),
            signed_at,
            verification_time,
        }) {
            Ok(bytes) => bytes,
            Err(error) => {
                push_trust_error(report, error);
                return;
            }
        }
    } else {
        report.push(
            "VN_SIGNATURE_UNTRUSTED",
            "$.exchange_manifest.signature.key_id",
            "cryptographic validity is insufficient; an external trust policy is required",
        );
        match base64::engine::general_purpose::STANDARD.decode(embedded_key) {
            Ok(bytes) => bytes,
            Err(_) => {
                report.push(
                    "VN_SIGNATURE_KEY",
                    "$.exchange_manifest.signature.public_key_base64",
                    "invalid public key encoding",
                );
                return;
            }
        }
    };
    if key_bytes.len() != 32 {
        report.push(
            "VN_SIGNATURE_KEY",
            "$.exchange_manifest.signature.public_key_base64",
            "ed25519 public key must be 32 bytes",
        );
        return;
    }
    let Ok(payload) = signature_payload(&unsigned, key_id, signed_at) else {
        report.push(
            "VN_SIGNATURE_CANONICAL",
            "$",
            "signature payload cannot be canonicalized",
        );
        return;
    };
    let verifier = UnparsedPublicKey::new(&ED25519, &key_bytes);
    if verifier.verify(&payload, &signature_bytes).is_err() {
        report.push(
            "VN_SIGNATURE_VERIFY",
            "$.exchange_manifest.signature",
            "exchange signature verification failed",
        );
    }
}

fn push_trust_error(report: &mut ValidationReport, error: TrustError) {
    let (code, path, message) = match error {
        TrustError::InvalidPolicy(message) => (
            "VN_TRUST_POLICY",
            "$.exchange_manifest.signature.key_id",
            message,
        ),
        TrustError::UntrustedKey => (
            "VN_SIGNATURE_UNTRUSTED",
            "$.exchange_manifest.signature.key_id",
            "signing key is not trusted".to_string(),
        ),
        TrustError::AmbiguousKey => (
            "VN_TRUST_POLICY",
            "$.exchange_manifest.signature.key_id",
            "signing key identifier is ambiguous".to_string(),
        ),
        TrustError::RevokedKey => (
            "VN_SIGNATURE_REVOKED",
            "$.exchange_manifest.signature.key_id",
            "signing key is revoked".to_string(),
        ),
        TrustError::AlgorithmMismatch => (
            "VN_SIGNATURE_ALGORITHM",
            "$.exchange_manifest.signature.algorithm",
            "signing algorithm is not authorized".to_string(),
        ),
        TrustError::PurposeDenied => (
            "VN_SIGNATURE_SCOPE",
            "$.exchange_manifest.signature.key_id",
            "key is not authorized for exchange bundles".to_string(),
        ),
        TrustError::ContextDenied => (
            "VN_SIGNATURE_SCOPE",
            "$.exchange_manifest.signature.key_id",
            "key is not authorized for this signature context".to_string(),
        ),
        TrustError::PublicKeyMismatch => (
            "VN_SIGNATURE_KEY_MISMATCH",
            "$.exchange_manifest.signature.public_key_base64",
            "embedded public key does not match trusted key identifier".to_string(),
        ),
        TrustError::InvalidTimestamp => (
            "VN_SIGNATURE_TIME",
            "$.exchange_manifest.signature.signed_at",
            "signature timestamp is invalid".to_string(),
        ),
        TrustError::FutureSignature => (
            "VN_SIGNATURE_TIME",
            "$.exchange_manifest.signature.signed_at",
            "signature timestamp exceeds allowed clock skew".to_string(),
        ),
        TrustError::OutsideValidity => (
            "VN_SIGNATURE_TIME",
            "$.exchange_manifest.signature.signed_at",
            "signature is outside the trusted key validity window".to_string(),
        ),
    };
    report.push(code, path, message);
}

fn validate_execution_plan(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let Some(plan) = bundle
        .get("compilation")
        .and_then(Value::as_object)
        .and_then(|compilation| compilation.get("execution_plan"))
        .and_then(Value::as_object)
    else {
        return;
    };
    let expected = digest_object_without(plan, "plan_digest").ok();
    if expected.as_deref() != string_at(plan, "plan_digest") {
        report.push(
            "VN_PLAN_DIGEST",
            "$.compilation.execution_plan.plan_digest",
            "execution plan digest mismatch",
        );
    }
}

fn validate_compile_report(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let allowed: BTreeSet<&str> = [
        "provider_calls",
        "validation_rejections",
        "actual_cost_microunits",
        "fallback_events",
        "decoded_pixels",
        "audio_seconds_processed",
        "candidate_bytes_repaired",
    ]
    .into_iter()
    .collect();
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    let Some(compile_report) = capsule.get("compile_report").and_then(Value::as_object) else {
        return;
    };
    for key in compile_report.keys() {
        if !allowed.contains(key.as_str()) {
            report.push(
                "VN_COMPILE_REPORT_FIELD",
                format!("$.capsule.compile_report.{key}"),
                "undeclared compile-report field",
            );
        }
    }
    for key in [
        "provider_calls",
        "validation_rejections",
        "actual_cost_microunits",
        "fallback_events",
    ] {
        if compile_report.get(key).and_then(Value::as_u64).is_none() {
            report.push(
                "VN_COMPILE_REPORT_COUNTER",
                format!("$.capsule.compile_report.{key}"),
                "required non-negative counter missing",
            );
        }
    }
}

fn validate_reviews_and_extensions(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    for (index, evidence) in array_at(capsule, "evidences").iter().enumerate() {
        let Some(evidence) = evidence.as_object() else {
            continue;
        };
        if let Some(review) = evidence.get("review").and_then(Value::as_object) {
            if string_at(review, "status") == Some("verified") {
                for key in ["reviewer", "method", "reviewed_at", "reviewed_digest"] {
                    if !review.contains_key(key) {
                        report.push(
                            "VN_REVIEW_ATTESTATION",
                            format!("$.capsule.evidences[{index}].review.{key}"),
                            "verified state requires a complete attestation",
                        );
                    }
                }
                let expected = digest_object_without(evidence, "review").ok();
                if expected.as_deref() != string_at(review, "reviewed_digest") {
                    report.push(
                        "VN_REVIEW_DIGEST",
                        format!("$.capsule.evidences[{index}].review.reviewed_digest"),
                        "reviewed digest does not bind the current Evidence",
                    );
                }
            }
        }
        if let Some(quality) = evidence.get("quality").and_then(Value::as_object) {
            if string_at(quality, "calibration_status") == Some("calibrated") {
                for key in ["method", "dataset_version"] {
                    if !quality.contains_key(key) {
                        report.push(
                            "VN_CALIBRATION_METADATA",
                            format!("$.capsule.evidences[{index}].quality.{key}"),
                            "calibrated state requires method and dataset version",
                        );
                    }
                }
            }
        }
        for (extension_index, extension) in array_at(evidence, "extensions").iter().enumerate() {
            let Some(extension) = extension.as_object() else {
                report.push(
                    "VN_EXTENSION_ENVELOPE",
                    format!("$.capsule.evidences[{index}].extensions[{extension_index}]"),
                    "extension must be an object",
                );
                continue;
            };
            for key in [
                "schema_id",
                "schema_version",
                "payload_digest",
                "content_ref",
            ] {
                if !extension.contains_key(key) {
                    report.push(
                        "VN_EXTENSION_ENVELOPE",
                        format!("$.capsule.evidences[{index}].extensions[{extension_index}].{key}"),
                        "versioned extension envelope field missing",
                    );
                }
            }
        }
    }
}

fn validate_completeness(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    let status = string_at(capsule, "status");
    let Some(completeness) = capsule.get("completeness").and_then(Value::as_object) else {
        return;
    };
    if status != string_at(completeness, "status") {
        report.push(
            "VN_COMPLETENESS_STATUS",
            "$.capsule.completeness.status",
            "capsule and completeness status differ",
        );
    }
    let gaps = capsule
        .get("diagnostics")
        .and_then(Value::as_object)
        .map(|value| array_at(value, "gaps"))
        .unwrap_or(&[]);
    let unsupported = array_at(completeness, "unsupported_modalities");
    let gap_ids = array_at(completeness, "gap_ids");
    let evidences = array_at(capsule, "evidences");
    let knowledge = capsule.get("knowledge").and_then(Value::as_object);
    let claims = knowledge
        .map(|value| array_at(value, "claims"))
        .unwrap_or(&[]);
    let concepts = knowledge
        .map(|value| array_at(value, "concepts"))
        .unwrap_or(&[]);
    if status == Some("complete")
        && (!gaps.is_empty() || !gap_ids.is_empty() || !unsupported.is_empty())
    {
        report.push(
            "VN_COMPLETENESS_COMPLETE",
            "$.capsule",
            "complete capsule contains gaps or unsupported modalities",
        );
    }
    if status == Some("empty")
        && (!evidences.is_empty() || !claims.is_empty() || !concepts.is_empty())
    {
        report.push(
            "VN_COMPLETENESS_EMPTY",
            "$.capsule",
            "empty capsule contains knowledge entities",
        );
    }
}

fn validate_anchors(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let mut refs: BTreeMap<String, (String, String, String)> = BTreeMap::new();
    for (manifest_index, manifest) in array_at(bundle, "anchor_manifests").iter().enumerate() {
        let Some(manifest) = manifest.as_object() else {
            continue;
        };
        let id = string_at(manifest, "anchor_manifest_id").unwrap_or_default();
        let revision = string_at(manifest, "source_revision_id").unwrap_or_default();
        let normalization = string_at(manifest, "normalization_profile_digest").unwrap_or_default();
        let expected = digest_object_without(manifest, "manifest_digest");
        let actual = string_at(manifest, "manifest_digest");
        if expected.ok().as_deref() != actual {
            report.push(
                "VN_ANCHOR_MANIFEST_DIGEST",
                format!("$.anchor_manifests[{manifest_index}].manifest_digest"),
                "anchor manifest digest mismatch",
            );
        }
        refs.insert(
            id.to_owned(),
            (
                revision.to_owned(),
                actual.unwrap_or_default().to_owned(),
                normalization.to_owned(),
            ),
        );
        for (anchor_index, anchor) in array_at(manifest, "anchors").iter().enumerate() {
            let Some(anchor) = anchor.as_object() else {
                continue;
            };
            match string_at(anchor, "anchor_type") {
                Some("audio_window") if !valid_digest(string_at(anchor, "content_digest")) => {
                    report.push("VN_AUDIO_CONTENT_BINDING", format!("$.anchor_manifests[{manifest_index}].anchors[{anchor_index}].content_digest"), "audio window must bind normalized audio bytes");
                }
                Some("page_region") => {
                    if let Some(rect) = anchor.get("region").and_then(Value::as_object) {
                        let x = rect.get("x").and_then(Value::as_f64).unwrap_or(2.0);
                        let y = rect.get("y").and_then(Value::as_f64).unwrap_or(2.0);
                        let width = rect.get("width").and_then(Value::as_f64).unwrap_or(2.0);
                        let height = rect.get("height").and_then(Value::as_f64).unwrap_or(2.0);
                        if x < 0.0
                            || y < 0.0
                            || width < 0.0
                            || height < 0.0
                            || x + width > 1.0
                            || y + height > 1.0
                        {
                            report.push("VN_PAGE_REGION_BOUNDS", format!("$.anchor_manifests[{manifest_index}].anchors[{anchor_index}].region"), "page region exceeds normalized page bounds");
                        }
                    }
                }
                _ => {}
            }
        }
    }
    for container_name in ["compilation", "capsule"] {
        let references = if container_name == "compilation" {
            bundle
                .get("compilation")
                .and_then(Value::as_object)
                .and_then(|value| value.get("execution_plan"))
                .and_then(Value::as_object)
                .map(|value| array_at(value, "anchor_manifest_refs"))
                .unwrap_or(&[])
        } else {
            bundle
                .get("capsule")
                .and_then(Value::as_object)
                .map(|value| array_at(value, "anchor_manifest_refs"))
                .unwrap_or(&[])
        };
        for (index, reference) in references.iter().enumerate() {
            let Some(reference) = reference.as_object() else {
                continue;
            };
            let id = string_at(reference, "anchor_manifest_id").unwrap_or_default();
            let actual = (
                string_at(reference, "source_revision_id").unwrap_or_default(),
                string_at(reference, "manifest_digest").unwrap_or_default(),
                string_at(reference, "normalization_profile_digest").unwrap_or_default(),
            );
            let matches = refs.get(id).map(|expected| {
                expected.0 == actual.0 && expected.1 == actual.1 && expected.2 == actual.2
            });
            if matches != Some(true) {
                report.push(
                    "VN_ANCHOR_MANIFEST_REF",
                    format!("$.{container_name}.anchor_manifest_refs[{index}]"),
                    "pinned anchor manifest reference does not resolve exactly",
                );
            }
        }
    }
}

fn range_domain(value: &Map<String, Value>) -> Option<(String, String, String)> {
    Some((
        string_at(value, "source_revision_id")?.to_owned(),
        string_at(value, "track_id").unwrap_or_default().to_owned(),
        string_at(value, "coordinate_kind")?.to_owned(),
    ))
}

fn range_bounds(value: &Map<String, Value>) -> Option<(i64, i64)> {
    match string_at(value, "coordinate_kind")? {
        "time" => Some((
            value.get("start_us")?.as_i64()?,
            value.get("end_us")?.as_i64()?,
        )),
        "pages" => Some((
            value.get("start_page")?.as_i64()?,
            value.get("end_page")?.as_i64()?,
        )),
        "text_bytes" => Some((
            value.get("start_byte")?.as_i64()?,
            value.get("end_byte")?.as_i64()?,
        )),
        "structural" => None,
        _ => None,
    }
}

fn validate_ranges(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    let Some(completeness) = capsule.get("completeness").and_then(Value::as_object) else {
        return;
    };
    for group in ["target_ranges", "covered_ranges"] {
        let mut prior: BTreeMap<(String, String, String), (i64, i64)> = BTreeMap::new();
        let mut last_sort_key: Option<((String, String, String), i64)> = None;
        for (index, range) in array_at(completeness, group).iter().enumerate() {
            let Some(range) = range.as_object() else {
                continue;
            };
            let Some(domain) = range_domain(range) else {
                report.push(
                    "VN_SOURCE_RANGE_REVISION",
                    format!("$.capsule.completeness.{group}[{index}]"),
                    "source range must identify its source revision and coordinate kind",
                );
                continue;
            };
            if let Some((start, end)) = range_bounds(range) {
                if start > end {
                    report.push(
                        "VN_SOURCE_RANGE_ORDER",
                        format!("$.capsule.completeness.{group}[{index}]"),
                        "range start exceeds end",
                    );
                }
                let sort_key = (domain.clone(), start);
                if last_sort_key
                    .as_ref()
                    .is_some_and(|prior_key| prior_key > &sort_key)
                {
                    report.push(
                        "VN_SOURCE_RANGE_CANONICAL",
                        format!("$.capsule.completeness.{group}"),
                        "ranges are not in canonical order",
                    );
                }
                if let Some((_, prior_end)) = prior.get(&domain) {
                    if start <= *prior_end + 1 {
                        report.push(
                            "VN_SOURCE_RANGE_CANONICAL",
                            format!("$.capsule.completeness.{group}[{index}]"),
                            "ranges overlap or were not maximally merged",
                        );
                    }
                }
                prior.insert(domain.clone(), (start, end));
                last_sort_key = Some(sort_key);
            }
        }
    }
    if let Some(diagnostics) = capsule.get("diagnostics").and_then(Value::as_object) {
        for (index, gap) in array_at(diagnostics, "gaps").iter().enumerate() {
            if let Some(range) = gap.get("source_range").and_then(Value::as_object) {
                if range_domain(range).is_none() {
                    report.push(
                        "VN_SOURCE_RANGE_REVISION",
                        format!("$.capsule.diagnostics.gaps[{index}].source_range"),
                        "gap range must identify its source revision",
                    );
                }
            }
        }
    }
}

fn validate_artifact_lineage(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    for (artifact_index, artifact) in array_at(bundle, "artifacts").iter().enumerate() {
        let Some(artifact) = artifact.as_object() else {
            continue;
        };
        for (line_index, line) in array_at(artifact, "lineage").iter().enumerate() {
            let Some(line) = line.as_object() else {
                continue;
            };
            let Some(locator) = line.get("locator").and_then(Value::as_object) else {
                report.push(
                    "VN_ARTIFACT_LOCATOR",
                    format!("$.artifacts[{artifact_index}].lineage[{line_index}]"),
                    "artifact fragment lineage requires a locator",
                );
                continue;
            };
            if string_at(locator, "kind") == Some("byte_range") {
                let start = locator.get("start_byte").and_then(Value::as_u64);
                let end = locator.get("end_byte").and_then(Value::as_u64);
                if start.is_none() || end.is_none() || start > end {
                    report.push(
                        "VN_ARTIFACT_LOCATOR",
                        format!("$.artifacts[{artifact_index}].lineage[{line_index}].locator"),
                        "invalid artifact byte range",
                    );
                }
            }
        }
    }
}

fn classification_rank(value: &str) -> i32 {
    match value {
        "public" => 0,
        "private" => 1,
        "confidential" => 2,
        "restricted" => 3,
        _ => 99,
    }
}

fn scope_rank(value: &str) -> i32 {
    match value {
        "public" => 0,
        "organization" => 1,
        "private" => 2,
        _ => 99,
    }
}

fn validate_policy(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let mut required_classification = 0;
    let mut required_scope = 0;
    let mut embedded_allowed = true;
    for source in array_at(bundle, "sources") {
        let Some(revision) = source.get("revision").and_then(Value::as_object) else {
            continue;
        };
        required_classification = required_classification.max(classification_rank(
            string_at(revision, "privacy_classification").unwrap_or("restricted"),
        ));
        let Some(rights) = revision.get("rights_profile").and_then(Value::as_object) else {
            report.push(
                "VN_RIGHTS_PROFILE",
                "$.sources[].revision.rights_profile",
                "rights profile is required",
            );
            continue;
        };
        required_scope = required_scope.max(scope_rank(
            string_at(rights, "sharing_scope").unwrap_or("private"),
        ));
        embedded_allowed &= rights
            .get("excerpt_export_allowed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    }
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    let Some(policy) = capsule
        .get("effective_access_policy")
        .and_then(Value::as_object)
    else {
        return;
    };
    if classification_rank(string_at(policy, "classification").unwrap_or("public"))
        < required_classification
        || scope_rank(string_at(policy, "sharing_scope").unwrap_or("public")) < required_scope
        || (policy
            .get("embedded_source_export_allowed")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            && !embedded_allowed)
    {
        report.push(
            "VN_ACCESS_POLICY_ESCALATION",
            "$.capsule.effective_access_policy",
            "effective policy is less restrictive than an input source",
        );
    }
    if digest_object_without(policy, "policy_digest")
        .ok()
        .as_deref()
        != string_at(policy, "policy_digest")
    {
        report.push(
            "VN_ACCESS_POLICY_DIGEST",
            "$.capsule.effective_access_policy.policy_digest",
            "policy digest mismatch",
        );
    }
    for (index, artifact) in array_at(bundle, "artifacts").iter().enumerate() {
        if artifact.get("effective_access_policy") != capsule.get("effective_access_policy") {
            report.push(
                "VN_ACCESS_POLICY_ARTIFACT",
                format!("$.artifacts[{index}].effective_access_policy"),
                "artifact did not inherit Capsule effective policy",
            );
        }
    }
}

fn collect_ids(bundle: &Map<String, Value>, report: &mut ValidationReport) -> BTreeSet<String> {
    let mut ids = BTreeSet::new();
    let mut insert = |id: Option<&str>, path: String| {
        if let Some(id) = id {
            if !ids.insert(id.to_owned()) {
                report.push("VN_DUPLICATE_ID", path, format!("duplicate entity id {id}"));
            }
        }
    };
    for (index, source) in array_at(bundle, "sources").iter().enumerate() {
        if let Some(source_obj) = source.get("source").and_then(Value::as_object) {
            insert(
                string_at(source_obj, "source_id"),
                format!("$.sources[{index}].source.source_id"),
            );
        }
        if let Some(revision) = source.get("revision").and_then(Value::as_object) {
            insert(
                string_at(revision, "source_revision_id"),
                format!("$.sources[{index}].revision.source_revision_id"),
            );
            for (track_index, track) in array_at(revision, "tracks").iter().enumerate() {
                insert(
                    track.get("track_id").and_then(Value::as_str),
                    format!("$.sources[{index}].revision.tracks[{track_index}].track_id"),
                );
            }
        }
    }
    for (manifest_index, manifest) in array_at(bundle, "anchor_manifests").iter().enumerate() {
        let Some(manifest) = manifest.as_object() else {
            continue;
        };
        insert(
            string_at(manifest, "anchor_manifest_id"),
            format!("$.anchor_manifests[{manifest_index}].anchor_manifest_id"),
        );
        for (anchor_index, anchor) in array_at(manifest, "anchors").iter().enumerate() {
            insert(
                anchor.get("anchor_id").and_then(Value::as_str),
                format!("$.anchor_manifests[{manifest_index}].anchors[{anchor_index}].anchor_id"),
            );
        }
    }
    if let Some(compilation) = bundle.get("compilation").and_then(Value::as_object) {
        insert(
            string_at(compilation, "compilation_id"),
            "$.compilation.compilation_id".to_owned(),
        );
        if let Some(plan) = compilation.get("execution_plan").and_then(Value::as_object) {
            insert(
                string_at(plan, "plan_id"),
                "$.compilation.execution_plan.plan_id".to_owned(),
            );
        }
    }
    if let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) {
        insert(
            string_at(capsule, "capsule_id"),
            "$.capsule.capsule_id".to_owned(),
        );
        for collection in ["evidences"] {
            for (index, item) in array_at(capsule, collection).iter().enumerate() {
                insert(
                    item.get("evidence_id").and_then(Value::as_str),
                    format!("$.capsule.{collection}[{index}]"),
                );
            }
        }
        if let Some(knowledge) = capsule.get("knowledge").and_then(Value::as_object) {
            for (name, id_field) in [("claims", "claim_id"), ("concepts", "concept_id")] {
                for (index, item) in array_at(knowledge, name).iter().enumerate() {
                    insert(
                        item.get(id_field).and_then(Value::as_str),
                        format!("$.capsule.knowledge.{name}[{index}]"),
                    );
                }
            }
        }
        if let Some(diagnostics) = capsule.get("diagnostics").and_then(Value::as_object) {
            for (name, id_field) in [("diagnostics", "diagnostic_id"), ("gaps", "gap_id")] {
                for (index, item) in array_at(diagnostics, name).iter().enumerate() {
                    insert(
                        item.get(id_field).and_then(Value::as_str),
                        format!("$.capsule.diagnostics.{name}[{index}]"),
                    );
                }
            }
        }
    }
    for (index, artifact) in array_at(bundle, "artifacts").iter().enumerate() {
        insert(
            artifact.get("artifact_id").and_then(Value::as_str),
            format!("$.artifacts[{index}].artifact_id"),
        );
    }
    ids
}

fn provenance_arrays(bundle: &Map<String, Value>) -> Vec<(&'static str, &[Value])> {
    let mut arrays = Vec::new();
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return arrays;
    };
    arrays.push(("$.capsule.provenance", array_at(capsule, "provenance")));
    for evidence in array_at(capsule, "evidences") {
        if let Some(object) = evidence.as_object() {
            arrays.push((
                "$.capsule.evidences[].provenance",
                array_at(object, "provenance"),
            ));
        }
    }
    if let Some(knowledge) = capsule.get("knowledge").and_then(Value::as_object) {
        for name in ["claims", "concepts"] {
            for item in array_at(knowledge, name) {
                if let Some(object) = item.as_object() {
                    arrays.push((
                        "$.capsule.knowledge[].provenance",
                        array_at(object, "provenance"),
                    ));
                }
            }
        }
    }
    arrays
}

fn validate_cross_references(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let ids = collect_ids(bundle, report);
    for (path, records) in provenance_arrays(bundle) {
        for record in records {
            let Some(record) = record.as_object() else {
                continue;
            };
            for id in array_at(record, "input_entity_ids")
                .iter()
                .filter_map(Value::as_str)
            {
                if !ids.contains(id) {
                    report.push(
                        "VN_PROVENANCE_DANGLING",
                        path,
                        format!("unknown provenance input {id}"),
                    );
                }
            }
        }
    }
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    if let Some(diagnostics) = capsule.get("diagnostics").and_then(Value::as_object) {
        let diagnostic_ids: BTreeSet<&str> = array_at(diagnostics, "diagnostics")
            .iter()
            .filter_map(|value| value.get("diagnostic_id").and_then(Value::as_str))
            .collect();
        for diagnostic in array_at(diagnostics, "diagnostics") {
            if let Some(diagnostic) = diagnostic.as_object() {
                for id in array_at(diagnostic, "related_entity_ids")
                    .iter()
                    .filter_map(Value::as_str)
                {
                    if !ids.contains(id) {
                        report.push(
                            "VN_DIAGNOSTIC_DANGLING",
                            "$.capsule.diagnostics.diagnostics[].related_entity_ids",
                            format!("unknown related entity {id}"),
                        );
                    }
                }
            }
        }
        for gap in array_at(diagnostics, "gaps") {
            if let Some(gap) = gap.as_object() {
                for id in array_at(gap, "diagnostic_ids")
                    .iter()
                    .filter_map(Value::as_str)
                {
                    if !diagnostic_ids.contains(id) {
                        report.push(
                            "VN_GAP_DIAGNOSTIC_DANGLING",
                            "$.capsule.diagnostics.gaps[].diagnostic_ids",
                            format!("unknown diagnostic {id}"),
                        );
                    }
                }
            }
        }
    }
    for (artifact_index, artifact) in array_at(bundle, "artifacts").iter().enumerate() {
        let Some(artifact) = artifact.as_object() else {
            continue;
        };
        for line in array_at(artifact, "lineage") {
            let Some(line) = line.as_object() else {
                continue;
            };
            for id in array_at(line, "entity_ids")
                .iter()
                .filter_map(Value::as_str)
            {
                if !ids.contains(id) {
                    report.push(
                        "VN_ARTIFACT_DANGLING",
                        format!("$.artifacts[{artifact_index}].lineage"),
                        format!("unknown lineage entity {id}"),
                    );
                }
            }
        }
    }
}

fn validate_external_references(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let mut external = BTreeMap::new();
    for dependency in array_at(bundle, "external_dependencies") {
        let Some(dependency) = dependency.as_object() else {
            continue;
        };
        let capsule_id = string_at(dependency, "capsule_id").unwrap_or_default();
        let policy_digest = string_at(dependency, "access_policy_digest").unwrap_or_default();
        for entity in array_at(dependency, "entities") {
            let Some(entity) = entity.as_object() else {
                continue;
            };
            external.insert(
                (
                    capsule_id.to_owned(),
                    string_at(entity, "entity_id")
                        .unwrap_or_default()
                        .to_owned(),
                ),
                (
                    string_at(entity, "entity_digest")
                        .unwrap_or_default()
                        .to_owned(),
                    policy_digest.to_owned(),
                ),
            );
        }
    }
    let Some(capsule) = bundle.get("capsule").and_then(Value::as_object) else {
        return;
    };
    let Some(knowledge) = capsule.get("knowledge").and_then(Value::as_object) else {
        return;
    };
    for claim in array_at(knowledge, "claims") {
        let Some(claim) = claim.as_object() else {
            continue;
        };
        for reference in array_at(claim, "external_refs") {
            let Some(reference) = reference.as_object() else {
                continue;
            };
            let key = (
                string_at(reference, "capsule_id")
                    .unwrap_or_default()
                    .to_owned(),
                string_at(reference, "entity_id")
                    .unwrap_or_default()
                    .to_owned(),
            );
            let Some(actual) = external.get(&key) else {
                report.push(
                    "VN_EXTERNAL_MISSING",
                    "$.capsule.knowledge.claims[].external_refs",
                    "external entity is not pinned by a dependency manifest",
                );
                continue;
            };
            if string_at(reference, "expected_digest") != Some(actual.0.as_str()) {
                report.push(
                    "VN_EXTERNAL_DIGEST",
                    "$.capsule.knowledge.claims[].external_refs",
                    "external entity digest mismatch",
                );
            }
            if let Some(required) = string_at(reference, "required_access_policy_digest") {
                if required != actual.1 {
                    report.push(
                        "VN_EXTERNAL_POLICY",
                        "$.capsule.knowledge.claims[].external_refs",
                        "external access-policy digest mismatch",
                    );
                }
            }
        }
    }
}

fn validate_provider_manifests(bundle: &Map<String, Value>, report: &mut ValidationReport) {
    let mut digests = BTreeSet::new();
    for (index, provider) in array_at(bundle, "provider_manifests").iter().enumerate() {
        let Some(provider) = provider.as_object() else {
            continue;
        };
        let expected = digest_object_without(provider, "manifest_digest");
        let actual = string_at(provider, "manifest_digest");
        if expected.ok().as_deref() != actual {
            report.push(
                "VN_PROVIDER_MANIFEST_DIGEST",
                format!("$.provider_manifests[{index}].manifest_digest"),
                "provider manifest digest mismatch",
            );
        }
        if let Some(actual) = actual {
            digests.insert(actual.to_owned());
        }
    }
    let planned: BTreeSet<String> = bundle
        .get("compilation")
        .and_then(Value::as_object)
        .and_then(|value| value.get("execution_plan"))
        .and_then(Value::as_object)
        .map(|value| array_at(value, "provider_manifest_digests"))
        .unwrap_or(&[])
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    if planned != digests {
        report.push(
            "VN_PROVIDER_MANIFEST_PIN",
            "$.compilation.execution_plan.provider_manifest_digests",
            "execution plan does not pin the exact provider manifest set",
        );
    }
}

/// Serialize a validated `ExchangeBundle` to canonical (VN-C14N-1) JSON bytes.
///
/// This is the v0.2 versioned writer: it produces deterministic JSON output
/// suitable for storage, exchange, or signature computation.
pub fn write_bundle(bundle: &ExchangeBundle) -> Result<Vec<u8>, String> {
    let value = serde_json::to_value(bundle).map_err(|error| error.to_string())?;
    canonical_bytes(&value)
}
