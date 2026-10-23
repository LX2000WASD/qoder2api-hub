# Q1 官方提示词与请求体瘦身 + 「伪装内核直连 API」可行性

> 基线：main `abb38c8`（上游 v1.3.4 + 2 个本地提交）；上游对照 v1.7.0（`ef900d7`）。
> Go 参考：`/home/sixiao/tool/脚本/_refs/Qoder-2API-Go`（EchoPing07/Qoder-2API-Go，README 自述为 fengyinxia/qoder2api 的 Go 重写，31 commits，最后提交 2026-10-19）。
> 证据等级：【已验证】本地实测或代码/文档直接可见；【推断】由多源事实合推；【未验证假设】无直接证据，需联网/真实账号实验。
> 本文件不改任何源码；所有体积数据由本地 `import qoder_proxy` 后调用 `build_qoder_body` 实测，**未发网络请求**。

## 0. 结论摘要（TL;DR）

1. **运行期真正上行的官方内容只有 system 提示词，工具表从不发送**：模板 `tools`（14 个、44,260 B）在无客户端工具时被置为 `[]`、有客户端工具时被整体替换（`qoder_proxy.py:4020-4026`），任何组合都不会上行。官方 system（12,747 B JSON / 12,324 字符 ≈ **3,081 tokens**）**只在「客户端没有有效 system 且 `--system-prompt` 为默认值」时**上行（`qoder_proxy.py:3928-3942`）。
2. **四种组合实测**（同一 53 字符用户消息、单工具）：无 system/无 tools **14,446 B**；无 system/1 tool **14,633 B**；有 system/无 tools **1,771 B**；有 system/1 tool **1,958 B**。官方 prompt 的净增量 **12,675 B**（qoder_encode 后 **+16,900 B** 上行字节）。
3. **「污染」要分三层**：仓库/镜像层 59,643 B（+59 KB 镜像，可忽略）；单请求上下文层 +3,081 tokens（短请求占 60%–98%，120k 字符长会话仅 9.3%）；**模型行为层才是主要问题**——模板把 `Platform: windows / MINGW64 / Today's date: <no value>` 写死（`baseprompt.json` 的 `messages[0].content` 第 109–116 行），并反复要求使用 TodoWrite/Bash/Task，而这些工具在 `tools=[]` 时根本不存在。**「工具表污染上下文」的说法不成立**（从不发送）；「短无 system 请求被 prompt 支配」成立。
4. **删掉官方提示词后反代照常工作**：system 不是协议必需。Go 参考的 `newRequestBody()` 只内联骨架、完全不构造 system（`bridge/chat.go:96-136`，注释明确写 “the former baseprompt.json, now inlined”）。更硬的反证：本仓在客户端带 system 时**已经把官方 prompt 整体替换成客户端文本**（`qoder_proxy.py:3939-3940`）；若上游强制校验官方 prompt，Claude Code / Codex / 任何带 persona 的客户端路径早就全线失败——而项目自述这些客户端是主力场景。**【推断，强度高】**
5. **「伪装内核」的门槛在认证层与客户端指纹层，不在 prompt**：COSY 签名覆盖 body（`qoder_sign.py:578-594`），cosy-* 头 / UA / 设备身份决定「你像不像官方客户端」（`qoder_sign.py:596-623`）。上游 v1.7.0 把推理 UA 从 `Go-http-client/2.0` 改成 `undici`、cosy-version 升到 1.1.75、补 cosy-business-*（`git show v1.7.0:CHANGELOG.md` 第 235-237 行），恰好证明被识别的是头部。
6. **上游 v1.7.0 对 `baseprompt.json` 零改动**（blob `2d6de81c` 与 v1.3.4 相同；`git diff --stat v1.3.4 v1.7.0 -- baseprompt.json` 空输出），也**没有**「最小体」开关（v1.7.0 全仓 grep `QD_.*PROMPT|MINIMAL|OFFICIAL_PROMPT` 无命中）。`--system-prompt` 语义照旧（v1.7.0 `qoder_proxy.py:5157-5167`、`docs/configuration.md:29`）。→ 这是本地独有的优化空间，但**应在上游合流（P0-A）之后落**，否则补丁要重放。
7. **推荐方案 A+**：新增 `QD_MINIMAL_BODY`（默认 `0` 保持现状，灰度稳定后默认 `1`）。开启时用「模板骨架（messages/tools 置空）」构造请求体，等价于只去掉官方 system；`--system-prompt` 仍可注入中性或自定义提示词。改动约 10 行 + 1 个测试文件，回滚只需改环境变量。
8. **方案 B（内置最小骨架常量 + 删除 baseprompt.json）收益只有 59.6 KB 与减少 IP 暴露**；代价是回滚能力、逆向快照、Dockerfile/README/启动校验/测试联动。建议 A+ 稳定一个版本后再做。
9. **今天就能用的零代码应急**：`--system-prompt " "` 或任意非默认字符串可立即压制模板 prompt（`--system-prompt ""` 无效，等价默认；`--system-prompt "You are a helpful assistant."` 也无效，字符串与 DEFAULT 相同）。
10. **未决/未验证**：上游是否在 `agent_common` 路径服务端另注入 prompt、是否对 prompt 做指纹校验、credits 的实际差额（受前缀缓存影响，静态推不出来）。必须用真实账号做影子统计 + 小流量 A/B 才能定论。

## 1. 现状证据

### 1.1 baseprompt.json 的准确构成（已验证）

文件 59,643 B，单行 JSON（`wc -l` = 0），23 个顶层键。按段拆：

