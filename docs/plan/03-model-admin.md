# Q3 对外 API 的模型启用管理与密钥/接口治理

> 基线：main `abb38c8`（= 上游 v1.3.4 + f14031a + abb38c8）· 上游对照 `v1.7.0`（ef900d7）· Go 参考 `/home/sixiao/tool/脚本/_refs/Qoder-2API-Go`
> 标注约定：**[已验证]** = 读过代码/跑过命令 · **[推断]** = 由代码语义推导 · **[未验证假设]** = 需实验或用户确认
> 本文件只出方案，不含任何代码改动。

## 0. 结论摘要（TL;DR）

1. **[已验证] 现状能力是「黑名单 + 每 Key 白名单」，没有全局启用白名单。** 只有 `banned_models`（qoder_settings.py:382-396）与 `api_keys[].models`（qoder_settings.py:243、365-379）。官方 catalog 的 `enable` 只被透传成 `/v1/models` 的展示字段（qoder_proxy.py:2365），**不参与任何请求闸门**——从 catalog 读 `enable` 的全仓只有 2365 这一处（3744 行那个是 query 参数解析，与本议题无关）。
2. **[已验证] 闸门已收敛到单点** `_model_precheck`（qoder_proxy.py:6240-6249），chat / responses / anthropic 三入口共用（7608 / 7375 / 7248），顺序 = 跨区错配 → 全局封禁 → Key 白名单。这一点比「三份实现」健康，扩展时不该推倒。
3. **[已验证·缺陷] 白名单/封禁按客户端原始 `model` 字符串匹配，不做别名归一。** `_key_model_error`（6227-6238）跑在 `resolve_upstream_key`（open_upstream:4388）之前，所以 `Qwen3.8-Max` / `qwen3.8-max` / `qmodel_38max` / `qmodel_38max (Qwen3.8-Max)` 四种写法判定不同，而 catalog 明确定义它们等价（qoder_catalog.py:1636-1689）。
4. **[已验证·缺陷] 看板没有模型闸门 UI，而且面板保存 Key 会把白名单静默清空。** `GET /settings` 的 key 视图不返回 `models`（qoder_proxy.py:1878-1887）；`saveApiKeys` 的 payload 不含 `models`（dashboard.html:3183-3190）；后端 `"models": item.get("models")` → None → `_clean_model_patterns(None)` → `[]`（qoder_proxy.py:6732 + qoder_settings.py:243、346-362）。**上游 v1.7.0 同一 bug**（v1.7.0:qoder_proxy.py:9144 + v1.7.0:dashboard.html:4502-4510）。README:77/361/669 宣称「看板可配模型白名单」与代码冲突，**以代码为准**。
5. **[已验证] `/v1/models` 不按 Key 过滤、不按 enabled 过滤**（qoder_proxy.py:6414-6430 → `model_entry` 全量），返回 `upstream_key`、`aliases`、能力元数据、上下文窗口、思考档位、峰谷价（2341-2545）。客户端友好，但内部 key 外泄，且「管理开放启用的模型」在没有过滤的情况下无从生效。
6. **[已验证] 错误语义不合格**：闸门拒绝一律 `400 invalid_request_error`，且 `error.code` 是 HTTP 数字而非机器可读串（qoder_proxy.py:6054-6056）。OpenAI 生态期望 `404 model_not_found` / `403 permission_denied` + 字符串 code。Go 参考给的是 400 + "Supported: ..." 列表（bridge/handler.go:396-402、models.go:273-299）。
7. **[已验证] 上游 v1.7.0 可白拿**：模型清单磁盘缓存 7 天 + `GET /models/refresh`（#58，v1.7.0:qoder_proxy.py:2423/8633）、`?merge_realms=1`（CHANGELOG:698、v1.7.0:8664）、`realm`/`realm_exclusive`（v1.7.0:2586、qoder_catalog.py:1895）、清单驱动自动换区 + `X-Qoder-Realm(-Reason)`（#77，v1.7.0:174-210、8153）、官方文案容器可覆盖（#68，`QD_OFFICIAL_TEXT`，qoder_catalog.py:1755）、`/agents/models`（#81，v1.7.0:2042/8567）、`/pricing`（qoder_pricing.py）。**但上游也没有启用管理面**，且自己承认（v1.7.0:docs/configuration.md:61「看板中没有这项设置的入口」）。
8. **[已验证] Go 参考的「启用」是 catalog 抽取时硬过滤官方 `enable=false`**（models.go:237-241），显示名→内部 key 用 `ModelMap`（33-47、169-171），未知模型 400 并列出支持项（273-299）。它**没有**模型管理后台（`/admin/api/models` 只读，admin.go:1053-1066），Key 只有 id/key/note/created_at（store/store.go:25-31），无 per-key 模型授权。
9. **推荐方案 A**：统一模型注册表（settings overlay）+ 单点闸门归一 + `/v1/models` 按 Key/启用过滤 + 面板「模型矩阵」+ 机器可读错误码，**6.5-8 人日**；**方案 B**（最小补丁：修保存丢字段 + 加 UI + 别名归一 + 过滤）**约 1.5-2 人日**。不建议照抄 Go 的 enable 硬过滤（理由见 §2.3）。
10. **接口治理 P0 只有两件**：修「面板保存丢字段」与「模型管理面」。`/usage/by-key`（6518-6531）、`/logs`、`/health`/`/metrics` 等观测面已经存在，缺的是接进 UI，不是重造。

## 1. 现状证据

### 1.1 settings 层：数据结构与语义

```python
# qoder_settings.py:221
REALMS = ("", "intl", "cn")

# qoder_settings.py:234-244（_clean_key_entry：白名单式重建，漏字段即静默丢弃）
return {
    "id": str(entry.get("id") or secrets.token_hex(6)),
    "name": str(entry.get("name") or "").strip() or "未命名",
    "key": key,
    "realm": realm,
    "enabled": entry.get("enabled", True) is not False,
    "created_at": entry.get("created_at") or time.strftime("%Y/%m/%d %H:%M"),
    "models": _clean_model_patterns(entry.get("models")),
}
```

