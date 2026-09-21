# creator-tools 创作者工具箱

把自媒体创作里重复、繁琐、容易出错的环节，变成一句自然语言就能完成的事。

creator-tools 由自媒体创作者 Bridge 创建，收录其在真实创作流程（选题、拍摄、剪辑、字幕、封面）中沉淀并反复打磨过的 AI Skills。每个 Skill 独立可用、互不依赖。当前发布 2 个 Skill，持续增加中。

**最新版本：v2.3.1**

## 你可以用它做什么

| 你交付的内容 | creator-tools 会帮你做什么 |
|---|---|
| 一条手机直出的口播或屏幕实操视频 | 画面—语义联合审计 → 粗剪普通气口与确认的重复口播 → 交付粗剪视频、审核 SRT 和 cutlist |
| 一个已经人工精剪、时间轴锁定的视频或音频 | 本地 Whisper 重转录 → 术语/语义校准 → 音频重锚定 → 中文排版，得到最终 SRT |
| 一份现成的 SRT 字幕 | 修错别字和专名、按语义合并/重切字幕、微调时间戳 |
| 一条无字幕视频和 1–4 行封面文字 | 筛选三个真实画面并制作封面候选；选定后生成 3:4、9:16、16:9、4:3 四比例成品 |

音视频的转录、抽帧和封面渲染均在本地运行。Agent 查看候选图时，图片如何传给模型取决于所用客户端及模型配置。

## 安装

### 方式一：自然语言安装（推荐）

把下面这句话直接发给你的 AI Agent（Claude Code、Codex 等）：

```text
帮我下载安装这个 skill：https://github.com/bridge-wang/creator-skills
```

> **给 AI Agent 的安装指引**——如果你是 Agent，正在替用户安装本仓库，按以下步骤执行：
>
> 1. 克隆仓库到临时目录：`git clone --depth 1 https://github.com/bridge-wang/creator-skills.git`
> 2. 把 `skills/` 下的每个子目录完整复制到用户的 skill 目录：Claude Code 为 `~/.claude/skills/`，Codex 为 `~/.codex/skills/`，通用 Agents 为 `~/.agents/skills/`。目标位置已有同名目录时，先问用户是否覆盖。
> 3. 按实际使用的 Skill 运行自检：字幕用 `bash <skill 目录>/bri-video-srt/scripts/check_setup.sh`；封面用 `python3 <skill 目录>/bri-cover-generate/scripts/cover_pipeline.py doctor`。封面功能不需要 Whisper 或语音模型。
> 4. 告诉用户：新开一次对话后，用自然语言提供对应素材，或直接调用 Skill 名称即可使用。

### 方式二：skills CLI

```bash
npx -y skills add bridge-wang/creator-skills -g --all
```

只安装封面 Skill：

```bash
npx -y skills add bridge-wang/creator-skills -g --skill bri-cover-generate -y
```

安装到当前项目并指定 Codex：

```bash
npx -y skills add bridge-wang/creator-skills --skill bri-cover-generate --agent codex -y --copy
```

### 方式三：Claude Code 插件市场

```bash
claude plugin marketplace add bridge-wang/creator-skills
claude plugin install bri-video-srt@creator-tools
claude plugin install bri-cover-generate@creator-tools
```

## 依赖

### 视频剪辑与字幕

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

### 封面生成

需要 Python 3.10+、Pillow、FFmpeg 和 FFprobe。优先使用当前环境已有工具，不会自动安装软件或下载语音模型。

```bash
python3 skills/bri-cover-generate/scripts/cover_pipeline.py doctor
```

字体文件与完整许可已内置，约 25 MB，运行时无需联网下载字体。当前输入以无硬字幕、SDR、方形像素视频为主；HDR 等特殊输入需先明确转换方式。

## Skill 目录

