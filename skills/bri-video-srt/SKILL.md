---
name: bri-video-srt
description: 面向中文口播的两阶段「粗剪 → 人工精剪 → 最终 SRT」工作流。用户提供原始口播并说「粗剪一下」时，进入阶段 A：按对标节奏删长气口、识别高置信度重复口播，交付粗剪视频、同步的审核 SRT 与剪辑时间记录；用户提供剪映等软件人工精剪、时间轴已锁定的 MP4/音频并说「生成字幕」时，直接进入阶段 B：使用本地 whisper-cli（large-v3-turbo）重转写、术语校准、语义断句、真实音频重锚定和中文排版，交付唯一可导入剪辑软件的最终 SRT。默认不烧录字幕、不渲染硬字幕。用户说「校准字幕」「修正字幕错别字」「调整字幕断句」并提供 SRT 时也使用。未剪口播只说“配字幕”时，先确认要粗剪还是已完成精剪。
---

# bri-video-srt：粗剪与最终字幕

这个 skill 不把“粗剪”和“最终字幕”混成一次不可逆操作。机器擅长先删明显气口、找候选重复；人负责判断节奏与误剪；只有人工精剪锁定时间轴后，才生成最终字幕。这样不必在会被剪映再次改变的时间轴上做两遍字幕润色。

**默认不烧录、不渲染、不重编码已精剪的成片。** 转录完全在本地进行（whisper.cpp），不上传音视频。

本文档中 `<SKILL_DIR>` 指本 skill 所在目录。

## 先判断所处阶段

| 输入状态 | 进入阶段 | 交付 |
|---|---|---|
| 手机直出、未剪的单人口播；用户说「粗剪一下」 | 阶段 A：机器粗剪 | 粗剪视频 + 同步审核 SRT + 剪辑时间记录 |
| 剪映等软件人工精剪后、时间轴已经锁定的 MP4；用户说「生成字幕」 | 阶段 B：最终字幕 | 唯一的最终 SRT |
| 已有 `.srt` | 校准子流程 | 校准后的 SRT；没有同时间轴音频时不做音频重锚定 |

未剪口播只说“配字幕”时，先问是否需要先走阶段 A。用户明确只要原始转写或明确视频已锁定时，跳过阶段 A。阶段 A 的视频进剪映后，必须等用户人工精剪并导出锁定时间轴版本，才进入阶段 B。

为避免目录膨胀，原始转写、WAV、草稿 SRT 与 operations JSON 全部只放在会话临时目录；除非用户明确要求保留，媒体目录只留下本表列出的交付物。

## 第 0 步：确认环境和输入

首次使用（或怀疑环境有变）先运行：

```bash
bash "<SKILL_DIR>/scripts/check_setup.sh"
```

- 必需：`ffmpeg`、`whisper-cli`、`python3`、`$WHISPER_MODEL` 指向的 `ggml-large-v3-turbo.bin`。缺少或损坏模型时停止并告知用户；不要下载替代模型。
- 阶段 A 还需要 `auto-editor`。
- 用 `ffprobe` 检查输入时长。用户明确语言时传 `zh` / `en`；否则 `auto`。
- 阶段 B 有 MP4 和 MP3 同时导出时，优先 MP4 内的音轨：它与最终剪映时间轴一致。MP3 的编码填充可能带来毫秒级首尾差异。

## 阶段 A：机器粗剪（原始口播）

### A1. 先转写供重复检测，不做最终字幕

从原始视频抽取 16 kHz 单声道 WAV，放入临时目录：

```bash
ffmpeg -nostdin -loglevel error -y -i "<原始视频>" -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.wav"
```

用统一的本机 Whisper 模型生成原始 SRT：

```bash
whisper-cli \
  -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 \
  --output-srt --output-file "<临时目录>/<basename>.rough" \
  "<临时目录>/<basename>.wav"
```

- `-mc 0` 必须带，防止长静音触发复读循环。
- 此 SRT 只用于检测重复口播与人工核对，**不写入视频目录，也不做最终字幕润色**。

### A2. 检测长气口；此时不要导出 4K

切点检测和实际剪切分开，避免 auto-editor 破坏色彩，也避免在重复口播切点尚未验证时提前付出整片 4K 编码成本：

`<审核目录>` 固定为 `<视频目录>/review/<basename>`，只容纳本次粗剪供人工审核的三份交付物。

