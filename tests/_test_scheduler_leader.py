"""Wave2 守卫：本地 5 分钟保活 / 时区偏移告警 / scheduler.enabled 立即落盘 / Account.save flock

    python tests/_test_scheduler_leader.py

这四项是 fork v1.4 的本地能力（f14031a），合流到 v1.7.0 时未重放；本套件是它们的
守卫。设计原则（与 tests/README.md 一致）：
  · 断言**行为**优先（调用次数/落盘内容/文件权限），源码级断言只用于「接线」
    （property 用 @property、save 的 with 里必须同时有两把锁）；
  · 所有 fake 都在 finally 里还原，套件自身不留全局副作用。
"""
import datetime
import inspect
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *          # noqa: F401,F403

P, A = bootstrap()
declare_suite(__file__, ["qoder_scheduler.py", "qoder_accounts.py"])
import qoder_scheduler as S         # noqa: E402
import qoder_tasks as T             # noqa: E402


class _Pool:
    """最小账号池桩：Scheduler 只用 .dir / .accounts。"""

    def __init__(self, accounts=None):
        self.dir = tempfile.mkdtemp(prefix="sched12-")
        self.accounts = list(accounts or [])


class _Acc:
    """最小账号桩：只提供 run_keepalive 会读的字段。"""

    def __init__(self, uid, minutes=10, enabled=True):
        self.uid = uid
        self.enabled = enabled
        self.access_token = "dt-stub"
        self.expires_at = time.time() + minutes * 60
        self.refresh_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        return True


# ---------------------------------------------------------------- [1] 本地轻量保活
check("KEEPALIVE_TICK_INTERVAL 是 5 分钟", S.KEEPALIVE_TICK_INTERVAL == 300,
      S.KEEPALIVE_TICK_INTERVAL)
check("临期阈值与任务层 run_keepalive 默认值一致（防两处常量漂移）",
      S._KEEPALIVE_THRESHOLD
      == inspect.signature(T.run_keepalive).parameters["threshold_seconds"].default,
      (S._KEEPALIVE_THRESHOLD,
       inspect.signature(T.run_keepalive).parameters["threshold_seconds"].default))

_ka_calls = {"n": 0, "uids": [], "force": None}
_ka_result = {"refreshed": 1, "failed": 0, "failed_uids": [], "logs": []}


def _fake_keepalive(pool, force=False, **kw):
    _ka_calls["n"] += 1
    _ka_calls["uids"] = [a.uid for a in pool.accounts]
    _ka_calls["force"] = force
    return dict(_ka_result)


_orig_ka = S.run_keepalive
S.run_keepalive = _fake_keepalive
try:
    near = _Acc("u-near", minutes=10)        # 10 分钟 -> 临期
    far = _Acc("u-far", minutes=600)         # 10 小时 -> 不临期
    off = _Acc("u-off", minutes=10, enabled=False)
    s = S.Scheduler(_Pool([near, far, off]))
    s._last_keepalive_tick = 0.0
    s._local_keepalive_tick()
    check("本地保活只挑临期账号（跳过禁用/远未过期）",
          _ka_calls["uids"] == ["u-near"], _ka_calls)
    check("本地保活走 force=False（不整批刷新）", _ka_calls["force"] is False,
          _ka_calls.get("force"))
    n1 = _ka_calls["n"]
    s._local_keepalive_tick()
    check("5 分钟节流：紧接着的第二次 tick 不再调任务层",
          _ka_calls["n"] == n1, (_ka_calls["n"], n1))

    # 暂停时不做保活（与看板开关语义一致）
    s_off = S.Scheduler(_Pool([_Acc("u-x", minutes=1)]))
    s_off.enabled = False
    s_off._last_keepalive_tick = 0.0
    n2 = _ka_calls["n"]
    s_off._local_keepalive_tick()
    check("调度器暂停时不触发本地保活", _ka_calls["n"] == n2)

    # 没有临期账号 -> 一次都不调（否则任务层会写「无需刷新」噪声日志）
    s_none = S.Scheduler(_Pool([_Acc("u-ok", minutes=999)]))
    s_none._last_keepalive_tick = 0.0
    n3 = _ka_calls["n"]
    s_none._local_keepalive_tick()
    check("无临期账号时完全不调 run_keepalive（避免空跑噪声）",
          _ka_calls["n"] == n3)

    # 失败停放 1 小时：死号不每 5 分钟重试
    _ka_result = {"refreshed": 0, "failed": 1,
                  "failed_uids": ["u-park"], "logs": []}
    park = _Acc("u-park", minutes=5)
    s_park = S.Scheduler(_Pool([park]))
    s_park._last_keepalive_tick = 0.0
    s_park._local_keepalive_tick()
    check("保活失败的账号进入 1 小时停放表",
          s_park._keepalive_parked.get("u-park", 0) > time.time() + 3000,
          s_park._keepalive_parked)
    n4 = _ka_calls["n"]
    s_park._last_keepalive_tick = 0.0
    s_park._local_keepalive_tick()
    check("停放期内不再重试（死号不每 5 分钟打一次）", _ka_calls["n"] == n4)
