# Q4 专题：反代出站标头与请求参数是否需要优化

> 基线：本地 main `abb38c8`（上游 v1.3.4 + 2 个本地提交）· 上游 `v1.7.0` · Go 参考 `fengyinxia/qoder2api` @ `1ccd617`
> 证据等级：**【已验证】**=本机实测/代码原文/commit 原文；**【推断】**=基于代码与协议的合理外推；**【未验证】**=需真实账号探测才能定论。
> 本文件只回答"标头与参数"，不重复 01（提示词）/02（虚拟机）/03（模型管理）。

## 0. 结论摘要（TL;DR）

1. **标头必须对齐上游 v1.7.0，逐字照抄，不要重新设计**。【已验证：`git show 7fb9281` / `513254d`】本地 `qoder_sign.py:596-623` 仍是 v1.3.4 形态：`cosy-version=1.1.64`、`user-agent=Go-http-client/2.0`、`cosy-data-policy=AGREE`、缺 `cosy-business-product/type/scene`、缺 `cosy-machineos`（全部请求）与 `cosy-machinehostname`（仅 SSE）。
2. **`Go-http-client/2.0` 是本仓最大的单点"脚本指纹"**。上游 #61 原文："Qoder 官方客户端是 Electron/Node 应用，绝不可能发出该 UA —— 等于在每个 openapi 请求上自报『我是脚本』"。【已验证：`513254d` commit body】而且我们**同时**在 `qoder_proxy.py:2269` 与 `:4455` 二次覆写它，把 `qoder_sign` 生成的头覆盖掉，等于两处都要改。
3. **更糟的是 UA 与协议自相矛盾**：本机抓包证实我们的出站是 HTTP/1.1 + `Connection: close` + **无 ALPN**（Python 标准库不支持 h2），而 `Go-http-client/2.0` 这个字符串在 Go 里只在协商到 HTTP/2 时才发出。【已验证：本地 TLS 探针 `selected_alpn_protocol() == None`、请求行 `GET / HTTP/1.1`；wire 抓包含 `Connection: close`】上游的 `undici` 至少与 HTTP/1.1 语义不冲突。
4. **设备码形态要改，但必须带兼容开关**：`derive_id` 现在输出 32 位无连字符 hex，官方 `machineId` 是带连字符的 UUIDv4 形态；`generate_request_id` 现在带"uid 派生前缀 + 微秒时间戳"，是一条出现在**每个请求**里的稳定标记。【已验证：`qoder_fingerprint.py:20-39`】上游 #61 改成 UUID 形态 + `uuid4().hex`，后续提交补了 `QD_DEVICE_ID_LEGACY=1` 回退开关【已验证：`v1.7.0:qoder_fingerprint.py:31-72`、`407013e`】。**破坏性**：存量账号 machineId/sessionId 会变，对上游等于换设备。
5. **账号接口（openapi/PAT/刷新）UA 必须按区**：CN 用 `QoderWork/1.1.x`、INTL 用 `Qoder/1.1.x`。【已验证：`qoder_accounts.py:54/76` 有 `ua` 字段，但 `:1968/:2093/:3679/:3737/:3746` 全用 `CLIENT_UA`；上游 #61 改了这四处】
6. **活动/签到头要"减"不是"加"**：本地 `desktop_headers()` 是 `dict(self.headers())` 再改 UA（`qoder_accounts.py:2004`），于是把 `Content-Type / X-Request-ID / X-Machine-ID / X-Session-ID / Origin / Referer` 全带进了 `/sash/...`；上游 #75 按官方桌面端集合**只发** `Accept / User-Agent: Qoder / Authorization / Cosy-ClientType: 10 / Cosy-Version / Cosy-Machine*`。【已验证：`7fb9281` B 段 + `v1.7.0:qoder_accounts.py:2027-2075`】
7. **参数侧「透传化」方向对，但不能裸奔**：我们现在**静默丢弃** `temperature / top_p / stop / seed / n / response_format / logit_bias / parallel_tool_calls / stream_options`（三个入口都解析了，`build_qoder_body` 一个都不往上游写）。客户端以为采样参数生效、其实没发——这是"静默替换"，比 400 更坑。Go 的做法是透传进 `parameters` 让网关当裁判。【已验证：`qoder_proxy.py:4001-4018` 只写 `max_tokens`/`reasoning_effort`；`bridge/params.go:38-46` 透传 9 个键】但上游是否接受 `temperature` 仓里**零证据**，必须先探针再默认开。
8. **必须保留的本地校验只有四类**：① `reasoning_effort` 档位归一化（上游对不支持档位**不报错、直接忽略**，客户端会以为生效——`qoder_proxy.py:2788-2791` 有实测记录）；② `max_tokens` 的类型/别名翻译（`max_completion_tokens`→`max_tokens`，`:3028-3039`）；③ 工具形态降级（custom freeform→function 单参数，`:5064-5096`）；④ 图片/VL 与体积闸（后者见 01/02 专题）。
9. **缓存：标头改动不破坏前缀缓存，真正的缓存杀手在 Anthropic 入口**。本地 `qoder_anthropic.py:122-142` 仍把 `messages` 里的 system **上提合并**到最前面；上游 #55 实测这种形态让长会话前缀命中率 **92% → 14.7%**。【已验证：#55 commit `9576c13` 原文 + `docs/risk-control-and-queueing.md` §4.4】这是本地四问里**唯一有量化损失**的一项，优先级高于标头。
10. **不要动**：COSY 签名算法与 identity 9 键（`qoder_sign.py:561-576`）、`cosy-clientip=169.254.198.161`（官方固定值，上下游均保留）、`accept-encoding: identity`（两端都保留，保证签名字节与传输字节一致）、`login-version: v2`、`x-model-source: system`、`business.version=0.1.43`（模板残留，上下游一致，改它无证据）。

**三档结论**：必改 4 项（P0，全是照抄上游）· 建议改 4 项（P1：参数透传 / `n!=1` / #55 / 按模型 max_tokens）· 不要动 6 项（P2，见 §5.3）。

## 1. 现状证据

### 1.1 出站请求分类与调用点（A 问清单）

| # | 类别 | 代码入口 | 方法/传输 | 头来源 | 备注 |
|---|---|---|---|---|---|
| 1 | 聊天 SSE（推理） | `qoder_proxy.py:4452-4460` `open_upstream()` | POST urlopen，body=qoder_encode(json) | `CosySession.headers()` + `headers["User-Agent"]=CLIENT_UA`（`:4455`） | 唯一真正"签名"的链路 |
| 2 | 模型列表 | `qoder_proxy.py:2266-2272` `read_dynamic_models()` | **GET 带 body**（`data=sign_body`） | 同上 + UA 覆写（`:2269`） | 签名覆盖 `qoder_encode("{}")` |
| 3 | OAuth 刷新（drt/jrt） | `qoder_accounts.py:2090-2096` `_post_token()` | POST JSON | 就地构造：Content-Type/Accept/`UA=CLIENT_UA` | 无 Authorization（refresh_token 在 body） |
| 4 | 设备授权轮询 | `qoder_accounts.py:3637-3641` | GET query | `Accept` + **`User-Agent: "QoderWork"`（硬编码、不分区）** | 直连不走 `http_json`（注释说明有意保留） |
| 5 | 设备授权取 userinfo | `qoder_accounts.py:3677-3684` | GET | `Accept` + UA `CLIENT_UA` + Bearer | |
| 6 | PAT 导入（exchange） | `qoder_accounts.py:3732-3740` | POST JSON | Content-Type/Accept/UA `CLIENT_UA` | body=`{"personal_token": "pt-..."}` |
| 7 | PAT 导入（userinfo） | `qoder_accounts.py:3744-3751` | GET | Accept/UA `CLIENT_UA`/Bearer | |
| 8 | 签到 status / claim | `qoder_accounts.py:2189` / `:2261` | GET / POST `{}` | `Account.headers()`（openapi 头） | 未用 desktop 头 |
| 9 | 活动列表 / 活动领取 | `qoder_accounts.py:2296` / `:2451` | GET / POST `{}` | `desktop_headers()` | issue #10 的雷区 |
| 10 | 额度 / 套餐 / Pro 资格 / Pro 领取 | `:2673` / `:2745` / `:2761` / `:2776` | GET / GET / GET / POST `{}` | `Account.headers()` | |
| 11 | 版本检查（GitHub） | `qoder_proxy.py:2891` | GET | `User-Agent: qoder-proxy-update-check` | 非 Qoder 上游，无需对齐 |

