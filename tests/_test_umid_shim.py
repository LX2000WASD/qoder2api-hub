# -*- coding: utf-8 -*-
"""Q2：UMID shim 灰度 + 自检字段（离线；不联网、不执行真实 runtime-info）

    python tests/_test_umid_shim.py

守卫四件事（docs/plan/02-vm-identity.md §4 方案② / §6-V2）：
  1. QD_UMID_SHIM 默认关；设了优先于桌面端/提取目录，QD_NATIVE_IDENTITY=0 仍是总开关；
  2. argv/stdin/stdout 契约与官方 runtime-info 一致（环境码 / --account-stdin / 单行 JSON）；
  3. shim 的失败形态（无输入、非法 JSON）按「组件不可用」回退 {}，不崩；
  4. umid_status()/启动自检字段含 token_age/vm_brand，**不含 token 内容**。
所有用例都用临时脚本打桩，绝不拉起真实组件（否则会联网注册）。
"""
import datetime
import json
import os
import stat
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _suite_head import *          # noqa: F401,F403

P, A = bootstrap()
declare_suite(__file__, ["qoder_accounts.py", "scripts/umid_shim_example.sh"])

_ENV_KEYS = ("QD_UMID_SHIM", "QD_UMID_SHIM_JSON", "QD_UMID_SHIM_JSON_FILE",
             "QD_UMID_DIR", "QD_NATIVE_IDENTITY", "ACCOUNTS_DIR")
_saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
_saved_warned = set(A._runtime_info_warned)


def _reset_env():
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    A._native_exe_cache.clear()
    A._runtime_info_warned.clear()


def _restore():
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    A._native_exe_cache.clear()
    A._runtime_info_warned.clear()
    A._runtime_info_warned.update(_saved_warned)
    A._vm_cache.clear()


def _write(path, text, mode=0o755):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


def _shim_script(directory, payload='{"machineToken":"tok-1","machineType":"ty-1","machineCode":"co-1"}'):
    """写一个最小 shim：按契约把固定 JSON 打到 stdout。"""
    path = os.path.join(directory, "shim.sh")
    return _write(path, "#!/bin/sh\ncat >/dev/null 2>&1 || true\nprintf '%s\\n' '%s'\n"
                  % ("%s", payload))


_TMP = tempfile.mkdtemp(prefix="umid-shim-")
_FAKE_JSON = '{"machineToken":"tok-x","machineType":"ty-x","machineCode":"co-x"}'

# ---------------------------------------------------------------- [1] 开关与优先级
_reset_env()
check("默认关：未设置 QD_UMID_SHIM 时 umid_shim_path() 为空",
      A.umid_shim_path() == "", os.environ.get("QD_UMID_SHIM"))

_missing = os.path.join(_TMP, "nope.sh")
os.environ["QD_UMID_SHIM"] = _missing
check("设了但文件不存在 -> 按未设置处理并 WARN 一次",
      A.umid_shim_path() == "" and "shim-missing" in A._runtime_info_warned,
      A._runtime_info_warned)

_shim = _shim_script(_TMP)
os.environ["QD_UMID_SHIM"] = _shim
check("有效 shim -> 返回绝对路径", A.umid_shim_path() == os.path.abspath(_shim),
      A.umid_shim_path())

_reset_env()
_noexec = _write(os.path.join(_TMP, "noexec.sh"), "#!/bin/sh\nexit 0\n", mode=0o644)
os.environ["QD_UMID_SHIM"] = _noexec
if os.name == "posix":
    check("无执行权限 -> 按未设置处理并 WARN 一次",
          A.umid_shim_path() == "" and "shim-noexec" in A._runtime_info_warned,
          A._runtime_info_warned)
else:
    skip("无执行权限 -> 按未设置处理", "Windows 无 POSIX 执行位")

# 未设置时仍走原有查找路径（QD_UMID_DIR），设了 shim 则整体优先
_reset_env()
_umid_dir = os.path.join(_TMP, "umid")
os.makedirs(_umid_dir, exist_ok=True)
_real_ish = _write(os.path.join(_umid_dir, "runtime-info"), "#!/bin/sh\nexit 0\n")
os.environ["QD_UMID_DIR"] = _umid_dir
check("未设 shim 时沿用原路径（QD_UMID_DIR/runtime-info）",
      A.runtime_info_exe("cn") == _real_ish, A.runtime_info_exe("cn"))
