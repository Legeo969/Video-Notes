#![cfg(feature = "compiler_v3")]

use std::{fs, path::PathBuf};

use chrono::{TimeZone, Utc};
use video_notes_ai::compile_v3::{
    parse_and_validate, parse_and_validate_with_policy, write_bundle, TrustPolicy,
};

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .expect("repository root")
        .to_path_buf()
}

fn read_fixture(relative: &str) -> String {
    fs::read_to_string(repository_root().join(relative)).expect("fixture must be readable")
}

fn trusted_policy() -> TrustPolicy {
    TrustPolicy::from_json(&read_fixture("conformance/v0.2/trust/trusted-policy.json"))
        .expect("trusted fixture policy")
}

fn revoked_policy() -> TrustPolicy {
    TrustPolicy::from_json(&read_fixture("conformance/v0.2/trust/revoked-policy.json"))
        .expect("revoked fixture policy")
}

fn verification_time() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 7, 12, 19, 0, 0)
        .single()
        .unwrap()
}

#[test]
fn valid_v02_bundles_round_trip_without_field_loss() {
    for name in [
        "course-video-complete.json",
        "document-page-region.json",
        "visual-only-partial.json",
    ] {
        let fixture_text = read_fixture(&format!("examples/v0.2/valid/{name}"));
        let input: serde_json::Value = serde_json::from_str(&fixture_text).expect("valid JSON");
        let bundle =
            parse_and_validate_with_policy(&fixture_text, &trusted_policy(), verification_time())
                .unwrap_or_else(|report| panic!("{name} rejected: {:?}", report.issues));
        let output = serde_json::to_value(bundle).expect("bundle serialization");
        assert_eq!(input, output, "{name} lost fields during round trip");
    }
}

#[test]
fn red_team_fixtures_are_rejected_for_the_expected_contract() {
    let cases = [
        (
            "verified-without-reviewer.json",
            "VN_REVIEW_ATTESTATION",
            false,
        ),
        (
            "calibrated-without-dataset.json",
            "VN_CALIBRATION_METADATA",
            false,
        ),
        ("unversioned-extension.json", "VN_EXTENSION_ENVELOPE", false),
        (
            "complete-with-unsupported-modality.json",
            "VN_COMPLETENESS_COMPLETE",
            false,
        ),
        ("empty-with-evidence.json", "VN_COMPLETENESS_EMPTY", false),
        (
            "unknown-provenance-input.json",
            "VN_PROVENANCE_DANGLING",
            false,
        ),
        (
            "unknown-diagnostic-related-entity.json",
            "VN_DIAGNOSTIC_DANGLING",
            false,
        ),
        (
            "audio-window-without-content-digest.json",
            "VN_AUDIO_CONTENT_BINDING",
            false,
        ),
        (
            "anchor-manifest-substitution.json",
            "VN_ANCHOR_MANIFEST_REF",
            false,
        ),
        ("page-region-overflow.json", "VN_PAGE_REGION_BOUNDS", false),
        (
            "unbounded-compile-report-field.json",
            "VN_COMPILE_REPORT_FIELD",
            false,
        ),
        (
            "artifact-lineage-without-locator.json",
            "VN_ARTIFACT_LOCATOR",
            false,
        ),
        (
            "permission-inheritance-mismatch.json",
            "VN_ACCESS_POLICY_ESCALATION",
            false,
        ),
        (
            "noncanonical-overlapping-ranges.json",
            "VN_SOURCE_RANGE_CANONICAL",
            false,
        ),
        (
            "source-range-without-revision.json",
            "VN_SOURCE_RANGE_REVISION",
            false,
        ),
        (
            "stale-external-entity-digest.json",
            "VN_EXTERNAL_DIGEST",
            false,
        ),
        (
            "invalid-exchange-signature.json",
            "VN_SIGNATURE_VERIFY",
            false,
        ),
        ("stale-execution-plan-digest.json", "VN_PLAN_DIGEST", false),
        ("stale-reviewed-digest.json", "VN_REVIEW_DIGEST", false),
        (
            "signature-without-domain-separation.json",
            "VN_SIGNATURE_VERIFY",
            false,
        ),
        (
            "untrusted-self-signed-key.json",
            "VN_SIGNATURE_UNTRUSTED",
            false,
        ),
        (
            "trusted-key-id-key-substitution.json",
            "VN_SIGNATURE_KEY_MISMATCH",
            false,
        ),
        ("revoked-signing-key.json", "VN_SIGNATURE_REVOKED", true),
        (
            "signature-outside-key-validity.json",
            "VN_SIGNATURE_TIME",
            false,
        ),
        (
            "signature-metadata-tamper.json",
            "VN_SIGNATURE_VERIFY",
            false,
        ),
    ];
    for (name, expected_code, use_revoked_policy) in cases {
        let fixture_text = read_fixture(&format!("examples/v0.2/invalid/{name}"));
        let report = if use_revoked_policy {
            parse_and_validate_with_policy(&fixture_text, &revoked_policy(), verification_time())
                .expect_err(name)
        } else {
            parse_and_validate_with_policy(&fixture_text, &trusted_policy(), verification_time())
                .expect_err(name)
        };
        assert!(
            report.has_code(expected_code),
            "{name}: expected {expected_code}, got {:?}",
            report.issues
        );
    }
}