### 1.2 COSY 头矩阵（推理 + 模型列表，`qoder_sign.py:596-623`）

| 字段 | 本地取值 | 取值来源 | 可配 | v1.7.0 | 与官方形态 |
|---|---|---|---|---|---|
| `cosy-data-policy` | `AGREE` | 常量 `:601` | 否 | `agree`（`:648`） | **不一致**（大小写） |
| `content-type` | `application/json` | `:602` | 否 | 同 | 一致 |
| `cosy-business-product` | **缺失** | — | 否 | `cli`（`:650`） | **缺失** |
| `cosy-business-type` | **缺失** | — | 否 | `agent` | **缺失** |
| `cosy-scene` | **缺失** | — | 否 | `assistant` | **缺失** |
| `cosy-clienttype` | `5` | `:604` | 否 | 同 | 一致（CLI=5 / 桌面=10 / QoderWork=6） |
| `cosy-date` | unix 秒 | `bearer()` 返回 | 否 | 同 | 一致 |
| `cosy-user` | uid | `self.uid` | 否 | 同 | 一致 |
| `cosy-key` | base64(RSA(tempKey)) | `:556` | 否 | 同 | 一致 |
| `accept` | `text/event-stream` / `application/json` | 调用方传入 | 调用点决定 | 同 | 一致 |
| `cosy-clientip` | `169.254.198.161` | 常量 `:609` | 否 | 保留同值 | 一致（官方硬编码） |
| `authorization` | `Bearer COSY.{b64}.{md5}` | `bearer()` `:578-594` | 否 | 同 | 一致 |
| `accept-encoding` | `identity` | 常量 `:611` | 否 | 同（`:659`） | 一致（**勿改 gzip**：签名覆盖 body 字节） |
| `cosy-version` | `1.1.64` | 常量 `:525` | **否（无 env）** | `1.1.75`（`QD_COSY_VERSION` 可覆盖） | **落后一个版本** |
| `cosy-machineid` | md5 前 32 hex | `derive_id(uid,"machine")` | 否 | UUIDv4 形态 | **形态不一致** |
| `cosy-machinetoken` | base64url 43 字符 | `derive_machine_token` | 否 | 同派生 | 一致 |
| `cosy-machinetype` | 18 位去横线 | `derive_machine_type` | 否 | 同 | 一致 |
| `cosy-machineos` | **缺失** | — | 否 | 全部请求带（`QD_COSY_OS_HEADERS=0` 可关） | **缺失** |
| `cosy-machinehostname` | **缺失** | — | 否 | 仅 `sse=True` 带，按账号派生 `DESKTOP-XXXXXXX` | **缺失** |
| `login-version` | `v2` | `:615` | 否 | 同 | 一致 |
| `user-agent` | `Go-http-client/2.0` | `:616` | 否 | `undici`（`:666`/`:528`） | **不一致（风险信号）** |
| `cache-control` | `no-cache`（仅 sse） | `:618-619` | 否 | 同 | 一致 |
| `x-model-key` / `x-model-source` | `model_key` / `system` | `:620-622` | 模型决定 | 同 | 一致 |

**Python 标准库自动追加的头**（本机实测，本地 socket 服务端抓包）：

```text
GET /algo/api/v2/model/list?Encode=1 HTTP/1.1
Content-Length: 4
Host: 127.0.0.1:49661
Cosy-Data-Policy: AGREE
Content-Type: application/json
Cosy-Machinetype: 73bcf1fc641e54a503
Cosy-Clienttype: 5
Cosy-Date: 1792753034
Cosy-User: u-test
Accept: application/json
Cosy-Clientip: 169.254.198.161
Authorization: Bearer COSY.eyJjb3N5...
Accept-Encoding: identity
Cosy-Version: 1.1.64
Cosy-Machineid: ca0bfb324ea94332ad402d6311455a3b
Cosy-Machinetoken: 8H5OX8u_I9jcPcyUWJLzxhbhqf7cS3nYofUl0Um4E3p
Login-Version: v2
User-Agent: Go-http-client/2.0
Connection: close
```

- `Connection: close` 由 `http.client` 自动加（`urllib` 不复用连接）→ 每次请求一次完整 TLS 握手；`Host`/`Content-Length` 自动加；**无 `TE`、无 `Upgrade`、无 `Cookie`**。【已验证】
- ALPN 探针（本地 TLS 服务端 `set_alpn_protocols(["h2","http/1.1"])`）：Python 客户端 `selected_alpn_protocol() == None`、请求行 `GET /x HTTP/1.1`。【已验证】Go 的 `net/http` 默认 Transport 会协商 h2 并因此发出 `Go-http-client/2.0`【推断，Go 文档级常识】——即"UA 声称 h2、线上是 h1.1 且无 ALPN"。

### 1.3 账号侧头矩阵（openapi vs 桌面活动）

| 头 | `Account.headers()`（`:1963-1975`） | `desktop_headers()`（`:1977-2027`） | v1.7.0 `desktop_headers` | 判定 |
|---|---|---|---|---|
| `Content-Type` | `application/json` | **继承（有）** | **没有**（claim 由调用方补） | 本地多发，风险信号 |
| `Accept` | `application/json, text/plain, */*` | 继承（同） | `application/json` | 本地多发 `*/*` |
| `User-Agent` | `CLIENT_UA` → `Go-http-client/2.0` | `Qoder`（`:2005`） | `Qoder` | openapi 头错 |
| `Authorization` | Bearer | 继承 | Bearer | 一致 |
| `X-Request-ID` | `generate_request_id(uid)` = **uid 前缀 + 微秒** | **继承（有）** | **没有** | 稳定可聚类标记 |
| `X-Machine-ID` | `derive_id(uid,"machine")`（32 hex） | **继承（有）** | **没有** | 同上 |
| `X-Session-ID` | `derive_id(uid,"session")` | **继承（有）** | **没有** | 同上 |
| `Origin` / `Referer` | `https://qoder.com(.cn)` | **继承（有）** | **没有** | 桌面端不会发 Origin |
| `cosy-clienttype` | — | `10` | `10` | 一致 |
| `cosy-version` | — | `0.4.3`（`DESKTOP_CLIENT_VERSION :248`，`QD_DESKTOP_VERSION` 可覆盖） | `0.4.8` | 落后 5 个小版本 |
| `cosy-machineos` | — | 常量 `x86_64_win32`（`:249`） | `COSY_MACHINE_OS`（按平台 / auto） | 全部署同值 = 可聚类 |
| `cosy-machinehostname` | — | 常量 `DESKTOP-QODER`（`:250`） | `_header_safe_hostname(socket.gethostname())`（`:160`） | 全部署同值 = 可聚类 |
| `cosy-machine*` 六头 | — | 仅原生桥有 `machineToken` 时发（issue #10） | 同 + `minimal` 兜底（只发 os+hostname） | 本地无 minimal 分支 |

