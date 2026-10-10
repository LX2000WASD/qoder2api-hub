# tests/ —— 套件化测试

## 怎么跑

    python tests/run_all.py              # 全部套件（并发，一行一套件）
    python tests/run_all.py guards       # 只跑名字含 guards 的
    python tests/run_all.py dash         # 只跑前端 .js 套件
    python tests/run_all.py -h           # 帮助
    python _test_qoder.py                # 老入口仍然有效（兼容）

编排：`ThreadPoolExecutor` 并发（`QD_TESTS_JOBS` 控制，默认 4），
每套件写独立日志文件（`<tmp>/qd-suites-*/`），失败打印 tail 25 行，
子进程强制 `PYTHONIOENCODING=utf-8`，无匹配套件返回 2（防「0 passed / 0 failed → exit 0」的假绿）。

## 现有套件（20 个 / 889 checks，含 4 个前端 .js）

| 套件 | checks | 覆盖 |
|---|---|---|
| `_test_legacy.py` | 629 | 仓根 `_test_qoder.py` 的完整内容（[1]–[44] 段，一条不丢） |
| `_test_anthropic_bridge.py` | 40 | `/v1/messages` 桥接：入站映射、流式事件流、usage 守恒、effort clamp |
| `_test_offline_tools.py` | 31 | `_install_umid.py` 提取链路（格式/架构识别、integrity、幂等） |
| `_test_identity_cache.py` | 21 | 机器身份落盘缓存：命中/自愈/TTL/RESET/多数表决 |
| `_test_dashboard_limits_auth.js` | 19 | 前端：403/401 分流 + 限额保存载荷形状 |
| `_test_dashboard_effort.js` | 19 | 前端：档位三态渲染 + Key 列四态 |
| `_test_model_gates.py` | 16 | 模型闸门三入口共用、白名单/禁用、空配置放行 |
| `_test_dashboard_log_and_badge.js` | 15 | 前端：日志增量追加 + 护栏四态徽标 |
| `_test_failure_families.py` | 13 | 退避/熔断/降权三族接线 6 处与防误接反例 |
| `_test_anthropic_wiring.py` | 12 | 守卫链接线：x-api-key、形态决策单点、allowed_names |
| `_test_analytics_parity.py` | 9 | 逐行审计列与汇总视图计数一致性（by_key == by_account） |
| `_test_credits_mapping.py` | 10 | usage credits（复数优先）与聚合 |
| `_test_guards.py` | 10 | 护栏核心不变量（等号边界 / fail-open / free 豁免 / 全关短路） |
| `_test_leak_guard.py` | 10 | 泄漏回读管线端到端（5 形态 + 3 误伤用例，镜像内可跑） |
| `_test_dashboard_render.js` | 10 | 前端渲染纯函数四态 + toast 配色 |
| `_test_auth_matrix.py` | 5 | 面板鉴权矩阵（401 / 200-面板会话 / 403 语义与 401 分离） |
| `_test_credit_expiry.py` | 6 | 额度到期字段的防御性透传 |
| `_test_proxy_invariant.py` | 3 | 带凭证请求必须走统一出站函数（直连 urlopen 基线登记） |
| `_test_keepalive.py` | 2 | 超长请求行快速拒绝 + 同连接复用 |

> 数字为最近一次全量跑的实测（`SUMMARY: 20 suites, 889 checks, 0 failed`）；
> `_test_qoder.py` 单独跑为 629 checks / 0 failed / 4 skipped（fixture 缺失时 SKIP，退出码不受影响）。

## node 跳过行为（修后口径）

`.js` 套件用 `shutil.which("node")` **在编排层判定** node 是否存在：

- **不在子进程里让它自己抛**：`ThreadPoolExecutor` 里任何一个子进程抛
  `FileNotFoundError` 都会经 `ex.map` 传播、**整个编排直接退出** —— .js 套件归零，
  连 .py 套件的结果都打印不出来（alpine 镜像无 node，老实现就是这个下场）；
- 判定缺失 → 该套件返回 `None` → 编排层打印 ``[SKIP]    checks=-（node 未安装，.js 套件自动跳过）``，
  SUMMARY 行的 `skipped` 计数 +1，**退出码不受影响**；
- 装了 node 的环境（开发机 / CI）则照跑，没有「装了 node 却被跳过」的情况。

⚠️ 这是对旧文档承诺的更正：此前的「`run_all.py` 会自动发现并跳过 node 缺失的环境」
**在实现层并不成立**（老的 `command()` 直接返回 `["node", path]`，让 subprocess 自己抛）。

## 迁移策略（legacy 作终态）

**目标不是「迁完 44 段」，而是「测试结构持续变好、每一步都不带风险」。**

- `_test_qoder.py` 的 [1]–[44] 段**整体由 `_test_legacy.py` 承载并保持全绿**（629 条），
  不再追求逐段拆完 —— 这是**在用的测试文件**，不是技术债；
