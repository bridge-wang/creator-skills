# Changelog

## 1.4.0 — 2026-08-14

- 4K 导出进度改用预计成片时长计算，并安全忽略 ffmpeg 收尾的 `N/A`；只有编码成功退出后才显示 100%，cutlist 不再因进度解析失败而丢失。
- 新增 `review_srt_pipeline.py`，把审核字幕固定为“草稿 lint 通过后才做整片音频重锚定”，避免超长字幕导致重复扫描成片。
- 新增 `roughcut_inspect.py`，一次完整解码同时完成媒体属性/时长/SRT 校验、代表帧和所有重复切点前后截图。
- 重复口播切点从“范围内最长静音优先”改为“离 `retain_hint` 最近的合格静音优先；距离相同再选更长静音”。
- 增加上述行为的回归测试，并用第三课第一部分现有交付物进行非重渲染兼容验证。

## 1.3.0 — 2026-08-12

- 阶段 A 在 4K 导出前增加音频切点预检门禁。
- 重复口播保留句边界吸附到真实长静音末端，不再直接采用 Whisper 句段时间戳。
- 将所有切点拼接成一次性音频预览，用左右语义锚点验证是否削字或保留错误版本。
- 预检 N/N 通过后才允许一次 4K 导出；失败时不得先渲染再返工。
- 4K 成片重新转写后再次验证全部左右锚点。
- 增加粗剪候选 JSON 示例和可复用 `roughcut_preflight.py`。

## 1.2.0 — 2026-08-12

- 固化升级前的完整两阶段工作流作为回退基线。
- 包含最终字幕音频重锚定、语义校准与用户维护的专有名词规则。

## 回退

本 skill 位于 `creator-skills` Git 仓库中：

- v1.2.0 基线标签：`bri-video-srt-v1.2.0`
- v1.3.0 标签：`bri-video-srt-v1.3.0`
- v1.4.0 标签：`bri-video-srt-v1.4.0`

需要回退时优先使用 `git revert` 撤销对应版本提交，避免改写仓库历史。不要用 `git reset --hard`。

```bash
# 先查看两个版本间只属于该 skill 的变化
git diff bri-video-srt-v1.3.0..bri-video-srt-v1.4.0 -- \
  skills/bri-video-srt .claude-plugin/marketplace.json

# 在保留历史的前提下撤销 v1.4.0
git revert bri-video-srt-v1.4.0
```
