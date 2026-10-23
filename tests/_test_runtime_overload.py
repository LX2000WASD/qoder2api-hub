# -*- coding: utf-8 -*-
"""QD_MAX_CONNECTIONS 过载守卫（本地 f14031a：活跃连接上限 + 快速 503）。

    python tests/_test_runtime_overload.py

背景：看板高频轮询叠加慢客户端能把 ThreadingHTTPServer 的线程数顶到
上千（每个连接一个线程）。本地补丁在 process_request 里数活跃连接，
超限的连接**不经 Handler 线程**直接裸写一个 503 并关闭——客户端按常规
退避重试即可，进程不能被拖垮。这套断言全部在真进程上做：

  ① QD_MAX_CONNECTIONS 的解析：默认 256、有下限 8（防手滑配 1）、可调大；
  ② 达到上限后的下一个请求快速拿到 503（含机器可读 code 与 Connection:
     close），而不是排队等线程;
  ③ 被拒的请求不会把进程打挂：进程仍活着、日志无 Traceback；
  ④ 释放一个连接后立即恢复服务（计数确实在减，不是只加不减）；
  ⑤ 全部释放后 /metrics 的 active 计数回落（自计 1 条 metrics 连接）；
  ⑥ 连续过载 + 收尾：SIGTERM 仍退出码 0（过载不留脏状态）。
"""
import http.client
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403

declare_suite(__file__, ["qoder_proxy.py"])

_tmp = tempfile.mkdtemp(prefix="qd-runtime-overload-")
_procs = []
_fhs = []
CAP = 8


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _child_env(extra=None):
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
              "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        env.pop(k, None)
    env.update({
        "ACCOUNTS_DIR": os.path.join(_tmp, "accounts"),
        "QD_PROXY_USAGE_DIR": os.path.join(_tmp, "usage"),
        "QD_UPDATE_DISABLE": "1",
        "QD_LOG_JSON": "0",
        "QD_MAX_CONNECTIONS": str(CAP),
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    })
    env.update(extra or {})
    return env


def _start(port, tag):
    log_path = os.path.join(_tmp, "proxy-%s.log" % tag)
    fh = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "qoder_proxy.py"),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=_child_env(), stdout=fh, stderr=subprocess.STDOUT)
    _procs.append(proc)
    _fhs.append(fh)
    return proc, log_path


