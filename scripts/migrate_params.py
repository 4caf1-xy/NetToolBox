# -*- coding: utf-8 -*-
"""
migrate_params.py —— 存量种子 params 结构化迁移（schema 升级 2026-09-30）

【退役声明 2026-10-09 批2】
    desc_pending 标记机制已退役：全库 986 个标记已清扫完毕，validate 对
    结构化条目空 description 一律 ERROR（renderer.check_entry_params 单一执法点）。
    本脚本使命完成：migrate 侧 997/997 spec 早已结构化；strip 侧已空转（重跑 0 清理）。
    保留代码作历史参考与新库兜底工具；下次工程清理批可评估删除。

功能：
    扫描 seed_data/*.json 全部 command 条目，把旧格式 params（name/label/default/
    required/validate/example）升级为结构化 params：
        + type     : 从 validate 推导（ipv4→ip / int:→int+range / enum:→enum+choices /
                     其余→string；显式 validate 原样保留，不合并不删除）
        + description : 迁移骨架一律留空（后续 AI 分批补齐），打 desc_pending 标记
        + desc_pending : 【已退役】迁移期标记——原 validate_seed 对带标记的空
                     description 只报 WARNING；现空 description 一律 ERROR

纪律（对齐任务书硬约束）：
    · 默认 dry-run：只输出统计，不改任何文件
    · --apply 落盘前先把 seed_data/*.json 快照到 backup/seed-snapshot-<时间戳>/
    · 只动 params 段：command 本体、notes、verified 等一律不碰
    · 已结构化（带 type）的 spec 原样跳过，可重复执行（幂等）

用法：
    python scripts/migrate_params.py            # dry-run，输出统计
    python scripts/migrate_params.py --apply    # 快照 + 落盘
    python scripts/migrate_params.py --strip-pending   # 补完描述后清扫标记
"""

import io
import json
import os
import re
import shutil
import sys
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(REPO, "app")
BACKUP = os.path.join(REPO, "backup")
sys.path.insert(0, APP)

SEED_DIR = os.path.join(APP, "seed_data")

_RE_INT = re.compile(r"^int:(-?\d+)-(-?\d+)$")


def derive_struct(spec):
    """
    旧格式 spec → 结构化 spec（返回新 dict 或 None 表示无需迁移）。
    type 推导口径（与 renderer.normalize_spec 互补：显式 validate 保留 + 反推 type）：
        validate=ipv4/ip        → type=ip
        validate=int:lo-hi      → type=int  + range=lo-hi
        validate=enum:a|b|c     → type=enum + choices=[...]
        validate=vlan/masklen/port/port_range/ifname/path/… 或空 → type=string
        （validate 字段一律原样保留，UI 与校验都靠它工作）
    """
    if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
        return None
    if str(spec.get("type") or "").strip():
        return None                                    # 已结构化，幂等跳过

    rule = (spec.get("validate") or "").strip()
    stype, range_hint, choices = "string", "", None
    if rule in ("ipv4", "ip"):
        stype = "ip"
    else:
        m = _RE_INT.match(rule)
        if m:
            stype, range_hint = "int", "%s-%s" % (m.group(1), m.group(2))
        elif rule.startswith("enum:"):
            stype = "enum"
            choices = [x for x in rule[5:].split("|") if x]

    out = dict(spec)
    out["type"] = stype
    if range_hint:
        out["range"] = range_hint
    if choices is not None:
        out["choices"] = choices
    out["description"] = str(spec.get("description") or "")
    out["desc_pending"] = True
    return out


