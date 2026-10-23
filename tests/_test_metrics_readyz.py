# -*- coding: utf-8 -*-
"""/metrics 与 /readyz 守卫（本地 f14031a 的观测面 + 就绪探针）。

    python tests/_test_metrics_readyz.py

背景：/metrics 是新增的观测面（Prometheus 文本，采集器零配置直连），
/readyz 与 /ping /livez /healthz 分离——「活着但一个就绪账号都没有」必须
返回 503 让编排层摘流，而不是跟着 liveness 一起 200。断言：

  ① /metrics 返回合法 Prometheus 文本（HELP/TYPE 成对、每行可解析）；
  ② 指标覆盖账号池 / 计数 / 线程 / 活跃连接，计数为非负数；
  ③ /metrics **不含凭证**（API Key / access_token / refresh_token / uid）；
  ④ active_connections 计量正确（含发起 /metrics 的这条连接自身）；
  ⑤ /readyz：有就绪账号 200 pong；0 个就绪账号 503（与 /ping 的 200 分离）；
  ⑥ 防御式：POOL=None / count_ready 抛异常时 /readyz 503 且 /metrics 不 500；
  ⑦ 排空中：listener 停止服务，/readyz 不再返回 200；关闭后连接被拒。
"""
import http.client
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403

_tmp = tempfile.mkdtemp(prefix="qd-metrics-readyz-")
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(_tmp, "usage")
os.environ["ACCOUNTS_DIR"] = os.path.join(_tmp, "accounts")

P, A = bootstrap()

declare_suite(__file__, ["qoder_proxy.py"])

API_KEY_MARK = "GUARD-METRICS-API-KEY-7f3a"
TOKEN_MARK = "SECRET-ACCESS-TOKEN-9c1e"
REFRESH_MARK = "SECRET-REFRESH-TOKEN-42bd"
UID_MARK = "uid-secret-1234"


class _StubAccount(object):
    access_token = TOKEN_MARK
    refresh_token = REFRESH_MARK
    cooldown_until = 0.0

    @property
    def uid(self):
        return UID_MARK


class _StubPool(object):
    def __init__(self, accounts, ready):
        self.accounts = list(accounts)
        self._ready = ready

    def count_ready(self):
        return self._ready

    def get(self, _uid):
        return None


class _BoomPool(object):
    accounts = []

    def count_ready(self):
        raise RuntimeError("pool broken")

    def get(self, _uid):
        return None


