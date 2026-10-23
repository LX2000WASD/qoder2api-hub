# -*- coding: utf-8 -*-
"""排空在飞请求守卫（SIGTERM 优雅期的核心：慢客户端能读完当前帧）。

    python tests/_test_runtime_drain.py

背景：docker stop 先 SIGTERM，10s 后 SIGKILL。收尾顺序是
「停 listener -> nudge 空闲 keep-alive -> wait_connections_drained(grace)
-> server_close」。主流程（main 的 finally）本身在 _test_runtime_sigterm
里以真进程覆盖；这里钉住**排空原语本身**的语义，因为这条路径没法用真
代理端到端复现（真端点没有可控的慢响应，起假上游又太重）：

  ① 慢 SSE 客户端的响应还在写（handler 在 sleep）时发起 shutdown，
     wait_connections_drained 必须等它写完再返回 True；
  ② 客户端收到的是**完整**的两帧（不是被截断的半帧）；
  ③ 客户端读完的时刻晚于 shutdown 发起的时刻（证明它确实跨越了排空期）；
  ④ 排空后 active_connections 归零；
  ⑤ 优雅期不够时 wait_connections_drained 返回 False（不是死等/假 True）；
  ⑥ 空闲 keep-alive 连接被 nudge 立即唤醒（EOF），不占优雅期；
  ⑦ server_close 之后新连接被拒绝（listener 真的关了）；
  ⑧ server.shutdown 重复调用幂等（收尾路径可重入）。

判据层：QoderHTTPServer + 真实 Handler 的 socket 行为（进程内起服务）。
"""
import http.client
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403

_tmp = tempfile.mkdtemp(prefix="qd-runtime-drain-")
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(_tmp, "usage")
os.environ["ACCOUNTS_DIR"] = os.path.join(_tmp, "accounts")

P, A = bootstrap()

declare_suite(__file__, ["qoder_proxy.py"])

FRAME1 = b"data: {\"n\": 1}\n\n"
FRAME2 = b"data: {\"n\": 2}\n\n"
FULL = FRAME1 + FRAME2
_servers = []


class _SlowSSEHandler(P.Handler):
    """在真实 Handler 上加一个可控慢端点：先写一帧、停 0.7s、再写一帧。"""

    def do_GET(self):
        if self.path.split("?")[0] == "/slow-sse":
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(FULL)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(FRAME1)
            self.wfile.flush()
            time.sleep(0.7)
            self.wfile.write(FRAME2)
            self.wfile.flush()
            return
        return P.Handler.do_GET(self)


