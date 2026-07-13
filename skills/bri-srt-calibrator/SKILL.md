---
name: bri-srt-calibrator
description: 校准 .srt 字幕文件：修正语境明确的错别字和专名固定写法，按语义合并相邻短字幕，重划分被错误切断的字幕并按字数动态微调时间戳。Use when the user uploads or provides an SRT file and says “校准字幕”, “字幕校准”, “自动校准 SRT”, “调整字幕断句”, “修正字幕错别字”, “合并字幕”, or asks to fix subtitle segmentation/timestamps.
---

# bri-srt-calibrator：SRT 字幕自动校准

Use this skill to produce a corrected `.srt` beside the source file, preserving meaning and spoken tone while fixing obvious recognition errors, subtitle breaks, and timing boundaries.

本文档中 `<SKILL_DIR>` 指本 skill 所在目录（即本 SKILL.md 所在的目录）。

## Workflow

1. Confirm the input is a `.srt` file path or attached subtitle file. Save output beside the source as `<basename>.calibrated.srt` unless the user requests another path.
2. Read `<SKILL_DIR>/references/fixed_terms.tsv` before editing. Treat it as the user-maintained canonical spelling list. Read `<SKILL_DIR>/references/protected_phrases.txt` when boundary repair depends on phrases that must not be split.
3. Run the helper script for a first pass:

```bash
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" auto \
  "/path/to/input.srt" \
  --output "/path/to/input.calibrated.srt"
```

4. Review the output in context. The script is conservative but not a replacement for semantic judgment. Use `preview` to inspect neighboring cues:

```bash
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" preview \
  "/path/to/input.calibrated.srt" --start 35 --end 50
```

5. Do a language-continuity pass before delivery. `lint` catches timing, length, protected phrases, and known semantic-boundary patterns, but it cannot judge every Chinese phrase. Specifically inspect cues where a modifier, idiom, quantifier phrase, time phrase, or prepositional phrase is split across two subtitles.

6. If the automatic pass misses a semantic merge or bad split, create a small operations JSON and apply it:

```json
{
  "operations": [
    {
      "type": "merge",
      "cue_ids": [38, 39],
      "text": "所以说这三者是环环相扣，逐层递进的"
    },
    {
      "type": "repartition_pair",
      "left_id": 44,
      "left_text": "最后来讲一讲物理AI",
      "right_text": "会给我们老百姓的生活"
    },
    {
      "type": "repartition_span",
      "cue_ids": [25, 26],
      "texts": [
        "并且展示我用手机",
        "同时连接一台MacBook笔记本",
        "和一台Mac mini主机的效果"
      ],
      "boundary_ms_list": [51800, 53966]
    }
  ]
}
```

```bash
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" apply \
  "/path/to/input.calibrated.srt" "/path/to/ops.json" \
  --output "/path/to/input.calibrated.srt"
```

7. Validate before delivery:

```bash
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" lint \
  "/path/to/input.calibrated.srt"
```

Return the output path and briefly mention important categories of edits. Do not paste the full subtitle unless the user asks.

## Calibration Rules

Correct obvious recognition errors only when the surrounding context supports the change. Preserve the speaker's argument, order, rhythm, and verbal style.

Fixed spellings:
- Use canonical spellings from `references/fixed_terms.tsv`, such as `ChatGPT`, `Claude`, `harness engineering`, and `孙宇晨`.
- Apply `always` rules directly when the alias is clearly the same term.
- Apply `contextual` rules only when the surrounding cues make the meaning clear. For example, change `cloud` to `Claude` in an LLM/model/Anthropic/code-assistant context, but leave it alone when the speaker means cloud computing or the cloud.
- If a name is ambiguous and not in the fixed list, prefer leaving it unchanged and mention the uncertainty.
- 词表是给使用者自己维护的：转录里反复出现你领域的专名误识别时，把它加进 `fixed_terms.tsv`（格式见文件头注释），下次自动生效。

Timing integrity:
- If a manual edit only corrects words inside a cue, `replace_text` is safe.
- If a manual edit moves any word across a cue boundary, changes two cues into three cues, three cues into two cues, or otherwise changes segmentation across adjacent cues, do not use isolated `replace_text` operations. Use `repartition_pair`, `split`, or `repartition_span` over the entire affected contiguous time span so the new cue starts and ends are recalculated together.
- Preserve the combined start time of the first affected cue and the combined end time of the last affected cue. Assign internal boundaries from audio, a user-provided reference, or proportional text weights. If the user provides a manually calibrated screenshot or timestamp reference with an absolute offset, use its relative cue lengths and gaps, then map those boundaries into the target file's time span.
- After any segmentation edit, run `preview` on the affected range and compare against the original neighboring cues. Watch specifically for words that were moved before or after an existing timestamp boundary; those words require the boundary to move with them.
- Manual `boundary_ms` and `boundary_ms_list` values must be strictly inside the affected cue span and in increasing order. If they fall outside that span, re-check cue ids before applying.