os.environ["QD_UMID_SHIM"] = _shim
A._native_exe_cache.clear()
check("设了 shim 时整体优先于 QD_UMID_DIR / 桌面端",
      A.runtime_info_exe("cn") == os.path.abspath(_shim), A.runtime_info_exe("cn"))
os.environ["QD_NATIVE_IDENTITY"] = "0"
A._native_exe_cache.clear()
check("QD_NATIVE_IDENTITY=0 仍是总开关（连 shim 也不走）",
      A.runtime_info_exe("cn") == "", A.runtime_info_exe("cn"))

# ---------------------------------------------------------------- [2] 调用契约
_reset_env()
os.environ["QD_UMID_SHIM"] = _shim
_calls = []


class _Proc:
    returncode = 0
    stdout = (b'{"machineToken":"tok-1","machineType":"ty-1",'
              b'"machineCode":"co-1","vmInfo":{"isVm":false}}\n')
    stderr = b""


def _fake_run(cmd, input=None, **kwargs):
    _calls.append({"cmd": list(cmd), "input": input, "cwd": kwargs.get("cwd")})
    return _Proc()


import subprocess as _sp              # noqa: E402
_orig_run = _sp.run
_orig_platform = sys.platform
_sp.run = _fake_run
try:
    sys.platform = "linux"
    _got = A.run_runtime_info("cn", "u-contract")
    check("linux：argv=[shim, 0]，stdin 直接 EOF",
          _calls[-1]["cmd"] == [os.path.abspath(_shim), "0"]
          and _calls[-1]["input"] == b"", _calls[-1])
    check("stdout 单行 JSON 被正确解析",
          _got.get("machineToken") == "tok-1"
          and _got.get("vmInfo", {}).get("isVm") is False, _got)
    check("子进程 cwd = 组件所在目录", _calls[-1]["cwd"] == _TMP, _calls[-1])

    A.run_runtime_info("intl", "u-contract")
    check("intl：环境码 3（与官方桌面端 region===global 对齐）",
          _calls[-1]["cmd"] == [os.path.abspath(_shim), "3"], _calls[-1])

    sys.platform = "darwin"
    A.run_runtime_info("intl", "u-abc")
    check("darwin+account：追加 --account-stdin，stdin 是以换行结尾的账号 JSON",
          _calls[-1]["cmd"] == [os.path.abspath(_shim), "3", "--account-stdin"]
          and _calls[-1]["input"] == b'{"account":"u-abc"}\n', _calls[-1])

    A.run_runtime_info("cn", "")
    check("无 account 时不加 --account-stdin（即使 darwin）",
          "--account-stdin" not in _calls[-1]["cmd"], _calls[-1])
finally:
    _sp.run = _orig_run
    sys.platform = _orig_platform

# ---------------------------------------------------------------- [3] 真实脚本端到端
_example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "umid_shim_example.sh")
if os.name == "posix" and os.path.isfile(_example):
    _copy = _write(os.path.join(_TMP, "example.sh"),
                   open(_example, encoding="utf-8").read())
    _reset_env()
    os.environ["QD_UMID_SHIM"] = _copy
    os.environ["QD_UMID_SHIM_JSON"] = _FAKE_JSON
    _got2 = A.run_runtime_info("cn")
    check("示例脚本：QD_UMID_SHIM_JSON -> 契约 JSON 被网关接受",
          _got2.get("machineToken") == "tok-x"
          and _got2.get("machineType") == "ty-x", _got2)

    _json_file = _write(os.path.join(_TMP, "shim-identity.json"),
                        '{"machineToken":"tok-f","machineType":"ty-f",'
                        '"machineCode":"co-f"}', mode=0o600)
    os.environ.pop("QD_UMID_SHIM_JSON", None)
    os.environ["QD_UMID_SHIM_JSON_FILE"] = _json_file
    check("示例脚本：QD_UMID_SHIM_JSON_FILE 同样可用",
          A.run_runtime_info("cn").get("machineToken") == "tok-f")

    os.environ.pop("QD_UMID_SHIM_JSON_FILE", None)
    check("示例脚本：没有输入时 exit 1 + 空 stdout -> 网关回退 {}",
          A.run_runtime_info("cn") == {}, "should be empty")

    os.environ["QD_UMID_SHIM_JSON"] = "not-json"
    check("shim 输出非法 JSON -> 回退 {} 且不抛异常",
          A.run_runtime_info("cn") == {}, "should be empty")