| 能力 | 现状 | 证据 |
|---|---|---|
| Key 唯一标识 | `id`（面板生成 `k0/k1` 或 `token_hex(6)`） | qoder_settings.py:235、qoder_proxy.py:6704-6705 |
| Key 名称 | `name`，默认「未命名」 | qoder_settings.py:236 |
| Key 备注 | **无**（上游 v1.7.0 加了 `note`，截断 120 字符） | 本地 234-244 无 note；v1.7.0:qoder_settings.py:176 |
| 启用/停用 | `enabled`，`is not False`（缺省=True） | qoder_settings.py:239 |
| 出口绑定 | `realm ∈ {"", intl, cn}`，非法回落 "" | qoder_settings.py:231-233 |
| 每 Key 模型白名单 | `models` pattern 列表，空=不限制 | qoder_settings.py:243、365-379 |
| 自定义额度/配额 | **无**（limits 是**账号级**，不是 Key 级） | qoder_settings.py:446-451 `LIMIT_KEYS` 全为账号护栏 |
| 过期时间 | **无** | 同上 |
| 全局封禁 | `banned_models`（fnmatch，默认空） | qoder_settings.py:382-396 |
| 全局启用白名单 | **无此概念** | 全文无 `enabled_models`/`allow_models` |

匹配语义（`key_allows_model`，qoder_settings.py:365-379）：pattern 在清洗时统一 lower（346-362），匹配用 `fnmatch.fnmatchcase`；受限 Key + 空模型名 = **拒绝**（379 行前的 376-378）。

### 1.2 catalog 层：清单从哪来、enable 表示什么

三源优先级（qoder_proxy.py:2231-2244，`fetch_models`）：

1. 动态接口 `/algo/api/v2/model/list`（`read_dynamic_models`，2247-2297，需要账号、COSY 签名、body=`qoder_encode("{}")`）
2. 本机官方客户端目录缓存 `~/.qoder[.cn]/.models/<uid>/catalog-v6`（`read_local_models`，2187-2228，QMC 解密）
3. 仓库内 `qoder_catalog_{intl,cn}.json` → 缺失回落内嵌冻结副本（qoder_catalog.py:1519-1553）

```python
# qoder_proxy.py:2135-2184 merge_catalog 文档串（2140）
"""... enable/strategies 等状态不做任何裁剪。"""
# qoder_proxy.py:2146-2149：静态清单全量入 map，只按 is_chat_model 过滤
for item in source_static:
    mid = item.get("key") or item.get("id")
    if mid and is_chat_model(mid):
        merged[mid] = dict(item)
```

- 结果缓存在内存 5 分钟（2234-2236），**没有磁盘缓存**（上游 #58 才有）。
- `is_chat_model`（2119-2128）排除 `lite`、`codewise-`、`completion-`、`-image-alpha` 等非对话条目。
- `enable` 的实测取值（`qoder_catalog_intl.json` / `qoder_catalog_cn.json` 实测计数）：intl 17 条里只有 `qmodel_38max`/`qfmodel` 为 true（内嵌副本对应 qoder_catalog.py:317、378），其余 15 条全 false；cn 14 条**全为 true**。**[推断]** `enable` 是「该账号/套餐当前是否开放」的官方判定，随账号与套餐变化，不是稳定的模型属性。

### 1.3 三入口闸门接线

```python
# qoder_proxy.py:6240-6249
def _model_precheck(self, model, realm):
    """顺序：跨区错配[既有] -> 全局封禁[P1-4] -> Key 白名单[P1-4]。"""
    return (self._cross_realm_error(model, realm)
            or self._banned_model_error(model)
            or self._key_model_error(model))
```

| 入口 | 调用点 | realm 来源 | 拒绝后 |
|---|---|---|---|
| Chat `/v1/chat/completions` | qoder_proxy.py:7608 | `self._request_realm() or CURRENT_REALM`（7606） | `400 invalid_request_error`（7610） |
| Responses `/v1/responses` | qoder_proxy.py:7375 | 同上（7373） | `400`（7377） |
| Anthropic `/v1/messages` | qoder_proxy.py:7248 | 同上（7234） | `400`（7250） |

拒绝文案：`banned_model_message`（80-84）、`key_model_message`（87-94）、`_cross_realm_error`（6207-6219，文案含 Key 名称与「改用对应出口的 Key」指引）。

闸门之后才做归一：`open_upstream` → `model_key = qoder_catalog.resolve_upstream_key(model, realm=realm)`（4388）→ `build_qoder_body` 用 `model_key` 查 `models_for_realm` 拿 catalog 元数据（3961-3983）。

### 1.4 `/v1/models` 到底返回什么

```python
# qoder_proxy.py:6414-6430
if path in ("/v1/models", "/models"):
    if not self._authorized():
        return
    req_realm = self._request_realm() or CURRENT_REALM
    entries = fetch_models(realm=req_realm)
    data = [model_entry(mid, meta) for mid, meta in entries]
    return self._json(200, {"object": "list", "data": data,
                            "realm": req_realm or CURRENT_REALM,
                            "catalog_source": catalog_source})
```

- **不按 Key 过滤**：这里没有 `self.key_entry` 参与；`key_allows_model` 只在 6236 被调用。
- **不按 `enabled` 过滤**：`model_entry` 只把 `enable` 变成 `item["enabled"]` 并附 `disabled_reason`（2365、2391-2401）。
- **认证**：`_authorized` 允 API Key 或面板会话（6186-6193 的 `_key_ok` 在面板会话下直接 True），因此看板能用 `/v1/models` 渲染模型库（dashboard.html:1124）。
- **字段**（model_entry，2341-2545）：`id`=官方 display_name（2358-2367）、`upstream_key`、`aliases`（2385-2389）、`capabilities`/`supports_*`/`abilities`/`architecture`（2402-2427）、`context_length`/`context_windows`/`context_window_default`（2444-2450）、`max_output_tokens`（仅官方给才给，2451-2456）、`reasoning_efforts`/`reasoning_default_effort`/`reasoning_can_disable`（2457-2470）、`price_factor`/`price_factor_peak`/`price_factor_valley`/`off_peak`/`off_peak_active_now`（2481-2534）、`is_new`/`is_free`/`icon`（2535-2544）。

### 1.5 看板：只有「展示」，没有「管理」

| 区块 | 位置 | 能做什么 | 不能做什么 |
|---|---|---|---|
| 模型库与能力清单 | dashboard.html:385-405（表头）、1136-1300（渲染） | 展示官方模型名/介绍/峰谷价/能力/上下文/档位；`enabled=false` 显示官方原因（1268-1273） | 不能启用/停用、不能改显示名、不能按 Key 授权 |
| API Key 管理 | dashboard.html:594-605、2959-3038 | 增删改启停、随机生成、复制、出口绑定 | **无模型白名单字段**；编辑卡只有 name/realm/key（2977-2995） |
| 保存 Key | dashboard.html:3182-3208 | `postJSON('/settings/save', {api_keys: payload})` | payload 只有 id/name/realm/enabled/key（3183-3190）→ 后端把 models 清空 |
| 限额护栏 | dashboard.html:607-629、3210-3327 | 5 个账号级护栏 × 3 作用域 | 与模型启用无关；无 Key 级额度 |
| 页签 | dashboard.html:3329-3359 | gateway / analytics / logs / settings 四页 | 无「模型」页 |

