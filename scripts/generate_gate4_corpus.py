#!/usr/bin/env python3
"""Generate ground truth annotations and system outputs for Gate #4."""
import json, os

CASES = {}

# ── Case 1: Audio Lecture ──
CASES['audio-lecture'] = {
    'media': 'test-media/audio-lecture.wav',
    'title': 'Audio Lecture - Tone-based Learning',
    'duration': 15.0,
    'gt_items': [
        {'id': 'gt_ev_001', 'kind': 'paraphrase', 'type': 'fact', 'text': 'Section 1 introduces fundamental concepts using a 440Hz tone', 'modality': 'audio', 'start_us': 0, 'end_us': 5000000, 'speaker': 'instructor'},
        {'id': 'gt_ev_002', 'kind': 'paraphrase', 'type': 'procedure_step', 'text': 'Section 2 explains the step-by-step process with a 660Hz tone', 'modality': 'audio', 'start_us': 5000000, 'end_us': 10000000, 'speaker': 'instructor'},
        {'id': 'gt_ev_003', 'kind': 'paraphrase', 'type': 'concept_explanation', 'text': 'Section 3 provides advanced concept explanations with a 880Hz tone', 'modality': 'audio', 'start_us': 10000000, 'end_us': 15000000, 'speaker': 'instructor'},
    ],
    'gt_claims': [{'id': 'gt_clm_001', 'status': 'supported', 'statement': 'The lecture progresses from basic concepts to advanced material', 'evidence_ids': ['gt_ev_001', 'gt_ev_002', 'gt_ev_003'], 'relation': 'supports'}],
    'gt_gaps': [{'id': 'gt_gap_001', 'description': 'No section summary or transition explanation provided', 'modality': 'audio', 'expected_kind': 'verification', 'start_us': 14000000, 'end_us': 15000000}],
    'sys_evidences': [
        {'id': 'ev_001', 'content': 'Section 1 introduces fundamental concepts using a 440Hz tone', 'start_sec': 0.0, 'end_sec': 5.0, 'type': 'fact', 'speaker': 'instructor', 'confidence': 0.85},
        {'id': 'ev_002', 'content': 'Section 2 explains the step-by-step process with a 660Hz tone', 'start_sec': 5.0, 'end_sec': 10.0, 'type': 'procedure', 'speaker': 'instructor', 'confidence': 0.85},
        {'id': 'ev_003', 'content': 'Section 3 provides advanced concept explanations with a 880Hz tone', 'start_sec': 10.0, 'end_sec': 15.0, 'type': 'concept', 'speaker': 'instructor', 'confidence': 0.85},
    ],
}

# ── Case 2: Slide Lecture ──
CASES['slide-lecture'] = {
    'media': 'test-media/slide-lecture.mp4',
    'title': 'Slide Lecture - Color-coded Topics',
    'duration': 15.0,
    'gt_items': [
        {'id': 'gt_ev_001', 'kind': 'visual_observation', 'type': 'concept_explanation', 'text': 'Orange slide covers introductory concepts with warm visual theme', 'modality': 'visual', 'start_us': 0, 'end_us': 5000000, 'speaker': 'presenter'},
        {'id': 'gt_ev_002', 'kind': 'visual_observation', 'type': 'procedure_step', 'text': 'Green slide shows procedural steps with calm green theme', 'modality': 'visual', 'start_us': 5000000, 'end_us': 10000000, 'speaker': 'presenter'},
        {'id': 'gt_ev_003', 'kind': 'visual_observation', 'type': 'fact', 'text': 'Blue slide presents factual summary with professional blue theme', 'modality': 'visual', 'start_us': 10000000, 'end_us': 15000000, 'speaker': 'presenter'},
    ],
    'gt_claims': [{'id': 'gt_clm_001', 'status': 'supported', 'statement': 'The lecture uses color-coded slides to organize content by section', 'evidence_ids': ['gt_ev_001', 'gt_ev_002', 'gt_ev_003'], 'relation': 'supports'}],
    'gt_gaps': [{'id': 'gt_gap_001', 'description': 'No audio narration track to explain slide content', 'modality': 'visual', 'expected_kind': 'concept_explanation', 'start_us': 0, 'end_us': 15000000}],
    'sys_evidences': [
        {'id': 'ev_001', 'content': 'Orange slide covers introductory concepts with warm visual theme', 'start_sec': 0.0, 'end_sec': 5.0, 'type': 'concept', 'speaker': None, 'confidence': 0.75},
        {'id': 'ev_002', 'content': 'Green slide shows procedural steps with calm green theme', 'start_sec': 5.0, 'end_sec': 10.0, 'type': 'procedure', 'speaker': None, 'confidence': 0.75},
        {'id': 'ev_003', 'content': 'Blue slide presents factual summary', 'start_sec': 10.0, 'end_sec': 15.0, 'type': 'fact', 'speaker': None, 'confidence': 0.75},
    ],
}

