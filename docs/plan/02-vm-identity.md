# Q2：官方虚拟环境检测能否绕过 + Docker 环境伪装方案

> 结论口径：本文把「官方（Qoder/阿里 UMID）到底采什么」拆成**两套身份 + 一个本地打分器**。
> 所有结论标注 **【已验证】/【推断】/【未验证假设】**，验证手段见 §6。
> 实验环境：物理机 Lenovo Legion R9000P ARX8（AMD Ryzen 9 7945HX，DMI 无虚拟化厂商）
> + 本机已装的官方组件 `~/.qoder/.bin/runtime-info-linux-x64-e30b307eaae12680`
> （sha256 `e30b307e…7478`，652,488 B，与 `_install_umid.py:26` 记录的 blob[5] 尺寸逐字节吻合）。

---

## 0. 结论摘要（TL;DR）

1. **两套身份必须分开谈**：推理链路的 `cosy-machineid/machinetoken/machinetype` 是**按 UID 派生**的（`qoder_sign.py:550-552`），
   活动链路的六个 `cosy-machine*` 才用官方 UMID 组件的**真身份**（`qoder_accounts.py:2008-2027`）。
   Docker 只影响后者；**推理、额度、每日签到都不受影响**。【已验证：代码 + `README.md:449`】
2. **runtime-info 不是「纯本地探测器」**：实测它联网到 `pum.m.alibaba.com`（123.183.232.67:443），
   `machineToken` 是**服务端签发的不透明 token**，缓存在 `$HOME/.config/.locale_cfg`（350 B JSON，键为设备指纹派生的稳定 key）。
   断网 + 无缓存 → `exit=1`，stderr `sgsdk init callback reported error code 6`，**没有任何输出**。【已验证：strace + 断网注入】
3. **vmInfo 是本地打分，不上行**：`isVm/brand/percentage/vmTypeCode` 由本地证据算出（DMI、`/proc/1/cgroup`、
   `/proc/mounts` overlay、`/proc/cpuinfo`、`lsmod`、`pgrep`、`/sys/hypervisor/*`、`/sys/class/net/wlan0/address`）。
   我们的出站头里没有任何 `vmInfo` 字段（`qoder_accounts.py:2012-2020`）。【已验证：strings + strace + 代码】
4. **实体机也会被判 VM**：本机是**物理笔记本**，组件仍返回 `{"isVm":true,"brand":"KVM","percentage":85,"vmTypeCode":13}`。
   机制线索：组件 `exec lsmod`，本机加载了 `kvm/kvm_amd` 模块（strace 无 `/dev/kvm` 读取，故不是设备节点判定）。【已验证：实测】
   官方 Windows 客户端在开 VBS/HVCI 的 Win11 上同样 `isVm=true`（`README.md:576-578`）。**→ `isVm=true` 不可能等价于「极低信誉」**。
5. **「Docker 必然被判虚拟机」成立，但「必然极低信誉」不成立**：Docker 里 `/proc/1/cgroup` 含 `docker`、
   `/proc/mounts` 有 overlay 行，组件对应有 `Docker container (cgroup)` / `overlay / Container (root filesystem)` 判据，
   所以 `isVm=true, brand=Docker, vmTypeCode=50` 几乎必然。【推断，证据强度高：strings 判据 + hub 实测抖动分支 `Docker/50`】
   但 Go 实现在 Docker 里**一个机器头都不发**，仍能领通用「每日 100 Credits」（`_refs/Qoder-2API-Go/README.md:245`）。【已验证：对方 README 自述】
6. **不要把「伪装内核/伪装物理机」作为目标**：本轮唯一可验证的收益只有「让活动列表出现设备定向条目」，
   而这条收益**至今未被任何一方验证**（`README.md:569-573`、Go `README.md:245` 都写「设备定向活动是否受影响未验证」）。【未验证假设】
7. **shim 在客户端侧 100% 可行**：网关只解析 stdout 第一行 JSON（`qoder_accounts.py:429-431`；Go `auth/risk.go:86-115`），
   Go 甚至文档化了 `QODER_RUNTIME_INFO` 指任意可执行文件的钩子（`auth/risk.go:61-63`、`README.md:123`）。【已验证】
   但**服务端是否拿 token 反查 UMID** 未知——这是 shim 的唯一真实风险点。【未验证假设】
8. **推荐的落地组合是 ⑤+①+（可选）②：不伪装，先把「真身份的可持续性」做对**：容器内固定 hostname、
   给组件一个可写的持久 home（`.locale_cfg` 落盘）、保证容器能出网到 `pum.m.alibaba.com`、启动自检把
   `umid_ok / vm_brand / token_age` 可视化。工作量 1.5–2.5 人日，零风控新增风险。
9. **我们 main 的桌面端头有形态泄漏**（顺带回答 Q4 的一半）：`MACHINE_OS="x86_64_win32"`、`MACHINE_HOSTNAME="DESKTOP-QODER"`
   是硬编码常量（`qoder_accounts.py:249-250`），在 Linux/Docker 上等于自报「我不是桌面端」；上游 v1.7 已改成
   真实 hostname + 按系统推导的 machineos，并**删掉** `X-Request-ID / X-Machine-ID / Origin / Referer` 等官方桌面端不发的头（commit `7fb9281`）。【已验证】
10. **时钟比虚拟机重要**：COSY 签名带 `cosy-date`，漂移 >120s 直接 401，上游专门加了启动自检（commit `47bdc8c`，`qoder_proxy.py:5668-5690`）。容器部署的第一优先级是 NTP，不是 DMI 伪装。【已验证：上游代码】

---

## 1. 现状证据（file:line + 代码片段）

### 1.1 身份 A：账号级派生（推理链路唯一使用）

`qoder_fingerprint.py:20-52`
~~~python
def derive_id(uid, salt):
    seed = f"{salt}:{uid or 'anonymous'}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:36]     # 恒为 32 位 hex