def _request(port, path, timeout=3.0):
    c = None
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    except Exception as exc:                      # noqa: BLE001
        return None, {}, type(exc).__name__.encode()
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def _wait_active(srv, count, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if srv.active_connections() >= count:
            return True
        time.sleep(0.02)
    return False


def _wait_idle(srv, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if srv.active_connections() <= 0:
            return True
        time.sleep(0.02)
    return False


_old_pool = P.POOL
_old_key = P.API_KEY
srv = None
held = []

try:
    srv = P.QoderHTTPServer(("127.0.0.1", 0), P.Handler)
    port = int(srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    P.POOL = _StubPool([_StubAccount(), _StubAccount()], ready=2)
    P.API_KEY = API_KEY_MARK

    st, _h, body = _request(port, "/ping")
    check("测试服务端监听成功（/ping 200 pong）", st == 200 and body == b"pong\n",
          (st, body))

    print()
    print("[1] /metrics：Prometheus 文本格式")
    st, headers, raw = _request(port, "/metrics")
    text = raw.decode("utf-8", "replace")
    check("/metrics 返回 200", st == 200, st)
    check("Content-Type 为 Prometheus 文本",
          str(headers.get("Content-Type", "")).startswith(
              "text/plain; version=0.0.4"), headers.get("Content-Type"))
    check("Cache-Control: no-store（采集器不缓存）",
          str(headers.get("Cache-Control", "")) == "no-store",
          headers.get("Cache-Control"))

    types = {}
    samples = []
    bad_lines = []
    for line in text.splitlines():
        if not line or line.startswith("# HELP "):
            continue
        if line.startswith("# TYPE "):
            rest = line[len("# TYPE "):]
            parts = rest.split(" ")
            if len(parts) >= 2:
                types[parts[0]] = parts[1]
            else:
                bad_lines.append(line)
            continue
        if line.startswith("#"):
            bad_lines.append(line)
            continue
        parts = line.split()
        if len(parts) != 2:
            bad_lines.append(line)
            continue
        name, value = parts
        if not re.match(r"^[A-Za-z_:][A-Za-z0-9_:]*$", name):
            bad_lines.append(line)
            continue
        try:
            float(value)
        except ValueError:
            bad_lines.append(line)
            continue
        samples.append((name, float(value)))

    check("所有样本行可解析（name + 数值）", not bad_lines, bad_lines[:5])
    required = ["qoder_accounts_total", "qoder_accounts_ready",
                "qoder_accounts_cooling", "qoder_upstream_errors_total",
                "qoder_requests_total", "qoder_prompt_tokens_total",
                "qoder_completion_tokens_total", "qoder_total_tokens_total",
                "qoder_active_threads", "qoder_active_connections"]
    names = {n for n, _v in samples}
    missing = [n for n in required if n not in names]
    check("必测指标齐全（账号池/计数/线程/活跃连接）", not missing, missing)
    missing_type = [n for n in required if n not in types]
    check("每个指标都有 TYPE 声明", not missing_type, missing_type)
    neg = [(n, v) for n, v in samples if v < 0]
    check("计数/仪表值非负", not neg, neg[:5])
    check("账号池指标与桩一致（total=2, ready=2）",
          dict(samples).get("qoder_accounts_total") == 2.0
          and dict(samples).get("qoder_accounts_ready") == 2.0,
          {n: v for n, v in samples if n.startswith("qoder_accounts")})

    print()
    print("[2] /metrics 不含凭证")
    check("不含 API Key", API_KEY_MARK not in text)
    check("不含 access_token", TOKEN_MARK not in text)
    check("不含 refresh_token", REFRESH_MARK not in text)
    check("不含账号 uid", UID_MARK not in text)

    print()
    print("[3] active_connections 计量（含 /metrics 自身这条连接）")
    held = [socket.create_connection(("127.0.0.1", port), timeout=3)
            for _ in range(2)]
    check("两条空闲连接已被计入", _wait_active(srv, 2), srv.active_connections())
    st, headers, raw = _request(port, "/metrics")
    gauge = None
    if st == 200:
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith("qoder_active_connections "):
                try:
                    gauge = int(float(line.split()[1]))
                except Exception:
                    gauge = None
    check("active_connections == 2 条占用 + 1 条 metrics 自身",
          gauge == 3, gauge)
    for sock in held:
        try:
            sock.close()
        except Exception:
            pass
    held = []
    check("连接释放后计数归零", _wait_idle(srv, 5.0), srv.active_connections())

    print()
    print("[4] /readyz：就绪 -> 200；无就绪账号 -> 503（与 /ping 分离）")
    st, headers, body = _request(port, "/readyz")
    check("有 2 个就绪账号时 /readyz = 200 pong",
          st == 200 and body == b"pong\n", (st, body))
    P.POOL = _StubPool([], ready=0)
    st_r, _h, body_r = _request(port, "/readyz")
    st_p, _h2, body_p = _request(port, "/ping")
    check("0 个就绪账号时 /readyz = 503", st_r == 503, (st_r, body_r[:160]))
    check("503 错误体含机器可读 code=503",
          b'"code": 503' in body_r or b'"code":503' in body_r, body_r[:200])
    check("同一时刻 /ping 仍 200（活着但未就绪要能区分）",
          st_p == 200 and body_p == b"pong\n", (st_p, body_p))

    print()
    print("[5] 防御式：池缺失 / 池抛异常时不让探针和观测面崩")
    P.POOL = None
    st_none, _h, _b = _request(port, "/readyz")
    check("POOL=None 时 /readyz = 503（保守）", st_none == 503, st_none)
    st_m, _h, raw_m = _request(port, "/metrics")
    total_none = None
    if st_m == 200:
        for line in raw_m.decode("utf-8", "replace").splitlines():
            if line.startswith("qoder_accounts_total "):
                total_none = line.split()[1]
    check("POOL=None 时 /metrics 仍 200 且 total=0",
          st_m == 200 and total_none == "0", (st_m, total_none))
    P.POOL = _BoomPool()
    st_boom, _h, _b = _request(port, "/readyz")
    check("count_ready 抛异常时 /readyz = 503（不 500/不挂）", st_boom == 503,
          st_boom)
    st_bm, _h, _b = _request(port, "/metrics")
    check("count_ready 抛异常时 /metrics 仍 200", st_bm == 200, st_bm)

    print()
    print("[6] 排空中：/readyz 不再返回 200（不能再被调度到）")
    P.POOL = _StubPool([_StubAccount(), _StubAccount()], ready=2)
    st_before, _h, body_before = _request(port, "/readyz")
    check("排空前 /readyz 仍是 200（对照组）",
          st_before == 200 and body_before == b"pong\n", (st_before, body_before))
    P._safe_server_shutdown(srv)
    srv.nudge_idle_connections()
    st_drain, _h, body_drain = _request(port, "/readyz", timeout=0.8)
    check("排空中 /readyz 不再返回 200（超时/拒绝/503）",
          st_drain != 200, (st_drain, body_drain[:120]))
    srv.server_close()
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1.5)
        s.close()
        check("server_close 后连接被拒绝", False, "connect 仍然成功")
    except (ConnectionRefusedError, OSError):
        check("server_close 后连接被拒绝", True)
finally:
    P.POOL = _old_pool
    P.API_KEY = _old_key
    for sock in held:
        try:
            sock.close()
        except Exception:
            pass
    if srv is not None:
        try:
            P._safe_server_shutdown(srv)
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