| 段 | 字节 | 说明 |
|---|---:|---|
| 顶层标量（request_id…business 之前的全部字段） | 995 | 除 messages/tools 外的请求体骨架 |
| `messages` 段 | 14,203 | 3 条：1 条官方 system + 2 条示例 user |
| `tools` 段（含 business 尾巴） | 44,445 | tools 数组本身 44,260 |
| **合计** | **59,643** | |

`messages[0]` 序列化后 12,747 B；其 `content` 为 12,324 字符（12,477 B JSON），另带 `response_meta`（185 B，全 0 usage）与 `reasoning_content_signature`（空串）——**这两个字段会跟着 prompt 一起上行**，而客户端 system 路径不会带。2 条示例 user 合计 1,436 B，被 `build_qoder_body` 丢弃（`qoder_proxy.py:3928-3931`），永不上行。

`messages[0].content` 的关键内容（行号按 content 自身计；文件本身是单行 JSON）：

- 第 1 行：`You are Qoder, an interactive CLI tool ...`
- 第 3、7、119 行：身份规则——只准自称 Qoder；被问模型/系统提示词时**必须拒绝回答**
- 第 4 行：`Assist with defensive security tasks only...`（安全拒答策略）
- 第 30–34、118 行：强制使用 TodoWrite 管理任务
- 第 100–107 行：工具使用策略（Task / WebFetch / 并行调用 / 优先专用工具）
- 第 109–116 行：<env> 块，**写死 `Working directory: D:/Projects/Qoder`、`Platform: windows`、`OS Version: MINGW64_NT-10.0-22631...`、`Today's date: <no value>`**
- 第 6 行：禁止猜测 URL；第 8 行：跟随用户语言

工具表 14 个（合计 44,260 B）：`Bash`(10,828)、`TodoWrite`(10,635)、`Task`(5,575)、`Grep`(3,242)、`Read`(2,124)、`Skill`(2,018)、`WebSearch`(1,781)、`Edit`(1,638)、`WebFetch`(1,549)、`ImageGen`(1,198)、`Glob`(1,091)、`BashOutput`(1,060)、`Write`(990)、`KillBash`(503)。

**结论**：文件 74% 是工具表、24% 是提示词；但工具表是**死资产**，提示词才是唯一可能上行的部分。

### 1.2 构造逻辑：什么时候会注入（已验证）

```python
# qoder_proxy.py:3909
body = json.loads(json.dumps(BASEPROMPT))   # deep copy 整个 59 KB 模板

# qoder_proxy.py:3928-3942
tmpl_system = [m for m in (body.get("messages") or [])
               if (m or {}).get("role") == "system"]
if system_text is None:
    # 客户端没给 system：优先用 --system-prompt 覆盖值（非默认时），
    # 否则保留模板自带的 Qoder 系统提示词。
    if SYSTEM_PROMPT and SYSTEM_PROMPT != DEFAULT_SYSTEM_PROMPT:
        system_text = SYSTEM_PROMPT
    else:
        body["messages"] = tmpl_system          # ← 官方 prompt 从这里上行
if system_text is not None:
    body["messages"] = [{"role": "system", "content": system_text}]
body["messages"].extend(flat)

# qoder_proxy.py:4020-4026
tools = payload.get("tools")
if tools:
    body["tools"] = tools
else:
    body["tools"] = []                           # ← 模板工具表被丢弃
```

`system_text` 来自 `flatten_messages`：取**第一条** system/developer 的非空文本，其余 system 静默丢弃（`qoder_proxy.py:3829-3832`）。实际决策矩阵（本地实测）：

| 客户端 system | `--system-prompt` | 实际发出的 system | 字节 |
|---|---|---|---|
| 缺失 | 默认 `"You are a helpful assistant."` | **官方模板 system** | 12,747 |
| 缺失 | 任意非默认串（含 `" "`） | 该字符串 | 按串长 |
| 缺失 | 空串 `""` | **官方模板 system**（falsy 回落） | 12,747 |
| `""`（空） | 默认 | **官方模板 system**（`if ... and text` 跳过） | 12,747 |
| `" "`（空白） | 默认 | 空白 system（`sanitize_text` 未命中指纹时原样保留） | 1 |
| 非空文本 | 默认 | 客户端文本（官方 prompt 被整体替换） | 按串长 |
| 多条 system | 默认 | 仅第一条 | — |
| developer 角色 | 默认 | 当作 system | — |

两个容易踩的坑：
- 客户端显式传 `system: ""` 以为自己关掉了提示词，实际会拿到 12.7 KB 官方 prompt；
- 客户端传 `system: " "` 反而能压掉——因为这些字节在 `sanitize_text` 里不含指纹、不会被 strip。这是行为不一致，但也是现成的应急开关。

Responses 入口的 `instructions` 会被转成 system（`qoder_proxy.py:5119-5124`）；Anthropic 入口把顶层 system + messages 内 system 合并后放最前（`qoder_anthropic.py:122-143, 307-310`）。因此 Codex/Claude Code 正常使用时都带 system，默认路径下**不会**触发模板 prompt；只有裸 OpenAI 调用、或客户端确实不设 system 时才会。

### 1.3 使用点逐条清单（已验证）

