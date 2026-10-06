import importlib.util
import json
import math
import array
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import autocut
import pause_profile


class PacingTests(unittest.TestCase):
    def test_reference_statistics_exclude_edges_and_non_speech_section(self):
        result = pause_profile.summarize([[0, .7], [1, 1.2], [2, 2.4], [4, 6], [9, 10]],
                                         10, [[0, 3], [7, 10]])
        self.assertEqual(result['count'], 2)
        self.assertAlmostEqual(result['mean_seconds'], .3)
        self.assertAlmostEqual(result['median_seconds'], .3)

    def test_no_gaps_cannot_create_a_bogus_zero_target(self):
        with self.assertRaises(ValueError):
            pause_profile.summarize([[0, 10]], 10)

    def test_planning_refuses_candidate_only_validation(self):
        with self.assertRaisesRegex(SystemExit, '全稿语义复核'):
            autocut.main(['source.mp4', 'cuts.json', 'out.mp4', '--plan-only', '--report', 'plan.json'])

    def test_merge_never_bridges_a_low_voice_word(self):
        self.assertEqual(pause_profile.merge_microbreaks([[0, 1], [1.55, 2]]), [[0, 1], [1.55, 2]])

    def test_retake_join_counts_both_halves_of_silence(self):
        keep = autocut.cap_acoustic_pauses([(0, 1), (3, 5)], [[.8, 1], [3, 3.46]], .3, .17, .37)
        self.assertAlmostEqual(sum(b-a for a,b in keep), 3-.36)
        self.assertTrue(all(b <= 1 or a >= 3 for a,b in keep))
        self.assertTrue(any(a <= 3.46 < b for a,b in keep))

    def test_fractional_retake_join_does_not_split_one_pause_into_two(self):
        keep = [(0, 27.8), (31.136125, 40.25)]
        silences = [(27.6265, 27.8), (31.136125, 31.596125)]
        result = autocut.cap_acoustic_pauses(keep, silences, .307969, .17, .37)
        removed = sum(b-a for a,b in keep) - sum(b-a for a,b in result)
        self.assertAlmostEqual(removed, (.1735 + .46) - .307969)

    def test_silence_compression_preserves_low_speech_and_only_exact_operation_window(self):
        keep = autocut.cap_acoustic_pauses([(0, 12)], [[1, 10]], .3, .17, .37, [(5, 6)])
        self.assertEqual(keep, [(0, 1.15), (5, 6), (9.85, 12)])
        keep = autocut.cap_acoustic_pauses([(0, 4)], [[1, 1.4], [1.6, 2.1]], .3, .17, .37)
        self.assertTrue(any(a <= 1.4 and b >= 1.6 for a,b in keep))

    def test_long_and_short_video_choose_different_targets_with_course_alias(self):
        short = autocut.parse_args(['a.mp4', 'cuts.json', 'b.mp4'])
        long = autocut.parse_args(['a.mp4', 'cuts.json', 'b.mp4', '--pacing-profile', 'long_video'])
        course = autocut.parse_args(['a.mp4', 'cuts.json', 'b.mp4', '--pacing-profile', 'course'])
        self.assertAlmostEqual(short.max_pause, .307969)
        self.assertEqual(long.max_pause, .7)
        self.assertEqual(course.max_pause, long.max_pause)
        self.assertEqual(course.pacing_profile, 'long_video')

    @unittest.skipUnless(shutil.which('ffmpeg'), 'requires ffmpeg')
    def test_real_audio_detector_measures_known_pauses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'speech-surrogate.wav'
            samples = array.array('h')
            for duration, voiced in [(.2, False), (1, True), (.3, False),
                                     (1, True), (.6, False), (1, True), (.2, False)]:
                samples.extend(int(10000*math.sin(2*math.pi*220*i/16000)) if voiced else 0
                               for i in range(round(duration*16000)))
            with wave.open(str(path), 'wb') as output:
                output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                output.writeframes(samples.tobytes())
            duration, gaps = pause_profile.detect(path)
            result = pause_profile.summarize(gaps, duration)
            self.assertEqual(result['count'], 2)
            self.assertAlmostEqual(result['median_seconds'], .45, delta=.002)


if __name__ == '__main__':
    unittest.main()
