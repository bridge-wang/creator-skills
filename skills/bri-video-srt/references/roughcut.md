# 阶段 A：粗剪

依赖顺序是：源时间轴与转写 → 独立子 Agent 全文文字取舍 → 主 Agent 原音复核和操作审计 → 切点短预览 → 整片音频预览 → 导出 → 同轴字幕和验收。独立读取与分析可以并行，不能绕过导出前的验证。

## 1. 源片与转写

运行 `scripts/check_setup.sh`，用 ffprobe 确认音轨、时长、源 `r_frame_rate` 和 `start_time`。以下命令中的路径变量必须替换为当前任务的真实路径。

```bash
ffmpeg -nostdin -v error -y -i "$VIDEO_SOURCE" -vn \
  -af 'aresample=async=1:first_pts=0' -ar 16000 -ac 1 -c:a pcm_s16le "$VIDEO_TMP/source.wav"
whisper-cli -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 --output-srt --output-file "$VIDEO_TMP/source.raw" "$VIDEO_TMP/source.wav"
```

用已对齐 WAV 做声音候选检测，显式指定源标称帧率，避免 auto-editor v1 隐含平均帧率导致切点错位：

```bash
auto-editor "$VIDEO_TMP/source.wav" --frame-rate "$VIDEO_FPS" \
  --edit 'audio:threshold=4%' --margin 0sec --export v1 -o "$VIDEO_TMP/auto.json"
```

声音候选只是线索。短视频和长视频最终删静音均使用真实声学停顿，目标读取所选模式配置。

## 2. 独立子 Agent 先审完整文字稿

先按 [全文审稿](transcript-reader.md) 创建新的文字审稿子 Agent。它只接收完整原文、cue ID 和用户明确的版本偏好；通过大语言模型理解整段重录、单句纠错和句内重启，输出 `selection.json`。固定规则只校验来源、覆盖、顺序和引用，不负责决定重复。主 Agent 不把邻近相似候选当成子 Agent 的全文输入。

```bash
python3 scripts/transcript_selection.py prepare --srt "$VIDEO_TMP/source.raw.srt" \
  --output "$VIDEO_TMP/transcript-input.json"
# 创建子 Agent，等待其按全文审稿说明写出 selection.json。
python3 scripts/transcript_selection.py validate --srt "$VIDEO_TMP/source.raw.srt" \
  --selection "$VIDEO_TMP/selection.json" --report "$VIDEO_TMP/text-selection-report.json" \
  --clean-text "$VIDEO_TMP/selected-transcript.txt"
```

主 Agent 审阅提取稿和删除组，再通读原稿，按逻辑段落记录摘要、保留逻辑和实际删除意图，区分“说坏后重来”和有意的并列、强调、回顾。每个文字删除片段须经原音确认，或明确回退为保留；发现范围覆盖全稿，不依赖长静音，也不只围绕已选中的候选检查。

```bash
python3 scripts/semantic_review.py prepare --srt "$VIDEO_TMP/source.raw.srt" \
  --report "$VIDEO_TMP/semantic-review.json"
```

脚本会提供句内重启与邻近相似表达线索，作为独立全文审稿之后的补充检查。其 45 秒邻近窗口不限制全文语义取舍。逐项处置，也补入通读和原音发现的其他重复；算法没报出不等于不存在。

另写 decisions JSON：

```json
{
  "source_srt_sha256": "从报告原样复制的哈希",
  "coverage": [{"cue_ids": [1, 2, 3], "summary": "该逻辑段实际内容摘要"}],
  "findings": [{
    "id": "与线索 ID 对应；手动发现可另命名",
    "classification": "retake",
    "reason": "前次未完成，后次完整重说，说明哪些内容重复",
    "repeat_candidate_id": "与删除候选对应",
    "text_selection_segment_ids": ["对应 selection.json 中的 drop 片段 ID"],
    "audio_evidence": "局部原音时间、前后原话以及边界依据"
  }],
  "whole_transcript_conclusion": "全稿编辑结论及剩余不确定点"
}
```

`coverage` 必须把所有 cue 恰好覆盖一次。疑点分类为 `retake`、`rhetorical`、`parallel_example`、`recap`、`not_duplicate` 或 `uncertain`。每个子 Agent 标为 drop 的片段须通过 `text_selection_segment_ids` 恰好处置一次：原音确认后为 `retake`，否则为 `uncertain` 且 `action: keep`；不能遗漏子 Agent 的整段重录判断。不确定项在交付说明；不得把有未决项的检查说成全片无重复。

Whisper 可能把真实复读省掉、把停顿铺在字幕里。对卡壳、异常长句、前后断裂、用户指出的漏剪位置做局部原音复核；必要时以较短窗口重新转写，保持上下文但避免把同一源片段拼入预览两次。音频不清或没有可靠边界就保留，明确说明证据限度。

## 3. 重说边界与操作停顿

为确认重说填写 [候选格式](roughcut-candidates.example.json)。`discard_start` 和 `retain_hint` 是语义提示，真实切点用原音验证：

- 左锚点覆盖完整桥接尾句；右锚点覆盖完整重说开头，包括“所以说”“第一个”等引导词。
- 为保留核心短语写 `min/max`；错误半句应消失时写 `max: 0`。两遍都能命中的宽泛短语不能单独作为证明。
- 句内重启允许短静音支持，按原音把 `min_supporting_silence` 调小；无安全静音则单独核验边界或保留，不因为小于 0.8 秒就不列候选。
- 前卷保护低声句首，但不能把前卷长度当作成片气口目标。两种模式拼接后都需合并测量气口。

