#!/usr/bin/env python3
"""Validate an LLM's extractive transcript selection; never classify repetition."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from roughcut_preflight import parse_srt


def source_from_srt(path):
    cues = parse_srt(path)
    if not cues:
        raise ValueError('字幕为空')
    return {
        'source_srt_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        # Deliberately omit timing and heuristic suggestions from the reader's input.
        'cues': [{'id': i, 'text': text} for i, (_, _, text) in enumerate(cues, 1)],
    }


def validate_selection(source, selection):
    if not isinstance(selection, dict):
        return ['文字选择必须为 JSON 对象']
    errors = []
    if selection.get('source_srt_sha256') != source['source_srt_sha256']:
        errors.append('文字选择不属于当前转写')
    if selection.get('review_scope') != 'whole_transcript':
        errors.append('必须完成全文审稿')
    if not str(selection.get('whole_transcript_conclusion', '')).strip():
        errors.append('缺少全文审稿结论')
    segments = selection.get('segments', [])
    if not isinstance(segments, list) or not segments:
        return errors + ['缺少逐段原文选择']
    expected = {cue['id']: cue['text'] for cue in source['cues']}
    rebuilt = {cue_id: '' for cue_id in expected}
    by_id, last_cue = {}, 0
    for row in segments:
        if not isinstance(row, dict):
            errors.append('segments 中每项必须为对象')
            continue
        segment_id, cue_id, value = row.get('id'), row.get('cue_id'), row.get('text')
        if not isinstance(segment_id, str) or not segment_id or segment_id in by_id:
            errors.append('片段 ID 为空或重复')
            continue
        by_id[segment_id] = row
        if type(cue_id) is not int or cue_id not in expected:
            errors.append(f'{segment_id}: 原文 cue_id 无效')
            continue
        if cue_id < last_cue:
            errors.append(f'{segment_id}: 不得重排原文')
        last_cue = cue_id
        if not isinstance(value, str) or not value:
            errors.append(f'{segment_id}: 原文片段为空')
            continue
        rebuilt[cue_id] += value
        if row.get('action') not in ('keep', 'drop', 'uncertain'):
            errors.append(f'{segment_id}: action 必须为 keep/drop/uncertain')
        if row.get('action') == 'uncertain' and not str(row.get('reason', '')).strip():
            errors.append(f'{segment_id}: 未决项缺少理由')
    for cue_id, value in expected.items():
        if rebuilt[cue_id] != value:
            errors.append(f'cue {cue_id}: 全部片段必须按顺序恰好还原原文，禁止漏字、改写或重复抽取')

    groups = selection.get('retakes', [])
    if not isinstance(groups, list):
        return errors + ['retakes 必须为列表']
    group_ids, discarded = set(), []
    for group in groups:
        if not isinstance(group, dict):
            errors.append('retakes 中每项必须为对象')
            continue
        group_id = group.get('id')
        if not isinstance(group_id, str) or not group_id or group_id in group_ids:
            errors.append('重录组 ID 为空或重复')
            continue
        group_ids.add(group_id)
        for key in ('reason', 'unique_content_check'):
            if not str(group.get(key, '')).strip():
                errors.append(f'{group_id}: 缺少 {key}')
        for key, action in (('discarded_segment_ids', 'drop'), ('retained_segment_ids', 'keep')):
            ids = group.get(key, [])
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                errors.append(f'{group_id}: 缺少 {key}')
                continue
            if len(set(ids)) != len(ids):
                errors.append(f'{group_id}: {key} 重复引用')
            for segment_id in ids:
                if segment_id not in by_id or by_id[segment_id].get('action') != action:
                    errors.append(f'{group_id}: {segment_id} 必须引用 {action} 片段')
            if action == 'drop':
                discarded.extend(ids)
    expected_drops = [key for key, row in by_id.items() if row.get('action') == 'drop']
    if Counter(discarded) != Counter(expected_drops):
        errors.append('每个删除片段必须且只能属于一个重录组，并指明对应保留版本')
    if not any(row.get('action') in ('keep', 'uncertain') for row in by_id.values()):
        errors.append('不能删除全部文字')
    return errors


def extracted_spans(selection):
    offsets, spans = {}, []
    for row in selection['segments']:
        cue_id = row['cue_id']
        start = offsets.get(cue_id, 0)
        end = start + len(row['text'])
        spans.append({**row, 'char_start': start, 'char_end': end})
        offsets[cue_id] = end
    return spans


def clean_text(selection):
    """Extract only; uncertain text stays. Preserve source order and cue grouping."""
    lines, last_cue = [], None
    for row in selection['segments']:
        if row['action'] == 'drop':
            continue
        if row['cue_id'] != last_cue:
            lines.append(row['text'])
        else:
            lines[-1] += row['text']
        last_cue = row['cue_id']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--srt', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('validate')
    check.add_argument('--srt', type=Path, required=True)
    check.add_argument('--selection', type=Path, required=True)
    check.add_argument('--report', type=Path, required=True)
    check.add_argument('--clean-text', type=Path, required=True)
    args = parser.parse_args()
    source = source_from_srt(args.srt)
    if args.command == 'prepare':
        args.output.write_text(json.dumps(source, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'cues': len(source['cues']), 'output': str(args.output)}))
        return
    selection = json.loads(args.selection.read_text())
    errors = validate_selection(source, selection)
    report = {
        'selection_pass': not errors, 'errors': errors,
        'selection': selection,
        'limitation': '只验证出处、覆盖和引用关系，不证明语义判断正确；删除仍需原音核验。',
    }
    if not errors:
        report['source_spans'] = extracted_spans(selection)
        args.clean_text.write_text(clean_text(selection), encoding='utf-8')
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'selection_pass': not errors, 'errors': errors}, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
