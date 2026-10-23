# -*- coding: utf-8 -*-
"""SIGTERM 优雅退出守卫（本地 f14031a 运行时强化，上游无对应实现）。

    python tests/_test_runtime_sigterm.py

背景：合流后 docker stop / systemd 发的是 SIGTERM。旧路径在主线程的信号
处理器里直接 server.shutdown() 会死锁（shutdown 等 serve_forever 退出，
而循环正被信号处理器打断）；本地补丁改成独立线程停 listener + 主线程
finally 里排空 + 关 socket。这套断言**全部在进程边界观察**（起真进程、
发真信号），因为要守的正是「进程能不能干净地退出来」：

  ① SIGTERM 后退出码 0（不是被 SIGKILL / 异常退出）；
  ② 日志里既有「收到 SIGTERM」也有收尾完成的 "bye"（证明 finally 跑到）；
  ③ 全程没有 Traceback（handler 里的死锁/异常会以 traceback 收场）；
  ④ 退出耗时受优雅期约束（默认 10s，实测亚秒；这里钉 < 8s）；
  ⑤ listener 真的释放了：同一端口可立即再起一个实例并 /ping 通；
  ⑥ 重复 SIGTERM 只触发一次收尾（日志只出现一条 SIGTERM，幂等）；
  ⑦ 空闲 keep-alive 连接不占优雅期：持有 idle 连接时 SIGTERM 仍快速退
     出，且日志不出现 "grace period over"（nudge_idle_connections 生效）。

判据层：进程退出码 + 日志 + 端口行为。**不断言启动横幅/Key 文案**——
启动鉴权块正在被另一条分支改（LAN 横幅/Key 脱敏），本套件只看生命周期。
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

_tmp = tempfile.mkdtemp(prefix="qd-runtime-sigterm-")
_procs = []
_fhs = []


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _child_env(extra=None):
    """子进程环境：账号/用量目录隔离到临时目录，出站一律指向本地黑洞。

    HTTP(S)_PROXY 指到 127.0.0.1:9 是有意的：启动自检会发一个 HEAD 测时钟
    漂移（_measure_clock_skew），套件不允许它真的出网；连不上即 fail-open。
    """
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
              "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        env.pop(k, None)
    env.update({
        "ACCOUNTS_DIR": os.path.join(_tmp, "accounts"),
        "QD_PROXY_USAGE_DIR": os.path.join(_tmp, "usage"),
        "QD_UPDATE_DISABLE": "1",
        "QD_LOG_JSON": "0",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    })
    env.update(extra or {})
    return env


def _start(port, tag, extra_env=None):
    log_path = os.path.join(_tmp, "proxy-%s.log" % tag)
    fh = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "qoder_proxy.py"),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=_child_env(extra_env), stdout=fh,
        stderr=subprocess.STDOUT)
    _procs.append(proc)
    _fhs.append(fh)
    return proc, log_path


def _request(port, path="/ping", timeout=2.0):
    """返回 (status, body)；连不上/超时返回 (None, reason)。"""
    c = None
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read()
    except Exception as exc:                      # noqa: BLE001 - 探活本来就允许失败
        return None, type(exc).__name__
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


def _sigterm(proc):
    try:
        proc.send_signal(signal.SIGTERM)
        return True
    except Exception:
        return False


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
        skip("SIGTERM 优雅退出（整组）", "Windows 无 POSIX SIGTERM 语义")
        finish()

    print("[1] 基本路径：起服务 -> SIGTERM -> 退出码 0 + 收尾日志")
    port1 = _free_port()
    p1, log1 = _start(port1, "basic")
    check("服务端成功监听（/ping 200）", _wait_ping(port1, p1), _log(log1)[-500:])

    t0 = time.time()
    sent = _sigterm(p1)
    rc1 = _wait_exit(p1, timeout=15.0)
    elapsed1 = time.time() - t0
    text1 = _log(log1)
    check("SIGTERM 成功投递", sent)
    check("退出码 0（干净退出，不是被信号打死）", rc1 == 0, (rc1, text1[-800:]))
    check("日志含 SIGTERM 排空提示", "SIGTERM" in text1, text1[-800:])
    check("日志含收尾完成标记 bye（finally 跑到了）", "bye" in text1,
          text1[-800:])
    check("全程无 Traceback", "Traceback" not in text1, text1[-1200:])
    check("退出耗时受优雅期约束（< 8s）", elapsed1 < 8.0, "%.2fs" % elapsed1)

    print()
    print("[2] listener 确实释放：同端口可立即再起实例")
    p2, log2 = _start(port1, "reuse")
    check("退出后同端口立即可复用（/ping 200）", _wait_ping(port1, p2),
          _log(log2)[-800:])

    print()
    print("[3] 重复 SIGTERM 幂等：只触发一次收尾")
    if p2.poll() is None:
        _sigterm(p2)
        time.sleep(0.05)
        _sigterm(p2)          # 第二发：进程可能已退出，投递失败不算错
        rc2 = _wait_exit(p2, timeout=15.0)
        text2 = _log(log2)
        check("双发 SIGTERM 仍退出码 0", rc2 == 0, (rc2, text2[-800:]))
        check("重复信号不重复收尾（日志只有 1 条 SIGTERM）",
              text2.count("SIGTERM") == 1, text2[-800:])
        check("双发后仍无 Traceback", "Traceback" not in text2)
    else:
        check("双发 SIGTERM 仍退出码 0", False, "p2 启动后意外退出")

    print()
    print("[4] 空闲 keep-alive 连接不占优雅期（nudge_idle_connections）")
    port3 = _free_port()
    p3, log3 = _start(port3, "idle")
    up3 = _wait_ping(port3, p3)
    check("第三实例监听成功", up3, _log(log3)[-500:])
    idle_sock = None
    if up3:
        try:
            idle_sock = socket.create_connection(("127.0.0.1", port3), timeout=3)
            idle_sock.sendall(b"GET /ping HTTP/1.1\r\nHost: x\r\n"
                              b"Connection: keep-alive\r\n\r\n")
            time.sleep(0.3)
            # 不读响应、也不发第二个请求：连接处于「空闲等下一条请求」状态
            idle_sock.recv(65535)
            t3 = time.time()
            _sigterm(p3)
            rc3 = _wait_exit(p3, timeout=15.0)
            elapsed3 = time.time() - t3
            text3 = _log(log3)
            check("持有 idle 连接时 SIGTERM 仍退出码 0", rc3 == 0,
                  (rc3, text3[-800:]))
            check("idle 连接被 nudge 唤醒而非空等优雅期（< 4s）",
                  elapsed3 < 4.0, "%.2fs" % elapsed3)
            check("未出现 'grace period over'（没有空耗完整个优雅期）",
                  "grace period over" not in text3, text3[-800:])
        finally:
            if idle_sock is not None:
                try:
                    idle_sock.close()
                except Exception:
                    pass

    print()
    print("[5] 退出后端口不再应答（listener 已关）")
    if p3.poll() is not None:
        try:
            s = socket.create_connection(("127.0.0.1", port3), timeout=1.5)
            s.close()
            check("退出后端口不再接受连接", False, "connect 仍然成功")
        except (ConnectionRefusedError, OSError):
            check("退出后端口不再接受连接", True)
    else:
        check("退出后端口不再接受连接", False, "p3 未退出")
finally:
    _kill_all()
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