def derive_machine_type(uid):  return derive_id(uid, "machinetype").replace("-", "")[:18]
def derive_machine_token(uid):  return base64.urlsafe_b64encode(sha512(f"machinetoken:{uid}")).rstrip("=")[:43]
~~~

`qoder_sign.py:546-552`（会话级别固化，access token 轮换后重建）
~~~python
self.machine_id    = derive_id(self.uid, "machine")
self.machine_token = derive_machine_token(self.uid)
self.machine_type  = derive_machine_type(self.uid)
~~~

`qoder_sign.py:596-617`（推理/模型列表实际出站头）
~~~python
h = {"cosy-data-policy":"AGREE", "content-type":"application/json",
     "cosy-machinetype": self.machine_type, "cosy-clienttype":"5",
     "cosy-date": date, "cosy-user": self.uid, "cosy-key": self.cosy_key,
     "cosy-clientip":"169.254.198.161", "authorization": bearer,
     "cosy-version": COSY_VERSION, "cosy-machineid": self.machine_id,
     "cosy-machinetoken": self.machine_token, "user-agent":"Go-http-client/2.0"}
~~~

### 1.2 身份 B：UMID 原生身份（只有 /sash 活动平台用）

`qoder_accounts.py:263-268`（官方契约注释）
~~~python
#     <install>/resources/umid/runtime-info.exe prod --account-stdin
#     stdin: {"account": <uid>}   stdout: {"machineToken","machineType","machineCode",...}
# 服务端**按这些值过滤设备定向活动**：派生的假身份不会报错，但活动列表里
# 会静默少掉"每日领取 100 Credits"这类条目
~~~

`qoder_accounts.py:346-383` 查找顺序：桌面端 `resources/umid/runtime-info.exe` → `$QD_UMID_DIR/runtime-info` → `<repo>/umid/runtime-info`；
`QD_NATIVE_IDENTITY=0` 可整体关闭（`:357`）。

`qoder_accounts.py:419-431`（真实调用，注意 argv 是字面量 `"prod"`）
~~~python
proc = subprocess.run([exe, "prod", "--account-stdin"],
                      input=json.dumps({"account": account_id or ""}).encode("utf-8") + b" ",
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
                      cwd=os.path.dirname(exe))
out = proc.stdout.decode("utf-8", "replace").strip()
if out: return json.loads(out.split("\n", 1)[0])
~~~

`qoder_accounts.py:1977-2027`（活动列表头；**derived 分支一个机器头都不发**）
~~~python
ident = native_machine_identity(self.realm, self.uid)
if ident.get("machineToken"):
    h["cosy-machineid"]     = derive_id(self.uid, "machine")
    h["cosy-machinetoken"]  = ident.get("machineToken") or ...
    h["cosy-machinetype"]   = ident.get("machineType")  or ...
    h["cosy-machinecode"]   = ident.get("machineCode")  or ...
    h["cosy-machineos"]     = MACHINE_OS                 # "x86_64_win32"（硬编码）
    h["cosy-machinehostname"] = MACHINE_HOSTNAME         # "DESKTOP-QODER"（硬编码）
    self.machine_headers_state = MACHINE_HEADERS_NATIVE
else:
    self.machine_headers_state = MACHINE_HEADERS_OMITTED   # issue #10 的正确行为
~~~

### 1.3 每类请求到底带哪套身份（grep 结论，全部逐行核对）

| 请求 | 代码位置 | 头集合 | 是否用 UMID 真身份 |
|---|---|---|---|
| chat/completions、Responses、Anthropic | `qoder_sign.py:596-623` | COSY 全套 + 派生 machine 三件 | 否 |
| 模型列表 / 额度 `/api/v2/quota/usage` | `qoder_accounts.py:1963-1975`、`2668-2672` | UA + `X-Machine-ID/X-Session-ID/X-Request-ID`（派生） | 否 |
| 每日签到 `/sash/api/v1/me/daily-check-in/*` | `qoder_accounts.py:2189`、`2260-2261` | **普通 `headers()`**（无 cosy-*） | 否 |
| 活动列表 `/sash/api/v1/me/campaigns` | `qoder_accounts.py:2296` → `desktop_headers()` | UA=Qoder、`cosy-clienttype=10`、`cosy-version`、可选六机器头 | **是** |
| 活动领取 `/sash/.../claim` | `qoder_accounts.py:2433`/`2452` | 同上 | **是** |
| 诊断 `/diag/vm` | `qoder_proxy.py:6369-6378` → `local_vm_status()` | 无出站 | 否（只读本地） |

**这一步把爆炸半径压到最小**：Docker 的 VM 判定只可能影响「活动平台」这一条链路，而它连推理可用性都不碰。【已验证】

> ⚠️ **README 与代码冲突（以代码为准）**：`README.md:449` 写「只有**活动 / 签到**链路用真身份」，
> 但代码里 `checkin_status()`/`checkin()` 用的是普通 `headers()`（`qoder_accounts.py:2189`、`:2260-2261`），
> 只有 `campaigns()`/`campaign_reward()`/`claim_campaign()` 用 `desktop_headers()`（`:2296`、`:2433`、`:2452`）。
> 结论：**「每日签到」不需要 UMID 身份，只有「活动平台」需要**；README 的措辞把两者混在一起了。

### 1.4 签名覆盖范围：机器字段**不参与**签名

`qoder_sign.py:583-594`
~~~python
payload = {"cosyVersion": COSY_VERSION, "ideVersion": "", "info": self.info,
           "requestId": str(uuid.uuid4()), "version": "v1"}
raw = "\n".join([payload_b64, self.cosy_key, date, body, path])
sig = hashlib.md5(raw.encode()).hexdigest()
~~~
`info` 内是 9 个账号键（`qoder_sign.py:561-571`），**没有任何 machine 字段**。
→ 改 `cosy-machinetoken/id/type/os/hostname` 不会破坏签名，服务端看到的只是「头与账号不符」。【已验证】

### 1.5 身份落盘缓存与「换身份」路径

