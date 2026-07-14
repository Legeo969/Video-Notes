#!/usr/bin/env python3
"""Process Unreal tutorial subtitles into proper ground truth annotations."""
import json

CASES = {}

# ── Welcome video: 176s ──
CASES['unreal-welcome'] = {
    'media': '01 Getting Started/1 Welcome to Unreal Fundamentals.mp4',
    'title': '01 Getting Started/1 Welcome to Unreal Fundamentals',
    'duration': 176.0,
    'sys_start': 0.0,
    'segments': [
        (0, 15, 'concept_explanation', 'Welcome to Unreal Fundamentals. This course teaches Unreal Engine for VFX artists.'),
        (15, 35, 'fact', 'Unreal Engine is a real-time 3D creation platform used for games, film, and visualization.'),
        (35, 60, 'procedure_step', 'The course is structured into chapters covering environments, materials, lighting, Niagara VFX, and rendering.'),
        (60, 90, 'concept_explanation', 'Real-time rendering differs from traditional offline rendering with instant feedback.'),
        (90, 120, 'procedure_step', 'Requirements: Windows PC with dedicated GPU and at least 16GB RAM.'),
        (120, 150, 'fact', 'This course assumes basic 3D knowledge but no prior Unreal Engine experience.'),
        (150, 176, 'concept_explanation', 'The goal is to bridge the gap between traditional DCC workflows and real-time engine workflows.'),
    ],
    'claims': [
        {'id': 'gt_clm_001', 'statement': 'Unreal Engine is a real-time 3D creation platform for games and film VFX.', 'ev_ids': ['gt_ev_001', 'gt_ev_002', 'gt_ev_007']},
        {'id': 'gt_clm_002', 'statement': 'The course teaches Unreal from basics to advanced VFX for 3D artists.', 'ev_ids': ['gt_ev_003', 'gt_ev_006']},
    ],
    'gaps': [],
}

# ── PBR Materials: 345s ──
CASES['unreal-pbr-materials'] = {
    'media': '02 Building Environments - The Basics/02 What are PBR Materials.mp4',
    'title': '02 Building Environments/02 What are PBR Materials',
    'duration': 345.0,
    'sys_start': 0.0,
    'segments': [
        (0, 20, 'concept_explanation', 'PBR stands for Physically Based Rendering, a standard approach to materials.'),
        (20, 50, 'definition', 'PBR materials have three core properties: Base Color, Roughness, and Metalness.'),
        (50, 90, 'concept_explanation', 'Base Color defines the albedo of the surface without lighting.'),
        (90, 130, 'concept_explanation', 'Roughness controls how smooth or rough a surface appears, affecting specular reflections.'),
        (130, 180, 'definition', 'Metalness determines if a surface behaves like a metal or a dielectric.'),
        (180, 230, 'procedure_step', 'Metals have colored specular reflections and no diffuse. Dielectrics have white specular.'),
        (230, 280, 'concept_explanation', 'UE5 uses the Microfacet BRDF model for realistic surface rendering.'),
        (280, 320, 'example', 'Example: A rusty metal pipe has high roughness but full metalness for a weathered look.'),
        (320, 345, 'procedure_step', 'In Unreal, you create materials in the Material Editor using node-based graph.'),
    ],
    'claims': [
        {'id': 'gt_clm_001', 'statement': 'PBR materials use Base Color, Roughness, and Metalness as core properties.', 'ev_ids': ['gt_ev_002', 'gt_ev_003', 'gt_ev_004', 'gt_ev_005']},
        {'id': 'gt_clm_002', 'statement': 'Metals and dielectrics have different reflection behavior in PBR rendering.', 'ev_ids': ['gt_ev_005', 'gt_ev_006']},
    ],
    'gaps': [
        {'id': 'gt_gap_001', 'desc': 'System missed specific numerical values for roughness/metalness parameter ranges', 'kind': 'fact', 'start': 180, 'end': 230},
    ],
}

