//! Spec v0.2 exchange types.
#![cfg_attr(not(test), allow(dead_code))]
// compiler_v3: off-by-default experimental module; items referenced only by conformance tests are unreachable in non-test bin builds
//!
//! The reference implementation intentionally keeps some evolving leaf records as
//! `serde_json::Value` while the stable identity, policy, manifest, and bundle
//! boundaries are strongly typed. This preserves unknown *future* records only at
//! explicitly versioned boundaries; validation still rejects undeclared v0.2 fields.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const SPEC_VERSION: &str = "0.2.0-rc.3";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExchangeBundle {
    pub bundle_version: String,
    pub sources: Vec<SourceEntry>,
    pub anchor_manifests: Vec<AnchorManifest>,
    pub compilation: Compilation,
    pub capsule: Capsule,
    pub artifacts: Vec<Artifact>,
    pub provider_manifests: Vec<ProviderManifest>,
    pub external_dependencies: Vec<ExternalDependency>,
    pub exchange_manifest: ExchangeManifest,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceEntry {
    pub source: Source,
    pub revision: SourceRevision,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub source_id: String,
    pub title: String,
    pub created_at: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub origin_history: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceRevision {
    pub source_revision_id: String,
    pub source_id: String,
    pub content_digest: String,
    pub byte_length: u64,
    pub media_type: String,
    pub acquired_at: String,
    pub origin: Value,
    pub privacy_classification: String,
    pub tracks: Vec<Track>,
    pub rights_profile: RightsProfile,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Track {
    pub track_id: String,
    pub track_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub codec_or_format: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_us: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RightsProfile {
    pub basis: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub license_identifier: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub consent_record_digest: Option<String>,
    pub transform_allowed: bool,
    pub excerpt_export_allowed: bool,
    pub sharing_scope: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnchorManifest {
    pub source_revision_id: String,
    pub anchors: Vec<Value>,
    pub anchor_manifest_id: String,
    pub manifest_digest: String,
    pub normalization_profile_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Compilation {
    pub compilation_id: String,
    pub source_revision_ids: Vec<String>,
    #[serde(default)]
    pub request_digest: Option<String>,
    pub compilation_sequence: u64,
    pub state: String,
    pub spec_version: String,
    pub ir_schema_version: String,
    pub compiler_build: String,
    pub execution_plan: ExecutionPlan,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub capsule_id: Option<String>,
    pub idempotency_key_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionPlan {
    pub plan_id: String,
    pub plan_digest: String,
    pub required_modalities: Vec<String>,
    pub passes: Vec<Value>,
    pub budget: Value,
    pub anchor_manifest_refs: Vec<AnchorManifestRef>,
    pub provider_manifest_digests: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AnchorManifestRef {
    pub source_revision_id: String,
    pub anchor_manifest_id: String,
    pub manifest_digest: String,
    pub normalization_profile_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Capsule {
    pub capsule_id: String,
    pub compilation_id: String,
    pub source_revision_ids: Vec<String>,
    pub compilation_sequence: u64,
    pub ir_schema_version: String,
    pub status: String,
    pub completeness: Value,
    pub evidences: Vec<Value>,
    pub knowledge: Value,
    pub diagnostics: Value,
    pub provenance: Vec<Value>,
    pub created_at: String,
    #[serde(default)]
    pub compile_report: Option<CompileReport>,
    pub anchor_manifest_refs: Vec<AnchorManifestRef>,
    pub effective_access_policy: AccessPolicy,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompileReport {
    pub provider_calls: u64,
    pub validation_rejections: u64,
    pub actual_cost_microunits: u64,
    pub fallback_events: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decoded_pixels: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_seconds_processed: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub candidate_bytes_repaired: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AccessPolicy {
    pub classification: String,
    pub sharing_scope: String,
    pub embedded_source_export_allowed: bool,
    #[serde(default)]
    pub policy_digest: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub artifact_id: String,
    pub artifact_type: String,
    pub artifact_schema_version: String,
    pub input_capsule_ids: Vec<String>,
    pub emitter: Value,
    #[serde(default)]
    pub generation_options_digest: Option<String>,
    pub content_digest: String,
    #[serde(default)]
    pub media_type: Option<String>,
    pub lineage: Vec<Value>,
    pub created_at: String,
    #[serde(default)]
    pub lossy_export: bool,
    pub effective_access_policy: AccessPolicy,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderManifest {
    pub manifest_id: String,
    pub provider_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub model_id: String,
    pub model_revision: String,
    pub retrieved_at: String,
    pub expires_at: String,
    pub accepted_modalities: Vec<String>,
    pub joint_reasoning: bool,
    pub structured_output: Value,
    pub limits: Value,
    #[serde(default)]
    pub retention_policy: Option<String>,
    #[serde(default)]
    pub region: Option<String>,
    pub manifest_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExternalDependency {
    pub capsule_id: String,
    pub capsule_digest: String,
    pub access_policy_digest: String,
    pub entities: Vec<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExchangeManifest {
    pub bundle_id: String,
    pub canonicalization_profile: String,
    pub signature_context: String,
    pub content_digest: String,
    pub signature: ExchangeSignature,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExchangeSignature {
    pub algorithm: String,
    pub key_id: String,
    pub public_key_base64: String,
    pub signed_at: String,
    pub signature_base64: String,
}