def _request(port, path="/ping", timeout=2.0):
    c = None
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    except Exception as exc:                      # noqa: BLE001
        return None, type(exc).__name__.encode(), {}
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def _wait_ping(port, proc, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if _request(port, "/ping", timeout=1.0)[0] == 200:
            return True
        time.sleep(0.2)
    return False


def _wait_exit(proc, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return proc.returncode
        time.sleep(0.05)
    return None


def _log(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _kill_all():
    for proc in _procs:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        except Exception:
            pass
    for fh in _fhs:
        try:
            fh.close()
        except Exception:
            pass


try:
    if os.name != "posix":
        skip("QD_MAX_CONNECTIONS 过载（整组）", "依赖 POSIX 信号/套接字语义")
        finish()

    print("[1] 环境解析：默认 256 / 下限 8 / 可调大")
    probe_code = (
        "import os, sys, importlib\n"
        "sys.path.insert(0, %r)\n"
        "import qoder_proxy as P\n"
        "out = [P.QoderHTTPServer.max_active_connections]\n"
        "os.environ['QD_MAX_CONNECTIONS'] = '1'\n"
        "P = importlib.reload(P)\n"
        "out.append(P.QoderHTTPServer.max_active_connections)\n"
        "os.environ['QD_MAX_CONNECTIONS'] = '64'\n"
        "P = importlib.reload(P)\n"
        "out.append(P.QoderHTTPServer.max_active_connections)\n"
        "print('CAPS', out)\n" % ROOT)
    env = _child_env()
    env.pop("QD_MAX_CONNECTIONS", None)
    probe = subprocess.run([sys.executable, "-c", probe_code], env=env,
                           capture_output=True, text=True, timeout=120)
    line = ""
    for chunk in (probe.stdout or "").splitlines():
        if chunk.startswith("CAPS "):
            line = chunk[5:]
    caps = None
    try:
        caps = json.loads(line)
    except Exception:
        caps = None
    check("子进程 import 成功（上限可解析）", probe.returncode == 0 and caps,
          (probe.returncode, (probe.stderr or "")[-300:]))
    if caps:
        check("默认上限 256", caps[0] == 256, caps[0])
        check("手滑配 1 时下限兜底为 8（不把服务锁死）", caps[1] == 8, caps[1])
        check("可调大（64 -> 64）", caps[2] == 64, caps[2])

    print()
    print("[2] 达到上限后：快速 503，而不是排队等线程")
    port = _free_port()
    proc, log_path = _start(port, "cap")
    check("服务端成功监听（/ping 200）", _wait_ping(port, proc),
          _log(log_path)[-600:])

    held = []
    try:
        for _i in range(CAP):
            held.append(socket.create_connection(("127.0.0.1", port), timeout=3))
        # 等 server 把这 CAP 条连接都计入（accept 是异步的）：探测到 503 为止
        status, body, headers = None, b"", {}
        reject_latency = None
        deadline = time.time() + 6.0
        while time.time() < deadline:
            _t = time.time()
            status, body, headers = _request(port, "/ping", timeout=5.0)
            if status == 503:
                reject_latency = time.time() - _t
                break
            time.sleep(0.1)
        check("%d 条连接占满后下一个请求被拒（503）" % CAP, status == 503,
              (status, body[:160]))
        check("拒绝是即时的（< 2s；排队等线程的话客户端会超时而不是 503）",
              reject_latency is not None and reject_latency < 2.0,
              reject_latency)
    finally:
        pass

    print()
    print("[3] 拒绝应答的形状：机器可读错误体 + Connection: close")
    check("503 应答带 JSON 错误体（code=503）",
          b"503" in body and b"too many active connections" in body,
          body[:200])
    check("503 应答带 Connection: close（客户端知道要重连）",
          str(headers.get("Connection", "")).lower() == "close",
          headers.get("Connection"))
    check("Content-Type 为 JSON", "application/json" in
          str(headers.get("Content-Type", "")), headers.get("Content-Type"))

    print()
    print("[4] 进程没被过载打挂：连续被拒后仍存活、日志无 Traceback")
    rejects = 0
    latencies = []
    for _i in range(10):
        t0 = time.time()
        # 客户端 1.5s 超时：服务端若「排队等空闲线程」，这里会拿不到 503
        status, _body, _hd = _request(port, "/ping", timeout=1.5)
        latencies.append(time.time() - t0)
        if status == 503:
            rejects += 1
    check("连续 10 次过载请求全部 503（1.5s 客户端超时内，未排队）",
          rejects == 10, rejects)
    check("最大拒绝延迟 < 1.5s", max(latencies) < 1.5,
          "%.3fs" % max(latencies))
    check("进程仍存活", proc.poll() is None, proc.poll())
    check("日志无 Traceback（拒绝路径没抛异常）",
          "Traceback" not in _log(log_path), _log(log_path)[-600:])

    print()
    print("[5] 释放连接后立即恢复（计数在减，不是只加不减）")
    held.pop().close()
    recovered = False
    deadline = time.time() + 4.0
    while time.time() < deadline:
        st, bd, _hd = _request(port, "/ping", timeout=2.0)
        if st == 200 and bd == b"pong\n":
            recovered = True
            break
        time.sleep(0.1)
    check("释放 1 条连接后 /ping 恢复 200", recovered)

    for sock in held:
        try:
            sock.close()
        except Exception:
            pass
    held = []
    gauge = None
    deadline = time.time() + 6.0
    while time.time() < deadline:
        st, bd, _hd = _request(port, "/metrics", timeout=2.0)
        if st == 200:
            for line in bd.decode("utf-8", "replace").splitlines():
                if line.startswith("qoder_active_connections "):
                    try:
                        gauge = int(float(line.split()[1]))
                    except Exception:
                        gauge = None
            if gauge is not None and gauge <= 2:
                break
        time.sleep(0.2)
    check("全部释放后 active 计数回落（<= 2，含 metrics 自身）",
          gauge is not None and gauge <= 2, gauge)

    print()
    print("[6] 过载之后收尾仍干净：SIGTERM -> 退出码 0")
    try:
        proc.send_signal(signal.SIGTERM)
    except Exception:
        pass
    rc = _wait_exit(proc, timeout=15.0)
    text = _log(log_path)
    check("过载后 SIGTERM 仍退出码 0", rc == 0, (rc, text[-600:]))
    check("过载后收尾日志含 bye", "bye" in text, text[-600:])
    check("收尾全程无 Traceback", "Traceback" not in text, text[-800:])
finally:
    _kill_all()
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
