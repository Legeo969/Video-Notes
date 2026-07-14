#!/usr/bin/env python3
"""Convert video subtitle files (SRT/VTT) into ground truth annotation format.

This is a semi-automated annotation tool for Gate #4. It converts existing
video subtitles/closed captions into the ground truth annotation format,
significantly reducing manual annotation effort.

Process:
1. User finds a video with subtitles (YouTube CC, local SRT file, etc.)
2. This script converts subtitles into annotation JSON
3. User reviews and:
   a. Categorizes each item (fact/definition/procedure/etc.)
   b. Adds claims (what knowledge should be extracted)
   c. Adds gaps (what the system should find but might miss)
   d. Removes obvious non-content subtitles (um, okay, etc.)

Usage:
    python scripts/subtitle_to_annotation.py subtitles.srt -o annotation.json
    python scripts/subtitle_to_annotation.py subtitles.vtt --media "video.mp4"
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def parse_srt(text: str) -> list[dict]:
    """Parse SRT subtitle format into segments with timing."""
    segments = []
    blocks = re.split(r'\n\s*\n', text.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        # Line 1: sequence number (optional)
        # Line 2: timestamp range
        time_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})',
            lines[1] if '-->' in lines[1] else (lines[0] if '-->' in lines[0] else '')
        )
        if not time_match:
            continue
        # Text starts after the timestamp line
        text_start = 2 if '-->' in lines[1] else 1
        content = ' '.join(lines[text_start:]).strip()
        if not content:
            continue

        def ts_to_us(ts: str) -> int:
            ts = ts.replace(',', '.')
            parts = ts.split(':')
            h, m, s = float(parts[0]), float(parts[1]), float(parts[2].replace(',', '.'))
            return int((h * 3600 + m * 60 + s) * 1_000_000)

        segments.append({
            'start_us': ts_to_us(time_match.group(1)),
            'end_us': ts_to_us(time_match.group(2)),
            'text': content,
        })
    return segments


def parse_vtt(text: str) -> list[dict]:
    """Parse VTT subtitle format. (Simplified - most YouTube CC downloads as VTT)"""
    # VTT is similar to SRT but with "WEBVTT" header and optional styling
    # Remove header
    text = re.sub(r'^WEBVTT.*?\n\n', '', text, flags=re.DOTALL)
    # Remove styling blocks
    text = re.sub(r'STYLE\n.*?\n\n', '', text, flags=re.DOTALL)
    # Remove NOTE blocks
    text = re.sub(r'NOTE.*?\n\n', '', text, flags=re.DOTALL)
    return parse_srt(text)


def convert_to_annotation(
    segments: list[dict],
    media_path: str = "",
    annotator: str = "subtitle-converter",
    language: str = "auto",
) -> dict:
    """Convert subtitle segments into ground truth annotation skeleton."""
    items = []
    for i, seg in enumerate(segments, 1):
        # Skip very short segments (likely filler words)
        duration_us = seg['end_us'] - seg['start_us']
        text = seg['text'].strip()
        if duration_us < 500_000 or len(text) < 10:
            continue

        # Basic heuristics for type classification
        text_lower = text.lower()
        if any(w in text_lower for w in ['?', 'question', 'what about', 'how about']):
            ev_type = 'question'
        elif any(w in text_lower for w in ['step', 'click', 'press', 'type', 'select', 'open',
                                             'create', 'add', 'drag', 'set ', 'choose']):
            ev_type = 'procedure_step'
        elif any(w in text_lower for w in ['example', 'for instance', 'like this', 'such as']):
            ev_type = 'example'
        elif any(w in text_lower for w in ['warning', 'careful', 'important', 'note:']):
            ev_type = 'warning'
        elif any(w in text_lower for w in ['definition', 'called', 'known as', 'refers to',
                                            'means that', 'is a ']):
            ev_type = 'definition'
        elif any(w in text_lower for w in ['but ', 'however', 'alternatively', 'instead']):
            ev_type = 'decision'
        elif any(w in text_lower for w in ['fail', 'error', 'bug', 'problem', 'issue', 'broken']):
            ev_type = 'failure'
        else:
            ev_type = 'concept_explanation'

        items.append({
            'item_id': f'gt_ev_{i:03d}',
            'kind': 'quote',  # Default to quote since we have the transcript
            'type': ev_type,
            'text': text,
            'modality': 'audio',
            'anchor': {
                'start_us': seg['start_us'],
                'end_us': seg['end_us'],
                'confidence': 1.0,
            },
            'speaker': 'presenter',
            'notes': f'Auto-classified as {ev_type} — please review and correct',
        })

    return {
        'annotation_version': '0.1.0',
        'media_path': media_path,
        'annotator': annotator,
        'annotation_date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'language': language,
        'items': items,
        'claims': [],
        'gaps': [],
        'conflicts': [],
        'notes': 'Generated by subtitle_to_annotation.py. Please review items, add claims/gaps, and correct types.',
    }


def main():
    parser = argparse.ArgumentParser(description='Convert subtitles to ground truth annotation')
    parser.add_argument('input', help='Path to subtitle file (.srt or .vtt)')
    parser.add_argument('-o', '--output', help='Output annotation JSON path')
    parser.add_argument('--media', default='', help='Original media path/URL')
    parser.add_argument('--annotator', default='subtitle-converter', help='Annotator name')
    parser.add_argument('--language', default='auto', help='Content language')
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding='utf-8', errors='replace')

    if args.input.lower().endswith('.vtt'):
        segments = parse_vtt(text)
    else:
        segments = parse_srt(text)

    print(f'Parsed {len(segments)} subtitle segments')
    annotation = convert_to_annotation(segments, args.media, args.annotator, args.language)
    print(f'Generated {len(annotation["items"])} annotation items')

    # Show sample
    if annotation['items']:
        print(f'\nSample items:')
        for item in annotation['items'][:3]:
            start_s = item['anchor']['start_us'] / 1_000_000
            end_s = item['anchor']['end_us'] / 1_000_000
            print(f'  [{start_s:.0f}s-{end_s:.0f}s] ({item["type"]}) {item["text"][:60]}...')

    output_path = args.output or (Path(args.input).with_suffix('.annotation.json'))
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(annotation, f, indent=2, ensure_ascii=False)
    print(f'\nAnnotation written to {output_path}')
    print(f'\n⚠️  REVIEW REQUIRED:')
    print(f'   1. Correct auto-classified types for each item')
    print(f'   2. Add claims (key takeaways)')
    print(f'   3. Add gaps (missing content)')
    print(f'   4. Add conflicts if applicable')
    print(f'   5. Set annotator to your name')


if __name__ == '__main__':
    main()