关键实现证据（继承而非重建，`qoder_accounts.py:2004`）：

```python
h = dict(self.headers())   # ← Content-Type/X-Request-ID/X-Machine-ID/X-Session-ID/Origin/Referer 全被带进 /sash/...
h["User-Agent"] = "Qoder"
h["cosy-clienttype"] = DESKTOP_CLIENT_TYPE
h["cosy-version"] = desktop_version()
```

### 1.4 请求体字段矩阵（`baseprompt.json` + `build_qoder_body`）

| 字段 | 取值 | 来源 | 可配 | Go 参考 | 判定 |
|---|---|---|---|---|---|
| `request_id` / `chat_record_id` / `request_set_id` / `session_id` | 每请求 uuid4 | `qoder_proxy.py:3951-3955` | 否 | 同（`chat.go:165-169`） | 一致 |
| `stream` | 恒 `true` | `:3956` | 否 | 同（`:170`） | 一致（上游只支持 SSE） |
| `chat_task` | `FREE_INPUT` | 模板 | 否 | 同（`chat.go:102`） | 一致 |
| `source` | `1` | 模板 | 否 | 同（`chat.go:119`） | 一致 |
| `version` | `"3"` | 模板 | 否 | 同（`chat.go:120`） | 一致 |
| `session_type` | `qodercli` | 模板 | 否 | 同（`:122`） | 一致 |
| `agent_id` | `agent_common` | `:3957` / 模板 | 否 | 同（`:123`） | 一致 |
| `task_id` | `common` | 模板 | 否 | 同（`:124`） | 一致 |
| `aliyun_user_type` | 账号 `user_type`，兜底 `personal_professional_trial` | `:3958-3959`、换号后重写 `:4436-4437` | 账号属性 | `identity.UserType`（`chat.go:171`） | 一致（**换号后必须重写**，本地已做） |
| `model_config.key/display_name/is_vl/is_reasoning/source/max_input_tokens` | 目录元数据 | `:3972-3983` | 目录 | 精简版（`chat.go:48-53`） | 本地字段更全（无害） |
| `parameters.max_tokens` | 客户端给才写；否则模板 `32768` | `:4003-4008` + 模板 | 客户端 | 目录 `max_output_tokens` 或 `32000`（`params.go:92-118`） | 默认值口径不同 |
| `parameters.reasoning_effort` | 归一化后写 | `:4010-4017` | 客户端→归一化 | **原样透传**（`params.go:48-66`） | 语义不同（§2.2） |
| `parameters.temperature/top_p/stop/seed/n/...` | **不写（静默丢弃）** | 无代码 | 客户端 | 透传（`params.go:38-46,141-149`） | **差异** |
| `tools` | 客户端给则透传，否则 `[]` | `:4020-4026` | 客户端 | 无工具则省略字段（`chat.go:206` + omitempty） | 基本一致 |
| `business.id/begin_at/name` | uuid4 / 毫秒 / prompt 前 30 字 | `:4028-4036` | 否 | 同（`chat.go:198-204`） | 一致 |
| `business.product/type/stage/version` | `cli/agent/start/**0.1.43**` | 模板 | 否 | **不带这四个键** | 模板残留，上下游都没改 |
| `chat_context.text/extra.originalContent/modelConfig/imageUrls` | 最新 user 文本 + 模型副本 + 图片 | `:3986-3999` | 否 | 同（`chat.go:202-203`） | 一致 |
| `image_urls` | 图片 URL 列表 | `:3996-3998` | 否 | 图片走 messages 的 `image_url` part | 表示不同（都能用） |

`baseprompt.json` 实测键序与值（本机 `json.load`）：

```text
keys: request_id, request_set_id, chat_record_id, stream, chat_task, chat_context,
      image_urls, is_reply, is_retry, session_id, code_language, source, version,
      chat_prompt, parameters, aliyun_user_type, session_type, agent_id, task_id,
      model_config, messages, tools, business
source=1  version="3"  session_type="qodercli"  task_id="common"  agent_id="agent_common"
parameters={"max_tokens": 32768}
business={"product":"cli","version":"0.1.43","type":"agent","id":...,"name":"hi","begin_at":...,"stage":"start"}
```

### 1.5 参数处理现状（三入口 → 上游）

| 客户端字段 | Chat 入口 | Responses 入口 | Anthropic 入口 | 是否写进上游 `parameters` |
|---|---|---|---|---|
| `max_tokens` | 保留 | `max_output_tokens`→`max_tokens`（`qoder_proxy.py:5279`） | 必填校验（`qoder_anthropic.py:337-349`） | ✅ `:4003-4008` |
| `max_completion_tokens` | 别名翻译（`:3028-3039`） | — | — | ✅（间接） |
| `reasoning_effort` / `reasoning.effort` / `thinking.level` | 提取（`:2831-2846`）+ 归一化（`:2785-2828`） | `reasoning.effort`（`:5285-5291`） | `thinking.budget_tokens` 断点映射（`qoder_anthropic.py:171-200`） | ✅（归一化后） |
| `temperature` / `top_p` / `seed` | 存入内部 chat（`qoder_anthropic.py:315`、`:5275`） | 同 | 同 | ❌ **丢弃** |
| `stop` / `stop_sequences` | — | — | 映射为 `stop`（`:318-319`） | ❌ **丢弃** |
| `n` | 不读 | 不读 | 不读 | ❌ **丢弃**（恒返回 1 个 choice，不报错） |
| `parallel_tool_calls` | 不读 | 透传到内部 chat（`:5292-5293`） | — | ❌ **丢弃**（Go 发在顶层，`chat.go:93`） |
| `stream_options.include_usage` | 不读 | 不读 | 不读 | 入站常量（终局 usage 帧总是发） |
| `tools`（含 custom freeform） | 透传（`:4022-4024`） | custom→function 降级（`:5064-5096`） | `_tools_to_chat`（`qoder_anthropic.py:146-167`） | ✅ |
| `tool_choice` | 不读 | 透传到内部 chat（`:5297-5298`） | `_tool_choice_to_chat`（`:104-120`） | ❌ **丢弃** |
| `response_format` / `logit_bias` / 惩罚项 | 不读 | 不读 | 不读 | ❌ **丢弃** |

## 2. 机制拆解

### 2.1 上游到底怎么判"你是不是官方客户端"

证据链（全部来自上游 commit 原文或本仓实测注释，不是猜测）：

