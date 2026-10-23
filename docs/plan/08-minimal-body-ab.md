# 08 · 无提示词（QD_MINIMAL_BODY）灰度实测 · 2026-10-23

> 目的：验证去掉官方 Qoder system prompt 后是否影响使用与计费。
> 方法：同镜像 `qoder2api-hub:v1.8.1`、同账号池、独立端口 **8791** 起 canary（`QD_MINIMAL_BODY=1`、调度器关闭、独立 usage 目录），与线上 **8790**（模板兜底）逐条对比同一批请求。
> 证据：线上 usage 行的 `prompt_src`（`none` vs `template`）与响应 usage 帧。

## 0. 结论

1. **不影响基本使用**：聊天、写代码、工具调用、工具结果回传、多轮上下文、拒答（身份/系统提示词）全部正常。
2. **提示词开销真实存在**：同一请求 prompt tokens **~2,660 → ~120（−95%）**；13 条可比请求 credits 合计 **16.62 → 9.63（−42%）**，其中工具类请求差距最大（3.30/3.48 → 0.55/0.56）。
3. **质量未见退化，个别场景更好**：tool-result 用例中，线上（带官方提示词）把工具结果丢了、回了一段身份介绍；minimal 正确读出了文件内容。代码围栏风格略微不稳定（不影响可用性）。
4. **自带 system 的客户端完全不受影响**：Claude Code / Codex 类请求两边 prompt tokens 都是 121，输出一致。
5. 模型自身仍以 Qoder 身份作答、仍拒绝输出系统提示词——身份与安全拒答不全靠提示词。
6. Kimi-K3 的一次 403 是上游 `code 112`（账号套餐/定价权限），重试即 200，**与提示词无关**。

## 1. 数据（同一批请求，prod=模板 / min=无提示词）

| 用例 | prod prompt tok | min prompt tok | prod credits | min credits | 质量 |
|---|---:|---:|---:|---:|---|
| 解释快排 | 2659 | 113 | 0.613 | 0.473 | 等价 |
| 写回文函数 | 2666 | 120 | 0.932 | 0.714 | 等价（min 少了代码围栏） |
| 工具调用（天气） | 2915 | 369 | 3.300 | 0.551 | 等价，均 tool_calls=1 |
| 自带 system | 121 | 121 | 0.352 | 0.416 | 等价（输出长度不同） |
| 问模型身份 | 2660 | 114 | 0.571 | 1.120 | 都拒绝透露；min 输出更长 |
| 要求泄露 system | 2665 | 119 | 0.559 | 0.457 | 都拒绝 |
| 工具结果回传 | 2959 | 413 | 3.479 | 0.563 | **min 正确，prod 丢结果** |
| 多轮记忆 | 2689 | 143 | 0.401 | 0.285 | 等价（都答 42） |
| 数学应用题 | 2689 | 143 | 0.747 | 0.516 | 等价（min 更简洁） |
| 缩进报错 | 2683 | 137 | 0.916 | 0.968 | 等价 |
| 列表推导式 | 2686 | 140 | 0.620 | 0.624 | 等价 |
| TCP 类比 | 2667 | 121 | 1.913 | 0.754 | 等价 |
| 代码 review | 2672 | 126 | 2.214 | 2.187 | 等价（min 结构更清楚） |
| **合计** | — | — | **16.62** | **9.63** | — |

多模型抽查（闭包问题）：DeepSeek-Flash 0.776→0.846、GLM-5.3 0.742→0.782（输出长度差异，prompt 均为 2,665→119）；Kimi-K3 首次 403 为上游 112，重试 200。

## 2. 行为差异清单

| 维度 | 有官方提示词 | 无提示词 | 影响 |
|---|---|---|---|
| 身份 | "我是 Qoder，命令行编程助手" | "我是 Qoder / 我的名称是 Qoder" | 无 |
| 安全策略 | 提示词要求只做防御性安全 | 仅靠模型自身 + 上游审核 | 需留意（本仓用户场景为编码） |
| 工具纪律 | 要求 TodoWrite 优先、专用工具 | 由客户端工具定义驱动 | 对纯 API 客户端更干净 |
| 输出风格 | 代码围栏更稳定 | 偶尔省略围栏 | 轻微 |
| 上下文 | 内置 Windows/日期/工作目录等虚假环境 | 无 | minimal 更真实 |

## 3. 建议

1. **可切线上**：把生产 compose 加 `QD_MINIMAL_BODY=1` 后 `docker compose up -d`，观察 24h（按 `prompt_src` 分组看 credits 与失败率）。
2. **回滚**：删掉该环境变量再 `up -d`。
3. **保留选择权**：自带 system 的客户端本就不受影响；需要"中性助手"人设的可继续用 `--system-prompt`。
4. **观察项**：拒答率、10605 排队、以及是否出现"客户端依赖 Qoder 人设"的反馈；建议同时观察 1–2 个非 Qwen 模型的输出质量。

## 4. 复现

```bash
# canary（已在测试后删除）
docker run -d --name qoder-proxy-minimal --restart=no --network host \
  -e HOST=127.0.0.1 -e PORT=8791 -e API_KEY=... \
  -e QD_MINIMAL_BODY=1 -e QD_SCHEDULER_ENABLED=off \
  -e QD_PROXY_USAGE_DIR=/app/usage-canary \
  -v /opt/qoder2api-hub/accounts:/app/accounts \
  -v /opt/qoder2api-hub/usage-canary:/app/usage-canary \
  -v /opt/qoder2api-hub/umid-home:/app/home \
  --entrypoint python qoder2api-hub:v1.8.1 qoder_proxy.py
```