| 位置 | 用途 | 是否影响上行 |
|---|---|---|
| `qoder_proxy.py:284` | `DEFAULT_SYSTEM_PROMPT` 常量 | 判定「是否用户覆盖」 |
| `qoder_proxy.py:287-293` | 导入期读 `baseprompt.json`；缺失则 `BASEPROMPT={}` 静默兜底 | 是 |
| `qoder_proxy.py:299-342` | 启动期骨架校验（7 个键 + messages 结构），坏则 `SystemExit(1)` | 只影响启动 |
| `qoder_proxy.py:3909` | 每请求深拷贝模板（实测 0.239 ms；`build_qoder_body` 总耗时 0.256 ms，93% 花在这里） | 是（分配） |
| `qoder_proxy.py:3930-3942` | system 注入/替换 | **是** |
| `qoder_proxy.py:4020-4026` | tools 替换/置空 | **是** |
| `qoder_proxy.py:7826-7828, 7895` | `--system-prompt` CLI 选项与全局赋值 | 是 |
| `qoder_proxy.py:7841-7843` | main 里调用骨架校验 | 启动 |
| `qoder_proxy.py:7969-7971` | 启动日志打印 `catalog: baseprompt.json` / MISSING | 仅日志 |
| `Dockerfile:55` | `COPY ... baseprompt.json ./` | 镜像 |
| `README.md:168, 334, 628` | 参数表 / 链路图 / 文件表 | 文档 |
| `_test_qoder.py:1023-1049, 1061-1062` | 断言「客户端 system 替换模板」「无 tools 置空」「无 system 保留模板」 | CI 通过 `tests/_test_legacy.py:16-21` 包装运行 |
| `tests/_test_anthropic_wiring.py:106-130` | 构造无 system 的 body，只断言工具历史形态，不断言 prompt | 无耦合 |
| `dashboard.html:505` | 只有一处「提示词前缀命中复用比」文案，与 baseprompt 无关 | 否 |
| `qoder_settings.py` | 无任何 prompt/baseprompt 引用（grep 空） | 否 |
| 智能体配置 | **本仓 HEAD 不存在**该模块（上游 v1.7.0 才新增 `qoder_agents.py`） | — |
| `qoder_sign.py:578-594` | 签名覆盖 body 字符串；不解释 prompt 内容 | 间接（改 body 必须重签） |

### 1.4 四种组合实测（已验证）

方法：本地 `import qoder_proxy`，构造 `{"model":"qmodel_38max","messages":[...],"tools":...}`，调用 `build_qoder_body(payload, None, "qmodel_38max", realm="cn")`，对结果 `json.dumps(..., ensure_ascii=False)` 取 UTF-8 字节数，再用 `qoder_encode` 算上行字节。无网络。

固定 fixture：用户消息 53 字符（中文）；客户端 system 39 字符；工具为 1 个 `get_weather` function（189 B）。

| 客户端 system | 客户端 tools | body 总字节 | messages 字节 | tools 字节 | 上行（编码后） |
|---|---|---:|---:|---:|---:|
| 无 | 无 | **14,446** | 12,861 | 2 | 19,264 |
| 无 | 1 个 | 14,633 | 12,861 | 189 | 19,512 |
| 有 | 无 | **1,771** | 186 | 2 | 2,364 |
| 有 | 1 个 | 1,958 | 186 | 189 | 2,612 |

官方 prompt 净增量：**12,675 B JSON / 16,900 B 上行**。无论哪种组合，模板 44,260 B 的 tools 都不出现。

按会话长度折算（本地估算，公式同 `qoder_anthropic.py:567-583` 的 CJK 感知估算；英文 prompt ≈ 3,081 tokens）：

| 对话字符数 | 带官方 prompt | 不带 | 差 | 上行差 | prompt 占 token 比 |
|---:|---:|---:|---:|---:|---:|
| 200 | 14,785 | 2,072 | 12,713 | 16,950 | 98.4% |
| 2,000 | 20,185 | 7,472 | 12,713 | 16,950 | 86.0% |
| 8,000 | 38,185 | 25,472 | 12,713 | 16,950 | 60.6% |
| 30,000 | 104,185 | 91,472 | 12,713 | 16,950 | 29.1% |
| 120,000 | 374,185 | 361,472 | 12,713 | 16,950 | 9.3% |

即：**绝对开销恒定 3,081 tokens；相对开销由会话长度决定**。短问答被 prompt 支配，长上下文会话只占个位数百分比。上下文上限是 `max_input_tokens`（多数模型 180k/200k/1M），3k 约占 1.7%–0.3%。

> 口径说明：本表「不带」列用 `system: " "` 抑制官方 prompt（多一个 1 字符 system 消息），因此上行差 16,950 B 比 1.4 表首行的 16,900 B 多约 50 B；两者是同一量级。

### 1.5 「污染」到底污染在哪一层（已验证 + 推断）

| 层 | 事实 | 影响 | 评级 |
|---|---|---|---|
| 仓库/镜像 | 59,643 B 文件；`Dockerfile:55` 复制进镜像 | +59 KB；私有提示词随仓库分发 | 低 |
| 内存/CPU | 每请求深拷贝 59 KB；0.239 ms/请求 | 100 rps 约占 2.4% 单核 | 低 |
| 上行体积/上下文 | 仅无 system 时 +12,713 B / +3,081 tokens | 短请求 60–98%，长会话 <10% | 中 |
| 计费 | 上游返回 `prompt_tokens`/`cached_tokens`/`credits`（`qoder_proxy.py:515-536`） | 静态推不出净差额：官方 prompt 是**跨会话共享的稳定前缀**，缓存热后可能极便宜；未验证 | 待测 |
| 模型行为 | 把模型塑造成 Qoder CLI agent：Windows env、TodoWrite/Bash/Task 指令、身份拒答、简洁输出；而 `tools=[]` 或只有客户端工具 | **主要问题**：短请求可能自称 Qoder、拒绝谈模型、建议 Windows 命令；被要求用不存在的工具 | 高 |
| 内容/合规 | 仓库公开携带官方提示词全文 | IP/合规暴露；删文件也不能清除 git 历史 | 中（非技术） |

## 2. 机制拆解

### 2.1 认证层：COSY 签名覆盖 body，但不解释 body