- 缓存文件：`$ACCOUNTS_DIR/machine_identity.json`（`qoder_accounts.py:493-499`），记录 `machineToken/machineType/machineCode/vm/vm_info/cached_at`。
- 缓存优先：命中就不调组件（`:858-869`），这是「容器重建不换设备」的唯一来源。
- 首选表决：3 轮，分歧补到 5 轮（`:766-828`），**投票键含 `vmInfo.brand/vmTypeCode`**，为了对抗组件 VM 判定的抖动。
- 换身份三条路径（`:873-883`）：`force` 自愈、`QD_MACHINE_IDENTITY_RESET=1`、TTL 到期 —— 都会先 `_purge_identity_seed()`（`:679-697`）。
- 组件不可用时**回退过期缓存**（`:898-899`），比 derived 好。

### 1.6 /diag/vm 的性质

`qoder_proxy.py:6369-6378` → `qoder_accounts.local_vm_status()`（`:456-473`）→ `qoder_fingerprint.vm_status()`（`:181-227`）。
输出 `{is_vm, level, score, brand, brand_cn, vm_type_code, source, evidence, summary}`，**只用于看板/体检**；
`README.md:579` 明确「该字段只在本地诊断用，不上行，不影响请求链路」。【已验证】

### 1.7 Dockerfile / compose 现状

- `Dockerfile:12-21`：builder 阶段 `python:3.11-alpine` 跑 `_install_umid.py --platform linux --arch $TARGETARCH --version $QODERCLI_VERSION --dest /build/umid`，失败不阻断构建。
- `Dockerfile:46-48`：运行镜像装 `gcompat libstdc++ libgcc`（组件是 glibc 动态链接 ELF，`FileNotFoundError: /lib64/ld-linux-x86-64.so.2` 的修法）。
- `Dockerfile:60-62`：`COPY --from=umid-builder /build/umid /app/umid`。
- `Dockerfile:80-83`：`adduser -D -u 10001 -H qoder` —— **`-H` 不建 home 目录**；`USER 10001`。
- `docker-compose.yml:28-32`：只挂 `./accounts:/app/accounts` 与 `./usage:/app/usage`；`mac_address`/`hostname` 都是注释（`:9-15`）。

> 这条 `-H` 是本轮最重要的可操作发现：组件把 UMID 缓存写在 **passwd 解析出来的 home**（见 §2.1 实测），
> 而容器里的 `/home/qoder` 不存在、uid 10001 也无权创建 → `.locale_cfg` 写不进去 → **每次调用都会重新注册**，
> 只有 `accounts/machine_identity.json` 在替它兜底。【推断，证据：`-H` 语义 + 实测路径解析逻辑，验证方法见 §6-V2】

---

## 2. 机制拆解

### 2.1 官方组件到底是什么（实测，非文档推断）

**证据 1：strings（`strings -a` 后按关键词聚合）**
~~~
/sys/class/dmi/id/{bios_date,bios_vendor,bios_version,board_vendor,board_name,
                   product_name,product_version,sys_vendor,manufacturer}
/sys/hypervisor/type, /sys/hypervisor/uuid
/proc/1/cgroup  (docker|lxc|containerd → 对应品牌串)
/proc/mounts    (overlay → "overlay / Container (overlay root filesystem)")
/proc/cpuinfo   (flags hypervisor / vendor_id)
/proc/version, /proc/meminfo, /etc/mtab, /dev/null, /dev/zero
/sys/class/net/wlan0/address          # 硬编码 wlan0，不做网卡枚举
/dev/vboxguest, /dev/vmci, /dev/vmbus
pgrep -f {vmtoolsd,VBoxService,VBoxTray,vmware-guestd,xend,xenstored}
lsmod  {vboxguest,vboxsf,vmwgfx,vmw_balloon,xen_blkfront,xen_netfront,hv_balloon}
lshw -class disk | grep serial
https://{pum.m.alibaba.com,pum.m.taobao.com,us-pum.alibabachengdun.com,
        sg-pum.alibabachengdun.com}/repPc.json
"sgsdk returned empty token" / "sgsdk init callback timed out after " / "sgsdk init callback reported error code "
"full main brand percentage vmTypeCode machineToken machineType machineCode vmInfo"
"indicators, avg score: " / "weak indicators, max score: " / "Time Evidence"
~~~
→ 这是 **阿里 UMID/PUM（设备画像）SDK 的 Rust 外壳** + 本地虚拟化打分器。品牌词表里同时有
`Docker/LXC/Kubernetes/Containerd` 与 `AWS EC2/Alibaba ECS/Tencent CVM/Google Cloud/Azure VM`。

**证据 2：strace（`strace -f -e trace=openat,connect,execve`）**
~~~
openat("/etc/passwd")  →  openat("/home/sixiao/.config/.locale_cfg", O_WRONLY|O_CREAT|O_TRUNC)
openat("/proc/1/cgroup") / openat("/proc/mounts") / openat("/etc/mtab") / openat("/proc/cpuinfo")
openat("/sys/class/dmi/id/*") × 7 / openat("/sys/hypervisor/type") = ENOENT
openat("/sys/class/net/wlan0/address") = ENOENT（重复 10 次）
openat("/dev/nvme0n1p") = ENOENT
execve("lsmod") / execve("pgrep", ["-f","vmtoolsd"]) / ["-f","VBoxService"] / …
connect(AF_INET, 443, 123.183.232.67)          # pum.m.alibaba.com
~~~
→ **home 由 `/etc/passwd` 解析，不读 `$HOME`**（所以 `_install_umid.py:36-38` 的「换 HOME 仍相同」观察正确，
但归因为「不落状态文件」是**错的**；hub 自己在 `docs/CHANGELOG.md:310`、`:318-319` 已更正过这条）。

