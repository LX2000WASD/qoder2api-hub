# -*- coding: utf-8 -*-
"""跨进程互斥守卫：accounts 目录 flock + 调度 leader 锁（本地 f14031a）。

    python tests/_test_flock_leader.py

背景：多实例/多进程同时写同一 accounts 目录会丢更新；调度器多实例各自
在 09:00 领签到 = 重复领取（风控）。本地补丁：
  · qoder_accounts._NamedFlock：fcntl.flock(LOCK_EX) 跨进程互斥，同线程
    可重入；Windows 退化为进程内 RLock；
  · qoder_scheduler：巡检前非阻塞抢 <state_dir>/scheduler/.leader.lock，
    只有 leader 执行副作用并写 state.json。

断言全部以**行为**为准（跨进程真起子进程）：
  ① 锁文件路径约定（<accounts>/.save.lock、<state>/scheduler/.leader.lock）；
  ② 同线程可重入；释放深度归零后别的进程能拿到；
  ③ 跨进程互斥（父持锁 -> 子拿不到；子持锁 -> 父拿不到）；
  ④ 两个进程阻塞抢锁被串行化（事件区间不重叠）；
  ⑤ 持锁进程被 SIGKILL 后锁随进程释放（不会留下死锁）；
  ⑥ 同一进程内其它线程也互斥（flock 语义，不是 RLock 语义）；
  ⑦ 调度器：同 state_dir 两个进程只有一个 leader，非 leader 不写 state.json；
  ⑧ leader 退出后锁释放，下一进程能成为 leader。
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403

_tmp = tempfile.mkdtemp(prefix="qd-flock-leader-")
os.environ["ACCOUNTS_DIR"] = os.path.join(_tmp, "accounts")
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(_tmp, "usage")
os.environ["QD_NATIVE_IDENTITY"] = "0"

P, A = bootstrap()

declare_suite(__file__, ["qoder_accounts.py", "qoder_scheduler.py"])

HOLDER_SCRIPT = """
import sys, os, time
sys.path.insert(0, "@@ROOT@@")
import qoder_accounts as A
lock_path, mode, log_path, hold_s = (sys.argv[1], sys.argv[2], sys.argv[3],
                                     float(sys.argv[4]))
lk = A._named_flock(lock_path)
got = bool(lk.acquire(blocking=(mode == "block")))
print("GOT" if got else "BLOCKED", flush=True)
if got:
    with open(log_path, "a") as fh:
        fh.write("start %d %.3f\\n" % (os.getpid(), time.time()))
        fh.flush()
    time.sleep(hold_s)
    with open(log_path, "a") as fh:
        fh.write("end %d %.3f\\n" % (os.getpid(), time.time()))
    lk.release()
"""

SCHED_SCRIPT = """
import sys, os, time
sys.path.insert(0, "@@ROOT@@")
import qoder_scheduler as S
state_dir, mode, hold_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
sch = S.Scheduler(None, state_dir=state_dir)
got = bool(sch._acquire_leader())
print("LEADER" if got else "FOLLOWER", flush=True)
print("IS_LEADER %s" % sch.status().get("is_leader"), flush=True)
print("LOCKPATH %s" % sch._leader_lock_path(), flush=True)
if mode.startswith("write"):
    sch.logs = ["MARK-" + mode]
    sch._save_state(force=True)
time.sleep(hold_s)
if got:
    sch._release_leader()
