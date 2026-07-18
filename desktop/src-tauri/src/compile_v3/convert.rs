//! Conservative VideoCapsule → v0.2 ExchangeBundle conversion.
#![cfg_attr(not(test), allow(dead_code))]

use chrono::DateTime;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::canonical;
use super::ir::{
    AccessPolicy, Capsule, Compilation, CompileReport, ExchangeBundle, ExchangeManifest,
    ExchangeSignature, ExecutionPlan, RightsProfile, Source, SourceEntry, SourceRevision, Track,
    SPEC_VERSION,
};
use crate::compile::VideoCapsule;

/// Convert a legacy product-v2.1 capsule without inventing rights, source-byte
/// bindings, review attestations, or signing authority.
///
/// Legacy `Evidence` records do not carry the normalized frame/audio digests
/// required by v0.2 anchors. They are therefore retained as unsupported Claims
/// with explicit compilation Gaps, rather than being mislabeled as observable
/// v0.2 Evidence.
pub fn convert(legacy: &VideoCapsule) -> Result<ExchangeBundle, String> {
    validate_legacy_capsule(legacy)?;

    let source_hash = legacy.source_hash.to_ascii_lowercase();
    let source_digest = format!("sha256:{source_hash}");
    let source_id = entity_id("src", &source_hash);
    let revision_id = entity_id("rev", &source_hash);
    let track_id = entity_id("trk", &format!("{source_hash}:video"));
    let compilation_id = entity_id("cmp", &legacy.capsule_id);
    let capsule_id = entity_id("cap", &legacy.capsule_id);
    let plan_id = entity_id("plan", &legacy.capsule_id);
    let bundle_id = entity_id("bundle", &legacy.capsule_id);
    let duration_us = seconds_to_us(legacy.total_duration, "total_duration")?;
    let created_at = legacy.processed_at.clone();

    let origin_history = (!legacy.source_input.trim().is_empty())
        .then(|| legacy.source_input.clone())
        .into_iter()
        .collect::<Vec<_>>();
    let origin = if legacy.source_input.starts_with("http://")
        || legacy.source_input.starts_with("https://")
    {
        json!({
            "kind": "remote_url",
            "uri_redacted": redact_url(&legacy.source_input),
        })
    } else if !legacy.source_input.trim().is_empty() {
        json!({
            "kind": "local_file",
            "display_name": source_display_name(&legacy.source_input),
        })
    } else {
        json!({
            "kind": "imported_bundle",
            "display_name": "legacy VideoCapsule v2.1",
        })
    };

    let source = SourceEntry {
        source: Source {
            source_id: source_id.clone(),
            title: legacy.source_title.trim().to_string(),
            created_at: created_at.clone(),
            origin_history,
        },
        revision: SourceRevision {
            source_revision_id: revision_id.clone(),
            source_id,
            content_digest: source_digest,
            byte_length: 0,
            media_type: "video/mp4".to_string(),
            acquired_at: created_at.clone(),
            origin,
            privacy_classification: "private".to_string(),
            tracks: vec![Track {
                track_id: track_id.clone(),
                track_type: "video".to_string(),
                codec_or_format: None,
                duration_us: Some(duration_us),
                language: None,
            }],
            rights_profile: RightsProfile {
                basis: "unknown".to_string(),
                license_identifier: None,
                consent_record_digest: None,
                transform_allowed: false,
                excerpt_export_allowed: false,
                sharing_scope: "private".to_string(),
                expires_at: None,
            },
        },
    };

    let mut claims = Vec::with_capacity(legacy.evidences.len());
    let mut gaps = Vec::with_capacity(legacy.evidences.len().max(1));
    let mut diagnostics = Vec::new();
    let mut gap_ids = Vec::new();
    let mut provenance_inputs = vec![revision_id.clone(), compilation_id.clone()];

    for (index, evidence) in legacy.evidences.iter().enumerate() {
        let start_us = seconds_to_us(
            evidence.timestamp_start_sec,
            &format!("evidences[{index}].timestamp_start_sec"),
        )?;
        let end_us = seconds_to_us(
            evidence.timestamp_end_sec,
            &format!("evidences[{index}].timestamp_end_sec"),
        )?;
        if start_us > end_us || end_us > duration_us {
            return Err(format!(
                "evidences[{index}] has an invalid time range {start_us}..{end_us} for duration {duration_us}"
            ));
        }

        let claim_id = entity_id(
            "clm",
            &format!("{}:{index}:{}", legacy.capsule_id, evidence.id),
        );
        let gap_id = entity_id("gap", &format!("{}:{index}:anchor", legacy.capsule_id));
        let diagnostic_id = entity_id("diag", &format!("{}:{index}:anchor", legacy.capsule_id));
        let mut review_reasons = vec!["invalid_or_weak_anchor", "uncalibrated_model_score"];
        if evidence.speaker.is_some() {
            review_reasons.push("speaker_uncertain");
        }
        let parameters_digest = digest_json(&json!({
            "legacy_evidence_id": evidence.id,
            "chunk_sequence": evidence.chunk_sequence,
            "confidence": evidence.confidence,
            "evidence_type": format!("{:?}", evidence.evidence_type),
        }))?;

        claims.push(json!({
            "claim_id": claim_id,
            "statement": evidence.content,
            "claim_kind": "compiler_inference",
            "status": "unsupported",
            "evidence_relations": [],
            "review": {
                "status": "needs_review",
                "reasons": review_reasons,
            },
            "provenance": [{
                "producer_kind": "model_pass",
                "producer_id": "legacy.video-capsule.evidence",
                "producer_version": "2.1.0",
                "model": {
                    "provider": "legacy",
                    "model_id": truncate_chars(&legacy.model_used, 120),
                },
                "input_entity_ids": [revision_id],
                "parameters_digest": parameters_digest,
                "created_at": created_at,
            }],
        }));
        diagnostics.push(json!({
            "diagnostic_id": diagnostic_id,
            "code": "VALIDATION_LEGACY_ANCHOR_BINDING_MISSING",
            "severity": "review_required",
            "message": "Legacy evidence was retained as an unsupported claim because no normalized frame or audio content digest was available.",
            "stage": "legacy_conversion",
            "recoverability": "user_action",
            "related_entity_ids": [claim_id, gap_id],
            "created_at": created_at,
        }));
        gaps.push(json!({
            "gap_id": gap_id,
            "kind": "intentionally_excluded",
            "source_revision_id": revision_id,
            "affected_modalities": ["visual"],
            "reason_codes": ["legacy_anchor_content_binding_missing"],
            "diagnostic_ids": [diagnostic_id],
            "recoverability": "user_action",
            "source_range": {
                "source_revision_id": revision_id,
                "track_id": track_id,
                "coordinate_kind": "time",
                "start_us": start_us,
                "end_us": end_us,
            },
        }));
        gap_ids.push(gap_id.clone());
        provenance_inputs.push(claim_id);
        provenance_inputs.push(gap_id);
    }

    if legacy.evidences.is_empty() {
        let gap_id = entity_id("gap", &format!("{}:empty", legacy.capsule_id));
        gaps.push(json!({
            "gap_id": gap_id,
            "kind": "intentionally_excluded",
            "source_revision_id": revision_id,
            "affected_modalities": ["visual"],
            "reason_codes": ["legacy_capsule_has_no_bound_evidence"],
            "recoverability": "not_applicable",
            "source_range": {
                "source_revision_id": revision_id,
                "track_id": track_id,
                "coordinate_kind": "time",
                "start_us": 0,
                "end_us": duration_us,
            },
        }));
        gap_ids.push(gap_id);
    }

    for (index, warning) in legacy.warnings.iter().enumerate() {
        if warning.trim().is_empty() {
            continue;
        }
        diagnostics.push(json!({
            "diagnostic_id": entity_id("diag", &format!("{}:warning:{index}", legacy.capsule_id)),
            "code": "VALIDATION_LEGACY_WARNING",
            "severity": "warning",
            "message": truncate_chars(warning, 4000),
            "stage": "legacy_conversion",
            "recoverability": "user_action",
            "related_entity_ids": [compilation_id],
            "created_at": created_at,
        }));
    }

    let mut execution_plan = ExecutionPlan {
        plan_id,
        plan_digest: String::new(),
        required_modalities: vec!["visual".to_string()],
        passes: vec![json!({ "pass_id": "legacy_conversion", "version": "1" })],
        budget: json!({
            "max_wall_time_ms": 0,
            "max_cost_microunits": 0,
            "max_provider_input_tokens": 0,
            "max_provider_output_tokens": 0,
        }),
        anchor_manifest_refs: vec![],
        provider_manifest_digests: vec![],
    };
    execution_plan.plan_digest = digest_struct_without(&execution_plan, "plan_digest")?;

    let compilation = Compilation {
        compilation_id: compilation_id.clone(),
        source_revision_ids: vec![revision_id.clone()],
        request_digest: None,
        compilation_sequence: legacy.version as u64,
        state: "succeeded".to_string(),
        spec_version: SPEC_VERSION.to_string(),
        ir_schema_version: SPEC_VERSION.to_string(),
        compiler_build: "video-notes-ai-2.1.0-legacy-converter".to_string(),
        execution_plan,
        created_at: created_at.clone(),
        updated_at: created_at.clone(),
        capsule_id: Some(capsule_id.clone()),
        idempotency_key_digest: digest_json(&json!({
            "legacy_capsule_id": legacy.capsule_id,
            "source_hash": source_hash,
            "version": legacy.version,
        }))?,
    };

    let mut effective_access_policy = AccessPolicy {
        classification: "private".to_string(),
        sharing_scope: "private".to_string(),
        embedded_source_export_allowed: false,
        policy_digest: None,
    };
    effective_access_policy.policy_digest = Some(digest_struct_without(
        &effective_access_policy,
        "policy_digest",
    )?);

    let capsule = Capsule {
        capsule_id,
        compilation_id,
        source_revision_ids: vec![revision_id.clone()],
        compilation_sequence: legacy.version as u64,
        ir_schema_version: SPEC_VERSION.to_string(),
        status: "partial".to_string(),
        completeness: json!({
            "status": "partial",
            "target_ranges": [{
                "source_revision_id": revision_id,
                "track_id": track_id,
                "coordinate_kind": "time",
                "start_us": 0,
                "end_us": duration_us,
            }],
            "covered_ranges": [],
            "gap_ids": gap_ids,
            "unsupported_modalities": [],
        }),
        evidences: vec![],
        knowledge: json!({ "claims": claims, "concepts": [] }),
        diagnostics: json!({ "diagnostics": diagnostics, "gaps": gaps }),
        provenance: vec![json!({
            "producer_kind": "migration",
            "producer_id": "video-notes.legacy-capsule-converter",
            "producer_version": "2.1.0",
            "input_entity_ids": provenance_inputs,
            "parameters_digest": digest_json(&json!({
                "profile": "product-v2.1-to-spec-v0.2-conservative",
            }))?,
            "created_at": created_at,
        })],
        created_at: created_at.clone(),
        compile_report: Some(CompileReport {
            provider_calls: 0,
            validation_rejections: legacy.evidences.len() as u64,
            actual_cost_microunits: 0,
            fallback_events: 0,
            decoded_pixels: None,
            audio_seconds_processed: None,
            candidate_bytes_repaired: None,
        }),
        anchor_manifest_refs: vec![],
        effective_access_policy,
    };

    let mut bundle = ExchangeBundle {
        bundle_version: SPEC_VERSION.to_string(),
        sources: vec![source],
        anchor_manifests: vec![],
        compilation,
        capsule,
        artifacts: vec![],
        provider_manifests: vec![],
        external_dependencies: vec![],
        exchange_manifest: ExchangeManifest {
            bundle_id,
            canonicalization_profile: canonical::PROFILE.to_string(),
            signature_context: canonical::SIGNATURE_CONTEXT.to_string(),
            content_digest: String::new(),
            signature: ExchangeSignature {
                algorithm: "ed25519".to_string(),
                key_id: "unsigned-local-conversion".to_string(),
                public_key_base64: String::new(),
                signed_at: created_at,
                signature_base64: String::new(),
            },
        },
    };
    let mut unsigned = serde_json::to_value(&bundle).map_err(|error| error.to_string())?;
    unsigned
        .as_object_mut()
        .ok_or_else(|| "converted bundle must be an object".to_string())?
        .remove("exchange_manifest");
    bundle.exchange_manifest.content_digest = canonical::digest_value(&unsigned)?;
    Ok(bundle)
}

