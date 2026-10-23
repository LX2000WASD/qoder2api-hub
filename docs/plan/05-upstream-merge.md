# 05 · 上游 v1.7.0 合并/移植策略（89 提交）

> 基线：本地 main = v1.3.4 + `f14031a`（运行时强化）+ `abb38c8`（usage 按天轮转）。
> 上游：remote `upstream` = shuishuipingan/qoder2api-hub，tag `v1.7.0`。
> 所有实验在 /tmp 副本里做（`/tmp/qd-merge-sim`、`/tmp/qd-sim-oursA`、`/tmp/qd-sim-theirsA`、`/tmp/qd-base`、`/tmp/qd-up`），共享仓库未做任何写操作。
> 标注约定：**【验证】** = 本机实跑/实测；**【推断】** = 读码得出未实跑；**【假设】** = 未验证。

---

## 0. 结论摘要（TL;DR）

1. **不要把 `git merge v1.7.0` 当作一次普通合并**：文本层面只有 **8 个冲突文件 / 25 个冲突 hunk**【验证】，但两棵树的**函数契约已经漂移**，自动合并产物是**跑不起来的代码**——`-X ours` 后 12 个套件红、`-X theirs` 后 9 个套件红，而两边纯净基线都是 0 红【验证】。
2. 崩溃样本（不是猜测，是实跑 traceback）：`record_error() got an unexpected keyword argument 'stream'`、`UnboundLocalError: cannot access local variable '_usage_dirty_rows'`、`_install_umid.py` 的 `NameError: record_version` / `UnboundLocalError: candidates`。根因见 §2.1。
3. **推荐方案③（以 v1.7.0 为基线，把本地 2 个提交拆成 5 个补丁重新落）**，而不是方案①/②。理由：冲突不是「文本并集」问题，而是「本地重写了上游同名函数、上游又改了这些函数的契约」——在 v1.3.4 侧解冲突等于逆着上游的语义改；在 v1.7.0 侧重放功能只需动 5 个面。成本 4.5–6.75 人日（§8）。
4. **本地运行时强化在上游 v1.7.0 里完全不存在**【验证】：无 `SIGTERM` 处理器（只有一句注释 `v1.7.0:qoder_proxy.py:10781`）、无 `flock`/leader 锁、无连接上限（`QD_MAX_CONNECTIONS` 不存在）、无 SSE 有界队列（`v1.7.0:qoder_proxy.py:6775 box = _queue.Queue()` 无界 vs `HEAD:4664 maxsize=`）、无按天用量轮转。→ 这部分是本地净增量，**必须 100% 保留**。
5. 更糟的是：**本地这两个提交没有加任何一条测试**。`git grep -lE "SIGTERM|_is_leader|UsageLogChain" HEAD -- tests/` 为空，`tests/` 里唯一提到本地产物的是 `tests/_test_credits_mapping.py:59`（`usage_file_for_day()`）【验证】。→ 合并前必须先补 5–8 个守卫套件，否则回归不可见（§6.2）。
6. **上游客户端身份/队列/风控这批改动值得吃进**（#61/#75/#67/#71/#87/#43/#78/#70），直接对应「被判定虚拟环境 / 排队 10605 / 额度错乱」三类线上痛感。P0：身份对齐 #61/#75、队列信封 #71/#87、credits 打标 #43、可配置排程 #70。
7. **破坏性变更（必须拍板）**：`Dockerfile` 的 `CMD` 在 `0cb4baf`（"ui: adopt wb webui patterns"）改成了 `python qoder_proxy.py --lan`【验证】。`--lan` 会在 `API_KEY` 为空时**强制生成并持久化** Key（`v1.7.0:qoder_proxy.py:10845-10847` → `qoder_settings.ensure_launcher_key`，`v1.7.0:qoder_settings.py:128`），写盘失败直接抛异常（`save()` 无 try/except）→ 与本地「非 root + bind mount」组合会产生**启动即崩**或**客户端全部 401** 两种结局之一。
8. **用量文件的读写契约会断裂**【验证】：本地把它按天切成 `usage-YYYYMMDD.jsonl`（`HEAD:qoder_proxy.py:1122 _UsageLogChain`、写入侧 `HEAD:qoder_proxy.py:671-680`），上游新增的读侧仍 `open(USAGE_LOG)` 直读 `usage.jsonl`（合并后实测 6 处：`_iter_usage_rows_since`、`_iter_usage_rows_forward`、`_usage_log_head_ts`、`quality_snapshot`、`do_GET`×2）。合并后这些面板/质量指标**恒为 0**。
9. **测试体系可以共存**：纯净基线实测——本地 `HEAD` = 20 套件 / 889 checks 全绿；上游 `v1.7.0` = 70 套件 / 1941 checks 全绿；合并后目标 = **71 套件**（本地独有套件只有 `tests/_test_leak_guard.py` 一个）【验证】。CI 则要**二选一**（本地 `docker-publish.yml` 的 test+smoke+publish 与上游 `tests.yml` 的 3×OS/Python 矩阵重复跑），建议保留上游矩阵 + 本地 smoke/publish。
10. 版本号：`HEAD:qoder_proxy.py:58 VERSION = "1.3.4"`（本地提交自称 v1.4 但常量没动），合并后自动变 `1.7.0`；上游 CI 有 `version-assert`（tag 必须等于源码 `VERSION`，`v1.7.0:.github/workflows/tests.yml`），本地发版脚本要跟着改。
11. 额外坑：上游把 `qoder-proxy.pid`（内容 `17836`）**提交进了仓库**（`git cat-file -p v1.7.0:qoder-proxy.pid`）【验证】；合并会把它带进来，必须删除并补 `.gitignore`。

---

## 1. 现状证据（file:line + 代码片段）

### 1.1 两棵树的规模

| 指标 | 本地 `v1.3.4..HEAD` | 上游 `v1.3.4..v1.7.0` |
|---|---|---|
| 文件数 / 增删 | 18 文件，+3607 / −1461 | 120 文件，+22292 / −2069 |
| 提交数 | 2 | **89** |
| 测试套件 | 20 套件 / 889 checks | 70 套件 / 1941 checks |
| 文档树 | `docs/CHANGELOG.md` + `docs/img/*`（5 个） | 根 `CHANGELOG.md` + `docs/*.md`（11 篇）+ 同 4 张图 |
| 新增模块 | 0 | 5：`qoder_updater/webtools/agents/pricing/activity.py` |

【验证】命令：`git diff --stat v1.3.4..v1.7.0 | tail -1`、`git diff --stat v1.3.4..HEAD | tail -1`、三个 /tmp 副本各自 `python3 tests/run_all.py`。

### 1.2 本地独有实现锚点（上游无对应物）