finally:
    S.run_keepalive = _orig_ka

# 接线守卫：_run_loop 的每轮 wait 前必须调用它（合流漏重放时这条会红）
_src_sched = open(os.path.join(ROOT, "qoder_scheduler.py"), encoding="utf-8").read()
check("_run_loop 每轮 wait 前调用 _local_keepalive_tick",
      "_local_keepalive_tick()" in _src_sched
      and "_stop_event.wait(15)" in _src_sched
      and _src_sched.index("self._local_keepalive_tick()")
      < _src_sched.index("self._stop_event.wait(15)"))

# ---------------------------------------------------------------- [2] 时区偏移告警
_orig_offset = A._local_utc_offset
_orig_warned = A._TZ_SKEW_WARNED
try:
    A._TZ_SKEW_WARNED = False
    A._local_utc_offset = lambda: datetime.timedelta(0)      # 容器跑在 UTC
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        A.warn_timezone_skew()
        A.warn_timezone_skew()          # 第二次必须静默（进程级一次）
    msg = buf.getvalue()
    check("本地时区非 UTC+8 时告警一次，且给出 TZ 建议",
          msg.count("[qoder]") == 1 and "Asia/Shanghai" in msg, msg)

    buf2 = io.StringIO()
    with contextlib.redirect_stderr(buf2):
        A.warn_timezone_skew(force=True)
    check("force=True 可重放同一告警（诊断/测试用）", "[qoder]" in buf2.getvalue())

    A._TZ_SKEW_WARNED = False
    A._local_utc_offset = lambda: datetime.timedelta(hours=8)
    buf3 = io.StringIO()
    with contextlib.redirect_stderr(buf3):
        A.warn_timezone_skew()
    check("已是 UTC+8 时不告警（不制造噪声）", buf3.getvalue() == "",
          buf3.getvalue())

    A._TZ_SKEW_WARNED = False
    A._local_utc_offset = lambda: None       # 取不到偏移（受限环境）
    buf4 = io.StringIO()
    with contextlib.redirect_stderr(buf4):
        A.warn_timezone_skew()
    check("取不到时区偏移时静默（不影响启动）", buf4.getvalue() == "",
          buf4.getvalue())
finally:
    A._local_utc_offset = _orig_offset
    A._TZ_SKEW_WARNED = _orig_warned

# 启动接线：AccountPool 与 Scheduler 构造时都应调它
_warn_calls = {"n": 0}
_orig_warn = A.warn_timezone_skew
A.warn_timezone_skew = lambda force=False: _warn_calls.__setitem__(
    "n", _warn_calls["n"] + 1)
_orig_active = getattr(A, "_ACTIVE_POOL", None)
try:
    A.AccountPool(tempfile.mkdtemp(prefix="sched12-pool-"))
    check("AccountPool 构造即做时区校验（启动钩子）", _warn_calls["n"] == 1,
          _warn_calls)
    S.Scheduler(_Pool())
    check("Scheduler 构造即做时区校验（启动钩子）", _warn_calls["n"] == 2,
          _warn_calls)