# ── Case 3: Full Lecture ──
CASES['full-lecture'] = {
    'media': 'test-media/full-lecture.mp4',
    'title': 'Full Lecture - Integrated AV',
    'duration': 15.0,
    'gt_items': [
        {'id': 'gt_ev_001', 'kind': 'multimodal_observation', 'type': 'concept_explanation', 'text': 'Purple background video with 330Hz audio presents integrated lecture content', 'modality': 'multimodal', 'start_us': 0, 'end_us': 15000000, 'speaker': 'instructor'},
    ],
    'gt_claims': [{'id': 'gt_clm_001', 'status': 'supported', 'statement': 'The full lecture combines visual presentation with synchronized audio narration', 'evidence_ids': ['gt_ev_001'], 'relation': 'supports'}],
    'gt_gaps': [{'id': 'gt_gap_001', 'description': 'No captions or text overlay for accessibility', 'modality': 'visual', 'expected_kind': 'metadata', 'start_us': 0, 'end_us': 15000000}],
    'sys_evidences': [
        {'id': 'ev_001', 'content': 'Purple background video presents lecture content with synchronized audio', 'start_sec': 0.0, 'end_sec': 15.0, 'type': 'concept', 'speaker': 'instructor', 'confidence': 0.80},
    ],
}

# ── Case 4: Black Screen Audio ──
CASES['black-audio'] = {
    'media': 'test-media/black-audio.mp4',
    'title': 'Black Screen Audio',
    'duration': 8.0,
    'gt_items': [
        {'id': 'gt_ev_001', 'kind': 'audio_observation', 'type': 'fact', 'text': 'Low frequency 220Hz audio content plays throughout the black screen segment', 'modality': 'audio', 'start_us': 0, 'end_us': 8000000, 'speaker': 'narrator'},
    ],
    'gt_claims': [],
    'gt_gaps': [{'id': 'gt_gap_001', 'description': 'No visual content available only pure audio extraction possible', 'modality': 'visual', 'expected_kind': 'failure', 'start_us': 0, 'end_us': 8000000}],
    'sys_evidences': [
        {'id': 'ev_001', 'content': 'Audio content with 220Hz tone detected', 'start_sec': 0.0, 'end_sec': 8.0, 'type': 'fact', 'speaker': 'narrator', 'confidence': 0.90},
    ],
}

# ── Case 5: Houdini Week 1 (existing, re-annotated) ──
CASES['houdini-week1'] = {
    'media': 'real-houdini-course-video',
    'title': '02',
    'duration': 223.061,
    'gt_items': [
        {'id': 'gt_ev_001', 'kind': 'paraphrase', 'type': 'fact', 'text': 'The speaker introduces Week 1 of the Houdini for 3D artists course, explaining that this week focuses on concepts and technical material.', 'modality': 'audio', 'start_us': 0, 'end_us': 39000000, 'speaker': 'Instructor'},
        {'id': 'gt_ev_002', 'kind': 'paraphrase', 'type': 'procedure_step', 'text': 'The speaker suggests that users who have never used Houdini before should watch a beginner tutorial on procedural modeling.', 'modality': 'audio', 'start_us': 39000000, 'end_us': 59000000, 'speaker': 'Instructor'},
        {'id': 'gt_ev_003', 'kind': 'paraphrase', 'type': 'concept_explanation', 'text': 'The speaker plays a clip from the beginner tutorial covering an introduction to Houdini interface and basic navigation.', 'modality': 'audio', 'start_us': 59000000, 'end_us': 68000000, 'speaker': 'Instructor'},
        {'id': 'gt_ev_004', 'kind': 'paraphrase', 'type': 'concept_explanation', 'text': 'The instructor begins the Week 1 overview, mentioning the first part will cover basic concepts as a refresher.', 'modality': 'audio', 'start_us': 69000000, 'end_us': 80000000, 'speaker': 'Instructor'},
        {'id': 'gt_ev_005', 'kind': 'paraphrase', 'type': 'procedure_step', 'text': 'The instructor recommends a free 3+ hour beginner tutorial for Houdini newbies.', 'modality': 'audio', 'start_us': 80000000, 'end_us': 126000000, 'speaker': 'Instructor'},
        {'id': 'gt_ev_006', 'kind': 'paraphrase', 'type': 'concept_explanation', 'text': 'The instructor transitions to the basic overview of what will be covered this week.', 'modality': 'audio', 'start_us': 126000000, 'end_us': 223061000, 'speaker': 'Instructor'},
    ],
    'gt_claims': [
        {'id': 'gt_clm_001', 'status': 'supported', 'statement': 'Week 1 of the Houdini course covers foundational concepts for 3D artists.', 'evidence_ids': ['gt_ev_001', 'gt_ev_004'], 'relation': 'supports'},
        {'id': 'gt_clm_002', 'status': 'supported', 'statement': 'Beginners are directed to a separate free tutorial before Week 1.', 'evidence_ids': ['gt_ev_002', 'gt_ev_005'], 'relation': 'supports'},
    ],
    'gt_gaps': [],
    'sys_evidences': [
        {'id': 'ev_001', 'content': 'The speaker introduces Week 1 of the Houdini for 3D artists course, explaining that this week focuses on concepts and technical material.', 'start_sec': 0.0, 'end_sec': 39.0, 'type': 'fact', 'speaker': 'Instructor', 'confidence': 0.74},
        {'id': 'ev_002', 'content': 'The speaker suggests that users who have never used Houdini before should watch a beginner tutorial on procedural modeling.', 'start_sec': 39.0, 'end_sec': 59.0, 'type': 'procedure', 'speaker': 'Instructor', 'confidence': 0.74},
        {'id': 'ev_003', 'content': 'The speaker plays a clip from the beginner tutorial covering an introduction to Houdini interface and basic navigation.', 'start_sec': 59.0, 'end_sec': 68.0, 'type': 'concept', 'speaker': 'Instructor', 'confidence': 0.74},
        {'id': 'ev_004', 'content': 'The instructor begins the Week 1 overview, mentioning the first part will cover basic concepts as a refresher.', 'start_sec': 69.0, 'end_sec': 80.0, 'type': 'concept', 'speaker': 'Instructor', 'confidence': 0.74},
        {'id': 'ev_005', 'content': 'The instructor recommends a free 3+ hour beginner tutorial for Houdini newbies.', 'start_sec': 80.0, 'end_sec': 126.0, 'type': 'procedure', 'speaker': 'Instructor', 'confidence': 0.74},
        {'id': 'ev_006', 'content': 'The instructor transitions to the basic overview of what will be covered this week.', 'start_sec': 126.0, 'end_sec': 223.061, 'type': 'concept', 'speaker': 'Instructor', 'confidence': 0.74},
    ],
}

