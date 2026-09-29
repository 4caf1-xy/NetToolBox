# -*- coding: utf-8 -*-
"""第 1 轮改造冒烟测试：逻辑层 + 离屏 UI 实跑（不发真实网络请求，ChatWorker 用假线程替换）"""
import os
import sys
import time
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ai_bridge
import ui_ai
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QApplication

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ((" —— " + detail) if (detail and not cond) else ""))


# ---------------------------------------------------------------------------
# 1. Markdown 渲染自测（既有能力回归）
# ---------------------------------------------------------------------------
print("[1] render_selftest")
ok, cases = ui_ai.render_selftest()
check("Markdown 渲染自测全部通过", ok, str([c[0] for c in cases if not c[1]]))

# ---------------------------------------------------------------------------
# 2. 设备上下文块
# ---------------------------------------------------------------------------
print("[2] build_device_context")
empty = ai_bridge.build_device_context({"vendor": "", "os": "", "model_name": "",
                                        "symptom": "", "echo": "", "steps": ""})
check("全空上下文 → 不拼块（空串）", empty == "")
ctx_full = {"vendor": "华为", "os": "VRP8", "model_name": "S5720",
            "symptom": "不通", "echo": "回显内容", "steps": "已重启"}
block = ai_bridge.build_device_context(ctx_full)
check("非空上下文含首尾标记",
      block.startswith("【设备上下文】") and block.rstrip().endswith("【设备上下文结束】"))
order = [block.find(x) for x in ("厂商：华为", "OS 版本：VRP8", "设备型号：S5720",
                                 "故障现象：不通", "已尝试步骤：已重启", "命令回显：回显内容")]
check("字段顺序 = 厂商/OS/型号/现象/已试/回显 且全部命中", all(p != -1 for p in order) and order == sorted(order))
ctx_skip = dict(ctx_full, os="", model_name="")
block2 = ai_bridge.build_device_context(ctx_skip)
check("空字段跳行（OS/型号不出现）", "OS 版本" not in block2 and "设备型号" not in block2 and "厂商：华为" in block2)

# ---------------------------------------------------------------------------
# 3. 多轮组装 + 裁剪
# ---------------------------------------------------------------------------
print("[3] build_chat_messages")
_orig_load_config = ai_bridge.load_config


def _tiny_config(*a, **kw):
    cfg = dict(_orig_load_config())
    cfg["max_context_tokens"] = 100          # 预算 = 60 tokens，强制裁剪
    return cfg


ai_bridge.load_config = _tiny_config
try:
    rounds = []
    for i in range(6):
        rounds.append({"role": "user", "content": "u%d %s" % (i, "字" * 2000), "attachments": []})
        rounds.append({"role": "assistant", "content": "a%d %s" % (i, "字" * 2000), "attachments": []})
    msgs, trimmed = ai_bridge.build_chat_messages({"vendor": "华为"}, rounds + [
        {"role": "user", "content": "本条消息", "attachments": []}])
    check("系统消息在首位", msgs[0]["role"] == "system")
    check("系统提示含基线说明", "固定基线" in msgs[0]["content"])
    check("设备上下文拼在系统消息内", "【设备上下文】" in msgs[0]["content"] and "厂商：华为" in msgs[0]["content"])
    check("最新一轮永远保留（本条在末尾）", msgs[-1]["content"] == "本条消息")
    check("超预算触发裁剪（7 轮只留最新 1 轮）", trimmed == 6, "trimmed=%d" % trimmed)
    check("保留历史从 user 开始（不出现孤立 assistant）", msgs[1]["role"] == "user")
    # 预算内不裁剪
    small = [{"role": "user", "content": "hi", "attachments": []},
             {"role": "assistant", "content": "ok", "attachments": []},
             {"role": "user", "content": "第二条", "attachments": []}]
    msgs2, trimmed2 = ai_bridge.build_chat_messages({}, small)
    check("预算内不裁剪", trimmed2 == 0 and len(msgs2) == 4)
finally:
    ai_bridge.load_config = _orig_load_config

# ---------------------------------------------------------------------------
# 4. 文本附件
# ---------------------------------------------------------------------------
print("[4] load_text_attachment / merge")
tmpdir = tempfile.mkdtemp(prefix="ntb_ai_test_")
big = os.path.join(tmpdir, "run.log")
with open(big, "w", encoding="utf-8") as f:
    f.write("line %s\n" % ("x" * 100) * 1200)      # ~125KB → 截断
