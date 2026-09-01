---
name: bri-video-srt
description: 面向中文口播和屏幕实操的两阶段「粗剪 → 人工精剪 → 最终 SRT」工作流。用户提供原始口播并说「粗剪一下」时，进入阶段 A：先联合画面与语义保护网页操作、输入、切换和模型等待，再按节奏删普通气口、识别高置信度重复口播，交付粗剪视频、同步审核 SRT 与剪辑记录；用户提供人工精剪、时间轴已锁定的 MP4/音频并说「生成字幕」时，直接进入阶段 B：使用本地 whisper-cli（large-v3-turbo）重转写、术语校准、语义断句、真实音频重锚定和中文排版，交付唯一可导入剪辑软件的最终 SRT。默认不烧录字幕、不渲染硬字幕。用户说「校准字幕」「修正字幕错别字」「调整字幕断句」并提供 SRT 时也使用。未剪口播只说“配字幕”时，先确认要粗剪还是已完成精剪。
---

# bri-video-srt：粗剪与最终字幕

这个 skill 不把“粗剪”和“最终字幕”混成一次不可逆操作。机器擅长先删明显气口、找候选重复；人负责判断节奏与误剪；只有人工精剪锁定时间轴后，才生成最终字幕。这样不必在会被剪映再次改变的时间轴上做两遍字幕润色。

**默认不烧录、不渲染、不重编码已精剪的成片。** 转录完全在本地进行（whisper.cpp），不上传音视频。

本文档中 `<SKILL_DIR>` 指本 skill 所在目录。

## 执行总览：按编号顺序走完，禁止跳步

先判断进入哪个阶段，再从该阶段第一个编号步骤开始，逐步执行到底。每一步末尾都有「门禁」；门禁未列出的条件全部满足之前，不能执行下一步的命令，也不能因为“看起来能提前做”而调换顺序。

| 阶段 | 步骤顺序 |
|---|---|
| A：机器粗剪 | A0 环境 → A1 侦测转写 → A2 气口检测 → A3 操作停顿+重复口播+4K 导出（唯一一次）→ A4 审核字幕 → A5 完整解码验收 → A6 收口交付 |
| B：最终字幕 | A0 环境 → B1 侦测转写 → B2 校准（含错题集回写）→ B3 音频重锚定 → B4 排版验证 → B5 收口交付 |

## 运行账本与异常闭环

正常执行也要记录总开始/结束时间和各外部命令的墙钟时间。任何门禁失败、重试、估时明显偏离，或用户要求复盘时，必须读取并执行 [异常处理与复利手册](references/exception-playbook.md)：先选择不会触发第二次 4K 导出的最低成本回退，再把可复用的方法写回手册、术语库或脚本测试。复盘的目标是解释每段时间对应了什么工作，并让同类状况下一次有现成处置路径，不做责任归因。

## 先判断所处阶段

| 输入状态 | 进入阶段 | 交付 |
|---|---|---|
| 手机直出、未剪的单人口播；用户说「粗剪一下」 | 阶段 A：机器粗剪 | 粗剪视频 + 同步审核 SRT + 剪辑时间记录 |
| 剪映等软件人工精剪后、时间轴已经锁定的 MP4；用户说「生成字幕」 | 阶段 B：最终字幕 | 唯一的最终 SRT |
| 已有 `.srt` | 校准子流程（等同阶段 B 的 B2/B4） | 校准后的 SRT；没有同时间轴音频时不做音频重锚定 |

未剪口播只说“配字幕”时，先问是否需要先走阶段 A。用户明确只要原始转写或明确视频已锁定时，跳过阶段 A。阶段 A 的视频进剪映后，必须等用户人工精剪并导出锁定时间轴版本，才进入阶段 B。

为避免目录膨胀，原始转写、WAV、草稿 SRT 与 operations JSON 全部只放在会话临时目录；除非用户明确要求保留，媒体目录只留下 A6/B5 收口门禁列出的交付物。

## A0/B0：确认环境和输入

**动作**：

1. 首次使用（或怀疑环境有变）运行：

```bash
bash "<SKILL_DIR>/scripts/check_setup.sh"
```

2. 用 `ffprobe` 检查输入时长。用户明确语言时传 `zh` / `en`；否则 `auto`。
3. 阶段 B 有 MP4 和 MP3 同时导出时，优先 MP4 内的音轨：它与最终剪映时间轴一致。MP3 的编码填充可能带来毫秒级首尾差异。