```bash
# 阶段 A 的三个审核交付物集中放在这里；审核通过后可整目录清理
mkdir -p "<视频目录>/review/<basename>"

# auto-editor 只输出原始声音区间；不要给它加 margin
auto-editor "<原始视频>" --edit "audio:threshold=4%" --margin 0sec \
  --export v1 -o "<临时目录>/<basename>.auto-editor.json"
```

- 正文相邻句的长静音最多保留 `1.05` 秒；片头留 `0.17` 秒，片尾留 `0.37` 秒。用户觉得节奏太紧或太松时，只调整这三个留白参数，不调整 auto-editor 的 `margin`。
- A2 只生成原始声音 cutlist。实际 4K 导出移到 A3 的预检门禁之后，并且整次粗剪只能执行一次。

### A3. 高置信度重复口播与导出前门禁

- 对照 A1 的原始 SRT、音频和长停顿，寻找“前一次没说好 → 停顿 → 完整重说”的紧邻重复。文本相似且音频语义明确重复才可剪；疑似重复、课程录屏或已剪成片一律保留。
- 不能直接把 Whisper 句段起止当作剪切边界。Whisper 时间戳只提供 `discard_start` / `retain_hint` 的语义提示；保留句开头必须由附近真实长静音末端支持。
- 为每个确认候选创建临时 JSON。字段格式见 `references/roughcut-candidates.example.json`：
  - `discard_start`：前一次废弃口播开始时间；
  - `retain_hint`：Whisper 推测的完整重说开始时间；
  - `left_anchors`：剪切前一条必须完整保留的语义短语，可提供多个识别变体；
  - `right_anchors`：完整重说开头必须出现的语义短语，可提供多个识别变体。
- 左右锚点应选对 ASR 小幅错字稳健的核心短语，不要依赖容易误识别的单个专名。默认预览每个切点左侧 10 秒、右侧 5 秒。
- 在 `snap_radius` 内，保留句切点默认吸附到**离 `retain_hint` 最近**的合格长静音末端；距离相同时才选更长的静音。这样不会因更早的停顿更长而错误吸附。音频锚点门禁仍是最终裁决，不能只相信吸附距离。

先生成合并 cutlist 和一次性音频预览：

```bash
python3 "<SKILL_DIR>/scripts/roughcut_preflight.py" prepare \
  --media "<原始视频>" \
  --wav "<临时目录>/<basename>.wav" \
  --candidates "<临时目录>/<basename>.repeat-candidates.json" \
  --auto-json "<临时目录>/<basename>.auto-editor.json" \
  --combined-json "<临时目录>/<basename>.combined.json" \
  --preview-wav "<临时目录>/<basename>.cut-preview.wav" \
  --report "<临时目录>/<basename>.preflight.json"
whisper-cli \
  -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 --output-srt \
  --output-file "<临时目录>/<basename>.cut-preview.raw" \
  "<临时目录>/<basename>.cut-preview.wav"
python3 "<SKILL_DIR>/scripts/roughcut_preflight.py" validate \
  --preview-srt "<临时目录>/<basename>.cut-preview.raw.srt" \
  --report "<临时目录>/<basename>.preflight.json"
```

- `validate` 必须 N/N 通过。任何 `left_ok` / `right_ok` 失败都禁止 4K 导出；先增加上下文、修正 ASR 锚点变体或重新判断候选，再重跑几秒钟的音频门禁。
- 如果没有任何高置信度重复候选，跳过预览门禁，直接把 auto-editor JSON 作为 `combined.json`；不要为了满足流程虚构候选。这种情况运行 `autocut.py` 时不传 `--preflight-report`。
- 门禁通过后才执行本次粗剪唯一一次 4K 导出：

```bash
python3 "<SKILL_DIR>/scripts/autocut.py" \
  "<原始视频>" "<临时目录>/<basename>.combined.json" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  --max-pause 1.05 --head-pad 0.17 --tail-pad 0.37 \
  --report "<审核目录>/<basename>.rough-cut.cutlist.json" \
  --preflight-report "<临时目录>/<basename>.preflight.json"
```

