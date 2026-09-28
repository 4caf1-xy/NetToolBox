# -*- coding: utf-8 -*-
"""
migrate_to_slug.py —— 一次性迁移脚本（规格对齐 P0 的配套工具）

把旧版本（schema v1）的数据迁移到新规范（schema v2）：
    1. entries 表补 platform(network/linux) 与 duration(temp/perm/both) 两列
    2. platform 按 device_type 回填；duration 留空
    3. vendor / os_family 从"显示名"统一改成"小写 slug"
       （Cisco → cisco、华为 → huawei、Cisco IOS → ios、华为 VRP5 → vrp5 …）
    4. history 表补 table_name / record_id 并回填 entries 语义
    5. seed_data/*.json 里的对应字段一并改写

用法：
    python migrate_to_slug.py              # 执行迁移（自动备份）
    python migrate_to_slug.py --dry-run    # 只报告要改什么，不动数据
    python migrate_to_slug.py --no-backup  # 跳过备份（不推荐）

安全性：
    · 动手前先把 command_lib.db 备份成 command_lib.db.bak-<时间戳>，备份失败即中止
    · 幂等：重复执行不会重复修改，第二次跑会报告"无需变更"
    · 只读库（U 盘写保护）自动跳过并提示
    · 程序运行时的自动迁移（db._migrate）与本脚本做的事一致，
      本脚本的价值在于"显式备份 + 全量对照报告 + 可批量修种子 JSON"
"""

import io
import sqlite3
import json
import os
import sys
import glob
import shutil
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import db as dbmod          # noqa: E402  （放在 sys.path 之后导入）

DB_PATH = os.path.join(BASE, dbmod.DB_FILENAME)
SEED_DIR = os.path.join(BASE, "seed_data")


def log(text):
    print(text)


def backup_db(dry_run=False):
    """备份库文件；返回备份路径或 None（无库文件 / dry-run）"""
    if not os.path.isfile(DB_PATH):
        log("· 未发现 %s，跳过库备份（首次运行会自动建新库）" % dbmod.DB_FILENAME)
        return None
    if dry_run:
        log("· [dry-run] 会备份 %s" % dbmod.DB_FILENAME)
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = "%s.bak-%s" % (DB_PATH, stamp)
    try:
        shutil.copy2(DB_PATH, target)
    except Exception as exc:
        log("✘ 备份失败，已中止迁移：%s" % exc)
        raise SystemExit(1)
    log("· 已备份 → %s" % os.path.basename(target))
    return target


def _db_precheck():
    """
    用原生 sqlite3 做"迁移前快照"（不经过 db.Database，避免自动迁移把数据先改掉）：
    返回 (缺列列表, 需要归一化维度的条目数, 需要回填的 history 条数)
    """
    if not os.path.isfile(DB_PATH):
        return [], 0, 0
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cols = set(r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall())
        missing = [c for c in ("platform", "duration") if c not in cols]
        hcols = set(r["name"] for r in conn.execute("PRAGMA table_info(history)").fetchall())
        if "table_name" not in hcols:
            missing.append("history.table_name")
        if "record_id" not in hcols:
            missing.append("history.record_id")

        need_norm = 0
        for r in conn.execute("SELECT vendor, os_family FROM entries").fetchall():
            if (dbmod.normalize_vendor(r["vendor"]) != (r["vendor"] or "")
                    or dbmod.normalize_os(r["os_family"]) != (r["os_family"] or "")):
                need_norm += 1
        need_hist = 0
        if "table_name" in hcols:
            need_hist = conn.execute(
                "SELECT count(*) FROM history WHERE table_name IS NULL OR table_name = ''"
            ).fetchone()[0]
        conn.close()
        return missing, need_norm, need_hist
    except sqlite3.Error:
        return [], 0, 0


