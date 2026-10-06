#!/usr/bin/env python3
"""Whole-transcript review coverage and repeat suggestions; suggestions never authorize cuts."""
import argparse
import hashlib
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from roughcut_preflight import normalized, parse_srt


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def suggest(cues):
    """Find nearby restarts, even inside one cue or with intervening/changed words.

    This is an intentionally recall-oriented triage, not a semantic classifier.
    Parallel examples, recaps and rhetoric must be decided from context by the agent.
    """
    suggestions, seen = [], set()
    for index, (start, end, text) in enumerate(cues):
        current = normalized(text)
        for size in range(min(24, len(current) // 2), 1, -1):
            found = False
            for offset in range(len(current) - size * 2 + 1):
                phrase = current[offset:offset + size]
                second = current.find(phrase, offset + size)
                if second >= 0 and second - offset <= 32:
                    # Two-character words are merely suggestions, never deletion rules.
                    key = (index + 1, index + 1)
                    suggestions.append({'id': f'cue-{index+1}-restart', 'cue_ids': [index + 1],
                                        'signal': 'intra_cue_restart', 'shared_text': phrase})
                    seen.add(key); found = True; break
            if found:
                break
        for other in range(index + 1, len(cues)):
            if cues[other][0] - end > 45:
                break
            following = normalized(cues[other][2])
            match = SequenceMatcher(None, current, following, autojunk=False).find_longest_match()
            ratio = SequenceMatcher(None, current, following, autojunk=False).ratio()
            if match.size >= 6 or (min(len(current), len(following)) >= 5 and ratio >= .65):
                key = (index + 1, other + 1)
                if key not in seen:
                    suggestions.append({'id': f'cues-{index+1}-{other+1}',
                                        'cue_ids': [index + 1, other + 1],
                                        'signal': 'nearby_similar_expression',
                                        'shared_text': current[match.a:match.a + match.size],
                                        'similarity': round(ratio, 3)})
                    seen.add(key)
    return suggestions


def prepare(srt):
    cues = parse_srt(srt)
    if not cues:
        raise ValueError('字幕为空')
    return {'schema_version': 1, 'source_srt_sha256': digest(srt),
            'text_selection_required': True,
            'cue_count': len(cues),
            'cues': [{'id': i + 1, 'start': a, 'end': b, 'text': text}
                     for i, (a, b, text) in enumerate(cues)],
            'suggestions': suggest(cues),
            'limitation': '脚本只提供线索；必须通读全部原稿。ASR 可能省略实际复读，文本全覆盖也不是音频零漏检证明。'}


def validate(report, decisions, candidate_ids=()):
    errors, covered = [], []
    if report.get('text_selection_required'):
        from transcript_selection import validate_selection
        selection = report.get('text_selection')
        if not isinstance(selection, dict):
            errors.append('缺少独立子 Agent 的全文文字选择')
        else:
            selection_errors = validate_selection(report, selection)
            if selection_errors:
                return selection_errors
            drops = [row['id'] for row in selection.get('segments', [])
                     if row.get('action') == 'drop']
            handled = []
            for finding in decisions.get('findings', []):
                ids = finding.get('text_selection_segment_ids', [])
                if ids:
                    handled.extend(ids)
                    if finding.get('classification') not in ('retake', 'uncertain'):
                        errors.append('文字删除意图必须经原音确认删除，或明确回退为 uncertain/keep')
            if Counter(handled) != Counter(drops):
                errors.append('子 Agent 的每个删除片段必须且只能被主 Agent 处置一次')
    if decisions.get('source_srt_sha256') != report['source_srt_sha256']:
        errors.append('复核决策不属于当前转写')
    for group in decisions.get('coverage', []):
        ids = group.get('cue_ids', [])
        if not ids or not str(group.get('summary', '')).strip():
            errors.append('每个语义段必须记录 cue_ids 和内容摘要')
        covered.extend(ids)
    if sorted(covered) != list(range(1, report['cue_count'] + 1)):
        errors.append('原稿必须逐段覆盖且不能重复计数，不能只核对已选中的重复候选')
    by_id = {row['id']: row for row in decisions.get('findings', [])}
    if len(by_id) != len(decisions.get('findings', [])):
        errors.append('findings ID 重复')
    for suggestion in report['suggestions']:
        if suggestion['id'] not in by_id:
            errors.append('未处理疑点：' + suggestion['id'])
    allowed = {'retake', 'rhetorical', 'parallel_example', 'recap', 'not_duplicate', 'uncertain'}
    for finding in by_id.values():
        kind = finding.get('classification')
        if kind not in allowed or not str(finding.get('reason', '')).strip():
            errors.append('疑点缺少分类或理由：' + finding['id'])
        if kind == 'retake':
            if finding.get('repeat_candidate_id') not in candidate_ids:
                errors.append('确认重说尚未进入剪辑候选：' + finding['id'])
            if not str(finding.get('audio_evidence', '')).strip():
                errors.append('确认重说缺少局部音频证据：' + finding['id'])
        if kind == 'uncertain' and finding.get('action') != 'keep':
            errors.append('未决疑点必须保留并明确记录 action=keep：' + finding['id'])
    if not str(decisions.get('whole_transcript_conclusion', '')).strip():
        errors.append('缺少通读全稿后的结论')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'validate'])
    parser.add_argument('--srt', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--decisions', type=Path)
    parser.add_argument('--candidates', type=Path)
    parser.add_argument('--text-selection', type=Path,
                        help='独立子 Agent 的 selection.json；validate 时必填')
    args = parser.parse_args()
    report = prepare(args.srt)
    if args.command == 'validate':
        if not args.decisions:
            parser.error('validate 需要 --decisions')
        if not args.text_selection:
            parser.error('validate 需要 --text-selection（全文子 Agent 的原文选择）')
        report['text_selection'] = json.loads(args.text_selection.read_text())
        decisions = json.loads(args.decisions.read_text())
        config = json.loads(args.candidates.read_text()) if args.candidates else {'candidates': []}
        errors = validate(report, decisions, {row['id'] for row in config['candidates']})
        report.update(semantic_review_pass=not errors, errors=errors, decisions=decisions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'cues': report['cue_count'], 'suggestions': len(report['suggestions']),
                      'semantic_review_pass': report.get('semantic_review_pass'),
                      'errors': report.get('errors', [])}, ensure_ascii=False))
    if report.get('errors'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