**证据 3：断网 + 无缓存**
~~~
$ rm -f /home/sixiao/.config/.locale_cfg
$ strace -f -e inject=connect:error=ENETUNREACH … runtime-info 0
exit=1   stderr: sgsdk init callback reported error code 6   stdout: （空）
$ runtime-info 0        # 恢复网络
{"machineToken":"P1gAuASauMRaD8Eh3yec5ppnEqps7G4rtHKu3Q1HyammpSLAL0lKDoJ3mD2OW4we4_cLqHyKPz1smGE-NG-XkDom",
 "machineType":"cffe44b61387b89a01","machineCode":"146…","vmInfo":{"isVm":true,"brand":"KVM",…}}
~~~
**→ `machineToken` 是服务端签发，不是本地哈希。** 【已验证】

**证据 4：稳定性矩阵（同一台机器）**

| 操作 | machineToken | machineType/Code | vmInfo |
|---|---|---|---|
| 有 `.locale_cfg`，连续 5 次 | 完全一致 | 完全一致 | 一致 |
| 删除 `.locale_cfg` 后第 1 次 | 变新 | 变新 | **不变**（KVM/85/13） |
| 再第 2 次 | 与第 1 次相同 | 相同 | 相同 |
| 换 `HOME`/`cwd`/不设 `HOME` | **不变**（走 passwd home） | 不变 | 不变 |
| `argv` 传 `0/3/4/prod/--help/空` | **不变**（有缓存时 argv 被忽略） | 不变 | 不变 |

→ `machineType/machineCode` 跟着 token 一起换，说明它们是**服务端分配**的，不是硬件哈希。
→ `vmInfo` 与 token 无关，是每次现算的本地结论。【已验证】

`.locale_cfg` 内容（350 B，JSON）：两个 16 字符随机 key（**重新注册后 key 不变**）→ 长 base64 token，
外加固定 key `bdb13dc551d3db53` → 注册时间戳 ms。即：**设备指纹派生的稳定 key + 服务端下发的可轮换 token**。

### 2.2 身份产生链路（重构）

~~~
本地指纹 ──► .locale_cfg 的稳定 key（DMI/MAC/cgroup/overlay/lsmod 聚合，重新注册后不变）
     │
     └─► HTTPS POST pum.m.alibaba.com/repPc.json（携带指纹 + 设备画像）
              └─► 返回 {machineToken(不透明 66B), machineType(18hex), machineCode(18hex)}
                        └─► 写入 .locale_cfg（350B）→ 之后每次读缓存、零网络也能出结果
~~~
【推断，证据强度高：strace + 断网实验 + 重新注册后 key 不变/token 变】

### 2.3 vmInfo 判定原理与「为什么 Docker 必然 isVm=true」

组件是**多证据加权打分**（strings 有 `indicators, avg score` / `weak indicators, max score` / `Time Evidence`）：

| 证据类别 | 触发条件 | 在 Docker 里 |
|---|---|---|
| cgroup | `/proc/1/cgroup` 含 `docker` / `containerd` / `kubepods` / `lxc` | **命中**（cgroup v1）；cgroup v2 常见 `0::/` → 可能不命中 |
| 文件系统 | `/proc/mounts` 或 `/etc/mtab` 有 `overlay` 行 | **一定命中**（`overlay / Container (overlay root filesystem)`） |
| DMI | `sys_vendor/product_name` 含 vmware/virtualbox/kvm/xen/microsoft… | 容器内读到的是**宿主机** DMI → 云主机命中，家宽物理机不命中 |
| CPU | `/proc/cpuinfo` 有 `hypervisor` 标志 | 云主机命中 |
| 模块 | `lsmod` 有 kvm/vboxguest/vmwgfx/xen_*/hv_balloon | **KVM 宿主机上的容器会命中** |
| 进程 | `pgrep -f vmtoolsd/VBoxService/...` | 容器内一般看不到宿主进程 → 不命中 |
| 设备 | `/dev/vboxguest`、`/dev/vmci`、`/dev/vmbus` | 默认不存在 |
| MAC | `/sys/class/net/wlan0/address` | 容器无 `wlan0` → **永远 ENOENT（该证据在所有 Linux 上基本失效）** |
| 磁盘序列 | `lshw -class disk` | alpine 无 `lshw` → 失效 |

**两个反直觉但重要的结论：**
1. **实体机会误报**：本机物理笔记本（DMI=LENOVO，`cpuinfo` 无 hypervisor）被判 `isVm=true / KVM / 85%`；
   strace 显示没有读 `/dev/kvm`，只有 `exec lsmod`，而本机 `lsmod` 确有 `kvm_amd/kvm`。
   → **装了 KVM 的 Linux 桌面/宿主机 100% 被误判为 KVM 虚拟机**。【已验证：实测 + 代码路径推断】
2. **Docker 的 isVm 依据主要是 overlay 行**，不是 cgroup：cgroup v2 下 `/proc/1/cgroup` 常是 `0::/init.scope`
   （本会话宿主就是这么显示的），组件只靠 cgroup 会漏；overlay 判据不会漏。
   → 现代 Docker（cgroup v2）仍然会被判 `Docker/vmTypeCode=50`。
   hub 实测的抖动分支正是 `KVM/13 ↔ Docker/50`（`docs/CHANGELOG.md:281-286`），与此吻合。【推断，强度高】

### 2.4 服务端到底看什么（活动平台的四条实测事实）

1. **全套派生六头 → 被过滤**：issue #10 逐头隔离实验（`qoder_accounts.py:1991-1996`、`docs/CHANGELOG.md:702-713`）：
   任一头单独出现 → 活动可见；六头全发 → 「每日 100」整条消失；去掉 machinetoken 或 machineid → 恢复。
   → 服务端把 `machineToken/Type/Code` 当**一组可信断言**校验，不一致就整条降级，而不是报错。【已验证：社区+仓库实测】
2. **一个头都不发 → 通用活动仍在**：Go 实现（Docker/Linux 无 helper）明确写「此时一个机器头都不发，
   通用每日 100 可正常领取」（`_refs/Qoder-2API-Go/README.md:245`、`auth/risk.go:13-17`、`auth/checkin.go:116-140`）。【已验证：对方自述】