def migrate_db(dry_run=False):
    """迁移库内数据；返回 (改动条目数, 是否只读)"""
    if not os.path.isfile(DB_PATH):
        return 0, False

    missing, pre_need_norm, pre_need_hist = _db_precheck()
    if missing:
        log("· 待补列：%s" % "、".join(missing))
    if pre_need_norm:
        log("· 待归一化维度：%d 条（显示名 → slug）" % pre_need_norm)
    if pre_need_hist:
        log("· 待回填 history：%d 条（table_name/record_id）" % pre_need_hist)

    if dry_run:
        log("· [dry-run] 未改动任何数据（以上为将要处理的数量）")
        return pre_need_norm, False

    # 构造 Database 会自动跑 _migrate()：补列 + 回填 + 归一化，全部幂等
    database = dbmod.Database(DB_PATH)
    if database.readonly:
        log("· 命令库为只读（U 盘写保护），跳过库内迁移")
        database.close()
        return 0, True

    database.conn.commit()
    log("· 库统计：%s｜平台：%s" % (database.stats(), database.platform_stats()))
    log("· schema 版本：%s" % database.conn.execute("PRAGMA user_version").fetchone()[0])
    database.close()
    return pre_need_norm, False


def migrate_seed_files(dry_run=False):
    """把 seed_data/*.json 里的 vendor / os_family / platform / duration 归一化"""
    files = sorted(glob.glob(os.path.join(SEED_DIR, "*.json")))
    if not files:
        log("· seed_data 下没有 JSON 文件，跳过")
        return 0, 0

    touched_files = 0
    touched_entries = 0
    for path in files:
        try:
            with io.open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception as exc:
            log("  ✘ %s 解析失败，跳过：%s" % (os.path.basename(path), exc))
            continue
        entries = data.get("entries", []) if isinstance(data, dict) else data
        changed = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            old = (entry.get("vendor") or "", entry.get("os_family") or "",
                   entry.get("platform") or "", entry.get("duration"))
            entry["vendor"] = dbmod.normalize_vendor(entry.get("vendor"))
            entry["os_family"] = dbmod.normalize_os(entry.get("os_family"))
            entry["platform"] = dbmod.normalize_platform(entry.get("platform"))
            entry["duration"] = dbmod.normalize_duration(entry.get("duration"))
            if old != (entry["vendor"], entry["os_family"],
                       entry["platform"], entry["duration"]):
                changed += 1
        if isinstance(data, dict):
            data["schema"] = dbmod.SCHEMA_VERSION
            data["vendor_slug"] = dbmod.normalize_vendor(data.get("vendor_slug", ""))
        if changed:
            touched_files += 1
            touched_entries += changed
            if dry_run:
                log("  [dry-run] %-22s 需归一化 %d 条" % (os.path.basename(path), changed))
            else:
                with io.open(path, "w", encoding="utf-8") as fp:
                    json.dump(data, fp, ensure_ascii=False, indent=2)
                    fp.write("\n")
                log("  %-22s 已归一化 %d 条" % (os.path.basename(path), changed))
        else:
            log("  %-22s 无需变更" % os.path.basename(path))
    return touched_files, touched_entries


def main():
    dry_run = "--dry-run" in sys.argv
    no_backup = "--no-backup" in sys.argv

    log("=" * 66)
    log("NetToolBox 规格对齐迁移（v1 → v2：platform/duration + 维度 slug）")
    log("模式：%s" % ("只报告不修改（dry-run）" if dry_run else "实际执行"))
    log("=" * 66)

    if not dry_run and not no_backup:
        backup_db(dry_run=False)

    log("-" * 66)
    log("【1/2】命令库")
    db_changed, readonly = migrate_db(dry_run=dry_run)

    log("-" * 66)
    log("【2/2】种子库 JSON")
    files_changed, entries_changed = migrate_seed_files(dry_run=dry_run)

    log("=" * 66)
    if dry_run:
        log("dry-run 结果：库内 %d 条、%d 个 JSON 文件（%d 条）需要归一化"
            % (db_changed, files_changed, entries_changed))
        log("确认无误后去掉 --dry-run 再跑一次即可实际执行。")
    else:
        log("迁移完成：库内 %d 条、%d 个 JSON 文件（%d 条）已归一化"
            % (db_changed, files_changed, entries_changed))
        if readonly:
            log("注意：命令库当时为只读，库内迁移被跳过 —— 关闭 U 盘写保护后重跑本脚本即可。")
        log("建议接着执行：python main.py --validate-seed  （种子库质检）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
