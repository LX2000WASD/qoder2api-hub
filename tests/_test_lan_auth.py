# -*- coding: utf-8 -*-
"""Docker --lan 鉴权加固套件（docs/plan/07-docker-auth.md H1-H8）。

守护：
  * qoder_proxy.py 的启动鉴权块：Key 四级优先（H2）、启动日志脱敏（H3）、
    面板默认密码门控（H4）、settings.json 权限（H5）、观测面（H6）、
    启动自检四行（H8）；
  * docker-compose.yml / Dockerfile 的编排默认值（H1/H4/H7 的"不跟随上游"）；
  * qoder_updater.REPO 与更新端点 403（H7）。

    python tests/_test_lan_auth.py

不依赖 docker：H1/H4/H7 的编排检查直接解析文件；H2/H3/H5/H8 走纯函数；
H4 另起一个子进程验证「默认密码 + 0.0.0.0 + 门控」真的拒绝启动；H6 起真实
Handler 断言观测面不含凭证 / 账号 uid。
"""
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="qd-lan-auth-")
os.environ["ACCOUNTS_DIR"] = _TMP
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(_TMP, "usage")
for _flag in ("QD_LOG_SENSITIVE", "QD_REQUIRE_PANEL_PASSWORD",
              "QD_UPDATE_DISABLE", "QD_NATIVE_IDENTITY"):
    os.environ.pop(_flag, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *        # noqa: E402,F401,F403

declare_suite(__file__, ["qoder_proxy.py", "docker-compose.yml", "Dockerfile"])
P, A = bootstrap()
import qoder_settings as S       # noqa: E402

KEY = "qd-" + "A" * 32 + "wxyz"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


compose = read("docker-compose.yml")
dockerfile = read("Dockerfile")

# --------------------------------------------------------------------- H1
print("\n[H1] 端口只发布到宿主机回环（不跟随上游 8790:8790）")
check("compose 端口是 127.0.0.1:8790:8790",
      re.search(r'^\s*-\s*"127\.0\.0\.1:8790:8790"\s*$', compose,
                re.M) is not None)
check("compose 没有裸 8790:8790 发布",
      re.search(r'^\s*-\s*"8790:8790"\s*$', compose, re.M) is None)
check("镜像 CMD 仍是 --lan",
      'CMD ["python", "qoder_proxy.py", "--lan"]' in dockerfile)
check("镜像内监听 0.0.0.0（--lan 语义不变）", "HOST=0.0.0.0" in dockerfile)

# --------------------------------------------------------------------- H2
print("\n[H2] Key 四级优先：--api-key > env > panel > launcher")
for label, kwargs, want in [
        ("cli 最高", dict(cli_key="k-cli", env_key="k-env",
                          panel_key="k-panel", launcher_key="k-lan"),
         ("k-cli", "cli")),
        ("env 次之", dict(env_key="k-env", panel_key="k-panel",
                          launcher_key="k-lan"), ("k-env", "env")),
        ("panel 第三", dict(panel_key="k-panel", launcher_key="k-lan"),
         ("k-panel", "panel")),
        ("launcher 兜底", dict(launcher_key="k-lan"), ("k-lan", "launcher")),
        ("全空 -> none", dict(), (None, "none"))]:
    got = P.resolve_api_key(**kwargs)
    check(label, got == want, got)
check("面板显式清空 Key 不被 launcher 顶掉",
      P.resolve_api_key(panel_key="", launcher_key="k-lan") == ("", "panel"))
check("空 cli/env 视为未设置",
      P.resolve_api_key(cli_key="", env_key="",
                        launcher_key="k-lan") == ("k-lan", "launcher"))
check("KEY_SOURCES 四种来源 + none",
      set(P.KEY_SOURCES) == {"cli", "env", "panel", "launcher", "none"},
      P.KEY_SOURCES)

# --------------------------------------------------------------------- H3
print("\n[H3] 启动日志脱敏（QD_LOG_SENSITIVE 默认 0）")
masked = P.mask_secret(KEY)
check("掩码保留前缀 3", masked.startswith("qd-"), masked)
check("掩码保留后 4 位", masked.endswith("wxyz"), masked)
check("中间全是 *", set(masked[3:-4]) == {"*"}, masked)
check("掩码不含原文主体", "A" * 8 not in masked, masked)
check("短 Key 整串打星（不留原文前缀）", P.mask_secret("qd-1234") == "*" * 16)
check("空 Key -> 空串", P.mask_secret("") == "")
banner = P.lan_banner_lines(["10.0.0.2"], 8790, KEY, "launcher", True,
                            sensitive=False)
check("横幅不含完整 Key", not any(KEY in line for line in banner))
check("横幅不含 ?key= URL", not any("?key=" in line for line in banner))
check("横幅带掩码", any(masked in line for line in banner))
check("横幅说明 launcher 新生成",
      any("newly generated" in line for line in banner))
panel_banner = P.lan_banner_lines(["10.0.0.2"], 8790, KEY, "panel", False)
check("面板 Key 的横幅不写 'newly generated'（06 §3.2a 的误判陷阱）",
      not any("newly generated" in line for line in panel_banner))
check("面板 Key 的横幅写明 panel 来源",
      any("panel settings" in line for line in panel_banner))
os.environ["QD_LOG_SENSITIVE"] = "1"
try:
    check("QD_LOG_SENSITIVE=1 时 mask_secret 原样（兼容上游）",
          P.mask_secret(KEY) == KEY)
    raw_banner = P.lan_banner_lines(["10.0.0.2"], 8790, KEY)
    check("QD_LOG_SENSITIVE=1 时横幅恢复完整 Key + ?key=",
          any(KEY in line for line in raw_banner)
          and any("?key=" in line for line in raw_banner))
finally:
    os.environ.pop("QD_LOG_SENSITIVE", None)
check("未设该变量时仍是脱敏（默认 0）", P.mask_secret(KEY) != KEY)

# --------------------------------------------------------------------- H4
print("\n[H4] 面板默认密码门控（QD_REQUIRE_PANEL_PASSWORD 默认 0）")
check("开 + 0.0.0.0 + 默认密码 -> 拒绝",
      bool(P.panel_password_gate_error("0.0.0.0", True, required=True)))
check("开 + 0.0.0.0 + 已改密码 -> 放行",
      P.panel_password_gate_error("0.0.0.0", False, required=True) is None)
check("开 + 回环绑定 -> 放行",
      P.panel_password_gate_error("127.0.0.1", True, required=True) is None)
check("关 -> 放行（与上游一致）",
      P.panel_password_gate_error("0.0.0.0", True, required=False) is None)
check("未设该变量时默认放行（向后兼容）",
      P.panel_password_gate_error("0.0.0.0", True) is None)
os.environ["QD_REQUIRE_PANEL_PASSWORD"] = "1"
try:
    check("默认值从环境变量读",
          P.panel_password_gate_error("0.0.0.0", True) is not None)
finally:
    os.environ.pop("QD_REQUIRE_PANEL_PASSWORD", None)

print("\n[H4] 真实启动拒绝（子进程，不依赖 docker）")
_gate_env = dict(os.environ)
_gate_env["QD_REQUIRE_PANEL_PASSWORD"] = "1"
_gate_env["QD_NATIVE_IDENTITY"] = "0"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
           "https_proxy", "all_proxy"):
    _gate_env.pop(_k, None)