有网页或软件实操时，先完成 [操作停顿审核](operation-pauses.md)，取得通过的 `operation-protected.json` 和 `pause-audit.json`。纯人物口播直接用声音候选 JSON。

```bash
python3 scripts/semantic_review.py validate --srt "$VIDEO_TMP/source.raw.srt" \
  --report "$VIDEO_TMP/semantic-review.json" --decisions "$VIDEO_TMP/semantic-decisions.json" \
  --text-selection "$VIDEO_TMP/selection.json" \
  --candidates "$VIDEO_TMP/repeat-candidates.json"
python3 scripts/roughcut_preflight.py prepare --media "$VIDEO_SOURCE" \
  --wav "$VIDEO_TMP/source.wav" --candidates "$VIDEO_TMP/repeat-candidates.json" \
  --auto-json "$VIDEO_TMP/auto.json" --combined-json "$VIDEO_TMP/combined.json" \
  --preview-wav "$VIDEO_TMP/cut-preview.wav" --report "$VIDEO_TMP/preflight.json"
```

实操素材把 `--auto-json` 换成已通过的保护版，并加 `--pause-audit-report`。用统一 Whisper 转写短预览，再运行 `roughcut_preflight.py validate --preview-srt ... --report ...`。左右锚点、短语计数全部通过才能继续。

无确认重说时使用空 candidates，跳过短预览并以声音/操作保护 JSON 为 combined；仍须完整语义复核，不能拿 `0/0` 证明全文没问题。

## 4. 节奏与整片预览

读取 [节奏配置](pacing-profiles.json)：短视频选 `short_video`，长视频或课程选 `long_video`。两种模式默认都不额外加长强调。只有用户明确要求时才使用 [强调计划](emphasis-pause-plan.example.json)，记录 `user_requested: true` 和具体范围，补回范围必须是实际静音。

```bash
python3 scripts/autocut.py "$VIDEO_SOURCE" "$VIDEO_TMP/combined.json" "$VIDEO_OUTPUT" \
  --pacing-profile short_video --audio-wav "$VIDEO_TMP/source.wav" \
  --semantic-review-report "$VIDEO_TMP/semantic-review.json" \
  --preflight-report "$VIDEO_TMP/preflight.json" \
  --plan-only --report "$VIDEO_TMP/cutlist.json"
python3 scripts/roughcut_preflight.py preview-cutlist --wav "$VIDEO_TMP/source.wav" \
  --cutlist "$VIDEO_TMP/cutlist.json" --preview-wav "$VIDEO_TMP/full-preview.wav"
python3 scripts/roughcut_preflight.py validate-silence --media "$VIDEO_TMP/full-preview.wav" \
  --cutlist "$VIDEO_TMP/cutlist.json"
```

长视频或课程把示例改为 `--pacing-profile long_video`，其余参数相同。实操必须传通过的画面审计报告。没有重复时省略 `--preflight-report`；有重复则不可省略。切换模式时从源片重算计划，不能复用另一模式的已裁剪区间来恢复被删气口。

转写整片预览，运行 `validate-final --srt ... --candidates ...`。同时重新通读整片实际转写，逐组检查文字取舍与原音复核决定是否落实、有无新增断句/残留/遗漏，保存实际复核记录。原稿候选通过不能代替这一步；不能把源稿的 selection.json 套到成片转写上。

每个超出目标的实际静音都必须有足够的操作、明确强调或局部低声证据。两种模式均允许最多一帧加 10 ms；检查从 cutlist 读取目标，不得回退到旧课程阈值。失败只修 cutlist 和音频预览。

## 5. 导出与同轴审核字幕

全部通过后，以同一参数运行 `autocut.py`，去掉 `--plan-only`，将 `--report` 指向正式 `.rough-cut.cutlist.json`。导出保持原分辨率，不默认升到 4K。只依据 ffmpeg 实际进度报告百分比。

从**导出视频**重新抽轨并转写；运行 `validate-final` 和 `validate-silence` 再核对实际结果。失败先定位与报告，不用第二次整片编码掩盖预检缺陷。

审核字幕执行 [校准规则](calibration.md)，但只修识别错误、明显边界和显示问题，不做文章式润色：

```bash
python3 scripts/srt_calibrate.py auto "$VIDEO_TMP/rendered.raw.srt" \
  --output "$VIDEO_TMP/rendered.draft.srt" --no-preserve-timing --max-chars 19
python3 scripts/srt_calibrate.py lint "$VIDEO_TMP/rendered.draft.srt" --max-chars 19
python3 scripts/review_srt_pipeline.py "$VIDEO_OUTPUT" "$VIDEO_TMP/rendered.raw.srt" \
  "$VIDEO_REVIEW_SRT" --draft-srt "$VIDEO_TMP/rendered.draft.srt" --skip-auto
```

先集中修完 draft，再重锚定。新错词写回词表；最终 lint 失败回到 draft 修正，不直接改最终 SRT 绕过验证。

## 6. 验收与收口

运行 `roughcut_inspect.py --source ... --output ... --cutlist ... --srt ... --sheet <临时图> --report <临时报告>`，同一解码进程完成全片音视频检查和抽帧；打开联系表，检查代表帧与全部重复切点。

对比宽高、帧率、像素格式、色彩属性，预计/实测时长和 SRT 末尾；音视频流首尾差不超过 0.05 秒。记录全稿复核、候选验证、实际气口统计、例外范围、测量来源及运行账本。正式目录只有入口定义的三份文件。报告验证范围，不声称自动检查证明了绝对零漏剪。