**门禁（全部满足才能进入 A1 / B1）**：

- [ ] `ffmpeg`、`whisper-cli`、`python3`、`$WHISPER_MODEL` 指向的 `ggml-large-v3-turbo.bin` 都存在。缺少或损坏模型时**停止并告知用户**；不要下载替代模型。
- [ ] 阶段 A 额外确认 `auto-editor` 已安装。

---

# 阶段 A：机器粗剪（原始口播）

### A1. 先转写供重复检测，不做最终字幕

**动作**：

```bash
ffmpeg -nostdin -loglevel error -y -i "<原始视频>" -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.wav"

whisper-cli \
  -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 \
  --output-srt --output-file "<临时目录>/<basename>.rough" \
  "<临时目录>/<basename>.wav"
```

**门禁**：

- [ ] `-mc 0` 必须带，防止长静音触发复读循环。
- [ ] 这份 SRT 只用于检测重复口播与人工核对，**不写入视频目录，也不做最终字幕润色**。

### A2. 检测长气口；此时不要导出 4K

切点检测和实际剪切分开，避免 auto-editor 破坏色彩，也避免在重复口播切点尚未验证时提前付出整片 4K 编码成本。

**动作**：

```bash
# <审核目录> 固定为 <视频目录>/review/<basename>，只容纳本次粗剪供人工审核的三份交付物（见 A6）；用户审核通过后可整目录清理
mkdir -p "<视频目录>/review/<basename>"

# auto-editor 只输出原始声音区间；不要给它加 margin
auto-editor "<原始视频>" --edit "audio:threshold=4%" --margin 0sec \
  --export v1 -o "<临时目录>/<basename>.auto-editor.json"
```

**门禁**：

- [ ] 纯口播或已经确认是普通口播停顿的相邻句长静音最多保留 `1.05` 秒；片头留 `0.17` 秒，片尾留 `0.37` 秒。实操演示中的操作型停顿不受此上限约束。全片结论、章节结论、核心判断、重大转折等关键语句另走 A3 的“强调停顿”审计，不能被全局气口上限抹平。用户觉得口播节奏太紧或太松时，只调整这些明确的留白参数，不调整 auto-editor 的 `margin`。
- [ ] A2 只生成原始声音 cutlist。实际 4K 导出必须等到 A3 的预检门禁全部通过之后，且整次粗剪只能执行一次。

### A3. 操作型停顿、重复口播与导出前门禁

如果原片包含网页、软件、大模型或课程实操演示，不能把“没有说话”直接等同于“无用气口”。

**动作 1：画面—语义联合审计（不少于 1.5 秒的候选静音）**

```bash
python3 "<SKILL_DIR>/scripts/pause_visual_audit.py" prepare \
  --media "<原始视频>" \
  --auto-json "<临时目录>/<basename>.auto-editor.json" \
  --srt "<临时目录>/<basename>.rough.srt" \
  --report "<临时目录>/<basename>.pause-audit.json" \
  --sheet-dir "<临时目录>/<basename>.pause-sheets"
```

- 必须打开全部联系表。每个候选独占一行，从左到右固定为“停顿前 / 停顿开始 / 停顿中间 / 停顿结束 / 停顿后”五帧，再结合报告里的前后口播判断。
- 保护操作型停顿采用“正向证据”门禁：必须明确看到输入内容或控件状态变化、页面实际切换、发送后的生成状态，或结果逐步出现/滚动。
- 分类只能使用：`operation`、`wait_generation`、`page_switch`、`typing_or_input`、`quiet_speech`、`ordinary_speech_pause`、`retake_context`、`uncertain`，每一项都要填写具体 `reason`。
- 页面结构变化、输入框文字增长、按钮/验证码状态变化、结果逐步生成或滚动，以及“打开、粘贴、发送、等待、来看结果”等与画面变化相互印证的操作叙事，才属于视觉上有意义的时间。只有口播中的操作词、页面可读但保持静止、讲者似乎在阅读/思考、光标轻微移动、选区取消，都不足以保护整段停顿；此时默认判为 `ordinary_speech_pause`。
- 保护分类必须在决策 JSON 中填写具体 `visual_evidence` 和 `protected_ranges_seconds`。后者只标记画面状态真正变化所需的最小时间窗；如果操作只占长停顿的一部分，只保护这一部分，其余静止区间仍按普通气口压缩。确认的操作窗总计不超过 `7.0` 秒时完整保留；超过时保留最早和最晚的必要窗口，总计最多 `7.0` 秒。
- auto-editor 的“低于 4%”不等于真正无声。报告会结合 `-35dB` 静音覆盖率和落在候选内部的 ASR 句段中点；出现 `possible_quiet_speech: true` 时，必须标为 `quiet_speech`、`retake_context` 或其他保护类型，不能标成普通静音。
- `ordinary_speech_pause`、无低声口播证据的 `uncertain` 和 `retake_context` 都交给默认规则压缩到最多 `1.05` 秒。受保护分类执行上述 `7.0` 秒上限，但 `possible_quiet_speech: true` 时禁止执行该上限并完整保留。`retake_context` 只说明这里需要继续做重复口播审计；只有后续左右语义锚点门禁确认重说后，才能删除前一次口播。
- `uncertain` 和 `retake_context` 不再自动保护空白：没有正向画面证据且没有低声口播时，即使无法百分之百排除思考或阅读，也按普通口播停顿压缩。只有存在低声口播证据时先完整保留；需要防止网页瞬移时，必须回到可指明的画面变化并标记最小保护窗口。