| Skill | 做什么 | 直接调用 |
|---|---|---|
| `bri-video-srt` | 两阶段视频工作流：未剪素材先粗剪并保护网页操作、输入、切页和模型等待；人工精剪锁定时间轴后，再生成最终 SRT。现成 SRT 可直接校准 | `/bri-video-srt <文件路径>`，或直接说「粗剪这个视频」「给精剪视频生成字幕」「校准这份字幕」 |
| [bri-cover-generate](skills/bri-cover-generate/SKILL.md) | 真实抽帧、三张候选、选定后四比例封面。默认 65% 黑色遮罩、白色思源宋体 Bold，支持 1–4 行标题 | `$bri-cover-generate`，附无字幕视频路径和封面文字 |

使用示例（安装后直接用自然语言）：

```text
粗剪这个视频 /Users/me/Videos/IMG_2035.MOV
给这个已经精剪的视频生成字幕 /Users/me/Videos/final.mp4
这条口播帮我删气口再出字幕 /Users/me/Videos/IMG_2036.MOV
校准一下这份字幕 /Users/me/Videos/final.srt
```

封面生成示例：

```text
$bri-cover-generate
视频：/path/to/无字幕视频.mp4
封面文字：第一行·第二行
```

也可以直接把文字分成 1–4 行发送，`·` 默认只表示换行，不进入图片。先获得 A/B/C 三张 3:4 候选，回复「选 B」后再生成四张成品：

| 比例 | 输出尺寸 |
| --- | --- |
| 3:4 | 1440 × 1920 |
| 9:16 | 1080 × 1920 |
| 16:9 | 1920 × 1080 |
| 4:3 | 1440 × 1080 |

字号按文字与比例自适应；竖版素材做横图时采用同帧左右重复拼接，不镜像人物。成品确认后清理登记的临时文件，保留选定原始帧、参数、成品、总览和字体授权依据。发布包不含用户视频、参考截图或本地测试记录。

已用真实视频跑通候选与四比例流程，并核对过已确认成品的像素一致性；不同视频的选帧和裁切仍需 Agent 查看画面。

## 自定义

- **专名词表**：转录里反复出现你所在领域的专名误识别（人名、产品名）时，把它加进 `skills/bri-video-srt/references/fixed_terms.tsv`（格式见文件头注释），下次校准自动生效。
- **保护短语**：不想被断句切开的短语，加到同目录 `protected_phrases.txt`，一行一条。
- **普通口播气口**：默认最多保留 `1.05` 秒，可让 Agent 调整 `max-pause`。
- **关键语句停顿**：全片/章节结论、核心判断、重大转折和行动号召前的停顿单独审计，默认调整到当前可听停顿约 `1.3` 倍。
- **实操型空白**：网页操作、输入、切页和模型等待先用“停顿前/开始/中间/结束/停顿后”五帧和前后口播做联合审计。只有可指明的输入、控件、切页、生成或滚动变化才保护，且只保护操作所需的最小窗口，总计最多 `7` 秒；静止页面与证据不足的停顿默认按普通气口压缩。

## 更新

已安装后，直接对 Agent 说：

```text
更新 creator-tools：https://github.com/bridge-wang/creator-skills
```

Agent 会重新拉取仓库并覆盖 `skills/` 下的同名目录；你在 `fixed_terms.tsv` / `protected_phrases.txt` 里的自定义内容注意先备份。插件市场用户执行：

```bash
claude plugin marketplace update creator-tools
claude plugin update bri-video-srt@creator-tools
claude plugin update bri-cover-generate@creator-tools
```

## 许可证

代码和文档采用 [MIT](LICENSE)。

`bri-cover-generate` 附带的思源宋体文件单独遵循 [SIL Open Font License 1.1](skills/bri-cover-generate/assets/fonts/LICENSE.txt)，不受 MIT 许可替代。允许使用该字体制作及销售商业封面作品；再分发字体时保留版权和许可，不单独售卖字体文件。详细来源及使用边界见[字体授权说明](skills/bri-cover-generate/references/font-license.md)。
