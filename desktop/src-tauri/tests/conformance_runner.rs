//! Spec v0.2 conformance runner — manifest-driven fixture validation.
//!
//! This binary reads the fixture manifest (`conformance/v0.2/fixture-manifest.json`)
//! and runs every valid and invalid fixture through the Rust `compile_v3` validator.
//! Results are printed to stdout and the process exits with a non-zero code when
//! any fixture does not produce the expected outcome.
//!
//! Run:
//!   cargo test --test conformance_runner --features compiler_v3 -- --nocapture
//!
//! Or as a standalone check:
//!   cargo test --test conformance_runner --features compiler_v3

#![cfg(feature = "compiler_v3")]

use std::{fs, path::PathBuf};

use chrono::{TimeZone, Utc};
use serde::Deserialize;
use sha2::Digest;
use video_notes_ai::compile_v3::{
    parse_and_validate, parse_and_validate_with_policy, write_bundle, TrustPolicy,
};

// ── Manifest types ───────────────────────────────────────────────────────

#[derive(Deserialize)]
struct FixtureManifest {
    #[allow(dead_code)]
    schema_version: String,
    valid: Vec<FixtureEntry>,
    invalid: Vec<InvalidFixtureEntry>,
}

#[derive(Deserialize)]
struct FixtureEntry {
    path: String,
    #[allow(dead_code)]
    schema: String,
    trust_policy: String,
}

#[derive(Deserialize)]
struct InvalidFixtureEntry {
    path: String,
    #[allow(dead_code)]
    failure: String,
    #[allow(dead_code)]
    finding: String,
    trust_policy: String,
}

// ── Helpers ──────────────────────────────────────────────────────────────

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .expect("repository root")
        .to_path_buf()
}

fn read_text(relative: &str) -> String {
    fs::read_to_string(repository_root().join(relative)).expect("fixture must be readable")
}

fn load_policy(relative: &str) -> TrustPolicy {
    TrustPolicy::from_json(&read_text(relative))
        .unwrap_or_else(|e| panic!("policy {relative}: {e}"))
}

fn verification_time() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 7, 12, 19, 0, 0)
        .single()
        .unwrap()
}

fn verification_time_inside_window() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 6, 15, 12, 0, 0)
        .single()
        .unwrap()
}

// ── Main test ────────────────────────────────────────────────────────────