3. **只发 os+hostname → 也可见**：上游 v1.7 新增 fallback，注释写「实测这一组合仍被服务端视为官方客户端」
   （`git show v1.7.0:qoder_accounts.py:2085-2093`、`:2427-2436`）。【未复现，仅上游注释】
4. **服务端用机器身份做「人」级去重**：`claim_campaign()` 的 docstring 记着实测结论——
   「服务端按'人'去重（同一机器指纹下的多账号合并为一人）……同人已领 → `BLOCKED` + `SAME_PERSON_ALREADY_CLAIMED`」
   （`qoder_accounts.py:2437-2447`）。这说明 machine 身份**参与服务端的账号归并**，不只是格式校验。【已验证：仓库实测记录】

**推论**：活动平台做的是「机器头完整性/一致性」判断，不是「UMID 反查」。它没有 raw 拒绝过任意 token 的证据；
但也没有「随便编一个 token 六头全发」能通过的实验。**这一格是空的**——这正是 §6-V3 要补的实验。

### 2.5 为什么「伪装内核/伪装物理机」是错的目标

- 官方自己都不信 `isVm`：Win11 开 VBS/HVCI 的**实体机**必然 `isVm=true (Hyper-V, 77%)`（`README.md:576-578`）；
  本机 Linux 实体机也是 `isVm=true (KVM, 85%)`。若 `isVm` 是硬门控，所有正经 Win11 用户都被判负——不可能。
- `vmInfo` 在**我们的出站头里完全没有**（`qoder_accounts.py:2012-2020` 只发 token/type/code/os/hostname）。
  Qoder 活动服务端要拿到 `isVm`，只能靠**反查 UMID**（用 token 换设备画像）。这条是否有、是否影响活动下发，
  **没有任何一方给出过证据**。→ 【未验证假设】。
- 真正被证实影响命中率的是**出口 IP**（数据中心 → intl 排队，issue #57；`docs/risk-control-playbook.md` L0 章节
  「出口 > 身份 > 调度 > 请求整形」）。伪装 DMI 不会改变出口 ASN。

> 一句话：**先把 IP、时钟、token 可持续性做对；DMI/VM 伪装是收益未证实、成本与合规风险都最高的那一档。**

---

## 3. 上游 v1.7.0 与 Go 参考实现对比

| 维度 | 我们 main（v1.3.4+2） | 上游 v1.7.0 | Go 参考（fengyinxia/qoder2api） | 结论 |
|---|---|---|---|---|
| 设备码形态 | 32 位无连字符 hex（`qoder_fingerprint.py:33`） | **带连字符 UUIDv4 形态**（`git show v1.7.0:qoder_fingerprint.py:44-56`，commit `513254d`） | `uuid.New().String()`（`bridge/bridge.go:85`） | 我们与官方形态不一致，是可聚类特征；切换属破坏性变更 |
| request id | uid 派生前缀 + 微秒（`:36-40`） | `uuid4().hex` 纯随机（同上） | 未对齐（无此头） | 必改（低成本、无风险） |
| 推理 UA | 硬编码 `Go-http-client/2.0`（`qoder_sign.py:616`） | `undici`（`git show v1.7.0:qoder_sign.py:527`） | `Qoder`（仅活动链路） | Go-http-client 是「我是脚本」的自报 |
| 推理 machineos | 无 | 所有请求带 `cosy-machineos`（默认 win=x86_64_win32 / 其他=x86_64_linux） | 无（只在签到链路发 `arch_goos`，`auth/checkin.go:396-407`） | 上游更接近官方；我们缺这一头 |
| 推理 hostname | 无 | 仅 SSE 带，按账号派生 `DESKTOP-XXXXXXX`（`_test_risk_identity.py:20-36`） | 无 | 上游有、我们无 |
| 活动 hostname | **常量 `DESKTOP-QODER`**（`qoder_accounts.py:250`） | **本机真实 hostname**（`git show v1.7.0:qoder_accounts.py:160`，commit `7fb9281`） | 本机 hostname（`auth/checkin.go:412-420`） | 常量会被所有部署共用 → 共同指纹 |
| 活动头集合 | `headers()` 超集 + 六机器头 | 精确 5 基础头 + 6 机器头，**无** X-Request-ID/Origin/Referer/Content-Type | 基础 6 头 + 可选 6 机器头（含 Origin/Referer） | 上游最贴近官方桌面端 0.4.8 |
| runtime-info 调用 | `[exe,"prod","--account-stdin"]` + `{"account":..} `（`:424-426`） | `[exe, 0|3]`，仅 darwin/win32 带 `--account-stdin`，stdin 换行结尾（`git show v1.7.0:qoder_accounts.py:347-385`） | `[exe]` 无参（`auth/risk.go:89`） | **三种写法实测都能出结果**（我实测 argv 被忽略），但应跟上上游契约 |
| 身份缓存分区 | 按 realm（`machine_identity.json`） | 按 `environment`（cn=0/intl=3）校验，不匹配即失效（`v1.7.0:qoder_accounts.py:565,611`） | 无缓存 | 上游多一层「区域环境码」一致性 |
| 派生头回退 | 无（不发） | intl 隐藏列表时发 **os+hostname 两最小头**重试（`v1.7.0:qoder_accounts.py:2085-2093`） | 无 | 上游多一条「让列表回来」的路径 |
| 组件发现 | Win 桌面端 + `$QD_UMID_DIR`/`<repo>/umid` | 再加 macOS/Linux 默认路径 + win32 PE 提取（`7fb9281` C 段） | `QODER_RUNTIME_INFO` 覆盖 + Win/macOS 默认路径 | 上游最全；Go 提供了 shim 钩子 |
| 容器自检 | 无 | 有：`_in_container()`、时钟漂移、accounts 可写、identity_cached（commit `47bdc8c`） | 无 | 上游可观测性好 |
| VM 检测用途 | 看板/体检（`/diag/vm`） | 同（`vm_status` 仅诊断，`3008819` 修 source 谎报） | **完全没有 VM 检测** | 三方都没把它当出站信号 |
| Docker 里的态度 | 提取组件 + 落盘缓存 + 可选固定 MAC | 同上 + 可选固定 hostname + 容器 WARN（intl 数据中心出口） | 无 helper 就不发机器头，明确「Docker 也能领每日 100」 | **没有任何一方选择伪装 VM** |