### 1.6 测试覆盖

- `tests/_test_model_gates.py`：配置层 S1-S5c（43-82），接线层 G1/G2/G3/G4/G7/G8（167-208）。断言「两层计数为 0」（`open_upstream` 桩 + `urlopen` 桩，107-128）。
- **未覆盖**：`/v1/responses` 入口（只有 G8 覆盖 `/v1/messages`）；别名/显示名写法；`/v1/models` 是否过滤；面板保存往返（`models` 是否被保留）；`banned_models` 与白名单同时命中的优先级。
- 上游 v1.7.0 的同名套件只有指纹头差异（`git diff HEAD v1.7.0 -- tests/_test_model_gates.py` 仅新增 `declare_suite`），**检查数同为 17**——这块上游也没补。

### 1.7 缺口清单（「现在到底缺什么」）

| # | 缺口 | 证据 | 影响 | 严重度 |
|---|---|---|---|---|
| G-1 | 无「全局启用白名单」，只有黑名单 | qoder_settings.py:382-396 | 无法「只开放 3 个模型给外部」 | 高 |
| G-2 | 无模型管理 UI（含 per-key 授权） | dashboard.html:594-605、385-405 | 只能手改 settings.json | 高 |
| G-3 | 面板保存 Key 静默清空白名单 | dashboard.html:3183-3190 + qoder_proxy.py:6732 + qoder_settings.py:243 | **数据丢失**，且用户无感知 | 高（bug） |
| G-4 | `GET /settings` 不回显 `models`/`banned_models` | qoder_proxy.py:1868-1904 | 即使手改配置，UI 也看不到 | 高 |
| G-5 | 白名单/封禁不做别名归一 | qoder_proxy.py:6227-6238 vs qoder_catalog.py:1636 | 同模型不同写法判定不同 → 误拒 | 高 |
| G-6 | `/v1/models` 不按 Key/启用过滤 | qoder_proxy.py:6414-6430 | 客户端下拉里全是不可用模型 | 中高 |
| G-7 | 错误码不可机读（`code` 是数字） | qoder_proxy.py:6054-6056 | 编排层只能靠中文文案判断 | 中 |
| G-8 | 显示名/别名不可配 | qoder_catalog.py:1591-1619（硬编码） | 想给外部起名（如 `gpt-4o`）只能改代码 | 中 |
| G-9 | catalog 无手工覆盖 | qoder_proxy.py:2135-2184 | 只能改 JSON/等官方刷新，且官方刷新会覆盖 | 中 |
| G-10 | 无模型级 quota/开关以外的策略 | qoder_settings.py:446-451（账号级） | 「这个 Key 一天最多 100 次 qwen-max」做不到 | 中 |
| G-11 | `/v1/models` 暴露 `upstream_key` | qoder_proxy.py:2364 | 内部命名外泄（不影响功能） | 低 |
| G-12 | 无 `/models/refresh`、无磁盘缓存 | 对比 v1.7.0:8633/2423 | 容器重启回落到陈旧快照 | 中 |

`known_client_names()`（qoder_catalog.py:1692-1701）是**死代码**：全仓仅定义、无引用（`grep -rn known_client_names` 只命中定义处）。

## 2. 机制拆解（上游/官方到底怎么判，为什么现在这样做会/不会出问题）

### 2.1 为什么白名单会「误拒」：闸门早于归一

请求进入 handler 时 `model` 是客户端原样字符串。`_model_precheck`（6240-6249）直接把它交给：

```python
# qoder_proxy.py:6227-6238
def _key_model_error(self, model):
    entry = getattr(self, "key_entry", None)
    if not entry:
        return ""
    if qoder_settings.key_allows_model(entry, model):
        return ""
    return key_model_message(entry, model)

# qoder_settings.py:365-379
patterns = _clean_model_patterns((entry or {}).get("models"))
if not patterns: return True
name = str(model or "").strip().lower()      # 只 lower，不解析别名
return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
```

而别名归一发生在 `open_upstream` 内部（4388）。因此：

| 管理员配 | 客户端写 | 现在的结果 | 期望 |
|---|---|---|---|
| `qmodel_38max` | `Qwen3.8-Max` | **拒绝** | 放行（catalog 认定等价） |
| `qwen3.8-max` | `qmodel_38max (Qwen3.8-Max)` | **拒绝** | 放行 |
| `qwen*` | `Qwen3.8-Max` | 放行（lower 后前缀命中） | 放行（碰巧对） |
| `qmodel_38max` | `glm-5.3` | 拒绝 | 拒绝 |

**[推断]** 这是当前最容易被用户报成「白名单不生效」的原因；通配 `qwen*` 能命中纯属大小写/前缀巧合。

### 2.2 为什么面板保存会清空白名单：三段链路都「按白名单重建」

1. 读：`GET /settings` → `runtime_settings_view()`（1868-1904），key 条目只映射 id/name/realm/enabled/masked/source/created_at（1878-1887），**`models` 不出现**。
2. 写：看板 `loadSettings` 用上述字段重建 `API_KEY_ROWS`（dashboard.html:2890-2902，无 models）→ `saveApiKeys` 组 payload（3183-3190，无 models）→ `POST /settings/save`。
3. 落盘：`_handle_settings_save` 逐行重建 `cleaned`，`"models": item.get("models")`（6732）→ `None`；`set_api_keys` → `_clean_key_entry`（qoder_settings.py:243）→ `_clean_model_patterns(None)` → `[]`（346-362 的 else 分支）。

结论：**任何一次「在看板上点保存 Key / 启停 Key / 删除 Key」都会清掉所有 Key 的白名单**（toggleKeyRow/removeKeyRow 也走 `saveApiKeys`，见 dashboard.html:3196-3198 注释）。上游 v1.7.0 的链路逐字相同（v1.7.0:2077-2106 / 4502-4510 / 9144），所以**升级不能修掉它**。

