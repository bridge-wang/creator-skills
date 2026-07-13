---
name: bri-video-srt
description: 一句指令完成「视频/音频 → 成品 SRT 字幕」全流程：（口播原始素材先自动删气口，色彩无损）→ ffmpeg 抽音轨 → 本地 whisper-cli（large-v3-turbo）转录出原始 SRT → 自动调用 srt-auto-calibrator 校准错别字、断句与时间戳 → 按「中文文案排版指北」规范排版（中英文之间、中文与数字之间加空格等），产出可直接导入剪辑软件的最终字幕文件。当用户提供视频或音频文件路径并说「出字幕」「提取字幕」「生成字幕」「转录成 SRT」「做一份字幕文件」「给这个视频配字幕」「删气口」「剪气口」时使用；即使用户没提 Whisper、SRT 或任何工具名，只要意图是从影音文件得到一份可直接使用的字幕文件，就用本 skill，不要只做转录而跳过校准环节。
---

# bri-video-srt：视频一键出成品字幕

把一个视频（或音频）文件变成两份 SRT：原始转录版 + 校准成品版。成品版是交付物，可直接导入剪辑软件。口播原始素材还会先自动删气口，产出一份剪好的视频，SRT 与它时间轴对齐。

**转录完全在本地进行（whisper.cpp），不上传音视频到任何云端。**

## 流程

### 第 0 步：确认环境和输入

- 首次使用（或怀疑环境有变）先跑依赖自检，缺什么按提示装什么：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/check_setup.sh"
```

  必需：`ffmpeg`、`whisper-cli`（whisper.cpp）、`python3`、Whisper 模型 `ggml-large-v3-turbo.bin`（约 1.6 GB，自检脚本会给出下载命令；模型路径优先取 `$WHISPER_MODEL` 环境变量，默认 `~/Models/whisper/ggml-large-v3-turbo.bin`）。可选：`auto-editor`（只有删气口功能需要）。如果有必需项缺失，把自检输出里的安装命令告诉用户并协助安装，装齐前不要继续。
- 确认输入文件存在，用 `ffprobe` 看一眼时长，顺口告诉用户（长视频转录要等一会儿，用户心里有数）。
- 语言默认 `auto`。用户明确说了语言（如「中文」「英文视频」）就传对应的 `zh` / `en`。

### 第 1 步：删气口（仅对口播原始素材）

**什么时候做**：输入是手机直出的口播原始素材（典型特征：`.MOV` 后缀、HDR 竖屏、单人口播），或用户明确说「删气口」。**什么时候跳过**：纯音频、别人的成片、已经剪辑过的视频、未安装 auto-editor，或用户说「不要剪」。拿不准就问一句。

两步走，剪切点检测和实际剪切分开，为的是**色彩无损**（auto-editor 自己导出会把 10-bit HLG 降成 8-bit，色调会坏）：

```bash
# 1. auto-editor 只做检测，输出剪切点 JSON（auto-editor 不在 PATH 时尝试 ~/.local/bin/auto-editor）
auto-editor "<视频路径>" --edit "audio:threshold=4%" --margin 0.2sec \
  --export v1 -o "<临时目录>/<basename>.cutlist.json"

# 2. 按剪切点用 ffmpeg 重编码剪切，位深/色域/传递函数全部保持与源一致
#    （macOS 上自动用 hevc_videotoolbox 硬件编码；其他平台回退 libx265，较慢）
python3 "${CLAUDE_PLUGIN_ROOT}/skills/bri-video-srt/scripts/autocut.py" \
  "<视频路径>" "<临时目录>/<basename>.cutlist.json" "<视频所在目录>/<basename>.cut.mp4"