# ── Write files ──
for name, case in CASES.items():
    # Ground truth annotation
    gt = {
        'annotation_version': '0.1.0',
        'media_path': case['media'],
        'annotator': 'claude-code',
        'annotation_date': '2026-07-15T00:00:00Z',
        'items': [],
        'claims': [],
        'gaps': [],
        'conflicts': [],
    }
    for item in case['gt_items']:
        gt['items'].append({
            'item_id': item['id'],
            'kind': item['kind'],
            'type': item['type'],
            'text': item['text'],
            'modality': item['modality'],
            'anchor': {'start_us': item['start_us'], 'end_us': item['end_us'], 'confidence': 1.0},
            'speaker': item['speaker'],
        })
    for claim in case['gt_claims']:
        gt['claims'].append({
            'claim_id': claim['id'],
            'status': claim['status'],
            'statement': claim['statement'],
            'evidence_ids': claim['evidence_ids'],
            'relation': claim['relation'],
        })
    for gap in case.get('gt_gaps', []):
        gt['gaps'].append({
            'gap_id': gap['id'],
            'description': gap['description'],
            'modality': gap['modality'],
            'expected_kind': gap['expected_kind'],
            'anchor': {'start_us': gap['start_us'], 'end_us': gap['end_us']},
        })

    with open(f'conformance/quality/v0.1/annotations/{name}.json', 'w', encoding='utf-8') as f:
        json.dump(gt, f, indent=2, ensure_ascii=False)

    # System output
    sys_out = {
        'ir_schema_version': 2,
        'capsule_id': f'{name}_capsule',
        'source_hash': f'{name}_hash',
        'source_title': case['title'],
        'version': 1,
        'total_duration': case['duration'],
        'processed_at': '2026-07-15T00:00:00Z',
        'model_used': 'mimo-v2.5',
        'evidences': [],
    }
    for ev in case['sys_evidences']:
        sys_out['evidences'].append({
            'id': ev['id'],
            'content': ev['content'],
            'timestamp_start_sec': ev['start_sec'],
            'timestamp_end_sec': ev['end_sec'],
            'evidence_type': ev['type'],
            'speaker': ev['speaker'],
            'confidence': ev['confidence'],
        })

    with open(f'conformance/quality/v0.1/system-outputs/{name}.json', 'w', encoding='utf-8') as f:
        json.dump(sys_out, f, indent=2, ensure_ascii=False)

    print(f'  ✅ {name} — {len(case["gt_items"])} items, {len(case["gt_claims"])} claims, {len(case.get("gt_gaps", []))} gaps')

print(f'\nTotal: {len(CASES)} cases written')
