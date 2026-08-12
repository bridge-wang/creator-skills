# Changelog

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

需要回退时优先使用 `git revert` 撤销 v1.3.0 提交，避免改写仓库历史。不要用 `git reset --hard`。

```bash
# 先查看两个版本间只属于该 skill 的变化
git diff bri-video-srt-v1.2.0..bri-video-srt-v1.3.0 -- \
  skills/bri-video-srt .claude-plugin/marketplace.json

# 在保留历史的前提下撤销 v1.3.0
git revert bri-video-srt-v1.3.0
```