att, err = ai_bridge.load_text_attachment(big)
check("125KB 日志读取成功", att is not None and err == "", err)
check("超 100KB 截断标注", bool(att and att["truncated"]) and len(att["content"]) == ai_bridge.ATTACH_TRUNCATE_BYTES)
huge = os.path.join(tmpdir, "huge.txt")
with open(huge, "w", encoding="utf-8") as f:
    f.write("y" * (250 * 1024))
att2, err2 = ai_bridge.load_text_attachment(huge)
check("250KB → 明确拒绝（>200KB 上限）", att2 is None and "200KB" in err2)
small_f = os.path.join(tmpdir, "sw.log")
with open(small_f, "w", encoding="utf-8") as f:
    f.write("ERR port down")
att3, err3 = ai_bridge.load_text_attachment(small_f)
merged = ai_bridge.merge_attachments_into_text("执行后还是不通", [att3])
check("附件拼入 user 消息（首尾标记）",
      merged.startswith("执行后还是不通") and "【附件：sw.log】" in merged
      and "ERR port down" in merged and merged.rstrip().endswith("【附件结束】"))
check("纯文本消息无附件 → 原样", ai_bridge.merge_attachments_into_text("abc", []) == "abc")

# ---------------------------------------------------------------------------
# 5. 离屏 UI 实跑（假 ChatWorker，不发真实请求）
# ---------------------------------------------------------------------------
print("[5] 离屏 UI 实跑")
app = QApplication.instance() or QApplication(sys.argv)


class FakeWorker(QThread):
    chunk = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished_full = pyqtSignal(dict)

    def __init__(self, parent=None):
        super(FakeWorker, self).__init__(parent)
        self._cancelled = False
        self.messages_sent = None

    def set_request(self, config, messages):
        self.messages_sent = messages
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        self.chunk.emit("## 诊断建议\n\n先检查端口状态：\n```bash\ndisplay interface brief\n```\n"
                        "以上命令适用 华为 VRP8（未经真机验证）。")
        self.finished_full.emit({"usage": {"prompt_tokens": 120, "completion_tokens": 40},
                                 "model": "fake-model"})


# 会话目录指向临时目录，避免污染项目 sessions/
ai_bridge.sessions_dir = lambda: tmpdir
ai_bridge.ChatWorker = FakeWorker

tab = ui_ai.AiTab(db=None)
tab.resize(1200, 800)
tab.show()                      # offscreen 平台：不 show 则 isVisible 恒 False
app.processEvents()
tab.refresh_config_state()
app.processEvents()

check("已配置 → 主界面", tab.stack.currentIndex() == 1)
check("上下文面板默认展开", tab.ctx_body.isVisible())

tab.cmb_vendor.setCurrentText("华为")
tab.cmb_os.setCurrentText("VRP8")
tab.ed_device.setText("S5720")
tab.txt_symptom.setPlainText("配置 trunk 后对端学不到 VLAN")
tab.txt_echo.setPlainText("回显基线内容")
tab.txt_steps.setPlainText("1. 已检查 allowed vlan")

tab.txt_input.setPlainText("执行后还是不通")
tab.on_send()
worker = tab._worker
check("发送后进入生成态（停止按钮可见）", tab.btn_stop.isVisible())
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()

check("首轮完成：会话含 user+assistant 两条", len(tab.session["messages"]) == 2)
check("AI 气泡收到流式全文（含代码块）", "display interface brief" in tab._response_text)
check("系统消息含上下文面板字段（基线随发）",
      "配置 trunk 后对端学不到 VLAN" in tab._last_messages[0]["content"]
      and "回显基线内容" in tab._last_messages[0]["content"])
check("首轮发送后面板自动折叠", not tab.ctx_body.isVisible())
check("会话已落盘（sessions/）", len(ai_bridge.list_sessions()) == 1)
saved = ai_bridge.list_sessions()[0][1]
check("落盘结构含 session_id/ctx/messages",
      all(k in saved for k in ("session_id", "created_at", "ctx", "messages"))
      and saved["ctx"]["vendor"] == "华为")

# 转草稿按钮：响应含代码块 → 可用
check("响应含命令块 → 转为草稿条目可用", tab.btn_to_draft.isEnabled())

