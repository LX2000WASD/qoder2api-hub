# Qoder2API-Hub 诊断与优化总纲（定稿 v1）

> 日期：2026-10-23 · 基线：main `abb38c8`（上游 v1.3.4 + 2 个本地提交）
> 上游：已加 remote `upstream` = shuishuipingan/qoder2api-hub，fetch 至 `v1.7.0`（ef900d7，89 个提交）
> 参考仓库：`/home/sixiao/tool/脚本/_refs/Qoder-2API-Go`（EchoPing07/Qoder-2API-Go，fengyinxia/qoder2api 的 Go 重写）
> 本文件由 Lead 汇总并逐条复核；四个专题 + 合流专题见 `docs/plan/01..05-*.md`

**文档地图**

| 文件 | 内容 | 作者 |
|---|---|---|
| [00-analysis-and-plan.md](00-analysis-and-plan.md) | 本总纲：结论、路线图、待拍板 | Lead |
| [01-prompt-kernel.md](01-prompt-kernel.md) | 官方提示词/请求体瘦身 + 内核伪装边界 | prompt-kernel-analyst |
| [02-vm-identity.md](02-vm-identity.md) | 虚拟机检测、UMID 身份、Docker 环境 | vm-identity-analyst |
| [03-model-admin.md](03-model-admin.md) | 模型启用管理 + 密钥/接口治理 | model-admin-analyst |
| [04-headers-params.md](04-headers-params.md) | 出站标头矩阵、参数透传、缓存 | header-param-analyst |
| [05-upstream-merge.md](05-upstream-merge.md) | v1.7.0 合流/移植策略与演练 | upstream-merge-analyst |

---

## 0. 结论摘要（TL;DR）

1. **这次"拉取上游"不是 fast-forward，而是跨 89 个提交的合流**：本地 2 个提交（+3607/−1461 行）与上游 89 个提交（`qoder_proxy.py` 8121→11066 行）都从 v1.3.4 出发。**直接 `git merge v1.7.0` 只会得到 8 个冲突文件 / 25 个 hunk，但自动合并产物跑不起来**：实测 `-X ours` 12 套件红、`-X theirs` 9 套件红（两棵纯净基线全绿）。
2. **推荐合流方式：以 v1.7.0 为基线重建分支，把本地 2 个提交拆成 5 个补丁重放**（收尾 `git merge -s ours main` 记历史）。4.5–6.75 人日。理由不是"冲突多"，而是本地重写了上游同名函数、上游又改了这些函数的契约，在旧基线上解冲突等于逆着上游语义改。
3. **本地 v1.4 运行时强化（SIGTERM 排空 / SSE 背压 / 连接上限 / flock / 调度 leader 锁 / usage 按天轮转 / 非 root 镜像）上游完全没有**——这是净增量，必须 100% 保留；但它们**零测试覆盖**，合流前必须先补 5–8 个守卫套件。
4. **Q1（官方提示词）用户前提有一半不成立**：模板工具表 44,260 B **任何组合都不上行**；官方 system 12,747 B（≈3,081 tokens）只在"客户端没传有效 system 且 `--system-prompt` 为默认"时上行。真正的污染是**行为**：写死 `Platform: windows / D:/Projects/Qoder / Today's date: <no value>`，并要求用 TodoWrite/Bash，而 `tools=[]` 时这些工具根本不存在。
5. **Q1 推荐方案 A+：加 `QD_MINIMAL_BODY` 开关（约 10 行）**，开启时模板 `messages/tools` 置空、官方 prompt 彻底不入正文；默认 0 保现状，灰度稳定后默认 1。**删 `baseprompt.json` 文件是二期**（只值 59.6 KB 镜像体积 + IP 暴露，换来的是回滚能力丢失）。
6. **Q1「伪装内核」的门槛在认证层与客户端指纹层，不在 prompt**：COSY 签名覆盖 body，`cosy-*` 头 / UA / 设备身份决定"像不像官方客户端"。GitHub 上的 Go 参考实现 `newRequestBody()` 完全不发官方 prompt（`bridge/chat.go:96-136`），且本仓在客户端带 system 时**早已整体替换官方 prompt**——如果上游强校验官方 prompt，Claude Code / Codex 路径早就全线失败。
7. **Q2（虚拟机检测）主要矛盾判断错位**：`runtime-info` 不是本地探测器，而是阿里 UMID 的服务端注册客户端——它联网 `pum.m.alibaba.com:443`，`machineToken` 由服务端签发、缓存在 `$HOME/.config/.locale_cfg`（350 B）；断网+无缓存直接 exit=1。`vmInfo` 是**本地打分且不上行**，我们的出站头里没有任何 vmInfo 字段。
8. **Q2「Docker 必被判虚拟机」成立，「极低信誉」不成立**：本机是物理笔记本（Lenovo Legion，DMI 无虚拟化厂商），组件仍返回 `isVm=true / KVM / 85%`，因为 `lsmod` 里有 `kvm_amd`；官方 Windows 客户端开 VBS 时同理。且 Go 实现在 Docker 里**一个机器头都不发**，照样能领每日 100 Credits。
9. **Q2 真正要修的是三件小事**：容器内 `HOME` 不可写（`Dockerfile:80` `adduser -H`）导致组件每次重新注册；出口 IP（intl 走数据中心 = 排队主因，上游风控手册原话"出口 > 身份 > 调度 > 整形"）；时钟漂移（`cosy-date` >120 s 直接 401）。**不要做 DMI/VM 伪装。**
10. **Q3（模型管理）现状是"单点闸门 + 黑名单 + 每 Key 白名单"，缺全局启用白名单与管理面**；`/v1/models` 不按 Key / 不按 `enabled` 过滤。**硬 bug：面板保存任何 Key 都会静默清空该 Key 的白名单**（前端不回传 `models` + 后端 `item.get("models")` → None → `[]`），**上游 v1.7.0 同一个 bug**，升级修不掉。止血 1.5–2 人日，完整方案 6.5–8 人日。
11. **Q4（标头/参数）标头必须逐字照抄 v1.7.0**：本地仍是 v1.3.4 形态（`cosy-version=1.1.64`、UA `Go-http-client/2.0`、`cosy-data-policy=AGREE`、缺 `cosy-business-*`、缺 `cosy-machineos/hostname`）。**本机抓包证明我们实际是 HTTP/1.1 + `Connection: close` + 无 ALPN，UA 却自称 Go http2——自相矛盾的指纹**。参数侧 `temperature/top_p/stop/seed/n/parallel_tool_calls` 被**静默丢弃**（比 400 更坑）。
12. **Q4 唯一有量化损失的项是缓存**：上游 #55 实测 Anthropic 侧把 messages 内 system 上提合并会使长会话前缀命中率 **92% → 14.7%**，本地 `qoder_anthropic.py:122-142` 仍是旧形态——**优先级高于标头**。
13. **顺序**：P0 止血（Q3 bug + 本地强化补测试 + 合流演练）→ P1 基线重建到 v1.7.0（吃 #61/#75/#55/#71/#87/#43）→ P2 差异化（minimal body、模型矩阵、UMID 可持续性、参数灰度）。合计 **18–25 人日**；只做止血轨 **4–6 人日**。