1. **UA 是被动暴露的**。官方 CLI 的签名 WASM 头表里**没有** User-Agent，请求经打包的 undici fetch 发出，由 undici 补默认值 `undici`。【已验证：`v1.7.0:qoder_sign.py:526-528` 注释；`7fb9281` A 段】所以推理链路的"正确 UA"不是某个 Qoder 版本号，而是 `undici`。
2. **缺头 ≠ 报错，而是"静默降级"**。活动平台缺 `UA/cosy-clienttype/cosy-version` → 服务端 200 但返回**空活动列表**。【已验证：`qoder_accounts.py:1988-1991` 注释（踩坑记录）】
3. **"半套身份"比"没有身份"更糟**。派生 `cosy-machine*` 六头全发 → 服务端把 CLAIMABLE 活动**整条过滤**；逐头隔离实测：任一个单独出现仍可见，六头全发才被过滤。【已验证：`qoder_accounts.py:1991-1996` + issue #10】
4. **但版本号不是硬门槛**。Go 参考实现用 `cosy-version: 0.1.43`（`auth/auth.go:664,1083`）+ `Go-http-client/2.0`（`:670,1087`）+ `cosy-data-policy: AGREE`（`:1073`），按其代码语境仍能跑通推理。【推断：Go 仓自述可用；未在本机复验】→ 说明上游对"版本号/UA 字符串"是**弱信号**，真正用于设备定向的是机器身份（UMID）与行为。
5. **风险分级**：UA/版本号/大小写属于"聚类特征"（提高被判定为脚本的概率，不直接 403）；机器身份（`cosy-machine*`/UMID）属于"功能开关"（决定活动可见性）；签名与 body 属于"硬校验"（错就 403）。

**签名只覆盖 body 与 path，不覆盖头**（`qoder_sign.py:592`）：

```python
raw = "\n".join([payload_b64, self.cosy_key, date, body, path])
sig = hashlib.md5(raw.encode("utf-8")).hexdigest()
```

推论（重要）：**改头不会破坏签名**，改 body 字节（含 `accept-encoding: gzip` 导致的重编码、JSON 序列化顺序变化）会。本仓模型列表用 GET 带 body 且签名覆盖该 body（`:2260-2261`），Go 用 GET 无 body 且签名空串（`auth/auth.go:1117-1125`）——两者都自洽，恰好证明"服务端校验 body 与签名一致"。【推断，强】

### 2.2 参数语义：本地钳制 vs "网关是裁判"

**本地钳制清单与理由（逐条核过代码）**：

| 钳制 | 代码 | 为什么必须留 |
|---|---|---|
| `reasoning_effort` 档位归一化 | `qoder_proxy.py:2785-2828` | 上游对不支持档位**不报错、回落到默认档**（注释记录实测：dfmodel 只支持 low/high/max，传 medium/xhigh 与默认档输出一致）。不归一化 = 客户端以为设置生效但实际没有 |
| 关闭类取值统一映射 `none` | `:2806-2807` | 官方通用关闭值；`off/disabled/false/0` 上游不认 |
| 无档位表模型（cn `qmodel`）除 `none` 外不下发 | `:2812-2814` | 避免发它不认识的档位 |
| 无 `thinking_config`（路由器 `auto`）原样透传 | `:2808-2811` | 不猜测 |
| `max_completion_tokens`→`max_tokens` | `:3028-3039` | 上游只认 `max_tokens` |
| `max_tokens` 只做 `int()` + 真值判断 | `:4003-4008` | **没有**范围钳制（不做 1..N 裁剪），非法值直接不写 |
| 工具 custom freeform→单 `input` 参数 function | `:5064-5096` | 上游无 custom 概念，不降级工具会被静默忽略 |
| 工具历史结构化 vs 文本化二选一 | `:3919-3927` + 白名单 `:3735` | 上游对 `role:"tool"` 的接受度按模型分族（原生不支持时 400/provider_error） |
| `tools=[]` 兜底 | `:4020-4026` | 防止把模板自带的 44KB 桌面 agent 工具泄漏给普通客户端 |
| 历史工具结果截短 | `:3683-3712` | 体积/缓存（#48） |
| 图片抽到 `imageUrls` + `is_vl` | `:3996-3998`、`:3977` | 上游 chat 端点的多模态形态 |

**Go 的口径（"完全透传，网关是裁判"）**，原文见 `bridge/params.go:1-14`：

```go
// Policy (verbatim): a client-provided parameter value is forwarded
// unchanged — no clamping, no catalog validation, no tier normalization, no
// silent fallback substitution. Values the gateway rejects come back to the
// client as upstream errors ... The only synthesized values are (a) the catalog
// default for max_tokens when the client sent none, and (b) the "none" encoding.
```

Go 只在三种情况本地 400：字段 JSON 类型错、`n != 1`（`params.go:72-90`）、结构性无法满足的能力（如 VL 模型校验，`chat.go:217-230`）。**Go 自己也承认** `temperature` 等键"是否被上游接受未验证"——`params.go:33-37` 注释："every other key is the upstream's to accept or reject — we neither validate nor drop values"。

**评估：透传化能否提升兼容性而不 400？**

- 收益明确：`temperature/top_p/stop/seed/response_format` 是 OpenAI 客户端常规配置（OpenWebUI、LobeChat、各家 SDK 都带）。我们现在静默丢弃，客户端"调了没用"却永不报错——最难排查的一类兼容性问题。【已验证：全仓 `grep temperature` 只有两处入站映射（`qoder_anthropic.py:315`、`qoder_proxy.py:5275`），无出站写入】
- 风险同样明确：这些键在 Qoder 的 `parameters` 契约里**没有文档**，上游可能 400（`invalid_parameter_error` 本仓已在错误分类里预留，`:4137`）。
- 结论：**开关灰度，不默认开**。默认保持"丢弃"，`QD_PARAM_PASSTHROUGH=1` 时透传；灰度 24h 观察 400 与 `invalid_parameter` 计数后再定。**不要**照抄 Go 的"默认透传"——Go 是单账号本地工具，400 只是回给调用方；我们是多账号池，参数型 400 会触发换号/冷却（`open_upstream` 的 4xx 分支，`:4471-4490`），代价被放大。【推断】

**`reasoning_effort` 专项**：官方每模型合法档位不同（目录 `thinking_config.enabled.efforts`，`supported_efforts()` `:2762-2770`）。本地"就近映射"保证"发出去的值一定合法"，但会**悄悄改变语义**（客户端要 `xhigh`，我们发 `high`）。建议：保留合法性（不发非法档位），但把"就近映射"改成**默认只记日志、不下发**（`QD_EFFORT_CLAMP=0` 时丢弃未知档位而非替换）。这是语义诚实性问题，不是风控问题。

**`max_tokens` 默认值专项**：

| 口径 | 值 | 来源 |
|---|---|---|
| 本地 | **32768**（恒定，不随模型变） | `baseprompt.json` 的 `parameters.max_tokens`；客户端不给就原样发 |
| Go | 目录 `max_output_tokens`，缺失/非法兜底 **32000** | `models/models.go:73-77,175-180`；注释称"mirrors the client's LS() coercion, which returns 32000" |
| 上游 v1.7.0 | 32768（与本地同源模板，未改） | `git show v1.7.0:baseprompt.json` |

我们的静态目录快照**没有** `max_output_tokens`（本机核过 `qoder_catalog_cn.json`/`_intl.json` 的 key 并集：只有 `max_input_tokens`），所以"按模型取默认值"只能等 live `/model/list`（我们已原样透传全字段，`qoder_proxy.py:2288-2294`）。结论：**暂不改**；合流后把 live 目录的 `max_output_tokens` 接进 `catalog_meta` 再按模型取。32768 vs 32000 对用户几乎无感，但对"看起来像官方客户端"有一点意义（官方 CLI 会按模型给不同上限）。

### 2.3 缓存与亲和：改头会不会打穿前缀缓存

**结论：不会**（标头不参与 prompt 缓存），真正的风险在 system 前缀与工具形态。