def main():
    argv = sys.argv[1:]
    do_apply = "--apply" in argv
    do_strip = "--strip-pending" in argv

    files = sorted(f for f in os.listdir(SEED_DIR) if f.endswith(".json"))
    total_entries = total_specs = total_migrated = 0
    type_counter = {}
    plan = []                       # (filename, data, changed)

    for fn in files:
        path = os.path.join(SEED_DIR, fn)
        with open(path, encoding="utf-8") as fp:
            raw = fp.read()
        data = json.loads(raw)
        entries = data.get("entries")
        if not isinstance(entries, list):
            continue                # 排查树 / 报错字典不处理

        changed = False
        for e in entries:
            total_entries += 1
            params = e.get("params")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = []
            if not isinstance(params, list):
                params = []
            new_params = []
            for spec in params:
                total_specs += 1
                migrated = derive_struct(spec)
                if migrated is None:
                    new_params.append(spec)
                    continue
                total_migrated += 1
                type_counter[migrated["type"]] = type_counter.get(migrated["type"], 0) + 1
                changed = True
                new_params.append(migrated)
            if changed or params != e.get("params"):
                e["params"] = new_params
        plan.append((fn, data, raw, changed))

    # ---- 统计输出 ----
    print("=" * 66)
    print("params 结构化迁移 %s" % ("【APPLY 落盘】" if do_apply else
                                    ("【清扫 desc_pending】" if do_strip else "【DRY-RUN 预览】")))
    print("=" * 66)
    print("command 条目 %d ｜ params spec 总数 %d ｜ 本次迁移 %d ｜ 已结构化跳过 %d"
          % (total_entries, total_specs, total_migrated, total_specs - total_migrated))
    print("type 推导分布：%s" % json.dumps(type_counter, ensure_ascii=False))
    print("-" * 66)
    for fn, data, raw, changed in plan:
        n = sum(1 for e in data.get("entries", [])
                for s in (e.get("params") or [])
                if isinstance(s, dict) and s.get("type"))
        mark = "改" if changed else "·"
        print("  [%s] %-26s entries=%-3d 结构化 spec=%d" % (mark, fn,
              len(data.get("entries", [])), n))

    if do_strip:
        cleaned = 0
        for fn, data, raw, _c in plan:
            dirty = False
            for e in data.get("entries", []):
                for s in (e.get("params") or []):
                    if isinstance(s, dict) and s.get("desc_pending"):
                        if str(s.get("description") or "").strip():
                            s.pop("desc_pending", None)    # 已有描述 → 摘标记
                            dirty = True
                            cleaned += 1
                        # 仍为空 → 保留标记，继续 WARNING 提醒
            if dirty:
                _write(fn, data)
        print("清扫完成：摘除 desc_pending 标记 %d 个（描述仍为空的保留标记继续提醒）" % cleaned)
        return 0

    if not do_apply:
        # round-trip 校验：未变更文件重序列化必须与原文一致（证明落盘不会产生格式漂移）
        drift = 0
        for fn, data, raw, changed in plan:
            if changed:
                continue
            if json.dumps(data, ensure_ascii=False, indent=2) + "\n" != raw:
                drift += 1
                print("  !! 格式漂移预警：%s（apply 时全文会被重排，内容不变）" % fn)
        print("-" * 66)
        print("dry-run 结论：未变更文件 round-trip 漂移 %d 个；"
              "确认无误后执行 --apply 落盘（先自动快照到 backup/）" % drift)
        return 0

    # ---- 快照 + 落盘 ----
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_dir = os.path.join(BACKUP, "seed-snapshot-%s" % stamp)
    os.makedirs(snap_dir, exist_ok=True)
    for fn in files:
        shutil.copy2(os.path.join(SEED_DIR, fn), os.path.join(snap_dir, fn))
    print("快照完成：%s（%d 个文件）" % (snap_dir, len(files)))

    written = 0
    for fn, data, raw, changed in plan:
        if changed:
            _write(fn, data)
            written += 1
    print("落盘完成：改写 %d 个种子文件。下一步：跑 --validate-seed（应 EXIT=0，"
          "desc_pending 空描述记 WARNING），然后 AI 分批补 description。" % written)
    return 0


def _write(fn, data):
    path = os.path.join(SEED_DIR, fn)
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


if __name__ == "__main__":
    sys.exit(main())