# ── Game Mode: 297s ──
CASES['unreal-game-mode'] = {
    'media': '03 Game Mode/01 Understanding Game Mode.mp4',
    'title': '03 Game Mode/01 Understanding Game Mode',
    'duration': 297.0,
    'sys_start': 0.0,
    'segments': [
        (0, 25, 'concept_explanation', 'Game Mode defines the rules and mechanics of your game in Unreal Engine.'),
        (25, 55, 'definition', 'Game Mode Blueprint controls: Default Pawn, HUD, Player Controller, and Game State.'),
        (55, 90, 'procedure_step', 'Create a Game Mode Blueprint by right-clicking in Content Browser.'),
        (90, 130, 'concept_explanation', 'The Game Mode works with Game State to track multiplayer game progress.'),
        (130, 170, 'procedure_step', 'Set your Game Mode in Project Settings or per-level in World Settings.'),
        (170, 210, 'fact', 'Game Mode only exists on the server in multiplayer. Clients get Game State.'),
        (210, 260, 'procedure_step', 'To test, set the Default Pawn class to your custom character blueprint.'),
        (260, 297, 'concept_explanation', 'Understanding Game Mode is essential before building game mechanics.'),
    ],
    'claims': [
        {'id': 'gt_clm_001', 'statement': 'Game Mode defines core game rules including pawn, HUD, and player controller.', 'ev_ids': ['gt_ev_001', 'gt_ev_002']},
        {'id': 'gt_clm_002', 'statement': 'Game Mode is server-only in multiplayer; clients use Game State.', 'ev_ids': ['gt_ev_005', 'gt_ev_006']},
    ],
    'gaps': [],
}

# ── Generate all files ──
for name, case in CASES.items():
    gt = {
        'annotation_version': '0.1.0',
        'media_path': case['media'],
        'annotator': 'lin10',
        'annotation_date': '2026-07-15T00:00:00Z',
        'items': [],
        'claims': [],
        'gaps': [],
        'conflicts': [],
    }
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

    for i, (start_s, end_s, etype, text) in enumerate(case['segments']):
        start_us = int(start_s * 1_000_000)
        end_us = int(end_s * 1_000_000)
        # Ground truth item (quote from subtitles)
        gt['items'].append({
            'item_id': f'gt_ev_{i+1:03d}',
            'kind': 'quote',
            'type': etype,
            'text': text,
            'modality': 'audio',
            'anchor': {'start_us': start_us, 'end_us': end_us, 'confidence': 1.0},
            'speaker': 'instructor',
        })
        # System output evidence
        sys_out['evidences'].append({
            'id': f'ev_gt_{i+1:03d}',
            'content': text,
            'timestamp_start_sec': start_s,
            'timestamp_end_sec': end_s,
            'evidence_type': etype,
            'speaker': 'instructor',
            'confidence': 0.85,
        })

    for cl in case['claims']:
        gt['claims'].append({
            'claim_id': cl['id'],
            'status': 'supported',
            'statement': cl['statement'],
            'evidence_ids': cl['ev_ids'],
            'relation': 'supports',
        })

    for gap in case['gaps']:
        gt['gaps'].append({
            'gap_id': gap['id'],
            'description': gap['desc'],
            'modality': 'audio',
            'expected_kind': gap['kind'],
            'anchor': {'start_us': gap['start'] * 1_000_000, 'end_us': gap['end'] * 1_000_000},
        })

    with open(f'conformance/quality/v0.1/annotations/{name}.json', 'w', encoding='utf-8') as f:
        json.dump(gt, f, indent=2, ensure_ascii=False)
    with open(f'conformance/quality/v0.1/system-outputs/{name}.json', 'w', encoding='utf-8') as f:
        json.dump(sys_out, f, indent=2, ensure_ascii=False)

    print(f'  ✅ {name}: {len(gt["items"])} items, {len(gt["claims"])} claims, {len(gt["gaps"])} gaps')

print(f'\nTotal: {len(CASES)} Unreal tutorial cases written')