---

## 1. 基线与证据

### 1.1 仓库拓扑

```text
v1.3.4 ──┬── ae31046 (origin/main, GitHub LX2000WASD/qoder2api-hub)
         │
         ├── f14031a  v1.4 运行时强化      ← 本地独有，2 提交 +3607/−1461 行
         ├── abb38c8  usage 按天轮转        ← 本地独有（HEAD）
         │
         └── 89 commits ── v1.7.0 (upstream: shuishuipingan/qoder2api-hub, ef900d7)
```

- `git merge-base main v1.7.0` = `v1.3.4`；本地领先 2、落后 89。
- `baseprompt.json` 三方 blob 完全相同（`2d6de81c`）：**上游从未动过这份官方提示词**。

### 1.2 实测基线（`python3 tests/run_all.py`，2026-10-23 本机复跑）

| 树 | 套件 | 通过 | 失败 | checks | 结论 |
|---|---:|---:|---:|---:|---|
| 本地 HEAD（纯净） | 20 | 20 | 0 | 889 | 基线全绿 |
| 上游 v1.7.0（纯净） | 70 | 70 | 0 | 1941 | 上游全绿 |
| `merge -X ours` 产物 | 71 | 59 | **12** | 1802 | 跑不起来 |
| `merge -X theirs` 产物 | 71 | 62 | **9** | 1829 | 跑不起来 |

崩溃样本（实跑 traceback）：`TypeError: record_error() got an unexpected keyword argument 'stream'`（本地 4 参 `qoder_proxy.py:789` vs 上游 6 参 `:660`）、`UnboundLocalError: _usage_dirty_rows`、`_install_umid.py` 两种选边各爆 `NameError`/`UnboundLocalError`。

### 1.3 团队分工（5 个并行分析 agent，均为只读 + 独立产出）

| Agent | 任务 | 产出 |
|---|---|---|
| prompt-kernel-analyst | 提示词/请求体/内核伪装 | 01-prompt-kernel.md（461 行） |
| vm-identity-analyst | 虚拟机检测/UMID/容器 | 02-vm-identity.md（553 行） |
| model-admin-analyst | 模型启用/接口治理 | 03-model-admin.md（558 行） |
| header-param-analyst | 标头矩阵/参数/缓存 | 04-headers-params.md（583 行） |
| upstream-merge-analyst | 合流演练与策略 | 05-upstream-merge.md（469 行） |

工作区零污染：`git status` 仅 `?? docs/plan/`；合流实验全部在 `/tmp` 副本完成。

---

## 2. 上游 v1.7.0：能白拿什么、哪些会咬人

### 2.1 白拿清单（按优先级）

| 优先级 | 能力 | 锚点 |
|---|---|---|
| **P0** | 出站身份对齐（UUID 设备码 / 随机 request id / 按区 UA / 头集合） | `513254d` #61、`7fb9281` #75、`407013e` |
| **P0** | `cosy-machineos` + `cosy-machinehostname`（仅 infer-sse） | #64，`QD_COSY_OS_HEADERS` |
| **P0** | Anthropic system 前缀稳定（缓存命中 92%→14.7% 的修复） | `9576c13` #55 |
| **P0** | 信封层排队治理（10605 等待而非报错） | `e94aa24` #51、`4b0aa4f` #71、`1672854` #87 |
| **P1** | 模型清单磁盘缓存 + `/models/refresh`、`?merge_realms=1`、自动换区 `X-Qoder-Realm` | #58、#77 |
| **P1** | 用量洞察（成本估算 `qoder_pricing.py`）、失败行补字段（#78）、credits 打标（#43） | `42de7ea`、`3008819`、`9308cba` |
| **P1** | 可配置排程（#70）、风控遥测与 `/health risk`、`scripts/risk_diag.py` | `91f17c2`、`76716dc` |
| **P2** | 网关侧 web 工具（`qoder_webtools.py`）、一键更新、企业徽标、停止脚本、macOS/Linux 启动脚本 | `faed22a`、`1d27efa`、`ef900d7` |