按 `references/pause-audit-decisions.example.json` 另存逐项判断，再应用保护区间：

```bash
python3 "<SKILL_DIR>/scripts/pause_visual_audit.py" apply \
  --auto-json "<临时目录>/<basename>.auto-editor.json" \
  --report "<临时目录>/<basename>.pause-audit.json" \
  --decisions "<临时目录>/<basename>.pause-decisions.json" \
  --output-json "<临时目录>/<basename>.operation-protected.json" \
  --max-protected-pause 7.0
```

**门禁 1**：

- [ ] `apply` 必须显示 `audit_pass: true`，并在报告中记录 `capped_protected_count`、每段实际保留范围、`partial_protection` 和 `capped_to_seconds`。
- [ ] 任一候选未分类或没有理由都会失败；保护分类缺少具体画面证据或最小保护窗口也会失败，并禁止 4K 导出。
- [ ] 纯人物口播、没有任何屏幕操作时可跳过本门禁；一旦出现实操演示就必须执行。

**动作 2：重复口播候选与音频锚点预检**

- 对照 A1 的原始 SRT、音频和长停顿，寻找“前一次没说好 → 停顿 → 完整重说”的紧邻重复。文本相似且音频语义明确重复才可剪；疑似重复、课程录屏或已剪成片一律保留。
- 不能直接把 Whisper 句段起止当作剪切边界。Whisper 时间戳只提供 `discard_start` / `retain_hint` 的语义提示；保留句开头必须由附近真实长静音末端支持。
- 静音检测只允许合并不超过 `0.06` 秒的检测器微小断裂。更长的间隙可能是“第一个”“所以说”等低声短句，不能把原来相距 `0.55` 秒的多个静音块跨句首合并。
- 为每个确认候选创建临时 JSON。字段格式见 `references/roughcut-candidates.example.json`：
  - `discard_start`：前一次废弃口播开始时间；
  - `retain_hint`：Whisper 推测的完整重说开始时间；
  - `left_anchors`：剪切前必须完整保留的桥接语或完整语义尾句，可提供多个识别变体；不能只写桥接语之前仍能命中的宽泛上下文；
  - `right_anchors`：完整重说的句首契约，必须包含“所以说”“第一个”等引导词或序号，不能只写后半句；
  - `phrase_counts`：对容易残留的重复核心短语声明最终最少/最多出现次数，通常为恰好一次；
  - `retain_preroll`：在检测到的静音尾点之前主动保留的前卷，默认 `0.46` 秒；关键语句可按强调停顿计划提高。
- 左右锚点应选对 ASR 小幅错字稳健、同时又能区分“废弃说法”和“保留重说”的核心短语，不要依赖容易误识别的单个专名，也不能把两边都会出现的通用短语作为唯一锚点。若找不到有区分度的锚点，增加上下文、使用两个连续语义锚点，或放弃自动删除该候选。默认预览每个切点左侧 10 秒、右侧 5 秒。
- 左右锚点通过不代表切点正确：预览还必须验证 `phrase_counts`，并扫描相邻长短语复读。类似“要提高 Agent 的稳定性，要提高 Agent 的稳定性”的连续重复必须失败，不能留给人工审核发现。
- 在 `snap_radius` 内，保留句切点默认吸附到**离 `retain_hint` 最近**的合格长静音末端；距离相同时才选更长的静音。音频锚点门禁仍是最终裁决，不能只相信吸附距离。

