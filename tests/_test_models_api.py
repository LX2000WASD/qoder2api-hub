"""对外模型清单与设置回显套件（2026 模型闸门加固）。

    python tests/_test_models_api.py

守护三件事（对应 docs/plan/03-model-admin.md 方案 B 的 1/3 两条）：
  M1 GET /v1/models 默认只列「本网关真的能用」的模型：
     官方 enable=false / 全局 banned_models / 该 Key 白名单之外 一律不列；
  M2 ?all=1 返回全量（面板模型库用），响应带 filtered 标志；
  M3 GET /settings 回显 api_keys[].models / note 与 banned_models，
     POST /settings/save 的语义是「缺字段/null = 保留旧值，显式 [] 才清空」
     —— 这是「面板保存一次就丢白名单」那个数据丢失 bug 的回归钉。
  M4 merge_realms 视图同样过滤。
  M5 面板保存语义（保留 / null / [] / 字符串切分）。
  M6 面板守卫：源码锚点 + node 实跑 saveApiKeys/saveSingleKey，抓真实请求体
     （避免只改后端不改前端、或改坏函数体仍能骗过源码 grep 的假修复）。

装置照 _test_auth_matrix：真 ThreadingHTTPServer + 项目 Handler，但把
fetch_models / fetch_models_merged 换成固定 fixture（不发任何网络请求）。
"""
import http.client
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ACC = tempfile.mkdtemp(prefix="qd-models-api-")
os.environ["ACCOUNTS_DIR"] = ACC
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(ACC, "usage")

import qoder_settings as S
import qoder_proxy as P

# ACCOUNTS_DIR 在 qoder_proxy 里是 import 期常量（命令行才覆盖），
# 所以必须显式改模块属性 —— 只设环境变量会被忽略（_test_model_gates 同做法）。
P.ACCOUNTS_DIR = ACC

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import declare_suite

declare_suite(__file__, ["qoder_proxy.py", "qoder_settings.py", "dashboard.html"])

PASS = FAIL = 0


def check(label, ok, extra=None):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] %s  %r" % (label, extra))


# ---------------------------------------------------------------- fixtures
# 三条固定模型：两条官方可用（其中一条会被封禁）、一条官方 enable=false。
FIXTURE = [
    ("qmodel_38max", {"key": "qmodel_38max", "display_name": "Qwen3.8-Max",
                      "enable": True, "is_vl": True}),
    ("gmodel", {"key": "gmodel", "display_name": "GLM-5.3",
                "enable": True, "is_vl": True}),
    ("qmodel_latest", {"key": "qmodel_latest", "display_name": "Qwen3.7-Max",
                       "enable": False, "is_vl": True}),
]
_orig_fetch = P.fetch_models
_orig_merged = P.fetch_models_merged
P.fetch_models = lambda realm=None: [tuple(row) for row in FIXTURE]
P.fetch_models_merged = lambda: [(mid, meta, "cn") for mid, meta in FIXTURE]

S.set_api_keys(ACC, [
    {"id": "limited", "name": "受限Key", "key": "limited-key-1234",
     "models": ["qmodel_38max"], "note": "只开 Qwen3.8-Max"},
    {"id": "plain", "name": "普通Key", "key": "plain-key-1234"},
])
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