fn validate_legacy_capsule(capsule: &VideoCapsule) -> Result<(), String> {
    if capsule.source_hash.len() != 64
        || !capsule
            .source_hash
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("legacy source_hash must be a 64-character SHA-256 hex digest".to_string());
    }
    if capsule.version == 0 {
        return Err("legacy capsule version must start at 1".to_string());
    }
    if capsule.source_title.trim().is_empty() || capsule.source_title.chars().count() > 500 {
        return Err("legacy source_title must contain 1..=500 characters".to_string());
    }
    if capsule.capsule_id.trim().is_empty() {
        return Err("legacy capsule_id must not be empty".to_string());
    }
    DateTime::parse_from_rfc3339(&capsule.processed_at)
        .map_err(|error| format!("legacy processed_at is invalid: {error}"))?;
    seconds_to_us(capsule.total_duration, "total_duration")?;
    for (index, evidence) in capsule.evidences.iter().enumerate() {
        if evidence.content.trim().is_empty() || evidence.content.chars().count() > 50_000 {
            return Err(format!(
                "evidences[{index}].content must contain 1..=50000 characters"
            ));
        }
        if !evidence.confidence.is_finite() || !(0.0..=1.0).contains(&evidence.confidence) {
            return Err(format!("evidences[{index}].confidence is outside 0..=1"));
        }
    }
    Ok(())
}