```bash
python3 "<SKILL_DIR>/scripts/roughcut_preflight.py" prepare \
  --media "<原始视频>" \
  --wav "<临时目录>/<basename>.wav" \
  --candidates "<临时目录>/<basename>.repeat-candidates.json" \
  --auto-json "<临时目录>/<basename>.operation-protected.json" \
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

**门禁 2**：

- [ ] `validate` 必须 N/N 通过。任何 `left_ok` / `right_ok` 失败都禁止 4K 导出；先增加上下文、补充含义等价且仍有区分度的 ASR 变体，或重新判断候选，再只重跑短预览门禁。禁止为了“通过”而把锚点弱化成废弃与保留两边都会出现的通用短语。
- [ ] 每个候选的完整左桥接语、完整右句首和短语计数都通过；整份预览没有未豁免的相邻长短语复读。
- [ ] 如果没有任何高置信度重复候选，跳过重复口播预览门禁：实操素材把已经通过画面审计的 `operation-protected.json` 作为 `combined.json`，纯人物口播才直接使用 auto-editor JSON；不要为了满足流程虚构候选。这种情况运行 `autocut.py` 时不传 `--preflight-report`，但实操素材仍必须传 `--pause-audit-report`。

**动作 3：识别关键语句并生成强调停顿计划**

- 通读整份 A1 转写，识别全片结论、章节结论、核心判断、重大转折和行动号召。只有其结构重要性可以用前后文明确说明时才列入；不要因句子听起来有力就把普通句全部拉长。
- 对每个关键语句测量当前完整音频预览中的**可听停顿**，默认目标约为当前的 `1.3` 倍（一般接受 `1.2–1.4` 倍），并受原片实际静音长度约束。记录关键句、理由、当前秒数、目标秒数和需要补回的源片静音范围。
- 按 `references/emphasis-pause-plan.example.json` 生成临时计划。补回范围必须经音频确认是静音，不能为了延长停顿重新带回废弃口播或低声句首。

**门禁 3：整片音频预览必须先于 4K 导出通过**

无论是首次粗剪还是用户授权的返修重渲染，都先按最终 `kept_ranges_seconds` 生成整片 PCM 音频预览并重新转写。返修时可复用已审核 cutlist，但必须先修正范围，不能直接重编码旧报告。

```bash
python3 "<SKILL_DIR>/scripts/autocut.py" \
  "<原始视频>" "<临时目录>/<basename>.combined.json" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  --max-pause 1.05 --head-pad 0.17 --tail-pad 0.37 \
  --preflight-report "<临时目录>/<basename>.preflight.json" \
  --pause-audit-report "<临时目录>/<basename>.pause-audit.json" \
  --emphasis-pause-plan "<临时目录>/<basename>.emphasis-pause-plan.json" \
  --plan-only --report "<临时目录>/<basename>.corrected-cutlist.json"

python3 "<SKILL_DIR>/scripts/roughcut_preflight.py" preview-cutlist \
  --wav "<临时目录>/<basename>.wav" \
  --cutlist "<临时目录>/<basename>.corrected-cutlist.json" \
  --preview-wav "<临时目录>/<basename>.full-cut-preview.wav"

whisper-cli -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 --output-srt \
  --output-file "<临时目录>/<basename>.full-cut-preview.raw" \
  "<临时目录>/<basename>.full-cut-preview.wav"

python3 "<SKILL_DIR>/scripts/roughcut_preflight.py" validate-final \
  --srt "<临时目录>/<basename>.full-cut-preview.raw.srt" \
  --candidates "<临时目录>/<basename>.repeat-candidates.json"
```

- [ ] 所有完整桥接语、完整重说句首与短语计数通过；没有未豁免的相邻长短语复读。
- [ ] 每个强调停顿的实测值达到计划目标附近，且没有带回废弃口播。失败时只调整 cutlist 和重跑音频预览，不启动 4K。
- [ ] 普通气口补白后，必须再次扣除已验证的重复范围；否则补白会把预检已经删除的半句重新带回，造成“预检切点”和“实际导出切点”不一致。

**动作 4：门禁通过后执行本次粗剪唯一一次 4K 导出**

```bash
python3 "<SKILL_DIR>/scripts/autocut.py" \
  "<原始视频>" "<临时目录>/<basename>.combined.json" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  --max-pause 1.05 --head-pad 0.17 --tail-pad 0.37 \
  --report "<审核目录>/<basename>.rough-cut.cutlist.json" \
  --preflight-report "<临时目录>/<basename>.preflight.json" \
  --pause-audit-report "<临时目录>/<basename>.pause-audit.json" \
  --emphasis-pause-plan "<临时目录>/<basename>.emphasis-pause-plan.json"