`qoder_sign.py:578-594`：`sig = md5(payload_b64 + "\n" + cosy_key + "\n" + date + "\n" + body + "\n" + path)`，`body` 是 `qoder_encode(json.dumps(body_obj))` 后的字符串（`qoder_proxy.py:4398`）。因此：

- 请求体在签名后**不可再改**（改一个字节签名即失效）——这也是为什么所有 body 决策必须在 `build_qoder_body` 内一次定稿；
- 服务端校验的是「签名与 body 是否匹配」，**不是**「body 里有没有官方 prompt」。签名层没有 prompt 语义。

### 2.2 客户端指纹层：头部才是「你像不像官方客户端」的主战场（已验证 + 推断）

上游 v1.7.0 的身份对齐提交（PR #75，`CHANGELOG.md` 第 235–237 行）写得非常直白：

> 推理链路：UA 改为官方 CLI 同款 `undici`（此前是 `Go-http-client/2.0`，**等于自报脚本**）；`cosy-version` 升到 `1.1.75`；补 `cosy-business-product` / `cosy-business-type` / `cosy-scene`……

v1.7.0 的头表（`git show v1.7.0:qoder_sign.py` 第 643–676 行）包含 `cosy-business-product: cli`、`cosy-business-type: agent`、`cosy-scene: assistant`、`cosy-machineos`、`cosy-machinehostname`。而本地 HEAD（`qoder_sign.py:600-623`）的 cosy-version 是 **1.1.64**、UA 被 `qoder_proxy.py:4455` 覆写成 **Go-http-client/2.0**、缺上述 business/scene/os 头。

上游风控 playbook 的排序（`git show v1.7.0:docs/risk-control-playbook.md` 第 15 行）：

> 结论：**出口 > 身份 > 调度 > 请求整形**。顺序反了就是白忙。

「请求整形」是最后一位，且其 L3 清单（同文件第 55 行起）只讲前缀稳定、结构化工具历史、体积上限、max_tokens，**没有一条讲提示词**。这从上游自己的经验侧说明：提示词不是被风控识别的关键变量。

另一条关键边界证据：目前可观测到的官方硬过滤发生在**活动/签到接口**，不是推理接口。Go 参考的注释（`auth/risk.go:3-21`、`auth/checkin.go:10-18`）明确写：

> A PAT-derived or random fingerprint is treated as an unofficial client and the daily-credit campaign is silently dropped … the six Cosy-Machine* headers are sent only when the genuine desktop fingerprint is available.

也就是说：**设备身份/机器头在活动链路会被判定并过滤；推理链路仍接受 PAT + 派生身份**（Go 实现就是这么跑的）。这直接划出了「伪装」的可行边界。

### 2.3 请求体字段：协议必需 vs 伪装信号（推断）

依据：本地 `build_qoder_body` 的写入点 + Go `ChatRequestBody`（`bridge/chat.go:68-94`）与 `newRequestBody`（96-136）+ 上游 v1.7.0 的同构代码。**没有上游协议文档**，以下「必需性」是推断，不是规范。

| 字段 | 本地值 | Go 参考 | 判断 | 依据 |
|---|---|---|---|---|
| `request_id`/`request_set_id`/`chat_record_id`/`session_id` | 每请求新 UUID | 同 | 必需（服务端会话/追踪） | 两边都写；Go 生产可用 |
| `stream` | `True` | `true` | 必需（SSE 端点） | `CHAT_PATH` 是 sse（`qoder_proxy.py:281-282`） |
| `chat_task` | `FREE_INPUT` | `FREE_INPUT` | 必需（任务类型） | 两边硬编码一致 |
| `session_type` | `qodercli` | `qodercli` | 必需（客户端类型） | 两边一致；上游 v1.7.0 不改 |
| `agent_id` | `agent_common` | `agent_common` | 必需/强信号 | 同时出现在 URL 查询串 `AgentId=agent_common` |
| `source` / `version` | `1` / `"3"` | `1` / `"3"` | 强信号（版本门 430 可能看它） | 两边一致；未验证 430 判定源 |
| `task_id` | `common` | `common` | 强信号 | 两边一致 |
| `aliyun_user_type` | 账号 user_type 或 `personal_professional_trial`（`qoder_sign.py:526`） | 账号 identity | 强信号（与账号绑定） | 两边都按账号覆盖；模板里的 `personal_standard` 会被覆盖 |
| `model_config.key` | 上游模型 key | 同 | 必需（路由） | 另有 `x-model-key` 头（`qoder_sign.py:620-622`） |
| `parameters.max_tokens` | 模板默认 32768，可被客户端覆盖 | 目录默认值 | 必需（配额/截断） | Go 注释：网关从 parameters 读完成上限（`bridge/params.go:55-58`） |
| `messages` | 客户端会话 | 同 | 必需 | — |
| `tools` | 客户端工具或 `[]` | 客户端工具或**省略** | 可选 | Go `omitempty`（`bridge/chat.go:88`、`bridge/params.go:288-300`） |
| `chat_context.text`/`originalContent` | 最新用户提示词 | 同 | 强信号（高亮/会话卡） | 两边都写 |
| `business.id`/`name`/`begin_at` | UUID/首 30 字/毫秒 | 同 | 弱必需 | 两边都写 |
| `business.product/version/type/stage` | `cli/0.1.43/agent/start` | **全省略** | 伪装信号（客户端画像） | Go 不发仍可用；上游把 product/type 又镜像到 cosy 头 |
| `is_reply`/`is_retry`/`code_language`/`chat_prompt`/`image_urls` | `true/false/""/""/null` | **全省略** | 伪装信号/冗余 | Go 不发仍可用；`is_reply=true` 对首轮是否语义正确**未验证** |
| `chat_context.chatPrompt/features/context` | `""/[]/[]` | 省略 | 冗余 | 无代码读取 |
| `model_config.display_name/format/api_key/url/max_input_tokens` | 目录元数据 | 仅 key/is_vl/is_reasoning/source | 冗余/画像 | 体积小，保留无害 |