1. 签名覆盖 body/path，缓存命中由**服务端看到的 prompt** 决定；请求 id / session_id / business.id 都不在 message 里。【推断，强】
2. 反证：若缓存按**整个 body 字节**做 key，本仓每请求随机的 `request_id/session_id/business.id` 会让命中率恒为 0；而 #55 报告者实测能到 **92%**，说明缓存 key 落在 messages 前缀上。【已验证：#55 commit 原文】
3. 真正影响前缀的本地因素（按影响排序）：
   - **Anthropic 入口的 system 上提合并**：`qoder_anthropic.py:122-142` 把 `messages` 内每轮都变的 system（时间、工具清单、context 用量）合并进最前面的 system → 前缀从第 0 字节就不同。上游 #55 实测 **92% → 14.7%**，修复方式是"就地降级为 user，位置不变"（`msg_system_mode()`，默认 `inline`）。**本地是修复前的形态**（`messages_to_chat` `:251` 直接 `continue` 丢掉 messages 内 system，内容已被上提）。【已验证】
   - 工具历史形态（结构化 vs LEAK_MARKER 文本）：形态一变，`messages` 字节全变（`:3919-3927`）。白名单本地是 `qwen/glm/gm4/gm5`（`:3735`），上游 #49 后续并入 `deepseek`。合流即得。
   - 工具结果截短点：`_shrink_tool_result`（`:3683`）一旦触发，后续每轮字节稳定；未触发↔触发的切换会打穿一次前缀（#48 已考虑）。
4. 亲和键（`derive_affinity_key` `:1954-1978`）= 前两条消息哈希 → 同一对话固定同账号；它**只读 messages**，所以 #61 把 request id 改成随机**不影响亲和**。【已验证：`open_upstream` 用 `body_obj["messages"]` 派生（`:4400-4404`）】
5. 观测口已有：`cached_tokens`（`:525-526`、命中率 `:618`）、`prompt_fingerprint` 的 `msgs_sha/system_sha/prefix_sha`（`:1981-1999`）。上游 #55 增加了 `sys_src/n_msg_system` 两个维度——本地没有，建议照抄（约 10 行）。

## 3. 上游 v1.7.0 与 Go 参考实现对比

### 3.1 三方差异表

| 维度 | 本地 main（v1.3.4 形态） | 上游 v1.7.0 | Go 参考（1ccd617） | 结论 |
|---|---|---|---|---|
| `cosy-version` | `1.1.64` 硬编码 | `1.1.75`，`QD_COSY_VERSION` 可覆盖（`qoder_sign.py:525`） | `0.1.43` 硬编码（`auth.go:664,1083`） | **风险信号**；照抄上游 |
| 推理 UA | `Go-http-client/2.0` + proxy 二次覆写 | `undici`（`qoder_sign.py:528,666`），proxy 不再覆写 | `Go-http-client/2.0` | **风险信号**；照抄上游 |
| `cosy-data-policy` | `AGREE` | `agree`（`:648`） | `AGREE` | 无害（大小写），顺手改 |
| `cosy-business-product/type/scene` | 缺 | `cli/agent/assistant`（`:650-652`） | 缺 | **风险信号**（官方 CLI 必有）；照抄 |
| `cosy-machineos` | 缺（推理链路） | 所有请求带，`QD_COSY_OS_HEADERS=0` 可关（`:669`） | 缺 | **风险信号**；照抄 |
| `cosy-machinehostname` | 缺 | 仅 SSE 带，按账号派生 `DESKTOP-XXXXXXX`（`:537-543,671`） | 缺 | **风险信号**（issue #64 官方实测）；照抄 |
| `cosy-clientip` | `169.254.198.161` | 同 | 缺 | 无害；保留 |
| `requestId`（COSY payload） | `uuid4()`（`qoder_sign.py:587`） | 同 | 同（`buildBearer`） | 一致 |
| `cosy-machineid` 形态 | 32 位无连字符 hex | UUIDv4 形态 + `QD_DEVICE_ID_LEGACY`（`fingerprint:43-62`） | 未查到 | **风险信号**；改 + 留开关 |
| `X-Request-ID` | uid 前缀 + 微秒 | `uuid4().hex`（`fingerprint:64-72`） | 无该头 | **风险信号**；照抄 |
| openapi UA | `Go-http-client/2.0`（4 处） | 按区 `cfg["ua"]`（`accounts:2018,2141,3979,4026,4035`） | `qoder/1.1.32` 仅用于 openapi（`auth.go:114,986`） | **风险信号**；照抄 |
| 活动头集合 | `dict(headers())` + 3 项 | 最小集（无 Content-Type/X-*/Origin/Referer）（`accounts:2027-2075`） | 不涉及 | **风险信号**；照抄 |
| 桌面 `cosy-version` | `0.4.3` | `0.4.8`（`accounts:120`） | 不涉及 | 风险信号（低）；改 |
| `MACHINE_HOSTNAME` | 常量 `DESKTOP-QODER` | 本机 hostname 规整（`accounts:160`） | 不涉及 | 风险信号；改 |
| `MACHINE_OS` | 常量 `x86_64_win32` | 按平台解析 / `auto`（`accounts:121` + `sign.py:537-570`） | 不涉及 | 风险信号；改 |
| 设备轮询 UA | `"QoderWork"` 硬编码 | 同（`accounts:3939`，未改） | 不涉及 | 两边一致，暂不动 |
| intl 推理主机顺序 | api1 主、api2/api3 备（`accounts:68-71`） | **api3 主**、api2/api1 备（`QD_INTL_GATEWAY`） | 未查到 | 与标头同批修（省一次回归） |
| HTTP 协议 | HTTP/1.1 + `Connection: close` + 无 ALPN | 同（仍 urllib） | HTTP/2（Go 默认 Transport） | **风险信号**（UA 自相矛盾）；见 P2 |
| `max_tokens` 默认 | 32768 恒定（模板） | 同 | 目录值 / 32000 | 无害；暂不动 |
| `reasoning_effort` | 归一化到合法档位 | 同（`:5235-5251`） | 原样透传 | 语义差异；建议"不替换、只记录" |
| `thinking` 关闭 | 映射 `none` | 同 | `none` + `max_thinking_tokens=0`（`params.go:120-128`） | Go 更贴官方；**未验证**上游是否要 `max_thinking_tokens` |
| 采样参数 | 丢弃 | 丢弃 | 透传（`params.go:38-46`） | 兼容性差异；开关灰度 |
| `n != 1` | 静默 1 个 choice | 同 | 400（`params.go:72-90`） | 建议 400（可选） |
| `parallel_tool_calls` | 丢弃 | 丢弃 | 顶层透传（`chat.go:93,295-298`） | 无害；可透传 |
| `stream_options.include_usage` | 不读（总发 usage） | 不读 | 读并控制终局 usage 帧（`params.go:195-202`） | 兼容性小差异 |
| `business.product/type/stage/version` | 发（version=0.1.43） | 同（模板未变） | 不发这四个键 | 无害；不动 |
| 认证方案 | COSY Bearer（`cosy-key` + `cosy-date`） | 同 | 两套：COSY Bearer + `appcode/date/signature`（`auth.go:179-205,656-672`） | Go 的 `signature` 方案用于哪类端点**未验证**，不跟进 |

### 3.2 哪些是"风险信号"、哪些"无害"