```

纯人物口播按前述规则跳过画面审计时，同时省略 `--pause-audit-report`；实操演示不得省略。

**门禁 4**：

- [ ] 不能只删 SRT 文字：每一段实际删除范围都要写入源视频时间戳和剪辑清单。把已验证的重复范围写入 `manual_repeat_ranges_seconds`。
- [ ] 实操演示的 cutlist 还必须写入 `protected_operation_ranges_seconds` 和 `pause_visual_audit_pass: true`，让人工复核能区分“主动保留的操作时间”和“普通气口”。
- [ ] `autocut.py` 用预计成片时长而不是原片时长计算进度；ffmpeg 收尾的 `out_time_ms=N/A` 必须安全忽略，并只在进程成功退出后显示 100%。因此进度解析异常不能再阻止已经成功的编码写出 cutlist。
- [ ] `autocut.py` 必须把每个切点统一量化到源视频帧边界，使用同一边界逐段成对裁切视频和音频，再用 `concat` 拼接。禁止用 `select` / `aselect` 独立筛流并分别重建时间轴；视频帧与音频帧取整粒度不同，切点越多口型漂移会越大。
- [ ] cutlist 必须记录 `emphasis_pause_ranges_seconds` 和 `semantic_pause_audit_pass: true`；没有识别到关键语句时也要在计划里明确记录空列表和理由，不能静默跳过语义审计。
- [ ] 记录抽轨、分析/转写、画面审计、重复预检、每一次审核字幕处理、4K 导出和成片复检的墙钟时间；重试不得合并隐藏。4K 导出估时优先使用同一机器、分辨率、帧率和编码器的最近实测“输出时长倍率”，没有可比基线就明确说估时未知。向用户报告真实百分比时应使用可观测的 ffmpeg 进度；拿不到精确进度就明确说是估算。

### A4. 生成与粗剪视频同步的审核 SRT

粗剪完成、所有确认的重复范围也已应用后，对**粗剪视频本身**重新转写，避免用原片字幕硬映射造成错位。原始转写和草稿放在临时目录；只输出一份审核字幕。

**动作**：

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

# 先生成草稿；在第一次整片音频重锚定之前集中完成术语、边界和长度检查
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" auto \
  "<临时目录>/<basename>.rough-cut.raw.srt" \
  --output "<临时目录>/<basename>.rough-cut.draft.srt" \
  --no-preserve-timing --max-chars 19

python3 "<SKILL_DIR>/scripts/srt_calibrate.py" lint \
  "<临时目录>/<basename>.rough-cut.draft.srt" --max-chars 19

# 复核并一次性修完草稿后，从草稿 lint → 重锚定 → 排版 → 最终 lint
python3 "<SKILL_DIR>/scripts/review_srt_pipeline.py" \
  "<审核目录>/<basename>.rough-cut.mp4" \
  "<临时目录>/<basename>.rough-cut.raw.srt" \
  "<审核目录>/<basename>.rough-cut.srt" \
  --draft-srt "<临时目录>/<basename>.rough-cut.draft.srt" \
  --skip-auto
```

`review_srt_pipeline.py` 内部复用的是 B2 校准同一套规则，包括 `calibration.md` 第 8 步的错题回收：这里发现的新识别错例同样要写回 `fixed_terms.tsv` / `protected_phrases.txt`，不要因为是审核字幕就当作可以不闭环。

**门禁**：

