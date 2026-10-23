# Docker `--lan` 鉴权加固规格（Wave 2）

> 背景：用户拍板 Docker 跟随上游 v1.7.0 的 `CMD ["python","qoder_proxy.py","--lan"]`，同时要求"不引入安全回归"、上线时同步 `/home/sixiao/tool/云服务/阿里云运维` 文档。
> 本文件只定规格与验收，不改码。实现排在上游合流（task-6）之后。

## 0. 结论摘要

1. **`--lan` 本身不是安全问题，上游的"配套默认值"才是**：端口发布改成全网卡、启动日志打印完整 API Key（含 `?key=` URL）、面板默认密码仅告警、settings.json 不设 0600。
2. **我们的暴露模型不变**：外网 → nginx 45421（TLS）→ 宿主机 `127.0.0.1:8790` → 容器。容器内监听 0.0.0.0 是 upstream 的 `--lan` 语义，不构成新增暴露面；**只要 compose 的端口发布保持 `127.0.0.1:8790:8790`**。
3. **必须做的 4 件**：端口绑定收紧（H1）、Key 日志脱敏（H3）、面板默认密码硬门控（H4）、settings.json 0600（H5，本地已有，重建时别丢）。
4. **建议做的 4 件**：Key 来源显式化（H2）、`/ping` 之外观测面收敛（H6）、updater 默认禁用（H7）、启动自检四行（H8）。
5. 验收 = 6 条容器冒烟 + 3 条单测 + 1 次"无 Key/有 Key/默认密码"三态矩阵。

## 1. 现状与上游差异

| 维度 | 本地现网（HEAD） | 上游 v1.7.0 | 风险 |
|---|---|---|---|
| 容器 CMD | `python qoder_proxy.py`（无 --lan） | `python qoder_proxy.py --lan` | 行为变化：强制鉴权 + 自动 Key |
| 端口发布 | `127.0.0.1:8790:8790` | `8790:8790`（全网卡） | **R1 暴露面扩大** |
| 首启无 Key | `auth_required()=False`（无鉴权） | 自动生成 `qd-…` 并落盘 | 本身是改进，但落盘权限与日志见 R2/R4 |
| Key 打印 | 不打印 | LAN 横幅打印完整 Key + `?key=` URL | **R2 凭据进日志** |
| 面板默认密码 | 仅告警 | 仅告警（0.0.0.0 时 WARN） | **R3 公网可登录** |
| settings.json | 原子写 + `chmod 0600` | 原子写，**无 chmod** | **R4 同机用户可读明文 Key** |
| 一键更新 | 不存在 | `REPO=shuishuipingan/…` | **R5 冲掉本地补丁** |

> 证据：`git show v1.7.0:qoder_proxy.py` 10845-10854（lan key）、10961-10980（LAN 横幅）、8523（面板默认密码判定）；`git show v1.7.0:qoder_settings.py` 的 `save()`；`git show v1.7.0:docker-compose.yml` ports；HEAD:qoder_settings.py 的 `save()`（含 chmod）。

## 2. 加固要求

### H1 端口绑定（P0，必做）

- `docker-compose.yml` 保持 `- "127.0.0.1:8790:8790"`，**不跟随**上游 `8790:8790`。
- 容器内 `HOST=0.0.0.0` + `--lan` 保持不变（nginx 从宿主机回环访问）。
- 验收：`ss -lntp | grep 8790` 只出现 `127.0.0.1:8790`；从另一台机器探 8790 应超时。

### H2 Key 来源显式化（P1）

优先级（代码里已存在，需文档化 + 启动日志打印来源）：

```
--api-key  >  环境变量 API_KEY  >  面板保存的 Key（api_key_override）  >  自动生成 launcher key
```

- 生产 compose 用 `env_file: ./qoder.env`（0600）注入 `API_KEY`，**不写进 compose 明文**。
- 自动生成仅作为首次启动兜底；生成后必须显式提示"已写入 accounts/settings.json（0600）"。

### H3 日志脱敏（P0，必做）

- 新增 `QD_LOG_SENSITIVE`（默认 `0`）：0 = 脱敏，1 = 上游原样（兼容个人桌面用户）。
- 脱敏规则：`qd-****************abc`（保留前缀 3 + 后 4）；**不打印 `?key=` URL**，改为 `http://<ip>:<port>/` + "登录面板后从设置页复制 Key"。
- 生成提示保留：`(newly generated & saved to accounts/settings.json)`。
- 验收：`docker logs` 全文 grep 不到完整 Key；`grep -c '?key=' ` = 0。