#[test]
fn conformance_runner() {
    let manifest_text = read_text("conformance/v0.2/fixture-manifest.json");
    let manifest: FixtureManifest =
        serde_json::from_str(&manifest_text).expect("valid fixture manifest JSON");

    let mut total = 0;
    let mut passed = 0;
    let mut failed: Vec<String> = Vec::new();

    // ── 1. Valid fixtures ────────────────────────────────────────────
    println!("\n═══ Valid fixtures ═══");
    for entry in &manifest.valid {
        total += 1;
        let fixture_text = read_text(&entry.path);
        let policy = load_policy(&entry.trust_policy);
        match parse_and_validate_with_policy(&fixture_text, &policy, verification_time()) {
            Ok(bundle) => {
                // Round-trip through the writer
                match write_bundle(&bundle) {
                    Ok(bytes) => {
                        let retext = String::from_utf8(bytes).expect("valid UTF-8");
                        match parse_and_validate_with_policy(&retext, &policy, verification_time())
                        {
                            Ok(rebundled) => {
                                if bundle.exchange_manifest.content_digest
                                    == rebundled.exchange_manifest.content_digest
                                {
                                    passed += 1;
                                    println!("  ✅ {} — accepted + round-trip OK", entry.path);
                                } else {
                                    failed.push(format!(
                                        "{}: content digest changed after write/read",
                                        entry.path
                                    ));
                                    println!("  ❌ {} — content digest mismatch", entry.path);
                                }
                            }
                            Err(report) => {
                                failed.push(format!(
                                    "{}: write/read rejected: {:?}",
                                    entry.path, report.issues
                                ));
                                println!("  ❌ {} — write/read rejected", entry.path);
                            }
                        }
                    }
                    Err(e) => {
                        failed.push(format!("{}: write failed: {e}", entry.path));
                        println!("  ❌ {} — write failed", entry.path);
                    }
                }
            }
            Err(report) => {
                failed.push(format!(
                    "{} should be valid but was rejected: {:?}",
                    entry.path, report.issues
                ));
                println!("  ❌ {} — rejected (expected valid)", entry.path);
            }
        }
    }

    // ── 2. Invalid fixtures ──────────────────────────────────────────
    println!("\n═══ Invalid fixtures ═══");
    for entry in &manifest.invalid {
        total += 1;
        let fixture_text = read_text(&entry.path);
        let policy = load_policy(&entry.trust_policy);
        let result = parse_and_validate_with_policy(&fixture_text, &policy, verification_time());
        if result.is_err() {
            passed += 1;
            println!(
                "  ✅ {} — rejected (expected {} {})",
                entry.path, entry.failure, entry.finding
            );
        } else {
            failed.push(format!(
                "{} should be invalid ({}/{}) but was accepted",
                entry.path, entry.failure, entry.finding
            ));
            println!(
                "  ❌ {} — accepted (expected invalid {}/{})",
                entry.path, entry.failure, entry.finding
            );
        }
    }

    // ── 3. Trust-policy edge cases ───────────────────────────────────
    println!("\n═══ Trust-policy edge cases ═══");

    // 3a. Valid bundle without external policy
    total += 1;
    let valid_fixture = read_text("examples/v0.2/valid/course-video-complete.json");
    if parse_and_validate(&valid_fixture).is_err() {
        passed += 1;
        println!("  ✅ Valid bundle rejected without TrustPolicy (expected)");
    } else {
        failed.push("Valid bundle was accepted without TrustPolicy".to_string());
        println!("  ❌ Valid bundle accepted without TrustPolicy (expected rejection)");
    }

    // 3b. Self-signed attacker bundle
    total += 1;
    let self_signed = read_text("examples/v0.2/invalid/untrusted-self-signed-key.json");
    let trusted_policy = load_policy("conformance/v0.2/trust/trusted-policy.json");
    let result = parse_and_validate_with_policy(&self_signed, &trusted_policy, verification_time());
    if result.is_err() {
        passed += 1;
        println!("  ✅ Self-signed attacker key rejected (expected)");
    } else {
        failed.push("Self-signed attacker key was accepted".to_string());
        println!("  ❌ Self-signed attacker key accepted (expected rejection)");
    }

    // 3c. Revoked key
    total += 1;
    let revoked_fixture = read_text("examples/v0.2/invalid/revoked-signing-key.json");
    let revoked_policy = load_policy("conformance/v0.2/trust/revoked-policy.json");
    let result =
        parse_and_validate_with_policy(&revoked_fixture, &revoked_policy, verification_time());
    if result.is_err() {
        passed += 1;
        println!("  ✅ Revoked signing key rejected (expected)");
    } else {
        failed.push("Revoked signing key was accepted".to_string());
        println!("  ❌ Revoked signing key accepted (expected rejection)");
    }

    // 3d. Signature outside key validity window
    total += 1;
    let expired_fixture = read_text("examples/v0.2/invalid/signature-outside-key-validity.json");
    let result = parse_and_validate_with_policy(
        &expired_fixture,
        &trusted_policy,
        verification_time_inside_window(),
    );
    if result.is_err() {
        passed += 1;
        println!("  ✅ Signature outside validity window rejected (expected)");
    } else {
        failed.push("Signature outside validity window was accepted".to_string());
        println!("  ❌ Signature outside validity accepted (expected rejection)");
    }

    // 3e. Key ID / public-key substitution
    total += 1;
    let substitution = read_text("examples/v0.2/invalid/trusted-key-id-key-substitution.json");
    let result =
        parse_and_validate_with_policy(&substitution, &trusted_policy, verification_time());
    if result.is_err() {
        passed += 1;
        println!("  ✅ Key ID / public-key substitution rejected (expected)");
    } else {
        failed.push("Key substitution was accepted".to_string());
        println!("  ❌ Key substitution accepted (expected rejection)");
    }

    // ── 4. Output report ─────────────────────────────────────────────
    println!("\n═══════════════════════════════════════════");
    println!("  Total:  {total}");
    println!("  Passed: {passed}");
    println!("  Failed: {}", failed.len());

    if failed.is_empty() {
        println!("  Result: ✅ ALL PASSED");
    } else {
        println!("  Result: ❌ FAILURES");
        for f in &failed {
            println!("    • {f}");
        }
    }
    println!("═══════════════════════════════════════════\n");

    assert!(
        failed.is_empty(),
        "{} conformance check(s) failed",
        failed.len()
    );
}