### 2.2 破坏性变更（合流时必须逐条处理）

1. **`Dockerfile` CMD 在 `0cb4baf` 改成 `--lan`** → 无 `API_KEY` 时强制 `ensure_launcher_key` 生成并落盘，写盘失败直接抛异常；与本地"非 root + bind mount"组合会得到"启动即崩"或"客户端全 401"两种结局之一。
2. **usage 读写契约断裂**：本地按天写 `usage-YYYYMMDD.jsonl`，上游新增读侧仍直读 `usage.jsonl`（合并后实测 6 处）→ 面板/质量指标恒 0。
3. **`req_realm` 语义变化（#77 自动换区）**：本地"跨区错配 → 400"会失效，变成自动换区——Q3 的闸门改造必须在这一版语义上重写。
4. **`qoder_updater.py:27 REPO = "shuishuipingan/qoder2api-hub"`**：一键更新会把整个上游覆盖进来，**冲掉全部本地补丁**。必须改 REPO 或 `QD_UPDATE_DISABLE=1`。
5. **上游误提交 `qoder-proxy.pid`（内容 17836）**：合流会带进来，需删除并补 `.gitignore`。
6. **版本号常量与 CI**：`VERSION = "1.3.4"`（本地自称 v1.4 但常量没动）；上游 CI 有 tag==VERSION 断言。
7. **文档树冲突**：上游 README 103 行 + `docs/*.md` + 根 `CHANGELOG.md`；本地 README 711 行 + `docs/CHANGELOG.md`（+952 行）。

---

## 3. Q1：官方提示词与「伪装内核」

### 3.1 事实：污染在哪一层（实测，非推断）

| 组合（同一 53 字符用户消息） | 上行 messages | 说明 |
|---|---:|---|
| 无 system / 无 tools | 14,446 B | 官方 prompt **12,747 B** 入场 |
| 无 system / 1 tool | 14,633 B | 同上 + 客户端工具 |
| 有 system / 无 tools | 1,771 B | 官方 prompt 被整体替换 |
| 有 system / 1 tool | 1,958 B | 同上 |

- 模板 `tools` 44,260 B **任何组合都不上行**（无客户端工具时置 `[]`，有则整体替换，`qoder_proxy.py:4020-4026`）。
- 官方 system 只在 `system_text is None` 分支写入（`qoder_proxy.py:3928-3942`）；`--system-prompt` 为默认值时才回落到模板。
- 坑：客户端传 `system: ""` **反而拿到**官方 prompt（falsy 回落）；传 `system: " "` 反而能压掉（现成应急开关）。

### 3.2 真问题不是体积，是行为

模板 system 里写死：`Working directory: D:/Projects/Qoder`、`Platform: windows`、`OS Version: MINGW64_NT-10.0-22631`、`Today's date: <no value>`；并强制"用 TodoWrite 管理任务 / 用 Bash"，而 `tools=[]` 时这些工具不存在。对短请求，这段 prompt 占上下文 60%–98%；对 120k 字符长会话只占 9.3%。

### 3.3 推荐：`QD_MINIMAL_BODY` 开关（A+），不是删文件

- 改动约 10 行：导入期预生成 `BASEPROMPT_MINIMAL`（`messages=[]`、`tools=[]`，骨架 23 键不变），`build_qoder_body` 首行按开关选模板；启动日志打印当前模式。
- 默认 `0`（现网行为不变）→ 影子统计（`prompt_src / prompt_chars / tools_n / body_bytes`）→ 灰度 → 下一版默认 `1`。
- 回滚：改环境变量重启，无需回滚代码。
- 二期 B：内置 1,198 B 骨架常量、删 `baseprompt.json` 与 Dockerfile COPY。收益仅 59.6 KB + 减少 IP 暴露，**不急于做**。
- 零代码应急：`--system-prompt " "`（`""` 无效）。

### 3.4 「伪装内核直连」的可行边界

- **能做的**：以协议字段 + COSY 签名 + 客户端指纹伪装成官方 CLI 调用（我们已经在做）；请求体可以不含官方 prompt（Go 参考实证）。
- **不能做的**：绕过 430 客户端版本硬门控；绕过活动链路的真机器身份；绕过模型级队列（10605 换号无效）。
- **证据强度**：上游无 prompt 校验的迹象（强推断），但服务端是否在 `agent_common` 路径另注入 prompt、是否对 prompt 做指纹，**未验证**，需影子统计 + 小流量 A/B。

### 3.5 风险与工作量

亲和键 `msgs[:2]` 变化会一次性换号、缓存冷启动；模型行为回归（拒答/风格）；credits 差额受前缀缓存影响无法静态推断。**0.5–1 人日**（含测试 `tests/_test_prompt_budget.py`）。

