# -*- coding: utf-8 -*-
"""QD_MINIMAL_BODY 请求体瘦身 + usage 观测（Wave-3 task-10）。

    python tests/_test_prompt_budget.py

钉住 5 组契约：
  [1] baseprompt.json 骨架不被删键（23 键 / messages/tools 类型）
  [2] QD_MINIMAL_BODY=0 保留官方 system、=1 不注入；两模式字节差可重放
  [3] 模板 14 个工具永远不上行（无客户端工具时 tools=[]）
  [4] system 空串/空白/多 system 的现状快照（行为变化必须显式改这里）
  [5] usage 行（成功 + 失败）写入 prompt_src/prompt_chars/tools_n/body_bytes

设计依据：docs/plan/01-prompt-kernel.md §1.2 决策矩阵、§1.4 四组合实测、§5.3。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403
declare_suite(__file__, ["qoder_proxy.py", "baseprompt.json"])

P, A = bootstrap()
import tempfile  # noqa: E402

MODEL = "qmodel_38max"
USER = "请把下面这段 Python 改成异步：\n\nfor i in range(3):\n    print(i)\n"
CLIENT_SYS = "You are X."
TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "w",
    "parameters": {"type": "object", "properties": {}}}}

_orig_prompt = P.SYSTEM_PROMPT
_orig_env = os.environ.get("QD_MINIMAL_BODY")


def _mode(value):
    if value is None:
        os.environ.pop("QD_MINIMAL_BODY", None)
    else:
        os.environ["QD_MINIMAL_BODY"] = value


def _build(messages, tools=None):
    payload = {"model": MODEL, "messages": messages}
    if tools is not None:
        payload["tools"] = tools
    meta = {}
    body = P.build_qoder_body(payload, None, MODEL, realm="cn", meta_out=meta)
    blob = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return body, meta, len(blob), len(P.qoder_encode(blob).encode("utf-8"))


def _sysmsgs(body):
    return [m for m in body.get("messages") or []
            if isinstance(m, dict) and m.get("role") == "system"]


def _roles(body):
    return [m.get("role") for m in body.get("messages") or []]


print("[prompt-budget] QD_MINIMAL_BODY 双模式 + 模板工具不上行 + usage 观测")
try:
    # ------------------------------------------------------------------
    # [1] 骨架
    # ------------------------------------------------------------------
    print()
    print("[1] baseprompt.json 骨架（不改文件，只钉结构）")
    with open(P.BASEPROMPT_PATH, encoding="utf-8") as _fh:
        _bp = json.load(_fh)
    check("骨架 23 个顶层键", len(_bp) == 23, len(_bp))
    check("messages 是非空列表且首条是 system",
          isinstance(_bp.get("messages"), list) and bool(_bp["messages"])
          and _bp["messages"][0].get("role") == "system")
    check("tools 是 14 个模板工具",
          isinstance(_bp.get("tools"), list) and len(_bp["tools"]) == 14,
          len(_bp.get("tools") or []))
    check("BASEPROMPT_MINIMAL 键集合与模板一致",
          set(P.BASEPROMPT_MINIMAL) == set(P.BASEPROMPT))
    check("BASEPROMPT_MINIMAL 的 messages/tools 置空且不共享容器",
          P.BASEPROMPT_MINIMAL["messages"] == []
          and P.BASEPROMPT_MINIMAL["tools"] == []
          and P.BASEPROMPT_MINIMAL["messages"] is not P.BASEPROMPT["messages"])

    # ------------------------------------------------------------------
    # [2] 双模式：官方 system 兜底 / 字节差
    # ------------------------------------------------------------------
    print()
    print("[2] 双模式：官方 system 兜底 / 字节差")
    _mode(None)
    _b_def, _m_def, _sz_def, _w_def = _build([{"role": "user", "content": USER}])
    _mode("0")
    _b0, _m0, _sz0, _w0 = _build([{"role": "user", "content": USER}])
    check("env 未设置 == QD_MINIMAL_BODY=0（默认行为不变）",
          _m_def == _m0 and _sz_def == _sz0
          and _roles(_b_def) == _roles(_b0) and _w_def == _w0,
          (_m_def, _m0, _sz_def, _sz0))
    check("mode 0：官方 system 兜底（12,324 字符）",
          len(_sysmsgs(_b0)) == 1
          and len(_sysmsgs(_b0)[0].get("content") or "") == 12324
          and _m0.get("prompt_src") == "template", _m0)
    check("mode 0：prompt_chars/tools_n 记录正确",
          _m0.get("prompt_chars") == 12324 and _m0.get("tools_n") == 0, _m0)
    _mode("1")
    _b1, _m1, _sz1, _w1 = _build([{"role": "user", "content": USER}])
    check("mode 1：无 system 消息", _sysmsgs(_b1) == [], _roles(_b1))
    check("mode 1：prompt_src=none / prompt_chars=0",
          _m1.get("prompt_src") == "none" and _m1.get("prompt_chars") == 0, _m1)
    check("mode 1：会话直接以 user 开头", _roles(_b1) == ["user"], _roles(_b1))
    check("两模式 JSON 字节差 > 12,000", _sz0 - _sz1 > 12000, (_sz0, _sz1))
    check("两模式上行编码差 > 16,000", _w0 - _w1 > 16000, (_w0, _w1))

    # ------------------------------------------------------------------
    # [2b] 客户端 system / --system-prompt 优先级
    # ------------------------------------------------------------------
    print()
    print("[2b] 客户端 system 与 --system-prompt 的优先级（两模式一致）")
    for _mode_val in ("0", "1"):
        _mode(_mode_val)
        _b, _m, _, _ = _build([{"role": "system", "content": CLIENT_SYS},
                               {"role": "user", "content": USER}])
        check("mode %s：客户端 system 原样保留" % _mode_val,
              _sysmsgs(_b)[0].get("content") == CLIENT_SYS
              and _m.get("prompt_src") == "client", _m)
    P.SYSTEM_PROMPT = "You are a neutral assistant."
    for _mode_val in ("0", "1"):
        _mode(_mode_val)
        _b, _m, _, _ = _build([{"role": "user", "content": USER}])
        check("mode %s：--system-prompt 覆盖仍生效" % _mode_val,
              _sysmsgs(_b)[0].get("content") == P.SYSTEM_PROMPT
              and _m.get("prompt_src") == "cli", _m)
    P.SYSTEM_PROMPT = _orig_prompt

    # ------------------------------------------------------------------
    # [3] 模板工具永不上行
    # ------------------------------------------------------------------
    print()
    print("[3] 模板工具永不上行")
    _bp_names = {t.get("function", {}).get("name") for t in _bp["tools"]}
    for _mode_val in ("0", "1"):
        _mode(_mode_val)
        _b, _m, _, _ = _build([{"role": "user", "content": USER}])
        _names = {t.get("function", {}).get("name") for t in _b["tools"]}
        check("mode %s：无客户端工具 -> tools=[]" % _mode_val, _b["tools"] == [])
        check("mode %s：模板 14 个工具一个都不出现" % _mode_val,
              not (_names & _bp_names), sorted(_names))
        check("mode %s：tools_n=0" % _mode_val, _m.get("tools_n") == 0, _m)
        _b2, _m2, _, _ = _build([{"role": "user", "content": USER}], tools=[TOOL])
        check("mode %s：客户端工具原样上行（模板仍不出现）" % _mode_val,
              len(_b2["tools"]) == 1
              and _b2["tools"][0]["function"]["name"] == "get_weather"
              and not ({t["function"]["name"] for t in _b2["tools"]} & _bp_names)
              and _m2.get("tools_n") == 1, _m2)

    # ------------------------------------------------------------------
    # [4] system 空串 / 空白 / 多 system 快照
    # ------------------------------------------------------------------
    print()
    print("[4] system 空串/空白/多 system 快照")
    _mode("0")
    _b, _m, _, _ = _build([{"role": "system", "content": ""},
                           {"role": "user", "content": USER}])
    check("mode 0：system 空串 -> 官方模板兜底（现状，可能是坑）",
          len(_sysmsgs(_b)[0].get("content") or "") == 12324
          and _m.get("prompt_src") == "template", _m)
    _b, _m, _, _ = _build([{"role": "system", "content": " "},
                           {"role": "user", "content": USER}])
    check("mode 0：system 空白 -> 空白 system 原样保留",
          _sysmsgs(_b)[0].get("content") == " "
          and _m.get("prompt_src") == "client", _m)
    _b, _m, _, _ = _build([{"role": "system", "content": "S1"},
                           {"role": "system", "content": "S2"},
                           {"role": "user", "content": USER}])
    check("mode 0：多 system 只取第一条（现状）",
          _sysmsgs(_b)[0].get("content") == "S1"
          and len(_sysmsgs(_b)) == 1)
    _mode("1")
    _b, _m, _, _ = _build([{"role": "system", "content": ""},
                           {"role": "user", "content": USER}])
    check("mode 1：system 空串 -> 不注入 system（与 mode 0 不同）",
          _sysmsgs(_b) == [] and _m.get("prompt_src") == "none", _m)
    _b, _m, _, _ = _build([{"role": "system", "content": " "},
                           {"role": "user", "content": USER}])
    check("mode 1：system 空白仍原样保留",
          _sysmsgs(_b)[0].get("content") == " "
          and _m.get("prompt_src") == "client", _m)
    _b, _m, _, _ = _build([{"role": "system", "content": "S1"},
                           {"role": "system", "content": "S2"},
                           {"role": "user", "content": USER}])
    check("mode 1：多 system 仍只取第一条",
          _sysmsgs(_b)[0].get("content") == "S1"
          and len(_sysmsgs(_b)) == 1)

    # ------------------------------------------------------------------
    # [5] usage 行观测字段
    # ------------------------------------------------------------------
    print()
    print("[5] usage 行观测字段（成功 + 失败）")
    _tmp = tempfile.mkdtemp(prefix="qd-promptbudget-")
    _orig_usage = (P.USAGE_DIR, P.USAGE_LOG, P.USAGE_SUMMARY)
    P.USAGE_DIR = _tmp
    P.USAGE_LOG = os.path.join(_tmp, "usage.jsonl")
    P.USAGE_SUMMARY = os.path.join(_tmp, "usage-summary.json")

    def _last_row():
        for _path in reversed(P._usage_log_files()):
            try:
                with open(_path, encoding="utf-8") as _fh:
                    _rows = [json.loads(x) for x in _fh if x.strip()]
            except FileNotFoundError:
                continue
            if _rows:
                return _rows[-1]
        raise FileNotFoundError(P.USAGE_LOG)

    try:
        _meta = {"prompt_src": "none", "prompt_chars": 0,
                 "tools_n": 0, "body_bytes": 2264}
        P.record_usage("m-meta", {"prompt_tokens": 10,
                                  "completion_tokens": 2, "total_tokens": 12},
                       stream=False, body_meta=_meta)
        _row = _last_row()
        check("record_usage：4 个观测字段全部落盘",
              all(_row.get(k) == _meta[k] for k in P.BODY_META_KEYS), _row)
        P.record_usage("m-legacy", {"prompt_tokens": 1,
                                    "completion_tokens": 1, "total_tokens": 2},
                       stream=False)
        _row2 = _last_row()
        check("record_usage：不传 body_meta 时旧行格式不变",
              not (set(P.BODY_META_KEYS) & set(_row2)), sorted(_row2))
        P.record_error("m-err-meta", 429, "limited", body_meta=_meta)
        _erow = _last_row()
        check("record_error：4 个观测字段全部落盘",
              all(_erow.get(k) == _meta[k] for k in P.BODY_META_KEYS), _erow)
        P.record_error("m-err-legacy", 502, "boom")
        _erow2 = _last_row()
        check("record_error：不传 body_meta 时键集合仍是旧的 7 个",
              set(_erow2) == {"at", "iso", "model", "error", "status",
                              "message", "elapsed_ms"}, sorted(_erow2))
    finally:
        P.USAGE_DIR, P.USAGE_LOG, P.USAGE_SUMMARY = _orig_usage
        shutil.rmtree(_tmp, ignore_errors=True)

    # 接线：open_upstream 把 meta 写进 usage_ctx，调用点交给 record_*
    _src = open(os.path.join(ROOT, "qoder_proxy.py"), encoding="utf-8").read()
    check("open_upstream 把 body_meta 写进 usage_ctx",
          'usage_ctx["body_meta"] = body_meta' in _src)
    check("成功/失败调用点都透传 body_meta（30 处）",
          _src.count('body_meta=usage_ctx.get("body_meta")') == 30,
          _src.count('body_meta=usage_ctx.get("body_meta")'))
finally:
    P.SYSTEM_PROMPT = _orig_prompt
    _mode(_orig_env)

print()
finish()
