# 06 · 运维文档同步：阿里云运维 ↔ qoder2api-hub（上游 v1.7.0 合流 / 本地 v1.8.0 上线）

> 作者：ops-deploy-analyst · 时间：2026-10-23
> 只读产出：**本文件是草案**。本轮不改 `/home/sixiao/tool/云服务/阿里云运维` 下任何文件，上线动作发生时才由 Lead 执行。
> 证据基准：本地 HEAD `abb38c8`；上游 **tag `v1.7.0`（ef900d7，89 提交）**；合流分支 `rebuild/v1.7.0` 截至 2026-10-23 已重放 **Batch-1/2/2.5/3/4（HEAD `9aa29ef`）**，Batch-5（文档/pid/updater）待做。代码行号统一取自 `git archive v1.7.0`（`/tmp/v17tag`），本地补丁另标批次与提交号。
> **本次发布口径（Lead 已拍板）**：VERSION 与镜像 tag 统一为 **v1.8.0**；篇名 `30-上游v1.7.0合流-本地v1.8上线记录-2026-10-XX.md`。
> 标注约定：**【已验证】**= 读过代码/文档原文或实测输出；**【推断】**= 由代码路径或部署证据推出、未在线上复现；**【未验证】**= 需上线时现场取证。

---

## 0. 结论摘要

1. **入口链路不变，`--lan` 不新增暴露面（Q1 已定论）**：公网只开 22 + 45421，nginx TLS/SNI 分流，`qoder` 子域回源 `127.0.0.1:8790`；**生产 compose 端口发布保持 `127.0.0.1:8790:8790`**，bridge 模式下容器内监听 0.0.0.0 不外露。v1.7.0 **没有新增监听端口、没有新增子域**，`01/02/12/14` 拓扑不用改，只需在 `02` 容器行补「容器内 `--lan`，宿主发布仍是回环」。**唯一例外**：若 inspect 显示 `NetworkMode=host`（此时 `ports` 声明被忽略，`--lan` 会直接绑宿主 0.0.0.0），按 §5 O1 补一条 8790 封锁即可，不影响上线；核对命令见 §3.2b。【已验证：唯一 bind 点 `v1.7.0:qoder_proxy.py:11013`；compose 只发布 8790 `:22`】
2. **真正要改的是「鉴权来源」「版本口径」「usage 落盘形态」三件事**，不是端口。`--lan` 把「无 Key 也能调」变成「必须有 Key」，而 Key 有**四级优先级**：`--api-key` > `API_KEY` 环境变量 > 面板保存的 Key > 首次自动生成的 launcher key（`v1.7.0:qoder_proxy.py:10839-10854`）。生产实际落到哪一级**必须上线前取证**（§3.2a 一条命令 + 判据表），它决定「客户端会不会集体 401」。【已验证：代码；未验证：生产设置】
3. **设备码直接切 UUIDv4（用户已拍板，不做回退演练）**：对上游等于「换设备」。**不是随机 UUID，是按账号 uid 稳定派生的 UUIDv4 形状**（`v1.7.0:qoder_fingerprint.py:43-61`）。`QD_DEVICE_ID_LEGACY=1`（`:31-40`）**仅作应急开关**，写进 §3.4 回滚章节，不纳入正常上线步骤。**对外表述不要写「随机 UUID」**。【已验证】
4. **usage 按天轮转随本次上线（Q3 已定）**：上游 v1.7.0 本身只有单文件（`v1.7.0:625-658`，读侧全直读）；本轮已在 **Batch-3（`0814c53`）** 重放轮转并改掉上游新增的 6 处直读（`docs/plan/05` §2.4）。**因此上线后必须验证**：`usage/` 出现 `usage-<今天>.jsonl`，且 `/pricing`、`/health.quality`、面板统计非空（§3.3 V8/V9）——这是本次唯一会「静默归零」的回归点。备份/清理脚本要从「一个文件」改成「一组按天文件」。【已验证：两边代码 + 批次提交；待验证：线上效果】
5. **版本口径已定：本次发布 v1.8.0**（VERSION 与镜像 tag 都用 `1.8.0`，避免与上游 `v1.7.0` 撞名；**上线前必须确认 `qoder_proxy.py` 的 VERSION 已改 `1.8.0`**，否则 CI 的 `tag==VERSION` 断言会红，`v1.7.0:.github/workflows/tests.yml:69-84`）。历史坑仍要记住：**上游 v1.4.0 ≠ 我们说的 v1.4**——上游 `v1.4.0` 才引入 `CMD --lan` 与 HEALTHCHECK（`CHANGELOG.md:617-653`），我们的「v1.4」是本地提交 `f14031a`；线上镜像 tag `qoder2api-hub:v1.4` 的运行版本其实是 `1.3.4`（`HEAD:qoder_proxy.py:58`）。**别再拿镜像 tag 当运行版本证据**。【已验证】
6. **`/metrics`、真 `/readyz`、`QD_LOG_JSON`、`chmod 0600` 已在 Batch-2.5（`1172654`）重放**（Q4 已定）：文档按「**已有**」写，不再列为「可能丢失」。回归只看 §3.3 V11/V12 与 `curl :8790/metrics` 非 404。【已验证：`9aa29ef` 工作树 `qoder_proxy.py:8983/9082`、`qoder_settings.py:95`】
7. **上线风险最高的不是代码，是三个夹具条件**：① `accounts/` 属主必须是 10001（否则 `--lan` 首启写 launcher key 失败 → 启动即崩；`v1.7.0:qoder_settings.py:53-65` 的 `save()` 无异常兜底）；② 时钟漂移 > 120 s 直接 401（`:10912-10916` 只告警不阻断）；③ 信任库被 `/opt/ca/alpine` 挂载覆盖（`安全分析/2026-10-23-容器信任库挂载修复.md:132-138`），**重建镜像后这份挂载会遮住新镜像里的 CA**。【已验证：代码与挂载证据；未验证：新镜像 CA 差异】

---

## 1. 现状盘点：当前 qoder2api-hub 在云上的部署形态

### 1.1 机器与系统

| 项 | 值 | 证据 |
|---|---|---|
| 公网 IP / 系统 | `106.14.203.201` / Alibaba Cloud Linux 3 | `README.md:3`、`02-云端状态.md:8-15` |
| 规格 | 2 vCPU / 1.8 GB / 40 GB（已用 38%） | `02-云端状态.md:12` |
| Docker | 26.1.3 · Compose v2.27.0 | `02-云端状态.md:14` |
| 公网开放端口 | **仅 22/tcp 与 45421/tcp**，其余全 closed | `02-云端状态.md:44-54`、`06-遗留事项.md:47-64` |

### 1.2 入口链路（三层）

```text
客户端 → https://<sub>.sixiaoip.de5.net:45421（TLS，LE 通配符证书）
      → nginx:45421（SNI 分流，8 个后端；sixiaoip.conf + direct.conf）
      → http://127.0.0.1:8790（qoder-proxy 容器发布口）
      → 容器内 qoder_proxy.py（当前 HOST=0.0.0.0、PORT=8790）
```