---

## 4. Q2：虚拟机检测与 Docker 环境

### 4.1 事实修正（这是本轮最强的一次认知纠偏）

| 断言 | 结论 | 证据 |
|---|---|---|
| `runtime-info` 是本地探测器 | **错**：它是阿里 UMID 的**联网注册客户端** | `strace` connect `pum.m.alibaba.com:443`；`strings` 含 `sgsdk`/`pum`；断网+无缓存 `exit=1` |
| `machineToken` 是硬件哈希 | **错**：服务端签发的不透明 token，缓存在 `$HOME/.config/.locale_cfg`（350 B） | 删缓存后 token/type/code 全换、`vmInfo` 不变；换 HOME/cwd/argv 不影响（走 passwd home） |
| vmInfo 会上行、决定信誉 | **错**：本地打分，出站头里没有 vmInfo | `qoder_accounts.py:2012-2020` 与抓包 |
| 物理机 = 不是 VM | **错**：本机物理笔记本仍 `isVm=true / KVM / 85%` | 组件 `exec lsmod`，本机有 `kvm_amd` |
| Docker = 极低信誉 | **未证实**：Go 实现在 Docker 里一个机器头都不发，仍能领每日 100 | Go `README.md:245`、`auth/risk.go:3-21` |

**影响面**：只有 `/sash` 活动平台（campaigns/reward/claim）用真身份；**推理用派生身份、每日签到用普通 headers、额度同**。`README.md:449` 说"签到也用真身份"与代码冲突，以代码为准。

### 4.2 Docker 的真实短板（按影响排序）

1. **出口 IP**：intl 在数据中心直连 = 排队主因（上游风控手册："出口 > 身份 > 调度 > 请求整形"，"数据中心 IP 无法用协议头伪装成住宅 IP"）。
2. **时钟**：`cosy-date` 漂移 >120 s 直接 401；容器第一优先级是 NTP。
3. **UMID 身份可持续性**：`Dockerfile:80` 的 `adduser -H` 不建 home，而组件按 `/etc/passwd` 找 home → 容器里 `.locale_cfg` 写不进去 → **每次调用重新注册**，只靠 `accounts/machine_identity.json` 兜底。修法三行：`HOME=/app/home` + 挂卷 + compose 固定 `hostname`。
4. **设备码形态**：本地派生 32-hex；上游 #61 改 UUIDv4 形态（破坏性：对上游等于换设备），并留 `QD_DEVICE_ID_LEGACY=1` 回退。

### 4.3 方案矩阵

| 方案 | 做法 | 收益 | 风险 | 结论 |
|---|---|---|---|---|
| ⑤ 不伪装（主线） | 保持真组件 + 稳定缓存 + 出口/时钟 | 零新增风险 | 无 | **推荐** |
| ① 可持续性 | HOME/hostname/网络可达 pum + 启动自检可视化 | 消除重复注册 | 低 | **推荐** |
| ② shim 灰度 | 用自定义可执行文件替换组件输出 | 可伪造 vmInfo（但不上行） | 服务端可能反查 token（未验证） | 可选，默认关 |
| ③ 自造 machineToken | 完全本地生成 | 无 | 活动链路被判非官方，静默丢活动 | 不推荐 |
| ④ DMI/VM 伪装 | 改容器 DMI/MAC | 无 | 无收益（vmInfo 不上行） | **不做** |

### 4.4 结论与工作量

**不做 DMI/VM 伪装**；主线 ⑤+① **1.5–2.5 人日**；含 shim 灰度研究 3.5–4.5 人日。建议先花半天做 T1+T5（容器内 `/diag/vm` + token 年龄 + 出口 ASN 观测），拿到硬数据再决定是否投 shim。
安全事实：组件会把容器 DMI/MAC/cgroup 画像上报给阿里 UMID；服务端按机器指纹做"人"级去重（`SAME_PERSON_ALREADY_CLAIMED`），复制 `machine_identity.json` 到多机不可行。

---

## 5. Q3：模型启用管理与对外接口治理

### 5.1 现状（以代码为准）

- 闸门已收敛到单点 `_model_precheck`（`qoder_proxy.py:6240-6249`），chat/responses/anthropic 三入口共用（7608/7375/7248）：顺序 = 跨区错配 → 全局封禁 `banned_models` → 每 Key 白名单 `api_keys[].models`。
- **没有全局启用白名单**；官方 catalog 的 `enable` 只透传成 `/v1/models` 的展示字段，不参与闸门。
- `/v1/models` 不按 Key 过滤、不按 `enabled` 过滤，并暴露 `upstream_key`。
- 面板只有"模型库展示"，没有启用/停用、没有按 Key 授权 UI。

### 5.2 三个硬缺陷

1. **数据丢失**：面板保存任何 Key 都会清空该 Key 的模型白名单（`GET /settings` 不回传 `models` → 前端 payload 无 `models` → 后端 `item.get("models")`=None → `[]`）。**上游 v1.7.0 同 bug**。
2. **别名不归一**：闸门按客户端原始字符串匹配，`Qwen3.8-Max` / `qwen3.8-max` / `qmodel_38max` 判定不同（归一发生在闸门之后的 `open_upstream`）。
3. **错误语义不可机读**：拒绝一律 400 且 `error.code` 是数字；OpenAI 生态期望 `404 model_not_found` / `403 permission_denied` + 字符串 code。