**结论**：真正影响「被当成官方内核」的是头部 + 设备身份 + `agent` 字段族；`business.product/version/type/stage` 和 `is_reply` 一类是画像信号，不是协议必需。**prompt 与 tools 内容不属于任何一层必需字段**。

### 2.4 「缺官方 system 会被识别/降级」的证据审查

支持「会被识别」的直接证据：**没有**。

- 上游 `qoder_proxy.py` / `qoder_sign.py` / docs / tests 中没有任何「校验 system 内容/哈希」的代码或文字；
- v1.7.0 代码里 grep `baseprompt` 只出现在加载、构造、启动日志三处（docs 另有两处说明），没有任何服务端返回码分支依赖它；
- v1.7.0 的 CHANGELOG 与 docs 中没有「去掉提示词被封/降级」的记录（仅覆盖本仓可读到的上游文档，不含 GitHub issue 全文）。

反对「会被识别」的证据（更强）：

1. **本仓自己的主流路径已经替换了官方 prompt**：客户端带 system 时，`build_qoder_body` 发的是客户端文本（`qoder_proxy.py:3939-3940`）。Claude Code（Anthropic 入口）与 Codex（Responses `instructions`）必然带 system，这正是 README 主推的用法（`README.md:334` 链路、docs/clients）。若上游识别官方 prompt，这些路径不可能工作。
2. **Go 参考实现完全不构造 system**：`newRequestBody()`（`bridge/chat.go:96-136`）没有 messages 默认内容；`HandleChat` 直接 `body.Messages = transform.BuildQoderMessages(messages, ...)`（`bridge/chat.go:207`），`BuildQoderMessages` 只转换客户端消息、不注入任何提示（`transform/transform.go:365-384`）。注释明说模板是 “the former baseprompt.json, now inlined”——**社区移植时主动丢弃了 prompt/tools 内容，只保留骨架**。证据强度：中（第三方实现 + 完整代码路径；未在本机联网复验）。
3. **上游自己的风控排序**把「请求整形」放最后，且清单里没有提示词（见 2.2）。
4. **目前可观测到的官方硬过滤只发生在活动链路**（Go `auth/risk.go:3-21`），推理链路容忍 PAT/派生身份。

因此当前最合理的判断是：**上游不校验、也不会因为缺 prompt 降级推理；最多在模型行为上自然变化**。【推断，强度高；仍需 A/B 观测异常码】

### 2.5 为什么现在没炸，以及什么时候可能炸

- 对带 system 的客户端：官方 prompt 从不上行，等价于已经做了「去提示词」，只是没人明说。**这本身就是长期运行的 A/B 对照组**。
- 对无 system 的裸请求：模型会以 Qoder CLI agent 的口吻回答。用户可能觉得「怪」但不会报错；真正的症状是：自称 Qoder、拒答身份/系统提示词、给出 Windows 路径、要求用 TodoWrite。
- 可能炸的场景：
  - 上游未来对 `agent_common` 路径增加 prompt 指纹（无迹象）；
  - 服务端对空 `tools` 或空 system 的兼容性变化（Go 省略 tools 可用，空数组也已在用，未见异常）；
  - 模型行为回归被用户感知（不是协议故障，是产品故障）。

## 3. 上游 v1.7.0 与 Go 参考实现对比

| 维度 | 我们的做法（HEAD abb38c8） | 上游 v1.7.0（ef900d7） | Go 参考 | 结论 |
|---|---|---|---|---|
| baseprompt.json | 59,643 B，blob `2d6de81c` | **同一 blob，零改动**（`git diff --stat v1.3.4 v1.7.0 -- baseprompt.json` 空） | 无文件，骨架内联进 struct | 上游未解；本地可自研 |
| 官方 system 注入 | 无客户端 system 且 `--system-prompt` 默认时注入（3928-3942） | 逻辑逐行相同（v1.7.0:5157-5167） | **完全不注入**（chat.go:96-136） | 上游无开关；Go 证明可去掉 |
| 模板 tools | 恒被替换/置空（4020-4026） | `payload.get("tools") or []`（v1.7.0:5256） | 有则发、无则省略（params.go:288-300） | 一致；44 KB 模板工具从不上行 |
| 最小体开关 | 无 | **无**（v1.7.0 全仓 grep `QD_.*PROMPT`、`MINIMAL`、`OFFICIAL_PROMPT` 均无命中） | 设计上就是最小体 | 无现成实现可抄 |
| 多 system 合并 | 只取第一条（3829-3832） | 同（`flatten_messages`） | developer→system，全保留（transform.go:287-297） | 本地弱于 Go；与 Anthropic 入口合并策略不一致 |
| Anthropic 动态 system | 合并进最前 system（前缀逐轮变） | v1.5.5 起默认 inline（`QD_ANTHROPIC_MSG_SYSTEM`，`CHANGELOG.md:439-441`） | 无 Anthropic 入口 | 本地落后；与 prompt 改动同批做可提升缓存 |
| 出站 UA / cosy-version | `Go-http-client/2.0` / `1.1.64`（qoder_sign.py:616, 525） | `undici` / `1.1.75` + business/scene/machineos 头（v1.7.0:525-528, 643-676） | `Go-http-client/2.0` / `0.1.43`（auth.go:1071-1089） | 头部才是识别层；本地必须对齐上游（Q4） |
| 设备身份 | 按 UID 派生（fingerprint） | 派生 + 活动链路真 UMID | 同派生；活动链路无真身份则不发送 | 活动链路硬过滤，推理链路容忍 |
| 图片顶层冗余 | 仍填 `chat_context.imageUrls`/`image_urls`（3996-3998） | #59 后默认不填（v1.7.0:5226-5232） | 图片只在 messages | 合流时会被上游覆盖，注意冲突 |
| 工具类型清洗 | 入口 `normalize_tools`（7597） | build 内再 `normalize_tools(body)`（v1.7.0:5257-5261） | 原样透传 | 上游更稳；合并时保留上游 |