- 双域名：`*.sixiaoip.de5.net`（TLS1.2/1.3 全兼容，主推）与 `*.eidolonmachine.xyz`（仅 TLS1.3，SNI 审查）——`01-访问入口.md:6-21`、`02-云端状态.md:41`。
- qoder 的地址：API `https://qoder.sixiaoip.de5.net:45421/v1`，看板 `.../`（需面板密码）——`01-访问入口.md:27-28`。
- 容灾 Tunnel ingress 里 `qoder → http://127.0.0.1:8790`（`02-云端状态.md:82`），但 **sixiaoip 这个 zone 不在 ingress 里**，改回 CNAME 会 404（`01-访问入口.md:87-91`）。与本次升级无关，别顺手「修」。
- qoder 的 CORS 是**按路径**放行（`v1.7.0:qoder_proxy.py:377-382`，管理端点一律不放行），**不是 Origin 白名单**——所以 `13` 篇那套「直连后给应用加 Origin/Host 白名单」的适配**不适用于 qoder**，升级也不涉及这一块。【已验证】
- **v1.7.0 不需要改 nginx/DNS**：没有新端口，管理端点前缀也不变（`/accounts /usage /tasks /scheduler /settings /logs /diag /update /agents`，`v1.7.0:qoder_proxy.py:8544-8568`）。【已验证】

### 1.3 容器与路径

| 项 | 值 | 证据 |
|---|---|---|
| 部署目录 | `/opt/qoder2api-hub`（compose + `accounts/` + `usage/`） | `03-运维手册.md:38,115,152` |
| 容器名 | `qoder-proxy` | `02-云端状态.md:21` |
| 镜像 | `qoder2api-hub:v1.4`，ImageId `sha256:64e21035c92b` | `安全分析/2026-10-22-upgrade-maintenance.md:828` |
| 运行时长 | 截至 2026-10-23 14:37 `Up 12 days (healthy)` | `安全分析/2026-10-23-mihomo升级记录.md:238` |
| 发布端口 | `127.0.0.1:8790` + `10.255.0.1:8790`（docker0 网关，供同宿主容器访问）；外网探测 **000** | `安全分析/2026-10-22-external-exposure.md:432` |
| 挂载 | `./accounts:/app/accounts`、`./usage:/app/usage`、`/opt/ca/alpine:/etc/ssl/certs:ro`（2026-10-23 新增） | `安全分析/2026-10-23-容器信任库挂载修复.md:132-138` |
| 出站代理 | `HTTPS_PROXY=http://127.0.0.1:7890` | `安全分析/2026-10-22-tls-client-matrix.md:280` |
| 健康状态 | `healthy`（说明生产 compose 自带 healthcheck，命令体未取证） | `安全分析/2026-10-23-mihomo升级记录.md:238` |

> ✅ **网络模式（Q1 已定论，不属于本次变更范围）**：`02-云端状态.md:21` 写「host 网络」，而外部暴露面报告实测是「`127.0.0.1:8790` + `10.255.0.1:8790` 双绑定 + 外网 000」（`external-exposure.md:432`），双绑定更像 bridge + ports 发布；TLS 矩阵也写明「本次未深究其网络模式」（`tls-client-matrix.md:280`）。**无论哪种，生产 compose 的端口发布都保持 `127.0.0.1:8790:8790`，因此 `--lan` 不新增暴露面**；上线用 §3.2b 的 inspect 命令核对一次即可（若真为 host，按 §5 O1 加封锁）。【结论按 Lead 决策；模式本身仍【未验证】】
>
> 另注：若确为 bridge，容器内 `HTTPS_PROXY=127.0.0.1:7890` 指向的是**容器自己**，对外代理实际不生效（axonhub 用的是 `10.255.30.1:7890`）。国内版账号直连不依赖代理，线上看不出问题；**但别把它当成「已配好代理」**。这是本轮核对新发现的疑点，建议上线时一并 inspect。【推断】

### 1.4 鉴权与凭据

| 项 | 现状 | 证据 |
|---|---|---|
| API Key | `qd-lJ2…`（见凭据清单） | `05-凭据清单.md:20` |
| 面板密码 | 已改（非默认 `admin`） | `05-凭据清单.md:22` |
| 落盘位置 | `accounts/settings.json`（面板密码为 PBKDF2 摘要、0600） | `05-凭据清单.md:23` |
| 现状鉴权行为 | 本地镜像 CMD **无** `--lan`（`HEAD:Dockerfile:93`），无 Key 时 `auth_required()=False`（`HEAD:qoder_proxy.py:445`） | `docs/plan/07-docker-auth.md:20` |

> 【推断】生产此前被 nginx 挡在 TLS + 域名之后、客户端也都带 Key，所以「无 Key 也可调」没酿成事故。**`--lan` 之后这条路径会被强制关上**——这是行为变化，不是回归。

### 1.5 数据、备份与日志

- 备份只覆盖 `accounts/`：`rsync …:/opt/qoder2api-hub/accounts ./backup-qoder-accounts/`（`03-运维手册.md:113-115`）。**`usage/` 从未进备份**——而 v1.7.0 之后 `usage/` 里会多出 `pricing-overrides.json`（`v1.7.0:qoder_proxy.py:8717`）与按天文件，建议本次一并纳入备份。
- 日志：容器 stdout，靠 compose 的 `json-file max-size=10m / max-file=3` 兜底（`HEAD:docker-compose.yml:36-42`）。生产 compose 是否保留这两行【未验证】——**上线前必须确认**，否则 v1.7.0 更密的日志（队列/风控/自检）会加速写盘。
- 健康检查：生产有 healthcheck（healthy 状态为证）；仓库 compose 探 `/ping`（`HEAD:docker-compose.yml:48-53`），v1.7.0 镜像内置同样探 `/ping`（`v1.7.0:Dockerfile:73-74`）。**`/ping` 恒 200、不查账号池**（`:8609-8621`），所以「healthy ≠ 有可用账号」。【已验证】

### 1.6 已踩过的坑（本次升级要避免复发）

| 坑 | 事实 | 出处 | 与本次关系 |
|---|---|---|---|
| 413 | nginx 默认 `client_max_body_size 1MB` 截断长上下文，已全部改 64m | `14-SNI审查…md:129-131` | v1.7.0 新增**网关侧** `QD_MAX_BODY_MB=29` 会主动 413（`:5118`）→ 413 可能来自网关而非 nginx，**排障先看网关日志** |
| 403 / 11140 | 上游风控是账号级信号（`28` 篇实证） | `28-凭证403与11140事件分析-2026-10-23.md` | 该篇讲的是 **codebuddy**（`converter.py`），**不要套到 qoder** |
| 10605 排队 | 上游模型级队列（现有运维档案**未记录过**：`grep 10605` 全目录 0 命中） | 上游 `v1.7.0:docs/risk-control-and-queueing.md`、`CHANGELOG.md:389` | v1.7.0 改「等队列、不换号」（`CHANGELOG.md:361,382-385`）→ 需新写处置节 |
| 502/524 | 上游卡顿、超时调优 | `11-502-524事故与弹性修复-2026-10-11.md` | 网关侧心跳/超时变化需重新观测（`QD_SSE_HEARTBEAT` 默认 5s） |
| 证书 | LE 通配符 + acme.sh 自动续期 | `02-云端状态.md:41` | 与网关无关，但重建镜像不能动 `/opt/ca/alpine` 挂载语义 |
| 假阳性 | 本机 TUN/fake-ip 让端口探测全显示 open | `04-安全加固记录.md:83-87` | 暴露面复核必须用「服务器自身 mihomo 回连公网 IP」法（`17` 篇） |
| 容器 IP 漂移 | 重建后 IP 可能变，`DOCKER-USER` 规则要复核 | `02-云端状态.md:156-160` | qoder 只发布回环/网关地址、**不在 qd-firewall 规则里** → 无需改规则，但要复核外网 8790 仍 000 |

---

## 2. 变更映射表：v1.7.0 → 运维文档的哪一节要改

> 级别：**A**=必须改，**B**=建议补一句，**C**=仅新篇 `30-…` 记录。