else:
    skip("示例脚本端到端", "非 POSIX 或脚本缺失")

# ---------------------------------------------------------------- [4] umid_status / 自检
_reset_env()
_acc_dir = tempfile.mkdtemp(prefix="umid-acc-")
os.environ["ACCOUNTS_DIR"] = _acc_dir
os.environ["QD_UMID_SHIM"] = _shim
_now = time.time()
with open(os.path.join(_acc_dir, "machine_identity.json"), "w", encoding="utf-8") as fh:
    json.dump({"version": 1, "realm": {"cn": {
        "machineToken": "SECRET-TOKEN-123", "machineType": "ty-cache",
        "machineCode": "co-cache", "vm": True,
        "vm_info": {"isVm": True, "brand": "KVM", "percentage": 85,
                    "vmTypeCode": 13},
        "environment": "0", "source": "runtime-info",
        "cached_at": _now - 7200}}}, fh)
A._native_exe_cache.clear()
_st = A.umid_status("cn")
check("umid_status：present/source/shim_active",
      _st["present"] is True and _st["source"] == "shim"
      and _st["shim_active"] is True, _st)
check("umid_status：identity_cached + token_age≈2h（来自 cached_at）",
      _st["identity_cached"] is True
      and abs((_st["token_age_s"] or 0) - 7200) < 120
      and _st["token_age"] == "2h", (_st["token_age_s"], _st["token_age"]))
check("umid_status：vm_brand/vm_type_code 来自缓存 vm_info",
      _st["vm_brand"] == "KVM" and _st["vm_type_code"] == 13
      and _st["is_vm"] is True, (_st["vm_brand"], _st["vm_type_code"]))
check("umid_status 不含任何 token 内容（键与值都不出现）",
      "machineToken" not in _st and "SECRET-TOKEN-123" not in json.dumps(_st),
      sorted(_st.keys()))

# 启动自检行：同源、无 token
_lines = []
A._log_umid_startup(_lines.append)
check("启动自检行包含 present/source/shim_active/token_age/vm_brand",
      len(_lines) == 1 and _lines[0].startswith("umid: ")
      and "present=yes" in _lines[0] and "shim_active=1" in _lines[0]
      and "token_age=2h" in _lines[0] and "vm_brand=KVM" in _lines[0],
      _lines)
check("启动自检行不含 token",
      "SECRET-TOKEN-123" not in _lines[0] and "tok-" not in _lines[0], _lines[0])

# /diag/vm 的数据源（local_vm_status）也带上自检字段
_reset_env()
os.environ["ACCOUNTS_DIR"] = _acc_dir
os.environ["QD_UMID_SHIM"] = _shim
A._native_exe_cache.clear()
A._vm_cache.clear()
_st_vm = A.local_vm_status("cn", force=True)
check("local_vm_status（/diag/vm 数据源）附带 shim/自检字段且只增不删",
      _st_vm.get("shim_active") is True and _st_vm.get("present") is True
      and "token_age" in _st_vm
      and set(("is_vm", "level", "score", "brand", "brand_cn",
               "vm_type_code", "source", "evidence", "summary")) <= set(_st_vm),
      sorted(_st_vm.keys()))

# ---------------------------------------------------------------- [5] 未设置 = 行为不变
_reset_env()
os.environ["ACCOUNTS_DIR"] = _acc_dir
os.environ["QD_NATIVE_IDENTITY"] = "0"
_st_off = A.umid_status("cn")
check("未设置 shim 且组件关闭：source=none / shim_active=0 / present=no",
      _st_off["source"] == "none" and _st_off["shim_active"] is False
      and _st_off["present"] is False, _st_off)
check("未设置 shim 时 runtime_info_exe 仍走原逻辑（此处无组件 -> 空串）",
      A.runtime_info_exe("cn") == "", A.runtime_info_exe("cn"))

_restore()
finish()