| 能力 | 证据（HEAD） |
|---|---|
| SIGTERM 优雅退出 + 排空 | `qoder_proxy.py:8049-8072`（`_on_sigterm`：先停 listener 再 drain）、`qoder_proxy.py:268 _safe_server_shutdown`、`qoder_proxy.py:5927 wait_connections_drained` |
| 连接上限 / 拒绝策略 | `qoder_proxy.py:5864 max_active_connections = max(8, int(os.environ.get("QD_MAX_CONNECTIONS", ...)))`、`_reject_overloaded_connection`、`qoder_proxy.py:6102` 打点 |
| SSE 背压队列 | `qoder_proxy.py:4623 _SSE_QUEUE_MAXSIZE`（`QD_SSE_QUEUE_SIZE` 默认 16）+ `:4664 box = _queue.Queue(maxsize=_SSE_QUEUE_MAXSIZE)`；心跳/空闲上限在 `sse_with_heartbeat`（上游同名函数但队列**无界**，见 §1.6 清单 B） |
| 跨进程具名锁 flock | `qoder_accounts.py:108 class _NamedFlock`、`:209 _named_flock()`（fcntl.flock + 同线程可重入，Windows 退化 RLock） |
| 调度 leader 锁 | `qoder_scheduler.py:49-50 LEADER_LOCK_NAME = ".leader.lock"`、`:96-99`、`:418-434`、`:523-532`（只有 leader 写 `state.json`） |
| state 防抖批量写盘 | `qoder_scheduler.py:_arm_state_flush / _flush_pending_state`（合并时 8 个 hunk 里的第 3 个） |
| usage 按天轮转 | 写侧 `qoder_proxy.py:671-680` + `qoder_accounts.py:1162 usage_file_for_day()`；读侧 `qoder_proxy.py:1122 class _UsageLogChain`（6 位联读）+ `qoder_accounts.py:1179 _usage_files_for_fold()` |
| 非 root 镜像 | `Dockerfile:80 RUN adduser -D -u 10001 -H qoder`、`Dockerfile:83 USER 10001` |
| 容器内可跑测试 | `Dockerfile` 新增 `COPY tests/ ./tests/` |
| CI 门禁 | `.github/workflows/docker-publish.yml`：`test`(run_all) → `smoke`(uid 10001/模块存在性/导入/`/ping`) → `publish`（仅 tag/手动） |

### 1.3 上游 v1.7.0 关键新增锚点

| 主题 | 锚点 |
|---|---|
| 排程可配置 #70 | `v1.7.0:qoder_scheduler.py:163 _occurrences`、`:187 _next_target`、`:205 _apply_schedule`、`:221 set_schedule`（cron/jitter/到点即补 3h） |
| 信封排队 #71/#87 | `v1.7.0:qoder_proxy.py:5891 _envelope_queue_action`、`:5934 _envelope_queue_wait`、`QD_QUEUE_*` 5 个键 |
| credits 用量打标 #43 | `v1.7.0:qoder_proxy.py:607 CREDITS_USAGE_REFRESHER.mark_used(account)`、`v1.7.0:qoder_accounts.py:3354 def mark_used` |
| 失败行字段 #78 | `v1.7.0:qoder_proxy.py:660 def record_error(model, status, message, elapsed_ms=None, stream=None, account=None, key_id=None)` |
| 流量守卫 #61/#75 | `v1.7.0:qoder_proxy.py:6144 _traffic_guard_mode()`、`QD_TRAFFIC_GUARD` |
| 本地 web 工具 | `qoder_webtools.py`（387 行，唯一模型驱动出网处；默认关，`QD_WEB_TOOLS`） |
| 一键更新 | `qoder_updater.py`（284 行；`REPO = "shuishuipingan/qoder2api-hub"`，`v1.7.0:qoder_updater.py:27`） |
| 计费/流水 | `qoder_pricing.py`（113 行）、`qoder_activity.py`（75 行，`usage/activity.jsonl`） |

### 1.4 合并演练实测（C 项）

演练命令（/tmp 副本，未推送、未动共享仓库）：

    git clone '/home/sixiao/tool/脚本/qoder2api-hub' /tmp/qd-merge-sim
    cd /tmp/qd-merge-sim
    git remote add upstream https://github.com/shuishuipingan/qoder2api-hub
    git fetch upstream --tags
    git merge v1.7.0          # 实测：8 冲突文件 / 25 冲突 hunk

**结果【验证】**：冲突文件 8 个、冲突 hunk 25 个（`grep -c '^<<<<<<< '` 汇总）：

| 文件 | 冲突 hunk | 本地 Δ（v1.3.4..HEAD） | 上游 Δ（v1.3.4..v1.7.0） | 冲突密度 |
|---|---|---|---|---|
| `qoder_proxy.py` | 5 | +722 / −64 | +3909 / −306 | 低（但契约漂移最多） |
| `qoder_scheduler.py` | **8** | +371 / −105 | +239 / −35 | **最高**（8 hunk / 870 行） |
| `qoder_accounts.py` | 1 | +564 / −72 | +925 / −144 | 低 |
| `qoder_tasks.py` | 2 | +23 / −2 | +185 / −35 | 中 |
| `_install_umid.py` | 3 | +38 / −8 | +118 / −44 | 高（两策略都炸） |
| `README.md` | 3 | +537 / −1046 | +73 / −1190 | 两边都是全量重写 |
| `tests/README.md` | 2 | +83 / −106 | +167 / −0 | 文档 |
| `tests/run_all.py` | 1 | +25 / −7 | +81 / −3 | 低 |
| `Dockerfile` | 0（自动） | +23 / −3 | +14 / −2 | 语义需人工确认（§2.5） |
| `docker-compose.yml` | 0（自动） | +39 / −2 | +8 / −1 | 同上 |
| `qoder_settings.py` | 0（自动） | +82 / −13 | +156 / −18 | 同上 |

**rebase / cherry-pick 成本相同**【验证】：`git cherry-pick f14031a`（等价 `git rebase v1.7.0` 的第一步）落在**同一组 8 个文件、同样 25 个 hunk**；第二个提交 `abb38c8` 只动 `qoder_proxy.py` + `tests/_test_credits_mapping.py`，而它改的正是上游 #43/#78 改过的那段（`record_error` / `_persist_usage`）→ 还要再冲突一轮。

**两种「自动驾驶」策略的真实代价**【验证】（/tmp 用 `git merge -X ours/theirs v1.7.0` 实做后跑 `python3 tests/run_all.py`）：

| 树 | 套件 | 通过 | 失败 | checks |
|---|---|---|---|---|
| 纯净 `HEAD`（本地基线） | 20 | 20 | 0 | 889 |
| 纯净 `v1.7.0`（上游基线） | 70 | 70 | 0 | 1941 |
| `-X ours`（冲突全留本地） | 71 | 59 | **12** | 1802 |
| `-X theirs`（冲突全取上游） | 71 | 62 | **9** | 1829 |

### 1.5 上游 89 个提交按主题归类（A 项）

按 `git log --oneline v1.3.4..v1.7.0` + 逐提交 `--name-only` 归类（89 条全覆盖；「价值」是我们侧是否该吃）：