### 2.1 Docker / 启动 / 端口 / 健康检查

| # | 变更（代码锚点） | 影响 | 要改的文档·节 | 级别 |
|---|---|---|---|---|
| D1 | `CMD … --lan`（`v1.7.0:Dockerfile:85`；引入于上游 `v1.4.0`，`CHANGELOG.md:638`） | 监听 0.0.0.0 + 强制 Key；首启自动生成 launcher key 落 `accounts/settings.json`（`qoder_settings.py:128-145`，启动调用 `qoder_proxy.py:10846`） | `02` §容器表 qoder 行；`03` §qoder 升级流程；`04` §6；`05` §Qoder 网关（新增「Key 来源」） | A |
| D2 | 上游 compose `ports: 8790:8790`（`docker-compose.yml:22`）**不跟随** | 发布口仍 `127.0.0.1:8790`（+`10.255.0.1`）；生产 compose 本就不在仓库同步范围（`03:156-160` 明确 `--exclude docker-compose.yml`） | `02` §容器表；`03` §升级流程加一句「compose 不覆盖」 | A |
| D3 | 镜像 `HEALTHCHECK /ping`（`v1.7.0:Dockerfile:73-74`）；compose healthcheck 跟随 `PORT`（`:38`） | 容器 STATUS 有 healthy/unhealthy 语义；改 `PORT` 不再需要同步探针 | `02` §容器表加「健康」列；`03` §常用命令加 `docker inspect --format '{{.State.Health.Status}}'` | B |
| D4 | 启动自检四类信号：时钟漂移 >120s、`accounts/` 不可写、容器+intl、`traffic_guard`（`:10904-10916`、`env_report:5685-5700`） | 「莫名 401」现在有日志线索 | `03` 新增「启动日志必看四行」；`30` 验证节 | A |
| D5 | 上游 **无** `USER`/`adduser`（`v1.7.0:Dockerfile` 全文无 USER；对照 `HEAD:Dockerfile:80-83`） | 重建若丢掉本地 patch 会回到 root；若保留则必须 `chown 10001` | `04` §6 改为「重建后逐条回归」；`30` 验证 `id -u` | A |
| D6 | 新增 POSIX 启停脚本 `start-qoder-proxy.sh` / `start-qoder-proxy-lan.sh` / `stop-qoder-proxy.sh`（`v1.7.0:docs/deployment.md:14-19`）与误提交的 `qoder-proxy.pid`（仓库根，5 B） | **Docker 里用不到**（容器 PID1 是 python，停止靠 `docker stop` 的 SIGTERM）；脚本的 pid 文件方案与本地 flock 是两套互斥机制（`docs/plan/05` §1.5），**择一**；pid 文件应在 Batch-5 删掉并补 `.gitignore` | `03` 明确「容器部署不看启停脚本」；`30` 记录 `docker stop` 行为 | B |

### 2.2 端点（新增 / 语义变化）

| # | 端点 | 语义与风险 | 要改的文档·节 |
|---|---|---|---|
| E1 | `/ping`、`/healthz`、`/livez`、`/readyz` | 四别名**恒 200**、免鉴权（`:8529-8621`） | `01` §快速排障；`30` |
| E2 | `/health` 新增 `risk` / `env` / `quality`（`:8598-8625`） | **免鉴权**（不在面板路由表 `:8544-8568`），公网可读版本号/账号数/风控计数/`clock_skew`/`container`/`proxy` 布尔；`env_report` 已核对**不含密钥**（`:5685-5700`） | `01` 加「`/health` 可公开」；`04` 记为可接受信息面（或按 §5 O7 收敛） |
| E3 | `/models/refresh`（`:8633`） | 容器内模型清单陈旧不必重启；失败返回 502 | `03`「模型清单不更新怎么办」；`30` |
| E4 | `/pricing`（`:8692-8718`） | 上游直读 `usage.jsonl`，**Batch-3 已改链式读**；若回归则 `unpriced` 恒空（面板/成本页失真）。覆盖文件 `usage/pricing-overrides.json` | `02` §数据目录；`03` §备份（usage 也要备）；`30` |
| E5 | `/agents`、`/agents/models`、`/agents/cli`（`:8567, 8647, 8622-8639`） | 一键配置客户端；写配置**仅 loopback + 面板会话**；`/agents/cli` 公开可下载源码 | `03` 新增「给本机客户端一键配置」；`04` 记「公开源码端点」 |
| E6 | `/update/check`、`/update/plan`、`/update/apply`（`:8653, 8673, 10290`） | 容器内 `apply` 默认拒绝（`qoder_updater.py:187-190`）；`REPO` 指上游（`:27`）→ 一键更新会**抹掉本地补丁** | `03` 加「容器内一键更新不可用，fork 必须 `QD_UPDATE_DISABLE=1`」；`04` |
| E7 | `/readyz` 真实语义**已回退补齐**（Batch-2.5） | 无就绪账号返 503（`9aa29ef:qoder_proxy.py:8983`）；上游基线本身是恒 200 别名（`v1.7.0:8529`） | `01` 探针说明；`30` 验证 |
| E8 | `/metrics` **已补齐**（Batch-2.5） | Prometheus 文本（`9aa29ef:qoder_proxy.py:9082`）；上游无 | `03` 观测节；`30` 验证 |

### 2.3 环境变量（新增 / 默认值，可照抄）

| 变量 | 默认 / 取值 | 用途 | 锚点 | 文档 |
|---|---|---|---|---|
| `QD_QUEUE_WAIT_MODE` | `visible` / `fast` / `wait` | 10605 排队策略：可见等待 / 快速失败 503 / 旧静默 sleep | `:5417-5419` | `03` 新增「排队」；`30` |
| `QD_QUEUE_MAX_ROUNDS` | **代码 3**（文档写 6） | 排队重试轮数 | `:5406` vs `docs/configuration.md:116` | `03`（以代码为准） |
| `QD_QUEUE_WAIT_MAX_SECS` / `…_BUDGET_SECS` / `…_JITTER` | 180 / 600 / 2.0 | 单轮上限 / 总预算 / 抖动 | `:5407-5409` | 同上 |
| `QD_TRAFFIC_GUARD` | 未设（`on`/`off`） | 对所有账号启用并发闸门（intl/机房出口建议 `on`） | `:6144-6157` | `03`；`30` |
| `QD_TEAM_*` | `auto` / 1 / 400ms / 30s / 3 | 团队账号闸门（v1.7.0 统一企业判定） | `:6090-6238` | `03` |
| `QD_DEVICE_ID_LEGACY` | `0` | `1`=保持 32 位 hex 设备码（**仅应急开关**，正常上线不设） | `qoder_fingerprint.py:31-40` | `05`；`30` |
| `QD_COSY_VERSION` / `QD_DESKTOP_VERSION` | `1.1.75` / `0.4.8` | 上报客户端版本（**变了就是指纹变了**） | `docs/configuration.md:165-166` | `30`（告知，不改） |
| `QD_COSY_OS_HEADERS` / `QD_COSY_MACHINE_OS` | `1` / 按平台 | 新增 `cosy-machineos` / `cosy-machinehostname` 头 | `configuration.md:168-169` | `30` |
| `QD_WEB_TOOLS` / `…_ROUNDS` / `…_PROXY` | 关 / 3 / 复用 `HTTPS_PROXY` | 模型驱动的本地 web 工具代跑（**会出网**） | `:6283-6288`、`qoder_webtools.py:137` | `03`；`04`（默认关，别开） |
| `QD_UPDATE_DISABLE` / `QD_UPDATE_ALLOW_REMOTE` / `…_CONTAINER` | 关 / 关 / 关 | 更新端点三开关 | `:3128-3134`、`qoder_updater.py:187` | `03`；`04` |
| `QD_MAX_BODY_MB` | `29` | 发往上游的体上限，超限本地 413 | `:5118` | `14` 篇 413 节补一句 |
| `QD_SSE_HEARTBEAT` | `5` | 等首字心跳间隔（长上下文别调大） | `configuration.md:120` | `03` |
| `QD_AUTO_REALM` | `1` | 未指定区域时自动换区（**本地「跨区错配 400」语义被改**） | `:175`、`docs/plan/00` §2.2.3 | `30` 记录行为变化 |
| `QD_LOG_JSON` | 关 | `1`=stdout 输出 JSON 结构化日志（Batch-2.5 重放） | `9aa29ef:qoder_proxy.py:2490-2494` | `03` 日志节；适合后续接采集 |