"""


def _child_env():
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
              "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        env.pop(k, None)
    env.update({
        "ACCOUNTS_DIR": os.path.join(_tmp, "accounts"),
        "QD_PROXY_USAGE_DIR": os.path.join(_tmp, "usage"),
        "QD_NATIVE_IDENTITY": "0",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
    })
    return env


def _write_script(name, body):
    path = os.path.join(_tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body.replace("@@ROOT@@", ROOT))
    return path


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _parse_events(text):
    events = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in ("start", "end"):
            try:
                events.append((float(parts[2]), parts[0], int(parts[1])))
            except ValueError:
                pass
    return events


def _close_sched(sch):
    try:
        if getattr(sch, "_state_timer", None) is not None:
            sch._state_timer.cancel()
    except Exception:
        pass


try:
    if os.name != "posix" or A._fcntl is None:
        skip("跨进程 flock / leader 锁（整组）", "无 fcntl（Windows 退化为进程内锁）")
        finish()

    holder = _write_script("holder.py", HOLDER_SCRIPT)
    sched_child = _write_script("sched_child.py", SCHED_SCRIPT)
    env = _child_env()

    print("[1] 锁路径约定 + 进程内同一把锁")
    accounts_dir = os.path.join(_tmp, "accounts")
    os.makedirs(accounts_dir, exist_ok=True)
    check("账号目录锁固定为 <accounts>/.save.lock",
          A._account_dir_flock(accounts_dir).path ==
          os.path.join(os.path.abspath(accounts_dir), ".save.lock"),
          A._account_dir_flock(accounts_dir).path)
    lock_path = os.path.join(_tmp, "locks", ".save.lock")
    lk = A._named_flock(lock_path)
    check("同一路径返回同一把锁（重入记账才生效）",
          A._named_flock(lock_path) is lk)

    print()
    print("[2] 同线程可重入；释放到位后别的进程可拿")
    check("同线程重入两次都成功",
          lk.acquire(blocking=False) is True and lk.acquire(blocking=False) is True)
    lk.release()
    still_held = lk.held_by_current_thread()
    lk.release()
    check("两次 release 后深度归零", not lk.held_by_current_thread(),
          still_held)
    ev = os.path.join(_tmp, "reentrant.log")
    r = subprocess.run([sys.executable, holder, lock_path, "nonblock", ev, "0"],
                       env=env, capture_output=True, text=True, timeout=30)
    check("释放后子进程能拿到锁（深度记账没有泄漏）",
          "GOT" in (r.stdout or ""), (r.stdout, r.stderr[-200:]))

    print()
    print("[3] 跨进程互斥：父持锁 -> 子拿不到")
    check("父进程拿到锁", lk.acquire(blocking=False) is True)
    ev = os.path.join(_tmp, "parent_holds.log")
    r = subprocess.run([sys.executable, holder, lock_path, "nonblock", ev, "0"],
                       env=env, capture_output=True, text=True, timeout=30)
    check("子进程被挡住（BLOCKED）", "BLOCKED" in (r.stdout or ""),
          (r.stdout, r.stderr[-200:]))
    check("子进程没有写入事件（没进临界区）", not os.path.exists(ev)
          or _read(ev).strip() == "", _read(ev))
    lk.release()

    print()
    print("[4] 跨进程互斥：子持锁 -> 父拿不到（反向也成立）")
    ev = os.path.join(_tmp, "child_holds.log")
    child = subprocess.Popen([sys.executable, holder, lock_path, "block", ev,
                              "1.5"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        started = _wait_for(lambda: "start" in _read(ev), timeout=10.0)
        check("子进程已进入临界区", started, _read(ev))
        check("子持锁期间父进程拿不到", lk.acquire(blocking=False) is False)
        check("父进程仍认为未持有锁", not lk.held_by_current_thread())
        child.wait(timeout=30)
        ok = lk.acquire(blocking=False)
        check("子进程退出后父进程能拿到锁", ok is True)
        if ok:
            lk.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    print()
    print("[5] 两个进程阻塞抢锁被串行化（事件区间不重叠）")
    ev = os.path.join(_tmp, "serial.log")
    kids = [subprocess.Popen([sys.executable, holder, lock_path, "block", ev,
                              "0.35"], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL) for _ in range(2)]
    for kid in kids:
        kid.wait(timeout=30)
    events = sorted(_parse_events(_read(ev)))
    pattern = [kind for _ts, kind, _pid in events]
    check("两个进程都拿到了锁（2 start / 2 end）",
          pattern.count("start") == 2 and pattern.count("end") == 2, pattern)
    check("临界区严格串行（区间不重叠）",
          pattern == ["start", "end", "start", "end"], pattern)
    starts = [ts for ts, kind, _pid in events if kind == "start"]
    ends = [ts for ts, kind, _pid in events if kind == "end"]
    check("第二次 start 不早于第一次 end",
          len(starts) == 2 and len(ends) == 2 and starts[1] >= ends[0] - 0.05,
          (starts, ends))
    pids = {pid for _ts, _kind, pid in events}
    check("两个不同进程（真的是跨进程互斥）", len(pids) == 2, pids)

    print()
    print("[6] 持锁进程被 SIGKILL：锁随进程释放，不留死锁")
    ev = os.path.join(_tmp, "killed.log")
    victim = subprocess.Popen([sys.executable, holder, lock_path, "block", ev,
                               "30"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        check("受害进程已持锁", _wait_for(lambda: "start" in _read(ev),
                                          timeout=10.0), _read(ev))
        check("此期间父进程拿不到", lk.acquire(blocking=False) is False)
        victim.kill()          # SIGKILL：不给它走 release 的机会
        victim.wait(timeout=10)
        got = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if lk.acquire(blocking=False):
                got = True
                break
            time.sleep(0.05)
        check("SIGKILL 之后锁被内核释放（父进程可获取）", got)
        if got:
            lk.release()
    finally:
        if victim.poll() is None:
            victim.kill()
            victim.wait(timeout=10)

    print()
    print("[7] 同一进程的其它线程也互斥（flock 语义而非 RLock）")
    thread_lock = A._named_flock(os.path.join(_tmp, "locks", "thread.lock"))
    check("主线程持锁", thread_lock.acquire(blocking=False) is True)
    seen = {}

    def _other_thread():
        seen["got"] = bool(thread_lock.acquire(blocking=False))

    th = threading.Thread(target=_other_thread)
    th.start()
    th.join(10)
    check("其它线程拿不到（同一进程内不串行放行）", seen.get("got") is False,
          seen)
    thread_lock.release()

    print()
    print("[8] 调度 leader 锁：同 state_dir 只有一个 leader，非 leader 不写 state")
    state_dir = os.path.join(_tmp, "state_main")
    a = subprocess.Popen([sys.executable, sched_child, state_dir, "write-A",
                          "1.5"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    state_json = os.path.join(state_dir, "scheduler", "state.json")
    leader_lock = os.path.join(state_dir, "scheduler", ".leader.lock")
    b = None
    try:
        first = (a.stdout.readline() or "").strip()
        is_leader_a = (a.stdout.readline() or "").strip()
        lock_line = (a.stdout.readline() or "").strip()
        check("第一个实例成为 leader", first == "LEADER", (first, is_leader_a))
        check("leader 的 status().is_leader = True",
              is_leader_a == "IS_LEADER True", is_leader_a)
        check("leader 锁文件为 <state>/scheduler/.leader.lock",
              lock_line.endswith("scheduler/.leader.lock"), lock_line)
        check("leader 已写出 state.json", _wait_for(
            lambda: os.path.isfile(state_json) and "MARK-write-A" in
            _read(state_json), timeout=10.0), _read(state_json)[-300:])
        check("锁文件落在调度状态目录", os.path.isfile(leader_lock), leader_lock)

        b = subprocess.run([sys.executable, sched_child, state_dir,
                            "write-B", "0.1"], env=env, capture_output=True,
                           text=True, timeout=30)
        lines = [ln.strip() for ln in (b.stdout or "").splitlines() if ln.strip()]
        check("第二个实例竞争失败（FOLLOWER）",
              lines[:1] == ["FOLLOWER"], lines[:2])
        check("非 leader 的 status().is_leader = False",
              "IS_LEADER False" in lines, lines[:3])
        state_text = _read(state_json)
        check("非 leader 的 force 写盘被闸门挡住（state 无 MARK-write-B）",
              "MARK-write-B" not in state_text, state_text[-300:])
        check("leader 写入的内容仍在（没有被非 leader 覆盖）",
              "MARK-write-A" in state_text, state_text[-300:])
    finally:
        if a.poll() is None:
            a.wait(timeout=30)
        if a.poll() is None:
            a.kill()
            a.wait(timeout=10)

    c = subprocess.run([sys.executable, sched_child, state_dir, "write-C", "0.1"],
                       env=env, capture_output=True, text=True, timeout=30)
    lines_c = [ln.strip() for ln in (c.stdout or "").splitlines() if ln.strip()]
    check("leader 退出后下一进程能接任 leader（锁已释放）",
          lines_c[:1] == ["LEADER"], lines_c[:2])

    print()
    print("[9] 进程内：非 leader 不落盘 / leader 落盘")
    import qoder_scheduler as S
    state2 = os.path.join(_tmp, "state_2")
    sch = S.Scheduler(None, state_dir=state2)
    try:
        check("新调度器 leader 锁路径正确",
              sch._leader_lock_path() == os.path.join(
                  os.path.abspath(state2), "scheduler", ".leader.lock"),
              sch._leader_lock_path())
        path2 = os.path.join(state2, "scheduler", "state.json")
        sch._is_leader = False
        sch.logs = ["SHOULD-NOT-WRITE"]
        sch._save_state(force=True)
        check("非 leader force 写盘：文件不出现", not os.path.isfile(path2),
              _read(path2)[-200:])
        check("status().is_leader = False", sch.status()["is_leader"] is False)
        sch._is_leader = True
        sch._save_state(force=True)
        check("leader force 写盘：文件出现且内容正确",
              os.path.isfile(path2) and "SHOULD-NOT-WRITE" in _read(path2),
              _read(path2)[-200:])
        check("status().is_leader = True", sch.status()["is_leader"] is True)
    finally:
        _close_sched(sch)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
