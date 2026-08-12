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

### A2. 检测并剪切长气口

切点检测和实际剪切分开，避免 auto-editor 破坏色彩：

`<审核目录>` 固定为 `<视频目录>/review/<basename>`，只容纳本次粗剪供人工审核的三份交付物。

```bash
# 阶段 A 的三个审核交付物集中放在这里；审核通过后可整目录清理
mkdir -p "<视频目录>/review/<basename>"

# auto-editor 只输出原始声音区间；不要给它加 margin
auto-editor "<原始视频>" --edit "audio:threshold=4%" --margin 0sec \
  --export v1 -o "<临时目录>/<basename>.auto-editor.json"

# ffmpeg 实际导出粗剪视频；留白按对标节奏统一补回
python3 "<SKILL_DIR>/scripts/autocut.py" \
  "<原始视频>" "<临时目录>/<basename>.auto-editor.json" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  --max-pause 1.05 --head-pad 0.17 --tail-pad 0.37 \
  --report "<审核目录>/<basename>.rough-cut.cutlist.json"
```

- 正文相邻句的长静音最多保留 `1.05` 秒；片头留 `0.17` 秒，片尾留 `0.37` 秒。用户觉得节奏太紧或太松时，只调整这三个留白参数，不调整 auto-editor 的 `margin`。
- 用 `ffprobe` 对比源与粗剪视频的 `pix_fmt`、`color_space`、`color_primaries`、`color_transfer`；任一项不一致即停止交付。

### A3. 高置信度重复口播

- 对照 A1 的原始 SRT、音频和长停顿，寻找“前一次没说好 → 停顿 → 完整重说”的紧邻重复。文本相似且音频语义明确重复才可剪；疑似重复、课程录屏或已剪成片一律保留。
- 不能只删 SRT 文字：每一段实际从视频删除的范围都要记录为源视频时间戳，并同步写入粗剪的剪辑清单。后续字幕映射或复现剪辑依赖这份记录。
- 将确认重复范围和静音范围一起应用到粗剪视频；若证据不足，交付候选列表而不误剪。

### A4. 生成与粗剪视频同步的审核 SRT

粗剪完成、所有确认的重复范围也已应用后，对**粗剪视频本身**重新转写，避免用原片字幕硬映射造成错位。原始转写和草稿放在临时目录；只输出一份审核字幕：

```bash
ffmpeg -nostdin -loglevel error -y -i "<审核目录>/<basename>.rough-cut.mp4" \
  -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.rough-cut.wav"
whisper-cli -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 --output-srt --output-file "<临时目录>/<basename>.rough-cut.raw" \
  "<临时目录>/<basename>.rough-cut.wav"

# 轻量术语修正，再输出与粗剪视频同步的审核字幕
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" auto \
  "<临时目录>/<basename>.rough-cut.raw.srt" \
  --output "<临时目录>/<basename>.rough-cut.draft.srt"

# 用粗剪音轨重锚定，写出唯一的审核字幕
python3 "<SKILL_DIR>/scripts/srt_audio_reanchor.py" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  "<临时目录>/<basename>.rough-cut.draft.srt" \
  "<审核目录>/<basename>.rough-cut.srt"
python3 "<SKILL_DIR>/scripts/zh_typography.py" "<审核目录>/<basename>.rough-cut.srt"
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" lint "<审核目录>/<basename>.rough-cut.srt"
```

- 审核 SRT 的作用是让用户在剪映中更低成本地检查口播、重复与节奏；不做人工语义精修，避免把最终字幕工作重复一遍。
- 只有在粗剪视频完成后重新转写，审核 SRT 才能与它严格同轴。

### A5. 阶段 A 交付并停止

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