---

## 4. 方案选项

### 方案 ① 真机提取 runtime-info 二进制进镜像（现状已是这一步，需补齐「可持续性」）

- **做法**：保持 `Dockerfile:12-21,60-62` 的 builder 提取；补：固定 `hostname:`；给组件可写持久 home
  （`/app/home` + `HOME=/app/home`，或挂 volume）；确保容器能出网到 `pum.m.alibaba.com`；启动自检读一次组件。
- **成本**：0.5–1 人日。
- **收益**：拿到**真身份**（issue #10 唯一已验证有效的解）；`isVm` 仍是 true，但不影响（§2.5）。
- **风险**：组件会把你容器的 DMI/MAC/cgroup 画像**上报给阿里 UMID**（`repPc.json`）——这是隐私事实，须写进文档；
  国内出口才能稳定注册成功（国外 VPS 到 `pum.m.alibaba.com` 可能超时 → 无身份）。
- **回滚**：移除 `umid/` 或 `QD_NATIVE_IDENTITY=0` → 退回「不发机器头」的既知良好状态。

### 方案 ② shim 伪造 JSON（同名可执行文件输出构造好的 JSON）

- **可行性（客户端侧）**：**100%**。网关只 `json.loads(stdout 第一行)`（`qoder_accounts.py:429-431`），
  不做任何交叉校验；Go 甚至用 3 行 shell 脚本当 fixture（`auth/risk_test.go:11-30`）。
- **做法**：`QD_UMID_DIR=/app/umid-shim`，放一个 `runtime-info`（python/shell），输出
  `{"machineToken":"…","machineType":"…","machineCode":"…","vmInfo":{"isVm":false,"brand":"","percentage":0,"vmTypeCode":0}}`。
  注意：`vmInfo` **我们根本不外发**，所以「让 vmInfo 看起来像物理机」在本项目里**没有观测意义**（只有 /diag/vm 自欺）。
- **token 稳定性**：必须自己持久化（env / 挂载文件），否则重启换设备；建议与 `machine_identity.json` 同源。
- **风险**：**服务端是否用 token 反查 UMID 未知**。若反查，假 token 会让活动列表整条消失（比不发头更差，
  因为「不发头」已被证明能看到通用活动）。→ 只能在灰度账号上试，且必须能一键回退。
- **成本**：1 人日（含灰度与回滚开关）。

### 方案 ③ 完全自造 machineToken/Type/Code（不走组件、不联网）

- 本质是方案 ② 的一个子集（自造值）。**不推荐单独作为主线**：
  已有反证——「全套**派生**六头」被服务端整条过滤（issue #10），说明服务端确实在校验这组值；
  自造值与派生值在服务端看来是同一类「非官方断言」。【推断，证据强度中高】
- 唯一合理用法：**离线兜底**（组件不可用且缓存为空的场景），且默认关闭。

### 方案 ④ 宿主机 sidecar / 外部身份服务（HTTP 回调）

- **做法**：宿主机（真机 / 已装桌面的机器）跑一个小 HTTP 服务，容器启动时拉身份并写入 `machine_identity.json`；
  或直接挂载宿主机的 `~/.config/.locale_cfg` 只读副本。
- **收益**：多实例共享同一「设备」，身份最接近真机；跨容器重建稳定。
- **代价**：2–3 人日；引入跨机信任面（身份文件 = 设备凭证，`README.md:450` 明确「不要跨机器共享 accounts/」）；
  与「一机一号池」的风控口径冲突（多账号共用设备反而更容易被关联）。
- **结论**：只在「必须让设备定向活动可见」且 ② 失败时才考虑。

### 方案 ⑤ 不伪装（现状 + 最小修补）——**推荐主线**

- 保持「无原生身份就不发机器头」这条已被验证的策略（`qoder_accounts.py:2000-2001`）；
- 把 ① 的可持续性补齐；把 ② 作为**可选实验开关**（默认关）；
- 把 `vmInfo` 从「决策输入」降级为「诊断展示」（现状已如此，只需在文档里说清）；
- **成本 0.5 人日，零新增合规风险，可立即做。**

### 推荐组合

> **⑤（主线）+ ①（补可持续性）+ ②（可选灰度开关，默认关）**。
> 不做 ③；④ 仅在 ② 灰度失败且业务确需设备定向活动时评估。
> **不做任何 DMI/cgroup/overlay 的伪造**：收益未证实，成本与「被判定为刻意伪装」的风险都最高。

---

## 5. 落地改动清单（文件级 + 补丁草案 + 开关/回滚）

> 本轮**不改码**，以下为待批准草案。

### 5.1 `docker-compose.yml`

~~~yaml
services:
  qoder-proxy:
    # ① 固定 hostname：官方 0.4.8 活动头发真实 hostname，容器默认是容器 ID
    hostname: my-qoder-gw            # 换成人自己的名字，别照抄
    environment:
      - TZ=Asia/Shanghai
      - HOME=/app/home                # ② 组件按 /etc/passwd 找 home，但显式给更稳
      - QD_NATIVE_IDENTITY=1
      - QD_MACHINE_IDENTITY_CACHE=auto
      # - QD_UMID_DIR=/app/umid-shim  # ② 的 shim 开关（默认关）
    volumes:
      - ./accounts:/app/accounts
      - ./usage:/app/usage
      - ./umid-home:/app/home         # ③ .locale_cfg 持久化（350B，随时间几乎不变）
~~~
**回滚**：删 `hostname`/`umid-home` 两行，行为回到今天。

### 5.2 `Dockerfile`