补充证据（MODEL_ALIASES 之外的坑）：白名单是对「客户端名」生效，而 `/v1/models` 推荐的 `id` 是官方 display_name（2360），两者天然不一致。

### 2.3 官方 `enable` 到底能不能当启用开关用

- 官方语义是「该账号/套餐此刻是否开放」，随账号、套餐、区域、客户端版本漂移；快照里 intl 只有 2/15 为 true（qoder_catalog.py:59-884 中 `"enable": true` 仅 317 与 378）。
- 本地当前策略是**展示不拦截**（2140 注释明说「不做任何裁剪」），请求照样发上游，由上游判。
- Go 参考的策略相反：抽取 catalog 时 `if hasEnable && !enableFlag(enableVal) { continue }`（models.go:237-241）——不在 ModelMap 里的模型连 `/v1/models` 都不出现，且请求会在 `ResolveModel` 处 400（models.go:273-289）。
- **[推断]** 两种策略都能自洽，但把官方 `enable` 直接当本地启用开关，会把「换了个免费账号 → 一半模型消失」变成网关行为，且与双区路由叠加后非常难解释。**建议：官方 enable 只做「信号」**（默认值/提示徽标），本地启用由管理员注册表决定；如需复用官方判定，加显式开关 `registry.follow_official_enable=true`（默认 false）。

### 2.4 升级上游后闸门语义会变（必须先定契约）

```python
# 本地 7606
req_realm = self._request_realm() or CURRENT_REALM
# 上游 v1.7.0 10444（#77 之后）
req_realm = self._request_realm()          # 可能为 None
```

- 本地：未绑定出口时钉在当前区 → `_cross_realm_error(model, realm)` 生效（6207-6219 给出「该模型只在 X 区」的中文指引）。
- 上游：返回 None → `_cross_realm_error` 直接返回 ""（6209-6210 的 `if not realm or not model: return ""`），改由 `detect_model_realm` + `auto_realm_for_model` 自动换区（v1.7.0:186-210），并把结果写进 `X-Qoder-Realm`/`X-Qoder-Realm-Reason`（v1.7.0:8153-8156）。
- **[已验证]** 两边的 `_model_precheck` 代码逐字相同（本地 6240-6249 / 上游 8405-8414），差异只在**传入的 realm 是否非空**。
- 影响：合流后「跨区错配」不再报错而是静默换区。若我们要在注册表里表达「某模型只授权给 cn 出口的 Key」，不能只靠 realm 参数，必须在闸门里显式算 `effective_realm`（见 §5.2）。

### 2.5 未知模型：本地原样下发 vs Go 的 400

```python
# qoder_catalog.py:1681-1689
if keys and m_key in {x["key"] for x in STATIC_INTL_MODELS + STATIC_CN_MODELS}:
    return m_key          # 官方 key 但非本区域
...
return m_key              # 解析不出来：原样返回
```

于是 `model="gpt-4o"` 会带着 `model_config.key="gpt-4o"` 打到上游（3972-3983），由上游报错（通常是 4xx/业务错误），客户端看到的是上游文案而不是「模型不存在」。Go 的 `ResolveModel` 则直接返回 `UnsupportedModelError`，handler 转 400 并列出支持项（models.go:273-299、bridge/handler.go:396-402）。**[推断]** 对外多租户场景应选 Go 口径（本地拒绝、不发上游、可机读），否则每个错模型都消耗一次上游请求。

### 2.6 为什么启用状态必须做成 overlay，不能写进 catalog

- catalog 有三个来源且 5 分钟内存缓存（2231-2244），官方动态接口随时会**重新覆盖**字段（2156-2161 只做「非 None 增量覆盖」）。
- 若把 `enabled` 写进 `qoder_catalog_intl.json`，一次 `_refresh_catalog.py` 或动态拉取就丢。
- 因此：**官方 catalog 只读**，管理员配置单独存 `settings.json`，读取时 overlay（§5.1）。这也是上游 `models-cache-<realm>.json`（v1.7.0:2423）能安全存在的原因——它只缓存官方数据，不承载管理意图。

## 3. 上游 v1.7.0 与 Go 参考实现对比

| 维度 | 我们（main abb38c8） | 上游 v1.7.0 | Go 参考 | 结论 |
|---|---|---|---|---|
| 模型清单来源 | 动态 → 本机缓存 → 仓库 JSON/内嵌（qoder_proxy.py:2237-2241） | 动态 → **磁盘缓存 7 天** → 本机缓存 → 内置（v1.7.0:2455-2475、docs/models.md:14-20） | `DefaultModelMap` 回退 + `ExtractCatalog` 动态（models.go:33-47、217-269） | **拿上游**：#58 的磁盘缓存 + `/models/refresh` |
| 刷新入口 | 无（只能重启/等 5 分钟） | `GET /models/refresh`（v1.7.0:8633，docs/api.md:20） | 无 | 拿上游（P1） |
| 双区并集视图 | 无（只能 `?realm=` 单区） | `?merge_realms=1`（v1.7.0:8664、CHANGELOG:698） | 无 | 拿上游（P1，面板做矩阵要它） |
| 区域标注 | 无 | `realm` / `realm_exclusive`（v1.7.0:2586+、qoder_catalog.py:1895） | `RegionFilter` 前缀路由 `cn/ gl/ auto/`（route.go:52-79） | 两种都要：字段 + 前缀（P1） |
| 自动换区 | 无（未绑定即当前区，7606） | 清单驱动 + 账号驱动，带 `X-Qoder-Realm`（v1.7.0:174-230、8153） | 路由器按 region 过滤账号（routing.go:regionBase） | 合流即得；**闸门要适配 realm=None** |
| 全局启用白名单 | 无 | **无** | 无（只有官方 enable 硬过滤） | **我们要新做** |
| 全局封禁 | `banned_models`（qoder_settings.py:382-396） | 同（v1.7.0:331-345） | 无 | 保留，纳入注册表体系 |
| 每 Key 模型白名单 | 有后端、无 UI、保存即清空 | 同，**且有同一 bug** | 无 | 我们要新做（上游修不了） |
| 白名单匹配口径 | 原始字符串 + fnmatch（qoder_settings.py:365-379） | 同 | N/A | **我们要修**（归一后再匹配） |
| Key 备注 | 无 | `note`（v1.7.0:176，issue #69） | `Note`（store.go:29） | 拿上游（P1） |
| Key 额度/过期 | 无 | 无 | 无 | 自研（P2） |
| `/v1/models` 过滤 | 不过滤（6414-6430） | 不过滤（v1.7.0:8656-8680） | 只列 enable 通过的（models.go:302-319） | **我们要加**（按 Key/启用） |
| `/v1/models` 字段 | 丰富（能力/窗口/档位/峰谷价） | 同 + realm/realm_exclusive | 只有 id/object/created/owned_by（models.go:306-313） | 保持我们的丰富度 |
| 未知模型 | 原样下发上游 | 同 | 400 + Supported 列表（handler.go:396-402） | 改成本地 4xx（P1） |
| 错误码 | 400 + 数字 code | 同 | 400 + `invalid_request_error` 字符串（handler.go:399-401） | 我们要加字符串 code |
| 管理面（UI） | 无 | **无**（docs/configuration.md:61 自认） | 只读列表（admin.go:1053-1066），模型页并入统计页（stats.html:93-133） | **空白区 = 我们的差异化** |
| 官方 enable 使用 | 仅展示（2365） | 仅展示（docs/models.md:23） | **硬过滤**（models.go:237-241） | 不照抄；可选跟随开关 |
| 模型文案来源 | 仅 Windows 客户端路径（qoder_catalog.py:1722-1736） | `QD_OFFICIAL_TEXT` + Linux/容器路径 + 内嵌快照刷新（v1.7.0:qoder_catalog.py:1755-1843） | 无（模型名硬编码） | **拿上游**（容器部署必需） |
| 模型级配额 | 无 | 无 | 无 | P2 |
| 计费视图 | 无 `/pricing` | `/pricing` + `qoder_pricing.py`（v1.7.0:8692-8720） | stats 页有消耗列（stats.html:146） | 拿上游（P1） |