- 不能只删 SRT 文字：每一段实际删除范围都要写入源视频时间戳和剪辑清单。把已验证的重复范围写入 `manual_repeat_ranges_seconds`。
- `autocut.py` 用预计成片时长而不是原片时长计算进度；ffmpeg 收尾的 `out_time_ms=N/A` 必须安全忽略，并只在进程成功退出后显示 100%。因此进度解析异常不能再阻止已经成功的编码写出 cutlist。
- `autocut.py` 必须把每个切点统一量化到源视频帧边界，使用同一边界逐段成对裁切视频和音频，再用 `concat` 拼接。禁止用 `select` / `aselect` 独立筛流并分别重建时间轴；视频帧与音频帧取整粒度不同，切点越多口型漂移会越大。
- 记录抽轨、分析/转写、预检、4K 导出和成片复检的墙钟时间。向用户报告真实百分比时应使用可观测的 ffmpeg 进度；拿不到精确进度就明确说是估算。

### A4. 生成与粗剪视频同步的审核 SRT

粗剪完成、所有确认的重复范围也已应用后，对**粗剪视频本身**重新转写，避免用原片字幕硬映射造成错位。原始转写和草稿放在临时目录；只输出一份审核字幕：

```bash
ffmpeg -nostdin -loglevel error -y -i "<审核目录>/<basename>.rough-cut.mp4" \
  -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.rough-cut.wav"
whisper-cli -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 --output-srt --output-file "<临时目录>/<basename>.rough-cut.raw" \
  "<临时目录>/<basename>.rough-cut.wav"

# 成片转写后，先复核所有重复切点的左右锚点；失败不应自动再渲染
python3 "<SKILL_DIR>/scripts/roughcut_preflight.py" validate-final \
  --srt "<临时目录>/<basename>.rough-cut.raw.srt" \
  --candidates "<临时目录>/<basename>.repeat-candidates.json"

# 一次性执行：自动校准/拆分 → 草稿 lint → 重锚定 → 排版 → 最终 lint
python3 "<SKILL_DIR>/scripts/review_srt_pipeline.py" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  "<临时目录>/<basename>.rough-cut.raw.srt" \
  "<审核目录>/<basename>.rough-cut.srt" \
  --draft-srt "<临时目录>/<basename>.rough-cut.draft.srt"
```

- 审核 SRT 的作用是让用户在剪映中更低成本地检查口播、重复与节奏；不做人工语义精修，避免把最终字幕工作重复一遍。
- 只有在粗剪视频完成后重新转写，审核 SRT 才能与它严格同轴。
- `review_srt_pipeline.py` 必须先让草稿通过显示长度与时序 lint，才运行整片音频重锚定。草稿失败时先修复；人工修好草稿后用 `--skip-auto` 继续，避免重复跑 `silencedetect`。
- `validate-final` 必须 N/N 通过。若预检通过而成片失败，停止交付并报告该实验性门禁的失效原因；不要静默启动第二次整片 4K 渲染。

### A5. 单次完整解码与画面验收

审核 SRT 通过后，用同一个 ffmpeg 解码进程同时完成完整音视频解码、代表帧抽取和全部重复切点前后截图：

```bash
python3 "<SKILL_DIR>/scripts/roughcut_inspect.py" \
  --source "<原始视频>" \
  --output "<审核目录>/<basename>.rough-cut.mp4" \
  --cutlist "<审核目录>/<basename>.rough-cut.cutlist.json" \
  --srt "<审核目录>/<basename>.rough-cut.srt" \
  --sheet "<临时目录>/<basename>.inspection.jpg" \
  --report "<临时目录>/<basename>.inspection.json"
```

- 脚本必须验证完整解码成功，并对比宽高、帧率、`pix_fmt`、`color_space`、`color_primaries`、`color_transfer`；任一项不一致即停止交付。
- 脚本必须核对 cutlist 预计时长、成片实际时长和 SRT 末尾。默认允许 AAC/容器产生最多 `0.5` 秒首尾差异。
- 脚本必须确认 cutlist 使用 `paired_segment_concat_v1`，并检查成片音视频流的起点差和终点差；任一绝对值默认不得超过 `0.05` 秒。旧版独立筛流产物即使能完整解码也必须拒绝，不能把静态截图或字幕同轴误当作人物口型同步。
- 必须打开联系表肉眼检查。它包含整片代表帧，以及每个高置信度重复切点前后各一帧；不要再另跑一次完整解码或另生成第二张抽查图。
- 联系表和检查 JSON 只放临时目录，不增加阶段 A 的正式交付物。

### A6. 阶段 A 交付并停止

