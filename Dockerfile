# Qoder Multi-Account Reverse Proxy Gateway (CN + Intl)

# ---------------------------------------------------------------------------
# 构建期：提取 UMID 原生组件（@qoder-ai/qodercli 内嵌的 runtime-info）
# 纯标准库 python 实现（不需要 node）；npm 包只在 builder 阶段存在——31MB 的
# tarball 与下载过程都不会进入最终镜像。提取失败不阻断构建：那种情况下网关
# 退化为 derived 身份（活动列表可能被服务端过滤，见 issue #10），功能仍可用。
# ---------------------------------------------------------------------------
FROM python:3.11-alpine AS umid-builder
ARG TARGETARCH
WORKDIR /build
COPY _install_umid.py ./
RUN apk add --no-cache ca-certificates && \
    arch="x64"; [ "$TARGETARCH" = "arm64" ] && arch="arm64"; \
    python _install_umid.py --platform linux --arch "$arch" --dest /build/umid || \
    echo "WARN: UMID extraction failed (gateway will fall back to derived identity)"; \
    mkdir -p /build/umid

FROM python:3.11-alpine

# Set environment
# 注：API_KEY 默认为空，只作为 compose / docker run -e 的覆盖入口，不承载任何密钥。
# （docker build 的 SecretsUsedInArgOrEnv lint 提示来自变量名，而非真实密钥内容。）
# 本 fork 默认禁用「一键更新」：qoder_updater 的 REPO 指向本 fork，但上游
# release 的 tarball 会覆盖本地补丁；容器内更不该自我替换代码。
# 注意：QD_REQUIRE_PANEL_PASSWORD 与 QD_LOG_SENSITIVE 刻意**不在这里设默认值**
# ——前者由对外发布端口的编排显式开启（见 docker-compose.yml），后者保持代码
# 默认 0（掩码）。这样 docker run 的一次性用法与上游行为一致。
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8790 \
    API_KEY= \
    TZ=Asia/Shanghai \
    QD_UPDATE_DISABLE=1

WORKDIR /app

# Alpine timezone & certs + UMID 组件的 glibc 兼容层（issue #12）：
#   提取出的 runtime-info 是 glibc 动态链接的 ELF（依赖 libstdc++），alpine 是
#   musl——缺 /lib64/ld-linux-x86-64.so.2 与 libstdc++.so.6 时 exec 会直接失败
#   （exit 127，观感像「文件不存在」，实为组件在但跑不起来）。
#   gcompat 提供 glibc ABI 兼容层，libstdc++/libgcc 补齐 C++ 运行时（约 +3.1MB）。
RUN apk add --no-cache tzdata ca-certificates gcompat libstdc++ libgcc && \
    cp /usr/share/zoneinfo/${TZ} /etc/localtime && \
    echo "${TZ}" > /etc/timezone

# Copy application files (Zero external pip dependencies needed -
# AES/RSA/COSY signing are pure-stdlib implementations)
COPY qoder_proxy.py qoder_accounts.py qoder_catalog.py qoder_fingerprint.py \
     qoder_scheduler.py qoder_settings.py qoder_sign.py qoder_tasks.py \
     qoder_anthropic.py qoder_agents.py qoder_updater.py \
     qoder_pricing.py qoder_activity.py qoder_webtools.py \
     dashboard.html baseprompt.json ./

# 官方模型目录快照（运行时优先读取；缺失会回退 qoder_catalog.py 内嵌冻结副本）
COPY qoder_catalog_intl.json qoder_catalog_cn.json ./

# UMID 原生组件（构建期提取；运行时 qoder_accounts.runtime_info_exe() 在
# POSIX 下按 <repo>/umid/runtime-info 查找）。builder 提取失败时这里是空目录。
COPY --from=umid-builder /build/umid /app/umid

# 验证 / 诊断脚本一并入镜像（容器内自检用；纯标准库，零 pip 依赖）：
#   docker run --rm qoder-proxy:latest python _test_qoder.py
#   docker run --rm qoder-proxy:latest python _diag_gateway.py --chat
# 注：官方 fixture 不在镜像内，_test_qoder.py 的 [4.5] 组会打印 [SKIP]（不计失败）。
COPY _test_qoder.py _diag_gateway.py _diag_campaign.py _verify_models.py \
     _refresh_catalog.py _install_umid.py ./

# tests/ 编排（约 200KB，纯标准库）：容器内可跑完整套件自检——
#   docker run --rm qoder-proxy:<tag> python tests/run_all.py
# .js 套件需要 node（镜像内没有，自动 [SKIP]）。
COPY tests/ ./tests/

# 非 root 运行：镜像以 uid 10001 的 qoder 用户启动——PID1 与所有写盘
# （accounts/、usage/、settings.json）都不再是 root。umid 组件只需读+执行，
# 保持 root 属主（最小权限）；alpine 的 /tmp 是 1777，python 临时文件无碍。
# 注意：bind mount 的宿主机目录需先 chown（见 docker-compose.yml 注释）。
RUN adduser -D -u 10001 -H qoder && \
    mkdir -p /app/accounts /app/usage && \
    chown -R qoder:qoder /app/accounts /app/usage
USER 10001

# Volume persistence for credentials and usage logs
VOLUME ["/app/accounts", "/app/usage"]

EXPOSE 8790

# 健康探针（wb 同款）：/ping 永远 200 且不查账号池、不需要任何密钥；URL 跟随
# PORT，容器重映射端口后仍能正确报告健康状态。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/ping' % os.environ.get('PORT','8790'), timeout=3)" || exit 1

# 监听参数由上面的 ENV HOST/PORT 提供（qoder_proxy.py 的 argparse 默认值直接
# 读环境变量），所以 docker run -e PORT=9000 与 compose 的 environment 真的生效；
# 需要附带其它命令行参数时用 compose 的 command: 覆盖本 CMD。
#
# --lan 是有意的（wb 修过的真实事故：端口发布到宿主网络而 Key 校验默认关闭，
# 等于把上游额度向全网段裸奔）：--lan 监听全部网卡并**强制**生成随机 Key——
# 首次写入 ./accounts/settings.json（0600）并打印在启动日志。
#
# 启动横幅的安全默认值（v1.8.0 起，docs/plan/07-docker-auth.md）：
#   * 只打印**掩码** Key（qd-****************abc），且不再打印 ?key= 看板链接；
#     要上游那种完整 Key，显式设 QD_LOG_SENSITIVE=1。
#   * 默认面板密码 'admin' + 监听 0.0.0.0 时只告警（与上游一致）。对外发布
#     端口（反代/公网）时由编排设 QD_REQUIRE_PANEL_PASSWORD=1 拒绝启动，直到
#     面板密码改掉为止——本仓库的 docker-compose.yml 已设，镜像层不设默认值，
#     避免影响 docker run 的一次性用法。
#   * 显式传了 API_KEY 环境变量时仍以它为准（--api-key 默认读环境变量，
#     argparse 的显式值优先级最高）。
CMD ["python", "qoder_proxy.py", "--lan"]