- [ ] 审核 SRT 的作用是让用户在剪映中更低成本地检查口播、重复与节奏；不做人工语义精修，避免把最终字幕工作重复一遍。
- [ ] 只有在粗剪视频完成后重新转写，审核 SRT 才能与它严格同轴。
- [ ] 第一次整片音频重锚定前，集中扫描 raw/draft 中的已知别名、新专名错识别、被拆开的受保护短语、不完整语义单元和短时高密度字幕；先完成文本/边界操作，回写 `fixed_terms.tsv` / `protected_phrases.txt`，并让草稿通过显示长度与时序 lint。`--skip-auto` 只跳过自动校准，仍会重新运行整片音频重锚定；因此最终 lint 失败后的每次重跑都必须单独计时。
- [ ] 若最终 lint 因重锚定后的短时高密度失败，回到 draft 合并或重新分配完整语义单元，先 lint，再用 `--skip-auto` 重跑；不要直接修改最终 SRT 绕过闭环，也不要重新渲染视频。
- [ ] `validate-final` 必须 N/N 通过。若预检通过而成片失败，停止交付并报告该实验性门禁的失效原因；不要静默启动第二次整片 4K 渲染。
- [ ] `validate-final` 同时必须没有未豁免的相邻长短语复读；“左右锚点仍存在”不能覆盖重复计数或桥接语/句首契约失败。

### A5. 单次完整解码与画面验收

审核 SRT 通过后，用同一个 ffmpeg 解码进程同时完成完整音视频解码、代表帧抽取和全部重复切点前后截图。

**动作**：

```bash
python3 "<SKILL_DIR>/scripts/roughcut_inspect.py" \
  --source "<原始视频>" \
  --output "<审核目录>/<basename>.rough-cut.mp4" \
  --cutlist "<审核目录>/<basename>.rough-cut.cutlist.json" \
  --srt "<审核目录>/<basename>.rough-cut.srt" \
  --sheet "<临时目录>/<basename>.inspection.jpg" \
  --report "<临时目录>/<basename>.inspection.json"
```

**门禁**：

- [ ] 脚本必须验证完整解码成功，并对比宽高、帧率、`pix_fmt`、`color_space`、`color_primaries`、`color_transfer`；任一项不一致即停止交付。
- [ ] 脚本必须核对 cutlist 预计时长、成片实际时长和 SRT 末尾。默认允许 AAC/容器产生最多 `0.5` 秒首尾差异。
- [ ] 脚本必须确认 cutlist 使用 `paired_segment_concat_v1`，并检查成片音视频流的起点差和终点差；任一绝对值默认不得超过 `0.05` 秒。旧版独立筛流产物即使能完整解码也必须拒绝，不能把静态截图或字幕同轴误当作人物口型同步。
- [ ] 必须打开联系表肉眼检查。它包含整片代表帧，以及每个高置信度重复切点前后各一帧；不要再另跑一次完整解码或另生成第二张抽查图。
- [ ] 联系表和检查 JSON 只放临时目录，不增加阶段 A 的正式交付物。

### A6. 收口交付并停止

**门禁（交付前必须逐项核对，表外文件一律不留）**：

| 允许存在的文件 | 说明 |
|---|---|
| `<审核目录>/<basename>.rough-cut.mp4` | 粗剪成片 |
| `<审核目录>/<basename>.rough-cut.srt` | 同步审核字幕 |
| `<审核目录>/<basename>.rough-cut.cutlist.json` | 剪辑记录 |

- [ ] 清点 `<审核目录>` 内的实际文件列表，逐一对照上表；表外的任何文件——不论格式，不论看起来多“顺手有用”（转写纯文本、额外截图、汇总文档等）——一律不生成或删除。这条规则不针对某一种具体格式，凡是没在表里出现的都不留。
- [ ] 原始转写、草稿、WAV、联系表、检查 JSON 全部只在临时目录，不出现在审核目录或视频目录。
- [ ] 说明删了哪些长气口、哪些重复口播。**不要在此时制作最终字幕，更不要烧录。**
- [ ] 同时给出简洁耗时账本：端到端总耗时、可测命令墙钟时间、人工/语义判断与工具调度差额、各主要阶段和每次重试。高耗时点须说明其工作量或证据依据；若发生异常，说明已采用的低成本回退和沉淀到何处。

用户把粗剪视频导入剪映进行人工精剪，检查误剪、节奏、画面与转场；完成后导出一个时间轴锁定的 MP4（或完全同时间轴的音频）并交给阶段 B。

---

# 阶段 B：最终字幕（人工精剪后）

阶段 B 只接受锁定后的媒体，不再删气口或删除重复。

### B1. 抽音轨并重新转写

**动作**：