finally:
    A.warn_timezone_skew = _orig_warn
    if _orig_active is not None:
        A._ACTIVE_POOL = _orig_active

# ---------------------------------------------------------------- [3] enabled property
_d = tempfile.mkdtemp(prefix="sched12-state-")
_p = _Pool()
_p.dir = _d
_s = S.Scheduler(_p)
check("Scheduler.enabled 是 property（可拦截赋值）",
      isinstance(S.Scheduler.enabled, property))
check("构造期不回写 state.json（读盘恢复不应触发落盘）",
      not os.path.exists(os.path.join(_d, "scheduler", "state.json")))
_s._state_last_flush = time.time()      # 人为制造「防抖窗口内」
_s.enabled = False
_state_file = os.path.join(_d, "scheduler", "state.json")
with open(_state_file, encoding="utf-8") as _fh:
    _raw = json.load(_fh)
check("开关变更立即落盘（绕过 2s 防抖）", _raw.get("enabled") is False,
      _raw.get("enabled"))
check("内部背书字段是 _enabled 且为 bool",
      _s.enabled is False and _s._enabled is False, _s._enabled)
_s.enabled = True
with open(_state_file, encoding="utf-8") as _fh:
    check("再次切换同样立即落盘", json.load(_fh).get("enabled") is True)

# ---------------------------------------------------------------- [4] Account.save flock
_save_dir = tempfile.mkdtemp(prefix="sched12-save-")
_flock_calls = {"n": 0, "dirs": []}
_orig_flock = A._account_dir_flock


def _flock_spy(directory):
    _flock_calls["n"] += 1
    _flock_calls["dirs"].append(str(directory))
    return _orig_flock(directory)


A._account_dir_flock = _flock_spy
try:
    _acc = A.Account({"uid": "u-save-12", "realm": "cn", "accessToken": "dt-x"})
    _path = _acc.save(_save_dir)
finally:
    A._account_dir_flock = _orig_flock

check("Account.save 落盘走 _account_dir_flock（跨进程互斥）",
      _flock_calls["n"] == 1
      and _flock_calls["dirs"] == [os.path.realpath(_save_dir)],
      _flock_calls)
with open(_path, encoding="utf-8") as _fh:
    check("落盘 JSON 可解析且 uid 正确", json.load(_fh).get("uid") == "u-save-12")
check("tmp 中间文件不残留（原子 replace）",
      not [n for n in os.listdir(_save_dir) if n.endswith(".tmp")],
      os.listdir(_save_dir))
if os.name == "posix":
    check("账号 JSON 0600（含明文 token，不能给同机其他用户读）",
          (os.stat(_path).st_mode & 0o777) == 0o600,
          oct(os.stat(_path).st_mode & 0o777))
else:
    skip("账号 JSON 0600", "Windows 无 POSIX 权限位")

# 并发：多线程写同一账号，每次都要经过 flock，且落盘始终是合法 JSON
_flock_calls["n"] = 0
A._account_dir_flock = _flock_spy
try:
    _threads = [threading.Thread(target=_acc.save, args=(_save_dir,))
                for _ in range(6)]
    for _t in _threads:
        _t.start()
    for _t in _threads:
        _t.join()
finally:
    A._account_dir_flock = _orig_flock
with open(_path, encoding="utf-8") as _fh:
    _final = json.load(_fh)
check("6 线程并发 save：每次都持 flock 且落盘 JSON 合法",
      _flock_calls["n"] == 6 and _final.get("uid") == "u-save-12",
      (_flock_calls["n"], _final.get("uid")))
check("Account.save 临界区同时持 _SAVE_LOCK 与 _account_dir_flock（接线守卫）",
      "with _SAVE_LOCK, _account_dir_flock(base):" in open(
          os.path.join(ROOT, "qoder_accounts.py"), encoding="utf-8").read())

finish()
