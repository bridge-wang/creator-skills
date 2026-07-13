# creator-tools 创作者工具

自媒体创作过程中沉淀下来的 Claude Code skills：选题、视频剪辑、字幕处理。每个工具都在真实创作流程中打磨过。

Claude Code skills for content creators — subtitle generation, video editing, and more, battle-tested in a real creator workflow. Docs are in Chinese; the tools work with Chinese (and mixed Chinese/English) spoken content.

## 安装

在 Claude Code 里执行两条命令（把 `<你的用户名>` 换成本仓库实际所属的 GitHub 用户名）：

```
/plugin marketplace add <你的用户名>/creator-skills
/plugin install video-srt@creator-tools
```

装完重启 Claude Code（或执行 `/reload-plugins`）即可生效。

## 插件列表

### video-srt — 视频一键出成品字幕

一句话（如「给这个视频出字幕」+ 文件路径）完成全流程：

1. **删气口**（可选，口播原始素材）— auto-editor 检测停顿 + ffmpeg 色彩无损剪切，10-bit HLG 不降级
2. **本地转录** — ffmpeg 抽音轨 → whisper.cpp（large-v3-turbo 模型），音视频不上传任何云端
3. **自动校准** — 修错别字/专名、按语义合并与重切字幕、按字数微调时间戳（内置 `srt-auto-calibrator` skill，也可单独用它校准现成的 SRT）
4. **中文排版** — 按「中文文案排版指北」处理中英文/数字间距

产出 `<视频名>.calibrated.srt`，直接拖进剪映等剪辑软件。

**依赖**（首次使用时 Claude 会自动运行自检脚本并给出安装命令）：

| 依赖 | 安装 | 说明 |
|---|---|---|
| ffmpeg | `brew install ffmpeg` | 必需 |
| whisper.cpp | `brew install whisper-cpp` | 必需，提供 `whisper-cli` |
| python3 | 系统一般自带 | 必需 |
| Whisper 模型 | 自检脚本给出下载命令（约 1.6 GB，来自 Hugging Face） | 必需，默认放 `~/Models/whisper/`，可用 `$WHISPER_MODEL` 环境变量指定 |
| auto-editor | `pip install auto-editor` | 可选，只有「删气口」需要 |

主要面向 macOS（Apple Silicon 硬件编码）；Linux/Windows 上转录和校准流程同样可用，删气口一步回退软件编码。

**自定义**：转录中经常认错的专有名词，加到 `skills/srt-auto-calibrator/references/fixed_terms.tsv`（格式见文件头注释）；不想被断开的短语加到同目录 `protected_phrases.txt`。

## 许可

MIT