_gate_port = free_port()
try:
    _proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "qoder_proxy.py"), "--lan",
         "--port", str(_gate_port), "--accounts-dir", _TMP],
        cwd=ROOT, env=_gate_env, capture_output=True, text=True, timeout=90)
    _out = (_proc.stdout or "") + (_proc.stderr or "")
    _rc, _err = _proc.returncode, None
except subprocess.TimeoutExpired as _exc:
    _rc, _out, _err = None, ((_exc.stdout or b"").decode("utf-8", "replace")
                             if isinstance(_exc.stdout, bytes)
                             else (_exc.stdout or "")), "timeout"
check("退出码非 0", _rc not in (0, None), (_rc, _err))
check("报错点名 QD_REQUIRE_PANEL_PASSWORD", "QD_REQUIRE_PANEL_PASSWORD" in _out)
check("给出修复路径（--panel-password）", "--panel-password" in _out,
      _out[-400:])
check("没有进入监听阶段", "listening  :" not in _out)

# --------------------------------------------------------------------- H5
print("\n[H5] settings.json 权限 0600（内含 Key 明文）")
if os.name == "nt":
    skip("settings.json 权限位", "Windows 无 POSIX 权限位")
else:
    _dir = tempfile.mkdtemp(prefix="qd-mode-")
    _path = os.path.join(_dir, "settings.json")
    with open(_path, "w", encoding="utf-8") as fh:
        fh.write("{}")
    os.chmod(_path, 0o600)
    check("0600 读回 600", P.settings_file_mode(_dir) == 0o600)
    os.chmod(_path, 0o644)
    check("0644 能读出来（自检可发现外部改坏）",
          P.settings_file_mode(_dir) == 0o644)
    check("文件缺失 -> None",
          P.settings_file_mode(tempfile.mkdtemp(prefix="qd-mode-none-"))
          is None)
    _dir2 = tempfile.mkdtemp(prefix="qd-mode-save-")
    S.save(_dir2, {"api_key": "x"})
    check("save() 落盘即 0600", P.settings_file_mode(_dir2) == 0o600)

# --------------------------------------------------------------------- H6
print("\n[H6] 观测面：/metrics 与 /health 不含凭证 / 账号 uid")
KEY2 = "qd-" + "B" * 28 + "9xyz"
P.API_KEY = KEY2
P.API_KEY_FILE_SET = False
_acct = A.Account({"uid": "uid-secret-abc", "realm": "cn",
                   "accessToken": "dt-x", "domain": "qoder.com"})
_acct.nickname = "t"


