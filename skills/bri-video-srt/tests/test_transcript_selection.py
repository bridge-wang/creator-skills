import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import semantic_review
import transcript_selection as selection


class TranscriptSelectionTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            'source_srt_sha256': 'test',
            'cues': [{'id': 1, 'text': '这个功能收费。'},
                     {'id': 2, 'text': '刚才说错了，这个功能免费。'}],
        }
        self.result = {
            'source_srt_sha256': 'test', 'review_scope': 'whole_transcript',
            'segments': [
                {'id': 's1', 'cue_id': 1, 'text': '这个功能收费。', 'action': 'drop'},
                {'id': 's2', 'cue_id': 2, 'text': '刚才说错了，', 'action': 'drop'},
                {'id': 's3', 'cue_id': 2, 'text': '这个功能免费。', 'action': 'keep'},
            ],
            'retakes': [{'id': 'r1', 'discarded_segment_ids': ['s1', 's2'],
                         'retained_segment_ids': ['s3'], 'reason': '明确纠错',
                         'unique_content_check': '前句没有独有要点'}],
            'whole_transcript_conclusion': '保留纠正后的版本',
        }

    def test_intra_cue_selection_is_extractive_and_has_source_offsets(self):
        self.assertEqual(selection.validate_selection(self.source, self.result), [])
        self.assertEqual(selection.clean_text(self.result), '这个功能免费。\n')
        spans = selection.extracted_spans(self.result)
        self.assertEqual(spans[2]['char_start'], len('刚才说错了，'))
        self.assertEqual(spans[2]['char_end'], len(self.source['cues'][1]['text']))

    def test_rewritten_or_omitted_text_fails(self):
        for replacement in ('这个功能不收费。', '这个功能免费'):
            changed = copy.deepcopy(self.result)
            changed['segments'][2]['text'] = replacement
            self.assertTrue(selection.validate_selection(self.source, changed))
        self.result['segments'].pop(1)
        self.assertTrue(selection.validate_selection(self.source, self.result))

    def test_wrong_source_or_reordered_cues_fail(self):
        changed = copy.deepcopy(self.result)
        changed['source_srt_sha256'] = 'other'
        self.assertTrue(selection.validate_selection(self.source, changed))
        self.result['segments'] = self.result['segments'][1:] + self.result['segments'][:1]
        self.assertTrue(selection.validate_selection(self.source, self.result))

    def test_missing_replacement_and_double_disposition_fail(self):
        changed = copy.deepcopy(self.result)
        changed['retakes'][0]['retained_segment_ids'] = ['s1']
        self.assertTrue(selection.validate_selection(self.source, changed))
        changed = copy.deepcopy(self.result)
        changed['retakes'].append({**changed['retakes'][0], 'id': 'r2'})
        self.assertTrue(selection.validate_selection(self.source, changed))

    def test_uncertain_text_is_preserved_with_reason(self):
        self.result['segments'][0].update(action='uncertain', reason='前一遍有独有信息')
        self.result['retakes'][0]['discarded_segment_ids'] = ['s2']
        self.assertEqual(selection.validate_selection(self.source, self.result), [])
        self.assertIn('这个功能收费。', selection.clean_text(self.result))

    def test_repetition_is_not_decided_by_the_validator(self):
        source = {'source_srt_sha256': 'test', 'cues': [
            {'id': 1, 'text': '我强调两遍：先验证，再交付。先验证，再交付。'}]}
        result = {'source_srt_sha256': 'test', 'review_scope': 'whole_transcript',
                  'segments': [{'id': 's1', 'cue_id': 1, 'text': source['cues'][0]['text'],
                                'action': 'keep'}],
                  'retakes': [], 'whole_transcript_conclusion': '有意强调，保留'}
        self.assertEqual(selection.validate_selection(source, result), [])

    def review_and_decisions(self):
        report = {**self.source, 'cue_count': 2, 'suggestions': [],
                  'text_selection_required': True, 'text_selection': self.result}
        decisions = {'source_srt_sha256': 'test',
                     'coverage': [{'cue_ids': [1, 2], 'summary': '纠正功能费用'}],
                     'findings': [{'id': 'take-1', 'classification': 'retake',
                                   'reason': '原音确认纠正费用', 'audio_evidence': '合成测试证据字段',
                                   'repeat_candidate_id': 'cut-1',
                                   'text_selection_segment_ids': ['s1', 's2']}],
                     'whole_transcript_conclusion': '已逐项确认'}
        return report, decisions

    def test_main_agent_must_handle_every_text_deletion(self):
        report, decisions = self.review_and_decisions()
        self.assertEqual(semantic_review.validate(report, decisions, {'cut-1'}), [])
        decisions['findings'][0]['text_selection_segment_ids'] = ['s1']
        self.assertTrue(semantic_review.validate(report, decisions, {'cut-1'}))

    def test_audio_uncertainty_can_restore_text_without_authorizing_cut(self):
        report, decisions = self.review_and_decisions()
        decisions['findings'][0].update(classification='uncertain', action='keep',
                                        reason='原音与转写冲突，保留')
        decisions['findings'][0].pop('repeat_candidate_id')
        self.assertEqual(semantic_review.validate(report, decisions), [])
        decisions['findings'][0].pop('action')
        self.assertTrue(semantic_review.validate(report, decisions))

    def test_missing_subagent_result_cannot_pass_full_coverage(self):
        report, decisions = self.review_and_decisions()
        report.pop('text_selection')
        self.assertTrue(semantic_review.validate(report, decisions, {'cut-1'}))

    def test_cli_roundtrip_and_failed_check_does_not_create_clean_text(self):
        script = Path(selection.__file__)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = root / 'source.srt'
            srt.write_text('1\n00:00:00,000 --> 00:02:30,000\n这个功能收费。\n\n'
                           '2\n00:03:30,000 --> 00:06:00,000\n刚才说错了，这个功能免费。\n')
            source = selection.source_from_srt(srt)
            self.assertNotIn('start', source['cues'][0])
            self.assertNotIn('suggestions', source)
            self.result['source_srt_sha256'] = source['source_srt_sha256']
            input_path, output, report = root/'selection.json', root/'clean.txt', root/'report.json'
            input_path.write_text(json.dumps(self.result, ensure_ascii=False))
            cmd = [sys.executable, '-B', str(script), 'validate', '--srt', str(srt),
                   '--selection', str(input_path), '--report', str(report), '--clean-text', str(output)]
            completed = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(output.read_text(), '这个功能免费。\n')
            payload = json.loads(report.read_text())
            self.assertTrue(payload['selection_pass'])
            self.result['segments'][2]['text'] = '工具完全免费。'
            input_path.write_text(json.dumps(self.result, ensure_ascii=False))
            bad_output = root/'invalid.txt'
            cmd[-1] = str(bad_output)
            completed = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(bad_output.exists())


if __name__ == '__main__':
    unittest.main()