### 2.4 身份 / 设备码 / UMID / hostname

| 项 | v1.3.4（本地） | v1.7.0（上游） | 运维动作 | 文档 |
|---|---|---|---|---|
| 设备码形态 | 32 位无连字符 hex（`HEAD:qoder_fingerprint.py:20-36`） | 稳定派生的 **UUIDv4 形状**（`:43-61`） | **直接切（已拍板）**，不做回退演练；`QD_DEVICE_ID_LEGACY=1` 仅在应急回滚时使用（§3.4） | `02`（版本行）、`30` |
| 请求 ID | 稳定前缀 + 微秒 | 每请求随机 32 hex（`:64-72`） | 日志失去「按前缀追会话」能力 → `03` 排障思路要改 | `03` |
| UMID 身份 | 真身份组件 + `accounts/machine_identity.json` 落盘缓存 | 同机制，新增成文说明（`deployment.md:108-125`） | 该文件**必须随 `accounts/` 备份**；**不要在多机间复制**（一机一份） | `03` 备份节；`05` |
| HOME / 种子 | `adduser -H` 不建 home（`HEAD:Dockerfile:80`）→ 组件按 passwd 找 home 写 `$HOME/.config/.locale_cfg` 失败 → **每次调用重新注册** | 上游镜像是 root（HOME=`/root`），能写但**不持久**（容器层）→ 重建后仍重新注册 | 可选：compose 加 `HOME=/app/home` + 卷 + 固定 `hostname`（`v1.7.0:compose:16-19` 注释也建议固定） | `02`；`30` |
| hostname | 容器 ID（每次重建变） | 同上；`cosy-machinehostname` 取 `socket.gethostname()`（`qoder_accounts.py:158-160`） | 固定 hostname 可少一个变化维度，**但别照抄示例名** | `02` |

### 2.5 usage 落盘形态（备份 / 清理脚本要跟着改）

- 形态（本次发布即此形态，Batch-3 `0814c53` 已重放）：写 `usage-YYYY-MM-DD.jsonl`（UTC+8 计日），读侧 `_usage_log_files()` 联读旧 `usage.jsonl` + 全部日文件（`HEAD:qoder_proxy.py:670-681, 1101-1146`；`9aa29ef` 同源）。文件名实际是 `usage-2026-10-23.jsonl`（`HEAD:qoder_accounts.py:1162-1169`；**代码注释写 `YYYYMMDD` 是漂移**）。
- 上游基线只有 `usage.jsonl`（`v1.7.0:625-658`），读侧 6 处直读；这 6 处已在 Batch-3 改为链式读（`docs/plan/05` §2.4）。
- **运维后果**：万一读侧改漏，新行落 `usage.jsonl`、读侧看不到日文件 → **面板历史/成本/质量指标看起来「归零」且不报错**。上线后必须按 §3.3 V8/V9 验证；这是本次唯一「静默失败」型回归点。
- 要改：`02` §数据目录（列两种文件名）、`03` §备份（加 `usage/` 与 `pricing-overrides.json`）、`30`（上线后 `ls -la /opt/qoder2api-hub/usage/` 取证）。

### 2.6 版本号规则与发布

- 版本规则：补丁号累到 14 后升次版本（`v1.7.0:CHANGELOG.md:5-10`）；CI 强制 `v* tag == qoder_proxy.py 的 VERSION`（`.github/workflows/tests.yml:69-84`）。
- 口径（Q6/Q7 已定）：**镜像 tag 与 VERSION 都用 `1.8.0`**，篇名 `30-上游v1.7.0合流-本地v1.8上线记录-2026-10-XX.md`。
- 要改三处：`02` 容器表（镜像 tag 与运行版本分列，本次都写 1.8.0）、`README.md:57` 起现状段（补 qoder 版本行）、`05`（无）。
- **上线前硬检查**：`grep '^VERSION' qoder_proxy.py` = `"1.8.0"`；否则 CI tag 断言与 `/health.version` 都会对不上（§3.1 第 2 条、§3.3 V1）。【已验证：断言位置 `v1.7.0:.github/workflows/tests.yml:69-84`】

### 2.7 本地独有补丁（重建后必须逐项回归，否则等于静默降级）

| 补丁 | 本地锚点 | v1.7.0 基线状况 / 合流后 | 回归验证 |
|---|---|---|---|
| SIGTERM 优雅退出/排空 | `HEAD:qoder_proxy.py:8055-8072`、`:5927` | **无**（只有 `KeyboardInterrupt`，`:11051`） | `docker stop -t 30` 时日志出现 `SIGTERM received … draining` |
| 非 root 10001 | `HEAD:Dockerfile:80-83` | **无** | `docker exec qoder-proxy id -u` = 10001 |
| `/metrics` + 真 `/readyz` + `QD_LOG_JSON` | `HEAD:6406`、`:6346` | **已重放（Batch-2.5 `1172654`）** | `curl :8790/metrics` 非 404；无就绪账号时 `/readyz` = 503 |
| usage 按天轮转 | `HEAD:670-681` | **已重放（Batch-3 `0814c53`）** | `ls usage/` 出现 `usage-<今天>.jsonl`；`/pricing` 的 `unpriced` 不含常用模型 |
| flock / 调度 leader 锁 | `HEAD:qoder_accounts.py:108/209` | **无** | 双开第二份应退出 |
| 连接上限 / SSE 背压 | `HEAD:4623/4664` | 部分（队列无界） | 并发压测看 `QD_MAX_CONNECTIONS` 生效 |
| 凭据 0600 | `HEAD:qoder_settings.py:100` | **已重放（Batch-2.5）**：`9aa29ef:qoder_settings.py:95` | `stat -c %a accounts/settings.json` = 600 |

---

## 3. 上线检查清单（可直接照做）

### 3.1 前置（本地，不碰线上）

1. 构建：**不要**在 ECS 上 build（UMID 组件拉 npm 会卡死）——`03-运维手册.md:137-154`；本地 `docker build -t qoder2api-hub:<新tag> .`。
2. 门禁：`grep '^VERSION' qoder_proxy.py` 必须已是 `"1.8.0"`（Q7）；`python3 tests/run_all.py` 全绿；打 `v1.8.0` tag 时 CI 的 `tag==VERSION` 断言必须过（`tests.yml:69-84`）。
3. 镜像自检：`docker build -t qoder2api-hub:v1.8.0 .`；`docker run --rm qoder2api-hub:v1.8.0 python -c "import qoder_updater,qoder_pricing,qoder_agents,qoder_webtools"`；`docker inspect -f '{{.Config.Cmd}}' qoder2api-hub:v1.8.0` 应含 `--lan`。
4. 记录回滚点：旧镜像 ImageId `sha256:64e21035c92b`（确认仍在 ECS 本地，回滚不依赖重新拉取）。
5. 导出传输：`docker save qoder2api-hub:v1.8.0 | gzip > /tmp/qoder-v180.tar.gz` → `scp`（约 25–35 MB）。