```

- 剪完用 `ffprobe` 对比源和输出的 `pix_fmt` / `color_space` / `color_primaries` / `color_transfer`，四项必须一致，否则停下来报告，不要交付变色的文件。
- 阈值 `threshold=4%`、留白 `margin 0.2sec` 是验证过的默认值；用户嫌剪太狠→调大 margin，嫌剪不干净→调高 threshold。
- 剪好的 `<basename>.cut.mp4` 是交付物之一（用户拿它进剪映等剪辑软件）。**后续所有步骤都以它为输入**，这样 SRT 时间轴和成片对齐。

### 第 2 步：抽音轨

如果第 1 步剪了气口，这一步及后续全部以 `<basename>.cut.mp4` 为输入（下文的 `<basename>` 即变为 `<basename>.cut`，SRT 文件名自然和剪好的视频同名，剪辑软件导入时能自动配对）。

抽成 16 kHz 单声道 PCM WAV，放到会话临时目录（scratchpad），不要污染视频所在目录：

```bash
ffmpeg -nostdin -loglevel error -y -i "<视频路径>" -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.wav"
```

### 第 3 步：Whisper 转录出原始 SRT

用本地 whisper-cli + large-v3-turbo 模型：

```bash
whisper-cli \
  -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 \
  --output-srt --output-file "<视频所在目录>/<basename>" \
  "<临时目录>/<basename>.wav"
```

- `-mc 0` 必须带：关闭上下文沿用，防止无人声段（静音、纯 BGM）触发复读循环，复读同一句并吞掉真台词。这是实际踩过坑后加的，不要省略。
- 原始 SRT 落在视频同目录，名为 `<basename>.srt`。
- 转录完删掉临时 WAV。
- 如果模型文件缺失或损坏：**停下来告知用户**，给出自检脚本里的下载命令，由用户决定。不要静默换用其他模型——换模型会让转录质量和用户预期不一致。

### 第 4 步：调用 srt-auto-calibrator 校准

用 Skill 工具调用本插件自带的 `srt-auto-calibrator`（安装后完整名称为 `video-srt:srt-auto-calibrator`），输入上一步的 `<basename>.srt`。按该 skill 自己的流程走完（脚本 auto pass → 语义复查 → lint），产出 `<basename>.calibrated.srt`。

不要自己手写校准逻辑替代它——错别字表（fixed_terms.tsv）、保护短语、时间戳重分配都由它统一维护。

### 第 5 步：中文排版（指北规范）

对校准后的文件跑捆绑的排版脚本，应用「中文文案排版指北」的机械规则：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/bri-video-srt/scripts/zh_typography.py" "<basename>.calibrated.srt"
```

脚本处理的规则（确定性，不要用模型手改代替）：

- 中文与英文单词之间加一个半角空格：`打开任意一个agent产品` → `打开任意一个 agent 产品`
- 中文与数字之间加一个半角空格：`2026年` → `2026 年`
- 「%」「°」跟随前面的数字、不与数字之间加空格，但后接中文时要空格：`超越99.99%学习AI的人` → `超越 99.99% 学习 AI 的人`
- 全角标点与其他字符之间不加空格
- 压缩连续空格、去行尾空格

脚本管不了的指北规范（专有名词大小写如 GitHub/iPhone、遇到完整英文句子时标点的全半角选择）在校准环节顺手把握，不确定就保持原样。

### 第 6 步：交付

告诉用户各文件的路径和分工：

- `<basename>.cut.mp4` — 删好气口、色彩与原片一致的视频（若执行了第 1 步），**进剪辑软件用这份**，同时报告删了几秒气口
- `<basename>.srt` — 原始转录，留作对照
- `<basename>.calibrated.srt` — **成品字幕，直接拖进剪辑软件用这份**

简要说明校准改了哪几类东西（错别字几处、合并/重切几处），不要全文粘贴字幕内容。

## 边界情况

| 情况 | 处理 |
|---|---|
| 输入是纯音频（mp3/m4a/wav） | 同样流程，跳过「视频」措辞即可 |
| 视频没有音轨 | ffmpeg 会报错，告知用户，不要继续 |
| 同名 `.srt` 已存在 | 直接覆盖没关系——它本来就是本流程的中间产物；但 `.calibrated.srt` 若已存在且用户改过，先问一句 |
| 用户只要原始转录、明确说不要润色 | 做完第 3 步就停 |
| 用户要烧录进视频 | 那是另一件事，交付 SRT 后问清需求再说 |
| 非 macOS 平台 | 转录、校准、排版全流程可用；删气口一步会用 libx265 软件编码（慢），且色彩保真仅在 macOS + videotoolbox 上验证过，剪完务必做第 1 步的 ffprobe 四项对比 |