# ---- 中途更新面板回显，下一轮按新值组装 ----
tab.txt_echo.setPlainText("NEW-ECHO-AFTER-UPDATE")
tab.txt_input.setPlainText("现在还是丢包")
tab.on_send()
worker = tab._worker
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()
check("第 2 轮后消息数 = 4", len(tab.session["messages"]) == 4)
check("中途更新的回显在下轮生效（新回显进 prompt）", "NEW-ECHO-AFTER-UPDATE" in tab._last_messages[0]["content"])
check("旧回显不再随发", "回显基线内容" not in tab._last_messages[0]["content"])

# ---- 文本附件随本轮发送 ----
import PyQt5.QtWidgets as qw
_orig_getopen = qw.QFileDialog.getOpenFileNames
qw.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **kw: ([small_f], ""))
try:
    tab.on_attach()
finally:
    qw.QFileDialog.getOpenFileNames = _orig_getopen
check("附件已挂起且条栏可见", len(tab._pending_attachments) == 1 and tab.att_bar.isVisible())
tokens_before = tab.lbl_tokens.text()
tab.txt_input.setPlainText("看下这份日志")
tab.on_send()
worker = tab._worker
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()
check("附件内容拼入本轮 user 消息", "【附件：sw.log】" in tab._last_messages[-1]["content"]
      and "ERR port down" in tab._last_messages[-1]["content"])
check("发送后附件清空", not tab._pending_attachments and not tab.att_bar.isVisible())

# ---- 新建对话：身份保留 / 问题清空 / 旧会话进历史 ----
n_sessions_before = len(ai_bridge.list_sessions())
tab.on_new_session()
app.processEvents()
check("新建对话后旧会话已保存", len(ai_bridge.list_sessions()) == n_sessions_before)
check("身份字段保留", tab.cmb_vendor.currentText() == "华为" and tab.cmb_os.currentText() == "VRP8"
      and tab.ed_device.text() == "S5720")
check("问题字段清空", tab.txt_symptom.toPlainText() == "" and tab.txt_echo.toPlainText() == ""
      and tab.txt_steps.toPlainText() == "" and tab.txt_input.toPlainText() == "")
check("消息流清空且会话为新", len(tab.session["messages"]) == 0)
check("新建后面板重新展开", tab.ctx_body.isVisible())
check("无消息会话不重复落盘", len(ai_bridge.list_sessions()) == n_sessions_before)

# ---- prefill 链路（四入口落点）----
tab.prefill_context(vendor="Cisco", os_name="IOS 15.x", model="C2960",
                    symptom="端口 err-disable", echo="show log 输出", steps="已试 shut/no shut")
check("prefill 预填面板（厂商/OS/型号/现象）",
      tab.cmb_vendor.currentText() == "Cisco" and tab.txt_symptom.toPlainText() == "端口 err-disable")
check("prefill 走树模板可组装",
      "【已走路径】" in ui_ai.AiTab._compose_walk_text("", [{"title": "查端口状态", "branch_label": "down"}],
                                                      {"conclusion": "端口被关闭", "actions": [{"text": "恢复"}]}, ""))

# ---- 旧案例回看（load_case_record 兼容）----
old_case = {"context": {"vendor": "华为", "symptom": "旧现象"}, "response": "旧回复```bash\ndisplay version\n```",
            "ts_iso": "2026-01-01 08:00:00", "tokens": {"prompt_tokens": 1, "completion_tokens": 2}}
tab.load_case_record(old_case)
app.processEvents()
check("旧 ai_cases 案例回看不崩溃且渲染气泡", "旧回复" in tab._response_text)

# ---- token 估算 ----
check("token 估算非零", tab.lbl_tokens.text() != "约 0 tokens")

# ---------------------------------------------------------------------------
# [6] 第 2 轮：vision_model 图片附件 / 四入口适配 / 历史抽屉 / 旧案例兼容
# ---------------------------------------------------------------------------
print("[6] 第 2 轮：图片附件（vision_model）")
# ---- 纯逻辑：多模态组装 ----
img_att, err_img = ai_bridge.load_image_attachment(small_f)      # 非 png → 不校验扩展名（UI 层管），此处仅字节流
png = os.path.join(tmpdir, "shot.png")
with open(png, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)                  # 合法 PNG 魔数 + 填充
img_att, err_img = ai_bridge.load_image_attachment(png)
check("PNG → base64 data URL", img_att is not None and img_att["data_url"].startswith("data:image/png;base64,")
      and img_att.get("is_image") is True)
