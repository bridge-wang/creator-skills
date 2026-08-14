#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TERMS = SKILL_DIR / "references" / "fixed_terms.tsv"
DEFAULT_PHRASES = SKILL_DIR / "references" / "protected_phrases.txt"

TIME_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
SENTENCE_END = set("。！？?!；;…")
SOFT_END = set("，,、：:")
REFERENCE_SINGLE_LINE = "而物理AI，就是为了做这种现实任务而产生的"
DEFAULT_MAX_DISPLAY_CHARS = len(REFERENCE_SINGLE_LINE) - 2
MIN_SPLIT_CHARS = 6


@dataclass
class Cue:
    cue_id: int
    start_ms: int
    end_ms: int
    text: str
    locked_after: bool = False


@dataclass
class Rule:
    canonical: str
    aliases: list[str]
    mode: str
    context_regex: str
    note: str


def parse_timestamp(value: str) -> int:
    hour, minute, rest = value.split(":")
    second, millis = rest.split(",")
    return (
        int(hour) * 3_600_000
        + int(minute) * 60_000
        + int(second) * 1_000
        + int(millis)
    )


def format_timestamp(ms: int) -> str:
    ms = max(0, int(round(ms)))
    hour, ms = divmod(ms, 3_600_000)
    minute, ms = divmod(ms, 60_000)
    second, millis = divmod(ms, 1_000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s+([，。！？、；：,.!?;:])", r"\1", text)
    text = re.sub(r"([（(])\s+", r"\1", text)
    text = re.sub(r"\s+([）)])", r"\1", text)
    return text


def display_length(text: str) -> int:
    return len(re.sub(r"\s+", "", normalize_text(text)))


def parse_srt(path: Path) -> list[Cue]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block for block in re.split(r"\n\s*\n", raw.strip()) if block.strip()]
    cues: list[Cue] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n") if line.strip()]
        time_line_index = None
        match = None
        for i, line in enumerate(lines):
            match = TIME_RE.search(line)
            if match:
                time_line_index = i
                break
        if time_line_index is None or match is None:
            continue
        cue_id = len(cues) + 1
        if time_line_index > 0 and lines[time_line_index - 1].strip().isdigit():
            cue_id = int(lines[time_line_index - 1].strip())
        text = normalize_text(" ".join(lines[time_line_index + 1 :]))
        cues.append(
            Cue(
                cue_id=cue_id,
                start_ms=parse_timestamp(match.group("start")),
                end_ms=parse_timestamp(match.group("end")),
                text=text,
            )
        )
    if not cues:
        raise ValueError(f"No SRT cues found in {path}")
    return cues


def render_srt(cues: list[Cue]) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(cue.start_ms)} --> {format_timestamp(cue.end_ms)}",
                    normalize_text(cue.text),
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def write_srt(cues: list[Cue], path: Path) -> None:
    path.write_text(render_srt(cues), encoding="utf-8")


