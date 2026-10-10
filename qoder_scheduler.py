"""qoder_scheduler.py —— 后台定时调度器 (Scheduler)

负责常驻后台自动执行：
1. 每日签到 (Daily Checkin)：每日 09:00 / 21:00 为所有账号自动签到领积分。
2. 福利包巡检：签到巡检时顺带刷新额度与套餐快照。
3. Token 保活 (Keepalive)：每日 22:00 集中刷新；另对剩余寿命不足 4 小时的
   账号在任意巡检中提前刷新（drt- / jrt- 按 token 前缀路由，PAT 兜底）。
4. 状态持久化与看板展示：暴露状态、执行记录、支持手动立即触发与开关切换。

时区：全部排程/日期按 **UTC+8** 计算（业务窗口所在时区，官方 compose 设了
TZ=Asia/Shanghai，但 K8s/裸 docker 常漏 TZ——按 localtime 排的 09:00 签到
实为北京 17:00、日折叠早于上游 10:00 重置点）。

多实例部署（跨进程互斥）：
  · 签到/额度刷新这类**有副作用**的动作由 leader 锁（accounts/scheduler/
    .leader.lock，fcntl.flock）保证同一时刻只有一个实例执行——否则多个
    实例各自在 09:00/21:00 领取 = 重复领取（风控）；
  · Token 保活是**本地**动作（保持本实例 serve 的凭证新鲜），所有实例都做，
    不参与 leader 竞争；
  · state.json 只有持锁（leader）实例写；日志落盘改为防抖批量（不再每条
    日志都写一次盘）；
  · QD_SCHEDULER_ENABLED=off 可整体关闭调度器（线程不启动）。

漏执行补偿：槽位（日期+整点）级 mark-before-act —— 启动时与每 tick 判
「应执行未执行」的槽位并补跑；整点 tick 卡住、进程停机都不会再丢整轮。
签到与保活使用**独立锁**，避免长巡检阻塞保活。

状态落盘：<账号目录>/scheduler/state.json。
  · 放在账号目录的**子目录**里：AccountPool.load() 只扫顶层 *.json，子目录
    不会被误当成账号；
  · accounts/* 已被 .gitignore 忽略，无需新增忽略规则；
  · 目的：进程重启不再产生重复领取——槽位/启动补签每天最多一次
    （mark-before-act：先打标记落盘再动作，崩溃重启也不会重放）。
"""
import datetime
import json
import os
import sys
import threading
import time

import qoder_accounts
import qoder_tasks
from qoder_tasks import set_logger, run_batch_checkin, run_keepalive

# 状态文件相对账号目录的子路径（避免被 AccountPool 读取为账号 JSON）
STATE_SUBDIR = "scheduler"
STATE_FILE_NAME = "state.json"
# 跨进程 leader 锁文件名（与 state.json 同目录：accounts/scheduler/.leader.lock）
LEADER_LOCK_NAME = ".leader.lock"

# 巡回来由：启动自动 / 整点排程 / 手动触发
CYCLE_STARTUP = "startup"
CYCLE_HOUR = "hour"
CYCLE_MANUAL = "manual"

# 本地保活 tick 节奏：ready() 已不再在请求热路径刷新临期 token（会阻塞用户
# 请求 + 惊群），临期刷新改由这里提前完成（阈值 4h >> 240s，始终早于请求
# 路径需要它的时刻）。
KEEPALIVE_TICK_INTERVAL = 300

# 状态落盘防抖窗口（秒）：日志/状态变更合并批量写盘，不再每条日志写一次。
_STATE_DEBOUNCE = 2.0

# 本地保活失败停放窗口（秒）：避免对坏凭证按 5min 节奏连打上游。
_KEEPALIVE_PARK = 3600


def scheduler_disabled_by_env():
    """QD_SCHEDULER_ENABLED=off/0/false/no -> 整体关闭调度器。"""
    value = (os.environ.get("QD_SCHEDULER_ENABLED") or "on").strip().lower()
    return value in ("off", "0", "false", "no", "disable", "disabled")