## 4. 方案选项

### 方案 A+：开关化「不注入模板 system」，保留全部骨架字段（推荐）

做法：在导入期预生成 `BASEPROMPT_MINIMAL`（= 模板深拷贝后 `messages=[] tools=[]`，实测 1,198 B）；`QD_MINIMAL_BODY`（默认 `0`）为 `1` 时用它做 base，其余逻辑（model_config/chat_context/parameters/business/tools）不变；`--system-prompt` 仍然优先。

- 收益：无 system 请求体从 ~14.4 KB 降到 ~1.4 KB；3,081 tokens 归零；Windows env / 身份拒答 / 幽灵工具指令一起消失；深拷贝从 59 KB 降到 1.2 KB。
- 成本：约 10 行代码 + 1 个测试文件 + README 一段；0.5–1 人日。
- 风险：亲和键变化（首条消息从 system 变 user）→ 一次性换号/缓存冷启动；模型行为变化需 A/B；默认值切换需要灰度。
- 回滚：`QD_MINIMAL_BODY=0` 重启即回旧行为；文件未删，零数据迁移。

### 方案 B：内置最小骨架常量 + 删除 baseprompt.json

做法：把 1,198 B 骨架写成代码常量（或保留文件但只在 `QD_MINIMAL_BODY=0` 时加载）；删文件、删 `validate_baseprompt_skeleton`、改 Dockerfile/README/启动日志。

- 收益：仓库/镜像 -59.6 KB；不再分发官方提示词全文；无「忘开关又把 prompt 发出去」的可能。
- 成本：1–1.5 人日；回滚只能回滚镜像/提交；丢失从桌面端抓包来的协议快照（虽然 git 历史仍在）；`--system-prompt` 的「官方模板」默认语义要删掉。
- 风险：若上游某天要求 system 存在，恢复成本高；与 P0-A 合流冲突面更大。
- 结论：**不推荐现在做**；A+ 稳定一个版本、且合规上确实要清理时再做。

### 方案 C：零代码 `--system-prompt` 应急

`python qoder_proxy.py --system-prompt " "` 或 `--system-prompt "You are a helpful assistant. Be concise."`。

- 收益：今天就能验证「无官方 prompt」的线上表现；不重启代码。
- 代价：仍会下发一个 system 消息（1 字节空白或一句中性话）；每个部署实例都要改启动参数；`""` 无效。
- 用途：灰度前的预演，不作为最终形态。

### 方案 D：保留 prompt 但裁剪 env / 身份段

需要 fork 12 KB 提示词，维护成本高、每次官方更新都要重新对齐，且仍然保留 agent 人格。**不推荐**。

| 方案 | 成本 | 风险 | 收益 | 推荐 |
|---|---|---|---|---|
| A+ 开关 + 骨架 base | 0.5–1 人日 | 中（行为/亲和） | 高（token、行为、体积） | **首选** |
| B 删文件 | +1–1.5 人日 | 中高（回滚/快照） | 中（体积/合规） | 二期 |
| C 参数应急 | 0 | 低 | 中（仅验证） | 灰度前先做 |
| D 裁剪 prompt | 1 人日+持续维护 | 高 | 低 | 否 |
| 现状 | 0 | 行为污染持续 | 0 | 否 |

## 5. 落地改动清单（暂不实际改码）

### 5.1 方案 A+ 补丁草案（HEAD 行号；v1.7.0 合流后整体 +~1225 行）

```python
# 1) 导入期：在 BASEPROMPT 加载之后（qoder_proxy.py:289-293 之后）新增
BASEPROMPT_MINIMAL = {}
try:
    _sk = json.loads(json.dumps(BASEPROMPT))
    _sk["messages"] = []
    _sk["tools"] = []
    BASEPROMPT_MINIMAL = _sk
except Exception:
    BASEPROMPT_MINIMAL = {}

def minimal_body_enabled():
    """True = 不把模板 system 当兜底（客户端/--system-prompt 仍是唯一来源）。"""
    return os.environ.get("QD_MINIMAL_BODY", "0").strip().lower() in (
        "1", "true", "yes", "on")

def base_prompt():
    return BASEPROMPT_MINIMAL if minimal_body_enabled() else BASEPROMPT

# 2) build_qoder_body 第一行（qoder_proxy.py:3909）改为
body = json.loads(json.dumps(base_prompt()))

# 3) 启动日志（7969-7971）追加一行
log("prompt     : %s" % ("minimal (no template system fallback)"
                         if minimal_body_enabled() else "template fallback"))
```

**为什么这样就够**：模板 system 只在 `system_text is None` 分支被写入（3938）；骨架 `messages=[]` 时该分支写入空列表，随后 `extend(flat)` 得到纯客户端会话，与 Go 形态一致。客户端 system、`--system-prompt` 覆盖、tools 逻辑全部不变。**不要**只改 3938 行而不清空 `body["messages"]`，否则模板的 2 条示例 user 会跟着上行（1,436 B + 过期日期/技能列表）。

默认值策略：本次提交默认 `0`（CI 与现网行为不变）；影子统计 + 灰度通过后，下一次发布把默认改 `1`，并在 README 标注行为变更。

### 5.2 观测字段（与上游 `sys_src` 对齐）