## 4. 方案选项

### 方案 A（推荐）：注册表 overlay + 单点闸门 + 过滤 + 模型矩阵

**思路**：官方 catalog 保持只读事实源；新增 `settings.json#model_registry` 作为**管理意图**；二者在读取时合成「有效模型视图」；闸门与 `/v1/models` 都只读这个视图。

注册表字段建议：

```jsonc
{
  "model_registry_version": 1,
  "model_registry": [
    {
      "qoder_key": "qmodel_38max",             // 必填；身份键（= 官方 catalog key）
      "display_name": "Qwen3.8-Max",           // 对外 id，默认取官方 display_name
      "aliases": ["qwen3.8-max", "qwen-max"],  // 额外别名（用户可加，小写匹配）
      "enabled": true,                         // 全局启用；false = 本地拒绝且不列出
      "realms": ["cn", "intl"],                // 可用区；缺省 = 跟随 key_realm_span
      "max_output": 32000,                     // 可选覆盖（官方无此字段时不编造，仅管理员给）
      "context_window": 200000,                // 可选覆盖
      "tags": ["free", "off-peak"],            // 展示/批量筛选用，不参与判定
      "note": "对外主推"
    }
  ],
  "banned_models": ["kmodel", "gmodel"]        // 保留旧键，语义=永不可用（优先于 enabled）
}
```

闸门求值顺序（**与任务建议一致**，并给出与现状的差异）：

| 步骤 | 判定 | 失败返回 | 依据 |
|---|---|---|---|
| 0 | 归一：`canonical = resolve(model)`（官方 key / display_name / 「key (Name)」/ MODEL_ALIASES / registry.aliases，大小写不敏感）；**解析不出仍返回原串**（不引入新的拒绝面） | — | qoder_catalog.py:1636-1689 扩展 |
| 1 | 注册表 `enabled=false` | `404 model_not_found` + `code="model_disabled"` | 新 |
| 2 | `banned_models` 命中（canonical **或** 原始名任一命中） | `403 permission_error` + `code="model_banned"` | 扩展现有 382-396 |
| 3 | per-key 白名单（`key_allows_model` 改为对 canonical + 原始名双试） | `403 permission_error` + `code="model_not_allowed"` | 扩展 365-379 |
| 4 | realm 可用性（registry.realms / `key_realm_span` / 独占表） | `400 invalid_request_error` + `code="model_realm_mismatch"`（保留现有中文指引） | 扩展 6207-6219 |
| 5 | 放行 → open_upstream | — | — |

与现状的两处差异必须明说：

- 现状顺序是「realm → banned → key」（6247-6249）。**改成 banned → key → realm** 后，`_test_model_gates.py` 的 G2（Key 白名单外且无 realm 冲突）不受影响；但「同时跨区且不在白名单」的文案会变。**[已验证]** 现有 17 个 check 里没有该组合，改动不破测试；合流后需重跑。
- 状态码从 400 改成 403/404：**会破坏 G1/G2 现有的 `code == 400` 断言**（_test_model_gates.py:171-180），必须同批改测试。

`/v1/models` 输出规范：

- 默认视图 = 本区 × 注册表 enabled × 该 Key 白名单 三者交集；官方 `enable=false` 的模型仍保留（沿用现状），但追加 `available_locally` 字段区分「官方不可用」与「本网关禁用」。
- `?all=1`（面板专用）：跳过 Key/启用过滤，供模型矩阵渲染；`?include_disabled=1` 只跳过启用过滤。
- 保留 `upstream_key`/`aliases`（面板与 _verify_models.py 依赖），但 `?public=1` 下省略 `upstream_key`。**[未验证假设]** 是否有客户端会因此改变行为——推测无，OpenAI 客户端忽略未知字段。
- 顶层新增 `registry_version`，与 `catalog_source`（6424-6430）并列。

面板「模型矩阵」交互：

| 区域 | 元素 | 行为 |
|---|---|---|
| 设置页新 section「模型开放矩阵」 | 表格行 = canonical key | 开关（enabled）、对外 id 输入框、别名输入框、区多选、能力徽标（只读）、官方原因（只读） |
| 顶部工具条 | 全选启用 / 全选停用 / 仅显示本区 / 按 tag 筛选 / 搜索 | 批量写回，**单次 POST 提交全部行**（幂等覆盖） |
| 行内「授权 Key」 | 展开后勾选各 Key（读 `/settings` 的 key 列表） | 写回 `api_keys[].models` 或 `model_registry[].keys`（二选一，见 §7-D3） |
| 保存 | 复用 `POST /settings/save`，新增分片键 `model_registry` | 与 `api_keys` **分开提交**，避免整表重建 |

成本：约 6.5-8 人日（§8）。风险：闸门顺序/状态码变更影响现有客户端与测试；面板改动集中在 dashboard.html（194KB 单文件，需与上游 +2279 行合并）。

