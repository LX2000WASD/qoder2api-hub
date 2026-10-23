# -*- coding: utf-8 -*-
"""usage 按天轮转守卫（本地 abb38c8/f14031a，上游 v1.7.0 只有单文件）。

    python tests/_test_usage_rotation.py

背景：本地把 usage 流水按天切成 usage-YYYY-MM-DD.jsonl（UTC+8 计日），
读侧改成「旧 usage.jsonl + 全部日文件」联读（_UsageLogChain /
_usage_log_files），并把上游新增的 6 处直读 open(USAGE_LOG) 全部改走链。
这是合流时最容易「静默归零」的一处：写侧切了、读侧没跟，面板/质量指标
全为 0 而进程不报错。断言：

  ① 写侧：record_usage 落到 usage-<UTC+8 今天>.jsonl，不再写旧文件；
  ② 文件名日界来自 qoder_accounts.today_utc8（单一时钟，UTC+8）；
  ③ 读侧跨天联读：_UsageLogChain 能读到前一天的日文件；
  ④ 旧 usage.jsonl 仍在读取链路里（迁移期兼容）；
  ⑤ usage_daily 跨文件汇总不重不漏；
  ⑥ usage_file_for_day 异常时写入回退旧文件（迁移期兜底）；
  ⑦ fold_daily_usage（签到侧日折叠）读得到轮转后的当天/昨天文件；
  ⑧ 日折叠候选文件有界（只读当天 + 前 N-1 天，不扫全目录）。
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *  # noqa: F401,F403

_tmp = tempfile.mkdtemp(prefix="qd-usage-rotation-")
os.environ["QD_PROXY_USAGE_DIR"] = os.path.join(_tmp, "usage")
os.environ["ACCOUNTS_DIR"] = os.path.join(_tmp, "accounts")

P, A = bootstrap()

declare_suite(__file__, ["qoder_proxy.py", "qoder_accounts.py"])

today = A.today_utc8()
_yest_dt = datetime.datetime.strptime(today, "%Y-%m-%d") - datetime.timedelta(days=1)
yesterday = _yest_dt.strftime("%Y-%m-%d")
old5 = ( _yest_dt - datetime.timedelta(days=4)).strftime("%Y-%m-%d")


def _day_name(day):
    return os.path.basename(A.usage_file_for_day(day=day))


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _lines(path):
    return [ln for ln in _read(path).splitlines() if ln.strip()]


def _row(at, account, total, iso=None, error=False):
    row = {"at": at, "iso": iso or time.strftime("%Y-%m-%dT%H:%M:%S"),
           "model": "Qwen3.8-Flash", "realm": "cn", "account": account,
           "stream": False}
    if error:
        row.update({"error": True, "status": 500, "message": "boom"})
    else:
        row.update({"prompt_tokens": max(0, total - 1), "completion_tokens": 1,
                    "total_tokens": total, "credit": 0.0})
    return row


def _append(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _clear_daily_cache():
    with P._daily_lock:
        P._daily_cache.clear()


try:
    udir = P.USAGE_DIR
    check("写侧 USAGE_DIR 与 accounts.usage_dir() 同源（读写同一目录）",
          os.path.abspath(udir) == os.path.abspath(A.usage_dir()),
          (udir, A.usage_dir()))

    clock = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    expect_day = clock.strftime("%Y-%m-%d")
    day_file = A.usage_file_for_day(directory=udir)
    check("轮转文件名 = usage-<UTC+8 今天>.jsonl",
          os.path.basename(day_file) == "usage-%s.jsonl" % expect_day,
          os.path.basename(day_file))
    check("轮转文件名落在 USAGE_DIR 下",
          os.path.abspath(os.path.dirname(day_file)) == os.path.abspath(udir))

    print()
    print("[1] 写侧：record_usage 落到按天文件，不写旧 usage.jsonl")
    row = P.record_usage(
        "Qwen3.8-Flash",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "credits": 0.01},
        stream=False)
    legacy = P.USAGE_LOG
    check("record_usage 返回写入的行（含 realm）",
          isinstance(row, dict) and row.get("realm") == "cn", row)
    check("按天文件已创建", os.path.isfile(day_file), day_file)
    check("按天文件恰好 1 行", len(_lines(day_file)) == 1, len(_lines(day_file)))
    check("旧 usage.jsonl 未被写入（迁移期只读）", not os.path.isfile(legacy),
          _read(legacy)[-200:])
    content = _lines(day_file)[0] if _lines(day_file) else "{}"
    parsed = json.loads(content) if content else {}
    check("行内容正确（total_tokens=15）", parsed.get("total_tokens") == 15,
          parsed.get("total_tokens"))

    print()
    print("[2] 日志文件链：旧文件在前 + 日文件按日升序")
    files = [os.path.basename(p) for p in P._usage_log_files()]
    check("链里包含按天文件", os.path.basename(day_file) in files, files)
    check("链首位仍是旧 usage.jsonl（迁移期兼容读）",
          files[0] == "usage.jsonl", files)

    print()
    print("[3] 跨天联读：_UsageLogChain 能读到前一天的文件")
    yfile = A.usage_file_for_day(day=yesterday, directory=udir)
    _append(yfile, _row(time.time() - 86400, "acct-yest", 2,
                        iso=yesterday + "T10:00:00"))
    check("前一天日文件已创建", os.path.isfile(yfile), yfile)
    chain_rows = []
    with P._UsageLogChain() as fh:
        for line in fh:
            line = line.strip()
            if line:
                chain_rows.append(json.loads(line))
    check("链式读取跨天：读到昨天 + 今天共 2 行", len(chain_rows) == 2,
          [r.get("account") for r in chain_rows])
    check("链里能读到昨天那行的账号",
          any(r.get("account") == "acct-yest" for r in chain_rows),
          [r.get("account") for r in chain_rows])

    print()
    print("[4] 旧 usage.jsonl 仍在读取链路（迁移期兼容）")
    _append(legacy, _row(time.time(), "acct-legacy", 3))
    chain_rows2 = []
    with P._UsageLogChain() as fh:
        for line in fh:
            if line.strip():
                chain_rows2.append(json.loads(line))
    check("链里同时读到旧文件（3 行）", len(chain_rows2) == 3,
          [r.get("account") for r in chain_rows2])

    print()
    print("[5] usage_daily 跨文件汇总不重不漏")
    _clear_daily_cache()
    daily = P.usage_daily(days=2, group="model", realm="all")
    check("2 天窗口合计 = 今天 15 + 昨天 2 + 旧文件 3",
          daily["totals"]["total_tokens"] == 20,
          daily["totals"]["total_tokens"])
    check("请求数 = 3（三条明细各一行）",
          daily["totals"]["requests"] == 3, daily["totals"]["requests"])
    _clear_daily_cache()

    print()
    print("[6] 写侧兜底：usage_file_for_day 异常时回退旧文件")
    before = len(_lines(legacy))
    orig = A.usage_file_for_day

    def _boom(*_a, **_kw):
        raise RuntimeError("rotation unavailable")

    A.usage_file_for_day = _boom
    try:
        P._persist_usage(_row(time.time(), "acct-fallback", 4))
    finally:
        A.usage_file_for_day = orig
    after_lines = _lines(legacy)
    check("回退写进了旧 usage.jsonl（+1 行）", len(after_lines) == before + 1,
          (before, len(after_lines)))
    check("回退行内容正确",
          json.loads(after_lines[-1]).get("account") == "acct-fallback",
          after_lines[-1][:160] if after_lines else None)

    print()
    print("[7] 写侧时钟：文件名跟随 accounts.today_utc8（UTC+8 计日）")
    orig_clock = A.today_utc8
    A.today_utc8 = lambda: "2031-02-03"
    try:
        P._persist_usage(_row(time.time(), "acct-clock", 1,
                              iso="2031-02-03T00:00:00"))
    finally:
        A.today_utc8 = orig_clock
    shifted = os.path.join(udir, "usage-2031-02-03.jsonl")
    check("换日时钟后写入落到 usage-2031-02-03.jsonl", os.path.isfile(shifted),
          sorted(os.listdir(udir)))

    print()
    print("[8] 签到侧日折叠能读到轮转后的文件（今天 / 昨天）")
    _append(day_file, _row(time.time(), "acct-rot", 15, iso=today + "T12:00:00"))
    fold_today = A.fold_daily_usage()
    check("fold 今天：读到今天日文件的行", fold_today["rows"] >= 1,
          fold_today.get("rows"))
    check("fold 今天：acct-rot 的 token 合计正确",
          int(fold_today["tokens"].get("acct-rot") or 0) >= 15,
          fold_today["tokens"])
    fold_yest = A.fold_daily_usage(day=yesterday)
    check("fold 昨天：读到昨天日文件", fold_yest["rows"] == 1,
          (fold_yest.get("rows"), fold_yest.get("tokens")))
    check("fold 昨天：账号 token 正确",
          int(fold_yest["tokens"].get("acct-yest") or 0) == 2,
          fold_yest["tokens"])

    print()
    print("[9] 日折叠候选文件有界：不扫全目录（5 天前的文件不读）")
    ofile = A.usage_file_for_day(day=old5, directory=udir)
    _append(ofile, _row(time.time() - 5 * 86400, "acct-old", 999,
                        iso=old5 + "T10:00:00"))
    check("5 天前的日文件确实存在（不是没造出来）", os.path.isfile(ofile))
    # 候选窗口是「相对请求的那一天」：old5 + 前 N-1 天，绝不能把 5 天后的
    # 今天/昨天文件也扫进来（否则日志越滚越慢）。
    cands = [os.path.basename(p) for p in A._usage_files_for_fold(old5)]
    check("候选文件数有界（<= USAGE_FOLD_DAYS + 旧文件）",
          len(cands) <= A.USAGE_FOLD_DAYS + 1, cands)
    check("候选包含请求当天自己的文件", _day_name(old5) in cands, cands)
    check("候选不含今天/昨天的文件（不扫全目录）",
          _day_name(today) not in cands and _day_name(yesterday) not in cands,
          cands)
    check("USAGE_FOLD_DAYS 有界且 >= 1",
          isinstance(A.USAGE_FOLD_DAYS, int) and A.USAGE_FOLD_DAYS >= 1,
          A.USAGE_FOLD_DAYS)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

finish()