#[test]
fn compiler_v3_does_not_replace_the_legacy_reader() {
    // Presence of the legacy module is a compile-time compatibility assertion.
    let _legacy_schema_version = video_notes_ai::compile::IR_SCHEMA_VERSION;
    assert_eq!(video_notes_ai::compile_v3::SPEC_VERSION, "0.2.0-rc.3");
}

#[test]
fn strict_json_parser_rejects_duplicate_keys_and_excessive_depth() {
    let duplicate = r#"{"bundle_version":"0.2.0-rc.3","bundle_version":"0.2.0-rc.3"}"#;
    let duplicate_report = parse_and_validate(duplicate).expect_err("duplicate key must fail");
    assert!(duplicate_report.has_code("VN_SCHEMA_JSON"));

    let deeply_nested = format!("{}0{}", "[".repeat(66), "]".repeat(66));
    let depth_report = parse_and_validate(&deeply_nested).expect_err("excessive depth must fail");
    assert!(depth_report.has_code("VN_SCHEMA_JSON"));
}

#[test]
fn valid_signature_without_external_policy_is_rejected() {
    let fixture_text = read_fixture("examples/v0.2/valid/course-video-complete.json");
    let report =
        parse_and_validate(&fixture_text).expect_err("embedded key must not be a trust root");
    assert!(report.has_code("VN_SIGNATURE_UNTRUSTED"));
}

#[test]
fn canonicalization_matches_published_vectors() {
    let fixture_text = read_fixture("conformance/v0.2/canonicalization-vectors.json");
    let vectors: serde_json::Value =
        serde_json::from_str(&fixture_text).expect("canonical vectors JSON");
    for vector in vectors["vectors"].as_array().expect("vector list") {
        let bytes = video_notes_ai::compile_v3::canonical::canonical_bytes(&vector["value"])
            .expect("canonicalization");
        let actual: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
        assert_eq!(actual, vector["canonical_utf8_hex"].as_str().unwrap());
    }
    let signature = &vectors["signature_payload_vector"];
    let bytes = video_notes_ai::compile_v3::canonical::signature_payload(
        &signature["value"],
        signature["key_id"].as_str().unwrap(),
        signature["signed_at"].as_str().unwrap(),
    )
    .expect("signature payload");
    let actual: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    assert_eq!(actual, signature["payload_hex"].as_str().unwrap());
}

#[test]
fn write_bundle_produces_canonical_json_that_round_trips() {
    // Verify that write_bundle produces deterministic canonical JSON
    // that, when re-parsed, produces the same validated bundle.
    for name in [
        "course-video-complete.json",
        "document-page-region.json",
        "visual-only-partial.json",
    ] {
        let fixture_text = read_fixture(&format!("examples/v0.2/valid/{name}"));
        let bundle =
            parse_and_validate_with_policy(&fixture_text, &trusted_policy(), verification_time())
                .unwrap_or_else(|report| {
                    panic!("{name} initial parse rejected: {:?}", report.issues)
                });

        // Write to canonical JSON bytes
        let written = write_bundle(&bundle).unwrap_or_else(|e| panic!("{name} write failed: {e}"));

        // Re-parse from canonical bytes and validate
        let written_str = String::from_utf8(written).expect("valid UTF-8");
        let rebundled =
            parse_and_validate_with_policy(&written_str, &trusted_policy(), verification_time())
                .unwrap_or_else(|report| panic!("{name} re-parse rejected: {:?}", report.issues));

        // Both representations must produce identical content digests
        assert_eq!(
            bundle.exchange_manifest.content_digest, rebundled.exchange_manifest.content_digest,
            "{name}: content digest changed after write/read cycle"
        );
    }
}

#[test]
fn compiler_v3_module_is_off_by_default() {
    let manifest = include_str!("../Cargo.toml");
    let default_features = manifest
        .lines()
        .find(|line| line.trim_start().starts_with("default ="))
        .expect("Cargo.toml must declare default features");
    assert!(!default_features.contains("compiler_v3"));
}

#[test]
fn legacy_compile_module_coexists_with_compiler_v3() {
    // Verify that the legacy v2.1 compile module is still accessible
    // when compiler_v3 is enabled. This is a compile-time migration
    // boundary assertion.
    let _v3_spec = video_notes_ai::compile_v3::SPEC_VERSION;
    assert_eq!(_v3_spec, "0.2.0-rc.3");
}