### 方案 B（最小补丁，约 1.5-2 人日）：只修致命的，不做注册表

1. 修「保存清空白名单」：`runtime_settings_view` 回传 `models`/`note`，`saveApiKeys` payload 带上 `models`，后端保存改为「缺键 = 保留原值」而不是 None。
2. 白名单/封禁归一：`_model_precheck` 里先 `resolve_upstream_key` 再匹配（canonical + 原始名双试），不改顺序、不改状态码。
3. `/v1/models` 增加 `?all=1` / 按 Key 过滤（默认行为不变）。
4. 面板：Key 编辑卡加一个「模型白名单」文本框（逗号分隔），不新建模型页。

收益：堵住数据丢失 + 消除误拒，成本极低。代价：仍无「全局启用列表」，管理粒度只有黑名单。

### 方案 C（不推荐）：只写文档 / 外部管理

保持代码不动，把手改 `settings.json` + `POST /settings/save` 写进文档（上游就是这条路，v1.7.0:docs/configuration.md:59-65）。
收益：0 开发。风险：「保存即清空」的 bug 仍在，手改配置会在下一次面板保存时被抹掉——**这条路实际上不可用**，除非同时做 B-1。

### 推荐

先做 **B 的前两条（≤1 人日）止血**，再做 **A 的 W1/W2/W3/W5**（注册表、闸门、过滤、保存语义），最后做 W4 面板矩阵；C 只作为文档补充。

## 5. 落地改动清单（文件级 + 补丁草案 + 开关/回滚）

> 以下均为**草案**，本任务未改任何源码。

### 5.1 qoder_settings.py（+约 120 行）

- 新增常量 `MODEL_REGISTRY_KEY = "model_registry"`、`MODEL_REGISTRY_VERSION = 1`、`REGISTRY_FIELDS`。
- 新增 `model_registry(accounts_dir) -> list[dict]`：读 + 清洗（`qoder_key` 必填、别名 lower 去重、`realms` 收敛到 `("", "intl", "cn")` 子集、`enabled` 缺省 True）；**未配置时返回 []（= 全部跟随官方，零行为变化）**。
- 新增 `set_model_registry(accounts_dir, rows)`：整表覆盖 + 版本号；风格照抄 `set_banned_models`（`with _lock` + `save`）。
- 改 `key_allows_model(entry, model, canonical=None)`：第三参可选（向后兼容），匹配集合 = {`model`(lower), `canonical`(lower)}。
- `_clean_key_entry` 不变（已是白名单重建）；**但 /settings/save 的入口要改成 patch 语义**（见 5.3）。
- 迁移：`load()` 后若无 `model_registry`，不写盘、不补默认值（避免把「跟随官方」固化成一份会过期的清单）。

### 5.2 qoder_proxy.py（+约 200 行，-约 40 行）

新增统一解析/闸门（替换 `_cross_realm_error` / `_banned_model_error` / `_key_model_error` / `_model_precheck` 四个方法的实现，保留旧方法名做薄壳以兼容测试）：

```python
# 草案（未落地）
def model_registry_index():                 # 5 分钟缓存，随 settings mtime 失效
    rows = qoder_settings.model_registry(ACCOUNTS_DIR)
    return {r["qoder_key"]: r for r in rows}

def effective_models(realm, key_entry=None, include_disabled=False):
    """官方 catalog x 注册表 x Key 白名单 -> [(key, meta, row)]"""
    idx = model_registry_index()
    for mid, meta in fetch_models(realm=realm):     # 官方清单（只读）
        row = idx.get(mid) or {}
        # 封禁用 fnmatch（is_model_banned），不能拿 set 做精确匹配
        enabled = row.get("enabled", True) and not is_model_banned(mid)
        if not enabled and not include_disabled:
            continue
        if key_entry and not qoder_settings.key_allows_model(
                key_entry, meta.get("display_name") or mid, canonical=mid):
            continue
        yield mid, meta, row

def model_gate(self, model, realm):
    """-> (status, code, message) 或 None；三入口共用"""
    canonical = qoder_catalog.resolve_upstream_key(model, realm=realm)
    row = model_registry_index().get(canonical) or {}
    if row.get("enabled") is False:
        return 404, "model_disabled", "模型 %s 已被本网关停用" % model
    if is_model_banned(canonical) or is_model_banned(model):
        return 403, "model_banned", banned_model_message(model)
    if not qoder_settings.key_allows_model(self.key_entry, model, canonical=canonical):
        return 403, "model_not_allowed", key_model_message(self.key_entry, model)
    if not realm_ok(canonical, realm, self.key_entry):
        return 400, "model_realm_mismatch", self._cross_realm_error(model, realm)
    return None
```

三处调用点（7608 / 7375 / 7248）改为：

```python
gate = self.model_gate(model, req_realm)
if gate:
    status, code, msg = gate
    return self._error(status, msg, "invalid_request_error", err_code=code)
```

`_error` 扩展（6054-6056）：`def _error(self, code, message, err_type="server_error", err_code=None)`，body 里 `"code": err_code or code`（默认仍是数字，零回归）。

`/v1/models`（6414-6430）改为走 `effective_models`，支持 `?all=1` / `?include_disabled=1`；保留 `catalog_source`。

### 5.3 保存链路（必须与 5.2 同批，否则 UI 仍会丢字段）

| 文件 | 改动 | 关键点 |
|---|---|---|
| qoder_proxy.py:1878-1887 | key 视图补 `models`、`note` | 只读回显，不泄露 key 明文 |
| qoder_proxy.py:1888-1904 | 顶层补 `banned_models`、`model_registry`、`model_registry_version` | 面板据此渲染矩阵 |
| qoder_proxy.py:6688-6735 | api_keys 保存**逐字段 patch**：payload 缺 `models`/`note` 键 → 沿用 `existing[id]` 的值；缺 `enabled` → 沿用 | 与 limits 的「先校验后落盘」一致（6760-6776） |
| dashboard.html:2890-2902 | `API_KEY_ROWS` 增加 `models`/`note` | 与后端字段同名 |
| dashboard.html:3183-3190 | payload 带 `models`/`note` | 直击本次 bug |
| dashboard.html:594-605 | 新增「模型开放矩阵」section | 复用现有 `section` 卡片样式 |
| dashboard.html:2977-2995 | Key 编辑卡加「模型白名单」输入（逗号分隔，占位「空 = 不限制，支持 qwen* 通配」） | 与矩阵双向同步 |

### 5.4 错误语义（对外契约）