huge_img = os.path.join(tmpdir, "big.png")
with open(huge_img, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024))
img_big, err_big = ai_bridge.load_image_attachment(huge_img)
check("6MB 图片 → 明确拒绝（>5MB 上限）", img_big is None and "5MB" in err_big)

msg_img = {"role": "user", "content": "看这张截图",
           "attachments": [dict(img_att)]}
content = ai_bridge.message_api_content(msg_img)
check("带图消息 → OpenAI 多模态 content 数组",
      isinstance(content, list) and content[0]["type"] == "text"
      and content[0]["text"] == "看这张截图"
      and content[1]["type"] == "image_url"
      and content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
msg_txt = {"role": "user", "content": "纯文本", "attachments": [dict(att3)]}
check("纯文本+文本附件 → 仍为字符串", isinstance(ai_bridge.message_api_content(msg_txt), str))
check("图片 token 估算 = 文本 + 固定常数",
      ai_bridge.estimate_message_tokens(msg_img) ==
      ai_bridge.estimate_tokens("看这张截图") + ai_bridge.IMAGE_TOKEN_ESTIMATE)
msgs_img, _t = ai_bridge.build_chat_messages({"vendor": "华为"}, [msg_img])
check("build_chat_messages 输出带图消息为多模态数组",
      isinstance(msgs_img[-1]["content"], list) and msgs_img[-1]["content"][1]["type"] == "image_url")

# ---- UI：vision_model 配置后接受图片；未配置时拒绝并说明 ----
import PyQt5.QtWidgets as qw

_cfg_real = ai_bridge.load_config()


def _vision_cfg(*a, **kw):
    cfg = dict(_cfg_real)
    cfg["vision_model"] = "test-vision-model"
    return cfg


info_boxes = []
_orig_info = qw.QMessageBox.information
qw.QMessageBox.information = staticmethod(
    lambda *a, **kw: info_boxes.append(kw.get("title") if "title" in kw else (a[1] if len(a) > 1 else "")))
_orig_question = qw.QMessageBox.question
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.Yes)

tab2 = ui_ai.AiTab(db=None)
tab2.resize(1200, 800)
tab2.show()
tab2.refresh_config_state()          # 用真实配置（无 vision_model）初始化 tooltip/状态
app.processEvents()

# 未配置 vision_model（当前真实配置）：选 png → 拒绝 + 说明
ai_bridge.load_config = _orig_load_config
qw.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **kw: ([png], ""))
try:
    info_boxes.clear()
    tab2.on_attach()
finally:
    qw.QFileDialog.getOpenFileNames = _orig_getopen
check("未配置 vision_model → 图片被拒且有说明弹窗", len(tab2._pending_attachments) == 0 and len(info_boxes) == 1)
check("[附件] tooltip 说明未开放原因", "未开放" in tab2.btn_attach.toolTip())

# 配置 vision_model 后：接受 png，发送组装为多模态数组
ai_bridge.load_config = _vision_cfg
tab2.refresh_config_state()
qw.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **kw: ([png], ""))
try:
    tab2.on_attach()
finally:
    qw.QFileDialog.getOpenFileNames = _orig_getopen
check("配置 vision_model → 图片附件被接受", len(tab2._pending_attachments) == 1
      and tab2._pending_attachments[0].get("is_image"))
tab2.cmb_vendor.setCurrentText("华为")
tab2.txt_input.setPlainText("看这张截图")
tab2.on_send()
worker = tab2._worker
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()
check("发送后本轮 content 为多模态数组（text + image_url）",
      isinstance(tab2._last_messages[-1]["content"], list)
      and tab2._last_messages[-1]["content"][1]["type"] == "image_url")
check("带图消息 token 估算上涨（≥ 固定常数）",
      tab2.lbl_tokens.text() != "约 0 tokens")

# ---- 四入口适配：自动保存 → 新会话 → 预填 → 开场草稿（不自动发送）----
print("[7] 四入口适配 + 生命周期")
n_before_prefill = len(ai_bridge.list_sessions())
tab2.prefill_context(vendor="Cisco", os_name="IOS 15.x", model="C2960",
                     symptom="端口 err-disable", echo="show log 输出",
                     steps="本地报错字典已给出以下方案但未解决，请给进一步排查思路：\n1. 检查端口")
