# 实操画面与停顿审核

只在画面包含软件或网页操作时读取。纯人物口播跳过此文档。所有时间使用源容器时间；普通停顿目标读取 `pacing-profiles.json` 中所选短视频或长视频配置。

**动作 1：画面—语义联合审计（不少于 0.8 秒的候选静音）**

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
- `page_switch` 只保护硬切/转场实际发生的最小窗口，默认总计不得超过 `1.0` 秒；翻页前后的静止等待不能跟着整段豁免。确有多步操作需要更长时间时，按 `operation` 等真实类别逐段记录状态变化，不能把它伪装成一次翻页。
- auto-editor 的“低于 4%”不等于真正无声。报告会结合 `-35dB` 静音覆盖率和落在候选内部的 ASR 句段中点；出现 `possible_quiet_speech: true` 时，必须标为 `quiet_speech`、`retake_context` 或其他保护类型，不能标成普通静音。但 ASR 句段重叠/中点本身不是低声证据：Whisper 可能把停顿末端才开口的一句话铺满整段静音。`confirmed_silence_ratio >= 0.9` 时必须局部听审；确有语音才记录 `audible_speech_evidence` 和精确的 `verified_audio_ranges_seconds`，否则按普通气口处理。
- `pause_visual_audit.py prepare` 生成的 `possible_quiet_speech`、`confirmed_silence_ratio`、`contains_asr_midpoint` 等检测字段带证据摘要，禁止手工改写。`apply` 发现字段被改写必须失败；模型/人工只能在 decisions 文件填写分类、理由、画面证据、保护窗口与局部听审证据。
- 高静音率候选与 ASR 重叠时必须填写 `asr_overlap_resolution`（`timestamp_spill` / `retake` / `verified_quiet_speech` / `visual_operation`）和具体 `resolution_evidence`。凡标为 `retake_context` 的候选还必须填写 `repeat_candidate_id` 与 `retake_evidence`；后续重复预检会确认该 ID 真正进入删除候选，禁止“看出来是重说但只压停顿、没删错句”。
- `ordinary_speech_pause`、无低声口播证据的 `uncertain` 和 `retake_context` 都交给所选场景规则压缩到所选节奏配置的目标秒数。受保护分类执行上述 `7.0` 秒上限，但 `possible_quiet_speech: true` 时禁止执行该上限并完整保留。`retake_context` 只说明这里需要继续做重复口播审计；只有后续左右语义锚点门禁确认重说后，才能删除前一次口播。
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
