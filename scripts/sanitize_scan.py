#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sanitize_scan.py —— 脱敏扫描 CLI 薄壳。

核心实现在 app/sanitize_scan.py（红线件；export_site 与 UI 共用）：
    python scripts/sanitize_scan.py --db <command_lib.db>   # 只读全库扫描
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))

from sanitize_scan import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