### 3.2 上线步骤（窗口内，逐条留输出）

| 步 | 命令 | 期望 |
|---|---|---|
| P1 | `rsync -az -e 'ssh -i ~/.ssh/aliyun-ecs' root@106.14.203.201:/opt/qoder2api-hub/accounts ./backup-qoder-accounts-$(date +%Y%m%d)/` | 成功；**同时备份 `usage/` 与 `docker-compose.yml`** |
| P2 | `ssh … 'cp /opt/qoder2api-hub/docker-compose.yml{,.bak-$(date +%Y%m%d)}'` | 有 .bak |
| P3 | `ssh … 'docker inspect qoder-proxy --format "{{json .HostConfig.NetworkMode}}"'` | **取证网络模式**（§1.3 冲突）；顺带 `--format '{{json .Config.Env}}'` 看 `API_KEY/HOST/HTTPS_PROXY` |
| P4 | 见 **§3.2a**（一条命令判定鉴权落点） | 记下三字段组合 → 决定客户端是否要改 Key |
| P5 | `ssh … 'ls -la /opt/qoder2api-hub/usage/ \| head'` | 有 `usage-YYYY-MM-DD.jsonl` = 现网已在轮转（决定 §2.5 风险等级） |
| P6 | `ssh … 'chown -R 10001:10001 /opt/qoder2api-hub/accounts /opt/qoder2api-hub/usage'` | 返回 0；**这是 `--lan` 首启写盘的前提** |
| P7 | `ssh … 'gunzip -c /tmp/qoder-v170.tar.gz \| docker load'` | 输出新 ImageId，记录 |
| P8 | `sed -i 's#image: qoder2api-hub:.*#image: qoder2api-hub:<新tag>#' docker-compose.yml && docker compose up -d` | **必须 `up -d` 而不是 `restart`**——`restart` 不换镜像/不读新 CMD（`03:38` 旧写法要一并改） |
| P9 | `docker ps --filter name=qoder-proxy --format '{{.Status}}'` | `Up … (healthy)`、`restarts=0` |
| P10 | `docker logs --tail 60 qoder-proxy` | 无 traceback；若出现 `api key` 行，**取回后立即在面板换 Key**（§5 O5） |

### 3.2a 上线前一条命令判定鉴权落点（免面板密码）

**为什么要单独判**：`--lan` 之后 Key 有四级优先（`--api-key` > `API_KEY`/`QD_PROXY_KEY` > 面板保存的 Key > launcher），而**一旦面板里有启用中的 Key，launcher Key 就不再被接受**（`v1.7.0:qoder_proxy.py:446-453` 的 `identify_key`）。所以「日志里打印的 Key」不一定等于「最终接受的 Key」——**必须看字段，不要只看日志**。

```bash
docker exec qoder-proxy python -c "import json,os,urllib.request as u; p=json.load(u.urlopen('http://127.0.0.1:8790/panel/status')); h=json.load(u.urlopen('http://127.0.0.1:8790/health')); d=json.load(open('/app/accounts/settings.json')); print('auth_required=%s api_key_set=%s panel_keys=%d launcher=%s env_API_KEY=%s version=%s' % (int(h['api_key_required']), int(p['api_key_set']), len([k for k in d.get('api_keys',[]) if k.get('enabled',True)]), bool(d.get('launcher_key')), bool(os.environ.get('API_KEY')), h['version']))"
```

需要 **`api_key_set_by_panel`** 原值时（该字段只在面板设置视图里）：登录面板后 `curl -s http://127.0.0.1:8790/settings -H "X-Panel-Token: <token>"`，取 `api_key_set_by_panel`（`v1.7.0:qoder_proxy.py:2098-2104`）。

**判据表**（三字段组合 → 客户端是否要改 Key）

| auth_required | api_key_set | panel_keys(启用数) | env_API_KEY | 生效 Key 来自 | 客户端动作 |
|---|---|---|---|---|---|
| 1 | 1 | ≥1 | 0 | **面板保存的 Key**（`api_key_set_by_panel=1`） | **不用改**：继续用现网那把 Key |
| 1 | 1 | 0 | 1 | `API_KEY` 环境变量 | 与现网 Key 相同→不改；不同→改客户端（或把 env 改成原 Key） |
| 1 | 1 | 0 | 0 | **launcher**（首启生成，落 `accounts/settings.json`） | **必须改**：客户端换新 Key；或登录面板把 Key 改成原值（面板 Key 优先级更高，重启后仍生效） |
| 0 | 0 | — | — | 未启用鉴权（`auth_disabled=true`，或跑的根本不是 `--lan` 镜像） | 先排查镜像/CMD，**不要上线** |

> ⚠️ **已知陷阱（代码顺序导致）**：`--lan` 启动时先 `ensure_launcher_key()` 再让面板 Key 覆盖生效值（`:10845-10854`），而横幅在覆盖之后打印 `API_KEY`（`:10961-10980`）。若面板已有 Key，日志会打印**面板 Key**却仍带「(newly generated & saved to …)」字样——**光看日志会误判**。以 §3.2a 的字段为准。

### 3.2b 同窗口顺带复核（清单要求，但不属于本次代码改动）

| 项 | 命令 | 期望 / 处理 |
|---|---|---|
| **网络模式（Q1 核对）** | `ssh … 'docker inspect qoder-proxy --format "{{.HostConfig.NetworkMode}}"'`；`ssh … 'docker inspect qoder-proxy --format "{{json .NetworkSettings.Ports}}"'` | 期望看到 `127.0.0.1:8790` 发布；若为 `host` → 按 §5 O1 加 8790 封锁（不阻塞上线） |
| 证书 | `ssh … 'openssl x509 -enddate -noout -in /etc/nginx/ssl/sixiaoip.de5.net.fullchain.cer'`；`systemctl list-timers \| grep -i acme` | 到期 > 30 天；acme.sh cron 在（`02-云端状态.md:41`）。不足则先续期再上线 |
| NTP / 时钟 | `ssh … 'timedatectl'`；V1 的 `clock_skew_s` | `System clock synchronized: yes`；漂移 < 120 s，否则 401 |
| 出口代理 | 结合 P3 看 `HTTPS_PROXY`；bridge 模式下 `127.0.0.1:7890` 不可用 | 国内版直连不需代理；若要给 intl 用，改成宿主可达地址（如 `10.255.0.1:7890`）后再验证 |
| 安全组 | 控制台只保留 22 / 45421 | 未改动即可；**不要为 qoder 开 8790** |
| 磁盘 | `ssh … 'df -h /'` | 余量 > 5 GB（usage 日文件 + 新镜像 tar） |
| 回滚点 | 旧镜像 ImageId + compose .bak 路径写在 30 篇 | 两者都可在窗口内直接回滚 |

### 3.3 验证（全绿才算完成）

