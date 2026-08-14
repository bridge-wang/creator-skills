#!/usr/bin/env python3
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "srt_calibrate.py"
SPEC = importlib.util.spec_from_file_location("srt_calibrate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
# srt_calibrate.py uses `from __future__ import annotations`, so its
# dataclasses resolve annotations via sys.modules[cls.__module__] at
# decoration time; the module must be registered before exec_module runs.
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_rules(rows):
    rules = []
    for canonical, aliases, mode, context_regex in rows:
        rules.append(MODULE.Rule(canonical, aliases, mode, context_regex, ""))
    return rules


class UnresolvedAliasTests(unittest.TestCase):
    """Regression coverage for the 2026-08-14 incident: a final SRT was
    delivered straight from raw whisper output, skipping term calibration
    entirely, and lint had no way to detect that."""

    def test_flags_an_always_rule_alias_left_uncalibrated(self):
        rules = make_rules([("Claude", ["Cloud"], "always", "")])
        cues = [MODULE.Cue(1, 0, 1000, "旗舰模型Cloud表现很好")]
        hits = MODULE.find_unresolved_aliases(cues, rules)
        self.assertEqual(hits, [(1, "Cloud", "Claude")])

    def test_flags_a_contextual_rule_alias_when_context_matches(self):
        rules = make_rules([("Claude", ["Cloud"], "contextual", "模型|AI")])
        cues = [MODULE.Cue(1, 0, 1000, "旗舰模型Cloud表现很好")]
        hits = MODULE.find_unresolved_aliases(cues, rules)
        self.assertEqual(hits, [(1, "Cloud", "Claude")])

    def test_does_not_flag_contextual_alias_without_matching_context(self):
        rules = make_rules([("Claude", ["Cloud"], "contextual", "模型|AI")])
        cues = [MODULE.Cue(1, 0, 1000, "今天天气不错，白云朵朵")]
        hits = MODULE.find_unresolved_aliases(cues, rules)
        self.assertEqual(hits, [])

    def test_does_not_false_positive_on_already_correct_case_insensitive_canonical(self):
        # canonical "AI" vs alias "ai": case-insensitive matching must not
        # treat the already-correct capitalized form as unresolved.
        rules = make_rules([("AI", ["ai"], "always", "")])
        cues = [MODULE.Cue(1, 0, 1000, "这是一个AI工具")]
        hits = MODULE.find_unresolved_aliases(cues, rules)
        self.assertEqual(hits, [])

    def test_still_flags_the_actual_lowercase_alias(self):
        rules = make_rules([("AI", ["ai"], "always", "")])
        cues = [MODULE.Cue(1, 0, 1000, "这是一个ai工具")]
        hits = MODULE.find_unresolved_aliases(cues, rules)
        self.assertEqual(hits, [(1, "ai", "AI")])

    def test_lint_cues_surfaces_unresolved_alias_warning(self):
        rules = make_rules([("Claude", ["Cloud"], "always", "")])
        cues = [MODULE.Cue(1, 0, 2000, "旗舰模型Cloud表现很好")]
        warnings = MODULE.lint_cues(cues, rules=rules)
        self.assertTrue(any("unresolved alias" in w for w in warnings))

    def test_lint_cues_clean_after_calibration(self):
        rules = make_rules([("Claude", ["Cloud"], "always", "")])
        cues = [MODULE.Cue(1, 0, 2000, "旗舰模型Claude表现很好")]
        warnings = MODULE.lint_cues(cues, rules=rules)
        self.assertEqual(warnings, [])


class DanglingConnectorTests(unittest.TestCase):
    """Regression coverage for cues left ending in a bare 、/，/； after a
    manual split boundary was chosen right at the connector."""

    def test_lint_flags_a_cue_ending_in_a_dangling_pause_mark(self):
        cues = [
            MODULE.Cue(1, 0, 1000, "我好像都没有办法把我脑海中的思路、"),
            MODULE.Cue(2, 1000, 2000, "想法说清楚"),
        ]
        warnings = MODULE.lint_cues(cues)
        self.assertTrue(any("dangling connector" in w for w in warnings))

    def test_split_operation_strips_dangling_connector_automatically(self):
        cues = [MODULE.Cue(1, 0, 4000, "我好像都没有办法把我脑海中的思路、想法说清楚")]
        ops = [
            {
                "type": "split",
                "cue_id": 1,
                "texts": ["我好像都没有办法把我脑海中的思路、", "想法说清楚"],
            }
        ]
        result = MODULE.apply_operations(cues, ops)
        self.assertEqual(result[0].text, "我好像都没有办法把我脑海中的思路")
        self.assertEqual(MODULE.lint_cues(result), [])

    def test_repartition_pair_strips_dangling_connector_automatically(self):
        cues = [
            MODULE.Cue(1, 0, 2000, "所以这节课我想借助这样一个场景、"),
            MODULE.Cue(2, 2000, 3000, "一个demo"),
        ]
        ops = [
            {
                "type": "repartition_pair",
                "left_id": 1,
                "right_id": 2,
                "left_text": "所以这节课我想借助这样一个场景、",
                "right_text": "一个demo",
            }
        ]
        result = MODULE.apply_operations(cues, ops)
        self.assertFalse(result[0].text.endswith("、"))


if __name__ == "__main__":
    unittest.main()
