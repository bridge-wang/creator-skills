#!/usr/bin/env python3
"""对 SRT 字幕文本行应用「中文文案排版指北」的机械排版规则。

只处理字幕文本行（跳过序号行和时间轴行），规则：
1. 中文与英文单词之间加一个半角空格
2. 中文与数字之间加一个半角空格
3. 数字与单位之间加空格，但「%」「°」和数字之间不加（指北例外条款）
4. 全角标点与其他字符之间不加空格（正则只匹配汉字，天然满足）
5. 压缩连续空格，去行尾空格

用法：python3 zh_typography.py input.srt [--output output.srt]
不给 --output 时原地覆盖。
"""

import argparse
import re
import sys

CJK = r"一-鿿㐀-䶿"

# 中文 ↔ 英文/数字 之间补空格；% 和 ° 视作前面数字的一部分（后接中文时要空格）
RE_CJK_BEFORE = re.compile(rf"([{CJK}])([A-Za-z0-9])")
RE_CJK_AFTER = re.compile(rf"([A-Za-z0-9%°])([{CJK}])")
RE_MULTI_SPACE = re.compile(r" {2,}")
TIMELINE = re.compile(r"^\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->")


def space_line(line: str) -> str:
    line = RE_CJK_BEFORE.sub(r"\1 \2", line)
    line = RE_CJK_AFTER.sub(r"\1 \2", line)
    line = RE_MULTI_SPACE.sub(" ", line)
    return line.rstrip()


def process(text: str) -> str:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.isdigit() or TIMELINE.match(line) or not stripped:
            out.append(line.rstrip())
        else:
            out.append(space_line(line))
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        result = process(f.read())
    dest = args.output or args.input
    with open(dest, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"typography applied -> {dest}")


if __name__ == "__main__":
    main()