| # | 检查 | 命令 | 期望 |
|---|---|---|---|
| V1 | 进程内健康 | `curl -s http://127.0.0.1:8790/health` | 200；**`version=1.8.0`**；`accounts>0`；`env.clock_skew_s` 绝对值 < 120；`env.accounts_writable=true` |
| V2 | 鉴权三态 | 无 Key / 有 Key 调 `/v1/models` | 无 Key **401**，有 Key 200 |
| V3 | 面板 | `curl -s http://127.0.0.1:8790/panel/status` | `panel_password_is_default:false`、`api_key_set:true` |
| V4 | 公网入口 | `curl -sk https://qoder.sixiaoip.de5.net:45421/v1/models -H "Authorization: Bearer <key>"` | 200（两个域名各测一次） |
| V5 | 真实推理 | 一条短 `/v1/chat/completions` + 一条流式 | 200 / 正常流式；日志有 `chat done:` |
| V6 | Anthropic 入口 | `POST /v1/messages` | 200（#55 缓存修复生效） |
| V7 | 排队语义 | 触发 10605 时观察 | `visible` 模式有排队状态帧；**不再自动换号**（`CHANGELOG.md:361`） |
| V8 | usage 落地（轮转已重放，重点核） | `ls -la usage/ && tail -1 usage/usage-*.jsonl \| tail -1` | 新行落在**今天**的 `usage-<今天>.jsonl` 里；`/health.quality` 非全 0；面板「用量」有数 |
| V9 | 成本页 | `curl -s :8790/pricing -H …` | `ok:true`，`unpriced` 不含常用模型（含 = 轮转读侧没改） |
| V10 | 设备身份 | `docker exec qoder-proxy python -c "import qoder_fingerprint as f;print(f.derive_id('demo','machine'))"` | **带连字符、第 13 位 `4`、第 17 位 `a`**（`QD_DEVICE_ID_LEGACY` 未设；若输出 32 位无连字符 hex → 该变量被误设） |
| V11 | 非 root | `docker exec qoder-proxy id -u` | `10001`（若为 0 → 记入 30 篇遗留） |
| V12 | 文件权限 | `docker exec qoder-proxy stat -c %a /app/accounts/settings.json` | `600`（Batch-2.5 已补 chmod；若 644 → 挂载目录权限或旧镜像，见 §5 O6） |
| V13 | 暴露面 | 「服务器自身 mihomo 回连公网 IP」法（`17` 篇） | 8790 **不可达**；对照 `https://qoder….de5.net:45421/` 200 |
| V14 | 日志体积 | `docker inspect qoder-proxy --format '{{json .HostConfig.LogConfig}}'` | 有 `max-size/max-file`（否则本轮补） |
| V15 | 重启幂等 | `docker restart qoder-proxy` | V1/V2/V3 仍绿；**launcher key 不变**（settings.json 已落盘） |
| V16 | 观测端点（Q4 已重放） | `curl -s :8790/metrics \| head -5`；`curl -s -o /dev/null -w '%{http_code}' :8790/readyz` | `/metrics` 返回 Prometheus 文本（非 404）；有就绪账号时 `/readyz` 200 |

### 3.4 回滚（四层）

1. **镜像层**：`sed -i 's#image: qoder2api-hub:.*#image: qoder2api-hub:v1.4#' docker-compose.yml && docker compose up -d --no-build`。
2. **数据层断崖**：本次**已重放** usage 轮转（Batch-3）。回滚到旧镜像后，旧 reader 能否看到日文件取决于旧镜像是否含该补丁；不含则「回到升级前」显示（**数据不丢，只是不可见**）。**不要为了看起来一致去删日文件**；如需旧版继续可见，先把日文件内容 `cat` 追加回 `usage.jsonl`（注意别重复计数）。
3. **身份层（应急开关）**：设备码由二进制决定，**正常上线直接切 UUIDv4、不做回退演练（用户已拍板）**；`machine_identity.json` 不随回滚变化。仅当出现「上游把账号判成换设备并触发异常风控」时，才用 **应急开关** `QD_DEVICE_ID_LEGACY=1` 加回 compose 环境并重启（恢复旧 32 位 hex 形态，`v1.7.0:qoder_fingerprint.py:31-40`）；使用后须在 30 篇记录原因与时间窗。
4. **Key 层**：若升级生成了新 launcher key 并改过客户端，回滚后要改回原 Key（`05-凭据清单.md` 的 `qd-…`），或反向设置 `API_KEY`。
5. 回滚后重跑 V1/V2/V4/V13。

### 3.5 上线后 24h 观测清单（贴进 30 篇 §6）

| # | 观测项 | 命令 | 判据 | 异常处理 |
|---|---|---|---|---|
| O24-1 | usage 是否有量（轮转读侧） | `ssh … 'docker exec qoder-proxy sh -c "wc -l /app/usage/usage-$(date +%F).jsonl"'`；`curl -s :8790/usage -H "Authorization: Bearer $KEY"` | 行数随流量增长；`/usage` 的 tokens/total 非 0 | 有流量但行数不涨 → 先看 `docker logs \| grep -i "usage persist failed"`，再回滚 |
| O24-2 | 失败行三字段 | `docker exec qoder-proxy sh -c 'grep "\"error\": true" /app/usage/usage-*.jsonl \| tail -3'` | 每行含 `account` / `key_id` / `stream`（`v1.7.0:qoder_proxy.py:661-696`） | 缺字段 → #78 回归：面板「失败记录」看不出是哪个号/哪把 Key |
| O24-3 | 10605 排队与等待 | `docker logs qoder-proxy 2>&1 \| grep -cE "upstream queued\|still queued"`；`docker logs qoder-proxy 2>&1 \| grep "still queued (HTTP"` | 有等待日志，且**不再出现「换号」日志**（`:6520,6535,6635`） | 仍立即失败/换号 → 查 `QD_QUEUE_WAIT_MODE` 与 `visible` 模式客户端兼容性 |
| O24-4 | /health risk 计数 | `curl -s :8790/health \| python -c 'import json,sys;print(json.load(sys.stdin)["risk"])'` | `queue/rate/rejected` 有增长但可控；**`version_gate` 应恒 0** | 出现 `version_gate` = 430 客户端版本门 → 查 `QD_COSY_VERSION` 是否被改 |
| O24-5 | 容器日志体积 | `ssh … 'docker inspect -f "{{.LogPath}}" qoder-proxy \| xargs ls -lh'`；`docker inspect qoder-proxy --format '{{json .HostConfig.LogConfig}}'` | 单文件 < 10 MB、滚动 3 份 | 未配 logging → 补 compose `logging` 后 `up -d` |
| O24-6 | 设备码形态 | `docker exec qoder-proxy python -c "import qoder_fingerprint as f;print(f.derive_id('demo','machine'))"` | UUIDv4 形状（第 13 位 `4`、第 17 位 `a`） | 32 位 hex → `QD_DEVICE_ID_LEGACY` 被误设，移除后重启 |
| O24-7 | 账号 401/403 | `docker logs qoder-proxy 2>&1 \| grep -cE " 401\| 403"`；对照升级时间点 | 升级后**没有集中爆发** | 集中爆发先怀疑「换设备」（设备码变更），再怀疑出口/配额 |
| O24-8 | 容器资源 | `docker stats --no-stream qoder-proxy`；`docker ps --filter name=qoder-proxy` | 内存 < mem_limit（1g）、`restarts=0` | 逼近上限 → 查 SSE 有界队列/连接上限是否重放（Batch-2） |

> 24h 内每次观测把「时间 + 数值」记进 30 篇 §6；**只有 O24-1/2/3 是「不达标就必须回滚或热修」的硬项**，其余为趋势观察。

---

## 4. 新文档骨架：`30-上游v1.7.0合流-本地v1.8上线记录-2026-10-XX.md`

> 口径（Q6/Q7 已定）：篇名带上游基线 + 本地版本；正文首行注明「我方 **v1.8.0** = 上游 **v1.7.0** 基线 + 本地强化」。