交付且仅交付审核目录中的 `<basename>.rough-cut.mp4`、`<basename>.rough-cut.srt` 与 `<basename>.rough-cut.cutlist.json`，说明删了哪些长气口、哪些重复口播。**不要在此时制作最终字幕，更不要烧录。**

用户把粗剪视频导入剪映进行人工精剪，检查误剪、节奏、画面与转场；完成后导出一个时间轴锁定的 MP4（或完全同时间轴的音频）并交给阶段 B。

## 阶段 B：最终字幕（人工精剪后）

### B1. 抽音轨并重新转写

阶段 B 只接受锁定后的媒体，不再删气口或删除重复。抽取 WAV：

```bash
ffmpeg -nostdin -loglevel error -y -i "<精剪 MP4 或同轴音频>" -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.wav"
```

生成最终时间轴的原始 SRT 到临时目录：

```bash
whisper-cli \
  -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 \
  --output-srt --output-file "<临时目录>/<basename>.raw" \
  "<临时目录>/<basename>.wav"
```

原始 `<basename>.raw.srt` 只留在临时目录。它的文本是基线，但 Whisper 输出的是句段级时间戳，不应直接作为最终字幕交付。

### B2. 术语、语义与断句校准

读取 `<SKILL_DIR>/references/calibration.md`，严格执行：

1. `srt_calibrate.py auto` 做术语与机械修正，输出临时的 `<basename>.draft.srt`。
2. 语义复查并用 operations 修复不完整短语、专名切断、孤立连词和超长字幕。默认不自动拆句或按文字比例挪时轴；语义操作必须显式、可复查。
3. 在连续语音中没有可验证停顿时，不要声称获得了逐词级时间；宁可保留保守边界，也不要虚构精准切点。

### B3. 用真实音频重锚定句首

Whisper 常把下一句的起点放在上一句结束处，即使中间还有静音。语义校准后，必须运行：

```bash
python3 "<SKILL_DIR>/scripts/srt_audio_reanchor.py" \
  "<精剪 MP4 或同轴音频>" "<临时目录>/<basename>.draft.srt" \
  "<媒体目录>/<basename>.srt"
```

- 脚本用 `ffmpeg silencedetect` 找真实静音：仅当字幕开头位于一段尚未结束的静音中，才推到下一次真实开口，并收紧前一句结尾。
- 默认阈值为 `-35dB`、最短静音 `0.12` 秒；遇到持续的底噪或背景音乐，先用短片段复核再调整 `--threshold`，不要全局盲调。
- 它不改文字、不伪造连续语音中的逐词边界。没有同时间轴媒体时跳过此步，并明确说明最终时间无法做音频重锚定。

### B4. 中文排版与验证

```bash
python3 "<SKILL_DIR>/scripts/zh_typography.py" "<媒体目录>/<basename>.srt"
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" lint "<媒体目录>/<basename>.srt"
```

- 排版脚本处理中英文、中文与数字之间的空格、百分号/角度符号和多余空格。
- `lint` 必须无警告；同时抽查长句、术语、被拆分过的句子和音频重锚定较大的片段。

## 交付边界

默认到这里停止。交付：

- 阶段 B：`<basename>.srt`，唯一的最终成品，直接导入剪映。
- 阶段 A：审核目录中的 `<basename>.rough-cut.mp4`、`<basename>.rough-cut.srt`、`<basename>.rough-cut.cutlist.json`。
- 原始转写、草稿和 WAV 都在临时目录；完成交付后清理，不在项目目录留下多份中间文件。

**没有用户对 SRT 的明确确认，绝不烧录、渲染或重编码硬字幕视频。** 硬字幕属于本 skill 之外的独立后续工作。

## 边界情况

| 情况 | 处理 |
|---|---|
| 输入是纯音频 | 可直接走阶段 B；它必须与最终剪映时间轴一致。 |
| 输入是现成 SRT | 只做 B2/B4；若没有对应媒体，跳过 B3。 |
| 视频没有音轨 | 停止并告知用户。 |
| 最终 `<basename>.srt` 已存在且用户可能编辑过 | 先询问，避免覆盖人工修改。 |
| 用户明确只要原始转写 | 完成 B1 即停止。 |
| 用户要求烧录 | 先交付并等待确认；本 skill 不执行烧录或渲染。 |
| 非 macOS | 转写、校准、重锚定可用；阶段 A 的色彩保真仅在 macOS + VideoToolbox 验证过。 |
