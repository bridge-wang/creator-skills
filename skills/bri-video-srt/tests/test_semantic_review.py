import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import semantic_review


class SemanticReviewTests(unittest.TestCase):
    def test_interrupted_and_paraphrased_retake_is_suggested(self):
        cues = [(0, 2, '我要做一个付费的视频'), (2, 3, '不对，再来一次'),
                (3, 6, '我今天想做一个付费的视频课程')]
        self.assertTrue(any(row['cue_ids'] == [1, 3] for row in semantic_review.suggest(cues)))

    def test_intra_cue_restart_does_not_need_long_silence(self):
        cues = [(0, 3, '按理按理说这个错误很容易发现')]
        self.assertEqual(semantic_review.suggest(cues)[0]['cue_ids'], [1])

    def test_selected_candidates_pass_is_not_full_coverage(self):
        report = {'source_srt_sha256': 'hash', 'cue_count': 3, 'suggestions': []}
        decisions = {'source_srt_sha256': 'hash',
                     'coverage': [{'cue_ids': [1], 'summary': '仅审核第一个切点'}],
                     'whole_transcript_conclusion': '已看完'}
        self.assertTrue(semantic_review.validate(report, decisions))

    def test_rhetoric_is_kept_but_confirmed_retake_requires_audio_and_cut(self):
        report = {'source_srt_sha256': 'hash', 'cue_count': 1,
                  'suggestions': [{'id': 'a', 'cue_ids': [1]}]}
        decisions = {'source_srt_sha256': 'hash',
                     'coverage': [{'cue_ids': [1], 'summary': '好看是真好看，但是很贵'}],
                     'findings': [{'id': 'a', 'classification': 'rhetorical', 'reason': '转折强调'}],
                     'whole_transcript_conclusion': '保留修辞强调'}
        self.assertEqual(semantic_review.validate(report, decisions), [])
        decisions['findings'][0]['classification'] = 'retake'
        self.assertTrue(semantic_review.validate(report, decisions))

    def test_missing_suggestion_or_wrong_transcript_fails(self):
        report = {'source_srt_sha256': 'new', 'cue_count': 1,
                  'suggestions': [{'id': 'a', 'cue_ids': [1]}]}
        decisions = {'source_srt_sha256': 'old',
                     'coverage': [{'cue_ids': [1], 'summary': '内容'}],
                     'whole_transcript_conclusion': '已看完'}
        self.assertEqual(len(semantic_review.validate(report, decisions)), 2)


if __name__ == '__main__':
    unittest.main()