| 主题 | 关键 commit | 影响文件 | 价值 | 理由 |
|---|---|---|---|---|
| 风控与身份（设备码/UA/请求 id/机器身份） | `513254d`(#61)、`7fb9281`(#75)、`76716dc`、`407013e`、`879b336` | `qoder_proxy.py`、`qoder_accounts.py`、`qoder_fingerprint.py`、`qoder_sign.py`、`_install_umid.py`、`docker-compose.yml` | **P0** | 直接对应「被判定虚拟环境/低信誉」；也是 docker 部署的主要痛感来源 |
| 风控：流量守卫 / 遥测 / 排程亲和 | `67aa3ce`、`f420cf6`、`b52a892` | `qoder_proxy.py`、`scripts/risk_diag.py`、`docs/risk-control-*.md` | **P0** | 全池级流量守卫 + 430 提示 + 只读诊断脚本；本地完全没有 |
| 容器启动自检 | `47bdc8c` | `qoder_proxy.py`、`qoder_accounts.py` | **P1** | 时钟偏移/accounts 可写/身份缓存自检——与本地「非 root + bind mount」组合强相关 |
| 模型 catalog / 面板模型下拉 | `203c7d7`(#81)、`3c430cf`(#68)、`6ff2380`(#58) | `qoder_proxy.py`、`qoder_catalog.py`、`dashboard.html` | **P1** | #81 修 `/agents/models` 恒空；#68 模型文案快照；#58 模型缓存 |
| 用量与计费（#43/#78/pricing/activity） | `9308cba`(#43)、`3008819`(#78)、`42de7ea`、`d6dd0d4`、`d6aad81`(#39)、`65f515f`(#67) | `qoder_proxy.py`、`qoder_accounts.py`、`qoder_pricing.py`、`qoder_activity.py`、`dashboard.html` | **P1**（#43/#78 为 P0 级修复） | #43 credits 打标、#78 失败行补字段；insights/pricing 是锦上添花，但与本地按天轮转强耦合（§2.4） |
| 队列与信封（10605/RESOURCE_LIMIT） | `4b0aa4f`(#71)、`1672854`(#87)、`e94aa24`(#51 follow-up)、`6b7f65b` | `qoder_proxy.py`、`dashboard.html`、`tests/_test_issue71_queue_envelope.py` | **P0** | 排队可见 + 快速失败 + 403(10605) 不再当鉴权失败；线上直接可感知 |
| 工具历史与回显（#37/#47/#49/#50/#59/#60/#62/#67） | `f575bbb`(#37)、`df1dd96`(#47/#49)、`887ecd0`(#50)、`7880539`(#59)、`370948e`(#60)、`f45870f`(#62)、`8a42d22`(#63)、`4b0aa4f`(#72)、`16181ae`/`fd7a374`/`5acef5e`(#36/#33) | `qoder_proxy.py`、`qoder_anthropic.py`、`dashboard.html` | **P0** | 流式工具调用 id 边界、DSML 泄漏、图片内容、fail-closed(#37)、残缺回声 |
| anthropic 与 responses | `9576c13`(#55)、`e7dd942`、`5acef5e`、`1672854`(#87 Responses 回读) | `qoder_proxy.py`、`qoder_anthropic.py` | **P0** | system 前缀稳定（命中缓存）、thinking effort 不丢、Responses 回读兼容 |
| 排程可配置 | `4b0aa4f`(#70)、`91f17c2`、`ea7b5e6` | `qoder_scheduler.py`、`qoder_proxy.py`、`dashboard.html` | **P0** | 「整点漏跑=签到无效」的真因修复；与本地 leader 锁**结构性冲突**（§2.3） |
| 更新器 / 诊断 / 停止脚本 | `1d27efa`(一键更新)、`bcbcc82`(#84)、`911124f`/`a16ba19`/`d698e3b`/`dc1d1b5`/`9ec5533`(#89)、`945d1cd`/`31f5712`(POSIX 启动器) | `qoder_updater.py`、`start/stop-qoder-proxy.*`、`allow-firewall.sh`、`qoder_proxy.py` | **P1**（更新器为 **P0 安全项**） | 一键更新会把本地补丁覆盖掉（§5.7）；#89 的 pid 文件与本地 flock 是两套互斥方案，需择一 |
| 本地 web 工具代跑 | `faed22a`（release v1.6.4） | `qoder_webtools.py`、`qoder_proxy.py`、`qoder_settings.py`、`Dockerfile` | **P2** | 默认关、会由模型驱动出网，价值取决于是否用 Codex 类客户端 |
| 智能体一键配置 | `ac87c69`、`36a4c7a`(#53) | `qoder_agents.py`、`dashboard.html`、`Dockerfile` | **P2** | 便利性功能；PR #53 曾因读错 Key 字段发不出请求 |
| 面板 / UI | `0cb4baf`、`2cf8956`(#45)、`40e1e89`、`f7e8aa4`、`8fb4197`、`91f17c2` | `dashboard.html`、`qoder_settings.py` | **P2** | 深色主题、持久会话、签到状态徽标；注意 `0cb4baf` 顺带改了 Dockerfile CMD（§5.5） |
| 测试工程化 | `3b03627`、`57b0e3b`、`9fdcea3`、`a47bd44`、`4d3aa5d`、`dcfcc27`、`ea7b5e6`、`ffb553f` | `tests/*`、`tests/run_all.py`、`scripts/check_clean_checkout.py` | **P0**（免费获得） | 指纹/SUITE fallback/清理校验/时区 fixture，52 个新套件随之而来 |
| 文档重构 | `4622388`(#66)、`e83bc8e`、`f420cf6`、`b52a892` | 根 `CHANGELOG.md`、`docs/*.md` | **无**（照抄即可） | README 1737→103 行，11 篇主题文档；本地重写版与之冲突，建议取上游 |
| 其它缺陷修复 | `1aa467f`、`26fa756`、`7624f91`、`d8795b5`(#83)、`8e66af6`(#76)、`ef900d7`(#90)、`6b7f65b`(#69) | `qoder_proxy.py`、`qoder_accounts.py`、`qoder_scheduler.py` | **P0–P1** | 跨区 failover 死循环、realm 兜底、非对象帧当数据处理、企业账号判定 |

### 1.6 B 项：上游是否也有？两份清单

核对方法：对每项能力在 `v1.7.0` 上 `git grep -n` 目标符号/环境变量名（而非读 README）。

**清单 A：上游已实现 → 本地应对齐或直接丢弃**

| 能力 | 上游证据 | 处置 |
|---|---|---|
| 整点漏跑修复（到点即补 3h） | `v1.7.0:qoder_scheduler.py:187 _next_target / :163 _occurrences` | 丢弃本地 `_tick/_current_slot/all_hours/checkin_hours/keepalive_hours` |
| 「今天已盖章」判据 | `v1.7.0:qoder_accounts.py _checkin_stamped_today()` | 用上游；本地 `last_checkin.startswith(today_utc8())` 只是它的子集（合并时 1 个 hunk） |
| 容器启动自检（时钟偏移/accounts 可写/身份缓存） | `47bdc8c`（`qoder_proxy.py`+`qoder_accounts.py`） | 收下，替换本地 `warn_timezone_skew` 的孤立调用 |
| 启动防双开（pid 文件 + 垂死实例识别） | `911124f`(#89)、`v1.7.0:qoder_proxy.py:10781` 起 | 与本地 flock **功能重叠**：保留 flock（跨进程更硬）或改用 pid 文件，二选一 |
| 套件指纹/SUITE fallback/清理校验/CI 矩阵 | `57b0e3b`、`dcfcc27`、`3b03627`、`tests/_suite_head.py`、`scripts/check_clean_checkout.py` | 全收；本地 `tests/README.md` 的迁移叙述丢弃 |
| 失败行字段（`stream/account/key_id`） | `3008819`(#78)、`v1.7.0:qoder_proxy.py:660` | 用上游签名，本地只保留 dirty 计数逻辑（§5.3） |
| 面板持久签名会话 | `40e1e89`、`v1.7.0:qoder_settings.py panel_session_secret` | 收上游；本地 `panel_default_login_at/_cache_key` 保留为附加字段 |
| 模型文案与 `/agents/models` 修复 | `3c430cf`(#68)、`203c7d7`(#81) | 全收 |
| 平台启动/停止脚本 | `945d1cd`、`31f5712`、`stop-qoder-proxy.sh` | 收上游（Linux/macOS 官方脚本），本地无对应物 |

**清单 B：本地独有 → 必须保留（上游 grep 为空）**

| 能力 | 本地证据 | 上游核对结果 |
|---|---|---|
| SIGTERM 优雅退出 + 排空 | `HEAD:qoder_proxy.py:8049-8072`、`:268`、`:5927` | `git grep -nE "signal\.signal|SIGTERM" v1.7.0 -- qoder_proxy.py` 只命中一句注释 |
| 连接上限 + 快速拒绝 | `HEAD:qoder_proxy.py:5864`、`_reject_overloaded_connection` | `QD_MAX_CONNECTIONS` 在 v1.7.0 全库不存在 |
| SSE 有界队列背压 | `HEAD:qoder_proxy.py:4623 _SSE_QUEUE_MAXSIZE`、`:4664 box = _queue.Queue(maxsize=...)` | 上游 `sse_with_heartbeat` 同名但 `v1.7.0:6775 box = _queue.Queue()` **无界**；`QD_SSE_QUEUE_SIZE` 在 v1.7.0 不存在 |
| 跨进程 flock + leader 锁 + state 防抖 | `HEAD:qoder_accounts.py:108/209`、`HEAD:qoder_scheduler.py:49-50/96-99/418-434` | `git grep -nE "flock|fcntl" v1.7.0 -- '*.py'` 为空；`leader` 也为空 |
| usage 按天轮转 + 6 位链式读 | `HEAD:qoder_proxy.py:1122`、`HEAD:qoder_accounts.py:1162/1179` | v1.7.0 反向：单文件 `usage.jsonl` 直读（§2.4） |
| 非 root 镜像 + 容器内测试 | `HEAD:Dockerfile:80/83`、`COPY tests/` | `git grep -nE '^USER|adduser' v1.7.0 -- Dockerfile` 为空（root） |
| compose 资源/日志/端口收紧 | `HEAD:docker-compose.yml` `mem_limit/cpus/logging/127.0.0.1:8790:8790` | v1.7.0 无这三项 |
| CI 镜像层门禁（uid/模块存在性/导入/`/ping`） | `HEAD:.github/workflows/docker-publish.yml` → `smoke` job | 上游 `tests.yml` 只跑代码测试，无镜像冒烟 |
| 独有套件 | `tests/_test_leak_guard.py` | v1.7.0 `tests/` 无此文件（`comm -23` 唯一项） |
| qodercli 版本溯源 | `HEAD:_install_umid.py record_version()`、`Dockerfile ARG QODERCLI_VERSION` | v1.7.0 无（且合并后 `main()` 会调用它 → 必须一起补，见 §2.2） |




---

## 2. 机制拆解（上游/官方到底怎么判，为什么现在这样做会/不会出问题）

### 2.1 冲突 hunk 数严重低估工作量：四类冲突

1. **同锚点新增（可 union）**：两边在同一位置各加互不相干的功能。例：合并后 `qoder_proxy.py:340` 冲突 —— 本地加 `_safe_server_shutdown()`，上游加 `IS_WINDOWS/START_SCRIPT/port_owner_hint()`。这类占多数，手工 union 即可。
2. **签名漂移（union 会炸）**：`record_error` —— 本地 `HEAD:qoder_proxy.py:789 def record_error(model, status, message, elapsed_ms=None)`；上游 `v1.7.0:qoder_proxy.py:660` 扩到 6 参（`stream/account/key_id`）。冲突块只覆盖**函数体**，`def` 行在非冲突区被自动合并成「本地 4 参签名 + 上游带 `stream=` 的调用点」→ 运行时 `TypeError`（实测 `-X ours` 的 `_test_model_gates` / `_test_anthropic_wiring` 就死在这）。
3. **模块级状态漂移（union 会炸）**：本地引入 `_usage_dirty_rows` 计数器并在 `record_error` 里 `global` + `+= 1`；上游 `record_error` 没有 `global`。`-X theirs` 后函数体里既有本地那行自增、又没有 `global` 声明 → `UnboundLocalError: cannot access local variable '_usage_dirty_rows'`（`/tmp/qd-sim-theirsA/qoder_proxy.py:819`）。**这类 bug 只能靠跑测试发现**，读 diff 看不出来。
4. **结构性重写（必须重写，不能选边）**：`qoder_scheduler.py` —— 本地把「一次巡检」拆成 `_tick / _current_slot / _startup_cycle / _run_checkin_part / _run_credits_part / _run_keepalive_part`（HEAD 独有 14 个符号），上游把排程逻辑换成 `_apply_schedule / _next_target / _occurrences / set_schedule` + cron（v1.7.0 独有 10 个），两边共用 `_run_loop` 的循环体 → 直接 8 个 hunk 对撞。选任一边，另一边**变成死代码**：`-X ours` 后 `_apply_schedule(` 调用点 4→2、`_next_target(` 2→1，`_test_issue70_schedule` 直接 `AttributeError: 'Scheduler' object has no attribute 'checkin_times'`【验证】。

### 2.2 `_install_umid.py`：两边自动合并都会产出不可运行代码【验证】

- `-X ours`：`UnboundLocalError: cannot access local variable 'candidates'`（`/tmp/qd-sim-oursA/_install_umid.py:465`）—— 上游把下载/校验/扫描抽成 `fetch_candidates()`，本地在原 `main()` 里加了 `record_version()`，合并后 `main()` 走了不存在变量的分支。
- `-X theirs`：`NameError: name 'record_version' is not defined`（`/tmp/qd-sim-theirsA/_install_umid.py:485`）—— 本地的调用点被保留、函数体被上游覆盖。

**结论**：该文件必须**整文件择一 + 手工补**，不能逐 hunk 选边。

### 2.3 调度器：唯一正确的解是「保留上游为唯一驱动，把本地能力挂上去」

本地和上游解决的是**同一类问题的两个层次**：

- 上游 #70 解决「整点漏跑」（容器时区 ≠ 宿主时区、巡检跨分钟）→ 到点即补 3 小时窗口 + cron + jitter。
- 本地解决「多实例重复签到/写盘打架」→ leader 锁 + state 防抖 + `_local_keepalive_tick`（每 5 分钟轻量保活）。

正确形态：**上游 `_run_loop`（`v1.7.0:qoder_scheduler.py:309`）为唯一循环**，把本地 `_is_leader` 判定塞进 `_execute_cycle` 与 `_save_state`，把本地防抖写盘保留在 `_save_state` 内部，把 `_local_keepalive_tick` 作为循环里的一个 tick（间隔由上游 `self._stop_event.wait(15)` 决定）。本地 `_tick / _current_slot / all_hours / checkin_hours / keepalive_hours` **整段删除**（被上游 `checkin_times / keepalive_times / _next_target` 取代）。

### 2.4 usage 读写契约断裂（合并后「面板归零」的直接原因）【验证】

- 写入侧只写按天文件：`HEAD:qoder_proxy.py:671-680` → `qoder_accounts.usage_file_for_day()` → `usage-YYYYMMDD.jsonl`；`USAGE_LOG`（`usage.jsonl`）**已不再被写入**（仅作旧名兼容，`HEAD:qoder_proxy.py:414/1109/1678`）。
- 上游新增读侧仍直读旧名：合并后实测 6 处 `open(USAGE_LOG, ...)`：`_iter_usage_rows_since`(:1762)、`_iter_usage_rows_forward`(:1857)、`_usage_log_head_ts`(:1891)、`quality_snapshot`(:5754)、`do_GET`(:9152/:9235)。
- 同时合并后也有 6 处 `_UsageLogChain()`（本地改过的读侧）→ **同一个进程里两套读法并存**，上游新增面板/质量指标读到的永远是空文件。

必须做：把这 6 处换成 `_UsageLogChain()` / `_usage_log_files()`，并把 `_tail_lines(USAGE_LOG, …)`（`v1.7.0:qoder_proxy.py:816`）改成本地已有的多文件实现。

### 2.5 Dockerfile / compose：自动合并「成功」但语义是错的【验证】

合并后的 `/tmp/qd-merge-sim/Dockerfile`：`76:COPY tests/`、`84:USER 10001`、`93:HEALTHCHECK`、`105:CMD ["python","qoder_proxy.py","--lan"]` —— 三件事同时成立：

1. 非 root（本地）+ `--lan`（上游，`0cb4baf` 引入）；
2. `--lan` 且 `API_KEY` 空 → `ensure_launcher_key()`（`v1.7.0:qoder_proxy.py:10845`）→ `qoder_settings.save()` **无异常兜底** → 若 `./accounts` 属主不是 uid 10001（本地 compose 已提示 `chown -R 10001:10001`），**启动即抛异常退出**；
3. 即使写盘成功，本部署从「免鉴权、只监听 127.0.0.1」（本地 `docker-compose.yml` 收紧为 `127.0.0.1:8790:8790`）变成「必须带 Key」——老客户端全部 401。

compose 侧合并结果同样混搭：本地 `127.0.0.1:8790:8790` + `mem_limit` / `logging` + chown 提示，与上游 `hostname:` 提示、PORT 自适配 healthcheck 并存。可用，但**必须显式选一个鉴权策略**。

---

## 3. 上游 v1.7.0 与 Go 参考实现对比

| 维度 | 我们的做法（本地 main） | 上游 v1.7.0 | Go 参考（fengyinxia/qoder2api） | 结论 |
|---|---|---|---|---|
| 优雅退出 | 自实现 `_on_sigterm` + drain（`qoder_proxy.py:8049-8072`、`:5927`） | **无**（`git grep -nE SIGTERM v1.7.0 -- qoder_proxy.py` 只命中注释 `:10781`） | `main.go:1009-1060`：`signal.Notify(SIGINT,SIGTERM)` + `srv.Shutdown(ctx)` + drain 协程 | 本地必须保留；这是行业底线（Go 也有），上游反而缺失 |
| 连接/写超时与背压 | `QD_MAX_CONNECTIONS` 上限 + `sse_with_heartbeat` 有界队列（`HEAD:4623/4664`） | `ThreadingHTTPServer` 裸用（`v1.7.0:qoder_proxy.py:11013`）；有心跳但队列无界（`v1.7.0:6775`） | `main.go:1000-1002` 明确不设 `WriteTimeout`（SSE 需要），靠连接层资源约束（`auth/auth.go:1181`） | 本地实现更细，保留 |
| 跨进程互斥 | `_NamedFlock`（`qoder_accounts.py:108/209`）+ 调度 leader 锁 | 无 flock（`git grep -nE 'flock\|fcntl' v1.7.0 -- '*.py'` 空） | 无（单实例假设，`git grep -nE 'flock\|leader' --include='*.go'` 空） | 本地独有优势，保留；但见 §2.3 的重写要求 |
| 非 root | `Dockerfile:80/83` uid 10001 + chown accounts/usage | **root**（`git grep -nE '^USER\|adduser' v1.7.0 -- Dockerfile` 空） | **root**（`Dockerfile:13 FROM alpine:3.20`、`:21 CMD ["./qoder2api"]`，无 USER） | 保留本地；这也是本地 CI smoke 的第一步断言 |
| 用量持久化 | 按天 `usage-YYYYMMDD.jsonl` + 链式读（`HEAD:qoder_proxy.py:1122`） | 单文件 `usage.jsonl` 直读（`v1.7.0:qoder_proxy.py:410/816/941/1047/1088/…`） | 无 jsonl 流水（`grep -rn jsonl store/` 空；只做内存快照落盘，`store/store.go:843`） | 合并必须做「读写两侧对齐」（§2.4） |
| 模型 catalog | v1.3.4 快照，含 `enable` 字段（`HEAD:qoder_catalog.py:59`） | 同结构 + #68 模型文案快照、#81 `/agents/models` 解包修复 | `models/models.go:238` 读 `m["enable"]` 并过滤（与本项目同构） | `enable` 不是新增点；要的是上游 #81/#68 修复（P1） |
| 队列/信封 | 无 | #71 信封级排队（10605/RESOURCE_LIMIT）+ #87 排队可见/快速失败 | `bridge/handler.go` 有 failover，无信封治理 | 吃上游（P0）：直接影响 429/排队体验 |
| 身份对齐 | 无（v1.3.4 形态：32 位 hex 设备码） | #61 UUIDv4 设备码 + 按区 UA + 随机请求 id；#75 出站头对齐桌面端 0.4.8 / qodercli 1.1.75 | `auth/risk.go`：PAT 池 + 真实 umid helper 的机器身份 | 吃上游（P0）；升级需评估 `QD_DEVICE_ID_LEGACY=1`（§7） |
| 测试体系 | 20 套件 / 889 checks（全绿） | 70 套件 / 1941 checks（全绿，本机 Py3.14 实测） | 19 个 `_test.go` | 以上游为骨架，本地只补 `tests/_test_leak_guard.py` 与新增强化套件 |
| CI | `docker-publish.yml`：test → smoke(uid/模块/导入/`/ping`) → publish | `tests.yml`：3×(OS,Python) 矩阵 + `check_clean_checkout.py` + tag-version 断言 | 无 workflow | 保留上游矩阵 + 本地 smoke/publish，删本地重复 test |
| 文档树 | `docs/CHANGELOG.md`（952 行）+ 单文件 README | 根 `CHANGELOG.md` + `docs/*.md` 10 篇 | `README.md` 单文件 | 取上游结构；本地 CHANGELOG 内容并入根 CHANGELOG（或另存） |
| 自我更新 | 无 | `qoder_updater.py`（`REPO` 硬编码上游，`:27`） | 无 | 吃进但**必须先改 REPO 或默认禁用**（§5.7） |

---

## 4. 方案选项（成本 / 风险 / 收益 / 推荐）

### 方案① `git merge v1.7.0`（保留本地提交，手工解 25 个 hunk）

- 成本：冲突 8 文件 / 25 hunk 的首轮解决 ~2 人日；**但**§2.1 的四类语义冲突要逐函数核对，实测两套自动驾驶都留下 9–12 个红套件，人工至少要再修 3 处「自动合并已损坏」的产物（`record_error`、`_usage_dirty_rows`、`_install_umid.py`）→ 实际 3–4.5 人日。
- 风险：**中高**。三方合并会把本地重写的函数体与上游新增的调用点缝合，产生的 bug 静态阅读不可见（§2.1 第 3 类）。`qoder_scheduler.py` 8 个 hunk 的对撞必然要重写，等于在冲突态里做方案③的活。
- 收益：历史保留两条线，后续 `git merge upstream/main` 的 base 前进到 v1.7.0，冲突面下降。

### 方案② `git rebase` 本地 2 个提交到 v1.7.0

- 成本：与①相同的 25 个 hunk（实测），且要解**两轮**（第二个提交再撞 usage 段）→ 3.5–5 人日。
- 风险：**高**。rebase 把本地功能重放到上游语义之上，但每个提交仍是「大杂烩」，冲突态里被迫同时做「适配上游」和「保留功能」两件事；若本地提交已推到 `origin`（LX2000WASD fork），需要 force-push。
- 收益：线性历史好看，但**没有**降低任何实际难度。

### 方案③ 以 v1.7.0 为基线重建分支 + 选择性移植本地功能（**推荐**）

- 做法：`git switch -c next v1.7.0`，把本地能力拆成 5 个**可独立验证**的补丁依次重放（§5.2），最后 `git merge -s ours main` 记录分叉历史（tree 保持 next，parent 保留本地两个提交），使未来 `git merge upstream/main` 正常可用。
- 成本：**4.5–6.75 人日**（含补 5–8 个本地强化守卫套件、CI 归并、README 取舍、镜像冒烟）。
- 风险：**低-中**。每一步都有干净门禁（纯净 v1.7.0 = 70/70 绿），失败可整段回滚；不需要在冲突态里思考。
- 收益：本地补丁从「2 个 3600 行巨提交」变成 5 个语义单一补丁 → 下次上游发版（v1.8+）冲突面从 8 文件降到 2–3 文件。

### 方案④ 不合并，按特性 cherry-pick 上游

- 成本：要挑 ~20–25 个提交（身份 3、队列 3、credits 2、排程 2、工具回显 8、insights 4、#89/#84 2），每个都可能撞本地重写段 → 3–5 人日，且**不可复用上游测试**（52 个新套件依赖上游代码形态）。
- 风险：**高**。两棵树永久分叉，v1.8 要再来一遍；#87/#70 这类改动横跨 proxy/scheduler/dashboard，cherry-pick 等于做隐性 merge。
- 收益：短期只吃 P0（身份 + 队列）可把风险面压到最小。

### 推荐：方案③，但**分批交付**

- **Batch-1（1 人日）**：`v1.7.0` 基线 + 只重放「非 root + Docker/compose + CI 归并」（不碰 Python 热路径），先拿到能跑的镜像验证部署面。
- **Batch-2（1.5 人日）**：重放「usage 按天轮转」并把上游 6 处直读改为链式读（§2.4）——最容易验证的一块（`tests/_test_credits_mapping.py` 已有部分断言）。
- **Batch-3（2 人日）**：重放「SIGTERM/drain + 连接上限 + SSE 背压」并**先写 4 个套件再改码**。
- **Batch-4（1 人日）**：重放「flock + leader 锁 + state 防抖」，按 §5.4 的形态挂到上游 `_run_loop`。
- **Batch-5（0.5 人日）**：文档（README 取上游、本地独有内容并进 `docs/*.md`）、删 `qoder-proxy.pid`、`.gitignore` 补 `*.pid`、`qoder_updater.py` 的 REPO 改指向 fork。

---

## 5. 落地改动清单（文件级 + 关键补丁草案 + 开关/回滚）

### 5.1 起步（先在自己的分支做，共享仓库不动）

    cd '/home/sixiao/tool/脚本/qoder2api-hub'
    git tag backup/pre-v1.7.0-merge HEAD          # 回滚点 0
    git branch backup/main-local HEAD
    git switch -c next v1.7.0
    # Batch-1..5 逐批提交，每批一个 commit

收尾（记录分叉历史，不改 tree）：

    git merge -s ours main -m "record fork history: local v1.4 runtime hardening lineage"
    # 之后 git log --graph 能看到两条线汇合；未来 git merge upstream/main 正常

### 5.2 文件级处置表

| 文件 | 采取方 | 需手工做的事 |
|---|---|---|
| `qoder_proxy.py` | **上游为骨架** | 重放①`_on_sigterm/_safe_server_shutdown/wait_connections_drained`（HEAD:268/5927/8049）；②`QD_MAX_CONNECTIONS` 上限 + `_reject_overloaded_connection`；③`sse_with_heartbeat` 的有界队列（本地新增的 `_SSE_QUEUE_MAXSIZE` 上游侧不存在，取上游时会被丢掉，必须补回 `maxsize=`）；④`_UsageLogChain`/`_usage_log_files` 并把上游 6 处直读改掉；⑤`record_error` 用**上游 6 参签名**，本地 dirty-row 自增抽成 `_note_usage_row()`（含 `global`） |
| `qoder_scheduler.py` | **上游** | 删本地 `_tick/_current_slot/_startup_cycle/_run_*_part/all_hours/checkin_hours/keepalive_hours`；把 `_is_leader` 注入 `_execute_cycle`/`_save_state`；保留 `_arm_state_flush/_flush_pending_state/_write_state_file_locked`；`_local_keepalive_tick` 挂到 `_run_loop` 每次 wait 前 |
| `qoder_accounts.py` | **上游** | 保留 `_NamedFlock/_named_flock/now_utc8/today_utc8/warn_timezone_skew/usage_file_for_day/_usage_files_for_fold/_InFlightRefresh`；`_checkin_stamped_today` 用上游版（本地 `last_checkin.startswith(today_utc8())` 是它的子集）；把 `CreditsUsageRefresher.mark_used`（v1.7.0:3354）接进记录路径 |
| `qoder_tasks.py` | 上游 | 本地 `skip_until`（停放窗口）与上游 `failed_uids` **并存**：`failed_uids.append(uid)` 与 `skip_until[uid] = now + KEEPALIVE_PARK_SECONDS` 是同一分支的两件事 |
| `_install_umid.py` | **上游整文件** | 把本地 `record_version()`（写 `.qodercli-version`）与 `--version` 参数重新加上；两处调用点都在 `main()`，需与上游 `fetch_candidates()` 顺序对齐 |
| `tests/run_all.py` | 上游 | 保留本地 `--logs` 默认 tempdir 与 `JOBS`；上游 env 隔离（`QD_PROXY_USAGE_DIR`、清代理变量）与 SUITE fallback 全要 |
| `tests/README.md` / `README.md` | 上游 | README 取上游 103 行 + 本地独有章节（运行时强化/Cloudflare/资源上限）写进 `docs/deployment.md` |
| `Dockerfile` | 两边并集 | 保留上游 `COPY` 6 模块 + `HEALTHCHECK`；保留本地 `COPY tests/` + `adduser/USER 10001` + `ARG QODERCLI_VERSION`；**`CMD` 二选一（§5.5）** |
| `docker-compose.yml` | 两边并集 | 保留本地 `127.0.0.1:8790:8790` / `mem_limit` / `logging` / chown 提示；保留上游 `hostname` 与 PORT 自适配 healthcheck |
| `qoder_settings.py` | 上游 | 本地 `_cache_key`/`_mark_default_login` 保留；上游 `web_tools_enabled/set_web_tools_enabled/panel_session_secret` 收下 |
| `.github/workflows/*` | 上游 tests.yml + 本地 docker-publish | 删 `docker-publish.yml` 的 `test` job（与上游矩阵重复），保留 `smoke` 与 `publish` |
| `qoder-proxy.pid` | **删除** | 上游误提交的运行产物（内容 `17836`）；同时在 `.gitignore` 补 `*.pid` |

### 5.3 `record_error` 合并草案（Batch-3）

    # 上游签名（v1.7.0:660），保留全部可选参数
    def record_error(model, status, message, elapsed_ms=None,
                     stream=None, account=None, key_id=None):
        global _usage_dirty_rows          # ← 本地的批量刷盘计数器，必须有
        row = {
            "at": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": model, "error": True, "status": status,
            "message": str(message)[:400], "elapsed_ms": elapsed_ms,
            # 上游 #78：失败行补齐字段（缺省时一个键都不写）
            **({} if stream is None else {"stream": bool(stream)}),
            **({} if account is None else {"account": account}),
            **({} if key_id is None else {"key_id": key_id}),
        }
        _note_usage_row()                 # 本地：dirty 计数 + 到阈值/超时刷盘
        _append_usage_row(row)            # 本地：写到 usage_file_for_day()

### 5.4 调度器移植骨架（Batch-4）

    # 上游 _run_loop（v1.7.0:309）保留为唯一循环；本地能力以钩子形式挂入
    def _run_loop(self):
        time.sleep(10)
        try: self._startup_cycle()            # 本地保留：启动巡检 + mark-before-act
        except Exception as exc: self.log("初次巡检异常: %s" % exc)
        while not self._stop_event.is_set():
            if self.enabled:
                due = self._next_target()      # 上游 #70
                if due is not None:
                    kind, label, ts = due
                    self._fired[ts] = True
                    self._pending_focus = label
                    if self._acquire_leader(): # 本地 leader 锁（新增包装）
                        try: self._execute_cycle("整点排程命中 (%s)" % label, kind)
                        finally: self._release_leader()
            self._local_keepalive_tick()       # 本地：每 5 分钟轻量保活（内部自带节流）
            self._stop_event.wait(15)          # 上游 15s 粒度

`_save_state(force=False)` 内部保留本地三件套：`if not self._is_leader: return` → `_state_dirty=True` → 防抖 → `_write_state_file_locked()`。

### 5.5 `Dockerfile` CMD 三个选项（**需用户拍板**）

- **A（安全默认，推荐公网/隧道部署）**：保留上游 `--lan`，同时在 `.env`/compose 显式写 `API_KEY`（或首启后从 `docker logs | grep -i "api key"` 取回）。→ 鉴权强制开，行为变化明确。
- **B（保持现状）**：改回 `CMD ["python","qoder_proxy.py"]`，配合 compose 的 `127.0.0.1:8790:8790`（本机/隧道场景）。→ 与本地现网一致，零迁移。
- **C（折中）**：`CMD` 无 `--lan`，但 compose 增 `user: "10001:10001"` 与 `API_KEY` 提示；在 `docs/deployment.md` 写清「对外暴露必须设 Key」。→ 推荐给 NAS 用户。

不论哪个，`chown -R 10001:10001 ./accounts ./usage` 必须先执行（本地 compose 已有注释）。

### 5.6 开关与回滚

| 开关 | 用途 | 默认 |
|---|---|---|
| `QD_SCHEDULER_ENABLED=off` | 关调度器（排障） | 开 |
| `QD_MAX_CONNECTIONS` | 连接上限（本地） | 见 `qoder_proxy.py:5864` |
| `QD_SHUTDOWN_GRACE` | SIGTERM 排空窗口（本地） | 见 `qoder_proxy.py:5927` |
| `QD_USAGE_FLUSH_ROWS/SECS` | 本地批量刷盘阈值 | 保持现网值 |
| `QD_DEVICE_ID_LEGACY=1` | 保持旧 32 位 hex 设备码（升级期防「换机」判定） | 迁移窗口设 1，稳定后改 0 |
| `QD_UPDATE_DISABLE=1` | 关一键更新（fork 必须设，§5.7） | **fork 默认 1** |
| `QD_WEB_TOOLS` | 本地 web 工具代跑 | 关（上游默认关） |

回滚点：`backup/pre-v1.7.0-merge`（tag）、每批一个 commit（`git revert` 可单批回退）、镜像保留上一版 tag。

### 5.7 `qoder_updater.py` 必须改（**P0 安全**）

`v1.7.0:qoder_updater.py:27 REPO = "shuishuipingan/qoder2api-hub"` → 面板「一键更新」会下载**上游 release tarball** 并替换 `*.py`（只跳过 `accounts/`、`.git/`、`backups/`、日志/PID，见模块 docstring 第 4 条）→ **一次性抹掉本地全部补丁**。

两种处理（择一）：
1. 改 `REPO = "LX2000WASD/qoder2api-hub"`（fork 自己发 release 才能用）；
2. 保持上游代码，但部署固定 `QD_UPDATE_DISABLE=1`（`v1.7.0:qoder_proxy.py:3127`），面板按钮返回 403（`:10297`）。

---

## 6. 验证方法（能跑的命令 + 预期输出）

### 6.1 三道门禁（每批都必须过）

    # 门禁 1：全套件（60-90s）
    python3 tests/run_all.py --jobs 4 --logs /tmp/suite-logs
    # 实测基线：纯净 v1.7.0 → "70 suites, 70 passed, 0 failed, TOTAL 1941 checks"
    #           本地 HEAD    → "20 suites, 20 passed, 0 failed, TOTAL 889 checks"
    #           合并后目标   → "71 suites, 71 passed, 0 failed, TOTAL >= 1941 checks"

    # 门禁 2：套件不许污染仓库（上游 CI 有此步）
    python3 scripts/check_clean_checkout.py     # 预期：退出码 0，无输出

    # 门禁 3：镜像冒烟（本地 docker-publish.yml 的 smoke 步骤，逐条可复制）
    docker build -t qoder-proxy:verify .
    docker run --rm --entrypoint sh qoder-proxy:verify -c '[ "$(id -u)" = "10001" ] && echo ok'   # ok
    docker run --rm --entrypoint sh qoder-proxy:verify -c \
      'cd /app && for m in $(grep -hoE "^import (qoder_[a-z_]+)" *.py | awk "{print $2}" | sort -u); do test -f "/app/$m.py" || { echo MISSING $m; exit 1; }; done; echo modules-ok'
    docker run --rm --entrypoint python qoder-proxy:verify -c "import qoder_proxy, qoder_anthropic, qoder_updater, qoder_webtools, qoder_agents, qoder_pricing, qoder_activity; print('imports ok')"
    docker run -d --name smoke -p 18790:8790 qoder-proxy:verify && sleep 8 && curl -fsS http://127.0.0.1:18790/ping && docker rm -f smoke

### 6.2 合并后必须重跑的「风险相关验证」（本地功能无现成测试，需手工/新写）

| 验证 | 命令 / 观测 | 通过标准 |
|---|---|---|
| SIGTERM 优雅退出 | `python3 qoder_proxy.py --port 18791 & sleep 5; kill -TERM %1; wait` | 日志出现 `SIGTERM received: stopping listener, draining in-flight requests`（`HEAD:qoder_proxy.py:8059`），退出码 0，无 traceback |
| 排空在飞请求 | 起一个慢 SSE 客户端后发 SIGTERM | 客户端读完当前帧再断，不出现 `RemoteDisconnected` |
| 连接上限 | 并发 200 个 `/ping` | 超 `QD_MAX_CONNECTIONS` 的连接被快速拒（日志有 cap 提示 `HEAD:6102`），进程不 OOM |
| 双实例 leader | 同一 `accounts/`，`--port 18791`/`18792` 各起一个 | 只有一个实例写 `accounts/scheduler/state.json`；另一个显式非 leader 且**不**触发签到 |
| usage 按天轮转 | `QD_PROXY_USAGE_DIR=/tmp/u` 起服务，发 1 次请求 | `/tmp/u/usage-$(date +%Y%m%d).jsonl` 存在且 +1 行；`/usage/daily`、`/usage/analytics`（路由见 v1.7.0:qoder_proxy.py:8912/8960）与 `/panel/status` 的 quality 指标**非 0**（§2.4 的回归门禁） |
| Key/鉴权（若选 A） | 无 Key `curl /v1/models` | 401；带 Key → 200；`docker logs` 有 `api key` 行 |
| 非 root 写盘 | `docker compose up` 后看首个 500ms 日志 | 无 `PermissionError`；否则按 §5.5 先 chown |
| 身份对齐 | `/panel/status` 或 `_diag_gateway.py` | 出站 UA/设备码形态与 `docs/internals.md` 一致；`QD_DEVICE_ID_LEGACY` 行为符合预期 |
| 一键更新 | `QD_UPDATE_DISABLE=1` 下点面板按钮 | 403 `update is disabled`（`v1.7.0:qoder_proxy.py:10297`） |

### 6.3 观测指标（合并后 24h）

- `/usage/*` 与面板「用量洞察」非零（§2.4 验收）；
- 失败行的 `stream/account/key_id` 三列有值（#78）；
- 日志无 `UnboundLocalError` / `TypeError: record_error()` / `NameError`；
- 调度器 `mode` 字段来自上游（含 `checkin_times/cron`），且 `is_leader` 字段仍存在（本地 `status()` 合并项）；
- `docker stats` 内存在 `mem_limit` 之下，`/ping` p99 < 100ms。

---

## 7. 风险与未决问题（需用户拍板）

1. **鉴权默认值**：`--lan`（方案 A）还是保持无 Key + 只听 127.0.0.1（方案 B）？影响所有现网客户端是否要改 Key。
2. **`QD_DEVICE_ID_LEGACY`**：升级到 #61 的 UUIDv4 设备码后，上游可能把存量账号视为「换了设备」。是否先设 `=1` 观察一周再切 0？（【假设】未验证上游服务端的设备变更惩罚强度）
3. **账号目录属主**：现网 `./accounts`、`./usage` 属主是 1000 还是 10001？若非 10001，需停机 `chown`（或改用 `user: "1000:1000"` 覆盖，放弃非 root 收益）。
4. **一键更新**：改 `REPO` 指 fork（要自己发 release）还是直接禁用？两者都会让「面板一键更新」失效一半能力。
5. **README 归属**：取上游 103 行版（信息密度高、但丢本地部署/Cloudflare/资源限制章节），还是保留本地 711 行版（与上游持续冲突）？建议取上游 + 本地内容搬进 `docs/deployment.md`。
6. **本地 CHANGELOG**：`docs/CHANGELOG.md`（952 行）是否并入根 `CHANGELOG.md`？不并则成为新文档树里的孤儿文件。
7. **上游修复 vs 本地补丁的归属**：`#60`（散文分帧）与本地 `tests/_test_leak_guard.py` 的断言语义重叠（`-X ours` 实测该套件红：`[FAIL] B2 散文与 marker 分帧`）。合并后以哪边行为为准？
8. **测试补齐范围**：本地强化目前 0 覆盖。是否接受「Batch-3/4 先写测试再改码」（多 0.5–1 人日），还是「手工验证后补测」（风险自担）？

---

## 8. 工作量估计（人日）与依赖关系

| 批次 | 内容 | 人日 | 依赖 | 验证门禁 |
|---|---|---|---|---|
| B0 | 分支/备份/基线确认（三个纯净树跑一遍记录数字） | 0.25 | 无 | 20/889 与 70/1941 两个基线复现 |
| B1 | v1.7.0 基线 + Docker/compose/非 root + CI 归并 | 1.0 | B0 | 门禁 3（镜像冒烟 uid/模块/导入/`/ping`） |
| B2 | usage 按天轮转重放 + 上游 6 处直读改造 | 1.5 | B1 | 门禁 1 + §6.2「usage 按天轮转」 |
| B3 | SIGTERM/drain + 连接上限 + SSE 背压（**先写 4 个套件**） | 2.0 | B1 | 门禁 1（+4 套件）+ §6.2 前三行 |
| B4 | flock + leader 锁 + state 防抖（按 §5.4 挂到上游循环） | 1.0 | B3 | 门禁 1 + §6.2「双实例 leader」 |
| B5 | 文档/CHANGELOG/pid 清理/updater REPO/`.gitignore` | 0.5 | B1 | 门禁 2（clean checkout） |
| B6 | 端到端回归（真实账号一次 chat/stream/queue/签到） | 0.5 | B2–B5 | §6.2 全表 + §6.3 指标 |
| **合计** | | **6.75**（乐观 4.5，含返工 6.5–7） | | |

对比：方案① 3–4.5 人日 + 之后每次上游发版 1–2 人日冲突成本；方案② 3.5–5 人日；方案④ 3–5 人日且不可持续。**方案③ 的 6.75 人日里有 2.5 人日是「补测试」——这笔钱方案①②④ 也要付，只是付在事故之后。**

---

## 附：本次全部实测命令（可复现）

    # 盘点
    git log --oneline --no-decorate v1.3.4..v1.7.0 | wc -l                     # 89
    git diff --stat v1.3.4..v1.7.0 | tail -1                                   # 120 files, +22292/-2069
    git diff --stat v1.3.4..HEAD    | tail -1                                  # 18 files, +3607/-1461

    # 合并演练（/tmp）
    git clone '/home/sixiao/tool/脚本/qoder2api-hub' /tmp/qd-merge-sim && cd /tmp/qd-merge-sim
    git remote add upstream https://github.com/shuishuipingan/qoder2api-hub && git fetch upstream --tags
    git merge v1.7.0 ; git diff --name-only --diff-filter=U                     # 8 个文件
    for f in $(git diff --name-only --diff-filter=U); do grep -c '^<<<<<<< ' $f; done  # 合计 25

    # 自动驾驶代价（/tmp，两个独立副本）
    git merge -X ours   v1.7.0 && python3 tests/run_all.py    # 71 suites, 59 pass, 12 FAIL
    git merge -X theirs v1.7.0 && python3 tests/run_all.py    # 71 suites, 62 pass,  9 FAIL

    # 基线
    git clone ... /tmp/qd-base && (cd /tmp/qd-base && python3 tests/run_all.py)   # 20 suites, 889 checks, 0 fail
    git clone ... /tmp/qd-up   && (cd /tmp/qd-up && git checkout v1.7.0 && python3 tests/run_all.py)  # 70 suites, 1941 checks, 0 fail

    # 语义校验（示例）
    grep -c 'open(USAGE_LOG' /tmp/qd-sim-theirsA/qoder_proxy.py                  # 6（上游新增读侧）
    grep -c '_UsageLogChain()' /tmp/qd-sim-theirsA/qoder_proxy.py                # 6（本地读侧），两套并存