### 5.3 方案与工作量

- **方案 B（止血，1.5–2 人日）**：修保存丢字段 + 别名归一 + `/v1/models` 按 Key/启用过滤 + 面板回显。
- **方案 A（完整，6.5–8 人日）**：统一模型注册表 overlay（qoder_key/显示名/别名/realm/enabled/per-key/能力元数据）+ 单点 `model_gate` + 看板"模型矩阵" + 错误码 + 迁移与测试矩阵（G9–G16）。
- 不建议照抄 Go 的"catalog 抽取时硬过滤 `enable=false`"——官方 `enable` 随账号/套餐变化，硬过滤会把"当前套餐没开"误当"模型不存在"。

### 5.4 上游可白拿（无管理面）

磁盘缓存 7 天 + `/models/refresh`（#58）、`?merge_realms=1`、`realm/realm_exclusive`、#77 自动换区、#68 `QD_OFFICIAL_TEXT`（容器必需）、#81 `/agents/models`、`/pricing`。注意：上游 `docs/configuration.md:61` 自认"看板中没有这项设置的入口"。

---

## 6. Q4：反代标头与参数

### 6.1 头矩阵差异（推理/模型列表）

| 字段 | 本地（v1.3.4 形态） | 上游 v1.7.0 | 官方形态 | 判定 |
|---|---|---|---|---|
| `user-agent` | `Go-http-client/2.0` | `undici` | Electron/undici | **必改**（最大脚本指纹） |
| `cosy-version` | `1.1.64` | `1.1.75`（可 `QD_COSY_VERSION`） | 1.1.75 | **必改** |
| `cosy-data-policy` | `AGREE` | `agree` | agree | **必改** |
| `cosy-business-product/type/scene` | 缺失 | `cli/agent/assistant` | 有 | **必改** |
| `cosy-machineos` | 缺失 | 全部请求带 | 有 | **必改** |
| `cosy-machinehostname` | 缺失 | 仅 SSE | 有 | **必改** |
| `cosy-machineid` | 32-hex | UUIDv4 形态 | UUIDv4 | 建议（带 legacy 开关） |
| `cosy-clientip` / `identity` / `login-version` / `x-model-*` | — | 相同 | 相同 | **不要动** |

补充证据：本机 TLS 探针 `selected_alpn_protocol()==None`、请求行 HTTP/1.1、wire 有 `Connection: close`——**UA 自称 Go http2 与线上事实自相矛盾**（更明显的脚本指纹）。
账号侧：CN 应 `QoderWork/1.1.x`、INTL 应 `Qoder/1.1.x`（本地一律 `CLIENT_UA`，要改 4 处）；活动/签到头要**减集**（本地 `desktop_headers()` 继承了 `Content-Type/X-Request-ID/X-Machine-ID/X-Session-ID/Origin/Referer`，官方桌面端不发）。

### 6.2 参数：静默丢弃比 400 更坑

`temperature / top_p / stop / seed / n / response_format / logit_bias / parallel_tool_calls / stream_options` 三入口都解析，但 `build_qoder_body` 只写 `max_tokens` 与 `reasoning_effort`——客户端以为采样参数生效，其实没发。Go 的做法是透传 9 键让网关当裁判（它自己承认"上游是否接受未验证"）。
**必须保留的本地校验只有四类**：`reasoning_effort` 档位归一（上游对不支持档位静默忽略）、`max_tokens` 类型/别名翻译、工具形态降级（custom freeform→function）、图片/VL 与体积闸。

### 6.3 缓存：唯一有量化损失的一项

上游 #55 实测：Anthropic 侧把 messages 内的 system 上提合并 → 长会话前缀命中率 **92% → 14.7%**。本地 `qoder_anthropic.py:122-142` 仍是旧形态。**先做这个，再做标头**（标头改动不影响前缀缓存）。

### 6.4 分档与工作量

- 必改 4（照抄上游）：头集合、删两处 UA 覆写、UUID 设备码（带 legacy）、活动头减集 + 按区账号 UA。
- 建议 4（开关化）：参数透传 `QD_PARAM_PASSTHROUGH`（先探针 `temperature` 是否 400）、`n!=1` 显式拒绝、#55 前缀稳定、按模型 `max_tokens` 兜底。
- 不要动 6：COSY 签名与 identity 9 键、`cosy-clientip` 固定值、`accept-encoding: identity`、`login-version: v2`、`x-model-source`、`business.version=0.1.43`。
- 工作量：独立 **6.8 人日**；若合流已包含 #61/#75/#55 则 **3.8 人日**。

---

## 7. Go 参考仓库（EchoPing07/Qoder-2API-Go）对标

### 7.1 架构差异

