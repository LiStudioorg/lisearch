#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""使用 curl 获取 URL，并输出可读纯文本。

本文件为 `core.fetcher` 的命令行入口包装器，可直接执行：

    python3 scripts/fetch.py "https://example.com/article" [--max-chars 20000]

详见 core/fetcher.py 的功能说明。
"""

import os
import sys

# 确保可从任意工作目录执行，并定位到技能根目录下的 core 包。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.fetcher import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
