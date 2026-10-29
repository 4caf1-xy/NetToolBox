# -*- coding: utf-8 -*-
"""
tree_seed_io.py —— 排查树种子 JSON 的读/改/写引擎（F 批 MVP，纯逻辑零 Qt）

职责（MVP 边界：只动既有树的内部结构）
    只服务「编辑既有树内部结构 → 导出种子差异 → 门禁写入」这条链路：
      · list_tree_files     种子目录下的树 JSON 文件（排序稳定）
      · locate_tree_file    tree_id → 唯一归属文件（跨文件重复归属视为坏数据）
      · serialize           dict → 文本，逐字节保持既有格式
                            （UTF-8 无 BOM / indent=2 / CRLF / 尾部换行）
      · replace_tree        按 tree_id 替换树 + 同步 count 元数据
      · unified_diff        新旧文本统一差异（导出确认预览）
      · commit_tree         写入 → validate 门禁 → 失败回滚（原字节写回）
    写入走「同目录临时文件 + os.replace 原子替换」，写入中途被打断也
    不会在种子目录留下半截文件。

不做（留二批）：整树新建/删除、跨文件移动、拖拽复制、批量重构。

★ 纯逻辑约束：本模块不 import db / main / Qt —— validate 以可调用对象
  注入（签名 () -> (rc, output)），由 UI 层传 main.validate_seed 的包装，
  冒烟可传假门禁独立测试回滚。
"""

import difflib
import json
import os
import tempfile

TREE_FILE_GLOB = "trouble_trees_*.json"


class TreeSeedError(Exception):
    """种子 IO 失败（定位/解析/写入/门禁），UI 层直接把消息展示给用户"""


# ---------------------------------------------------------------------------
# 定位与读取
# ---------------------------------------------------------------------------

def list_tree_files(seed_dir):
    """seed_dir 下的树 JSON 文件（文件名排序，保证 UI 列表稳定）"""
    if not os.path.isdir(seed_dir):
        return []
    names = [n for n in os.listdir(seed_dir)
             if n.startswith("trouble_trees_") and n.endswith(".json")]
    return [os.path.join(seed_dir, n) for n in sorted(names)]


def load_doc(path):
    """读文件 → (doc dict, 原始 bytes)。解析失败抛 TreeSeedError。"""
    try:
        with open(path, "rb") as fp:
            raw = fp.read()
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise TreeSeedError("%s 解析失败：%s" % (os.path.basename(path), exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("trouble_trees"), list):
        raise TreeSeedError("%s 缺 trouble_trees 数组，不是排查树种子文件"
                            % os.path.basename(path))
    return doc, raw


def locate_tree_file(seed_dir, tree_id):
    """tree_id → (文件路径, 树 dict)。全部 26 棵树跨文件唯一（2026-10-29 实测）；
    重复归属说明种子坏了，报错而不是猜。找不到返回 (None, None)。"""
    hit = None
    dup = []
    for path in list_tree_files(seed_dir):
        doc, _raw = load_doc(path)
        for t in doc["trouble_trees"]:
            if t.get("tree_id") == tree_id:
                if hit:
                    dup.append(os.path.basename(path))
                else:
                    hit = (path, t)
    if dup:
        raise TreeSeedError("tree_id %s 同时出现在 %s 和 %s —— 种子归属冲突，先修数据"
                            % (tree_id, os.path.basename(hit[0]), "、".join(dup)))
    return hit or (None, None)


# ---------------------------------------------------------------------------
# 序列化与替换（保持既有字节风格，导出 diff 才不被格式噪声淹没）
# ---------------------------------------------------------------------------

def serialize(doc, crlf=False):
    """dict → 种子文本。既有文件口径：UTF-8 无 BOM / indent=2 / 尾部换行。
    换行风格按调用方探测传入（crlf=True 输出 CRLF，否则 LF）。
    2026-10-12 起 .gitattributes 对 *.json 钉死 eol=lf，四份树文件磁盘均已
    规范化为 LF；探测保留逻辑仅为兼容磁盘残留（回环逐字节一致），不应再产生 CRLF。"""
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if crlf:
        return text.replace("\n", "\r\n") + "\r\n"
    return text + "\n"


def detect_crlf(raw):
    """原文件字节 → 是否 CRLF 风格（含 \\r\\n 即按 CRLF 处理）。
    .gitattributes eol=lf 起磁盘应恒为 LF；本探测仅为兼容历史残留。"""
    return b"\r\n" in raw


def replace_tree(doc, new_tree):
    """按 tree_id 原位替换 doc["trouble_trees"] 中的树，并同步 count 元数据。
    键序保持：new_tree 应来自 load 出的 dict 的深拷贝改字段，不要重建新 dict。
    找不到目标树抛 KeyError。"""
    trees = doc["trouble_trees"]
    tid = new_tree.get("tree_id")
    for i, t in enumerate(trees):
        if t.get("tree_id") == tid:
            trees[i] = new_tree
            if "count" in doc:
                doc["count"] = len(trees)
            return doc
    raise KeyError("种子文件里没有 tree_id=%s 的树" % tid)


def unified_diff(old_text, new_text, fromfile="seed(当前)", tofile="seed(导出)"):
    """统一差异文本（导出确认对话框展示用）。无差异返回空串。"""
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=fromfile, tofile=tofile))


# ---------------------------------------------------------------------------
# 门禁写入：写入 → validate → 失败回滚（原子替换，不留中间态）
# ---------------------------------------------------------------------------

def _atomic_write(path, data):
    """同目录临时文件 + os.replace 原子替换。写入中途崩溃不会留半截种子。"""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tree_export_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def commit_tree(seed_dir, new_tree, validate_fn):
    """把编辑后的树写回种子文件，validate 门禁通过才算成功。

    流程（设计裁决②）：
        1. 定位 tree_id 唯一归属文件，留原字节底
        2. replace_tree + serialize → 新文本
        3. 与现文本相同 → 无事可做，返回 (True, "内容无变化", ...)
        4. 原子写入 → 调 validate_fn()（UI 层注入 main.validate_seed 包装）
        5. rc != 0 → 原字节写回（回滚），返回失败与 validate 输出
        6. rc == 0 → 成功

    返回 (ok, message, validate_output)。
    validate_fn 抛异常视同门禁失败（一并回滚）。
    """
    tid = new_tree.get("tree_id")
    path, _cur = locate_tree_file(seed_dir, tid)
    if not path:
        raise TreeSeedError("种子目录里找不到 tree_id=%s（编辑器只改既有树）" % tid)
    doc, old_bytes = load_doc(path)
    try:
        replace_tree(doc, new_tree)
    except KeyError as exc:
        raise TreeSeedError(str(exc))
    new_bytes = serialize(doc, crlf=detect_crlf(old_bytes)).encode("utf-8")
    if new_bytes == old_bytes:
        return True, "内容无变化，未写盘。", ""
    _atomic_write(path, new_bytes)
    try:
        rc, output = validate_fn()
    except Exception as exc:
        rc, output = 1, "门禁执行异常：%s" % exc
    if rc != 0:
        _atomic_write(path, old_bytes)          # 回滚：原字节写回，不留中间态
        return False, "validate 门禁未通过（EXIT=%d），已回滚到改动前。" % rc, output
    return True, "已写入 %s，validate EXIT=0。" % os.path.basename(path), output