| 维度 | 本仓（Python） | Go 参考 |
|---|---|---|
| 协议入口 | Chat / Responses / Anthropic **三入口** | 仅 `/v1/chat/completions` + `/v1/models` |
| 请求体 | 外部 `baseprompt.json` 模板（59.6 KB，条件注入 prompt） | 结构体内联最小体，**不发官方 prompt/tools** |
| 账号 | 双区 OAuth+PAT、亲和、四道额度护栏、调度器 | PAT 池、权重轮转、余量阈值、粘性 |
| 模型 | catalog 三源 + `upstream_key/aliases`/能力/峰谷价；闸门黑名单+每 Key 白名单 | catalog 动态加载 + 硬过滤 `enable=false`；无 per-key 授权 |
| 密钥 | id/name/realm/enabled/models（但 UI 丢字段） | id/key/note（无模型授权） |
| 身份 | 派生 machineId + 活动链路真 UMID + 缓存 | 仅真 UMID（checkin），拿不到就不发任何机器头 |
| 观测 | usage.jsonl（按天）+ Prometheus + 看板 | stats + 请求日志环形缓冲 + 看板 |
| 部署 | Docker/Windows/LAN/Compose | Docker/单二进制/macOS launchd |
| 测试 | 20 套件 889 checks（合流后 71 套件） | 约 8k 行 Go 单测 |

### 7.2 值得借鉴（3 点）

1. **最小请求体**：证明"不带官方 prompt 也能稳定跑"——Q1 的直接实证。
2. **失败姿态**：拿不到真身份时**宁可不发**机器头（发派生值会被判非官方、静默丢活动）——Q2 的保守策略依据。
3. **参数 verbatim + 网关当裁判**：配合本地只保留四类必要校验——Q4 的透传方向。

### 7.3 不要照抄（3 点）

1. `enable` 硬过滤（套餐维度 ≠ 模型不存在）。
2. 只有两入口（我们三入口是差异化）。
3. 无 per-key 授权/无模型管理面（正是 Q3 要补的）。

---

## 8. 路线图

### P0 止血（4–6 人日）— 不动架构，先修可确定的错

| # | 任务 | 依据 | 验收 |
|---|---|---|---|
| P0-1 | 修"面板保存清空每 Key 白名单"（前端带上 `models`、`GET /settings` 回显、后端保留） | 03 §5 | 面板保存前后白名单不变 + 单测 |
| P0-2 | `/v1/models` 按 Key/启用过滤 + 别名归一进闸门 | 03 §5 | G9–G11 套件 |
| P0-3 | 给本地 2 个提交补 5–8 个守卫套件（SIGTERM/背压/连接上限/flock/leader/按天轮转） | 05 §6.2 | 新套件全绿，且故意破坏时能红 |
| P0-4 | 合流演练 + 决策落地（见 §10 决策 1） | 05 | `/tmp` 演练记录 + 方案签字 |

### P1 基线重建（5–7 人日）— 吃上游，别自己造

| # | 任务 | 依据 | 验收 |
|---|---|---|---|
| P1-1 | 以 v1.7.0 为基线建分支，按 5 个补丁重放本地强化 | 05 §5 | 71 套件全绿（≈2700 checks） |
| P1-2 | 处理破坏性：`CMD --lan` 鉴权策略、usage 读写契约（6 处）、updater REPO、pid 文件 | 05 §2 | 容器冷启动 `/ping` + 面板指标非 0 |
| P1-3 | 头集合照抄 v1.7.0 + 删 UA 覆写 + 活动头减集 + 按区 UA | 04 §5.1 | 头矩阵快照测试 + 抓包对照 |
| P1-4 | Anthropic 前缀稳定（#55） | 04 §6 | 长会话 `cached_tokens` 对比 |
| P1-5 | 设备码 UUID 形态 + `QD_DEVICE_ID_LEGACY` 迁移窗口 | 04 §5.1、02 §4.2 | 存量账号命中率不塌 |

### P2 差异化（9–12 人日）— 做上游没有的

| # | 任务 | 依据 | 验收 |
|---|---|---|---|
| P2-1 | `QD_MINIMAL_BODY` + 影子统计 + 灰度 | 01 §5 | 影子统计 1 周 + 灰度 A/B |
| P2-2 | UMID 可持续性（HOME/hostname/出口/时钟/启动自检） | 02 §4 | 容器内 `/diag/vm` + token 年龄稳定 |
| P2-3 | 模型注册表 + 模型矩阵管理面 | 03 §5 | 管理面可开关/授权 + 三入口一致 |
| P2-4 | 参数透传灰度 + 探针 | 04 §6.2 | 1 次探针 + 开关回滚 |
| P2-5 | （可选）shim 灰度研究 | 02 §4.3 | 默认关，只观测 |

---

## 9. 工作量汇总

| 轨道 | 人日 | 说明 |
|---|---:|---|
| P0 止血 | 4–6 | 含补测试 |
| P1 基线重建 | 5–7 | 含 5 个补丁重放与破坏性处理 |
| P2 差异化 | 9–12 | 模型矩阵占大头 |
| **合计** | **18–25** | 只做止血轨 4–6 |

---

## 10. 待拍板（按阻塞程度排序）

1. **合流方式**：方案③（v1.7.0 基线 + 5 补丁重放，收尾 `merge -s ours`）vs 直接 merge vs 按特性 cherry-pick。（阻塞 P1 全部）
2. **Docker 默认鉴权**：跟随上游 `CMD --lan`（无 Key 时自动生成并落盘）vs 保持 `127.0.0.1 + 无 Key`。
3. **设备码形态迁移**：是否接受存量号池"对上游等于换一次设备"；`QD_DEVICE_ID_LEGACY` 默认给 1 还是 0。
4. **minimal body 默认值**：默认 0 灰度多久、是否批准真实额度 A/B。
5. **UMID 上报与 shim**：是否接受组件把容器 DMI/MAC/cgroup 画像上报阿里；是否批准 shim 灰度（默认关）。
6. **模型管理数据模型**：per-key 授权存 `api_keys[].models` 还是新注册表；停用返回 404 还是 403（现有测试断言写死 400）；`/v1/models` 是否继续暴露 `upstream_key`。
7. **参数透传**：是否批准 1 次真实账号探针（`temperature` 是否 400）。
8. **部署细节**：`accounts` 属主 1000 vs 10001；README 取上游 103 行还是本地 711 行；本地 `docs/CHANGELOG.md` 是否并入根 `CHANGELOG.md`。
9. **#60 与本地 `_test_leak_guard` 断言冲突**：以哪边为准（`-X ours` 实测该套件红）。