fn seconds_to_us(seconds: f32, field: &str) -> Result<u64, String> {
    if !seconds.is_finite() || seconds < 0.0 {
        return Err(format!("{field} must be a finite non-negative number"));
    }
    let micros = f64::from(seconds) * 1_000_000.0;
    if micros > u64::MAX as f64 {
        return Err(format!("{field} exceeds the supported time range"));
    }
    Ok(micros.round() as u64)
}

fn entity_id(prefix: &str, seed: &str) -> String {
    let digest = format!("{:x}", Sha256::digest(seed.as_bytes()));
    format!("{prefix}_{}", &digest[..40])
}

fn digest_json(value: &Value) -> Result<String, String> {
    canonical::digest_value(value)
}

fn digest_struct_without<T: serde::Serialize>(value: &T, excluded: &str) -> Result<String, String> {
    let value = serde_json::to_value(value).map_err(|error| error.to_string())?;
    let object = value
        .as_object()
        .ok_or_else(|| "digest target must be an object".to_string())?;
    canonical::digest_object_without(object, excluded)
}

fn truncate_chars(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

fn source_display_name(input: &str) -> String {
    std::path::Path::new(input)
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| truncate_chars(name, 500))
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "legacy source".to_string())
}

fn redact_url(input: &str) -> String {
    input
        .split(['?', '#'])
        .next()
        .map(|value| truncate_chars(value, 2000))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile::{CompileMode, Evidence, EvidenceType};

    fn dummy_capsule() -> VideoCapsule {
        VideoCapsule {
            ir_schema_version: 2,
            capsule_id: "短标识符-✓".to_string(),
            source_hash: "aabbccddee00112233445566778899aabbccddee00112233445566778899aabb"
                .to_string(),
            source_title: "Test Video".to_string(),
            version: 1,
            total_duration: 120.0,
            processed_at: "2026-07-13T10:00:00Z".to_string(),
            model_used: "mimo-v2.5".to_string(),
            evidences: vec![Evidence {
                id: "短证据".to_string(),
                source_hash: "test_hash".to_string(),
                version: 1,
                chunk_sequence: 0,
                content: "This is a legacy model interpretation.".to_string(),
                timestamp_start_sec: 0.0,
                timestamp_end_sec: 30.0,
                evidence_type: EvidenceType::Fact,
                speaker: Some("Instructor".to_string()),
                confidence: 0.85,
                visual_context: "Test Context".to_string(),
                prev_chunk_summary_hash: None,
                is_redundant: false,
                needs_review: false,
                review_reasons: vec![],
            }],
            global_summary: "[Chunk 0] Test summary.".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
            warnings: vec![],
            source_input: String::new(),
        }
    }

    #[test]
    fn converted_bundle_is_conservative_and_retains_legacy_knowledge() {
        let bundle = convert(&dummy_capsule()).expect("conversion succeeds");

        assert_eq!(bundle.bundle_version, SPEC_VERSION);
        assert_eq!(bundle.sources.len(), 1);
        assert_eq!(bundle.sources[0].revision.rights_profile.basis, "unknown");
        assert!(!bundle.sources[0].revision.rights_profile.transform_allowed);
        assert!(bundle.anchor_manifests.is_empty());
        assert!(bundle.capsule.evidences.is_empty());
        assert_eq!(
            bundle.capsule.knowledge["claims"].as_array().unwrap().len(),
            1
        );
        assert_eq!(
            bundle.capsule.diagnostics["gaps"].as_array().unwrap().len(),
            1
        );
        assert_eq!(bundle.capsule.status, "partial");
        assert!(bundle
            .exchange_manifest
            .content_digest
            .starts_with("sha256:"));
    }

    #[test]
    fn converted_bundle_writes_to_canonical_json_without_panicking_on_unicode_ids() {
        let bundle = convert(&dummy_capsule()).expect("conversion succeeds");
        let bytes = crate::compile_v3::validate::write_bundle(&bundle)
            .expect("write_bundle should succeed");
        assert!(!bytes.is_empty());
    }

    #[test]
    fn conversion_rejects_invalid_physical_ranges() {
        let mut capsule = dummy_capsule();
        capsule.evidences[0].timestamp_end_sec = 121.0;
        assert!(convert(&capsule).is_err());
    }
}
