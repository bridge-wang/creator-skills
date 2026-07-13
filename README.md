# creator-tools 创作者工具箱

把自媒体创作里重复、繁琐、容易出错的环节，变成一句自然语言就能完成的事。

creator-tools 由自媒体创作者 Bridge 创建，收录其在真实创作流程（选题、拍摄、剪辑、字幕）中沉淀并反复打磨过的 AI Skills。当前发布 2 个 Skill，持续增加中。

**最新版本：v1.0.0**

## 你可以用它做什么

| 你交付的内容 | creator-tools 会帮你做什么 |
|---|---|
| 一条手机直出的口播视频 | 删气口（色彩无损）→ 本地转录 → 校准 → 排版，直接产出可导入剪映的成品 SRT 字幕 |
| 一个已经剪好的视频或音频 | 本地 Whisper 转录 + 全自动校准，得到成品字幕 |
| 一份现成的 SRT 字幕 | 修错别字和专名、按语义合并/重切字幕、微调时间戳 |

音视频**全程在本地处理**（whisper.cpp），不上传任何云端。

## 安装

### 方式一：自然语言安装（推荐）

把下面这句话直接发给你的 AI Agent（Claude Code、Codex 等）：

```text
帮我下载安装这个 skill：https://github.com/bridge-wang/creator-skills
```

> **给 AI Agent 的安装指引**——如果你是 Agent，正在替用户安装本仓库，按以下步骤执行：
>
> 1. 克隆仓库到临时目录：`git clone --depth 1 https://github.com/bridge-wang/creator-skills.git`
> 2. 把 `skills/` 下的**每个**子目录完整复制到用户的 skill 目录（两个 skill 互相配合，必须都装）：Claude Code 为 `~/.claude/skills/`，Codex 为 `~/.codex/skills/`，通用 Agents 为 `~/.agents/skills/`。目标位置已有同名目录时，先问用户是否覆盖。
> 3. 运行依赖自检并按输出提示协助用户装齐必需依赖：`bash <skill 目录>/bri-video-srt/scripts/check_setup.sh`
> 4. 告诉用户：新开一次对话后，直接说「给这个视频出字幕 + 文件路径」即可使用。

### 方式二：skills CLI

```bash
npx -y skills add bridge-wang/creator-skills -g --all
```

### 方式三：Claude Code 插件市场

```bash
claude plugin marketplace add bridge-wang/creator-skills
claude plugin install bri-video-srt@creator-tools
```

## 依赖

首次使用时 Agent 会自动运行自检脚本，缺什么会给出对应安装命令，也可手动检查：

```bash
bash skills/bri-video-srt/scripts/check_setup.sh
```

| 依赖 | 安装 | 说明 |
|---|---|---|
| ffmpeg | `brew install ffmpeg` | 必需 |
| whisper.cpp | `brew install whisper-cpp` | 必需，提供 `whisper-cli` |
| python3 | 系统一般自带 | 必需 |
| Whisper 模型（large-v3-turbo） | 自检脚本给出下载命令（约 1.6 GB，Hugging Face） | 必需，默认放 `~/Models/whisper/`，可用 `$WHISPER_MODEL` 环境变量改路径 |
| auto-editor | `pip install auto-editor` | 可选，只有「删气口」需要 |

主要面向 macOS（Apple Silicon 硬件编码）；Linux / Windows 上转录和校准流程同样可用，删气口一步回退软件编码（较慢）。

## Skill 目录

| Skill | 做什么 | 直接调用 |
|---|---|---|
| `bri-video-srt` | 视频/音频一键出成品字幕全流程：删气口 → 转录 → 校准 → 中文排版 | `/bri-video-srt <文件路径>`，或直接说「给这个视频出字幕」 |
| `bri-srt-calibrator` | 单独校准现成的 SRT：错别字、专名、语义断句、时间戳 | 提供 SRT 文件并说「校准字幕」 |

使用示例（安装后直接用自然语言）：

```text
给这个视频出字幕 /Users/me/Videos/IMG_2035.MOV
这条口播帮我删气口再出字幕 /Users/me/Videos/IMG_2036.MOV
校准一下这份字幕 /Users/me/Videos/final.srt
```

## 自定义

- **专名词表**：转录里反复出现你所在领域的专名误识别（人名、产品名）时，把它加进 `skills/bri-srt-calibrator/references/fixed_terms.tsv`（格式见文件头注释），下次校准自动生效。
- **保护短语**：不想被断句切开的短语，加到同目录 `protected_phrases.txt`，一行一条。
- **删气口松紧**：嫌剪太狠调大 margin，嫌剪不干净调高 threshold（对 Agent 说即可，如「气口留白放宽一点」）。

## 更新

已安装后，直接对 Agent 说：

```text
更新 creator-tools：https://github.com/bridge-wang/creator-skills
```

Agent 会重新拉取仓库并覆盖 `skills/` 下的同名目录；你在 `fixed_terms.tsv` / `protected_phrases.txt` 里的自定义内容注意先备份。插件市场用户执行：

```bash
claude plugin marketplace update creator-tools
claude plugin update bri-video-srt@creator-tools
```

## 许可证

[MIT](LICENSE)。随意使用、修改、分发。