class _Pool(object):
    def __init__(self, accounts):
        self.accounts = accounts

    def representative(self):
        return self.accounts[0] if self.accounts else None

    def count_ready(self):
        return len(self.accounts)

    def pick(self, *args, **kwargs):
        return self.representative()

    def apply_settings(self, **kwargs):
        pass


P.POOL = _Pool([_acct])
_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % _srv.server_address[1]


def fetch(path, headers=None, post=False):
    req = urllib.request.Request(
        BASE + path, headers=headers or {}, method="POST" if post else "GET",
        data=b"" if post else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


for _probe in ("/ping", "/healthz", "/livez", "/readyz"):
    _status, _text = fetch(_probe)
    check("探针 %s 免鉴权 200" % _probe, _status == 200, _status)
_status, _health = fetch("/health")
check("/health 200（免鉴权）", _status == 200, _status)
check("/health 不含 API Key", KEY2 not in _health)
check("/health 未鉴权时不带账号 uid",
      '"uid"' not in _health and "uid-secret-abc" not in _health,
      _health[:200])
check("/health 仍报告 api_key_required（运维判据）",
      "api_key_required" in _health)
_status, _health_keyed = fetch("/health",
                               {"Authorization": "Bearer " + KEY2})
check("/health 带 Key 才给 uid",
      '"uid"' in _health_keyed and "uid-secret-abc" in _health_keyed,
      _health_keyed[:200])
_status, _metrics = fetch("/metrics")
check("/metrics 200（免鉴权，采集器直连）", _status == 200, _status)
check("/metrics 不含完整 Key", KEY2 not in _metrics)
check("/metrics 不含 qd- 前缀凭据", "qd-" not in _metrics)
check("/metrics 不含账号 uid", "uid" not in _metrics.lower())
check("/metrics 是 Prometheus 文本", "qoder_accounts_total" in _metrics)

# --------------------------------------------------------------------- H7
print("\n[H7] 一键更新默认禁用（fork 仓库 + 镜像/编排开关 + 403）")
check("qoder_updater.REPO 指向 fork",
      P.qoder_updater.REPO == "LX2000WASD/qoder2api-hub",
      P.qoder_updater.REPO)
check("Dockerfile ENV QD_UPDATE_DISABLE=1",
      "QD_UPDATE_DISABLE=1" in dockerfile)
check("compose 保留 QD_UPDATE_DISABLE=1", "QD_UPDATE_DISABLE=1" in compose)
check("compose 保留 fork 的 GHCR 镜像行",
      "ghcr.io/lx2000wasd/qoder2api-hub" in compose)
_token = P.PANEL.create()
_saved_flag = P.UPDATE_DISABLED
P.UPDATE_DISABLED = True
try:
    _status, _text = fetch("/update/apply", {"X-Panel-Token": _token},
                           post=True)
finally:
    P.UPDATE_DISABLED = _saved_flag
check("面板会话 POST /update/apply -> 403", _status == 403, _status)
check("403 文案 update is disabled", "update is disabled" in _text,
      _text[:160])

# --------------------------------------------------------------------- H8
print("\n[H8] 启动自检四行（不含敏感值）")
_lines = P.startup_selfcheck_lines("0.0.0.0", 8790, "env")
check("恰好四行", len(_lines) == 4, _lines)
check("auth 行格式", _lines[0].startswith("auth       :")
      and "key_source=env" in _lines[0]
      and "bind=0.0.0.0:8790" in _lines[0], _lines[0])
check("security 行格式", _lines[1].startswith("security   :")
      and "settings.json mode=" in _lines[1]
      and "updater=" in _lines[1]
      and "sensitive_log=0" in _lines[1], _lines[1])
check("ingress 行格式", _lines[2].startswith("ingress    :")
      and "published=" in _lines[2]
      and "tls=" in _lines[2], _lines[2])
check("identity 行格式", _lines[3].startswith("identity   :")
      and "device_id=" in _lines[3]
      and "legacy=" in _lines[3]
      and "umid=" in _lines[3], _lines[3])
check("四行不含任何 Key",
      all(KEY2 not in line and KEY not in line for line in _lines))
_saved_accounts = P.ACCOUNTS_DIR
_mode_dir = tempfile.mkdtemp(prefix="qd-selfcheck-")
with open(os.path.join(_mode_dir, "settings.json"), "w", encoding="utf-8") as fh:
    fh.write("{}")
if os.name != "nt":
    os.chmod(os.path.join(_mode_dir, "settings.json"), 0o644)
P.ACCOUNTS_DIR = _mode_dir
try:
    _secured = P.startup_selfcheck_lines("127.0.0.1", 8791, "none")
finally:
    P.ACCOUNTS_DIR = _saved_accounts
if os.name == "nt":
    skip("自检行反映 settings.json 权限", "Windows 无 POSIX 权限位")
else:
    check("自检行反映 0644（能发现被改坏的权限）",
          "mode=644" in _secured[1], _secured[1])
check("回环绑定的 ingress 行不声称 nginx",
      "tls=none" in _secured[2] and "loopback" in _secured[2], _secured[2])

_srv.shutdown()
finish()
