# Qoder2API-Hub — 国际版、国内版多账号网关中枢

<p align="center">
  <img src="https://img.shields.io/badge/Release-v1.3.4-2496ED?style=flat-square" alt="Version 1.3.4">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/API-OpenAI_Compatible-412991?style=flat-square" alt="OpenAI API">
  <img src="https://img.shields.io/badge/Dual_Realm-CN_&_Intl-0DBD8B?style=flat-square" alt="Dual Realm">
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/Zero-Dependency-ff69b4?style=flat-square" alt="Zero Dependency">
</p>

把阿里 **[qoder.com.cn](https://qoder.com.cn)**（国内版）与 **[qoder.com](https://qoder.com)**（国际版）的
原生服务封装为标准 OpenAI 兼容接口（Chat Completions / Responses / Anthropic `/v1/messages`），
带多账号负载轮询、稳定设备指纹隔离、OAuth 免客户端登录、每日签到与额度查询、Pro 福利包自动领取、
后台常驻调度器与 Web 监控看板 —— 纯标准库实现，零外部 pip 依赖。

> ⚡ 架构与交互对齐 WorkBuddy2API-Hub，上游协议替换为 Qoder COSY 签名体系。

---

## 🖼️ 看板预览

**网关总览** —— 双区出口状态、调度器、账号池与请求流水一屏尽览：

![网关总览](docs/img/dashboard.png)

**模型清单** —— 与官方桌面版同源：官方模型名、峰谷价、上下文多窗口、思考档位、能力徽标：

![模型清单](docs/img/models.png)

**数据指标看板** —— Token 消耗透视、TTFT 首字延迟、生成速度与缓存命中率：

![数据指标看板](docs/img/metrics.png)

**签到与福利中心** —— 每日签到领积分、Pro 福利包一键领取、限时活动真实状态（双区域）：

![签到与福利中心](docs/img/benefits.png)

---

## 📑 目录

- [一、功能矩阵](#一功能矩阵)
- [二、快速开始](#二快速开始) —— 本机双击 / Docker / 局域网与云端，三条路径各自 3 条命令到能跑
- [三、配置参考](#三配置参考) —— 启动参数 + 环境变量全表
- [四、模型清单与区域路由](#四模型清单与区域路由)
- [五、面板、鉴权与接口一览](#五面板鉴权与接口一览)
- [六、部署与运维详解](#六部署与运维详解) —— Docker、机器身份、凭证、调度器、限额护栏
- [七、排障手册](#七排障手册) —— 按症状组织，附诊断脚本
- [八、已知限制](#八已知限制)
- [九、开发与测试](#九开发与测试)
- [十、常见问答（FAQ）](#十常见问答faq)
- [十一、变更纪年](#十一变更纪年) —— 完整历史见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- [十二、致谢与引用声明](#十二致谢与引用声明)
- [十三、免责声明](#十三免责声明)

---

## 一、功能矩阵

| # | 能力 | 要点 |
|---|---|---|
| 1 | **双区多账号网关** | 国内版（`gateway.qoder.com.cn`）与国际版（`api1.qoder.sh`，`api2`/`api3` 自动故障切换）独立配置与管理；同一套使用姿势，区域间数据隔离 |
| 2 | **三协议入口** | Chat Completions（`/v1/chat/completions`）、Responses API（`/v1/responses`，Codex / Claude Code）、Anthropic 原生（`/v1/messages`，`x-api-key` 鉴权）；custom freeform 工具（`apply_patch`）双向转译 |
| 3 | **多账号负载轮询** | 按账号状态轮选出站；前缀会话亲和（同一对话固定同一账号，命中上游 prompt 缓存；`QD_AFFINITY_MAX_MSGS` 可加上限，默认关） |
| 4 | **稳定物理设备指纹** | 以账号 UID + 业务盐单向 MD5 派生 `machineId` / `machineToken` / 会话标识：同账号长期固定在同一台虚拟物理设备，多账号机器码彼此独立，天然防关联风控 |
| 5 | **凭证一键入池** | ① OAuth 设备授权（PKCE S256，浏览器点一下就入池）；② PAT（`pt-`）导入；③ 只读扫描本机桌面 App（`auth.v1.dat`，DPAPI+AES-256-GCM）与 Qoder CLI（`~/.qoder*/.auth/user`，AES-128-CBC）——两步确认，永不静默采用 |
| 6 | **模型清单随官方走** | 三源优先级（动态接口 > 本机客户端目录 > 双区快照），`id` = 官方模型名；峰谷价、思考档位、上下文窗口、禁用原因逐字段透传，`_refresh_catalog.py` 一键随客户端更新 |
| 7 | **每日签到与额度（真实领取）** | 活动平台 `/sash/api/v1/me/campaigns` 列出 → 对 CLAIMABLE 活动 POST claim（官方幂等，已领返回 `replayed`）；09:00/21:00 整点自动签到；quota/usage 与套餐快照实时刷新 |
| 8 | **Pro 福利包与券活动** | 资格检查 → 领取两步走；券/兑换码按账号落盘（`campaignCodes`）、任务中心回显兑换码；服务端按「人」去重时自动冷却 6 小时 |
| 9 | **后台常驻调度器** | 09:00 / 21:00 签到 + 额度快照；22:00 集中 Token 保活（`drt-`/`jrt-` 按前缀路由，PAT 兜底）；寿命不足 4 小时提前刷新；状态落盘，重启不重复领取 |
| 10 | **COSY 签名链路** | RSA-1024 包裹 AES-128 会话密钥 + MD5 请求签名 + 自定义 Base64（`=` → `$`）；AES-128/256、RSA-PKCS1v15、GCM、DPAPI、QMC 全部纯 Python 实现 |
| 11 | **瞬时故障韧性** | 连接层 2 次退避重试 + 流内信封层（HTTP200 建流后投 418）重开重试；退避/熔断/降权三族按错误类型分流，单号池不熔断；内容审核拒绝快速失败给中文解释 |
| 12 | **HTTP 帧层治「重连」** | 流式统一 `Transfer-Encoding: chunked` 收尾（同连接可连发 5 次流式）；上游首字延迟（实测 40–71s）期间每 5s 发 SSE 心跳 `: ping`；`/ping` 免鉴权探活 |
| 13 | **泄漏文本回读守卫** | 模型把工具调用序列化复述成正文时，流式/非流式/Responses 三链路还原为结构化 `tool_calls`；截断无法还原则严格吞掉，普通回复一律 fail-open 不吞正文 |
| 14 | **工具历史优化** | 结构化直传（Qwen/GLM 白名单，默认 auto）+ 历史工具结果削减（默认单侧保留 2000 字符，实测省 49–80% token），两者都有回退开关 |
| 15 | **Web 看板** | 弹性指标卡、签到与福利中心、模型能力清单、按 Key 用量透视、实时请求流水与增量日志；面板密码 PBKDF2-SHA256；多 API Key 独立出口绑定与模型白名单 |
| 16 | **账号限额护栏** | 余额地板 / 当日 token / 当日 credit / 单模型当日 token 四闸门，用尽自动换号而非等上游 402/429；计数全部来自本地 usage，零额外上游调用 |
| 17 | **零依赖 & 容器化** | 纯 Python 3.9+ 标准库；GHCR 多架构镜像（amd64/arm64），alpine 下同样零依赖；CI 门禁 = 全量离线测试 + 镜像冒烟（非 root 校验、模块存在性反查、容器探活），过了才发布 |

> 本表每一条的机制、实测数字与开关见后续章节；历史决策（含 issue #1–#23 的定位过程）见
> [docs/CHANGELOG.md](docs/CHANGELOG.md)。

---

## 二、快速开始

**三条路径怎么选？**

```
本机 Windows 自用 ──────────→ 双击 start-qoder-proxy.bat（零配置，窗口即生命周期）
有 Docker / NAS / 服务器 ───→ docker compose up -d（预构建镜像，pull 即升级）
手机 / 局域网 / 云端共享 ───→ --lan 模式（0.0.0.0 + 强制 API Key）
```

### 路径 A：本机 Windows 双击

```text
1. 双击 start-qoder-proxy.bat        # 保持窗口开着 = 网关在跑
2. API 地址：http://127.0.0.1:8790/v1 # 看板：http://127.0.0.1:8790/
3. 首次无账号 → 看板点「+ 添加账号 (OAuth)」或「🔑 导入 PAT」
```

- **生命周期就是那个 cmd 窗口**（刻意设计）：窗口开着网关就活着，关掉即停，无后台残留；下次重双击即可。关窗（或容器收到 SIGTERM）会先排空在途请求再退出，不硬杀。
- 改端口：`start-qoder-proxy.bat 8791`（默认 8790；8788 被 mimo-api-proxy.mjs 占用、8789 为 wb-proxy 默认）。
- 客户端（Chatbox / NextChat / Cherry Studio / Kelivo 等）：Base URL 填 `http://127.0.0.1:8790/v1`；
  未配 Key 时 API Key 留空或任意字符；模型名填 `/v1/models` 里的 **`id`（官方模型名，如 `Qwen3.8-Max`）**。

### 路径 B：Docker 容器

```bash
docker compose pull        # 1. 拉取预构建镜像（amd64/arm64）
docker compose up -d       # 2. 启动（accounts/ 与 usage/ 持久化挂载）
docker compose logs -f     # 3. 看日志；http://<服务器IP>:8790/ 即看板
```

- 想从源码自建：把 `docker-compose.yml` 里的 `image:` 注释掉、改 `build: .`，然后 `docker compose up -d --build`。
  裸 `docker run`：`docker run -d --name qoder-proxy --restart unless-stopped -p 8790:8790
  -v $(pwd)/accounts:/app/accounts -v $(pwd)/usage:/app/usage -e API_KEY=your_secret_key
  ghcr.io/shuishuipingan/qoder2api-hub:latest`
- **可用 tag**：`latest` · `1.3`（跟随 1.3.x）· 具体版本（如 `1.3.4`）· `sha-<短提交>`（钉死某次构建）。
  入口：[GHCR 包页面](https://github.com/shuishuipingan/qoder2api-hub/pkgs/container/qoder2api-hub) ·
  [Releases](https://github.com/shuishuipingan/qoder2api-hub/releases)
  （GitHub 仓库首页侧栏未必列出容器包，用上面链接即可）。
- ⚠️ **不要用 `1.3.2` / `1.3.3` 镜像**：少一个模块文件，启动即崩；升级到 1.3.4+（见[变更纪年](#十一变更纪年)）。

### 路径 C：局域网共享 / 云服务器

```bash
# Windows：双击 start-qoder-proxy-lan.bat（自动随机生成并持久化 qd- 开头 API Key）
# Linux/容器：绑定 0.0.0.0 + 显式 API Key
python qoder_proxy.py --lan --api-key "你的强Key"
```

- Base URL：`http://<本机IP>:8790/v1`；看板支持 `http://<IP>:8790/?key=<Key>` 直达（免登录）。
- LAN 模式**强制要求 API Key**（首次启动自动生成高强度随机 Key 并持久化到 `accounts/settings.json`，
  终端打印，重启复用）；自定义：`start-qoder-proxy-lan.bat 8790 我的Key`。
- 其他设备连不上 → 管理员运行一次 `allow-firewall.bat`（Windows 放行）。
- 用 **compose** 共享时注意：v1.4 起 `ports` 默认只发布到 `127.0.0.1`（按反代/隧道模型设计）；
  局域网直连把它换成 `"8790:8790"`（见[部署与运维详解](#docker-镜像与-compose)）。
- **云服务器注意**：只放行必要端口、用强 Key、面板默认密码 `admin` 务必改掉（默认密码下「复制明文 Key」被 403 挡住）。

### 客户端接入

| 客户端 | Base URL | Key | 备注 |
|---|---|---|---|
| OpenAI 兼容（Chatbox/NextChat/Cherry/Kelivo…） | `http://127.0.0.1:8790/v1` | 留空或看板里加的 Key | 模型名 = `/v1/models` 的 `id`（官方模型名） |
| Codex CLI | `OPENAI_BASE_URL=http://127.0.0.1:8790/v1` | `OPENAI_API_KEY` = 看板 Key | `apply_patch` 自动降级 function 工具，工具回路完整 |
| Claude Code / Anthropic 客户端 | 同上 | `x-api-key`（也接受 Bearer） | 直连 `/v1/messages`；三条已知差异见[八、已知限制](#八已知限制) |

模型名也接受 upstream key（`qmodel_38max`）、展示 id（`dfmodel (DeepSeek-Flash)`）、
人类别名（`qwen3.8-max`）与官方显示名；区域独占模型自动路由到归属出口。

---

## 三、配置参考

### 启动参数（命令行）

`python qoder_proxy.py` 与启动脚本的参数（环境变量同名项互为别名）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `HOST` 或 `127.0.0.1` | 监听地址；对外暴露用 `0.0.0.0`（`--lan` 隐含） |
| `--port` | `PORT` 或 `8790` | 监听端口 |
| `--lan` | 关 | 监听全部网卡并强制 API Key（无 Key 时自动生成并持久化） |
| `--api-key` | `API_KEY`/`QD_PROXY_KEY` | `/v1/*` 的 Bearer；看板设置的 Key 优先于它，命令行最高 |
| `--system-prompt` | 官方模板 | 请求无 system 消息时使用（默认保持官方 Qoder 模板） |
| `--accounts-dir` | `ACCOUNTS_DIR` 或 `./accounts` | 账号凭证目录 |
| `--usage-dir` | `QD_PROXY_USAGE_DIR` 或 `./usage` | usage.jsonl / usage-summary.json 目录 |
| `--panel-password` | `admin` | 启动时设定面板密码（不落明文） |

### 环境变量（全表）

**网络与监听**

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8790` | 监听端口（改它时代码、compose `ports:` 一起改） |
| `HOST` | `127.0.0.1` | 监听地址；容器/云端对外暴露设 `0.0.0.0` |
| `API_KEY` / `QD_PROXY_KEY` | 空 | `/v1/*` Bearer；留空时本机直连免鉴权 |
| `QD_PROXY_DEFAULT_REALM` | `cn` | 网关默认出口区域（`cn` / `intl`）；看板顶部可切换且状态落盘，未绑定出口的 Key 跟随它 |
| `HTTPS_PROXY` / `HTTP_PROXY` | 空 | urllib 标准出站代理（**换出口零代码**：国际版走代理直接设它）；fake-ip 透明代理（Clash）网关已内置 fake-ip 网段白名单，不会误拒内网解析 |
| `QD_MAX_CONNECTIONS` | `256` | 并发客户端连接上限；超限新连接直接 503 并断开（防闲连接扫描耗尽线程） |
| `QD_CONNECTION_TIMEOUT` | `120` | 单连接空闲秒数，超时自动断开（keep-alive 连接独占线程） |
| `QD_DNS_CACHE_TTL` | `60` | DNS 结果缓存秒数（出站解析不再逐请求）；`0` 关闭 |
| `QD_SHUTDOWN_GRACE` | `10` | SIGTERM 优雅退出的排空宽限期（秒）：等待在途请求收尾再关 |

**目录与数据**

| 变量 | 默认 | 说明 |
|---|---|---|
| `ACCOUNTS_DIR` | `./accounts` | 账号凭证、面板设置（`settings.json`）、机器身份缓存、调度器状态 |
| `USAGE_DIR` / `QD_PROXY_USAGE_DIR` | `./usage` | 请求流水（按天 `usage-YYYYMMDD.jsonl`）与指标快照 |
| `QD_USAGE_FLUSH_ROWS` | `50` | usage 汇总批量落盘行数（与 `QD_USAGE_FLUSH_SECS` 先到先触发） |
| `QD_USAGE_FLUSH_SECS` | `300` | usage 汇总批量落盘间隔（秒）；退出时强刷，JSONL 流水仍逐请求追加 |
| `QD_MAX_PAYLOAD_BYTES` | `50MB` | 请求体上限 |

**协议调优**

| 变量 | 默认 | 说明 |
|---|---|---|
| `QD_SSE_HEARTBEAT` | `5` | SSE 心跳间隔（秒）；长首字等待期防中间代理超时断连；`0` 关闭 |
| `QD_SSE_QUEUE_SIZE` | `16` | SSE 转发队列容量（**背压**）：慢客户端顶住后阻塞读上游，背压传导，防内存无界增长 |
| `QD_SSE_IDLE_LIMIT` | `300` | 流式空闲上限（秒），须 < urlopen 超时 600s，先于 socket 超时触发可见的错误帧 |
| `QD_AFFINITY_BY_PREFIX` | `1` | 前缀会话亲和（同对话钉同账号，命中上游 prompt 缓存） |
| `QD_AFFINITY_MAX_MSGS` | `0`（关） | 超过该消息数不再绑定账号（防长对话钉死同一账号） |
| `QD_AFFINITY_DEBUG` | `0` | 打印亲和命中/未命中调试日志 |
| `QD_STRUCTURED_TOOL_HISTORY` | `auto` | 工具历史结构化直传：`on`/`off`/`auto`（auto = CN 出口 + Qwen/GLM 白名单 + id 齐备） |
| `QD_TOOL_RESULT_KEEP` | `2000` | 历史工具结果单侧保留字符数；`0`/`off` 完全关闭削减 |
| `QD_TOOLCHOICE_NONE_DROPS_TOOLS` | `0` | `tool_choice=none` 时连工具一起删（旧行为回退开关） |
| `QD_ANTHROPIC_THINKING` | 空 | 空 = 不发 thinking block；`empty` = 实验（空 signature） |
| `QD_DESKTOP_VERSION` | 内置 | 覆盖 `cosy-version`（当前对齐官方 0.4.3 客户端为 `1.1.64`） |

**签到与机器身份**

| 变量 | 默认 | 说明 |
|---|---|---|
| `QD_NATIVE_IDENTITY` | `1` | 调用官方风控桥取真机器身份（结果缓存 6h）；`0` 关闭回退派生值 |
| `QD_UMID_DIR` | 空 | 原生 UMID 组件目录（`runtime-info` 位置；默认查 `umid/` 与桌面端路径） |
| `QD_MACHINE_IDENTITY_CACHE` | `auto` | 机器身份落盘缓存（`accounts/machine_identity.json`）；`off` 关闭 |
| `QD_MACHINE_IDENTITY_CACHE_TTL` | `0`（不过期） | 到期自动换身份（清组件种子取新值，真的换） |
| `QD_MACHINE_IDENTITY_RESET` | 空 | `=1` 启动时清缓存与组件种子，强制换身份 |
| `QD_MACHINE_IDENTITY_VOTE` | `1` | 首次落盘前多数表决（3 次全取，有分歧补到 5）；`0` 跳过只调一次 |

**调度器与日志**

| 变量 | 默认 | 说明 |
|---|---|---|
| `QD_SCHEDULER_ENABLED` | 开 | `off` 关闭后台调度器（纯转发场景用；也可用看板 `/scheduler/toggle`，状态落盘） |
| `QD_LOG_JSON` | 关 | `=1` 输出结构化 JSON 日志到 stdout（默认人类可读格式走 stderr，便于 `docker logs` 阅读） |
| `QD_TASKS_DEBUG` | 空 | 签到/任务层调试日志 |

**测试专用**

| 变量 | 默认 | 说明 |
|---|---|---|
| `QD_TESTS_JOBS` | `4` | `tests/run_all.py` 并发数（串行调试用 `1`） |
| `QD_TEST_FIXTURE_DIR` | 自动探测 | 官方协议 fixture 目录；缺失时相关 KAT 显示 `[SKIP]`（退出码不受影响） |

### v1.4 运行时强化（按最终状态登记）

> ✅ = 已生效（本文档其余部分即按此行为书写）；🚧 = 仍未合入，合入前以其上方旧行为为准。

| 项 | 状态 | 说明 |
|---|---|---|
| 凭证文件 0600 | ✅ | 所有账号 JSON、`settings.json`、`machine_identity.json`、调度器状态一律 0600 + 原子写（tmp 文件 chmod 后 rename） |
| 坏文件隔离 `accounts/.corrupt/` | ✅ | 无法解析的账号 JSON 移入隔离目录、不再参与选号（重启不会重复报坏文件）；`/health` 暴露隔离计数 |
| 账号文件跨进程 `flock` | ✅ | 读-改-写在进程间串行化（Windows 退化为进程内锁），多实例/面板并发保存不再写坏账号文件 |
| SIGTERM 优雅退出 | ✅ | 停止监听 → 排空在途请求（或超时）再退；`docker stop` / systemd 默认信号同样优待 |
| `/readyz` 真实健康判定 | ✅ | 无就绪账号时返回 503（摘流），`/ping` 等仍 200（存活） |
| `/metrics`（Prometheus） | ✅ | 文本格式端点，与 `/health` 同级开放，采集器零配置 |
| `QD_LOG_JSON=1` 结构化日志 | ✅ | 机器可读 JSON 到 stdout；默认人类可读格式走 stderr |
| 调度器级互斥 + `QD_SCHEDULER_ENABLED` | ✅ | 调度器 leader 锁（`accounts/scheduler/.leader.lock`）跨进程串行巡检；非持锁实例只做本地保活不签到；`QD_SCHEDULER_ENABLED=off` 可关 |
| SSE 背压 / 连接数上限 | ✅ | `QD_SSE_QUEUE_SIZE` 队列背压防 OOM；`QD_MAX_CONNECTIONS` / `QD_CONNECTION_TIMEOUT` 防闲连接耗尽线程 |
| Docker 非 root 用户 | ✅ | 镜像以 uid 10001 运行；compose 注释里给出宿主机目录 `chown -R 10001:10001 ./accounts ./usage` 一次性指引 |
| compose 资源限制与日志轮转 | ✅ | `mem_limit: 1g` / `cpus: 2`；json-file 日志单文件 10MB、保留 3 个滚动文件 |
| 默认端口收紧到 `127.0.0.1` | ✅ | compose 默认只发布到回环（按「反代 / Cloudflare Tunnel」访问模型设计，compose 附隧道骨架）；要老式 LAN 直连把 `ports` 换成 `"8790:8790"` |
| CI：push 前跑测试与冒烟 | ✅ | push/PR 跑 `tests/run_all.py`（装 node 连前端套件一起跑）；打 tag 或手动触发才构建发布，且**先冒烟后推送**（冒烟镜像额外校验容器以 uid 10001 运行） |

---

## 四、模型清单与区域路由

### 三源优先级（与官方桌面版同源）

```
1) 动态接口  GET {gateway}/algo/api/v2/model/list?Encode=1   （COSY 签名，需账号，300s 缓存）
2) 本机官方客户端目录 ~/.qoder*/.models/<uid>/catalog-v6      （QMC 解密，离线可用）
3) 内置双区官方快照 qoder_catalog_intl.json / qoder_catalog_cn.json
   （客户端更新后 python _refresh_catalog.py 重新导出并打印差异；--dry-run 只看差异；
    两个文件都缺失时才回退 qoder_catalog.py 内嵌冻结副本并打印 WARNING，catalog_source 标注来源）
```

清单**以动态源返回的集合为准**（官方桌面版此刻显示什么这里就显示什么），`/v1/models` 逐字段输出：
`id`（官方模型名，**客户端唯一要填的值**）· 官方介绍文案 · 本地化名 · `context_config` 多窗口 ·
`thinking_config` 档位 · 峰谷价（`price_factor_peak` / `price_factor_valley` / `off_peak` 窗口与
`off_peak_active_now`）· `is_free` / `is_new`；`enable=false` 条目不过滤并附**官方原文禁用原因**
（`disabled_reason` + 上游 `disabled_message_key`）。官方 catalog 与动态接口均无「最大输出」字段，
故不输出任何编造值。

### 双区清单与差异

| | 🇨🇳 国内版 | 🌐 国际版 |
|---|---|---|
| 出口域名 | `gateway.qoder.com.cn` | `api1.qoder.sh`（故障自动切 api2/api3） |
| 动态清单（示例，以官方此刻为准） | 动态 14 条，全部开通：`Auto` · `Qwen3.8-Max` · `Qwen3.8-Flash` · `Qwen3.7-Max` · `Qwen3.7-Plus` · `Qwen3.7-Flash` · `DeepSeek-V4-Pro` (96K) · `DeepSeek-Flash` · `GLM-5.3` · `GLM-5.3-Flash` (1M) · `GLM-5.2` · `Kimi-K3` · `Kimi-K2.8-Preview` · `MiniMax-M2.7` | 动态 17 条：`Qwen3.8-Max`、`Qwen3.8-Flash` 开放；其余 15 条（`Ultimate`/`Performance`/`Efficient`/`Sonus`/`Cantus`/`DeepSeek-V4-Pro`/`MiniMax-M3`/`Auto` 等）标注**官方原文**「需要升级或购买千问官方套餐开放」 |
| 区域独占 | `q37fmodel` / `glm-5.2` | `smodel` / `ultimate` |
| 跨区差异 | 区域独占模型（`q37fmodel` / `glm-5.2`）只走国内出口 | 区域独占模型（`smodel` / `ultimate`）只走国际出口；同 key 跨区可能是不同模型（`mmodel`：国际 MiniMax-M3 / 国内 MiniMax-M2.7）；上下文窗口与倍率逐项取官方条目（`dmodel`：国内 96000 / 国际 1000000） |

### 峰谷价（官方 `promotion` 字段，低谷窗口 22:00–08:00）

| 模型 | 峰价 | 谷价 | 官方折扣 |
|---|---|---|---|
| `Qwen3.8-Max` (`qmodel_38max`) | 0.50x | 0.20x | 错峰 4 折 |
| `Qwen3.7-Max` (`qmodel_latest`) | 0.50x | 0.10x | 错峰 2 折 |
| `Qwen3.7-Plus` (`qmodel`) | 0.10x | 动态为准（快照 0.04x） | 错峰 4 折 |

低谷窗口内看板把价签切换为发光高亮块 +「● 低谷生效中」徽标（后端字段 + 浏览器本地时间双保险判定）。
判定顺序 promotion 分支优先于 0 价分支：`is_free` 表示「含免费权益」而非 0 价
（`Qwen3.8-Max` `is_free=true` 但价 0.20x），不会被错标免费。`Qwen3.8-Flash` 另有限时免费 0.00x。

### 思考档位（`reasoning_effort`）

官方字段就是 `parameters.reasoning_effort`，但**每个模型合法档位不同**，上游对不支持的档位静默忽略：

| 模型 | 官方档位 | 默认 | 传 `medium`/`high` 会怎样 |
|---|---|---|---|
| `qfmodel`（Qwen3.8-Flash） | `low`/`medium`/`xhigh` | `medium` | `medium` 生效；`high` 被忽略 |
| `qmodel_38max`（Qwen3.8-Max） | `low`/`medium`/`xhigh` | `medium` | 同上 |
| `dfmodel`（DeepSeek-Flash） | `low`/`high`/`max` | `max` | `medium`/`xhigh` 都被忽略 |
| `qmodel`（Qwen3.7-Plus） | 无档位（仅开/关） | — | 任何档位被忽略，只有 `none` 能关思考 |

网关按官方目录归一化：命中原样透传；未命中取「最近的合法档位」（同距偏向该模型默认档，如
`dfmodel` 的 `medium→high`、`qfmodel` 的 `high→medium`）并写日志；无档位表但有 `thinking_config`
的模型只保留 `none`；完全没有 `thinking_config`（如路由器 `auto`）原样透传不猜测。
`/v1/models` 的 `reasoning_efforts` / `reasoning_default_effort` 即该模型的合法档位与默认档；
客户端 `reasoning.effort` / `thinking.effort/level` 三种写法都兼容。

### 区域路由与协议常量

- **Key 出口绑定**：未绑定出口的 Key 跟随看板全局出口；绑定国际/国内出口的 Key 流量强制走对应域名；
  Key 出口与模型区域不匹配时返回通俗 400（`q37fmodel` 只能走国内、`smodel` 只能走国际这类独占模型自动路由）。
- **协议常量**（对齐官方 0.4.3 双区桌面端）：`cosy-version = 1.1.64`；推理路径
  `/algo/api/v2/service/pro/sse/agent_chat_generation`；模型清单 `/algo/api/v2/model/list`（GET 需携带
  与签名一致的 `{}` body，否则 403）；`/api/v1/deviceToken|jobToken/*`、`/api/v1/userinfo`、
  `/api/v2/quota/usage`、`/api/v2/user/plan`、`/sash/api/v1/me/*` 均与新客户端一致；COSY RSA 公钥与
  客户端内置 PEM 逐字节相同。

**推理链路（COSY 签名）**：

```
客户端 OpenAI 请求
  → build_qoder_body()   官方 baseprompt 模板 + 会话压平（system/工具/参数覆写）
  → qoder_encode()       Qoder 自定义 Base64（标准 B64 三段轮转 + 字母表映射, '=' → '$'）
  → COSY 签名            RSA(1024) 包裹 AES-128 会话密钥 → info(AES-CBC 身份)
                         Bearer = COSY.{payloadB64}.{md5(payload\ncosyKey\ndate\nbody\npath)}
  → POST {gateway}/algo/api/v2/service/pro/sse/agent_chat_generation?…&Encode=1
  → SSE 信封解包          {"headers","body","statusCodeValue"} 嵌套帧 → 内层 OpenAI chunk
  → 标准 OpenAI SSE / chat.completion 回给客户端
```

---

## 五、面板、鉴权与接口一览

### 面板访问密码

- 打开看板需要**面板密码**，默认 `admin`；与 API Key 相互独立，只用于看板登录。
- 密码以 **PBKDF2-SHA256（600k 轮，兼容旧 120k 哈希）** 摘要存于 `accounts/settings.json`（0600），
  所有比较走 `hmac.compare_digest`；首次用默认密码登录会记录 `panel_default_login_at` 提醒改掉。
- 会话 TTL 7 天；看板「设置 → 面板密码」或 `--panel-password` 修改。
- **刻意守卫**：仍用默认密码时 `GET /settings/reveal`（查看明文 Key）返回 403 并提示先改密码 ——
  LAN 模式下这等于堵住「任何人登录都能读走明文 Key」。

### API Key 与出口绑定

- 看板「设置」页添加 / 一键生成（`qd-` 前缀随机 Key）、单独启停、删除即刻失效；持久化到 `settings.json`。
- 每个 Key 可绑定出口：🌐 国际版（`api1.qoder.sh`，故障自动切 api2/api3）· 🇨🇳 国内版
  （`gateway.qoder.com.cn`）· 跟随面板切换。面板配过 Key 后，启动脚本里的旧 `--api-key` 自动失效。
- **模型闸门**：全局 `banned_models` + 每 Key 的 `models` 白名单（fnmatch 通配），三个协议入口共用同一
  判定点；拒绝发生在本地（出站与网络层计数均为 0），空配置默认放行。

### HTTP 接口一览（面板类需会话、API 类需 Key）

| 类别 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 探活 | GET | `/ping` `/healthz` `/livez` `/readyz` | **免鉴权**：`/ping` 等纯文本 `pong` 判活；`/readyz` 是**就绪探针**——池里一个就绪账号都没有时返回 503（让 docker/k8s 摘流），liveness 类仍 200 |
| 状态 | GET | `/health` | 网关版本、出口、账号池规模、当前账号（带 Key 时） |
| 监控 | GET | `/metrics` | Prometheus 文本格式（账号池 / 上游错误 / 线程 / usage 概要）；与 `/health` 同级开放、零配置采集，要认证在反代层挡 |
| 推理 | POST | `/v1/chat/completions` | 标准 Chat Completions |
| 推理 | POST | `/v1/responses` | Responses API（Codex / Claude Code） |
| 推理 | POST | `/v1/messages` `/v1/messages/count_tokens` | Anthropic 原生（`x-api-key` 或 Bearer；count_tokens 是**本地估算** CJK 感知，无上游调用） |
| 模型 | GET | `/v1/models` | 模型清单（含峰谷价/档位/能力/`catalog_source`） |
| 看板 | GET | `/` | 单文件 Web 看板 |
| 面板 | GET/POST | `/panel/login` `/panel/logout` `/panel/password` | 登录 / 登出 / 改密 |
| 面板 | GET/POST | `/settings` `/settings/reveal` `/settings/save` | Key 与限额管理；reveal 受默认密码守卫 |
| 面板 | GET | `/update/check` | 新版本检测（缓存 6h，`?force=1` 强刷） |
| 面板 | GET | `/diag/vm` | 本机虚拟化检测（看板「签到与福利中心」同源卡片） |
| 账号 | GET/POST | `/accounts` `/accounts/export` | 账号列表 / 全量·单账号导出（可选带密钥，支持 Dry-Run 预检） |
| 账号 | POST | `/accounts/login/start` `/accounts/login/poll` `/accounts/login/cancel` | OAuth 设备授权流 |
| 账号 | POST | `/accounts/import/pat` `/accounts/import/desktop` `/accounts/import` | PAT / 本机凭证扫描 / JSON 导入 |
| 账号 | POST | `/accounts/checkin` `/accounts/refresh` `/accounts/test` `/accounts/set` `/accounts/delete` | 手动签到、刷新、测试、启停、删除 |
| 额度 | GET | `/accounts/credits`（POST `/accounts/credits/fetch` 强刷） | 逐号刷新额度（适合手动点） |
| 额度 | GET | `/credits/summary` | **API-key 级**只读积分汇总：只回本地登记值，**绝不触发上游查询**（返回 `by_realm`/`totals`/`accounts[]`；未登记账号的 `credits_*` 为 `null` 且不计入汇总；空池返回空结构） |
| 任务 | GET | `/tasks` | 签到状态、连续天数、福利资格、兑换码（`summary.codes` 按账号） |
| 任务 | POST | `/tasks/run` `/tasks/travel` | 批量签到领奖 / 批量领 Pro 福利包（均幂等） |
| 调度 | GET/POST | `/scheduler` `/scheduler/trigger` `/scheduler/toggle` | 排程状态 / 立即巡检 / 开关 |
| 用量 | GET | `/usage` `/usage/recent` `/usage/analytics` `/usage/by-account` `/usage/by-key` `/usage/perf` `/usage/timeseries` | 用量快照、流水、透视与区间序列（`range` 支持 `day/week/month/all/custom`，另有 `since` / `until` 自定义区间） |
| 日志 | GET/POST | `/logs` `/logs/export` `/logs/clear` | 运行日志（增量追加） |

> 路由白名单外的一切路径返回 404；`/v1/completions`（legacy）明确 404 并指引改用 `/v1/chat/completions`。

---

## 六、部署与运维详解

### Docker 镜像与 compose

- **镜像**：GHCR 预构建多架构（linux/amd64 + linux/arm64）；tag 见[路径 B](#路径-bdocker-容器)。
- **持久化**：`./accounts`（凭证、设置、机器身份、调度器状态）与 `./usage`（流水与指标）。
  NAS / 长期部署建议改绝对路径，避免源码目录被重装覆盖时连数据一起删。
- **健康检查**：免鉴权 `/ping`（interval 60s / timeout 5s / retries 3 / start_period 30s）。
- **`PORT` / `HOST` 真实生效**：镜像 CMD 不写死端口，环境变量与 compose `environment` 直接生效。
- **容器内自检**：镜像含 `_test_qoder.py` / `_diag_gateway.py` / `_diag_campaign.py` /
  `_verify_models.py` / `_refresh_catalog.py`，可直接 `docker run --rm <img> python _test_qoder.py`。
- **alpine glibc 兼容层**：UMID 原生组件是 glibc ELF，镜像已内置 `gcompat libstdc++ libgcc`（+3.1 MB）；
  缺失时 `exit 127`（组件在但跑不起来，stderr 会提示一次 `[runtime-info] 无法执行`）。
- **v1.4 运行时强化**（已生效）：镜像以非 root（uid 10001）运行 —— bind 挂载的宿主目录首次启动前执行一次
  `sudo chown -R 10001:10001 ./accounts ./usage`；compose 限 `mem_limit: 1g` / `cpus: 2` 并开 json-file 日志轮转
  （10MB × 3）；`ports` 默认只发布到 `127.0.0.1`（配 Cloudflare Tunnel 骨架，要 LAN 直连换 `"8790:8790"`）；
  SIGTERM 优雅排空；账号文件 0600 + 跨进程 flock + 坏文件隔离（详见[配置参考](#三配置参考)的同名表）。
- **升级**：`docker compose pull && docker compose up -d`；升级 = 重建容器，机器身份由落盘缓存保持不变（见下）。

### 机器身份与容器稳定性

容器里的机器身份是**「容器级」**的（官方 UMID 组件输出，与账号参数无关）。机制是**三输入模型**：

```
identity = f( 种子 [$HOME/.config/.locale_cfg，组件写的随机种子],
              机器证据 [DMI / CPU / …],
              容器证据 [/proc/mounts 的 overlay 行、网卡 MAC] )
```

这解释了两件事：**同一容器内身份恒定**（种子不变，实测 69 分钟 24 次采样无变化），而**重建容器会换**
（overlay 行变，compose 层盖不住）。

**默认行为（v1.2.13+）：机器身份落盘缓存** —— 官方组件首次输出的身份缓存到
`accounts/machine_identity.json`，之后优先复用：**重建 / 升级 / 重启容器后身份不变**。
- 前提：`accounts/` **必须是挂载卷**（不挂卷缓存写在容器内部、随容器消亡，等于没缓存）；
- 缓存的是**官方组件自己的输出**，不是伪造；首次落盘前**多数表决**（先 3 次，有分歧补到 5 次，
  防 VM 判定抖动被冻结进缓存；`QD_MACHINE_IDENTITY_VOTE=0` 跳过）；
- 社区补充实测：默认 bridge 下 `stop/start`（含 `compose restart`、宿主机重启自动拉起）也会换；
  `network_mode: host` 下不变；**任何网络模式重建都换** —— 落盘缓存把「重建」这一类也覆盖了。

**操作清单**

| 你想…… | 做法 |
|---|---|
| 重建/升级后身份不变 | 保持 `accounts/` 挂载即可（默认开启，无需配置） |
| 主动换一套身份 | 删 `accounts/machine_identity.json`，或启动时 `QD_MACHINE_IDENTITY_RESET=1`（会一并清组件种子） |
| 定期轮换身份 | `QD_MACHINE_IDENTITY_CACHE_TTL=<秒>`（到期清种子取新身份，真的换；默认 0 = 不过期） |
| 关闭落盘缓存 | `QD_MACHINE_IDENTITY_CACHE=off` |
| 组件调用本身也稳定 | （可选）固定网卡 MAC：`mac_address: "02:42:ac:11:00:77"` + 保留 `ports:`；⚠️ 换成自己生成的 MAC（`printf '02:42:%02x:%02x:%02x:%02x\n' $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256))`），别照抄 —— macvlan / 同宿主多实例会撞；service 级被拒时改 `networks.<网络名>.mac_address`（实测 Docker 29.6.1 / 29.7.2 均接受） |
| 彻底共享宿主网络栈 | `network_mode: host`（适合宿主机端口冲突；代价是无 `ports:` 映射，切换前确认目标端口在宿主上空闲） |
| Linux/Docker 拿真身份 | `python _install_umid.py`（从官方 npm 包提取内嵌 UMID 组件，零依赖、不需要 node） |

**边界提醒**：
- 只有**活动 / 签到**链路用真身份；**推理 / API 链路用按账号 UID 派生的身份，完全不受影响**；
- 该文件代表**物理机器的身份**，**不要跨机器共享 `accounts/`**；
- 三条会自动「换身份」的路径：服务端不认时的自愈（活动列表被判未认可）· `QD_MACHINE_IDENTITY_RESET=1`
  · TTL 到期。**其余路径一律不碰种子**（否则「重建不换」会被自己削弱）；
- 以上在 **amd64 与 arm64**、真实官方镜像 + 真实 compose 下验证（issue #18 作者与我们的独立复现）。

### 凭证家族与生命周期

```
accessToken:   dt- (OAuth 设备流, ~30天)  或 jt- (PAT 交换, 24小时)
refreshToken:  drt- (~1年, 旋转)          或 jrt- (48小时)
personalToken: pt- (长期兜底, 看板导入)
```

刷新按 `refreshToken` 前缀路由，PAT 永不覆盖活跃 OAuth 会话只做最终兜底；access token 轮换后
COSY 会话自动重建。死会话识别：上游 `TOKEN_EXPIRE` / `12153` / `Offline user session not found`
→ 自动停用账号并标注需重新登录。账号添加三条路径见看板：OAuth 设备授权（推荐）、PAT 导入、
本机已登录凭证扫描（只读探测桌面 App 与 CLI 两类官方存储，弹窗分区域列出，导入需二次确认）。

### 后台定时调度器

- **09:00 / 21:00**：全量自动签到（补签未签账号）+ 额度快照刷新；
- **22:00**：集中 Token 保活（`drt-` → deviceToken/refresh，`jrt-` → jobToken/refresh，失败回落 PAT）；
- 任意巡检中对剩余寿命不足 4 小时的 Token 提前刷新；
- 状态落盘（`<账号目录>/scheduler/state.json`，原子写）：进程重启不重复领取，启动巡检每天最多补签一次
  （mark-before-act，崩溃不重放）；账号文件读写与调度器巡检均有跨进程 flock（leader 锁），`QD_SCHEDULER_ENABLED=off` 可关调度。

### 账号限额护栏

四条护栏（看板「设置」页配置，支持 global 与 realm 两级作用域，**留空 = 继承、显式 0 = 关闭**）：

| 护栏 | 字段 | 说明 |
|---|---|---|
| 余额地板 | `reserve_credits` | 剩余积分低于阈值即停用该账号 |
| 当日 token | `daily_token_limit` | 本日累计 token 超限即换号 |
| 当日 credit | `daily_credit_limit` | 本日累计积分超限即换号 |
| 单模型当日 token | `model_daily_token_limit` | 单模型维度限制 |

- **402 / 额度停放**：停到 UTC+8 每日 10:00（qoder 自己的重置窗口，非通用 4am），仅余额恢复可提前解封；
- 后台余额刷新惰性运行（30min tick + 12h TTL，阈值全 0 时线程不存在），计数来自本地 usage 折叠，零额外上游调用；
- ⚠️ **量纲**：qoder 的 credits 是**积分点数**（实测账号区间 0~1100），**不是 token 量级**；
- 「临期积分优先分派」默认关闭（0）：上游额度接口不返回 per-package 到期字段，开了也不生效；
  已做防御性透传，上游补字段即自动生效。

---

## 七、排障手册

### 症状速查表

| 症状 | 首查 | 一条命令 |
|---|---|---|
| 客户端一直「工作中」，一个字都不吐，网关日志也不动 | **网关没在跑** | `python _diag_gateway.py --chat` |
| 反复重连 / 连不上 | 端口、keep-alive、空闲超时 | `python _diag_gateway.py`；确认用的是 `http://` 不是 `https://` |
| 502 / `upstream_transient_error` | 上游瞬时故障（TLS 抖动 / 服务端抖） | 看日志重试计数；持续失败再查 `HTTPS_PROXY` 与上游域名 |
| `content_policy_rejected` | 上游内容审核拒绝（确定性，重试无用） | 检查 / 缩短输入 |
| 签到「成功」但额度没动 | 显示语义（v1.2.15 已修） | 看板任务行展开 `message` / `claimed` |
| 活动列表为空 / 新号没签到活动 | 机器身份 / 风控去重 | `python _diag_campaign.py` |
| 容器启动即 `ModuleNotFoundError` | 旧镜像（1.3.2 / 1.3.3） | 升级到 1.3.4+，或临时挂 `- ./qoder_anthropic.py:/app/qoder_anthropic.py:ro` |
| 启动报端口被占 | 另一个程序或旧实例 | 网关自检会提示；`netstat -ano \| findstr :8790` |
| 复制 API Key 时跳回登录页 | 403 ≠ 401（默认密码守卫） | 先去「设置 → 面板密码」改密 |

### 诊断脚本

```bash
python _diag_gateway.py            # 端口 → /health → /v1/models，逐项报出断在哪
python _diag_gateway.py --chat     # 追加真实流式自检（200 / 首字节 / [DONE] 收尾）
python _diag_campaign.py           # 签到/活动链路体检：身份来源、机器头、本机虚拟化、活动状态（中文，只读）
python _diag_campaign.py --uid XX  # 只看某个账号（uid 前缀匹配）；--no-local 跳过虚拟化检查
python _verify_models.py --base http://127.0.0.1:8790   # 网关模型库 vs 官方此刻数据逐字段比对
                                   # 退出码 0=一致 1=有差异（打印 FAIL 明细）2=网关不可达
python _refresh_catalog.py --dry-run  # 客户端更新后预览双区快照差异（不写文件）
```

脚本均已打进镜像，可直接在容器里跑（纯标准库）。看板「签到与福利中心」的「本机虚拟化检测」卡片
与 `GET /diag/vm` 同源。

### 常见症状详解

**1. 客户端一直「工作中」，一个字都不吐**
九成是**网关没在跑**：请求根本没到达，所以日志自然一动不动。判别口诀：日志时间戳停在某一刻、此后再无
`POST /v1/chat/completions` = 网关已停。本机模式重双击 `start-qoder-proxy.bat`；Docker 模式
`docker compose up -d`。若上游首字慢（`xhigh` + 2–3 万 token 实测 40–71 秒）属于正常等待，
网关会在等待期每 5 秒发 SSE 心跳 `: ping` 防中间代理超时断连。

**2. 反复重连 / 连不上**
- 流式已用 `Transfer-Encoding: chunked` 收尾（实测同连接可连发 5 次流式全部成功），不会再因
  `Connection: close` 让连接池型客户端复用半关闭连接；
- 用 `/ping` 判活而不是根路径（旧版根路径返回 404 会被判成「网关不可用」而反复重连）；
- fake-ip 模式透明代理（Clash）：网关已内置 fake-ip 网段白名单，不会因 DNS 把域名解析成内网地址而拒请求；
- 换出口走代理：设 `HTTPS_PROXY` / `HTTP_PROXY` 即可，零代码改动。

**3. 502 / `upstream_transient_error`**
连接层瞬时错误（含对 qoder.sh 的 TLS `UNEXPECTED_EOF` 抖动）同账号重试 2 次（1s/2s 退避）；
上游先 HTTP200 建流、再在 SSE 信封里投 418 的错误（access log 记 200 而业务错 418）在**未向客户端
写出任何字节前**重开上游重试 2 次，流式客户端全程无感。重试仍失败才短冷却（15s，单账号池实际 3s）
换号；短错误冷却期间（≤10s）后续请求「等待续上」而非报错；`429 usage exceeds frequency limit`
只在上游真频控时出现。耗尽后其余瞬时错误返回中文友好提示，错误详情经 `qoder_detail` 保留 400 字节
完整送达日志（含内层 `details`）。

**4. 签到 / 活动领不到**
按可能性排序：① 跑在**虚拟机/云桌面**（新人 300 明确不参与，活动也可能被风控过滤，
`_diag_campaign.py` 有 VBS/HVCI 误报提示）；② **同设备多账号按「人」去重**（绕过列表直接 claim 会
返回 `SAME_PERSON_ALREADY_CLAIMED`，被去重的号列表里连活动都不显示）；③ 只注册了网页账号、
**没登录过最新版桌面客户端**（300 在首次登录客户端时发放）；④ 当日活动每账号每轮一次，
错过 10:00（UTC+8）窗口不补。国际版还需要真实 UMID 机器身份（见[八、已知限制](#八已知限制)）。

**5. 频控类 429 与排队**
信封层 `403 (10605 / isQueued)` 排队满时，会对触发排队的**账号+模型**精确冷却（默认 30s）并解绑
会话亲和，下一次请求自动轮换；死会话（`TOKEN_EXPIRE`）同样停用账号。内容审核拒绝
（`InternalError.Algo.DataInspectionFailed`）与客户端参数错误（`invalid_parameter_error）
**不重试**、快速失败 —— 前者返回中文解释 `content_policy_rejected`（确定性拒绝，请检查/缩短输入）。

---

## 八、已知限制

1. **国际版签到需要真实 UMID 机器身份**。国际版服务端严格校验 `Cosy-MachineToken/Code/Type`；
   拿不到真身份时网关**不用派生假值去撞**（实测发全套派生机器头会让可领取活动被整条过滤，issue #10），
   状态如实标为 `machine_headers=omitted` 并给 `hint`。Linux/Docker 部署可用
   `python _install_umid.py` 从官方 npm 包提取内嵌 UMID 组件取得真身份。⚠️ 该组件输出的是
   **机器级**身份（与账号参数无关，不同账号/空账号返回同一个 `machineToken`）：它解决「Linux 拿不到
   真身份」，**不能**解决「同一台机器多个国际版账号都能领」——上游按设备去重（每台设备每日仅 1 个
   国际版账号可领），需要不同物理设备。另外「**下发**（看得到这条活动）绑定设备，**领取**不校验机器头」
   —— 重建容器换身份在这件事上没有用武之地。国内版不受此限制（只需宿主头）。
2. **容器内身份是「容器级」**：同容器稳定，新建容器即换；v1.2.13+ 的落盘缓存已覆盖重建/升级
   （前提是 `accounts/` 挂卷），机制与操作见[部署详解](#机器身份与容器稳定性)。
3. **虚拟化字段不能用来判断「是不是虚拟机」**：`runtime-info` 的 `isVm` 对所有账号返回同一个值，
   且在开了 VBS/内核隔离的实体机（Windows 11 默认开启 VBS/HVCI）上会误报 `isVm=true`（实测华硕
   游戏本也被判 `isVm=true (Hyper-V, 77%)`）；`vmInfo.brand` 不是固定值（`Docker`/`KVM` 都出现过）。
   该字段只在本地诊断用（看板卡片 / `_diag_campaign.py` / `/diag/vm`），**不上行**，不影响请求链路。
4. **Anthropic `/v1/messages` 入口的三条差异**（不影响 Chat Completions 与 Responses 两条既有入口）：
   - **流式不做流内信封重开**（`message_start` 已发出，重开会变成两个 start、协议违规；改为发明确的
     `event: error` 终态事件而非静默关流；非流式仍完整重试）；
   - **默认不返回 thinking block**（Anthropic 规范要求服务端生成的 `signature`，网关无法生成也
     **不伪造**；`QD_ANTHROPIC_THINKING=empty` 为实验开关，带空签名，客户端若做非空校验可能报错）；
   - **入站 thinking block 一律丢弃**（无法验证其签名，不回灌污染推理连贯性）。
5. **官方 fixture 缺失时部分密码学 KAT 会跳过**：3 条断言显式 `[SKIP]`（打印候选清单，绝不静默；
   退出码不受影响）；`QD_TEST_FIXTURE_DIR` 指向目录后跑完整 KAT。
6. **临期积分优先分派不生效**：上游额度接口不返回 per-package 到期字段；已做防御性透传（上游补字段即
   自动生效），并刻意不把顶层 `expiresAt` 贴到包级（它可能是按月重置而非到期作废，硬贴会反向浪费）。
7. **真实上游链路未经端到端发布验证**：离线确定性测试（889 项断言）全绿、CI 镜像冒烟通过，
   但发布环境未实跑真实上游；以你自己的部署环境验证为准。
8. **网关不代做官方客户端任务**（如桌面端「站点」发布）：只做条件满足后的自动领取与呈现；
   领取只认服务端「任务已完成」状态；任务未完成时任务中心明确标注需先在官方桌面端完成
   （成就 `sites_first_use` 那类）。
9. **usage 历史数据的 credit 0 不可回填**：v1.2.19 修复了把复数 `credits` 读成单数 `credit` 导致的
   恒 0 统计（issue #22），但已写入的 0 是永久性的；只能从此后新写入的行恢复正确。

---

## 九、开发与测试

```bash
python tests/run_all.py              # 套件化入口（并发，20 suites / 889 checks；可加套件名片段过滤）
python _test_qoder.py                # 兼容入口（629 checks / 0 failed / 4 skipped）
python qoder_proxy.py --port 8790    # 直接启动
```

测试为**离线确定性**：AES-128/256 官方向量与 fixture KAT、QMC 与凭证解密、自定义 B64、COSY 签名、
双区目录全字段（峰谷价/多窗口/思考档位/展示 id/解析）、独占路由、签到能力运行时探测、活动平台归一化、
DeepSeek `reasoning_content` 回填保留、信封解包、custom 工具转译、三族熔断接线、泄漏回读管线、
前端渲染（4 个 `.js` 套件，自读 `dashboard.html` + 内联元素桩，不依赖 jsdom）。每个套件都带
「改坏 → 必红」自证；套件编排与迁移策略见 [tests/README.md](tests/README.md)。

模块结构：

| 文件 | 职责 |
|---|---|
| `qoder_proxy.py` | 主网关：HTTP 路由、COSY 数据面、三协议转换、用量统计、看板鉴权 |
| `qoder_anthropic.py` | Anthropic `/v1/messages` 入站/出站转换、usage 守恒映射、effort 断点映射 |
| `qoder_sign.py` | 自定义 Base64、纯库 AES-128/256 + GCM、RSA、DPAPI、QMC 解密、COSY 签名 |
| `qoder_accounts.py` | 双区账号池、OAuth 设备流、PAT、Token 生命周期、本机凭证扫描/导入、机器身份缓存 |
| `qoder_catalog.py` | 双区官方模型快照加载（外部 JSON 优先，内嵌冻结副本兜底）、别名与独占表 |
| `qoder_catalog_intl.json` / `qoder_catalog_cn.json` | 双区官方模型快照（`_refresh_catalog.py` 刷新） |
| `qoder_tasks.py` | 签到闭环（含 DISABLED 归一化）、Pro 福利包、批量执行、保活巡检 |
| `qoder_scheduler.py` | 整点排程调度器（状态落盘、启动补签闸门） |
| `qoder_settings.py` | 面板密码（PBKDF2 600k 轮）、多 API Key 出口绑定、模型白名单、会话管理 |
| `qoder_fingerprint.py` | UID 稳定设备指纹派生（derive_id） |
| `baseprompt.json` | 官方推理请求体模板 |
| `dashboard.html` | 单文件 Web 看板（含本机凭证扫描导入与虚拟化检测卡片） |
| `_install_umid.py` | 从官方 npm 包提取内嵌原生 UMID 组件（Linux/macOS 机器级真身份） |
| `_diag_gateway.py` / `_diag_campaign.py` | 网关存活/流式自检 · 活动资格与虚拟化体检 |
| `_verify_models.py` / `_refresh_catalog.py` | 模型库端到端验证 · 官方快照刷新 |

---

## 十、常见问答（FAQ）

**Q1：为什么客户端一直「工作中」不吐字？**
A：九成是网关没在跑（详见[排障手册 · 症状 1](#常见症状详解)）。一条命令确诊：`python _diag_gateway.py --chat`。

**Q2：为什么给 Qwen3.8-Flash 传 `reasoning_effort` 没反应？**
A：上游对模型不支持的档位**静默忽略**（`qfmodel` 只认 `low/medium/xhigh`）。网关会按官方档位表
归一化到最近的合法档位并在日志标注；想确认某模型的合法档位，看 `/v1/models` 的 `reasoning_efforts`。

**Q3：为什么新注册的号没有新人 300 Credits / 没有签到活动？**
A：① 跑在虚拟机/云桌面（新人 300 明确不参与，活动也可能被风控过滤）；② 同设备批量注册，第一个号
拿走试用后其余号会被冻结；③ 只注册了网页账号、没登录过最新版桌面客户端；④ 每日活动每轮限领一次，
错过当日 10:00（UTC+8）窗口不补。跑 `python _diag_campaign.py` 逐项体检。

**Q4：为什么签到显示「成功」但额度没动？**
A：旧版把「已领取 / 暂无可领 / 名额发完」统一渲染成「成功」（v1.2.15 已修）。现在任务行如实显示
活动平台结论（`claimed` / `message`）与 `next_available_at`（次日 10:00 UTC+8）。

**Q5：重建容器后签到不到了？**
A：身份换了会影响活动链路（推理不受影响）。只要 `accounts/` 是挂载卷，v1.2.13+ 的落盘缓存默认保持
身份不变；想手动换身份删 `accounts/machine_identity.json` 或设 `QD_MACHINE_IDENTITY_RESET=1`。

**Q6：同机多账号为什么共享「每日 100 Credits」名额？**
A：服务端按「人」（设备身份）去重，同一设备上的多账号共用每轮一次额度，被去重的号列表里连活动都
不显示。国际版需要不同物理设备（新人 300 的「虚拟机不参与」同源）；国内版同理按人去重。
网关不伪造设备身份去绕过这条规则。

**Q7：502 / TLS `UNEXPECTED_EOF` 偶发正常吗？**
A：上游 provider 故障与 TLS 抖动会自动重试（连接层 2 次 + 流内信封层 2 次）；持续失败才换号冷却。
国际版想换出口走代理：设 `HTTPS_PROXY` 即可（网关未禁用 urllib 的 ProxyHandler）。

**Q8：面板密码和 API Key 有什么区别？**
A：面板密码只开网页看板（PBKDF2 摘要存储，默认 `admin` 务必改）；API Key 校验 `/v1/*` 请求并可
绑定出口区域与模型白名单。两者独立，按需分别配置。

**Q9：怎么换端口 / 让局域网访问？**
A：换端口：`start-qoder-proxy.bat 8791` 或 `--port`/`PORT`。局域网：`--lan` 模式（自动生成并持久化
随机 Key；`start-qoder-proxy-lan.bat 8790 我的Key` 可自定义）；连不上时管理员跑一次 `allow-firewall.bat`。

**Q10：多个网关实例能同时跑吗？**
A：同端口启动时网关会自检并提示（Windows 下 SO_REUSEADDR 会让连接静默分流、极难诊断，启动 probe
会拦住重复启动）。调度器级跨进程互斥（leader 锁）与 `QD_SCHEDULER_ENABLED` 开关均已合入；单机多实例时签到只由持锁实例执行，其余实例只做本地保活。

---

## 十一、变更纪年

完整历史（v1.1.0 → v1.3.4，含 issue #1–#23 的定位过程、每版验证证据与自我更正）见
[**docs/CHANGELOG.md**](docs/CHANGELOG.md)；发版说明另见
[GitHub Releases](https://github.com/shuishuipingan/qoder2api-hub/releases)。

**v1.3.4 摘要**：修复 1.3.2/1.3.3 预构建镜像漏 COPY `qoder_anthropic.py`（启动即崩，issue #23）；
CI 新增镜像冒烟（模块存在性反查 + import + 容器 `/ping`）——「构建成功不等于镜像能跑」。
在跑旧镜像的用户请升级到本版。

---

## 十二、致谢与引用声明

本项目在协议兼容、COSY 签名与设备授权链路设计中，深度参考了开源社区现有项目的经验与逆向成果：

- **[mmqz/cpa-multi-plugins](https://github.com/mmqz/cpa-multi-plugins)**：CN/Intl 常量表、OAuth 设备授权与
  PAT 交换流程、COSY 签名与自定义 Base64 编码的验证实现；签到与保活排程设计（09:00/21:00 签到 ·
  22:00 保活）、按 token 前缀路由的刷新策略。
- **[Liki4/qodercli2api](https://github.com/Liki4/qodercli2api)**：Qoder OAuth 与推理协议逆向全记录
  （设备流 PKCE 细节、`deviceToken`/`jobToken` 端点、SSE 信封与 `[DONE]`/`event:finish` 语义）。
- **[Sliverkiss/workbuddy2api](https://github.com/Sliverkiss/workbuddy2api)**：设备指纹稳定派生设计
  （`derive_id`）、整点排程调度理念、指纹脱敏管线与 DeepSeek 多轮思维链回填。
- **WorkBuddy2API-Hub**：本项目的架构、看板交互与功能矩阵蓝本。

---

## 十三、免责声明

1. 本项目为非官方自托管网关，仅供技术研究、逆向协议学习与个人合法授权账号在私有环境测试使用。
2. 本项目不提供任何账号及额度。请严格遵守官方服务条款，禁止用于任何商业转售、恶意并发或违规滥用。