~~~dockerfile
# ④ 给非 root 用户一个可写 home（-H 不建目录是当前 .locale_cfg 写不进去的根因）
RUN adduser -D -u 10001 -H qoder && \
    mkdir -p /app/accounts /app/usage /app/home && \
    chown -R qoder:qoder /app/accounts /app/usage /app/home
ENV HOME=/app/home
~~~
**风险**：`HOME` 变更会影响 `_purge_identity_seed()` 的目标路径（`qoder_accounts.py:691` 用 `expanduser("~")`）——
这正好是我们要的（清 `/app/home/.config/.locale_cfg`），但需回归 `QD_MACHINE_IDENTITY_RESET` 路径。

### 5.3 `qoder_accounts.py`（小改）

1. `run_runtime_info()` 的 argv 契约对齐官方 0.4.8：`[exe, "3" if realm=="intl" else "0"]`，
   仅 darwin/win32 才追加 `--account-stdin`；保留 `QD_RUNTIME_INFO_ENV` 覆盖。
   （现状 `"prod"` 实测可用，属兼容性改进而非修 bug，**风险低**）
2. 解析时记录 `environment`，缓存条目加字段；不匹配则失效重取（对齐上游 `7fb9281` B 段）。
3. 组件失败时把 `runtime_info_exe()` 返回值与实际原因分开上报（已有 `_runtime_info_warn`，issue #12）。
4. shim 支持：`QD_UMID_DIR` 已能指向任意目录，**无需改码**；若要「只允许显式 shim」再加 `QD_UMID_TRUST_SHIM=1` 守卫。

### 5.4 `qoder_proxy.py`（`/diag/vm` 增强，可选）

在 `local_vm_status()` 结果里补三个只读字段（不改结构，只增不删）：
`umid_exe`（路径/空）、`identity_cached_at`（`machine_identity.json` 的 `cached_at`）、`identity_env`。
价值：Docker 里能一眼看出「是没组件 / 组件跑不起来 / 有身份但老了」。

### 5.5 文档（`docs/`）

- 「组件会把容器的 DMI/MAC/cgroup 画像上报给 `pum.m.alibaba.com`」写进 README 已知限制（隐私）；
- 「`isVm=true` 在实体机上也会出现（KVM 模块/VBS）」，明确它**不是**封号信号；
- 「Docker 里推荐：固定 hostname + home 持久化 + 出口可达 pum」。

### 5.6 开关与回滚总表

| 开关 | 默认 | 作用 | 回滚方式 |
|---|---|---|---|
| `QD_NATIVE_IDENTITY` | `1` | 0 = 完全不调组件（退回 derived + 不发机器头） | 置 0 |
| `QD_UMID_DIR` | 空 | 指向 shim / 自定义组件目录 | 删变量 |
| `QD_MACHINE_IDENTITY_CACHE` | `auto` | off = 不落盘（重建即换设备） | 置 auto |
| `QD_MACHINE_IDENTITY_RESET` | 空 | 1 = 清缓存 + 清种子换新身份 | 一次性变量 |
| `HOME`（compose） | 镜像内 | 决定 `.locale_cfg` 落点 | 删该 env |
| `hostname`（compose） | 容器 ID | 决定 `cosy-machinehostname` | 删该行 |

---

## 6. 验证方法

### V1 组件在容器里到底报什么（5 分钟，零风险）
~~~bash
docker run --rm -e HOME=/tmp qoder-proxy:latest sh -c '
  /app/umid/runtime-info 0; echo "exit=$?"'
~~~
期望：JSON 里 `isVm=true, brand="Docker", vmTypeCode=50`（overlay 判据）；若输出 `Docker container (cgroup)` 则 cgroup 也命中。
对照：`docker run --rm --network none …` 且**无缓存** → 应看到 `exit=1` + `sgsdk init callback reported error code 6`
（证明身份依赖出网，与本文实测一致）。

### V2 容器内 token 是否每次变化（验证 §1.7 的 `-H` 推断）
~~~bash
docker run --rm qoder-proxy:latest sh -c '
  R=/app/umid/runtime-info; H=$(mktemp -d); export HOME=$H; mkdir -p $H/.config
  for i in 1 2 3; do $R 0 | head -c 60; echo; done; ls -la $H/.config'
~~~
若三次 token 全不同且 `.config` 为空 → 确认容器里「无法落盘 → 每次重新注册」，
则 §5.2 的 `HOME` 挂载是**必需**而非可选。

### V3 shim 灰度（只影响活动链路，可秒级回滚）
1. 准备 `/app/umid-shim/runtime-info`，输出固定 JSON（含 `vmInfo`）；
2. `QD_UMID_DIR=/app/umid-shim` + 删 `accounts/machine_identity.json`；
3. 观察 `_diag_campaign.py`（`:179` 用的就是 `desktop_headers()`）：
   `showCampaign` 是否 true、`claimable` 是否出现「每日 100」；
4. 判定：**假 token 若能过 → 说明服务端只做形态校验；若被过滤 → 立即回滚删 shim**。
   （对照组：什么都不设 = 现状）

### V4 「Docker 是否真的极低信誉」观测实验（不滥用）
- **对象**：1 个 CN 账号 + 1 个 intl 账号，**连续 3 天**，每天固定时间各 1 次，不并发、不换出口。
- **分组**：A 组 = 现状（Docker + 原生身份）；B 组 = 同账号在**物理机**上跑同一版本（换机即换身份，注意备份/还原 `accounts/`）。
- **指标**：`showCampaign`、`claimable` 数量、`/sash` HTTP 码、`/health.risk` 的 queue/rate/rejected、
  推理延迟 P50、是否出现 10605。
- **判据**：若 A/B 的 `claimable` 与 10605 率无差异 → 「Docker = 低信誉」证伪；若 A 明显差 → 再区分是 IP 还是身份
  （加 C 组：Docker + 住宅代理）。
- **成本**：3 天 × 每天 10 分钟；**不制造额外请求**（就是日常签到/一次聊天）。