| 分类 | 项 | 判据 |
|---|---|---|
| **风险信号（必改）** | 推理 UA=`Go-http-client/2.0`；`cosy-version` 落后；缺 `cosy-business-*`/`cosy-machineos`/`cosy-machinehostname`；`X-Request-ID` 带 uid 前缀；`machineid` 非 UUID 形态；openapi UA=Go 默认值；活动头带 `Origin/Referer/X-*` | 都是"官方客户端不可能发出"的组合特征；上游 #61/#75 原文判据 + issue #10/#64 实测 |
| **风险信号（低，顺手改）** | `AGREE`→`agree`；桌面 `cosy-version` 0.4.3→0.4.8；`MACHINE_HOSTNAME` 常量；`MACHINE_OS` 常量 | 与官方同形，但无实测证明被用于判定 |
| **无害（不要动）** | `cosy-clientip` 固定值；`accept-encoding: identity`；`login-version: v2`；`x-model-source: system`；COSY 签名算法/identity 9 键；`business.version=0.1.43`；`source=1`/`version="3"`/`session_type=qodercli` | 上下游一致或属硬校验，改动无收益且可能 403 |
| **未验证** | 采样参数透传是否 400；`max_thinking_tokens` 是否必要；UA/ALPN 矛盾是否被检测；Go 的 `signature/appcode` 端点覆盖范围 | 仓内零证据，需探针 |

## 4. 方案选项

### 选项 A：合流 v1.7.0 后"照抄标头"（推荐）

- 做法：P0-A 合流 → 标头/设备码/UA 全部以 v1.7.0 为准，本地不保留任何自研变体；参数侧另开开关灰度。
- 成本：合流后 0.5 人日核对 + 0.5 人日测试。
- 风险：machineId 形态变更 = 存量账号"换设备"（用 `QD_DEVICE_ID_LEGACY=1` 平滑）；活动平台行为可能变化（机器头集合改了）。
- 收益：一次性消除 8 类聚类特征；后续上游再改头只需跟随。

### 选项 B：不合并，在 main 上手工打补丁对齐

- 做法：把 `7fb9281`/`513254d` 的 qoder_sign/fingerprint/accounts/proxy 改动重写一遍。
- 成本：1.5～2 人日（要自己解 `qoder_proxy` 两处覆写、`accounts` 四处 UA、`desktop_headers` 重写）。
- 风险：与后续上游改动继续分叉；`desktop_headers(minimal)` 等新分支要自己实现。
- 收益：不承担合流风险（如果 05 专题判定合流成本过高）。
- 判断：**只在合流被否时选**。

### 选项 C：参数完全透传（Go 口径）

- 做法：`temperature/top_p/stop/seed/n/response_format/logit_bias/惩罚项/tool_choice` 一律透传进 `parameters`（`parallel_tool_calls` 按 Go 放顶层），`n != 1` 直接 400。
- 成本：0.5 人日代码 + 1 人日灰度观测。
- 风险：上游 400 会进入"换号 + 冷却"路径，多账号池下影响面比 Go 大。
- 收益：OpenAI 生态客户端兼容性显著提升（采样参数真的生效）。
- 判断：**降级为"先探针、再开关灰度、不默认开"**。

### 选项 D：只删 UA 覆写，其他不动（最小改动）

- 成本：0.2 人日。收益：只消除最刺眼的一处。
- 风险：**这个选项其实是空的**——`headers()` 默认值就是 `Go-http-client/2.0`（`:616`），删覆写也还是它；要真改必须动 `qoder_sign`。
- 判断：**放弃**（记录在此以免被误选）。

### 推荐

**A + C 的灰度版**：先合流（A），标头照抄；参数侧按 P1 开关灰度（C 的保守实现）。同时移植 #55 的 system 内联修复（缓存收益最大、风险最低）。

## 5. 落地改动清单

> 均为**草案**，本次不落码。行号对应当前 main `abb38c8`。每项都给开关与回滚。

### 5.1 必改（P0）

**P0-1 `qoder_sign.py`：头集合对齐 v1.7.0（`:519-560`、`:596-623`）**

```diff
-COSY_VERSION = "1.1.64"
+COSY_VERSION = (os.environ.get("QD_COSY_VERSION") or "1.1.75").strip() or "1.1.75"
+# 官方 CLI 不设 UA，由打包的 undici fetch 补默认值
+COSY_USER_AGENT = "undici"
+
+def _cosy_os_headers():
+    return str(os.environ.get("QD_COSY_OS_HEADERS") or "1").strip().lower() \
+        not in ("0", "off", "false", "no", "disable", "disabled")
+COSY_MACHINE_OS = _resolve_machine_os()   # win32 -> x86_64_win32；其余 x86_64_linux；auto 可识别
...
     h = {
-        "cosy-data-policy": "AGREE",
+        "cosy-data-policy": "agree",
         "content-type": "application/json",
-        "cosy-machinetype": self.machine_type,
+        "cosy-business-product": "cli",
+        "cosy-business-type": "agent",
+        "cosy-scene": "assistant",
         "cosy-clienttype": "5",
@@
-        "user-agent": "Go-http-client/2.0",
+        "user-agent": COSY_USER_AGENT,
     }
+    if _cosy_os_headers():
+        h["cosy-machineos"] = COSY_MACHINE_OS
+        if sse:
+            h["cosy-machinehostname"] = self.machine_hostname
     if sse:
         h["cache-control"] = "no-cache"
```

- 开关：`QD_COSY_VERSION`（版本）、`QD_COSY_OS_HEADERS=0`（关 machineos/hostname）。
- 回滚：`QD_COSY_VERSION=1.1.64 QD_COSY_OS_HEADERS=0` + 恢复 UA 常量（建议顺手加 `QD_COSY_USER_AGENT` 一级开关，便于线上切换）。
- 注意：`CosySession.__init__` 需加 `self.machine_hostname = _machine_hostname(self.uid)`。

**P0-2 `qoder_proxy.py`：删除两处 UA 覆写（`:54`、`:2269`、`:4455`）**

```diff
-from qoder_accounts import (get_realm_config, gateway_candidates, CLIENT_UA,
+from qoder_accounts import (get_realm_config, gateway_candidates,
@@ read_dynamic_models
             headers = sess.headers(sign_body, raw_url, model_key="", sse=False,
                                    accept="application/json")
-            headers["User-Agent"] = CLIENT_UA
@@ open_upstream
                 headers = sess.headers(encoded, raw_url, model_key=model_key, sse=True)
-                headers["User-Agent"] = CLIENT_UA
```

- 证据：这正是上游 `7fb9281` 的改动（`git show 7fb9281 -- qoder_proxy.py`）。
- 回滚：改回三行即可；建议加源码守卫测试（见 §6.1）。

**P0-3 `qoder_fingerprint.py`：设备码 UUID 形态 + 请求 id 随机化（`:20-39`）**

```diff
+def _device_id_legacy():
+    raw = str(os.environ.get("QD_DEVICE_ID_LEGACY") or "").strip().lower()
+    return raw in ("1", "on", "true", "yes", "enable", "enabled")
+
 def derive_id(uid: str, salt: str) -> str:
-    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:36]
+    h = hashlib.md5(seed.encode("utf-8")).hexdigest()
+    if _device_id_legacy():
+        return h
+    h = h[:12] + "4" + h[13:16] + "a" + h[17:]
+    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
 
 def generate_request_id(uid: str) -> str:
-    prefix = derive_id(uid, "req"); suffix = str(time.time_ns() % 1000000).zfill(6)
-    return f"{prefix}-{suffix}"
+    return uuid.uuid4().hex
```

- 开关：`QD_DEVICE_ID_LEGACY=1`（存量号池保旧值）。
- 回滚：设开关即回滚（**注意**：一旦用新形态跑过，回滚等于再换一次设备；灰度前先备份 `accounts/`）。
- 联动：`derive_machine_type` 仍 `replace("-","")[:18]`，UUID 形态下去横线 32 字符截 18，**长度不变**。【已验证】