---

## 11. 验证门禁（每个阶段）

- `python3 tests/run_all.py`：当前 20 套件/889 checks 全绿；重建后目标 71 套件全绿。
- 镜像冒烟：非 root（uid 10001）、模块存在性反查、容器 `/ping`。
- 头矩阵快照测试：对 `CosySession.headers()` 断言 19 个字段（含新增 `cosy-business-*`/`machineos`）。
- 观测三件套：`prompt_src/prompt_chars/tools_n/body_bytes`（Q1）、`umid_ok/vm_brand/token_age`（Q2）、`cached_tokens`（Q4）。
- 真实账号 A/B 只在用户批准后做，且先做影子统计（零额外上游调用）。

---

## 12. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 合流误判函数契约 | 静默坏（比崩溃更糟） | 71 套件门禁 + 先补本地守卫套件 |
| 换设备码导致信誉/缓存抖动 | 命中率下降、排队变多 | `QD_DEVICE_ID_LEGACY` 默认 1，按账号逐步切 |
| 去 prompt 引发行文回归 | 回复风格/拒答变化 | 开关 + 影子统计 + 灰度 |
| 参数透传触发上游 400 | 请求失败 | 开关默认关 + 探针 + 本地白名单校验兜底 |
| 活动链路真身份被"人"级去重 | 多号领不到 | 不复制 `machine_identity.json`；遵守上游规则 |
| 一键更新覆盖本地补丁 | 全部本地改动丢失 | 改 `qoder_updater.REPO` 或 `QD_UPDATE_DISABLE=1` |

---

## 附录 A · 复现命令

```bash
# 拓扑与规模
git merge-base main v1.7.0 && git rev-list --count v1.7.0..main && git rev-list --count main..v1.7.0
git diff --stat v1.3.4 v1.7.0 | tail -1

# 基线
python3 tests/run_all.py                       # 20 套件 889 checks

# 上游内容（不 checkout）
git show v1.7.0:qoder_sign.py | sed -n '580,700p'
git show v1.7.0:docs/risk-control-playbook.md
git show v1.7.0:docs/models.md

# 合流演练（隔离副本）
git clone /home/sixiao/tool/脚本/qoder2api-hub /tmp/qd-merge-sim
cd /tmp/qd-merge-sim && git remote add upstream https://github.com/shuishuipingan/qoder2api-hub
git fetch upstream --tags && git merge v1.7.0 && grep -rc '^<<<<<<< ' --include=*.py .
```

## 附录 B · 本文件已核对的硬事实

- `baseprompt.json` 三方 blob 相同：`2d6de81ceff45bacd7b0041139086667ce9498e4`（v1.3.4 / HEAD / v1.7.0）。
- 本地强化上游全无：`_NamedFlock`/`LEADER_LOCK_NAME`/`QD_MAX_CONNECTIONS`/`_UsageLogChain` 在上游 v1.7.0 中 grep 计数为 0。
- 上游 `Dockerfile` CMD = `--lan`；本地 = 无参数；上游 `qoder-proxy.pid` 内容 `17836`。
- 上游 `qoder_updater.py:27 REPO = "shuishuipingan/qoder2api-hub"`。
- 本地 `/v1/models` 无过滤（`qoder_proxy.py:6414-6430`）；闸门单点（6240-6249）。
- 本地 `build_qoder_body` 只写 `max_tokens`/`reasoning_effort`（4001-4018）。
---

## 13. 执行状态（2026-10-23 20:15 更新）

### 13.1 合流已完成（方案③）

| 项 | 结果 |
|---|---|
| 新基线 | 上游 `v1.7.0`（ef900d7，89 提交）+ 本地 8 个重放提交 |
| 分支 | `rebuild/v1.7.0` → 已 fast-forward 到 `main`（`d94a878`） |
| 改动规模 | vs v1.7.0：18 文件，+1403/−165（含 124 行 leak_guard 套件） |
| 测试 | **71 套件 / 1951 checks 全绿**（上游 70/1941 + 本地 1 套件） |
| 版本 | `VERSION = "1.8.0"`（fork 版本，避免与上游 tag 撞名） |
| 回滚点 | tag `backup/pre-v1.8-merge`、branch `backup/main-local` |
| 历史 | `git merge -s ours main` 记录分叉，`docs/CHANGELOG-fork.md` 保 952 行本地历史 |

重放批次：Batch-1 SIGTERM/排空 · Batch-2 连接上限/SSE 背压 · Batch-2.5 `/metrics`+`QD_LOG_JSON`+0600+`/readyz` · Batch-3 usage 按天轮转（9 处直读改链） · Batch-4 flock/leader/state 防抖 · Batch-5 非 root 镜像/CI/updater/pid/VERSION · Batch-6 文档与 `QD_SCHEDULER_ENABLED`。