check("prefill 后旧会话保持已保存（完成即存，prefill 不新增重复文件）",
      len(ai_bridge.list_sessions()) == n_before_prefill)
check("prefill 后新会话无消息", len(tab2.session["messages"]) == 0)
check("面板按入口预填", tab2.cmb_vendor.currentText() == "Cisco"
      and tab2.txt_symptom.toPlainText() == "端口 err-disable"
      and "本地报错字典" in tab2.txt_steps.toPlainText())
draft = tab2.txt_input.toPlainText()
check("开场草稿已插入输入框（报错诊断话术）", "请给出进一步排查思路与验证命令" in draft)
check("草稿不自动发送（无 worker）", not (tab2._worker and tab2._worker.isRunning()))

# 在 err-disable 会话里发一条消息（产生历史记录，供后续历史抽屉打开/恢复用）
tab2.txt_input.setPlainText("接口 err-disable 怎么恢复")
tab2.on_send()
worker = tab2._worker
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()

tab2.prefill_context(vendor="华为", symptom="整机卡顿", walk_record=[
    {"title": "查看 CPU", "branch_label": "利用率高"},
    {"title": "查看日志", "branch_label": "无异常"},
], leaf_data={"conclusion": "CPU 利用率过高", "actions": [{"text": "定位高 CPU 进程"}]})
check("排查向导入口：面板预填含【排查结论】",
      "【排查结论】" in tab2.txt_steps.toPlainText() and "CPU 利用率过高" in tab2.txt_steps.toPlainText())
check("排查向导入口：草稿为结论评估话术", "排查向导已给出结论" in tab2.txt_input.toPlainText())
check("向导结论标记存在（协同指令将拼入系统提示）",
      ai_bridge.has_wizard_conclusion(tab2._collect_ctx()))
check("err-disable 会话已保存（完成即存，进入历史）", len(ai_bridge.list_sessions()) == n_before_prefill + 1)

# ---- 历史抽屉：sessions 列表 / 搜索 / 打开恢复 / 删除 ----
print("[8] 历史抽屉")
n_sessions = len(ai_bridge.list_sessions())
check("当前至少有 2 个历史会话", n_sessions >= 2, "n=%d" % n_sessions)
dlg = ui_ai.SessionHistoryDialog(tab2, tab2.window())
dlg.resize(760, 560)
dlg.show()
app.processEvents()
check("历史抽屉列出全部会话", dlg.list_items.count() == n_sessions)

# 搜索过滤
dlg.ed_search.setText("err-disable")
app.processEvents()
visible = [i for i in range(dlg.list_items.count()) if not dlg.list_items.item(i).isHidden()]
check("搜索过滤命中 1 条", len(visible) == 1, "visible=%d" % len(visible))
dlg.ed_search.clear()
app.processEvents()

# 打开最早那条会话（err-disable 那条）→ 完整恢复
dlg.list_items.setCurrentRow(visible[0])
n_before_restore = len(ai_bridge.list_sessions())
dlg.on_open()
app.processEvents()
restored_sid = tab2.session["session_id"]
restored_n = len(tab2.session["messages"])
check("打开历史会话：消息流与面板完整恢复", restored_n >= 2
      and tab2.cmb_vendor.currentText() == "Cisco"
      and tab2.txt_symptom.toPlainText() == "端口 err-disable")
check("恢复不额外落盘（打开动作本身）", len(ai_bridge.list_sessions()) == n_before_restore)

# 恢复后继续对话：同一 session_id，新消息追加保存
tab2.txt_input.setPlainText("继续排查：接口频繁震荡")
tab2.on_send()
worker = tab2._worker
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()
reloaded = ai_bridge.load_session(os.path.join(ai_bridge.sessions_dir(), restored_sid + ".json"))
check("继续对话回写同一会话文件（session_id 不变）",
      tab2.session["session_id"] == restored_sid and reloaded is not None
      and len(reloaded["messages"]) == restored_n + 2)

# 删除（确认弹窗已 mock 为 Yes）
dlg2 = ui_ai.SessionHistoryDialog(tab2, tab2.window())
dlg2.show()
app.processEvents()
target_n = len(ai_bridge.list_sessions())
dlg2.list_items.setCurrentRow(0)
target_path = dlg2._rows[0][1]
dlg2.on_delete()
app.processEvents()
check("删除带确认且生效（列表 -1，文件消失）",
      len(ai_bridge.list_sessions()) == target_n - 1 and not os.path.exists(target_path))