Merging:
- Merge adjacent short cues when they form one continuous idea, especially when the first cue lacks sentence-ending punctuation and the next cue is a short continuation.
- Do not leave a bare conjunction or discourse connector as its own subtitle, such as `但`、`而`、`所以`、`然后`; merge it into the following semantic unit when timing allows.
- When the second cue is a directional/result complement or completes an unfinished object phrase, merge without inserting punctuation. Examples of this abstract pattern include `从...` followed by `搬到...`, or an action phrase followed by its result complement.
- Use `，` only for a natural mid-sentence pause. Use stronger punctuation only when the sentence boundary is explicit.
- Keep merged cue durations from the first cue start to the last cue end.

Language continuity:
- Preserve complete semantic chunks even when each half is individually readable. Examples: `包括我在内` must not become `包括我` / `在内...`; `一年时间内` must not become `一年` / `时间内...`.
- For inclusive modifier phrases like `包括我在内很多人...`, prefer splitting before the whole modifier if length allows: `但它还是引发了` / `包括我在内很多人的一个共鸣`.
- For time-span phrases, keep the preposition + duration + suffix together when possible: `如果你自己想在一年时间内` / `成长为一名AI博主`.
- Treat `在...内`、`从...开始`、`把...变成`、`给...带来`、`包括...在内` as boundary-sensitive patterns. If the existing boundary cuts through the pattern, use `repartition_pair` or `repartition_span`, not isolated `replace_text`.
- A coordinator like `和`、`与`、`跟`、`及` should not start a subtitle when doing so orphans the first conjunct on the previous cue. Patterns like `X和Y的Z`、`X和Y一起做W` describe one joint subject and must stay in a single cue. Example: `这种人` / `和AI协作的skill` is wrong because `这种` modifies the whole `人和AI协作的skill`; the correct split is `这种` / `人和AI协作的skill`, or merge if length allows. When you see a cue starting with `和/与/跟/及` + 名词, check the previous cue's tail — if it ends with a bare noun that is the matched first conjunct, repartition so the coordinator stays with both conjuncts.
- Do not let display-length splitting create dangling fragments such as `包括我`、`在内`、`一年`、`时间内`、`在一年`. If a split produces one, merge to recover the phrase, then split again at the nearest complete semantic boundary.

Repartitioning:
- Repair splits that break a lexical unit, product name, person name, or semantic phrase, such as `物` / `理AI`.
- Repair splits that place a result/completion phrase on the wrong side of a boundary. Prefer keeping the action and its immediate result together, then split before the next contrasted topic or subject if the combined subtitle is too long.
- Preserve the original combined time span for the affected cues.
- Place the new boundary proportionally by text weight unless the user provides an exact timestamp. The helper script does this for `repartition_pair` and `split` operations.

Display length:
- Treat `而物理AI，就是为了做这种现实任务而产生的` as the single-line visual limit reference. The automatic max subtitle length is this reference's character count minus 2 characters for safety.
- Every delivered cue should be at or below that display-length budget. If a semantic merge would exceed the budget, first merge to recover meaning, then split again at the best semantic boundary.
- Prefer split points at existing soft punctuation, before contrast/progression connectors such as `才`、`以及`、`然后`、`但是`, and before a new topic/subject introduced after a result phrase. Avoid splitting inside product names, fixed terms, person names, or protected phrases.
- When the best text-length split would cut through a protected phrase such as `Mac mini`, move the split to before or after the whole phrase, even if that produces a less visually balanced pair.
- For parallel noun phrases with the same repeated head word and no punctuation between them, insert `、` rather than leaving the two items glued together. This applies to list-like phrases such as `A无人机B无人机` without being tied to that exact noun.

Quality bar:
- Keep cue order monotonic and non-overlapping.
- Avoid over-polishing. This is subtitle calibration, not article rewriting.
- Prefer local edits around the broken cues instead of large rewrites.
- For long files, inspect representative changed ranges and run `lint`; do not rely on visual intuition alone. `lint` should fail when any cue exceeds the display-length budget.

## Operation Types

Use operations for precise post-pass fixes:

- `replace_text`: `{ "type": "replace_text", "cue_id": 12, "text": "..." }`
- `merge`: `{ "type": "merge", "cue_ids": [38, 39], "text": "..." }`
- `repartition_pair`: `{ "type": "repartition_pair", "left_id": 44, "left_text": "...", "right_text": "..." }`
- `split`: `{ "type": "split", "cue_id": 9, "texts": ["第一段", "第二段"] }`
- `repartition_span`: `{ "type": "repartition_span", "cue_ids": [25, 26], "texts": ["第一段", "第二段", "第三段"] }`

For `repartition_pair` and `split`, optional `boundary_ms` or `boundary_ms_list` can override proportional timing.
For `repartition_span`, optional `boundary_ms_list` can override proportional timing; it must contain one fewer boundary than the number of output texts.