破坏性处理：`--lan` 保留（鉴权加固在 Wave 2）；`qoder_updater.REPO` → `LX2000WASD/qoder2api-hub`；`QD_UPDATE_DISABLE=1`；删 `qoder-proxy.pid` 并补 `.gitignore`；设备码切 UUIDv4（legacy 默认 0）。

### 13.2 Wave 2 已完成（运行时强化收尾，合入 main）

| 任务 | 内容 | commit | 结果 |
|---|---|---|---|
| task-12 | 5min 轻保活 / 时区告警 / scheduler.enabled 立即落盘 / Account.save flock | c2b75d8 | 72 套件 / 1978 checks |
| task-8 | `--lan` 鉴权加固 H1–H8（Key 四级优先+来源日志、掩码、面板默认密码门控、端口收紧、settings 0600、自检四行） | d29afba + 7f35460 | 74 套件 / 2063 checks |
| task-9 | 运行时强化守卫套件 7 个（SIGTERM/排空/过载/背压/flock+leader/按天轮转/metrics+readyz） | d6b0f2a | 80 套件 / 2209 checks |

容器冒烟：宿主仅 `127.0.0.1:8790`；日志完整 Key 命中 0、`?key=` 0；`settings.json=600`；无 Key `/v1`→401；默认密码+门控→exit 2；`/update/apply`→403。

### 13.3 Wave 3 已完成（自研第一轮，合入 main）

| 方向 | 内容 | commit | 实测 |
|---|---|---|---|
| Q3 模型管理 | 面板保存不再清空白名单（缺失=保留/显式 `[]`=清空）+ 别名归一 + `/v1/models` 过滤（`?all=1` 全量）+ 设置页「模型闸门」 | bf7bd48/58b73e9 | 过滤/回显/面板真实请求体（node 实跑）全绿 |
| Q1 请求体瘦身 | `QD_MINIMAL_BODY`（默认 0）+ `prompt_src/prompt_chars/tools_n/body_bytes` 观测 | a05cc90 | 同一请求：mode0 12,784 B（含 system）→ mode1 35 B（无 system） |
| Q2 UMID | 可写 `/app/home`（`adduser -h`）+ 启动自检 + `QD_UMID_SHIM` 灰度（默认关）+ 示例 shim | 293cf8d | `/diag/vm` 返回 shim 值且 `shim_active=1`；无 token 泄漏 |

最终门禁（main @ 65b0a09）：**83 套件 / 2305 checks 全绿**；`check_clean_checkout` 通过（并修好了它在 linked worktree 下静默空过的问题）。

### 13.4 部署前必读的行为变更

1. **鉴权**：容器以 `--lan` 启动，`/v1` 强制 Key；Key 优先级 `--api-key > API_KEY > 面板 > launcher`；启动日志只打印来源与掩码（`QD_LOG_SENSITIVE=1` 才还原上游原文）。
2. **端口**：仓库 compose 收紧为 `127.0.0.1:8790:8790`；容器内仍监听 0.0.0.0，不新增外网暴露。
3. **面板密码**：compose 设 `QD_REQUIRE_PANEL_PASSWORD=1`，默认密码 + 0.0.0.0 绑定会拒绝启动（exit 2）。上线前必须确认生产面板密码已改。
4. **`/v1/models` 语义变化**：默认只列「官方 enable + 未全局封禁 + 当前 Key 白名单」内的模型，`?all=1` 才是全量；**无账号冷启动时快照里多数模型 `enable=false`，客户端可能只看到 1 个模型**（有账号后按真实套餐展开）。
5. **设备码**：UUIDv4 形态、`QD_DEVICE_ID_LEGACY` 默认 0——对上游等于换一次设备（用户已拍板直接切）。
6. **usage**：写入 `usage-YYYY-MM-DD.jsonl`（UTC+8），备份/清理脚本要按组文件处理；v1.7.0 的 `pricing-overrides.json` 也建议纳入备份。

### 13.5 仍待办

- **参数透传探针**（Q4，用户已批准）：本机无账号凭据，需在上线窗口用 1 次真实调用验证 `temperature` 是否被上游 400；通过后再灰度 `QD_PARAM_PASSTHROUGH`。
- **shim 灰度观测**（Q2，用户已批准）：默认关，只在容器内做观测；服务端是否反查 token 仍未验证。
- **上线窗口**：构建镜像 `v1.8.0`、按 [06-ops-sync.md](06-ops-sync.md) 更新阿里云运维文档、执行 24h 观测清单（usage 非零/失败行三字段/10605 等待/设备码形态）。
- **等授权**：push、打 tag、GHCR 发布——均未执行。
- **小遗留**：`tests/README.md` 套件表未补新套件；`_verify_models.py` 默认视图看不到 `enable=false` 模型（可加 `?all=1`）；排空窗口无显式 503；`QD_SSE_QUEUE_SIZE`/`QD_MAX_CONNECTIONS` 填非法值会 fail-fast 但缺友好提示；`/health` 是否对公网收敛待拍板。
- **生产 compose 与仓库 compose 是两份**：本次只改仓库那份，上线时按 06 §3 同步 `/opt/qoder2api-hub`。