```markdown
# 30 · 上游 v1.7.0 合流 + 本地 v1.8.0 上线记录（2026-10-XX）

> 操作窗口：2026-10-XX HH:MM–HH:MM（UTC+8）
> 镜像：qoder2api-hub:v1.4（sha256:64e21035c92b）→ **qoder2api-hub:v1.8.0**（ImageId sha256:…）
> 运行版本：1.3.4 → **1.8.0**
> 依据流程：03-运维手册.md §「qoder 网关升级流程」；检查项与映射：docs/plan/06-ops-sync.md
> 代码侧背景：docs/plan/05-upstream-merge.md（Batch-1…5）

## 1. 为什么升
（上游 v1.5.x–v1.7.0 取舍：#61 设备码 / #75 标头 / #55 Anthropic 缓存 / #71+#87 排队 / #43 credits / #90 企业判定；
 本地 7 类强化必须保留的理由见 06 §2.7）

## 2. 变更摘要（与本机运维相关的 8 条）
1) CMD --lan → 强制 Key；Key 来源 = ___
2) 端口发布仍 127.0.0.1:8790（+10.255.0.1），未跟随上游 0.0.0.0
3) HEALTHCHECK /ping；容器 STATUS 语义变化
4) 设备码 32hex → UUIDv4 形状（直接切；QD_DEVICE_ID_LEGACY 未设，仅应急）
5) usage 轮转：Batch-3 已重放 + 读侧 6 处已改；上线后 V8/V9 实测结果
6) /metrics、真 /readyz、QD_LOG_JSON、chmod 0600：Batch-2.5 已重放（记实测）
7) 一键更新：QD_UPDATE_DISABLE=1；REPO 指向
8) 信任库挂载 /opt/ca/alpine 与新镜像 CA 的关系

## 3. 执行步骤与实测（表格：步 / 操作 / 输出摘要 / 判定）

## 4. 验证记录（V1–V15 全表，逐条贴输出摘要）

## 5. 暴露面与安全复核
- 外网 8790 不可达（方法：mihomo 回连法，对照 45421）
- 面板默认密码 / Key 来源 / settings.json 权限 / 日志是否含明文 Key
- /health 公开字段清单（version/accounts/risk/env，无密钥）

## 6. 上线后 24h 观测（按 06-ops-sync.md §3.5 的 O24-1…8 逐项记录）
- /health.risk 的 queue/rate/rejected 计数趋势
- 10605 排队率、平均等待、visible 模式客户端兼容性
- usage 日文件数量与大小（磁盘余量）
- 账号 401/403 事件是否集中在升级后（区分「换设备」与真失效）
- 容器内存/重启次数（宿主 1.8 GB，mem_limit 是否仍 1g）

## 7. 回滚（镜像 / 数据 / 身份 / Key 四层，见 06 §3.4）

## 8. 遗留与后续
- 设备码 legacy 窗口何时关闭
- /metrics、/readyz 是否补回
- 面板默认密码硬门控（docs/plan/07 H4）是否落地
- usage 纳入定期备份
- 版本命名口径统一
```

---

## 5. 安全不回归清单（`--lan` 的暴露面逐条）

> 与 `docs/plan/07-docker-auth.md` 的 H1–H8 对齐，这里给**运维侧**验证与失败处理（O1–O10）。

| # | 风险 | 判据 | 验证命令 | 失败处理 |
|---|---|---|---|---|
| O1 | 监听 0.0.0.0 带来新暴露 | **Q1 已定：发布口保持 `127.0.0.1:8790:8790` → 无新增暴露面**；若 inspect 为 `host` 网络则例外（`ports` 失效） | `docker inspect qoder-proxy -f '{{.HostConfig.NetworkMode}} {{json .NetworkConfig.Ports}}'`；`ss -lntp \| grep 8790` | host 网络：改 compose 为端口发布或加 `-p 127.0.0.1:8790:8790`；临时 `iptables -I INPUT -i eth0 -p tcp --dport 8790 -j DROP` 并写进 `qd-firewall.sh` |
| O2 | 端口发布被改成 `8790:8790` | 上游 compose 就是这么写的（`v1.7.0:docker-compose.yml:22`） | 同 O1 | **不要**把仓库 compose 同步到生产（`03:156-160` 已规定排除） |
| O3 | 无 TLS 直连 8790 | 8790 只应经 nginx 暴露 | V13 外部探测 = 000 | 立即恢复回环发布 + `qd-firewall.sh` |
| O4 | 面板默认密码 `admin` | `--lan` 下仅 WARN 不阻断（`:10858-10865`） | `curl -s :8790/panel/status` → `panel_password_is_default` 必须 false | 面板内改密码或 `--panel-password`；07 的 H4 硬门控**尚未实现，不能假设有** |
| O5 | 自动生成的 Key 进日志 | `--lan` 首启会打印完整 Key（含 `?key=` URL） | `docker logs qoder-proxy 2>&1 \| grep -Ei 'api key\|\?key='` | 取回后**立刻在面板改 Key** 并更新客户端；07 H3 脱敏未实现前这是已知缺口 |
| O6 | `settings.json` 落盘权限 | 上游 `save()` 无 chmod（`v1.7.0:qoder_settings.py:53-65`）；**Batch-2.5 已补回**（`9aa29ef:qoder_settings.py:95`） | `stat -c %a accounts/settings.json` | 立即 `chmod 600`；镜像里补回 chmod（应属 Batch-1） |
| O7 | `/health` 免鉴权信息面 | 版本号、账号数、风控计数、时钟漂移、`container`/`proxy` 布尔（`:5685-5700` 已核对无密钥） | `curl -sk https://qoder….de5.net:45421/health \| head -c 400` | 可接受；要收敛则 nginx 对 `/health` 加 `allow 127.0.0.1; deny all;` 或只保留 `/ping` |
| O8 | 日志无上限 | v1.7.0 日志更密（队列/风控/自检） | V14 | compose 补 `logging: json-file max-size: 10m max-file: 3` |
| O9 | 信任库挂载遮住新镜像 CA | `/opt/ca/alpine` 是 2026-10-23 从旧镜像摘的 120 张（`信任库挂载修复:107,164`） | `docker exec qoder-proxy grep -c BEGIN /etc/ssl/certs/ca-certificates.crt`；canary 应 REJECT | 若新 base 更新，按**指纹**重建 `/opt/ca/alpine`（勿用字符串 grep） |
| O10 | 时钟漂移 401 | `/health.env.clock_skew_s` 绝对值 > 120 → 401 | V1 | 宿主 `timedatectl` 已同步；容器继承宿主时钟，仍要看自检行 |

---

## 6. 决策记录（Lead 2026-10-23）与剩余未决