- **新代码的测试一律走新套件**（`_test_*.py` / `.js`）；legacy 自然冻结；
- 判定依据：`python tests/_tools/dep_scan.py` 列出每段定义的全局名与后续引用；
  实测 **55 个段跑完 `--leaf-only` 没有任何输出 —— 不存在纯叶子段**
  （段间共享显式全局如 `time`/`base64`，也有隐式供后续段继承的绑定，
  与三轮回滚的现象完全一致）；
- 三次回滚各暴露一个坑，已内建进流程：① **迁移与修 bug 不混在同一次编辑**；
  ② **共享头部显式抽出**（`_suite_head.py`）；③ **段可能有全局副作用**（搬走让后续段 NameError，
  而**新套件单跑是绿的** → legacy 只在整体跑时才暴露）。

前端套件的做法是**自读 `dashboard.html` + 抽 script 块 + 内联约 15 行元素桩**，
没有引入 jsdom（守住零依赖红线）；`tests/_tools/dash_extract.js` 锚点缺失即抛，
防「切片拿到空串、断言照样通过」这种假绿。工作包里提到的 wb `_dom_stub.js` 在 wb 里并不存在。

## 观测点原则（来自 C3 那五轮的教训）

**判据要拦在对的那一层**：说「没有发上游」就要拦在**网络层**（`http_json` 桩），
而不是「`open_upstream` 是否被调用」—— 后者在正常路径上本来就会被调用（它内部才选号）。

## 迁移期纪律（每批新套件都要过一遍）

### 1. 路径：一律基于 `__file__`，绝不用 cwd

套件在 `tests/` 下跑（`run_all.py` 就是 `cwd=tests`），而仓根文件在上一层。
统一用 `_suite_head.ROOT`：

    _src = open(os.path.join(ROOT, "qoder_proxy.py"), encoding="utf-8").read()

（`_test_offline_tools.py` 踩过这个：用 `__file__` 当仓库根，`cwd=tests/` 时全错；已验证两种跑法都绿。）

### 2. 依赖：段里用到的模块必须能在共享头部找到

段落在 legacy 里可能靠「前面某段顺手 import」才拿到 `hashlib` 之类，独立成套件就会 NameError。
`_suite_head.py` 已把常用标准库模块级 import 并放进 `__all__`。

### 3. 隔离：每个套件自己的临时目录、不共享模块级全局

- 临时文件一律 `tempfile.mkdtemp(prefix="qd-<套件名>-")`，`finally` 里 `rmtree`；
- 需要改模块级全局（`P.USAGE_LOG` / `P.POOL` / `P.API_KEY` …）时**必须** try/finally 恢复；
- **不要写仓库内的共享路径**（`usage/` / `accounts/` / `umid/`）—— 并发编排下那是跨进程共享的，
  是最典型的「单跑绿、并发红」来源；
- 需要独占某个全局资源的套件单独串行跑：`QD_TESTS_JOBS=1 python tests/run_all.py`，
  或 `python tests/run_all.py <套件名片段>`。

### 4. 环境变量在并发下是安全的（但别依赖）

`run_all.py` 每个套件跑在**独立子进程**里，`os.environ[...]` 的读写不会跨套件串味；
但**同一进程内**的多个断言互相影响仍要防（改完要恢复）。

### 5. 「单跑绿、并发红」的排查顺序

1. 先跑 `QD_TESTS_JOBS=1 python tests/run_all.py`（串行）—— 若串行也红，那是套件自身问题；
2. 串行绿、并发红 → 查上面的第 3 条（共享路径/全局）；
3. 还是找不到 → 跑两遍 `run_all.py` 对比日志（`<tmp>/qd-suites-*/`），看失败是否稳定复现；
4. 历史教训：某次 `run_all` 出现 1 failed，追查为**撞上并发编辑的中间态**（不是回归）——
   **并行开发期的瞬时红要先排除中间态再当回归**。

## 「改坏 → 必红」自证（节选）

变异只在**备份副本**上做（`%TEMP%` 下先备份、跑完立即还原，原文件不留痕）：

- **guards**：`reserve_blocked` 的 `<=` 改成 `<` → `[FAIL] reserve 含等号` → RED exit 1；
- **proxy_invariant**：往 `qoder_accounts.py` 追加一处直连 `urlopen`（基线断言 == 3）→ RED exit 1；
- **dashboard_render**：`|| earned > 0` 改成 `>= 0` → 签到结果与 toast 配色两条 FAIL → RED exit 1；
- **credits_mapping**：改回只读单数 `credit` → 必红；把 `billable` 塞进聚合表 → 必红；
- **identity_cache**：把 `QD_MACHINE_IDENTITY_RESET` 的清种子去掉 → 轮换路径必红；
- **leak_guard**：把 `recover_leaked_tool_calls` 退化成原样 → 重复输出与泄漏形态立刻复现。