| 场景 | HTTP | `error.type` | `error.code` | 依据 |
|---|---|---|---|---|
| 注册表停用 | 404 | `invalid_request_error` | `model_disabled` | OpenAI 语义（模型不存在） |
| 全局封禁 | 403 | `permission_error` | `model_banned` | 管理意图明确，可披露 |
| Key 白名单外 | 403 | `permission_error` | `model_not_allowed` | 同 |
| 出口不匹配 | 400 | `invalid_request_error` | `model_realm_mismatch` | 保留现有中文指引 |
| 未知模型（可选） | 404 | `invalid_request_error` | `model_not_found` | Go 口径（handler.go:396-402） |

**[推断]** 改成 403/404 后，任何按 400 写死分支的上层编排会受影响——这是需要用户拍板的兼容性取舍（§7-D4）。

### 5.5 开关与回滚

- 环境开关 `QD_MODEL_REGISTRY=0`：`model_registry()` 直接返回 []，闸门退化为「banned + 白名单（归一后）」= 方案 B 行为。
- settings 版本键：`model_registry_version` 缺失 = 视为未启用注册表；写入即 1，回滚只需删键（不删数据）。
- 代码回滚：新增函数与旧方法并存，`model_gate` 单点切换；旧 `_model_precheck` 保留为一行 delegate，保证合流/cherry-pick 时冲突面最小。


## 6. 验证方法

### 6.1 单测扩展（tests/_test_model_gates.py，17 → 约 40 checks）

| 组 | 用例 | 断言 |
|---|---|---|
| G9 | 三入口 × 注册表停用 | chat / responses / anthropic 均 `404` + `code=model_disabled` + 两层计数 0 |
| G10 | 三入口 × 白名单外 | 均 `403` + `code=model_not_allowed` |
| G11 | 别名矩阵 4 写法 | `Qwen3.8-Max` / `qwen3.8-max` / `qmodel_38max` / `qmodel_38max (Qwen3.8-Max)` 判定一致 |
| G12 | 优先级 | 「停用 + 白名单外」→ 404；「封禁 + 白名单内」→ 403 |
| G13 | realm | 注册表 `realms=["intl"]` + 绑定 cn 的 Key → 400 `code=mismatch`；未绑定则按上游 auto（合流后）或当前区 |
| G14 | `/v1/models` | 带白名单 Key → 只返回允许项；`?all=1` → 全量；`?include_disabled=1` → 含停用 |
| G15 | 面板保存往返 | `POST /settings/save {api_keys:[{id,name,realm,enabled,key:""}]}` 后 `models`/`note` 仍在 |
| G16 | 迁移 | 无 `model_registry` 的 settings.json → 行为与改动前逐字一致（沿用 G4 的对照组风格） |

现有 17 个 check 的映射：S1-S5c（43-82）不动；G1/G2 只改期望状态码；G3/G4/G7 不动；G8 扩成三入口（202-207）。

### 6.2 面板 JS 测试（沿用 tests/_test_dashboard_*.js 模式）

- `renderModelMatrix` 快照：行数 = `/v1/models?all=1` 条数；停用行带 `disabled` 类。
- `saveApiKeys` payload 断言：含 `models` 字段（防回归，直击本次 bug）。

### 6.3 对比实验（人工，一次性）

```bash
# 1) 复现「保存清空白名单」：临时 accounts 目录 + 手写 settings.json
printf '%s' '{"api_keys":[{"id":"k1","name":"t","key":"abcd1234","models":["qwen*"]}]}' > /tmp/qd-acc/settings.json
# 面板登录后随便点一次「保存 Key」，再读回：
curl -s -H "X-Panel-Token: TOKEN" localhost:8790/settings | python -m json.tool

# 2) 注册表生效验证（过滤前后）
curl -s "localhost:8790/v1/models?all=1" | python -c 'import json,sys;print(len(json.load(sys.stdin)["data"]))'
curl -s -H 'Authorization: Bearer KEY' localhost:8790/v1/models \
  | python -c 'import json,sys;print([m["id"] for m in json.load(sys.stdin)["data"]])'

# 3) 三入口一致性（同一被停用模型，三个协议都要本地拒绝）
for p in /v1/chat/completions /v1/responses /v1/messages; do
  curl -s -o /dev/null -w "$p %{http_code}\n" -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer KEY' -d '{"model":"DISABLED","messages":[],"max_tokens":1}' \
    "localhost:8790$p"
done
```

### 6.4 观测指标

- 日志：`chat`/`anthropic` 已有 model 行（7602、7242）；闸门拒绝建议加 `tag="gate"`，字段 `model/canonical/code`。
- `/metrics`（6406-6410）加计数器 `qoder_model_gate_rejects_total{code=...}`，区分「权限拒绝」与「配置错误」。
- `/usage/by-key`（6524-6531）已带 models 分桶（1483-1512），矩阵上线后可对照「开放了但没人用」的模型。
- `_verify_models.py`（模型库逐字段校验，见其 docstring 1-30 行）需扩展 `?all=1` 分支，否则过滤后会误报缺模型。

## 7. 风险与未决问题

| # | 问题 | 现状/影响 | 建议 | 需用户拍板 |
|---|---|---|---|---|
| D1 | 白名单匹配口径 | 现状按原始名，误拒 | canonical + 原始名双试（兼容旧配置） | 否（按建议做） |
| D2 | `/v1/models` 是否暴露 `upstream_key` | 现状暴露，面板与 _verify_models.py 依赖 | 默认保留，加 `?public=1` 隐藏 | **是** |
| D3 | per-key 授权存哪：`api_keys[].models`（旧）还是 `model_registry[].keys`（新） | 旧键已在用且会被面板清空 | 保留旧键为唯一事实源；矩阵 UI 直接编辑旧键 | **是**（数据模型） |
| D4 | 停用模型的 HTTP 码 404 / 403 / 400 | 现状一律 400 | 404 + `code`，白名单 403 | **是**（兼容性） |
| D5 | 是否跟随官方 `enable` | 现状只展示不拦截 | 默认不跟随，提供开关 | **是** |
| D6 | 官方 catalog 漂移时注册表怎么办 | 新模型默认出现且启用 | 未知新增默认启用 + 标 NEW；已删除的 key 保留孤儿行不自动删 | **是** |
| D7 | 是否需要模型级 quota | 现在只有账号级 | P2；需先定「按 Key 还是按模型」 | **是** |
| D8 | 合流导致行号/语义漂移 | v1.7.0 改了 `req_realm`（§2.4）、dashboard +2279 行 | 先合流再改，或把补丁做成独立 commit 便于 rebase | **是**（排期） |
| D9 | 是否引入 `/models/refresh` + 磁盘缓存 | 容器重启回落陈旧快照 | 随上游拿（P1） | 否 |
| D10 | `banned_models` 与注册表语义重叠 | 两处都能「关模型」 | banned = 不可覆盖的硬封禁；enabled = 可覆盖的管理开关；矩阵里 banned 行只读并标注 | **是** |