def request(method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    hdrs = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        try:
            return int(resp.status), json.loads(raw)
        except ValueError:
            return int(resp.status), raw
    finally:
        conn.close()


PLAIN = {"Authorization": "Bearer plain-key-1234"}
LIMITED = {"Authorization": "Bearer limited-key-1234"}
PANEL = {"X-Panel-Token": P.PANEL.create()}


# M6e 用的 JS 探针：从 dashboard.html 抽出 saveApiKeys 真函数跑一遍，断言
# 请求体里带 models（旧版这里是源码 grep，改坏函数体也能骗过 grep）。
PAYLOAD_PROBE_JS = """const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const from = html.indexOf('async function saveApiKeys');
const to = html.indexOf('async function saveBannedModels', from);
if (from < 0 || to < 0) { console.log('ANCHOR-MISSING'); process.exit(2); }
const src = html.slice(from, to);
const posted = [];
const rows = [{ id: 'k1', name: 'n', realm: '', enabled: true, key: '', masked: 'abcd******wxyz', note: 'x', models: ['qwen*'] }];
const fn = new Function('API_KEY_ROWS', 'postJSON', 'toast', 'loadSettings',
  src + '; return saveApiKeys;')(rows,
  async (u, b) => { posted.push([u, b]); return {}; },
  () => {}, () => {});
Promise.resolve(fn()).then(() => {
  const body = posted[0] && posted[0][1];
  const ok = posted.length === 1 && posted[0][0] === '/settings/save'
    && body && Array.isArray(body.api_keys)
    && JSON.stringify(body.api_keys[0].models) === JSON.stringify(['qwen*'])
    && body.api_keys[0].note === 'x';
  console.log(ok ? 'PAYLOAD-OK' : 'PAYLOAD-BAD ' + JSON.stringify(posted));
  process.exit(ok ? 0 : 1);
});"""


# M6f 用的 JS 探针：Key 编辑卡的 saveSingleKey 必须把模型白名单文本框
# 解析成 pattern 列表再交给 saveApiKeys（镜像 saveApiKeys 的 payload 探针）。
SINGLE_PROBE_JS = """const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const i = html.indexOf('function keyModelsText');
const j = html.indexOf('async function toggleKeyRow', i);
if (i < 0 || j <= i) { console.log('ANCHOR-MISSING'); process.exit(2); }
const src = html.slice(i, j);
let sent = null;
const row = { id: 'k2', name: 'n2', realm: '', enabled: true, key: '', masked: 'abcd******wxyz', note: '', models: [], _editing: true };
const els = { editKeyName_0: { value: 'n2' }, editKeyRealm_0: { value: '' },
  editKeyValue_0: { value: '' }, editKeyNote_0: { value: '' },
  editKeyModels_0: { value: 'Qwen3.8-Max, glm-5.3' } };
const fn = new Function('document', 'API_KEY_ROWS', 'saveApiKeys', 'toast',
  src + '; return saveSingleKey;')({ getElementById: id => els[id] || null },
  [row], async () => { sent = row.models; }, () => {});
Promise.resolve(fn(0)).then(() => {
  const ok = JSON.stringify(sent) === JSON.stringify(['qwen3.8-max', 'glm-5.3']);
  console.log(ok ? 'SINGLE-OK' : 'SINGLE-BAD ' + JSON.stringify(sent));
  process.exit(ok ? 0 : 1);
});"""


def ids_of(path, headers):
    status, body = request("GET", path, headers=headers)
    if not isinstance(body, dict):
        return status, None, body
    return status, [m.get("id") for m in body.get("data") or []], body


try:
    print("[models-api] M1 /v1/models 默认过滤")
    # 基线：官方 enable=false 的 Qwen3.7-Max 默认不列（此前的实现会列出）
    st, ids, body = ids_of("/v1/models?realm=cn", PLAIN)
    check("M1a 不受限 Key：官方 enable=false 不列出（Qwen3.7-Max 缺席）",
          st == 200 and ids == ["Qwen3.8-Max", "GLM-5.3"], (st, ids))
    check("M1b 默认响应带 filtered=true（客户端可据此判断是否过滤过）",
          isinstance(body, dict) and body.get("filtered") is True, body)

    # 每 Key 白名单：只列出允许的模型
    st, ids, _b = ids_of("/v1/models?realm=cn", LIMITED)
    check("M1c 白名单 Key：只列出白名单内的模型",
          st == 200 and ids == ["Qwen3.8-Max"], (st, ids))

    # 白名单按归一后的写法匹配：配上游 key，返回官方模型名
    S.set_api_keys(ACC, [
        {"id": "limited", "name": "受限Key", "key": "limited-key-1234",
         "models": ["qmodel_38max"], "note": "只开 Qwen3.8-Max"},
        {"id": "plain", "name": "普通Key", "key": "plain-key-1234"},
    ])
    st, ids, _b = ids_of("/v1/models?realm=cn", LIMITED)
    check("M1d 白名单写上游 key -> 对外仍按官方模型名列出（归一后匹配）",
          st == 200 and ids == ["Qwen3.8-Max"], (st, ids))

    # 全局封禁：display_name 形式的 pattern 也要能封住 key 形式的条目
    S.set_banned_models(ACC, "GLM-5.3")
    st, ids, _b = ids_of("/v1/models?realm=cn", PLAIN)
    check("M1e 全局封禁按官方模型名命中（GLM-5.3 不列出）",
          st == 200 and ids == ["Qwen3.8-Max"], (st, ids))

    print()
    print("[models-api] M2 ?all=1 显示全部")
    st, ids, body = ids_of("/v1/models?all=1&realm=cn", LIMITED)
    check("M2a ?all=1：绕过白名单/封禁/官方 enable，返回全量 3 条",
          st == 200 and sorted(ids or []) ==
          sorted(["Qwen3.8-Max", "GLM-5.3", "Qwen3.7-Max"]), (st, ids))
    check("M2b ?all=1：filtered=false（面板据此确认拿到的是全量）",
          isinstance(body, dict) and body.get("filtered") is False, body)

    print()
    print("[models-api] M3 merge_realms 同样过滤")
    st, ids, body = ids_of("/v1/models?merge_realms=1", PLAIN)
    check("M3a merge_realms 默认视图：仍过滤官方 enable=false 与封禁",
          st == 200 and ids == ["Qwen3.8-Max"], (st, ids))
    st, ids, body = ids_of("/v1/models?merge_realms=1&all=1", PLAIN)
    check("M3b merge_realms + all=1：全量",
          st == 200 and len(ids or []) == 3, (st, ids))

    print()
    print("[models-api] M4 GET /settings 回显 models / note / banned_models")
    st, view = request("GET", "/settings", headers=PANEL)
    entries = {}
    if isinstance(view, dict):
        entries = {e.get("id"): e for e in view.get("api_keys") or []}
    check("M4a api_keys[].models 回显（受限 Key 有值、普通 Key 为空列表）",
          st == 200 and entries.get("limited", {}).get("models") == ["qmodel_38max"]
          and entries.get("plain", {}).get("models") == [], (st, entries))
    check("M4b api_keys[].note 回显",
          entries.get("limited", {}).get("note") == "只开 Qwen3.8-Max",
          entries.get("limited"))
    check("M4c 顶层 banned_models 回显（清洗后的小写列表）",
          isinstance(view, dict) and view.get("banned_models") == ["glm-5.3"],
          (view or {}).get("banned_models") if isinstance(view, dict) else view)

    print()
    print("[models-api] M5 /settings/save：缺字段/null = 保留，显式 [] = 清空")
    keep_payload = {"api_keys": [
        {"id": "limited", "name": "受限Key", "realm": "", "enabled": True,
         "key": ""},
        {"id": "plain", "name": "普通Key", "realm": "", "enabled": True,
         "key": ""},
    ]}
    st, _reply = request("POST", "/settings/save", keep_payload, headers=PANEL)
    stored = {e.get("id"): e for e in S.api_keys(ACC)}
    check("M5a 旧面板（payload 不含 models）保存 -> 白名单保留（数据丢失回归钉）",
          st == 200 and stored.get("limited", {}).get("models") == ["qmodel_38max"],
          (st, stored.get("limited")))
    check("M5b payload 不含 note -> 备注保留",
          stored.get("limited", {}).get("note") == "只开 Qwen3.8-Max",
          stored.get("limited"))

    null_payload = {"api_keys": [
        {"id": "limited", "name": "受限Key", "realm": "", "enabled": True,
         "key": "", "models": None, "note": None},
        {"id": "plain", "name": "普通Key", "realm": "", "enabled": True,
         "key": ""},
    ]}
    request("POST", "/settings/save", null_payload, headers=PANEL)
    stored = {e.get("id"): e for e in S.api_keys(ACC)}
    check("M5c 显式 null 也算「保留」（与任务约定一致）",
          stored.get("limited", {}).get("models") == ["qmodel_38max"],
          stored.get("limited"))

    clear_payload = {"api_keys": [
        {"id": "limited", "name": "受限Key", "realm": "", "enabled": True,
         "key": "", "models": []},
        {"id": "plain", "name": "普通Key", "realm": "", "enabled": True,
         "key": ""},
    ]}
    request("POST", "/settings/save", clear_payload, headers=PANEL)
    stored = {e.get("id"): e for e in S.api_keys(ACC)}
    check("M5d 显式 [] -> 清空白名单（唯一能清空的写法）",
          stored.get("limited", {}).get("models") == [],
          stored.get("limited"))

    set_payload = {"api_keys": [
        {"id": "limited", "name": "受限Key", "realm": "", "enabled": True,
         "key": "", "models": "Qwen3.8-Max, glm-5.3"},
        {"id": "plain", "name": "普通Key", "realm": "", "enabled": True,
         "key": ""},
    ]}
    request("POST", "/settings/save", set_payload, headers=PANEL)
    stored = {e.get("id"): e for e in S.api_keys(ACC)}
    check("M5e 字符串输入按逗号切分 + 小写化后落盘",
          stored.get("limited", {}).get("models") == ["qwen3.8-max", "glm-5.3"],
          stored.get("limited"))

    print()
    print("[models-api] M6 dashboard.html 源码守卫（防「只改后端」假修复）")
    dash = open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8").read()
    save_fn = dash.split("async function saveApiKeys", 1)[-1].split("}", 1)[0]
    check("M6a saveApiKeys payload 含 models 字段",
          "models: row.models" in save_fn, save_fn[:300])
    check("M6b Key 编辑卡有模型白名单输入（editKeyModels_）",
          "editKeyModels_" in dash, None)
    check("M6c 模型库请求带 all=1（否则面板看不到未开通模型）",
          "/v1/models?all=1&realm=" in dash, None)
    check("M6d 设置页有全局封禁输入 + 保存入口",
          "setBannedModels" in dash and "saveBannedModels" in dash, None)

    # M6e：不是源码 grep —— 用 node 把 saveApiKeys 真跑一遍，抓真实请求体。
    # 这是「面板保存一次就丢白名单」的端到端回归：payload 里必须有 models。
    node = shutil.which("node")
    if not node:
        print("  [SKIP] M6e saveApiKeys 实际 payload（环境无 node）")
    else:
        import subprocess
        probe = os.path.join(ACC, "_payload_probe.js")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(PAYLOAD_PROBE_JS)
        proc = subprocess.run(
            [node, probe, os.path.join(ROOT, "dashboard.html")],
            capture_output=True, text=True, timeout=60)
        check("M6e saveApiKeys 实际请求体含 models（node 跑真函数）",
              "PAYLOAD-OK" in (proc.stdout or ""),
              ((proc.stdout or "")[:200], (proc.stderr or "")[:200]))
        probe2 = os.path.join(ACC, "_single_probe.js")
        with open(probe2, "w", encoding="utf-8") as fh:
            fh.write(SINGLE_PROBE_JS)
        proc2 = subprocess.run(
            [node, probe2, os.path.join(ROOT, "dashboard.html")],
            capture_output=True, text=True, timeout=60)
        check("M6f Key 编辑卡输入 -> 切分小写 -> 写回 row.models（node 跑真函数）",
              "SINGLE-OK" in (proc2.stdout or ""),
              ((proc2.stdout or "")[:200], (proc2.stderr or "")[:200]))
finally:
    try:
        srv.shutdown()
        srv.server_close()
    except Exception:
        pass
    P.fetch_models = _orig_fetch
    P.fetch_models_merged = _orig_merged
    shutil.rmtree(ACC, ignore_errors=True)

print("")
print("SUMMARY: TOTAL %d checks, %d passed, %d failed" % (PASS + FAIL, PASS, FAIL))
print("RESULT: %s (exit %d)" % ("RED" if FAIL else "GREEN", 1 if FAIL else 0))
sys.exit(1 if FAIL else 0)