上游 v1.7.0 用 `sys_src`/`n_msg_system` 排查缓存（`docs/internals.md:116`，`qoder_proxy.py:2168-2198`）。本地 HEAD 的 `prompt_fingerprint` 只看客户端 messages（`qoder_proxy.py:1981-1999`），**看不出最终 body 里 system 的来源**。建议：

- 在 `open_upstream` 的 `usage_ctx`（4392-4397）里加 `prompt_src`（`client|cli|template|none`）、`prompt_chars`、`tools_n`、`body_bytes`；
- 通过现有 8 处 `record_usage(... effort=usage_ctx.get("effort") ...)`（7300-7793）同款方式透传，或先只 `log()` 一行降低改动面；
- 字段命名避开 `sys_src`（上游含义是 Anthropic system 位置），用 `prompt_src` 防止合并时语义混淆。

### 5.3 测试改动

- `_test_qoder.py:1061-1062`（经 `tests/_test_legacy.py` 进 CI）断言「无 system 保留模板」；默认仍为 0 时保持通过，默认切 1 时必须改为按 env 分支断言。
- 新增 `tests/_test_prompt_budget.py`（纯本地、无网络），至少覆盖：
  1. `QD_MINIMAL_BODY=0` + 无 system → `messages[0].role == "system"` 且长度 12,324 字符（防模板被误改）；
  2. `QD_MINIMAL_BODY=1` + 无 system → `messages` 无 system、总字节 < 2 KB、且不含示例 user 的 `2026-04-15`/`available_skills`；
  3. 两种模式下「客户端 system 替换」「`--system-prompt` 覆盖」「无 tools 置空」「模板 tools 永不上行」；
  4. `baseprompt.json` 骨架 23 键、`messages`/`tools` 类型不变（防误删字段）；
  5. `system: ""`、`system: " "`、多 system 的现状快照（行为变更必须显式改测试）。
- `tests/_test_anthropic_wiring.py:106-130` 不受影响（不断言 prompt）。

### 5.4 文档改动（A+）

- `README.md:168` 参数表：`--system-prompt` 默认值说明改为「默认不注入（`QD_MINIMAL_BODY=1`）；设为 0 时回落到官方模板」；
- `README.md:334` 链路图、`README.md:628` 文件表补一句「模板仅作骨架/回滚资产」；
- 启动日志与 `/health` 之外无 UI 改动。

### 5.5 方案 B 追加改动（二期）

- 新增 `BODY_SKELETON` 常量（= 现有模板去掉 messages/tools 内容，1,198 B，23 键全保留）；
- `validate_baseprompt_skeleton` 改为校验常量（或仅在 `QD_MINIMAL_BODY=0` 时校验文件）；
- 删 `baseprompt.json`、`Dockerfile:55` 的 COPY、`README.md:628` 表项、`qoder_proxy.py:7969-7971` 的 MISSING 分支；
- 删除 `--system-prompt` 的「官方模板」语义（默认值改为空 → 不注入）。

### 5.6 回滚

- A+：`QD_MINIMAL_BODY=0` + 重启；无需回滚代码。
- B：回滚镜像/提交；git 历史仍保留 `baseprompt.json`（若合规要求「删除」，需重写历史，本任务不做）。

## 6. 验证方法

### 6.1 本地静态复现（已验证，可直接跑）

```bash
python3 - <<'PY'
import sys, os, json
sys.path.insert(0, os.getcwd()); os.environ.setdefault("ACCOUNTS_DIR", "_acc")
import qoder_proxy as P
def sz(o): return len(json.dumps(o, ensure_ascii=False).encode())
U = "请把下面这段 Python 改成异步：\n\nfor i in range(3):\n    print(i)\n"
TOOL = [{"type":"function","function":{"name":"get_weather",
         "parameters":{"type":"object","properties":{}}}}]
for sys_on in (False, True):
    for tools_on in (False, True):
        p = {"model":"qmodel_38max", "messages":([{"role":"system","content":"You are a helpful assistant for coding."}] if sys_on else []) + [{"role":"user","content":U}]}
        if tools_on: p["tools"] = TOOL
        b = P.build_qoder_body(p, None, "qmodel_38max", realm="cn")
        enc = P.qoder_encode(json.dumps(b, ensure_ascii=False).encode())
        print(sys_on, tools_on, sz(b), sz(b["messages"]), sz(b["tools"]), len(enc.encode()))
PY
```

期望值见 1.4 表（±个位数字节，取决于时间戳位数）。

### 6.2 影子统计（不改上行，先量影响面）

新增 `QD_MINIMAL_BODY=shadow`：build 时同时算出「带模板」与「不带模板」两个 body，只在 usage/log 记录 `prompt_src`、`prompt_chars`、`body_bytes` 与省下的 token 估算，**上行仍用旧 body**。目标：

- 统计「无有效 system」的请求占比（决定改动影响面）；
- 得到按模型/入口/key 的分布（chat vs responses vs anthropic）；
- 采样比对 body 字节与亲和键变化比例。

### 6.3 金丝雀 A/B（需要真实账号额度，需用户拍板）

- 分组：同一 Key 按时间片切换（例如 A 组 2 小时 / B 组 2 小时，同模型同提示集），避免「不同 Key 绑不同账号」的混杂；或给测试 Key 加 `X-QD-Prompt-Mode: minimal` 头（仅鉴权通过时生效），与默认组并行。
- 样本：每臂 ≥ 200 次成功请求，覆盖 3 类入口（chat/Responses/Anthropic）、≥ 5 个模型（含 DeepSeek 族与 qwen 族）。
- 指标（全部已有落盘/端点）：
  - 错误率与错误族：HTTP 4xx/5xx、信封 `inner_errors`（`qoder_proxy.py:998`、`/usage/perf` 6532）、`DataInspectionFailed` 计数；
  - token/计费：`prompt_tokens`、`cached_tokens`、`credits`/`original_credits`（515-536）、每请求均值；
  - 缓存：`cached_tokens / prompt_tokens`（上游 playbook 的 >80% 判据）；
  - 延迟：`ttft_ms`/`elapsed_ms`；
  - 行为：输出 token 均值、工具调用合法率（`finish_reason=tool_calls` 且参数可解析）、身份问题拒答率、中文回复率。
