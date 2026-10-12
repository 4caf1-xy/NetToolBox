#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_site.py —— 静态站点导出 CLI 薄壳。

核心实现在 app/site_export.py（exe 打包与 UI 菜单共用同一实现）：
    python scripts/export_site.py --out dist/站点.html [--db path]
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))

from site_export import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