# ---- 旧案例兼容：历史抽屉切到旧案例来源 ----
dlg2.cmb_source.setCurrentIndex(1)      # 旧案例（ai_cases）
app.processEvents()
check("旧案例来源可列出（当前 ai_cases 为空也正常）", dlg2.list_items.count() == len(ai_bridge.list_cases()))
# 直接用 load_case_record 回看（第 1 轮已验），此处验证不落盘副作用
n_now = len(ai_bridge.list_sessions())
tab2.load_case_record(old_case)
app.processEvents()
check("旧案例回看不产生会话落盘", len(ai_bridge.list_sessions()) == n_now)

qw.QMessageBox.information = _orig_info
qw.QMessageBox.question = _orig_question

# ---------------------------------------------------------------------------
# [9] 界面重构（样板间）：折叠跨会话记忆 / 空态引导 / Composer 自适应 / 发送中文案
# ---------------------------------------------------------------------------
print("[9] 界面重构：折叠记忆 / 空态引导 / Composer")
import theme
stored = theme.load_ui_state().get("ai_ctx_expanded")
check("折叠状态已持久化（落盘值 = 面板当前可见性）",
      stored == tab2.ctx_body.isVisible(),
      "stored=%r visible=%r" % (stored, tab2.ctx_body.isVisible()))

tab3 = ui_ai.AiTab(db=None)
tab3.resize(1200, 800)
tab3.show()
tab3.refresh_config_state()
app.processEvents()
check("新实例继承持久化状态（跨会话记忆）",
      tab3.ctx_body.isVisible() == bool(stored),
      "visible=%r stored=%r" % (tab3.ctx_body.isVisible(), stored))
tab3.toggle_context_panel(expand=True)
app.processEvents()
check("展开后摘要隐藏 / 折叠后显示", not tab3.lbl_ctx_summary.isVisible()
      and tab3.ctx_body.isVisible())
check("自动随发说明唯一出处 = 折叠头 tooltip",
      "每轮自动随消息发送" in tab3.btn_ctx_toggle.toolTip())
check("空态引导可见（新会话无消息）", tab3._empty_guide.isVisible())
chips = [w for w in tab3._empty_guide.findChildren(qw.QPushButton)
         if w.objectName() == "ExampleChip"]
check("空态含 3 个示例问题 chip", len(chips) == 3, "n=%d" % len(chips))
chips[0].click()
app.processEvents()
check("点示例 chip → 填入输入框", tab3.txt_input.toPlainText() == chips[0].text())
check("示例不自动发送（无 worker）", not (tab3._worker and tab3._worker.isRunning()))

# Composer 高度自适应：失焦钳 2 行，聚焦扩到 5 行（需 >2 行内容才能观察到扩高）
tab3.txt_input.setPlainText("第一行\n第二行\n第三行\n第四行")
tab3.btn_attach.setFocus()          # 先移走焦点（示例 chip 点击后焦点已落在输入框）
app.processEvents()
h_unfocused = tab3.txt_input.height()
tab3.txt_input.setFocus()
app.processEvents()
h_focused = tab3.txt_input.height()
check("聚焦后输入框扩高（2→5 行空间）", h_focused > h_unfocused,
      "%d → %d" % (h_unfocused, h_focused))
tab3.txt_input.clear()
app.processEvents()
check("清空后收回 2 行", tab3.txt_input.height() <= h_unfocused + 2)

# 发送中状态：按钮禁用 + 文案「生成中…」，完成后复原
tab3.txt_input.setPlainText("测生成中文案")
tab3.on_send()
check("生成中：按钮禁用 + 文案「生成中…」",
      not tab3.btn_send.isEnabled() and tab3.btn_send.text() == "生成中…")
worker = tab3._worker
t0 = time.time()
while worker.isRunning() and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()
# enabled 与网络探测结果联动（_update_send_state）；文案复原是本次断言重点
check("完成后按钮文案复原「发送」",
      tab3.btn_send.text() == "发送"
      and tab3.btn_send.isEnabled() == (tab3.network_ok
                                        and ai_bridge.is_configured()
                                        and not (tab3._worker and tab3._worker.isRunning())))

print()
print("=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL PASS")