```bash
ffmpeg -nostdin -loglevel error -y -i "<精剪 MP4 或同轴音频>" -vn -ar 16000 -ac 1 -c:a pcm_s16le "<临时目录>/<basename>.wav"

whisper-cli \
  -m "${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}" \
  -l auto -mc 0 \
  --output-srt --output-file "<临时目录>/<basename>.raw" \
  "<临时目录>/<basename>.wav"
```

**门禁**：

- [ ] 原始 `<basename>.raw.srt` 只留在临时目录。它的文本是基线，但 Whisper 输出的是句段级时间戳，不应直接作为最终字幕交付。

### B2. 术语、语义与断句校准（含错题集回写）

读取 `<SKILL_DIR>/references/calibration.md`，严格按其 Workflow 全部步骤执行，包括：

1. `srt_calibrate.py auto` 做术语与机械修正，输出临时的 `<basename>.draft.srt`。
2. 语义复查并用 operations 修复不完整短语、专名切断、孤立连词和超长字幕。默认不自动拆句或按文字比例挪时轴；语义操作必须显式、可复查。
3. 在连续语音中没有可验证停顿时，不要声称获得了逐词级时间；宁可保留保守边界，也不要虚构精准切点。
4. **错题回收（calibration.md Workflow 第 8 步，不可省略）**：本轮如果手动改过一个当时不在 `fixed_terms.tsv` / `protected_phrases.txt` 里的真实识别错误，收尾前必须把它写回对应文件，再重跑一次 `lint` 确认新条目已被覆盖。只在正文里改一次而不写回词表，视为 B2 未完成。

**门禁**：

- [ ] 是否有新出现、当前词表未覆盖的识别错例？有 → 必须先完成第 4 点回写才能进入 B3。
- [ ] `lint` 对本轮改动无警告。

### B3. 用真实音频重锚定句首

Whisper 常把下一句的起点放在上一句结束处，即使中间还有静音。语义校准后，必须运行：

**动作**：

```bash
python3 "<SKILL_DIR>/scripts/srt_audio_reanchor.py" \
  "<精剪 MP4 或同轴音频>" "<临时目录>/<basename>.draft.srt" \
  "<媒体目录>/<basename>.srt"
```

**门禁**：

- [ ] 脚本用 `ffmpeg silencedetect` 找真实静音，并以 `-vn` 禁用视频流，避免为音频检测额外解码整片 4K 画面：仅当字幕开头位于一段尚未结束的静音中，才推到下一次真实开口，并收紧前一句结尾。
- [ ] 默认阈值为 `-35dB`、最短静音 `0.12` 秒；遇到持续的底噪或背景音乐，先用短片段复核再调整 `--threshold`，不要全局盲调。
- [ ] 它不改文字、不伪造连续语音中的逐词边界。没有同时间轴媒体时跳过此步，并明确说明最终时间无法做音频重锚定。

### B4. 中文排版与验证

**动作**：

```bash
python3 "<SKILL_DIR>/scripts/zh_typography.py" "<媒体目录>/<basename>.srt"
python3 "<SKILL_DIR>/scripts/srt_calibrate.py" lint "<媒体目录>/<basename>.srt"
```

**门禁**：

- [ ] 排版脚本处理中英文、中文与数字之间的空格、百分号/角度符号和多余空格，必须无残留问题。
- [ ] `lint` 必须无警告；同时抽查长句、术语、被拆分过的句子和音频重锚定较大的片段。
- [ ] 如果重锚定把某条短句压成高文字密度，回到 draft，把相邻完整语义单元合并或重新分配；draft lint 通过后重新执行 B3/B4。不要直接修改最终 SRT，也不要跳过整片重锚定。

### B5. 收口交付并停止

**门禁（交付前必须逐项核对，表外文件一律不留）**：

| 允许存在的文件 | 说明 |
|---|---|
| `<媒体目录>/<basename>.srt` | 唯一的最终成品，直接导入剪映 |

- [ ] 清点 `<媒体目录>` 内与本次交付相关的文件列表，逐一对照上表；表外的任何文件——不论格式，不论看起来多“顺手有用”（转写纯文本、字幕的另一份副本、汇总文档等）——一律不生成或删除。这条规则不针对某一种具体格式，凡是没在表里出现的都不留。
- [ ] 原始转写、草稿、WAV 全部只在临时目录；完成交付后清理，不在项目目录留下多份中间文件。
- [ ] **没有用户对 SRT 的明确确认，绝不烧录、渲染或重编码硬字幕视频。** 硬字幕属于本 skill 之外的独立后续工作。

---

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