### V5 回归测试（改码后必跑）
- `python tests/run_all.py`（20 suites / 889 checks，`README.md:603`）；
- 现有身份相关套件：`tests/_test_identity_cache.py`（覆盖缓存/表决/自愈）、
  `tests/_test_offline_tools.py` [21][30]（vm_status + UMID 提取器纯函数）；
- 新增断言建议：argv 契约（0/3 + 平台差异）、`HOME` 变更后 `_purge_identity_seed` 命中新路径、
  shim 开关下 `machine_identity_source` 仍只取 `runtime-info|derived` 两值。

### V6 观测指标（上线后）
| 指标 | 采集点 | 健康值 |
|---|---|---|
| 身份来源 | `machine_identity_source` | `runtime-info` 占比 = 1（除刻意关闭） |
| 缓存年龄 | `machine_identity.json.cached_at` | 长期不变（重建不换） |
| VM 判定分布 | `/diag/vm.brand` | Docker=50 / KVM=13；**不作为告警** |
| 组件失败分类 | stderr `[runtime-info]` 前缀 | 0（alpine 缺 gcompat 会在启动即暴露） |
| 活动可见性 | `campaigns().show_campaign` | true |

---

## 7. 风险与未决问题（需你拍板）

1. **是否允许「把容器画像上报给阿里 UMID」**。方案 ①/② 都会让 `pum.m.alibaba.com` 收到容器的 DMI/MAC/cgroup 汇总。
   这是隐私事实，不是技术缺陷；需要确认是否接受（不接受 → 只能走「不发机器头」的现状）。
2. **是否允许 shim**。伪造第三方设备指纹没有技术障碍（客户端侧已验证），但**服务端是否反查 UMID 未知**；
   一旦反查，假 token 的表现可能比「不发头」更差。是否批准做 V3/V4 的灰度实验？
3. **`machineType/machineCode` 是服务端分配值**（本文实测：删缓存后随 token 一起变）。
   这与 `qoder_fingerprint.py:43-45` 把它们当「本地派生量」的假设不同 —— 派生值只在缺组件时用，语义上应注释清楚（文档项，非 bug）。
4. **`_install_umid.py:34-42` 的「已知限制」段落自相矛盾**：说「不落任何状态文件」（假，实测写 `.locale_cfg`）、
   又说「不同 HOME 返回同一 token」（真，但原因是走 `/etc/passwd` 而非无状态）。建议下一轮改文档，**不要改代码行为**。
5. **上游 v1.7 的 UUID 形态切换是破坏性的**（`513254d`）：切换即所有账号「换设备」。是否跟进？建议**不跟进**，
   或在合并 v1.7 时设 `QD_DEVICE_ID_LEGACY=1` 保持存量。
6. **`.locale_cfg` 的 token 有效期未知**（66 B 不透明二进制，无可见时间戳）。
   hub 只有「25s+ 仍被接受」和「69 分钟 24 次不变」两条弱证据；长缓存是否会被服务端作废，**未验证**。
7. **cgroup v2 让 cgroup 判据失效**，组件其余判据仍在；若未来官方升级到「只认 cgroup」的版本，
   Docker 检测会失效——那时也不需要伪装。**不建议为了这个可能性提前加固**。
8. **多容器共用同一份 `accounts/`** = 共用同一台设备（`README.md:450`），比 VM 伪装危险得多；需要运维约束。
9. **shim 的「去重副作用」**：服务端按机器指纹把账号合并为「人」（§2.4 事实 4），
   所以 shim 若给所有账号发**同一个** token，等于把它们绑成同一个人——活动奖励只会给第一个号。
   即使用真组件也一样（机器级身份，`_install_umid.py:34-42` 已写明「不能解决同机多号各自领」）。
   → 任何「共享身份 / 复制 `machine_identity.json`」的方案都要先接受这条业务约束。

---

## 8. 工作量估计（人日）与依赖关系

| 项 | 内容 | 人日 | 依赖 |
|---|---|---|---|
| T1 | compose/Dockerfile 的 hostname + HOME 持久化 + 文档 | 0.5 | 无（可立即做） |
| T2 | `run_runtime_info` argv/环境码对齐上游 + 缓存加 `environment` | 0.5 | T1（同文件回归） |
| T3 | `/diag/vm` 增补 `umid_exe / identity_cached_at` 只读字段 | 0.5 | 无 |
| T4 | shim 开关 + 目录骨架 + 默认关闭守卫 | 0.5 | T2 |
| T5 | V1/V2 容器实验（含断网对照） | 0.5 | T1 |
| T6 | V3 shim 灰度（单账号、可回滚） | 1.0 | T4, T5 |
| T7 | V4 三日观测实验 + 结论 | 1.0（跨 3 天） | T5；需真实账号与物理机各一 |
| T8 | 文档收尾（隐私/GitHub 已知限制/回滚手册） | 0.5 | T1–T7 |
| **合计** | 不含 V4 等待时间 | **3.5–4.5 人日** | |

**关键路径**：T1 → T5 → （T4 → T6）/ T7。T3/T8 可并行。
**建议排期**：先做 T1+T5（半天内就能拿到「容器里 isVm/brand/token 行为」的硬数据），
再决定是否投入 T4/T6 —— **在拿到 V5 数据之前，不要写任何伪装代码**。

---

### 附：本轮实验的一次性副作用（如实登记）

- 本机 `/home/sixiao/.config/.locale_cfg` 由实验创建（350 B，原不存在），中途被删除重建两次，
  当前存在且内容为一次新注册的有效 token；**这正是网关运行时会生成的文件**，可安全保留，也可直接删掉（下次调用自动重建）。
- 临时目录 `/tmp/qd-home-*`、`/tmp/qd-exp-a`、`/tmp/qd-tr*.txt`、`/tmp/qd-out*.json` 为实验产物，可随时删除。
- 仓库源码、`accounts/`、`baseprompt.json`、git 历史**零改动**（未执行任何 git 写操作）。