def _serve():
    srv = P.QoderHTTPServer(("127.0.0.1", 0), _SlowSSEHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _servers.append(srv)
    return srv, int(srv.server_address[1])


def _wait_active(srv, count=1, timeout=5.0):
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


def _get(port, path="/ping", timeout=3.0):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def _close_server(srv):
    try:
        P._safe_server_shutdown(srv)
    except Exception:
        pass
    try:
        srv.server_close()
    except Exception:
        pass

try:
    print("[1] 慢 SSE 客户端：响应在写时 shutdown，客户端能读完当前帧")
    srv, port = _serve()
    st, body = _get(port, "/ping")
    check("测试服务端用真实 Handler 提供 /ping（200 pong）",
          st == 200 and body == b"pong\n", (st, body))

    result = {}
    first_frame = threading.Event()

    def slow_client():
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            c.request("GET", "/slow-sse")
            r = c.getresponse()
            head = r.read(len(FRAME1))
            result["status"] = r.status
            result["head"] = head
            first_frame.set()
            rest = r.read()
            result["rest"] = rest
            result["finished_at"] = time.time()
            c.close()
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
            first_frame.set()

    th = threading.Thread(target=slow_client, daemon=True)
    th.start()
    check("客户端在 shutdown 之前收到第一帧（响应确实在途中）",
          first_frame.wait(10) and result.get("head") == FRAME1,
          result.get("error") or result.get("head"))
    check("响应进行中：active_connections >= 1", _wait_active(srv, 1),
          srv.active_connections())

    t_shutdown = time.time()
    P._safe_server_shutdown(srv)
    srv.nudge_idle_connections()
    drained = srv.wait_connections_drained(5.0)
    th.join(10)
    check("排空返回 True（等到了在飞响应写完）", drained is True, drained)
    check("客户端收到完整两帧（没有半帧截断）",
          result.get("rest") == FRAME2 and result.get("head") == FRAME1,
          result.get("error") or (result.get("head"), result.get("rest")))
    check("客户端读完的时刻晚于 shutdown 发起时刻（跨越了排空期）",
          float(result.get("finished_at") or 0) > t_shutdown,
          (result.get("finished_at"), t_shutdown))
    check("排空后活跃连接归零", _wait_idle(srv, 3.0),
          srv.active_connections())

    t_repeat = time.time()
    repeat_ok = True
    try:
        P._safe_server_shutdown(srv)
    except Exception:
        repeat_ok = False
    check("server.shutdown 重复调用幂等（不抛异常、快速返回）",
          repeat_ok and (time.time() - t_repeat) < 2.5,
          (repeat_ok, "%.2fs" % (time.time() - t_repeat)))

    print()
    print("[2] 优雅期不足：wait_connections_drained 返回 False（据实上报）")
    srv2, port2 = _serve()
    c2 = http.client.HTTPConnection("127.0.0.1", port2, timeout=15)
    c2.request("GET", "/slow-sse")
    r2 = c2.getresponse()
    reader2 = threading.Thread(target=lambda: r2.read(), daemon=True)
    reader2.start()
    check("第二个慢响应已在途中", _wait_active(srv2, 1), srv2.active_connections())
    check("grace=0.15s 时排空返回 False（不假装排空完成）",
          srv2.wait_connections_drained(0.15) is False)
    check("随后在 3s 优雅期内排空完成", srv2.wait_connections_drained(3.0) is True)
    reader2.join(5)
    c2.close()

    print()
    print("[3] 空闲 keep-alive 连接：nudge 立即唤醒，不占优雅期")
    st3, body3 = _get(port2, "/ping")
    check("nudge 前的 /ping 正常", st3 == 200 and body3 == b"pong\n", (st3, body3))
    c3 = http.client.HTTPConnection("127.0.0.1", port2, timeout=5)
    c3.request("GET", "/ping")
    r3 = c3.getresponse()
    check("idle 连接读到完整响应", r3.status == 200 and r3.read() == b"pong\n",
          r3.status)
    check("idle 连接计入活跃数", _wait_active(srv2, 1), srv2.active_connections())
    t_nudge = time.time()
    srv2.nudge_idle_connections()
    eof = None
    try:
        c3.sock.settimeout(1.5)
        eof = c3.sock.recv(16)
    except Exception as exc:
        eof = "ERR:%s" % type(exc).__name__
    check("nudge 让空闲连接立即 EOF（服务端不再空等）", eof == b"", eof)
    check("nudge 在 1.5s 内唤醒连接", (time.time() - t_nudge) < 1.5)
    check("nudge 后活跃数归零", _wait_idle(srv2, 3.0),
          srv2.active_connections())
    try:
        c3.close()
    except Exception:
        pass

    print()
    print("[4] 关闭后：listener 不再接受连接")
    _close_server(srv2)
    time.sleep(0.1)
    try:
        s = socket.create_connection(("127.0.0.1", port2), timeout=1.5)
        s.close()
        check("server_close 后新连接被拒绝", False, "connect 仍然成功")
    except (ConnectionRefusedError, OSError):
        check("server_close 后新连接被拒绝", True)

    print()
    print("[5] 无连接时排空立即成功（空池不空等）")
    srv3, _port3 = _serve()
    t0 = time.time()
    ok3 = srv3.wait_connections_drained(5.0)
    check("无连接时 wait_connections_drained 立即 True",
          ok3 is True and (time.time() - t0) < 1.0,
          (ok3, "%.2fs" % (time.time() - t0)))
finally:
    for srv in _servers:
        _close_server(srv)
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