**P0-4 `qoder_accounts.py`：UA 按区 + 活动头最小集 + 机器常量（`:88`、`:246-250`、`:1963-2027`、`:2090-2096`、`:3677-3684`、`:3732-3751`）**

```diff
     def headers(self, purpose="openapi"):
-            "User-Agent": CLIENT_UA,
+            "User-Agent": cfg.get("ua") or CLIENT_UA,
     def _post_token(self, url, payload, kind):
-            "User-Agent": CLIENT_UA,
+            "User-Agent": get_realm_config(self.realm).get("ua") or CLIENT_UA,
     def import_pat(...):                     # :3679 / :3737 / :3746
-                "User-Agent": CLIENT_UA,
+                "User-Agent": cfg.get("ua") or CLIENT_UA,
     def desktop_headers(self):
-        h = dict(self.headers())
-        h["User-Agent"] = "Qoder"
+        # 官方桌面端 createRequestHeaders + createAuthorizedHeaders：不多不少
+        h = {"Accept": "application/json", "User-Agent": "Qoder",
+             "Authorization": "Bearer " + self.access_token,
+             "cosy-clienttype": DESKTOP_CLIENT_TYPE,
+             "cosy-version": desktop_version()}
         ...
-DESKTOP_CLIENT_VERSION = "0.4.3"
-MACHINE_OS = "x86_64_win32"
-MACHINE_HOSTNAME = "DESKTOP-QODER"
+DESKTOP_CLIENT_VERSION = "0.4.8"
+MACHINE_OS = qoder_sign.COSY_MACHINE_OS
+MACHINE_HOSTNAME = _header_safe_hostname(socket.gethostname())
```

- 附带：`campaign_claim`（`:2451`）现在依赖继承来的 `Content-Type`，改最小集后**必须显式补** `headers["Content-Type"]="application/json"`（上游 `v1.7.0:accounts:2556` 即如此）；`pro_claim`（`:2776`）走 `headers()`，不受影响。
- 开关：`QD_DESKTOP_VERSION`、新增 `QD_DESKTOP_HEADERS=full|minimal`（默认 `minimal`；`full` 回旧行为，便于对比活动可见性）。
- 回滚：`QD_DESKTOP_HEADERS=full` + 恢复 `MACHINE_*` 常量。
- 待定：`realms["cn"/"intl"]["ua"]` 版本号同步到 `1.1.75`（随 P0-1）；容器里 `gethostname()` 是随机 hex，比 `DESKTOP-QODER` 更"像真的"，但**同镜像多容器仍可能撞名**。注意两套 hostname 不要混用：推理链路是**账号级** `DESKTOP-XXXXXXX`，桌面活动链路是**机器级** hostname。

### 5.2 建议改（P1）

**P1-5 采样参数透传（开关默认关）**：`build_qoder_body` 的 `parameters` 段落（`:4001-4018`）后追加：

```python
_PARAM_PASSTHROUGH = ("temperature", "top_p", "stop", "seed", "n",
                      "presence_penalty", "frequency_penalty",
                      "logit_bias", "response_format")
if _env_flag("QD_PARAM_PASSTHROUGH"):
    for _k in _PARAM_PASSTHROUGH:
        _v = payload.get(_k)
        if _v is not None:
            params[_k] = _v
```

- 灰度：先单 Key 开 1 小时，观察 `invalid_parameter_error` 与 400 比例；无异常再全量。
- 回滚：`QD_PARAM_PASSTHROUGH=0`（默认即关）。

**P1-6 `n != 1` 显式 400**：chat 入口校验（Go 口径，`params.go:72-90`）。默认**只记日志**，`QD_STRICT_PARAMS=1` 才 400，避免打断现有客户端。

**P1-7 `reasoning_effort` 未知档位改"丢弃 + 日志"**：`normalize_reasoning_effort`（`:2817-2828`）的就近映射分支加开关 `QD_EFFORT_CLAMP`（默认 `1` 保持现状；`0` 返回 `None` 并记 note）。理由：就近映射是"静默改变语义"。

**P1-8 移植 #55（system 内联 + 指纹维度）**：`qoder_anthropic.py` 加 `msg_system_mode()/count_msg_system()`，`system_prompt_of` 在 `inline` 模式下不在 messages 内合并；`messages_to_chat`（`:251`）把 system 就地降级为 user；`prompt_fingerprint`（`qoder_proxy.py:1981`）增加 `sys_src/n_msg_system`。开关 `QD_ANTHROPIC_MSG_SYSTEM=inline|merge|drop`（默认 inline，`merge` 回旧行为）。收益：缓存命中率可量化回升（上游实测 14.7% → 92%）。

**P1-9 按模型 `max_tokens` 默认**（需 live 目录）：`params` 未给时取 `catalog_meta.get("max_output_tokens")`，无则 32768。前提：把 live `/model/list` 的 `max_output_tokens` 写进 `catalog_meta`。风险低、收益低，可与 03 专题的目录改造合并做。

### 5.3 不要动（P2）

| 项 | 位置 | 理由 |
|---|---|---|
| COSY 签名算法 / `identity` 9 键 / payload 结构 | `qoder_sign.py:561-594` | 硬校验，错即 403 |
| `cosy-clientip: 169.254.198.161` | `:609` | 官方固定值，上下游一致 |
| `accept-encoding: identity` | `:611` | 保证签名 body 字节与发送字节一致；改 gzip 有 403 风险 |
| `login-version: v2` / `x-model-source: system` | `:615`、`:622` | 上游一致 |
| `business.version=0.1.43` / `source=1` / `version="3"` / `session_type=qodercli` | `baseprompt.json` | 上游 v1.7.0 模板零改动，改它无证据 |
| 设备轮询 UA `"QoderWork"` | `accounts:3639` | 上游也未改；待 intl 官方抓包再动 |
| `Connection: close` / 无 ALPN | urllib 默认 | 上游同样是 urllib；要改需自建连接池（≥3 人日，属性能议题） |

## 6. 验证方法

### 6.1 单测：头矩阵断言 + 源码守卫

照抄上游 `[45]` 组 17 条断言（`git show 513254d -- _test_qoder.py` 可直接取），本地再加：

| 断言 | 方式 |
|---|---|
| `CosySession.headers()` 含 `cosy-business-product/type/scene/machineos`，SSE 时含 `machinehostname` | 直接调用，集合断言 |
| `headers()["user-agent"] == "undici"`，且全仓 `"Go-http-client/2.0"` 只允许出现在 `qoder_accounts.CLIENT_UA` 一处 | 源码级守卫（上游 #45-13/14 同款思路） |
| `derive_id` 匹配 `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-a[0-9a-f]{3}-[0-9a-f]{12}$`；`QD_DEVICE_ID_LEGACY=1` 时退回 32 hex | 参数化 |
| `generate_request_id` 50 次全不同、`^[0-9a-f]{32}$`、不含 `derive_id(uid,"req")` 前缀 | 同上游 #45-7..10 |
| 两区 `Account.headers()["User-Agent"]` 分别是 `QoderWork/`、`Qoder/` | 同上游 #45-11..12 |
| `desktop_headers()` **不含** `X-Request-ID/X-Machine-ID/X-Session-ID/Origin/Referer` | 新断言（本地当前会红） |
| `desktop_headers()` 在无原生桥时不含任何 `cosy-machine*`（issue #10） | 本地已有（`_test_qoder.py:1682`），改最小集后仍须绿 |