#[test]
fn canonical_bytes_match_python_reference() {
    // Cross-language comparison: verify Rust produces the same canonical
    // bytes as Python for every fixture in the corpus.
    //
    // The reference file is generated by:
    //   python scripts/verify_cross_language_interop.py
    //
    // If this test fails, re-run the Python script to regenerate the reference.

    let reference_text = read_text("conformance/v0.2/cross-language-reference.json");
    let reference: serde_json::Value =
        serde_json::from_str(&reference_text).expect("valid reference JSON");

    let canonical_digests = reference["canonical_digests"]
        .as_object()
        .expect("canonical_digests object");
    let sig_payload_digests = reference["signature_payload_digests"]
        .as_object()
        .expect("signature_payload_digests object");

    let mut failures: Vec<String> = Vec::new();

    // ── Canonical bytes comparison ───────────────────────────────
    println!("\n═══ Cross-language: canonical bytes ═══");
    for (path, expected_digest) in canonical_digests {
        let expected = expected_digest.as_str().unwrap_or("");
        let fixture_text = read_text(path);
        let value: serde_json::Value =
            serde_json::from_str(&fixture_text).expect("fixture must be valid JSON");
        match video_notes_ai::compile_v3::canonical::canonical_bytes(&value) {
            Ok(bytes) => {
                let actual = format!("sha256:{:x}", sha2::Sha256::digest(&bytes));
                if actual == expected {
                    println!("  ✅ {path} — canonical digest matches Python");
                } else {
                    failures.push(format!(
                        "{path}: Rust canonical digest {actual} != Python {expected}"
                    ));
                    println!("  ❌ {path} — digest mismatch");
                }
            }
            Err(e) => {
                failures.push(format!("{path}: Rust canonicalization failed: {e}"));
                println!("  ❌ {path} — canonicalization error: {e}");
            }
        }
    }

    // ── Signature payload comparison ─────────────────────────────
    println!("\n═══ Cross-language: signature payloads ═══");
    for (path, expected_digest) in sig_payload_digests {
        if expected_digest
            .as_str()
            .is_none_or(|s| s.starts_with("ERROR"))
        {
            println!("  ⬜ {path} — Python could not compute payload (expected)");
            continue;
        }
        let expected = expected_digest.as_str().unwrap_or("");
        let fixture_text = read_text(path);
        let value: serde_json::Value =
            serde_json::from_str(&fixture_text).expect("fixture must be valid JSON");

        // Extract unsigned value (without exchange_manifest)
        let unsigned = if let Some(obj) = value.as_object() {
            let mut clone = obj.clone();
            clone.remove("exchange_manifest");
            serde_json::Value::Object(clone)
        } else {
            value.clone()
        };

        // Extract key_id and signed_at from the manifest
        let key_id = value
            .get("exchange_manifest")
            .and_then(|m| m.get("signature"))
            .and_then(|s| s.get("key_id"))
            .and_then(|k| k.as_str())
            .unwrap_or("synthetic-fixture-key-v0.2-rc.3");
        let signed_at = value
            .get("exchange_manifest")
            .and_then(|m| m.get("signature"))
            .and_then(|s| s.get("signed_at"))
            .and_then(|k| k.as_str())
            .unwrap_or("2026-07-12T12:00:00Z");

        match video_notes_ai::compile_v3::canonical::signature_payload(&unsigned, key_id, signed_at)
        {
            Ok(bytes) => {
                let actual = format!("sha256:{:x}", sha2::Sha256::digest(&bytes));
                if actual == expected {
                    println!("  ✅ {path} — signature payload digest matches Python");
                } else {
                    failures.push(format!(
                        "{path}: Rust sig payload {actual} != Python {expected}"
                    ));
                    println!("  ❌ {path} — digest mismatch");
                }
            }
            Err(e) => {
                failures.push(format!("{path}: Rust signature payload failed: {e}"));
                println!("  ❌ {path} — signature payload error: {e}");
            }
        }
    }

    // ── Result ─────────────────────────────────────────────────
    println!("\n═══════════════════════════════════════════");
    if failures.is_empty() {
        println!("  ✅ All cross-language comparisons passed");
    } else {
        println!("  ❌ {} comparison(s) failed", failures.len());
        for f in &failures {
            println!("    • {f}");
        }
    }
    println!("═══════════════════════════════════════════\n");

    assert!(
        failures.is_empty(),
        "{} cross-language comparison(s) failed",
        failures.len()
    );
}
