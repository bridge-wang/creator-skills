#!/usr/bin/env bash
# video-srt 依赖自检：只检查、不安装。缺什么就打印对应的安装命令。
# 用法：bash check_setup.sh
set -u

MODEL_PATH="${WHISPER_MODEL:-$HOME/Models/whisper/ggml-large-v3-turbo.bin}"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
MODEL_MIN_BYTES=1500000000   # 完整模型约 1.62 GB，小于此值视为下载不完整

ok=0; miss=0

check() { # $1=名称 $2=检测命令 $3=安装提示
  if eval "$2" >/dev/null 2>&1; then
    echo "✅ $1"
    ok=$((ok+1))
  else
    echo "❌ 缺少 $1"
    echo "   安装：$3"
    miss=$((miss+1))
  fi
}

echo "== video-srt 依赖自检 =="
check "ffmpeg / ffprobe" "command -v ffmpeg && command -v ffprobe" \
  "macOS: brew install ffmpeg | Linux: apt install ffmpeg | Windows: winget install ffmpeg"
check "whisper-cli (whisper.cpp)" "command -v whisper-cli" \
  "macOS: brew install whisper-cpp | 其他平台: https://github.com/ggml-org/whisper.cpp"
check "python3" "command -v python3" \
  "macOS: brew install python | https://www.python.org/downloads/"

if [ -f "$MODEL_PATH" ] && [ "$(wc -c < "$MODEL_PATH" | tr -d ' ')" -ge "$MODEL_MIN_BYTES" ]; then
  echo "✅ Whisper 模型（large-v3-turbo）：$MODEL_PATH"
  ok=$((ok+1))
else
  echo "❌ 缺少 Whisper 模型（ggml-large-v3-turbo.bin，约 1.6 GB）"
  echo "   下载：mkdir -p \"$(dirname "$MODEL_PATH")\" && curl -L -o \"$MODEL_PATH\" \"$MODEL_URL\""
  echo "   （或设置环境变量 WHISPER_MODEL 指向你已有的模型文件）"
  miss=$((miss+1))
fi

# 可选依赖：只有「删气口」功能需要
if command -v auto-editor >/dev/null 2>&1 || [ -x "$HOME/.local/bin/auto-editor" ]; then
  echo "✅ auto-editor（可选，删气口用）"
else
  echo "⚠️  未安装 auto-editor（可选，只有『删气口』功能需要；不装则跳过该步）"
  echo "   安装：pip install auto-editor，或到 https://github.com/WyattBlue/auto-editor/releases 下载对应平台二进制放入 PATH"
fi

echo
if [ "$miss" -eq 0 ]; then
  echo "🎉 全部就绪，可以直接使用 video-srt。"
else
  echo "还差 $miss 项必需依赖，按上面提示安装后重新运行本脚本。"
  exit 1
fi