def split_aliases(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


def load_rules(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    if not path.exists():
        return rules
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = raw_line.split("\t")
        while len(parts) < 5:
            parts.append("")
        canonical, aliases, mode, context_regex, note = [part.strip() for part in parts[:5]]
        if canonical and aliases:
            rules.append(
                Rule(
                    canonical=canonical,
                    aliases=split_aliases(aliases),
                    mode=mode or "always",
                    context_regex=context_regex,
                    note=note,
                )
            )
    return rules


def load_phrases(path: Path, rules: list[Rule]) -> list[str]:
    phrases: set[str] = {rule.canonical for rule in rules if rule.canonical}
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#"):
                phrases.add(line)
    return sorted(phrases, key=len, reverse=True)


def alias_pattern(alias: str) -> str:
    if alias.startswith("re:"):
        return alias[3:]
    escaped = re.escape(alias)
    escaped = escaped.replace(r"\ ", r"\s+")
    if re.fullmatch(r"[A-Za-z0-9 ]+", alias):
        return rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    return escaped


def apply_rules(cues: list[Cue], rules: list[Rule]) -> int:
    changed = 0
    for i, cue in enumerate(cues):
        before = cue.text
        context = " ".join(
            part.text
            for part in cues[max(0, i - 2) : min(len(cues), i + 3)]
            if part.text
        )
        for rule in rules:
            if rule.mode == "contextual":
                if not rule.context_regex or not re.search(rule.context_regex, context, flags=re.I):
                    continue
            for alias in rule.aliases:
                cue.text = re.sub(alias_pattern(alias), rule.canonical, cue.text, flags=re.I)
        cue.text = normalize_text(cue.text)
        if cue.text != before:
            changed += 1
    return changed


def text_weight(text: str) -> float:
    weight = 0.0
    latin_run = 0
    for ch in text:
        if ch.isascii() and ch.isalnum():
            latin_run += 1
            continue
        if latin_run:
            weight += max(1.0, latin_run * 0.7)
            latin_run = 0
        if "\u4e00" <= ch <= "\u9fff":
            weight += 1.0
        elif ch in SENTENCE_END or ch in SOFT_END or ch.isspace():
            weight += 0.0
        else:
            weight += 0.5
    if latin_run:
        weight += max(1.0, latin_run * 0.7)
    return max(weight, 1.0)


def ends_sentence(text: str) -> bool:
    stripped = text.rstrip("”’\"')】）")
    return bool(stripped) and stripped[-1] in SENTENCE_END


def continuation_start(text: str) -> bool:
    return bool(
        re.match(
            r"^(所以|因此|然后|而且|但是|不过|以及|同时|逐层|递进|也|就|才|又|再|把|给|对|在|会|能|要|可以|的是|的话|理AI|搬到|来到|进入|回到|落到|变成|成为|用于|实现|完成|吃饱了)",
            text,
        )
    )


def standalone_discourse_marker(text: str) -> bool:
    normalized = normalize_text(text)
    if re.fullmatch(
        r"(要知道|回想一下|我们可以说|这是因为|三年前|换句话说|也就是说|简单来说|说白了)",
        normalized,
    ):
        return True
    if re.match(r"^(所以你看|所以你想|所以说|你看|你想|我跟你讲|我跟你说|我告诉你)", normalized):
        return True
    return False


def leading_conjunction(text: str) -> bool:
    return bool(re.fullmatch(r"(但|而|可|又|再|就|才|然后|所以|因此|不过|而且|同时)", normalize_text(text)))


def no_comma_continuation(left: str, right: str) -> bool:
    right = normalize_text(right)
    if leading_conjunction(left):
        return True
    if re.match(r"^(搬到|来到|进入|回到|落到|带到|送到|运到|放到|移到)", right):
        return True
    if re.match(r"^(变成|成为|用于|实现|完成|产生|发生|出现)", right):
        return True
    if re.match(r"^(吃饱了|做好了|做完了|说完了|讲完了|看完了|跑通了)", right):
        return True
    return bool(re.search(r"(从|把|给|将|被|让)[^，。！？；;]*$", normalize_text(left)))


def join_text(left: str, right: str, connector: str = "，") -> str:
    left = normalize_text(left)
    right = normalize_text(right)
    if not left:
        return right
    if not right:
        return left
    if left[-1] in SOFT_END or right[0] in SENTENCE_END or right[0] in SOFT_END:
        return normalize_text(left + right)
    if ends_sentence(left):
        return normalize_text(left + right)
    if no_comma_continuation(left, right):
        return normalize_text(left + right)
    return normalize_text(left + connector + right)


def should_merge(left: Cue, right: Cue, max_chars: int) -> bool:
    if left.locked_after:
        return False
    gap = right.start_ms - left.end_ms
    if gap > 180 or gap < -20:
        return False
    if ends_sentence(left.text):
        return False
    if standalone_discourse_marker(right.text):
        return False
    combined_len = display_length(left.text + right.text)
    if combined_len > max_chars:
        return False
    if re.search(r"(说|表示|认为|提到|指出)$", right.text) and text_weight(right.text) <= 8:
        return False
    if leading_conjunction(left.text):
        return True
    if left.text.endswith(tuple(SOFT_END)):
        return True
    if text_weight(left.text) <= 12:
        return continuation_start(right.text)
    return continuation_start(right.text)


def merge_continuations(cues: list[Cue], max_chars: int) -> tuple[list[Cue], int]:
    merged: list[Cue] = []
    count = 0
    i = 0
    while i < len(cues):
        current = Cue(
            cues[i].cue_id,
            cues[i].start_ms,
            cues[i].end_ms,
            cues[i].text,
            cues[i].locked_after,
        )
        i += 1
        while i < len(cues) and should_merge(current, cues[i], max_chars):
            current.end_ms = cues[i].end_ms
            current.text = join_text(current.text, cues[i].text)
            count += 1
            i += 1
        merged.append(current)
    return merged, count


def clean_split_piece(text: str) -> str:
    return normalize_text(text.strip(" ，,、；;：:"))


def forbidden_split_indexes(text: str, phrases: Iterable[str] = ()) -> set[int]:
    normalized = normalize_text(text)
    normalized_lower = normalized.lower()
    forbidden: set[int] = set()
    for phrase in phrases:
        phrase = normalize_text(str(phrase))
        if len(phrase) < 2:
            continue
        phrase_lower = phrase.lower()
        start = 0
        while True:
            position = normalized_lower.find(phrase_lower, start)
            if position < 0:
                break
            forbidden.update(range(position + 1, position + len(phrase)))
            start = position + 1
    return forbidden


def protected_phrase_split(left: str, right: str, phrases: Iterable[str]) -> str | None:
    for phrase in phrases:
        if protected_phrase_boundary_match(left, right, phrase):
            return normalize_text(str(phrase))
    return None


def protected_phrase_boundary_candidates(phrase: str) -> list[tuple[str, str, str]]:
    phrase = normalize_text(phrase)
    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for split_at in range(1, len(phrase)):
        prefix = phrase[:split_at]
        suffix = phrase[split_at:]
        variants = [(prefix, suffix, suffix)]

        compact_prefix = prefix.rstrip()
        compact_suffix = suffix.lstrip()
        boundary_gap = prefix[len(compact_prefix) :] + suffix[: len(suffix) - len(compact_suffix)]
        if compact_prefix and compact_suffix and boundary_gap:
            variants.append((compact_prefix, compact_suffix, boundary_gap + compact_suffix))

        for variant in variants:
            if variant[0] and variant[1] and variant not in seen:
                candidates.append(variant)
                seen.add(variant)
    return candidates


def protected_phrase_boundary_match(left: str, right: str, phrase: str) -> tuple[str, str] | None:
    left_lower = normalize_text(left).lower()
    right_lower = normalize_text(right).lower()
    for prefix, right_prefix, append_suffix in protected_phrase_boundary_candidates(str(phrase)):
        if left_lower.endswith(prefix.lower()) and right_lower.startswith(right_prefix.lower()):
            return right_prefix, append_suffix
    return None


TIME_AMOUNT_RE = re.compile(
    r"([零一二两三四五六七八九十百千万半\d]+(?:年|个月|月|周|星期|天|日|小时|分钟|秒))$"
)
TIME_SUFFIX_RE = re.compile(r"^(时间内|之内|以内|内|之后|以后|之前|前|以来|左右)")
INCLUSIVE_PREFIX_RE = re.compile(r"(包括[^\s，。！？、；：,.!?;:]{1,12})$")
BOUNDARY_EDGE_CHARS = " ，,、。；;：:"


def strip_boundary_edges(text: str) -> str:
    return normalize_text(text.strip(BOUNDARY_EDGE_CHARS))


def semantic_boundary_issue(left: str, right: str) -> str | None:
    left = normalize_text(left)
    right = normalize_text(right)
    if INCLUSIVE_PREFIX_RE.search(left) and right.startswith("在内"):
        return "inclusive phrase split (包括...在内)"
    if TIME_AMOUNT_RE.search(left) and TIME_SUFFIX_RE.match(right):
        return "time-span phrase split"
    return None


def repartition_pair_texts(left: Cue, right: Cue, left_text: str, right_text: str) -> None:
    left_text = normalize_text(left_text)
    right_text = normalize_text(right_text)
    boundary = proportional_boundaries(left.start_ms, right.end_ms, [left_text, right_text])[0]
    left.end_ms = boundary
    right.start_ms = boundary
    left.text = left_text
    right.text = right_text
    left.locked_after = True


def repair_semantic_boundaries(cues: list[Cue], max_chars: int) -> int:
    changes = 0
    for i in range(len(cues) - 1):
        left = cues[i]
        right = cues[i + 1]
        left_text = normalize_text(left.text)
        right_text = normalize_text(right.text)

        inclusive = INCLUSIVE_PREFIX_RE.search(left_text)
        if inclusive and right_text.startswith("在内"):
            prefix = strip_boundary_edges(left_text[: inclusive.start()])
            rest = strip_boundary_edges(right_text[len("在内") :])
            new_right = normalize_text(inclusive.group(1) + "在内" + rest)
            if prefix and new_right and display_length(prefix) <= max_chars:
                repartition_pair_texts(left, right, prefix, new_right)
                changes += 1
                continue

        time_amount = TIME_AMOUNT_RE.search(left_text)
        time_suffix = TIME_SUFFIX_RE.match(right_text)
        if time_amount and time_suffix:
            suffix = time_suffix.group(1)
            rest = strip_boundary_edges(right_text[len(suffix) :])
            new_left = normalize_text(left_text + suffix)
            if rest and display_length(new_left) <= max_chars:
                repartition_pair_texts(left, right, new_left, rest)
                changes += 1
                continue

    return changes


def split_candidates(text: str, max_chars: int, phrases: Iterable[str] = ()) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    normalized = normalize_text(text)
    normalized_lower = normalized.lower()
    forbidden = forbidden_split_indexes(normalized, phrases)
    for phrase in phrases:
        phrase = normalize_text(str(phrase))
        if len(phrase) < 2:
            continue
        phrase_lower = phrase.lower()
        start = 0
        while True:
            position = normalized_lower.find(phrase_lower, start)
            if position < 0:
                break
            phrase_end = position + len(phrase)
            left_len = display_length(normalized[:position])
            if MIN_SPLIT_CHARS <= left_len <= max_chars and position not in forbidden:
                candidates.append((position, 85))
            left_len = display_length(normalized[:phrase_end])
            if MIN_SPLIT_CHARS <= left_len <= max_chars and phrase_end not in forbidden:
                candidates.append((phrase_end, 75))
            start = position + 1
    for i, ch in enumerate(normalized):
        left_len = display_length(normalized[: i + 1])
        if left_len < MIN_SPLIT_CHARS or left_len > max_chars:
            continue
        if ch in SOFT_END:
            if i + 1 not in forbidden:
                candidates.append((i + 1, 80))
        if ch in "的了":
            if i + 1 not in forbidden:
                candidates.append((i + 1, 15))

    connector_re = re.compile(
        r"(才|以及|然后|所以|因此|但是|不过|而且|同时|会|能|可以|把|给|再|也|就|"
        r"前[\u4e00-\u9fff]{0,6}(?=就|也|都|并|不)|"
        r"后[\u4e00-\u9fff]{0,6}(?=就|也|都|并|不))"
    )
    for match in connector_re.finditer(normalized):
        i = match.start()
        left_len = display_length(normalized[:i])
        if MIN_SPLIT_CHARS <= left_len <= max_chars and i not in forbidden:
            score = 65 if match.group(0).startswith(("前", "后")) else 45
            candidates.append((i, score))
    return candidates


def best_split_index(text: str, max_chars: int, phrases: Iterable[str] = ()) -> int:
    candidates = split_candidates(text, max_chars, phrases)
    if candidates:
        target = max(MIN_SPLIT_CHARS, int(max_chars * 0.75))
        return max(
            candidates,
            key=lambda item: (
                item[1],
                -abs(display_length(text[: item[0]]) - target),
                display_length(text[: item[0]]),
            ),
        )[0]

    normalized = normalize_text(text)
    forbidden = forbidden_split_indexes(normalized, phrases)
    running = 0
    for i, ch in enumerate(normalized):
        if ch.isspace():
            continue
        running += 1
        if running >= max_chars:
            proposed = i + 1
            for index in range(proposed, 0, -1):
                left = clean_split_piece(normalized[:index])
                right = clean_split_piece(normalized[index:])
                if (
                    index not in forbidden
                    and display_length(left) >= MIN_SPLIT_CHARS
                    and display_length(left) <= max_chars
                    and right
                ):
                    return index
            for index in range(proposed + 1, len(normalized)):
                left = clean_split_piece(normalized[:index])
                right = clean_split_piece(normalized[index:])
                if index not in forbidden and display_length(left) >= MIN_SPLIT_CHARS and right:
                    return index
            return i + 1
    return len(normalized)


def split_text_to_display_limit(text: str, max_chars: int, phrases: Iterable[str] = ()) -> list[str]:
    text = normalize_text(text)
    if display_length(text) <= max_chars:
        return [text]
    index = best_split_index(text, max_chars, phrases)
    left = clean_split_piece(text[:index])
    right = clean_split_piece(text[index:])
    if not left or not right:
        return [text]
    return [left] + split_text_to_display_limit(right, max_chars, phrases)


def split_long_cues(cues: list[Cue], max_chars: int, phrases: Iterable[str] = ()) -> tuple[list[Cue], int]:
    result: list[Cue] = []
    split_count = 0
    for cue in cues:
        parts = split_text_to_display_limit(cue.text, max_chars, phrases)
        if len(parts) == 1:
            result.append(cue)
            continue
        split_count += len(parts) - 1
        boundaries = proportional_boundaries(cue.start_ms, cue.end_ms, parts)
        starts = [cue.start_ms] + boundaries
        ends = boundaries + [cue.end_ms]
        for index, part in enumerate(parts):
            result.append(Cue(cue.cue_id + index, starts[index], ends[index], part))
    return result, split_count


def punctuate_parallel_terms(cues: list[Cue]) -> int:
    changed = 0
    repeated_head = re.compile(
        r"(?<![、，,])((?:(?![和与跟及、，,])[\u4e00-\u9fff]){2,8}?)"
        r"(无人机|机器人|模型|公司|行业|任务|场景|工具|系统|"
        r"平台|框架|技术|产品|材料|基地|房屋|层|环节|阶段|步骤|维度|方向|领域|赛道)"
        r"((?:(?![和与跟及、，,])[\u4e00-\u9fff]){2,8}?)\2"
    )
    repeated_verb_object = re.compile(r"(?<![、，,])(登陆[\u4e00-\u9fff]{2})(登陆[\u4e00-\u9fff]{2})")
    for cue in cues:
        before = cue.text
        cue.text = repeated_head.sub(r"\1\2、\3\2", cue.text)
        cue.text = repeated_verb_object.sub(r"\1、\2", cue.text)
        cue.text = normalize_text(cue.text)
        if cue.text != before:
            changed += 1
    return changed


def proportional_boundaries(start_ms: int, end_ms: int, texts: list[str]) -> list[int]:
    duration = max(1, end_ms - start_ms)
    weights = [text_weight(text) for text in texts]
    total = sum(weights) or 1.0
    boundaries: list[int] = []
    running = 0.0
    for weight in weights[:-1]:
        running += weight
        boundaries.append(int(round(start_ms + duration * running / total)))
    min_piece = min(280, max(80, duration // (len(texts) * 3)))
    previous = start_ms
    clamped: list[int] = []
    for boundary_index, boundary in enumerate(boundaries):
        remaining = len(texts) - boundary_index - 1
        low = previous + min_piece
        high = end_ms - remaining * min_piece
        boundary = min(max(boundary, low), high)
        clamped.append(boundary)
        previous = boundary
    return clamped


def validate_boundaries(start_ms: int, end_ms: int, boundaries: list[int], op_type: str) -> None:
    previous = start_ms
    for boundary in boundaries:
        if boundary <= previous or boundary >= end_ms:
            raise ValueError(
                f"{op_type} boundaries must be strictly inside "
                f"{format_timestamp(start_ms)} --> {format_timestamp(end_ms)}"
            )
        previous = boundary


def repair_protected_boundaries(cues: list[Cue], phrases: list[str]) -> int:
    changes = 0
    i = 0
    while i < len(cues) - 1:
        left = cues[i]
        right = cues[i + 1]
        repaired = False
        for phrase in phrases:
            if len(phrase) < 2:
                continue
            match = protected_phrase_boundary_match(left.text, right.text, phrase)
            if match:
                right_prefix, append_suffix = match
                new_left = left.text + append_suffix
                new_right = right.text[len(right_prefix) :].lstrip("，,、。；;：: ")
                if not new_right:
                    left.end_ms = right.end_ms
                    left.text = normalize_text(new_left)
                    del cues[i + 1]
                else:
                    start_ms = left.start_ms
                    end_ms = right.end_ms
                    boundary = proportional_boundaries(start_ms, end_ms, [new_left, new_right])[0]
                    left.end_ms = boundary
                    right.start_ms = boundary
                    left.text = normalize_text(new_left)
                    right.text = normalize_text(new_right)
                    left.locked_after = True
                changes += 1
                repaired = True
            if repaired:
                break
        i += 1
    return changes


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}.calibrated{input_path.suffix}")


def lint_cues(
    cues: list[Cue],
    max_chars: int = DEFAULT_MAX_DISPLAY_CHARS,
    phrases: Iterable[str] = (),
) -> list[str]:
    warnings: list[str] = []
    previous_end = -1
    for index, cue in enumerate(cues, 1):
        if cue.end_ms <= cue.start_ms:
            warnings.append(f"cue {index}: non-positive duration")
        if cue.start_ms < previous_end:
            warnings.append(f"cue {index}: overlaps previous cue")
        if not cue.text.strip():
            warnings.append(f"cue {index}: empty text")
        duration_s = max((cue.end_ms - cue.start_ms) / 1000, 0.001)
        cps = text_weight(cue.text) / duration_s
        if cps > 18:
            warnings.append(f"cue {index}: high text density ({cps:.1f} weighted chars/s)")
        length = display_length(cue.text)
        if length > max_chars:
            warnings.append(f"cue {index}: over display length ({length}>{max_chars})")
        if index < len(cues):
            phrase = protected_phrase_split(cue.text, cues[index].text, phrases)
            if phrase:
                warnings.append(f"cue {index}: protected phrase split across next cue ({phrase})")
            else:
                issue = semantic_boundary_issue(cue.text, cues[index].text)
                if issue:
                    warnings.append(f"cue {index}: {issue} across next cue")
        previous_end = cue.end_ms
    return warnings


def find_position(cues: list[Cue], cue_id: int) -> int:
    for position, cue in enumerate(cues):
        if cue.cue_id == cue_id:
            return position
    raise ValueError(f"cue_id {cue_id} not found")


def apply_operations(cues: list[Cue], operations: Iterable[dict]) -> list[Cue]:
    cues = [Cue(cue.cue_id, cue.start_ms, cue.end_ms, cue.text) for cue in cues]
    for op in operations:
        op_type = op.get("type")
        if op_type == "replace_text":
            cue = cues[find_position(cues, int(op["cue_id"]))]
            cue.text = normalize_text(str(op["text"]))
        elif op_type == "merge":
            cue_ids = [int(value) for value in op["cue_ids"]]
            positions = [find_position(cues, cue_id) for cue_id in cue_ids]
            if positions != list(range(min(positions), max(positions) + 1)):
                raise ValueError(f"merge cue_ids must be contiguous: {cue_ids}")
            selected = cues[min(positions) : max(positions) + 1]
            text = op.get("text")
            if text is None:
                text = selected[0].text
                for cue in selected[1:]:
                    text = join_text(text, cue.text)
            merged = Cue(selected[0].cue_id, selected[0].start_ms, selected[-1].end_ms, normalize_text(str(text)))
            cues[min(positions) : max(positions) + 1] = [merged]
        elif op_type == "repartition_pair":
            left_pos = find_position(cues, int(op["left_id"]))
            right_pos = find_position(cues, int(op["right_id"])) if "right_id" in op else left_pos + 1
            if right_pos != left_pos + 1:
                raise ValueError("repartition_pair requires adjacent cues")
            left = cues[left_pos]
            right = cues[right_pos]
            left_text = normalize_text(str(op["left_text"]))
            right_text = normalize_text(str(op["right_text"]))
            boundary = op.get("boundary_ms")
            if boundary is None:
                boundary = proportional_boundaries(left.start_ms, right.end_ms, [left_text, right_text])[0]
            boundary = int(boundary)
            validate_boundaries(left.start_ms, right.end_ms, [boundary], "repartition_pair")
            left.end_ms = boundary
            right.start_ms = boundary
            left.text = left_text
            right.text = right_text
        elif op_type == "split":
            position = find_position(cues, int(op["cue_id"]))
            original = cues[position]
            texts = [normalize_text(str(text)) for text in op["texts"]]
            if len(texts) < 2:
                raise ValueError("split requires at least two texts")
            boundaries = op.get("boundary_ms_list")
            if boundaries is None:
                boundaries = proportional_boundaries(original.start_ms, original.end_ms, texts)
            boundaries = [int(value) for value in boundaries]
            if len(boundaries) != len(texts) - 1:
                raise ValueError("boundary_ms_list length must be len(texts) - 1")
            validate_boundaries(original.start_ms, original.end_ms, boundaries, "split")
            starts = [original.start_ms] + boundaries
            ends = boundaries + [original.end_ms]
            replacement = [
                Cue(original.cue_id + offset, starts[offset], ends[offset], texts[offset])
                for offset in range(len(texts))
            ]
            cues[position : position + 1] = replacement
        elif op_type == "repartition_span":
            cue_ids = [int(value) for value in op["cue_ids"]]
            positions = [find_position(cues, cue_id) for cue_id in cue_ids]
            if positions != list(range(min(positions), max(positions) + 1)):
                raise ValueError(f"repartition_span cue_ids must be contiguous: {cue_ids}")
            selected = cues[min(positions) : max(positions) + 1]
            texts = [normalize_text(str(text)) for text in op["texts"]]
            if len(texts) < 2:
                raise ValueError("repartition_span requires at least two texts")
            boundaries = op.get("boundary_ms_list")
            if boundaries is None:
                boundaries = proportional_boundaries(selected[0].start_ms, selected[-1].end_ms, texts)
            boundaries = [int(value) for value in boundaries]
            if len(boundaries) != len(texts) - 1:
                raise ValueError("boundary_ms_list length must be len(texts) - 1")
            validate_boundaries(selected[0].start_ms, selected[-1].end_ms, boundaries, "repartition_span")
            starts = [selected[0].start_ms] + boundaries
            ends = boundaries + [selected[-1].end_ms]
            replacement = [
                Cue(selected[0].cue_id + offset, starts[offset], ends[offset], texts[offset])
                for offset in range(len(texts))
            ]
            cues[min(positions) : max(positions) + 1] = replacement
        else:
            raise ValueError(f"unknown operation type: {op_type}")
    return cues


def cmd_auto(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path(input_path)
    cues = parse_srt(input_path)
    rules = load_rules(Path(args.terms))
    phrases = load_phrases(Path(args.phrases), rules)
    changed_terms = apply_rules(cues, rules)
    punctuated = punctuate_parallel_terms(cues)
    semantic_repaired = 0
    repaired = 0
    # Preserve Whisper's timestamp anchors by default. Proportional boundary
    # rewrites are useful only after audio review; otherwise they can make a
    # subtitle appear before or after the matching speech.
    if not args.no_boundary_repair and not args.preserve_timing:
        semantic_repaired = repair_semantic_boundaries(cues, args.max_chars)
        repaired = repair_protected_boundaries(cues, phrases)
    merged = 0
    split_count = 0
    if not args.no_merge and not args.preserve_timing:
        cues, merged = merge_continuations(cues, args.max_chars * 2)
    if not args.no_split and not args.preserve_timing:
        cues, split_count = split_long_cues(cues, args.max_chars, phrases)
    write_srt(cues, output_path)
    print(f"wrote {output_path}")
    print(
        "term-normalized cues: "
        f"{changed_terms}; punctuation fixes: {punctuated}; "
        f"semantic repairs: {semantic_repaired}; boundary repairs: {repaired}; "
        f"merges: {merged}; splits: {split_count}; "
        f"max display chars: {args.max_chars}"
    )
    warnings = lint_cues(cues, args.max_chars, phrases)
    if warnings:
        print("lint warnings:", file=sys.stderr)
        for warning in warnings:
            print(f"- {warning}", file=sys.stderr)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    ops_path = Path(args.operations)
    output_path = Path(args.output) if args.output else default_output_path(input_path)
    cues = parse_srt(input_path)
    rules = load_rules(Path(args.terms))
    phrases = load_phrases(Path(args.phrases), rules)
    payload = json.loads(ops_path.read_text(encoding="utf-8"))
    operations = payload["operations"] if isinstance(payload, dict) else payload
    cues = apply_operations(cues, operations)
    write_srt(cues, output_path)
    print(f"wrote {output_path}")
    warnings = lint_cues(cues, args.max_chars, phrases)
    if warnings:
        print("lint warnings:", file=sys.stderr)
        for warning in warnings:
            print(f"- {warning}", file=sys.stderr)
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    cues = parse_srt(Path(args.input))
    start = args.start or 1
    end = args.end or len(cues)
    for display_index, cue in enumerate(cues, 1):
        if start <= display_index <= end or start <= cue.cue_id <= end:
            duration = (cue.end_ms - cue.start_ms) / 1000
            print(
                f"{display_index:>4} id={cue.cue_id:<4} "
                f"{format_timestamp(cue.start_ms)} --> {format_timestamp(cue.end_ms)} "
                f"({duration:.2f}s, w={text_weight(cue.text):.1f}) {cue.text}"
            )
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    cues = parse_srt(Path(args.input))
    rules = load_rules(Path(args.terms))
    phrases = load_phrases(Path(args.phrases), rules)
    warnings = lint_cues(cues, args.max_chars, phrases)
    if not warnings:
        print("OK")
        return 0
    for warning in warnings:
        print(warning)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate SRT subtitle text and timing.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auto = subparsers.add_parser("auto", help="Run a conservative automatic calibration pass.")
    auto.add_argument("input")
    auto.add_argument("--output")
    auto.add_argument("--terms", default=str(DEFAULT_TERMS))
    auto.add_argument("--phrases", default=str(DEFAULT_PHRASES))
    auto.add_argument("--max-chars", type=int, default=DEFAULT_MAX_DISPLAY_CHARS)
    auto.add_argument(
        "--preserve-timing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="preserve Whisper segment boundaries (default); disable only after audio review",
    )
    auto.add_argument("--no-merge", action="store_true")
    auto.add_argument("--no-split", action="store_true")
    auto.add_argument("--no-boundary-repair", action="store_true")
    auto.set_defaults(func=cmd_auto)

    apply = subparsers.add_parser("apply", help="Apply curated JSON operations.")
    apply.add_argument("input")
    apply.add_argument("operations")
    apply.add_argument("--output")
    apply.add_argument("--terms", default=str(DEFAULT_TERMS))
    apply.add_argument("--phrases", default=str(DEFAULT_PHRASES))
    apply.add_argument("--max-chars", type=int, default=DEFAULT_MAX_DISPLAY_CHARS)
    apply.set_defaults(func=cmd_apply)

    preview = subparsers.add_parser("preview", help="Print cue ranges for review.")
    preview.add_argument("input")
    preview.add_argument("--start", type=int)
    preview.add_argument("--end", type=int)
    preview.set_defaults(func=cmd_preview)

    lint = subparsers.add_parser("lint", help="Validate monotonic timing and text density.")
    lint.add_argument("input")
    lint.add_argument("--terms", default=str(DEFAULT_TERMS))
    lint.add_argument("--phrases", default=str(DEFAULT_PHRASES))
    lint.add_argument("--max-chars", type=int, default=DEFAULT_MAX_DISPLAY_CHARS)
    lint.set_defaults(func=cmd_lint)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