- 通过判据（建议）：错误率不劣化 >1pp；`credits`/请求不上升；缓存命中率下降 ≤5pp；无新的 400/403 子码；工具调用合法率不下降。
- 注意：切换瞬间亲和键变化（`derive_affinity_key` 取 `msgs[:2]`，`qoder_proxy.py:1954-1976`）会让无 system 的会话一次性换号；A/B 应跳过切换后前 10 分钟的缓存指标，或按「同一亲和键内」对比。

### 6.4 行为评测（小样本人工/自动）

固定 20 条提示，4 类：编码任务、身份/系统提示词追问、工具调用、中文长文。两臂各跑 3 次，盲评：
- 是否自称 Qoder / 是否拒答；
- 是否建议 Windows 路径或 TodoWrite（带模板臂的预期缺陷）；
- 工具调用是否命中客户端声明的工具；
- 回答长度/风格是否更接近通用助手。

### 6.5 不能本地验证的项

- 上游是否服务端注入 prompt（无接口可观测）；
- credits 的真实差额（依赖账号与缓存状态）；
- 是否有隐藏的 prompt 指纹校验（只能靠 A/B 异常码间接判断）。

## 7. 风险与未决问题

1. **亲和键漂移**：无 system 会话的首条消息由 system 变 user，`derive_affinity_key` 哈希变化 → 一次性换号 + 缓存冷启动。建议灰度时观察 `cached_tokens` 的前 10 分钟并接受一次性抖动；若要平滑，可让亲和键跳过 system（额外改动，未纳入 A+）。
2. **行为回归**：失去「简洁/先读后改/身份拒答/安全策略」约束；对通用 API 用户可能是改善，对想要 Qoder agent 体验的用户是退化。需要产品拍板（见 8）。
3. **计费不确定性**：官方 prompt 是跨会话共享前缀，缓存热后可能几乎免费；「省 3k tokens」在账单上可能小于预期。必须用 `credits` 实测。
4. **内容审核**：上游 `DataInspectionFailed` 针对消息内容；去掉提示词不会绕过审核，但模型更少自我审查，可能提高触发率。监控该项。
5. **空/空白 system 语义**：`system: ""` 反而触发官方 prompt（1.2 已实测）。是否顺手修成「空串 = 不要 system」需要拍板；修了会改变现有客户端行为。
6. **多 system 丢弃**：chat 入口只取第一条（3829-3832），Anthropic 入口全合并（122-143）。是否统一？统一会影响前缀缓存与现有行为。
7. **默认值**：A+ 默认 `0` 还是直接 `1`？建议先 `0` + 影子，再切 `1`。
8. **是否保留 prompt 作为可选模式**：`QD_MINIMAL_BODY=0` 可长期保留官方 prompt 作为「Qoder 风格」选项；也可以二期删除。需要产品/合规意见。
9. **是否彻底删文件**：删了仍可从 git 历史恢复；若目标是「不再分发」，需重写历史（本任务不做，且会影响所有克隆）。
10. **A/B 是否允许消耗真实额度/触发风控**：这是用户拍板项（Lead 计划 5.3 同问）。
11. **合流时序**：v1.7.0 的 `build_qoder_body` 行号与结构已变（新增图片门控 `5226-5232`、`normalize_tools` `5257-5261`）；建议 P0-A 合流后重放补丁，避免双份冲突。
12. **与 Q4 头对齐的混淆**：若同时改 UA/cosy-version 与 prompt，A/B 信号无法归因。必须分阶段：先头对齐（P0-B），再 prompt（P1-A）。

## 8. 工作量估计（人日）与依赖

| 工作项 | 人日 | 依赖 | 产出 |
|---|---:|---|---|
| A+ 补丁（开关 + 骨架 base + 启动日志） | 0.5 | P0-A 合流完成 | `QD_MINIMAL_BODY` |
| 新增 `tests/_test_prompt_budget.py` + 更新 legacy 断言 | 0.3 | A+ | 6–10 条断言 |
| 影子统计字段 + 日志 | 0.3 | A+ | 影响面数据 |
| 金丝雀 A/B（含脚本、采样、分析） | 1.0–1.5 | 真实账号额度；P0-B 头对齐已完成 | A/B 报告 |
| 行为评测（20 提示 × 2 臂） | 0.5 | 同上 | 行为对比 |
| 文档/README/启动提示 | 0.2 | A+ | 文档 |
| **A+ 合计** | **2.3–3.3** | | |
| B 二期（骨架常量 + 删文件 + 校验改造 + 测试） | 1.0–1.5 | A+ 稳定一个版本 | 无 baseprompt 版本 |
| 合流后补丁重放/冲突处理 | 0.3–0.5 | P0-A | — |

关键依赖：
- **P0-A（合流 v1.7.0）必须先做**：否则补丁行号/上下文与上游分叉，二次冲突成本高于收益。
- **P0-B（头对齐）先于 A/B**：否则无法区分行为差异来自 prompt 还是 UA/cosy-version。
- 真实账号额度是 A/B 的硬前提；没有额度时只能做影子统计与本地断言，结论停留在【推断】。
- 与 Q2（虚拟机检测）无直接依赖；与 Q3（模型管理）无依赖。