不拍板也会遇到的硬风险：

1. **dashboard.html 是 194KB 单文件**，上游已涨到 5981 行并在 #66 后重排页面（v1.7.0:1226 的 `keyList` 已在独立页）；模型矩阵会与上游前端重构正面冲突。
2. **改状态码必须同批改测试**，否则 tests/run_all.py 门禁会红（现有 20 套件 / 889 checks，见 f14031a 提交信息）。
3. **保存 patch 语义是安全敏感改动**：现有「空 key = 保留原值」依赖 `existing` 字典（6700-6704），新增 `models` patch 时不能顺手把「空 key 视为清空」。
4. **「只做 UI 不修后端 patch 语义」= 假修复**：每次保存仍会清空白名单，这是本次分析里最需要避免的坑。
5. **[未验证假设]** 上游 #87 的排队治理、#72 的 fail-closed 等改动会重排 handler 结构，可能让 §5.2 的补丁行号失效——一切以合流后的 diff 为准。

## 8. 工作量估计（人日）与依赖关系

| 编号 | 工作 | 人日 | 依赖 | 产出 |
|---|---|---:|---|---|
| W1 | settings 注册表读写 + 清洗 + 迁移（5.1） | 1.0 | 无 | 单测 S6-S9 |
| W2 | 闸门重构 `model_gate` + 三入口接线 + `_error` 扩展（5.2/5.4） | 2.0 | W1 | 单测 G9-G13 |
| W3 | `/v1/models` 过滤 + `?all=1` + `registry_version` | 0.5 | W1 | 单测 G14 |
| W4 | 面板模型矩阵 + Key 编辑卡联动（5.3 前端） | 1.5 | W3 | JS 测试 |
| W5 | 保存链路 patch 语义 + 回显（5.3 后端） | 0.5 | W1 | 单测 G15 |
| W6 | _verify_models.py 扩展 + 文档（新建 docs/models.md + 修正 README 冲突文案） | 0.5 | W3 | 文档 |
| W7 | 合流上游 v1.7.0 后回归 + 冲突收口 | 1.0-2.0 | 05 合流策略 | 全绿门禁 |
| **合计** | **方案 A** | **6.5-8.0** | | |
| B1 | 只做 W5 + 别名归一（W2 子集）+ 过滤 | 1.0 | 无 | G11 + G15 |
| B2 | B1 + Key 编辑卡模型文本框 | +0.5 | B1 | — |
| E1 | 接口治理 P0/P1 剩余项（附录） | 1.0 | 无 | — |

依赖关系：

- **强依赖 05（上游合流策略）**：v1.7.0 已改 `req_realm` / `_request_realm` / dashboard 页面结构。先合流再做 W2/W4 可省一次返工；先做则补丁要按「独立 commit + 少碰 dashboard」写。
- **与 Q1（提示词）、Q2（VM 检测）、Q4（标头参数）无耦合**，可并行。
- W1 必须先于 W2/W3/W5；面板（W4）需要 W3 的 `?all=1` 才能渲染全量。

## 附录 · 对外接口治理清单与优先级（回答 E）

| 能力 | 现状 | 证据 | 优先级 | 说明 |
|---|---|---|---|---|
| 健康检查 | `/ping` `/healthz` `/livez` `/readyz` + `/health` + `/metrics` | qoder_proxy.py:6342-6410 | **P0 已完成** | `/readyz` 无就绪账号返 503（6346-6348） |
| API Key CRUD | 增删改启停、随机生成、复制、明文 reveal（默认密码下禁用） | dashboard.html:2959-3038、6615-6632 | **P0 已完成** | 缺 note/过期/额度 |
| Key 备注 | 无（上游有 `note`） | v1.7.0:qoder_settings.py:176 | **P0**（随合流拿） | 纯展示，成本极低 |
| Key 出口绑定 | 有（`realm`） | qoder_settings.py:231-233 | **P0 已完成** | — |
| Key 模型白名单 | 后端有、无 UI、保存即丢 | §1.1 / §2.2 | **P0 修 bug** | 本次核心 |
| 模型开放管理 | 无 | §1.7 G-1/G-2 | **P0** | 本次核心 |
| 机读错误码 | `code` 是数字 | 6054-6056 | **P1** | 与模型闸门同批 |
| `/v1/models` 过滤 | 无 | 6414-6430 | **P1** | 客户端体验 |
| `/models/refresh` + 磁盘缓存 | 无 | v1.7.0:8633/2423 | **P1** | 随合流拿 |
| `?merge_realms=1` + realm 字段 | 无 | v1.7.0:8664/2586 | **P1** | 矩阵需要 |
| per-key 用量视图 | 后端 `/usage/by-key` + 面板「按 Key 用量透视」 | 6518-6531、dashboard.html:536 区块 | **P1 已完成** | 缺「模型 × Key」交叉 |
| 请求日志 | 内存环 + `/logs` / `/logs/export` + 面板流水 | 6580-6614 | **P1 已完成** | 不落盘、重启丢 |
| 计费视图 | 无 `/pricing` | 上游 v1.7.0:8692-8720、qoder_pricing.py | **P2** | 只估算不结算 |
| Key 过期 / 额度 | 无 | — | **P2** | 与账号级 limits 的关系需先定 |
| Key 变更审计 | 无 | — | **P2** | 建议先做 settings 变更审计（谁改了什么） |
| OpenAPI 描述 | 无（`/v1/completions` 明确 404，README:392） | README:392 | **P2** | 手写 5 个路径的 spec 即可 |
| 模型级 quota | 无 | qoder_settings.py:446-451 | **P2** | 依赖 W1 的注册表结构 |

- **P0 判据**：不修会丢数据（G-3），或用户明确要的「管理开放启用的模型」（G-1/G-2）。
- **P1 判据**：影响客户端接入正确性（过滤、错误码、refresh、realm 标注）。
- **P2 判据**：运营/合规增强，不阻塞对外可用。