| # | 事项 | 决策 | 落点 |
|---|---|---|---|
| Q1 | 网络模式与 `--lan` 暴露面 | **已定**：结论写死「生产 compose 发布保持 `127.0.0.1:8790:8790`，故 `--lan` 不新增暴露面」；inspect 命令留在 §3.2b 供上线核对（若 host 则按 O1 加封锁） | §0-1、§1.3、§3.2b、§5 O1 |
| Q2 | 设备码是否保留旧形态 | **已定**：直接切 UUIDv4，不做回退演练；`QD_DEVICE_ID_LEGACY=1` 仅作应急开关写进回滚章节 | §0-3、§2.4、§3.4-3 |
| Q3 | usage 轮转是否随本次上线 | **已定**：随本次上线（Batch-3 `0814c53` 已重放，上游 6 处直读已改）；上线后按 V8/V9 实测 | §0-4、§2.5、§3.3 |
| Q4 | `/metrics`、真 `/readyz`、日志与权限 | **已定**：Batch-2.5 `1172654` 已重放（`/metrics` + 真 `/readyz` + `QD_LOG_JSON` + `chmod 0600`），文档按「已有」写 | §0-7、§2.2 E7/E8、§2.3、§2.7、§3.3 V16 |
| Q5 | 一键更新：改 REPO 还是禁用 | **未拍板**（本文件建议 A：`QD_UPDATE_DISABLE=1`，容器内本也拒绝 apply；fork 发 release 才需要 B） | §2.2 E6、§5 |
| Q6 | 篇名/版本口径 | **已定**：`30-上游v1.7.0合流-本地v1.8上线记录-2026-10-XX.md`；正文写「我方 v1.8.0 = 上游 v1.7.0 基线 + 本地强化」 | §4 |
| Q7 | 镜像 tag 与 VERSION | **已定：`v1.8.0`**，VERSION 同步改 `1.8.0`（避免与上游 v1.7.0 撞名）；上线前用 §3.1-2 硬检查 | §2.6、§3.1 |
| Q8 | `/health` 是否对公网收敛 | **未拍板**（本文件建议 A 保持：无密钥；若担心账号数/版本号泄露再用 B） | §5 O7 |
| Q9 | Batch-5 剩余（updater REPO、删 `qoder-proxy.pid`、文档树）由谁做 | **未排期**，建议上线前完成或明确「本次不带」 | §2.1 D6、§2.2 E6 |

---

## 附录 A · 证据索引（可复查）

| 主题 | 锚点 |
|---|---|
| CMD `--lan` | `v1.7.0:Dockerfile:85`；`v1.7.0:CHANGELOG.md:638` |
| HEALTHCHECK | `v1.7.0:Dockerfile:73-74`；`v1.7.0:docker-compose.yml:38` |
| Key 优先级 / launcher key | `v1.7.0:qoder_proxy.py:10839-10854`；`qoder_settings.py:128-145` |
| 面板默认密码告警 | `v1.7.0:qoder_proxy.py:10858-10865`；公开判据 `:8599-8608` |
| 设备码 UUIDv4 / legacy | `v1.7.0:qoder_fingerprint.py:31-61`；`CHANGELOG.md:370-380` |
| hostname 头 | `v1.7.0:qoder_accounts.py:141-160` |
| 时钟自检 120s | `v1.7.0:qoder_proxy.py:10904-10916`；`qoder_accounts.py:1346-1358` |
| 排队 / traffic guard | `v1.7.0:qoder_proxy.py:5396-5419`、`:6144-6208` |
| `/health` 字段 | `v1.7.0:qoder_proxy.py:8598-8625`、`env_report:5685-5700` |
| `/pricing` 读 usage | `v1.7.0:qoder_proxy.py:8692-8718` |
| 上游 usage 单文件写 | `v1.7.0:qoder_proxy.py:625-658`；`qoder_accounts.py:1143-1207` |
| 本地轮转写/读 | `HEAD:qoder_proxy.py:670-681`、`:1101-1146`；`HEAD:qoder_accounts.py:1162-1189` |
| 本地 SIGTERM | `HEAD:qoder_proxy.py:8055-8072` |
| 本地非 root | `HEAD:Dockerfile:80-83` |
| 本地 chmod 0600 | `HEAD:qoder_settings.py:87-100` |
| 更新器 REPO / 容器拒绝 | `v1.7.0:qoder_updater.py:27`、`:184-190` |
| 鉴权字段与优先级 | `v1.7.0:qoder_proxy.py:436-453`（auth_required/identify_key）、`:2098-2104`（/settings 视图三字段）、`:8527`（/panel/status api_key_set）、`:10839-10854`（优先级） |
| 10605 排队日志 | `v1.7.0:qoder_proxy.py:6520,6535,6635`（queued/still queued）、`:5818,5855`（信封排队） |
| 失败行三字段 | `v1.7.0:qoder_proxy.py:660-696`（record_error：account/key_id/stream） |
| 合流批次（rebuild） | `7e76ceb` Batch-1（SIGTERM）、`aba41ae` Batch-2（连接/队列）、`1172654` Batch-2.5（ops/security）、`0814c53` Batch-3（usage 轮转）、`9aa29ef` Batch-4（flock/leader） |
| 版本规则 / CI 断言 | `v1.7.0:CHANGELOG.md:5-10`；`.github/workflows/tests.yml:69-84` |
| 生产挂载 / 信任库 | `安全分析/2026-10-23-容器信任库挂载修复.md:16,132-138,164` |
| 生产端口 / 外网 | `安全分析/2026-10-22-external-exposure.md:432` |
| 代理变量 | `安全分析/2026-10-22-tls-client-matrix.md:279-280` |
| 线上镜像 / 运行时长 | `安全分析/2026-10-22-upgrade-maintenance.md:828`；`2026-10-23-mihomo升级记录.md:238` |
| 运维入口 / 备份 / 升级 | `01-访问入口.md:10-28`；`02-云端状态.md:17-49,82,139-160`；`03-运维手册.md:38,85-160` |
| 凭据 / 安全记录 | `05-凭据清单.md:15-23`；`04-安全加固记录.md:54-58,83-87` |
| 合流策略 / 契约 | `docs/plan/05-upstream-merge.md` §2.4/§2.5/§5.5/§5.7；`docs/plan/07-docker-auth.md` §1-§3 |
| 上游自带运维文档 | `v1.7.0:docs/deployment.md:91-125,127-151`、`docs/configuration.md:87-169` |

## 附录 B · 现有运维文档的漂移（本次一并纠偏）

| # | 位置 | 现在写的 | 事实 | 处理 |
|---|---|---|---|---|
| B1 | `02-云端状态.md:21` | qoder-proxy「host 网络」 | 外部暴露面报告显示 `127.0.0.1:8790` + `10.255.0.1:8790` 双发布（bridge 特征） | 上线 inspect 后改写（Q1） |
| B2 | `02-云端状态.md:21` | 镜像 `qoder2api-hub:v1.4` | 当前运行版本 `/health.version=1.3.4`（本地常量未升）；**本次发布后两列都应是 `1.8.0`** | 改为「镜像 tag / 运行版本」两列，上线时同时更新 |
| B3 | `03-运维手册.md:38` | 重启用 `docker compose restart` | `restart` 不换镜像/CMD，升级必须 `up -d` | 拆成「重启」与「升级」两条 |
| B4 | `03-运维手册.md:144-147` | 示例 tag `v1.5` | 线上是 `v1.4`，且缺取证与回滚点步骤 | 用 §3.2 十步替换 |
| B5 | `03-运维手册.md:113-115` | 备份只列 `accounts/` | `usage/`、`pricing-overrides.json`、`machine_identity.json` 未提 | 补全并说明「一机一份 identity」 |
| B6 | `04-安全加固记录.md:54-58` | v1.4 强化清单（含 `/metrics`、`/readyz`、非 root） | 重建后上游没有这些 | 改为「升级后必须逐条回归」，记录本次结果 |
| B7 | `README.md:57` 起「一句话现状」段 | 只写了 codebuddy（v1.3.6/镜像）、AxonHub、modelprobe 的版本，**没有 qoder 网关的版本/镜像行** | qoder 版本只存在于 `01:27`、`02:21` | 在现状段补一行「qoder 网关：镜像 tag / 运行版本 / 上线日期」 |
| B8 | 本文件 | — | `QD_QUEUE_MAX_ROUNDS` 文档 6 / 代码 3；`usage_file_for_day` 注释写 YYYYMMDD、实际 YYYY-MM-DD；`/panel/status` 的 `api_key_set` 是「有生效 Key」而非「面板里存了 Key」 | 已在 §2.3/§2.5/§3.2a 标注：**以代码为准** |