class Scheduler(object):
    def __init__(self, pool, state_dir=None):
        self.pool = pool
        # 对齐社区默认排程（UTC+8 24 小时制；见模块 docstring 的时区说明）
        self.checkin_hours = [9, 21]     # 每日 09:00、21:00 签到
        self.keepalive_hours = [22]      # 每日 22:00 集中 Token 保活
        self.all_hours = sorted(set(self.checkin_hours + self.keepalive_hours))
        self._stop_event = threading.Event()
        self._thread = None
        # 签到与保活独立锁：长巡检（签到）不再阻塞保活
        self._checkin_lock = threading.Lock()
        self._keepalive_lock = threading.Lock()
        # 状态目录：优先显式传入（测试）→ 账号池目录 → 环境变量 → 脚本同级 accounts/
        self.state_dir = (state_dir or getattr(pool, "dir", None)
                          or os.environ.get("ACCOUNTS_DIR")
                          or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "accounts"))
        self._state_lock = threading.Lock()
        self._state_dirty = False
        self._state_last_flush = 0.0
        self._state_timer = None
        # 跨进程 leader 锁（ accounts/scheduler/.leader.lock）：持锁实例才执行
        # 签到/额度刷新并写 state.json；同一进程内同线程可重入
        self._leader_flock = qoder_accounts._named_flock(self._leader_lock_path())
        self._is_leader = True           # 单实例默认就是 leader；竞争失败时置 False
        self._last_skip_logged_slot = ""
        # 启动时校验容器时区（业务时序已统一 UTC+8，告警只是提醒）
        qoder_accounts.warn_timezone_skew()
        st = self._load_state()
        self._enabled = bool(st.get("enabled", True))   # 看板开关重启后仍生效
        self.last_run_time = st.get("last_run_time") or None
        self.next_run_time = None
        self.logs = [str(x) for x in (st.get("logs") or [])][-60:]
        # 「启动补签」当日标记：进程重启 N 次，同一天最多补签 1 次
        self.startup_claim_date = str(st.get("startup_claim_date") or "")
        self.last_cycle_date = str(st.get("last_cycle_date") or "")
        # 槽位级漏执行补偿：最后一次派遣的槽位（"YYYY-MM-DD HH"）
        self.last_dispatched_slot = str(st.get("last_dispatched_slot") or "")
        self._last_keepalive_tick = 0.0
        self._keepalive_parked = {}
        self._calc_next_fire()
        # 把任务层失败（死端点、上游结构变化）也打进看板日志。
        set_logger(self.log)
        if st:
            self.log("调度器状态已从 %s 恢复（enabled=%s，上次运行 %s，最近槽位 %s）"
                     % (self._state_path(), self._enabled,
                        self.last_run_time or "-",
                        self.last_dispatched_slot or "-"))

    # -- 属性：开关变更立即落盘 --------------------------------------------
    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)
        # 开关是用户动作：立即（force）持久化，不等防抖窗口
        self._save_state(force=True)

    # -- 状态持久化 ---------------------------------------------------------
    def _state_path(self):
        return os.path.join(self.state_dir, STATE_SUBDIR, STATE_FILE_NAME)

    def _leader_lock_path(self):
        return os.path.join(self.state_dir, STATE_SUBDIR, LEADER_LOCK_NAME)

    def _load_state(self):
        """读状态文件；不存在/损坏都返回 {}（按全新进程处理）。"""
        try:
            with open(self._state_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_state_file_locked(self):
        """状态文件写盘（唯一 tmp 名 + 0600 + 原子 replace）。

        持 multiprocessing 友好：tmp 名带 pid+uuid，多实例并发写也不会互相
        踩出半截 JSON；凭证文件权限 0600（state 含调度历史/账号行为痕迹）。
        必须在 self._state_lock 内调用；**不得**调用 self.log()（递归）。
        """
        path = self._state_path()
        tmp = "%s.%s.%d.tmp" % (path, qoder_accounts.uuid.uuid4().hex[:8],
                                os.getpid())
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {
                "enabled": bool(self._enabled),
                "last_run_time": self.last_run_time,
                "next_run_time": self.next_run_time,
                "startup_claim_date": self.startup_claim_date,
                "last_cycle_date": self.last_cycle_date,
                "last_dispatched_slot": self.last_dispatched_slot,
                "logs": self.logs[-60:],
                "updated_at": qoder_accounts.now_utc8().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
        except Exception as exc:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            try:
                sys.stderr.write("[scheduler] 状态落盘失败: %s\n" % exc)
                sys.stderr.flush()
            except Exception:
                pass

    def _save_state(self, force=False):
        """落盘状态（**防抖批量**：窗口内的多次变更合并为一次写盘）。

        · force=True 立即写（开关变更、补签闸门等 mark-before-act 场景）；
        · 普通日志触发的写最多每 _STATE_DEBOUNCE 秒一次，窗口内挂一个定时
          器兜底（不会再像旧实现那样每条日志都写一次盘）；
        · **只有持锁（leader）实例写**：多实例时 state.json 是共享文件，
          非持锁实例写会 last-write-wins 互相覆盖 leader 的视图。

        注意：本函数**不得**调用 self.log()，否则与 log→_save_state 形成递归。
        """
        with self._state_lock:
            if not self._is_leader:
                return
            now = time.time()
            self._state_dirty = True
            if not force and (now - self._state_last_flush) < _STATE_DEBOUNCE:
                self._arm_state_flush()
                return
            self._state_dirty = False
            self._state_last_flush = now
            self._write_state_file_locked()

    def _arm_state_flush(self):
        """挂一个兜底定时器：防抖窗口结束时把积攒的变更刷下去。"""
        if self._state_timer is not None and self._state_timer.is_alive():
            return
        try:
            timer = threading.Timer(_STATE_DEBOUNCE, self._flush_pending_state)
            timer.daemon = True
            self._state_timer = timer
            timer.start()
        except RuntimeError:
            pass

    def _flush_pending_state(self):
        """定时器/stop 入口：把积攒的状态变更写盘。"""
        with self._state_lock:
            if not self._is_leader or not self._state_dirty:
                return
            self._state_dirty = False
            self._state_last_flush = time.time()
            self._write_state_file_locked()

    def log(self, msg):
        ts = qoder_accounts.now_utc8().strftime("%Y-%m-%d %H:%M:%S")
        entry = "[%s] %s" % (ts, msg)
        self.logs.append(entry)
        if len(self.logs) > 60:
            self.logs = self.logs[-60:]
        # 日志即状态：防抖落盘（看板开关/上次运行时间在重启后仍然可见）
        self._save_state()
        try:
            import qoder_proxy
            qoder_proxy.add_log_entry("[调度器] %s" % msg, tag="scheduler")
        except Exception:
            pass

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if scheduler_disabled_by_env():
            self.log("调度器已被 QD_SCHEDULER_ENABLED=off 整体关闭（线程不启动）")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.log("后台定时调度器已启动")

    def stop(self):
        self._stop_event.set()
        self.log("后台定时调度器已暂停")
        self._flush_pending_state()

    # -- 主循环 -------------------------------------------------------------
    def _run_loop(self):
        # 启动后先休眠 10 秒等待主服务就绪，然后执行初次检查（启动巡检）
        time.sleep(10)
        try:
            self._startup_cycle()
        except Exception as exc:
            self.log("初次巡检异常: %s" % exc)

        while not self._stop_event.is_set():
            self._calc_next_fire()
            try:
                self._tick()
            except Exception as exc:
                self.log("调度循环异常: %s" % exc)
            self._stop_event.wait(30)

    def _startup_cycle(self):
        """启动巡检（保持原有语义：CYCLE_STARTUP 闸门内的补签 + 保活 + 快照）。

        另外把「当前槽位」标记为已派遣 —— 启动巡检已经覆盖了它的补签/保活，
        不标记的话随后的 _tick 会把同一个槽位再派遣一遍。
        """
        slot_key, _slot_hour = self._current_slot(qoder_accounts.now_utc8())
        if slot_key and slot_key != self.last_dispatched_slot:
            self.last_dispatched_slot = slot_key
            self._save_state(force=True)      # mark-before-act
        self._execute_cycle("启动初次初始化巡检", CYCLE_STARTUP)

    def _tick(self):
        """每 30s 一次：本地保活 + 槽位派遣（整点排程 / 漏执行补偿）。"""
        # 1) 本地保活（不参与跨进程 leader 竞争：本实例 serve 的凭证自己负责）
        self._local_keepalive_tick()
        if not self._enabled:
            return
        # 2) 槽位派遣：当前应执行的最近槽位还没派遣过 -> 补跑
        now8 = qoder_accounts.now_utc8()
        slot_key, slot_hour = self._current_slot(now8)
        if slot_key is None or slot_key == self.last_dispatched_slot:
            return
        # 多实例时，持锁的另一个实例可能已经派遣了这个槽位：先从盘上同步一次
        self._sync_slot_from_disk()
        if slot_key == self.last_dispatched_slot:
            return
        # mark-before-act：先把槽位标记落盘再动作；巡检中崩溃也不会重放
        self.last_dispatched_slot = slot_key
        self._save_state(force=True)
        self._execute_cycle("槽位排程/补跑 (%s)" % slot_key, CYCLE_HOUR,
                            slot_hour=slot_hour)

    def _sync_slot_from_disk(self):
        """从 state.json 同步槽位/闸门标记（跨实例可见性）。

        只有持锁实例写 state.json；本实例非持锁时，in-memory 的
        last_dispatched_slot 是旧的 —— 不同步就会重复派遣 = 重复领取。
        """
        st = self._load_state()
        if not st:
            return
        key = str(st.get("last_dispatched_slot") or "")
        if key and key != self.last_dispatched_slot:
            self.last_dispatched_slot = key
        gate = str(st.get("startup_claim_date") or "")
        if gate and gate != self.startup_claim_date:
            self.startup_claim_date = gate

    # -- 槽位（漏执行补偿的核心）--------------------------------------------
    def _current_slot(self, now8):
        """当前「应执行的最近槽位」-> (key, hour)；无排程小时 -> (None, None)。

        例如排程 [09, 21, 22]：14:36 -> ("2026-10-10 09", 9)；
        06:00（早于今日第一个槽位）-> 昨日 22 点。
        """
        if not self.all_hours:
            return None, None
        minutes = now8.hour * 60 + now8.minute
        for h in reversed(self.all_hours):
            if h * 60 <= minutes:
                return "%s %02d" % (now8.strftime("%Y-%m-%d"), h), h
        yesterday = now8 - datetime.timedelta(days=1)
        last_h = self.all_hours[-1]
        return "%s %02d" % (yesterday.strftime("%Y-%m-%d"), last_h), last_h

    def _local_keepalive_tick(self):
        """本地保活 tick：临期 token 提前刷新（不看板日志，避免刷屏）。"""
        now = time.time()
        if now - self._last_keepalive_tick < KEEPALIVE_TICK_INTERVAL:
            return
        self._last_keepalive_tick = now
        if not self._enabled:
            return
        if not self._keepalive_lock.acquire(blocking=False):
            return
        try:
            ka = run_keepalive(self.pool, force=False, quiet=True,
                               skip_until=self._keepalive_parked)
            refreshed, failed = ka["refreshed"], ka["failed"]
            if refreshed or failed:
                self.log("本地保活：刷新 %d 个，失败 %d 个"
                         % (refreshed, failed))
                for line in ka["logs"]:
                    if line.startswith("✓") or line.startswith("!"):
                        self.log(line)
        finally:
            self._keepalive_lock.release()

    def _calc_next_fire(self):
        now8 = qoder_accounts.now_utc8()
        cur_h = now8.hour
        next_h = None
        for h in self.all_hours:
            if h > cur_h or (h == cur_h and now8.minute == 0 and now8.second < 10):
                next_h = h
                break
        if next_h is not None:
            t8 = now8.replace(hour=next_h, minute=0, second=0, microsecond=0)
        else:
            t8 = (now8 + datetime.timedelta(days=1)).replace(
                hour=self.all_hours[0], minute=0, second=0, microsecond=0)
        self.next_run_time = t8.strftime("%Y-%m-%d %H:%M:%S")

    def trigger_now(self):
        """手动立即触发一次调度检查（不受"启动补签当日一次"闸门限制）。"""
        if self._checkin_lock.locked() or self._keepalive_lock.locked():
            return {"ok": False, "msg": "已有巡检正在执行，请稍候再试"}
        threading.Thread(target=self._execute_cycle,
                         args=("手动立即触发", CYCLE_MANUAL),
                         daemon=True).start()
        return {"ok": True, "msg": "已触发后台调度执行"}

    def _execute_cycle(self, trigger_reason="周期巡检", cycle_kind=CYCLE_HOUR,
                       slot_hour=None):
        """一次巡检周期：本地保活 + （leader 才做的）签到/额度刷新。

        签到与保活用**独立锁**：长巡检（签到逐账号 1s+ 间隔）不会阻塞保活。
        签到部分另外持跨进程 leader 锁：多实例时只有一个实例领取，其余
        实例只做本地保活后跳过（每槽位最多提示一次，不刷日志）。
        """
        # ① 本地保活（任何实例都做；不参与 leader 竞争）
        if self._keepalive_lock.acquire(blocking=False):
            try:
                self._run_keepalive_part(trigger_reason, cycle_kind, slot_hour)
            finally:
                self._keepalive_lock.release()
        else:
            self.log("跳过本次保活（%s）：上一轮保活仍在执行" % trigger_reason)

        # ② 签到 + 额度刷新：跨进程 leader 互斥
        if not self._checkin_lock.acquire(blocking=False):
            self.log("跳过本次巡检 (%s)：上一轮仍在执行" % trigger_reason)
            return
        try:
            already_held = self._leader_flock.held_by_current_thread()
            took = already_held or self._leader_flock.acquire(blocking=False)
            if not took:
                self._is_leader = False
                slot_key, _ = self._current_slot(qoder_accounts.now_utc8())
                if self._last_skip_logged_slot != slot_key:
                    self._last_skip_logged_slot = slot_key or "-"
                    self.log("跳过本次签到巡检（%s）：另一实例正在巡检"
                             "（leader 锁被持有，本实例只做本地保活）" % trigger_reason)
                return
            self._is_leader = True
            try:
                self._run_checkin_part(trigger_reason, cycle_kind, slot_hour)
                self._run_credits_part()
            finally:
                if not already_held:
                    self._leader_flock.release()
        finally:
            self._checkin_lock.release()

    def _run_keepalive_part(self, trigger_reason, cycle_kind, slot_hour=None):
        cur_hour = slot_hour if slot_hour is not None \
            else qoder_accounts.now_utc8().hour
        keepalive_due = cur_hour in self.keepalive_hours
        # 1. Token 保活：整点 22:00 全量刷新；其余巡检只刷新临近过期的
        force = keepalive_due
        ka = run_keepalive(self.pool, force=force, quiet=False,
                           skip_until=self._keepalive_parked)
        self.log("Token 保活：刷新 %d 个，失败 %d 个%s"
                 % (ka["refreshed"], ka["failed"],
                    "（22:00 集中保活）" if force else "（临近过期）"))
        for line in ka["logs"]:
            if line.startswith("!") or line.startswith("✓"):
                self.log(line)

    def _run_checkin_part(self, trigger_reason, cycle_kind, slot_hour=None):
        now8 = qoder_accounts.now_utc8()
        self.last_run_time = now8.strftime("%Y-%m-%d %H:%M:%S")
        self.last_cycle_date = now8.strftime("%Y-%m-%d")
        self.log("开始执行任务 (%s)..." % trigger_reason)
        if not self.pool or not self.pool.accounts:
            self.log("暂无可用的活跃账号，跳过本次巡检")
            self._save_state(force=True)
            return

        cur_hour = slot_hour if slot_hour is not None else now8.hour
        checkin_due = cur_hour in self.checkin_hours
        allow_complement = self._allow_complement_checkin(cycle_kind)

        # 2. 每日签到：整点签到窗口内执行；其余巡检只补签未签账号
        #    （能力运行时探测：接口不存在的区域由 run_checkin 给出原因并跳过）
        targets = [a for a in self.pool.accounts
                   if a.enabled and a.access_token]
        if checkin_due:
            pending = targets          # 09:00 / 21:00 窗口不受启动闸门影响
        elif allow_complement:
            pending = [a for a in targets if a.can_checkin()]
        else:
            pending = []               # 当日已补签：不发起任何领取
        checkin_count = 0
        earned = 0
        if pending:
            self.log("检测到 %d 个账号需要签到，执行自动签到..." % len(pending))
            res = run_batch_checkin(pending, gap=1.0, inter_gap=1.0)
            checkin_count = res["accounts_count"]
            earned = res.get("credit_added") or 0
            for line in res["logs"]:
                if line.startswith("✓") or line.startswith("!"):
                    self.log(line)
        elif checkin_due:
            self.log("所有账号今日已签到")

        self.log("巡检完成：签到 %d 个，本次新增积分 +%d"
                 % (checkin_count, earned))
        self._save_state(force=True)

    def _run_credits_part(self):
        # 3. 刷新额度快照（看板积分卡片依赖）
        if not self.pool:
            return
        targets = [a for a in self.pool.accounts
                   if a.enabled and a.access_token]
        for acc in targets:
            try:
                acc.fetch_credits()
            except Exception:
                pass
            time.sleep(0.5)

    def _allow_complement_checkin(self, cycle_kind):
        """启动巡检的补签闸门：**同一天最多补签一次**（其余巡回来由不受限）。

        为什么需要：启动巡检会对 can_checkin() 为真的账号发真实领取请求，
        进程反复重启就会反复领取。这里做 mark-before-act——先把当日标记落盘，
        再去做领取；即使随后进程崩溃/被杀，重启后也不再重放。
        can_checkin() 依赖的 per-account lastCheckin（落盘在账号 JSON）是第二道闸。
        标记的读-改-写与巡检周期共用同一把跨进程 leader 锁：本实例正在巡检
        （已持锁）时直接写；否则尝试获取锁，拿不到说明另一实例正在巡检
        （补签交给它），拿到则在锁内完成「读-改-写」再释放。
        """
        if cycle_kind != CYCLE_STARTUP:
            return True
        today = qoder_accounts.today_utc8()
        if self.startup_claim_date == today:
            return False
        already_held = self._leader_flock.held_by_current_thread()
        took = already_held or self._leader_flock.acquire(blocking=False)
        if not took:
            return False        # 另一实例正在巡检：补签交给它
        try:
            self.startup_claim_date = today
            self._save_state(force=True)
        finally:
            if not already_held:
                self._leader_flock.release()
        return True

    def status(self):
        return {
            "enabled": self._enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "is_leader": self._is_leader,
            "mode": "整点排程 (09:00/21:00 签到 · 22:00 Token 保活，UTC+8)",
            "mode_cn": "整点排程 (09:00/21:00 每日签到 · 22:00 Token 保活，UTC+8)",
            "mode_intl": "整点排程 (09:00/21:00 每日签到 · 22:00 Token 保活，UTC+8)",
            "last_run_time": self.last_run_time or "尚未运行",
            "next_run_time": self.next_run_time or "待调度",
            "startup_claim_date": self.startup_claim_date or "",
            "last_cycle_date": self.last_cycle_date or "",
            "last_dispatched_slot": self.last_dispatched_slot or "",
            "state_file": self._state_path(),
            "leader_lock": self._leader_lock_path(),
            "logs": self.logs[-20:],
        }
