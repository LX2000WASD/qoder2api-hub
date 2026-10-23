# -*- coding: utf-8 -*-
"""SSE 有界队列 / 背压守卫（本地 f14031a：sse_with_heartbeat 的 maxsize）。

    python tests/_test_sse_backpressure.py

背景：sse_with_heartbeat 用「读线程 + 主线程」解耦，主线程负责按空闲
间隔补 ": ping" 心跳。上游队列原本是无界 queue.Queue()：一个慢客户端
会让上游全速把数据灌进内存（OOM/被上游判定为异常连接）。本地补丁把
队列改成 maxsize（默认 16，QD_SSE_QUEUE_SIZE 可调），背压经 put 传导
到上游。这套断言钉住「有界」这一条本质：

  ① 常量本身有界：默认 16、环境变量可配、配 0 兜底为 1（绝不无界）；
  ② 慢消费者时生产者停在「已消费 + maxsize + 1」以内（不再全速灌内存）；
  ③ 恢复消费后不丢帧、顺序不变（背压是阻塞，不是丢弃）；
  ④ 上游异常原样抛给消费方（有界队列不吞异常）；
  ⑤ 心跳仍在工作：上游静默期间发出 ": ping"，且不污染数据帧；
  ⑥ interval=0 显式关心跳时退化为直通（不发 ping、数据照常）；
  ⑦ 一条流异常之后，新流照常工作（无跨流残留状态）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403

_tmp = tempfile.mkdtemp(prefix="qd-sse-backpressure-")
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(_tmp, "usage")
os.environ["ACCOUNTS_DIR"] = os.path.join(_tmp, "accounts")

P, A = bootstrap()

declare_suite(__file__, ["qoder_proxy.py"])

PING = b": ping\n\n"
ITEMS = [b"data: %d\n\n" % i for i in range(200)]


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
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    })
    env.pop("QD_SSE_QUEUE_SIZE", None)
    env.update(extra or {})
    return env


try:
    print("[1] 队列常量有界：默认 16 / 可配 / 配 0 兜底为 1")
    default_size = P._SSE_QUEUE_MAXSIZE
    check("默认队列上限为 16（有界）", default_size == 16, default_size)

    probe_code = (
        "import os, sys, importlib, json\n"
        "sys.path.insert(0, %r)\n"
        "import qoder_proxy as P\n"
        "out = {'default': P._SSE_QUEUE_MAXSIZE}\n"
        "os.environ['QD_SSE_QUEUE_SIZE'] = '3'\n"
        "P = importlib.reload(P)\n"
        "out['three'] = P._SSE_QUEUE_MAXSIZE\n"
        "os.environ['QD_SSE_QUEUE_SIZE'] = '0'\n"
        "P = importlib.reload(P)\n"
        "out['zero'] = P._SSE_QUEUE_MAXSIZE\n"
        "print('SIZES', json.dumps(out))\n" % ROOT)
    probe = subprocess.run([sys.executable, "-c", probe_code], env=_child_env(),
                           capture_output=True, text=True, timeout=120)
    sizes = None
    for line in (probe.stdout or "").splitlines():
        if line.startswith("SIZES "):
            try:
                sizes = json.loads(line[6:])
            except Exception:
                sizes = None
    check("子进程探测成功（/可调）", probe.returncode == 0 and sizes,
          (probe.returncode, (probe.stderr or "")[-300:]))
    if sizes:
        check("QD_SSE_QUEUE_SIZE=3 -> 3", sizes.get("three") == 3,
              sizes.get("three"))
        check("QD_SSE_QUEUE_SIZE=0 -> 1（绝不无界）", sizes.get("zero") == 1,
              sizes.get("zero"))

    print()
    print("[2] 慢消费者：生产者被背压挡住（不会全速灌内存）")
    produced = []
    sink = []

    def source():
        for item in ITEMS:
            produced.append(item)
            yield item

    gen = P.sse_with_heartbeat(source(), sink.append, interval=5)
    head = [next(gen) for _ in range(3)]
    # 等生产者真正被 put 阻塞住（长度连续 0.25s 不增长）
    last = -1
    stable_since = time.time()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if len(produced) != last:
            last = len(produced)
            stable_since = time.time()
        elif time.time() - stable_since > 0.25:
            break
        time.sleep(0.02)
    bound = 3 + default_size + 1
    check("生产者停在「已消费 + maxsize + 1」以内（有界队列生效）",
          len(produced) <= bound, (len(produced), bound))
    check("生产者没有把 200 帧全灌进来（无界队列会）",
          len(produced) < len(ITEMS), len(produced))
    check("背压时已有数据在途（队列没被 0 帧空转）", len(produced) >= 4,
          len(produced))

    print()
    print("[3] 恢复消费：不丢帧、顺序不变（阻塞而非丢弃）")
    rest = list(gen)
    check("恢复后收全 200 帧且顺序不变", head + rest == ITEMS,
          (len(head), len(rest)))
    check("心跳不混进数据流", not any(x == PING for x in head + rest))

    print()
    print("[4] 上游异常原样抛给消费方（不吞、不串成正常帧）")
    def boom_source():
        yield b"data: a\n\n"
        yield b"data: b\n\n"
        raise RuntimeError("upstream boom")

    sink2 = []
    gen2 = P.sse_with_heartbeat(boom_source(), sink2.append, interval=5)
    got2 = [next(gen2), next(gen2)]
    raised = None
    try:
        next(gen2)
    except Exception as exc:                      # noqa: BLE001 - 要的正是异常
        raised = exc
    check("异常原样抛出（RuntimeError）", isinstance(raised, RuntimeError),
          type(raised).__name__ if raised else "no exception")
    check("异常文本原样保留", "upstream boom" in str(raised or ""), raised)
    check("异常之前的帧正常送达", got2 == [b"data: a\n\n", b"data: b\n\n"], got2)

    print()
    print("[5] 心跳：上游静默期间发 : ping，且不污染数据帧")
    pings = []

    def slow_source():
        yield b"data: x\n\n"
        time.sleep(0.35)
        yield b"data: y\n\n"

    gen3 = P.sse_with_heartbeat(slow_source(), pings.append, interval=0.05)
    out3 = [next(gen3), next(gen3)]
    check("静默期间至少发出 1 条心跳", pings.count(PING) >= 1, len(pings))
    check("心跳走 send 回调（不进数据流）", out3 == [b"data: x\n\n",
                                                    b"data: y\n\n"], out3)

    print()
    print("[6] interval=0 显式关心跳：直通、不发 ping")
    pings4 = []

    def source4():
        yield b"data: 1\n\n"
        time.sleep(0.2)
        yield b"data: 2\n\n"

    out4 = list(P.sse_with_heartbeat(source4(), pings4.append, interval=0))
    check("interval=0 时不发心跳", pings4 == [], pings4)
    check("interval=0 时数据照常直通", out4 == [b"data: 1\n\n", b"data: 2\n\n"],
          out4)

    print()
    print("[7] 异常流之后新流照常工作（无跨流残留）")
    gen5 = P.sse_with_heartbeat(iter([b"data: z\n\n"]), lambda _b: None,
                                interval=5)
    check("异常之后新流正常产出", list(gen5) == [b"data: z\n\n"], gen5)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