跑法：`python tests/run_all.py`（当前 20 suites / 889 checks 基线）+ `python _test_qoder.py`。上游合流后套件数会变（78 文件 / 1392+ checks）。

### 6.2 抓包对照（改前/改后 diff）

本机 socket 服务端抓 wire 头（本次已用，脚本要点）：

```python
import socket, threading, urllib.request
from qoder_sign import CosySession, qoder_encode
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
def serve():
    c, _ = srv.accept(); data = b""
    while b"\r\n\r\n" not in data: data += c.recv(65536)
    print(data.split(b"\r\n\r\n")[0].decode("latin-1"))
    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"); c.close()
threading.Thread(target=serve, daemon=True).start()
sess = CosySession(uid="u", access_token="t")
body = qoder_encode(b"{}")
h = sess.headers(body, "http://127.0.0.1:%d/algo/api/v2/model/list?Encode=1" % port,
                  model_key="", sse=False, accept="application/json")
urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%d/algo/api/v2/model/list?Encode=1" % port,
    data=body.encode(), method="GET", headers=h), timeout=5).read()   # 观察服务端收到的头
```

对照基准：① 改前输出（§1.2）；② `git show v1.7.0:qoder_sign.py` 的头字面量；③ 官方客户端抓包（若可获取，优先）。

### 6.3 真实账号探针（成本可控）

| 探针 | 请求 | 观测 | 成本 |
|---|---|---|---|
| 推理头对齐 | 1 条最小 chat（`max_tokens=16`） | HTTP 200 + SSE 首帧 + `cached_tokens` 字段 | 1 次额度 |
| 采样参数 | 1 条带 `temperature=0.2, top_p=0.9, stop=["\n\n"]` 的 chat | 是否 400 / `invalid_parameter_error` | 1 次额度 |
| `max_thinking_tokens` | 1 条 `reasoning_effort=none` + `max_thinking_tokens=0` | 是否 400、思考帧是否消失 | 1 次额度 |
| 活动头最小集 | 1 次 `campaigns()` | CLAIMABLE 数量（对比 `QD_DESKTOP_HEADERS=full`） | 0 额度 |
| 设备码形态 | 1 条 chat | 200 即通过（上游对换设备不敏感，见 #61 提示） | 1 次额度 |

**前置**：探针在 `QD_DEVICE_ID_LEGACY=1`（不换设备）+ 独立 API Key 下做，避免污染主池冷却状态。

### 6.4 缓存命中率观测（P1-8 的核心验收）

- 指标：`cached_tokens / prompt_tokens`（`qoder_proxy.py:618` 已算），按 `model + 入口` 分组。
- 方法：同一多轮会话（>8 轮、带动态 system）分别跑 `QD_ANTHROPIC_MSG_SYSTEM=merge` 与 `inline`，比较第 4 轮起命中率。
- 预期：`inline` 明显更高（上游实测 92% vs 14.7%）；若无差异，说明 system 本来就不在 messages 里，回看 `n_msg_system`。
- 可写测试的断言：`prompt_fingerprint` 的 `system_sha` 在 `inline` 下逐轮稳定。

## 7. 风险与未决问题

1. **machineId 变更 = 对上游换设备**（#61 明确警告）。需拍板：存量号池接受一次"换设备"，还是默认 `QD_DEVICE_ID_LEGACY=1` 只对新号启用新形态。**未决**。
2. **UA 改成 `undici` 后仍与协议不符**：我们实际是 HTTP/1.1 + 无 ALPN + `Connection: close`。`undici` 是 Node 的 fetch 实现（HTTP/1.1 客户端，语义自洽），比 `Go-http-client/2.0` 更可信；但"每请求新建 TLS、无会话复用"本身仍是特征。是否本轮自建连接池（≥3 人日）**未决**。
3. **`cosy-version=1.1.75` 是否需要与账号实际客户端版本匹配**：官方错误码 `430`=版本过低（上游 `docs/risk-control-and-queueing.md` §7.4）。我们报 1.1.75 高于客户端，理论上不会判"过低"，但"版本号与行为不匹配"（1.1.75 该有的新字段我们没有）是否被检测**未验证**。
4. **采样参数是否 400 完全没证据**：仓内 `usage/` 日志只有本地测试桩（110 行全是 502 桩），无真实上游错误样本。必须先探针。
5. **活动头最小集会不会让 CN 的 CLAIMABLE 变少**：issue #10 的结论是"派生六头全发才被过滤"，最小集不发机器头应更安全；但去掉 `Content-Type`/`Origin` 后服务端是否改变行为**未验证**（上游已这么干，风险低）。
6. **两套 hostname 来源**（桌面=本机 hostname，推理=按账号派生 `DESKTOP-XXXXXXX`）。容器里本机 hostname 是随机 hex（非 `DESKTOP-` 前缀），与官方 Windows 桌面端形态不符——是否规整成 `DESKTOP-XXXXXXX` 需拍板（上游只做 header-safe 规整，没伪造前缀）。
7. **intl 推理主机 api1→api3** 与标头同批修最省事，但属"端点"议题（02/05），需与该专题 owner 对齐，避免双改。
8. **Go 的 `appcode/date/signature` 认证方案**用于哪些端点未验证；若某些 openapi 端点其实要求该方案，我们现在的纯 Bearer 可能只是"刚好能用"。**未决**。
9. **`n != 1` 改 400 会打断现有客户端**（它们现在拿到 1 个 choice 也能用）。建议默认只告警。

## 8. 工作量估计（人日）与依赖

| 任务 | 人日 | 依赖 | 备注 |
|---|---:|---|---|
| P0-1 标头对齐 `qoder_sign` + 单测矩阵 | 1.0 | P0-A 合流（05 专题） | 含 wire 抓包对照 |
| P0-2 删除 proxy UA 覆写 + 源码守卫 | 0.2 | P0-1 | 与 P0-1 同一 PR |
| P0-3 设备码/请求 id + legacy 开关 | 0.5 | P0-A | 灰度前备份 `accounts/` |
| P0-4 账号 UA/活动头/机器常量 | 0.8 | P0-A | 含 claim 的 Content-Type 修复 |
| P1-5 采样参数透传 + 探针 | 1.5 | P0-1 上线 | 0.5 代码 + 1.0 灰度观测 |
| P1-6/7 `n!=1`、effort 不替换 | 0.5 | P0-A | 开关化 |
| P1-8 移植 #55 + 指纹维度 | 0.5 | 合流后（上游已有实现） | 若合流则≈0（直接拿到） |
| P1-9 按模型 max_tokens | 0.3 | 03 专题目录改造 | 可延后 |
| 缓存命中率 A/B 观测 | 1.0 | P1-8 / P0-1 | 需真实多轮会话 |
| 文档与看板口径更新 | 0.5 | 全部 | `docs/internals.md` 头表 |
| **合计** | **6.8**（合流已含 #55/#61/#75 时约 **3.8**） | | |

依赖图：`P0-A 合流` → `P0-1/2/3/4`（可并行）→ `P1-5 探针` → `P1-6/7/9`；`P1-8` 与 `P0-*` 无耦合，可最先做。

**一句话取舍**：只能做一件事时先做 P1-8（system 内联）——唯一有量化损失（92%→14.7%）的项；能做两件时加 P0-1（`undici` + `cosy-business-*` + `machineos/hostname`），它同时消除 5 个"官方客户端不可能发出"的特征。
