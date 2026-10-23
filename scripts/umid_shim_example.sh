#!/bin/sh
# umid_shim_example.sh — QD_UMID_SHIM 的示例实现（灰度/实验用，**不是**生产方案）
#
# 背景：官方 runtime-info 是阿里 UMID 的联网注册客户端，machineToken 由服务端
# 签发（见 docs/plan/02-vm-identity.md §2.1/§2.2）。本脚本只做「按同一契约把一段
# JSON 交给网关」这件事，方便你在灰度账号上验证：
#   · 服务端到底只做形态校验，还是会拿 token 反查 UMID（**未验证**）；
#   · 缓存/轮换策略在不动官方组件时是否可复现。
#
# 契约（与官方组件逐字一致，见 qoder_accounts.run_runtime_info）：
#   argv : <exe> <0|3> [--account-stdin]      # 0=国内 ONLINE / 3=国际 US_ONLINE
#   stdin: {"account":"<uid>"}\n             # 仅 darwin/win32 且带 account 时
#   stdout: 单行 JSON，至少 machineToken/machineType/machineCode，可带 vmInfo
#   exit : 0=成功；非 0 且 stdout 为空 -> 网关视为「组件不可用」并回退缓存/派生
#
# 用法（默认关，必须显式开）：
#   QD_UMID_SHIM_JSON='{"machineToken":"...","machineType":"...","machineCode":"..."}'
#   # 或从文件读（推荐：0600 的挂载卷，别写进 git / 镜像层）
#   QD_UMID_SHIM_JSON_FILE=/app/home/umid-shim.json
#
# ⚠️ 本脚本**不内置任何伪造 token**：没有输入就报错退出，避免有人把它当默认值。
# ⚠️ vmInfo 是本地诊断字段，网关不把它发给上游（qoder_accounts.desktop_headers），
#    所以「让 vmInfo 看起来像物理机」在本项目里没有收益；本仓库也不做 DMI/VM 伪装。
set -eu

# 消费 stdin（--account-stdin 时网关会写一行再关闭；不读也不会死，读掉更干净）
cat >/dev/null 2>&1 || true

if [ -n "${QD_UMID_SHIM_JSON_FILE:-}" ]; then
    if [ ! -r "$QD_UMID_SHIM_JSON_FILE" ]; then
        echo "umid-shim: cannot read QD_UMID_SHIM_JSON_FILE=$QD_UMID_SHIM_JSON_FILE" >&2
        exit 1
    fi
    cat "$QD_UMID_SHIM_JSON_FILE"
    exit 0
fi

if [ -n "${QD_UMID_SHIM_JSON:-}" ]; then
    printf '%s\n' "$QD_UMID_SHIM_JSON"
    exit 0
fi

echo "umid-shim: set QD_UMID_SHIM_JSON or QD_UMID_SHIM_JSON_FILE (refusing to invent a token)" >&2
exit 1
