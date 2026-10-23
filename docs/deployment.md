# 部署

- [本机运行](#本机运行)
- [局域网共享](#局域网共享)
- [Docker](#docker)
- [出口代理](#出口代理)
- [数据目录](#数据目录)
- [升级](#升级)

## 本机运行

只需要 Python 3.9+，不用 `pip install` 任何依赖。

| 脚本 | 平台 | 行为 |
|---|---|---|
| `start-qoder-proxy.bat [端口]` | Windows | 后台分离运行。可以关掉启动窗口，日志写入 `qoder-proxy.log` / `qoder-proxy.err.log` |
| `stop-qoder-proxy.bat` | Windows | 按 `qoder-proxy.pid` 停止。只停命令行里含 `qoder_proxy.py` 的那个进程，不会误杀其他 Python |
| `./start-qoder-proxy.sh [端口]` | Linux / macOS | 前台运行，终端就是进程的生命周期，`Ctrl+C` 或关闭终端即停止；同时写 `qoder-proxy.pid` |
| `./stop-qoder-proxy.sh [端口]` | Linux / macOS | 按 `qoder-proxy.pid` 停止（先 TERM，10s 不退再 KILL）。只停命令行里含 `qoder_proxy.py` 的那个进程；pid 文件缺失时按「进程名 + --port」兜底，不会误杀编辑器或另一份检出 |

默认端口是 **8790**，要改就在后面跟端口号，例如 `start-qoder-proxy.bat 8791`。Linux / macOS 首次使用前先执行 `chmod +x *.sh`。

脚本会自动寻找 Python，查找顺序如下：

1. 脚本目录下的便携运行时 `python/`
2. PATH 上的 `python3` / `python`；Windows 上还会找 `py` 启动器
3. Codex 自带的运行时

Linux / macOS 可以用 `QD_PROXY_PYTHON=/usr/bin/python3.12 ./start-qoder-proxy.sh` 指定解释器。

也可以直接运行：

```bash
python qoder_proxy.py --port 8790
```

## 局域网共享

| 平台 | 命令 |
|---|---|
| Windows | `start-qoder-proxy-lan.bat [端口] [API Key]` |
| Linux / macOS | `./start-qoder-proxy-lan.sh [端口] [API Key]` |

两个脚本都在**前台**运行，关闭窗口即停止。

- 监听所有网卡，Base URL 是 `http://<本机局域网 IP>:8790/v1`
- 局域网模式必须使用 API Key。不指定时，首次启动会生成一个 `qd-` 开头的随机 Key，保存到 `accounts/settings.json`（0600），之后重启继续使用同一个
- **启动横幅默认只打印掩码**（`qd-****************abc`），也不再打印带 Key 的看板链接：登录看板后从「设置」页复制 Key。个人桌面用户想恢复上游那种完整 Key + `?key=` 链接，设 `QD_LOG_SENSITIVE=1`
- 其他设备要带 Key 打开看板时，自行拼 `http://<IP>:8790/?key=<API Key>`（Key 从设置页复制）
- 如果面板还在用默认密码，启动时会打印警告，看板也会拒绝显示明文 Key。请先修改面板密码。要让网关**拒绝启动**而不是只告警，见下面 Docker 的 `QD_REQUIRE_PANEL_PASSWORD`

其他设备连不上时，放行防火墙：

| 平台 | 命令 |
|---|---|
| Windows | 运行 `allow-firewall.bat [端口]`（会弹出 UAC 请求管理员权限） |
| Linux / macOS | `sudo ./allow-firewall.sh [端口]`（自动识别 ufw / firewalld / nftables / iptables） |
| WSL2 | 在 WSL 内运行 `allow-firewall.sh`，它会打印 Windows 侧还需要执行的防火墙规则和 `netsh portproxy` 命令 |

## Docker

镜像发布在 GHCR，支持 `linux/amd64` 和 `linux/arm64`。

首次部署先写面板密码（容器以 `--lan` 启动，对外发布端口时默认密码不可接受；
仓库的 `docker-compose.yml` 已设 `QD_REQUIRE_PANEL_PASSWORD=1`，不写就拒绝启动）：

```bash
install -m 600 /dev/null qoder.env      # 可选：API_KEY= 放这里，别写进 compose
docker compose run --rm qoder-proxy python -c \
  "import qoder_settings; qoder_settings.set_panel_password('/app/accounts', '<新密码>')"
docker compose pull && docker compose up -d && docker compose logs -f
```

从源码构建：把 `docker-compose.yml` 里的 `image:` 注释掉，改用 `build: .`，然后执行 `docker compose up -d --build`。

不用 compose 的话（**端口仍然只发布到回环**，与 compose 一致）：

```bash
docker run -d --name qoder-proxy --restart unless-stopped \
  -p 127.0.0.1:8790:8790 \
  -e QD_REQUIRE_PANEL_PASSWORD=1 \
  -v "$(pwd)/accounts:/app/accounts" \
  -v "$(pwd)/usage:/app/usage" \
  ghcr.io/lx2000wasd/qoder2api-hub:latest
```

**镜像 tag**（[包页面](https://github.com/LX2000WASD/qoder2api-hub/pkgs/container/qoder2api-hub)）：

| Tag | 含义 |
|---|---|
| `latest` | 最新发布版 |
| `1.5` | 1.5.x 系列的最新版 |
| `1.5.11` | 固定版本 |
| `sha-<短提交>` | 固定到某次构建 |

**容器配置：**

- 容器以 `--lan` 启动，会强制使用 API Key：要么通过 `API_KEY` 环境变量传入（建议 0600 的 `env_file`），要么在首次启动时自动生成并写入 `./accounts/settings.json`（0600）
- **端口只发布到宿主机回环**：`127.0.0.1:8790:8790`。容器内仍监听 `0.0.0.0`（`--lan` 语义），对外入口是反代（nginx + TLS）。写成 `8790:8790` 会把网关连同面板发布到所有网卡——上游 v1.7.0 的默认值，本 fork 不跟随
- **面板默认密码门控**：`QD_REQUIRE_PANEL_PASSWORD=1` 时，监听 `0.0.0.0` 且面板密码仍是 `admin` 会**拒绝启动**（退出码 2，日志给出修复命令）；仓库的 compose 已设。默认 `0` = 上游行为（只 WARN）
- 启动日志只打印**掩码** Key（`qd-****************abc`）：登录看板 → 设置页复制；`QD_LOG_SENSITIVE=1` 才按上游原样打印完整 Key 和 `?key=` 链接
- 环境变量：`HOST`（默认 `0.0.0.0`）、`PORT`（默认 `8790`）、`API_KEY`、`TZ`（默认 `Asia/Shanghai`）、`QD_REQUIRE_PANEL_PASSWORD`、`QD_LOG_SENSITIVE`、`QD_UPDATE_DISABLE`。改 `PORT` 时要同步修改 `ports:` 映射
- 需要持久化的两个目录：`/app/accounts` 和 `/app/usage`。NAS 或长期部署建议挂载绝对路径
- 镜像自带健康检查，探测的是 `/ping`；`QD_UPDATE_DISABLE=1` 是镜像默认（一键更新端点返回 403）

### 鉴权与 Key 轮换

Key 的生效优先级（高 → 低），启动日志只打印**来源**、不打印值：

| 顺位 | 来源 | 怎么设置 | 日志 `key_source=` |
|---|---|---|---|
| 1 | 命令行 `--api-key` | compose `command:` 里显式加 | `cli` |
| 2 | 环境变量 `API_KEY`（或 `QD_PROXY_KEY`） | 0600 的 `qoder.env` + `env_file:` | `env` |
| 3 | 面板保存的 Key | 看板 → 设置 → API Key | `panel` |
| 4 | 自动生成的 launcher Key | 首启兜底，落 `accounts/settings.json` | `launcher` |

- **面板里有启用中的 Key 时，launcher Key 不再被接受**（`identify_key` 的既有语义）：日志里的 Key 不一定等于最终接受的 Key，以 `/health` 的 `api_key_required` 和设置页为准
- 生产把 Key 放 0600 的 `qoder.env`，不要写进 `docker-compose.yml` 明文（这个文件通常提交进 git）
- 轮换：看板 → 设置改成新 Key 并保存（立即生效，旧 Key 失效）；或重启时带 `--api-key <新 Key>`。改完同步客户端
- 只想换自动生成的 Key：删掉 `accounts/settings.json` 里的 `launcher_key` 字段（或删文件）后重启；注意这会同时重置面板密码

### 容器启动自检

有几类问题会让网关「莫名失效」，在容器里尤其常见。网关每次启动都会检查，发现问题就在日志里打印 WARN，结果也可以在 `/health` 的 `env` 字段中查看：

| 检查 | 为什么重要 |
|---|---|
| 与上游的时钟偏差超过 120 秒 | COSY 签名带时间戳，时钟偏差太大会让所有请求直接 401。同步宿主机时间即可 |
| `accounts/` 不可写 | 设备身份无法落盘，每次重建容器都会换一台「设备」 |
| `accounts/settings.json` 的权限不是 `600` | 文件里是 API Key 明文，同机其他用户可读。执行 `chmod 600 accounts/settings.json`（挂载目录被宿主机改过权限时最常见） |
| 容器里启用了国际版账号（只在容器中检查） | 数据中心出口访问国际版网关容易被风控，表现为聊天一直排队。建议改用住宅出口，或改用国内版，见[风控处置手册](risk-control-playbook.md) |
| `umid` 自检是 `present=no`，或 `token_age` 一直很小 | 镜像里没有原生组件 / 容器 home 不可写 → 每次调用都重新向 UMID 注册。签到与活动链路可能被上游过滤，见下文「容器里的 UMID」 |

启动日志还有四行自检（**不含任何敏感值**，可直接贴进工单）：

```text
auth       : required=1 key_source=env | panel_default=0 | bind=0.0.0.0:8790
security   : settings.json mode=600 | updater=disabled | sensitive_log=0
ingress    : published=127.0.0.1:8790 (expected) | tls=external(nginx)
identity   : device_id=uuidv4 | legacy=0 | umid=present(age=2h)
```

- `auth.key_source` 取 `cli` / `env` / `panel` / `launcher`，与上面「鉴权与 Key 轮换」的优先级表对应；`panel_default=1` 表示面板密码还是 `admin`
- `ingress` 在容器内且监听 `0.0.0.0` 时按「回环发布 + 外部反代」推断；裸跑（非容器）会打印 `0.0.0.0:<port> (all interfaces)`，提示端口对所有网卡可见
- `umid=present(age=…)` 的 age 是 `accounts/machine_identity.json` 的缓存年龄；`missing` 表示镜像里没有原生组件（功能降级为派生身份，签到链路可能被上游过滤）

### 容器里的设备身份

推理请求使用按账号 UID 派生的设备身份，跟容器无关。**只有签到 / 活动链路**需要官方 UMID 组件给出的真实机器身份：镜像构建时已经从官方 npm 包里提取了这个组件。

这个身份是「容器级」的，容器一重建就会变。为此网关会把首次拿到的身份缓存到 `accounts/machine_identity.json`，之后一直复用。所以：

- 只要 `accounts/` 是挂载卷，重启、重建、升级容器都不会更换设备身份
- 不要在多台机器之间共享同一个 `accounts/`，这个文件代表的是一台物理设备

相关开关：

| 变量 | 作用 |
|---|---|
| `QD_MACHINE_IDENTITY_CACHE=off` | 关闭落盘缓存 |
| `QD_MACHINE_IDENTITY_RESET=1` | 启动时清空缓存并换一个新身份（直接删除缓存文件效果相同） |
| `QD_MACHINE_IDENTITY_CACHE_TTL=<秒>` | 定期轮换身份，默认 `0` 表示永不过期 |

如果你不挂载 `accounts/`，又希望重启后身份不变，可以在 compose 里固定网卡 MAC（`mac_address:`，请自己生成一个，不要照抄示例），或者改用 `network_mode: host`。这两种做法都只能保证「重启不变」，重建容器还是会换身份。

### 容器里的 UMID：HOME、hostname 与 shim 灰度（v1.8.0 起）

官方 `runtime-info` 并不是「本地探测器」，而是阿里 UMID 的**联网注册客户端**：
`machineToken` 由服务端签发（注册时连 `pum.m.alibaba.com`），注册缓存写在
**passwd home** 的 `~/.config/.locale_cfg`（350 B JSON）。因此有两个推论：

1. 断网 + 无缓存时组件**没有输出**（`sgsdk init callback reported error code 6`），网关会回退到
   `accounts/machine_identity.json`，再不行才退派生身份；
2. 容器里如果 home 不可写，每次调用都会**重新注册**（token 每次都变），身份只能靠
   `accounts/machine_identity.json` 兜底。

> 以上结论与判据见 [docs/plan/02-vm-identity.md](02-vm-identity.md)；`vmInfo`（`isVm` / `brand` /
> `vmTypeCode`）是**本地打分、不上行**，实体机也会误报（KVM 模块、Windows VBS/HVCI），
> 所以它不是封号信号，本仓库**不做任何 DMI / VM 伪装**。

**镜像与编排（默认行为，无需配置）**

- 镜像里 `adduser -h /app/home`（不是 `-H`）+ `ENV HOME=/app/home`：passwd home 与 `$HOME`
  **指向同一处**。这一点必须一致 —— `_purge_identity_seed()` 用 `expanduser("~")` 清种子，
  两者不一致时会清不到组件真正的缓存，「换身份」自愈会空转；
- `docker-compose.yml` 挂 `./accounts/umid-home:/app/home`：注册缓存跨重建保留（放在 `accounts/`
  子目录里复用已有持久卷与 `.gitignore`；`AccountPool.load()` 只扫顶层 `*.json`，子目录不会被当成账号）；
- 首次启动前 `sudo chown -R 10001:10001 ./accounts ./usage`（非 root 运行）。

**自检**：启动日志多一行（不含任何 token 内容，可直接贴工单），`/diag/vm` 返回同源字段：

```text
umid: present=yes source=native shim_active=0 token_age=2h vm_brand=KVM vm_type_code=13
```

| 字段 | 含义 |
|---|---|
| `present` | 组件（或 shim）是否可用；`no` = 镜像里没有提取到，功能降级 |
| `source` | `native`（官方组件）/ `shim`（QD_UMID_SHIM）/ `none` |
| `shim_active` | 本次是否走 shim（`0` = 正常路径） |
| `token_age` | `accounts/machine_identity.json` 里该区域身份的缓存年龄 |
| `vm_brand` / `vm_type_code` | 缓存里的本地 VM 判定（仅诊断） |

- 启动自检**不执行组件**（只读路径与缓存），不会拖慢启动；要看实时判定用 `/diag/vm?force=1`
- `hostname:`（compose 里默认注释）可选固定：官方桌面端的 `Cosy-MachineHostname` 取本机 hostname，
  容器默认是容器 ID，每次重建都变。填一个**你自己的**名字，不要照抄示例（共用同一个值反而是共同指纹）

**shim 灰度（默认关，仅用于实验）**

`QD_UMID_SHIM=<可执行文件>` 让网关用你自己的程序替代官方组件，契约完全一致：

```text
argv : <exe> <0|3> [--account-stdin]      # 0=国内 / 3=国际
stdin: {"account":"<uid>"}\n             # 仅 darwin/win32 且带 account 时
stdout: 单行 JSON，至少 machineToken/machineType/machineCode
exit : 0=成功；非 0 且无输出 -> 网关按「组件不可用」回退缓存/派生
```

示例（不内置任何 token，JSON 由你从环境变量或 0600 文件提供）：

```yaml
    environment:
      - QD_UMID_SHIM=/opt/umid-shim/umid_shim_example.sh
    volumes:
      - ./scripts/umid_shim_example.sh:/opt/umid-shim/umid_shim_example.sh:ro
```

```bash
QD_UMID_SHIM_JSON_FILE=/app/home/umid-shim.json   # 0600；不要写进 git / 镜像层
```

命中 shim 时 stderr 打一次 `[umid] shim_active=1`（启动那行也会显示 `shim_active=1`）。

> ⚠️ **风险（务必读完再开）**：服务端是否拿 `machineToken` 反查 UMID **未经验证**。
> 若反查，假 token 可能让活动列表整条消失 —— 比「不发机器头」（已验证仍能看到通用活动）更差。
> 因此 shim 只用于**单账号灰度观测**，默认关闭、随时可回滚；不要用它伪造物理机/虚拟机
> （`vmInfo` 不上行，伪造没有收益），也不要把 shim 当作生产方案。

**回滚**：去掉 `QD_UMID_SHIM` 与对应挂载即回到官方组件；要同时换回身份就
`QD_MACHINE_IDENTITY_RESET=1`（或删 `accounts/machine_identity.json`）。

## 出口代理

网关访问上游时，**只读取环境变量里的代理**：`HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`（大小写均可）。没有设置时直接连接。每个请求都会重新读取，所以开关代理不需要重启网关。

- **Windows 不读取系统代理**（即注册表 / WinINET 里的设置）。如果你的机器必须通过系统代理才能上网，需要显式设置一次，然后重启网关：
  ```bat
  setx HTTPS_PROXY http://127.0.0.1:7897
  ```
- TUN / fake-ip 模式的透明代理（例如 Clash）无需任何设置，网关能识别 fake-ip 网段
- Docker 用 `-e HTTPS_PROXY=...` 传入

## 数据目录

| 路径 | 内容 |
|---|---|
| `accounts/*.json` | 每个账号一个文件，**Token 以明文保存**，不要外传或同步到网盘 |
| `accounts/settings.json` | 面板密码摘要、API Key、模型闸门、限额配置 |
| `accounts/machine_identity.json` | 缓存的机器身份 |
| `accounts/models-cache-<区域>.json` | 上次成功获取的官方模型清单，7 天内有效。上游暂时不可用时用它兜底 |
| `accounts/agent-backups/` | 智能体一键配置写入前的原文件备份 |
| `accounts/scheduler/state.json` | 调度器状态 |
| `usage/usage.jsonl` | 请求流水 |
| `usage/usage-summary.json` | 用量汇总 |

两个目录的位置可以分别用 `--accounts-dir`（或 `ACCOUNTS_DIR`）和 `--usage-dir`（或 `QD_PROXY_USAGE_DIR`）修改。

## 升级

| 部署方式 | 升级步骤 |
|---|---|
| **一键更新（源码部署）** | 看板「运行信息」→ 有新版本时点 **一键更新**，见下 |
| 源码（手动） | `git pull`，然后重启网关：Windows 先 `stop-qoder-proxy.bat` 再 `start-qoder-proxy.bat`；
Linux / macOS 先 `./stop-qoder-proxy.sh` 再 `./start-qoder-proxy.sh`（常驻/launchd 场景直接 restart 也可） |
| Docker | `docker compose pull && docker compose up -d`。**v1.8.0 起** compose 默认 `127.0.0.1:8790:8790` + `QD_REQUIRE_PANEL_PASSWORD=1`：面板若还是默认密码，先按上面「Docker」一节写密码，否则容器拒绝启动（日志会给出修复命令） |

各版本的变更见[更新日志](../CHANGELOG.md)。

### 一键更新（看板按钮）

检测到新版本后，「运行信息」卡片会出现 **一键更新** 按钮。点击后网关自己完成：

1. 从 **GitHub 官方 release** 下载源码 tarball（只认这一个来源）；
2. 解压到临时目录，**逐个 `.py` 编译校验**，并核对 `VERSION` 必须等于目标 tag；
3. 把本次会被覆盖的文件**先备份**到 `backups/update-<旧版本>-<时间戳>/`；
4. 覆盖代码文件后**自动重启**（按原启动参数拉起新进程）；
5. 页面轮询 `/health`，版本变化后自动刷新。

**不会被覆盖的**：`accounts/`（账号与设置）、`python/`（内置运行时）、`backups/`、`.git/`、日志与 PID 文件。

任一步失败都会**从备份回滚**并保留旧版本运行；失败原因会直接显示在卡片上。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `QD_UPDATE_DISABLE` | 关 | 设为 `1` 完全禁用更新端点（只保留「检查新版本」） |
| `QD_UPDATE_ALLOW_REMOTE` | 关 | 更新端点默认**只允许本机**访问；设为 `1` 才允许远程触发（面板会话仍然是必需的） |
| `QD_UPDATE_ALLOW_CONTAINER` | 关 | 容器里默认拒绝自更新（镜像重建会覆盖），改为在宿主机 `docker compose pull` |

> 容器部署请用 `docker compose pull`：镜像里的代码由镜像决定，容器内自更新会被下次重建覆盖。

---

## 本 fork 的运行时强化（v1.8.0 起）

以下能力来自本 fork 在 v1.7.0 基线之上的重放（上游 v1.7.0 没有这些实现）；
升级/替换镜像时请对照本节的验收项。

### 进程生命周期与容量

| 能力 | 行为 | 开关 / 观测 |
|---|---|---|
| SIGTERM 优雅退出 | 收到 `SIGTERM`（`docker stop` / systemd 默认信号）先停 listener，再唤醒空闲 keep-alive 连接（`shutdown(SHUT_RD)`），把剩余优雅期留给在途请求 | `QD_SHUTDOWN_GRACE`（秒，默认 10）；日志出现 `SIGTERM received: stopping listener, draining in-flight requests` |
| 连接上限 | 活跃连接超过上限的新连接**直接 503**（不排队等线程），客户端按常规退避重试 | `QD_MAX_CONNECTIONS`（默认 256）；`/metrics` 的 `qoder_active_connections` |
| SSE 有界队列（背压） | 读线程→主线程队列容量固定：慢客户端把背压传导到上游，而不是让上游数据灌满网关内存 | `QD_SSE_QUEUE_SIZE`（默认 16）、`QD_SSE_HEARTBEAT`、`QD_SSE_IDLE_LIMIT`（默认 900） |

### 多实例互斥（跨进程）

- 调度器用 `accounts/scheduler/.leader.lock`（`fcntl.flock`）选主：**同一时刻只有一个实例**执行签到 / 额度刷新 / 写 `state.json`；抢不到锁的实例只跳过本轮，不重复领取。
- `state.json` 落盘改为**防抖批量**（2s 窗口 + 兜底定时器）；`mark-before-act` 的关键写入（启动补签闸门、开关变更、`stop()`）走 `force=True` 立即落盘。
- 查看谁在当 leader：`/scheduler/status` 返回的 `is_leader`。

### 用量流水按天轮转

- 写入：`usage/usage-YYYY-MM-DD.jsonl`（**UTC+8 计日**，与签到业务日一致）；旧的 `usage/usage.jsonl` 仅作迁移期兼容读取。
- 读取：看板/统计/`/usage/*` 一律跨文件读（旧文件 + 全部日文件），`fold_daily_usage` 折叠「当天 + 前 2 天」。
- 汇总文件 `usage-summary.json` 改为**批量重写**：`QD_USAGE_FLUSH_ROWS`（默认 50 行）/ `QD_USAGE_FLUSH_SECS`（默认 300 秒）+ 退出强刷 + 后台 `qd-usage-flusher` 线程。

### 观测与安全

- **`GET /metrics`**：Prometheus 文本格式（账号池 total/ready/cooling、requests/errors/tokens、活跃线程/连接）。与 `/health` 同级**免鉴权**，要加认证请在反向代理层挡。指标里没有 Key、没有账号 uid（`*_tokens_total` 里的 “token” 是计量单位，不是凭据）。
- **`QD_LOG_JSON=1`**：日志改为 stdout 单行 JSON（`ts/level/tag/msg/id`），供采集器解析；默认仍是人类可读格式写 stderr。
- **`/readyz` 是就绪探针**：池中零就绪账号时返回 503（供 docker/k8s 摘流）；`/ping`、`/livez`、`/healthz` 仍是 liveness，恒返回 `pong`。
- **`accounts/settings.json` 以 0600 落盘**（内含 API key 明文），并带 (mtime, size) 读缓存与写后失效。
- **`/health` 的账号明细（`uid` / `domain` / `credential_file`）只在带 Key 或面板会话时返回**；未鉴权请求只有聚合计数。

### Docker `--lan` 鉴权加固（v1.8.0 起）

上游 v1.7.0 的 `--lan` 本身没问题，问题在它的配套默认值。本 fork 逐项加固（规格见
`docs/plan/07-docker-auth.md` H1–H8，回归套件 `tests/_test_lan_auth.py`）：

| # | 加固点 | 落地 | 回滚开关 |
|---|---|---|---|
| H1 | 端口只发布到宿主回环 | `docker-compose.yml` 的 `127.0.0.1:8790:8790`（不跟随上游 `8790:8790`） | 改回 `8790:8790`（不建议） |
| H2 | Key 来源显式化，只记来源不记值 | 启动日志 `key_source=cli/env/panel/launcher` | 无（只加日志） |
| H3 | 启动日志脱敏、不打印 `?key=` | `mask_secret()` + LAN 横幅（默认 `qd-****************abc`） | `QD_LOG_SENSITIVE=1`（= 上游原文） |
| H4 | 面板默认密码硬门控 | `QD_REQUIRE_PANEL_PASSWORD=1` 时默认密码 + `0.0.0.0` 拒绝启动（退出码 2） | `QD_REQUIRE_PANEL_PASSWORD=0`（= 上游只 WARN） |
| H5 | 凭据文件 0600 + 启动自检 | `qoder_settings.save()` 的 chmod（已保留）+ 启动 WARN | 无 |
| H6 | 观测面收敛 | `/metrics` 无凭据/uid；`/health` 未鉴权不给账号明细 | 无 |
| H7 | 一键更新默认关闭 | `qoder_updater.REPO` 指向本 fork + 镜像/编排 `QD_UPDATE_DISABLE=1` + `/update/apply` 403 | 去掉该环境变量并显式改回官方 REPO |
| H8 | 启动自检四行 | `auth` / `security` / `ingress` / `identity` | 无 |

### 镜像与编排

- 镜像以 **uid 10001（非 root）** 运行，PID1 与所有写盘都不是 root。
- `docker-compose.yml` 增加 `mem_limit: 1g`、`cpus: "2"` 与 json-file 日志滚动（10MB × 3）；bind mount 的宿主机目录需先 `sudo chown -R 10001:10001 ./accounts ./usage`。
- 镜像内置 `tests/`：`docker run --rm qoder-proxy:<tag> python tests/run_all.py` 可在容器内跑全套件（`.js` 套件无 node 时自动 `[SKIP]`）。

### 一键更新默认关闭

本 fork 把 `qoder_updater.REPO` 指向 `LX2000WASD/qoder2api-hub`，并在镜像/编排里默认
`QD_UPDATE_DISABLE=1`：更新只会拉**本 fork** 的 release，且默认不执行——上游 release 的
tarball 会把本节的本地补丁全部覆盖掉。需要更新时请显式改回并用面板确认来源。