### H4 面板默认密码硬门控（P0，必做）

- 新增 `QD_REQUIRE_PANEL_PASSWORD`（默认 `0`）。设为 `1` 时：若 `args.host == "0.0.0.0"` 且 `panel_password_is_default()` → **拒绝启动**，打印修复命令（`--panel-password` 或面板设置）。
- 我们的 compose 显式设 `QD_REQUIRE_PANEL_PASSWORD=1`（CI/本地裸跑不受影响）。
- 理由：nginx 把面板暴露在公网域名下，默认 `admin` 等于把账号池交出去；仅 WARN 不足以构成"无回归"。

### H5 凭据文件权限（P0，必做）

- 保留本地 `save()` 的 `chmod(tmp, 0o600)`（重建时上游版本没有，必须补回）。
- 启动自检：`accounts/settings.json` 存在且 mode 不是 600 → WARN 并提示 `chmod 600`。
- 验收：容器内 `stat -c %a accounts/settings.json` = 600。

### H6 观测面收敛（P1）

- `/ping`、`/livez`、`/healthz`、`/readyz` 保持免鉴权（探针需要）。
- `/metrics` 与 `/health` 的明细：不含凭证、不含账号 uid 列表；外网只经 nginx 暴露，nginx 层对 `/metrics` 加 `allow`/`deny`（运维侧）。
- 验收：`curl /metrics | grep -i 'qd-\|token\|uid'` 无命中。

### H7 一键更新（P0，安全）

- `qoder_updater.REPO` 改 `LX2000WASD/qoder2api-hub`；镜像 `ENV QD_UPDATE_DISABLE=1`；面板按钮返回 403。
- 验收：`curl -X POST /update/...`（面板会话）→ 403 + `update is disabled`。

### H8 启动自检（P2）

启动日志四行（不含敏感值）：

```text
auth       : required=1 key_source=env | panel_default=0 | bind=0.0.0.0:8790
security   : settings.json mode=600 | updater=disabled | sensitive_log=0
ingress    : published=127.0.0.1:8790 (expected) | tls=external(nginx)
identity   : device_id=uuidv4 | legacy=0 | umid=present(age=…)
```

## 3. 验收矩阵

| 场景 | 命令 | 期望 |
|---|---|---|
| 无 Key 调 /v1 | `curl -s -o /dev/null -w '%{http_code}' -X POST :8790/v1/chat/completions -d '{}'` | 401 |
| 有 Key | 带 `Authorization: Bearer $KEY` | 非 401 |
| 默认密码 + lan | `QD_REQUIRE_PANEL_PASSWORD=1` + 默认密码启动 | 退出码非 0 + 明确报错 |
| 日志脱敏 | `docker logs qoder-proxy 2>&1 \| grep -E 'qd-[A-Za-z0-9_-]{20,}'` | 无命中 |
| 端口 | 宿主 `ss -lntp` | 仅 127.0.0.1:8790 |
| 文件权限 | 容器内 `stat -c %a /app/accounts/settings.json` | 600 |
| updater | 面板会话 POST /update | 403 |
| 单测 | 新增 `tests/_test_lan_auth.py`（Key 优先级/脱敏/门控三态） | 全绿 |

## 4. 回滚

- 端口：改回 compose 一行即可。
- 门控：`QD_REQUIRE_PANEL_PASSWORD=0`。
- 脱敏：`QD_LOG_SENSITIVE=1`。
- 整体：镜像回退到上一 tag（部署脚本保留 `qoder-proxy:previous`）。

## 5. 运维文档同步点（详见 docs/plan/06-ops-sync.md）

- `01-访问入口.md`：qoder 网关的入口不变（nginx 45421），但需注明"容器内 --lan，宿主机仅回环发布"。
- `03-运维手册.md`：新增 Key 来源与轮换步骤、默认密码门控、日志脱敏开关。
- `04-安全加固记录.md`：追加本轮（H1/H3/H4/H5/H7）。
- `05-凭据清单.md`：API Key 存放位置改为 env_file/面板，不再依赖容器日志。
- 新篇 `30-上游v1.8合流上线记录.md`：上线时填。

## 6. 未决

- 是否需要"内网 LAN 直连"场景（若需要，单独 profile 开放端口 + 强制 Key + 面板密码门控，默认关闭）。
- nginx 是否需要为 `/metrics` 单独做 IP 白名单（取决于 modelprobe 是否要采集）。
