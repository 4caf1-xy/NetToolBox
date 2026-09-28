# -*- coding: utf-8 -*-
"""
backup_db.py —— command_lib.db 时间戳备份工具（任务3配套）

用途
    把主库复制到 backup/ 目录，文件名带时间戳，自动保留最近 10 份。
    公开仓不收 *.db（.gitignore 已覆盖），备份目录同样不入库。

用法
    python scripts/backup_db.py                  # 自动定位库文件
    python scripts/backup_db.py D:\\path\\x.db   # 显式指定库文件
    python scripts/backup_db.py --keep 20        # 调整保留份数

查找顺序（自动模式）
    1. app/command_lib.db        （源码运行：db.py 与种子同基准目录）
    2. dist_package/command_lib.db（打包产物随附库）
    3. ./command_lib.db          （U 盘便携场景）

硬性约束
    · 零联网、零 PyQt：纯标准库
    · 只读源文件（按字节复制），绝不写库
"""

from __future__ import print_function

import os
import shutil
import sys
import time

KEEP_DEFAULT = 10
BACKUP_DIRNAME = "backup"


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_db(explicit=None):
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit("错误：指定的库文件不存在：%s" % explicit)
        return explicit
    root = repo_root()
    for cand in (
        os.path.join(root, "app", "command_lib.db"),
        os.path.join(root, "dist_package", "command_lib.db"),
        os.path.join(os.getcwd(), "command_lib.db"),
    ):
        if os.path.isfile(cand):
            return cand
    raise SystemExit(
        "错误：未找到 command_lib.db（已尝试 app/、dist_package/、当前目录）。\n"
        "可显式指定：python scripts/backup_db.py <库文件路径>"
    )


def prune_old(backup_dir, keep):
    files = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith("command_lib") and f.endswith(".db")
    )
    overflow = len(files) - keep
    for name in files[:max(overflow, 0)]:
        os.remove(os.path.join(backup_dir, name))
        print("清理旧备份：%s" % name)
    return max(overflow, 0)


def main(argv):
    keep = KEEP_DEFAULT
    if "--keep" in argv:
        try:
            keep = int(argv[argv.index("--keep") + 1])
        except (IndexError, ValueError):
            raise SystemExit("错误：--keep 后面要跟数字，如 --keep 20")

    src = find_db(argv[0] if argv and not argv[0].startswith("--") else None)
    backup_dir = os.path.join(repo_root(), BACKUP_DIRNAME)
    os.makedirs(backup_dir, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.splitext(os.path.basename(src))[0]
    dst = os.path.join(backup_dir, "%s-%s.db" % (base, stamp))
    shutil.copy2(src, dst)

    size_kb = os.path.getsize(dst) / 1024.0
    print("备份完成：%s (%.1f KB)" % (dst, size_kb))
    pruned = prune_old(backup_dir, keep)
    total = len([f for f in os.listdir(backup_dir) if f.endswith(".db")])
    print("当前备份 %d 份（保留最近 %d 份%s）" % (total, keep, "，已清理 %d 份" % pruned if pruned else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
