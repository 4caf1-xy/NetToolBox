# Compose 实验室（docs/lab/README.md）

> 受众：执行条目验证的工程师与贡献者 ｜ 权威出处：本文件（compose 细节），环境映射口径见 [verify-guide](../verify-guide.md)。
>
> **定位**：给"复制 → 粘贴 → 贴回显"回路提供可抛弃的粘贴目标，压低验证会与贡献者参与门槛。
> **环境即抛型**：所有数据都在容器内，破坏性命令随便执行——`down -v` 一条命令回到全新状态。
> 容器里的 root 密码是刻意的弱密码 `ntbxlab`，端口只绑 `127.0.0.1`，**不要改成对外网开放**。

## 一、前置要求

| 项 | 要求 |
|---|---|
| Docker | Docker Desktop 4.x（WSL2 后端）或任意 Linux 上的 Docker Engine 20.10+，含 compose v2 |
| 内存 | 建议 16GB 物理内存；4 个服务运行合计 ≤2GB（srlinux 上限 1GB） |
| 磁盘 | 镜像合计约 1.5GB（srlinux 占大头）；数据盘随 Docker 配置 |
| 网络 | 首次 `up` 需拉取 `frrouting/frr`、`ghcr.io/nokia/srlinux` 与基础镜像，见 FAQ 拉取失败 |

## 二、拉起 / 进入 / 重置

```bash
# 在仓库根目录执行
docker compose -f lab/docker-compose.yml up -d          # 拉起全部 4 服务（首次会构建 2 个 Linux 镜像）
docker compose -f lab/docker-compose.yml ps             # 查看健康状态（healthy 才算就绪）
docker compose -f lab/docker-compose.yml down -v        # 抛弃环境：删容器+卷，回到全新状态
docker compose -f lab/docker-compose.yml up -d --build  # 改过 Dockerfile 后重建
```

| 服务 | 容器名（固定） | 进入方式 | 主机端口 |
|---|---|---|---|
| Ubuntu 22.04（systemd） | `ntbx-lab-ubuntu` | `ssh root@127.0.0.1 -p 2222`（密码 `ntbxlab`）或 `docker exec -it ntbx-lab-ubuntu bash` | 2222 |
| CentOS 7（systemd） | `ntbx-lab-centos7` | `ssh root@127.0.0.1 -p 2223`（密码 `ntbxlab`）或 `docker exec -it ntbx-lab-centos7 bash` | 2223 |
| FRR（vtysh 经典 CLI） | `ntbx-lab-frr` | `docker exec -it ntbx-lab-frr vtysh`（`exit` 退出） | 无 |
| Nokia SR Linux 26.7 | `ntbx-lab-srlinux` | `docker exec -it ntbx-lab-srlinux sr_cli` | 无 |

说明：
- `ntbx-lab-ubuntu` 以 systemd 为 PID 1（privileged），`systemctl` 类条目可正常验证；
- `ntbx-lab-centos7` 因 centos7 老 systemd（219）与 cgroup v2 宿主结构性不兼容（服务无法启动，实测见 FAQ 5），改由 sshd 前台直跑——ssh / yum / 网络排查类条目可用，`systemctl` 类条目不可在此容器验证；
- FRR 已启用 zebra / staticd / bgpd / ospfd / bfdd（配置在 `lab/frr/daemons`）；
- SR Linux 为 Nokia 官方免许可容器化 NOS，用于贡献者练习验证回路、未来 Nokia 种子预留；
- 镜像文件不入库，运行数据全在容器内。

## 三、设备映射表

与 [verify-guide](../verify-guide.md) 环境映射**同一套口径**（判定按其四档标准执行）：

| device_type | compose 实验室可用性 | 说明 |
|---|---|---|
| 服务器（Linux） | ✅ `linux-ubuntu` / `linux-centos7` | ubuntu/os:ubuntu2204 条目走 ubuntu 容器（systemd 可用，`systemctl` 类可验证）；centos7 条目走 centos7 容器——非 systemctl 类（yum/ss/tcpdump 等）可用，`systemctl` 类**不可验证**（cgroup v2 宿主 + systemd 219 兼容死结，见 FAQ 5），按**语法核对**档、不判绿；kylinV10 / openeuler2203 为 RHEL 系，在 centos7 容器只做**语法核对**档（包管理器/服务管理命令与实际发行版有差异，不判绿） |
| 通用 Linux（device_type 空） | ✅ `linux-ubuntu` | 纯 bash/procps 类命令在任意 Linux 容器等价 |
| 路由器（cisco/ios） | ⚠️ `frr` 仅语法核对档 | `ip route` / `show ip route` / OSPF 骨架语法与 IOS 同构；**不判绿**（转发行为、硬件接口与非 IOS 厂商语法无法等价）；华为/H3C/锐捷/中兴 `display` 系不适用，挂起 |
| 路由器（华为/H3C/锐捷/中兴/juniper） | ❌ 挂起 | 语法族不同，走模拟器（VRP/HCL/vSRX）或真机 |
| 交换机 | ⚠️ 大多挂起 | 二层特性（STP/VLAN/Trunk）非 FRR 覆盖范围；IOS 风格管理面骨架可在 frr 做语法核对档，不判绿 |
| 防火墙 | ❌ 真机专权 | FortiGate VM / PAN-OS VM / 深信服 AF 虚拟化 / 天融信虚拟化均需许可，**无免许可容器化路径**，本实验室不硬凑 |

## 四、实操示例：完整走一遍验证回路

### 示例 1：Linux 条目（查端口监听与占用 ss）

1. **打开命令库**，搜"查端口监听与占用"（Ubuntu 排查命令类），详情页勾选参数 `port=22`，复制命令：
   ```
   ss -tlnp | grep :22
   ```
2. **粘贴执行**：`ssh root@127.0.0.1 -p 2222`（密码 `ntbxlab`）进入 `ntbx-lab-ubuntu`，粘贴命令，得到类似回显：
   ```
   LISTEN 0  4096  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=1,fd=3))
   ```
3. **判读**：notes 判读口径为"LISTEN 有 → 服务在监听；没有 → 服务没起"。容器里 sshd 监听 22，与判读口径一致 → **绿档**。
4. **贴回显 + 回填**：notes 贴回显要点（sshd pid/监听地址），回填 `verified=1`、`verified_by`、`verified_model=Docker(ubuntu:22.04, 容器)`、`verified_date`。回填工具与字段名以伴生仓当版文档为准。

### 示例 2：FRR 条目（Cisco 静态路由 → 语法核对档）

1. **打开命令库**，搜"配置静态路由与浮动静态路由"（Cisco 路由器类），复制命令块：
   ```
   configure terminal
   ip route 192.168.20.0 255.255.255.0 10.0.0.2
   ip route 192.168.20.0 255.255.255.0 10.0.0.3 200
   ip route 0.0.0.0 0.0.0.0 10.0.0.2
   end
   write memory
   show ip route 192.168.20.0
   ```
2. **粘贴执行**：`docker exec -it ntbx-lab-frr vtysh`，粘贴命令块。FRR 与 IOS 同构，命令全部被接受（`configure terminal` 进入配置模式，粘贴的命令依次落盘，`end` 退出）。
3. **档位判定**：**配置是否被接受，用 `show running-config` 确认**——能看到 `ip route 192.168.20.0/24 10.0.0.2`（FRR 把 IOS 掩码写法规范化为 CIDR）与默认路由。注意与 IOS 的行为差异：下一跳 10.0.0.2 在容器里不可达时，`show ip route` **不显示**该 S 路由（FRR 不展示下一跳未解析的静态路由，IOS 则显示 inactive 行）——这是预期现象 → **语法核对档**，notes 记录"`show ip route` 无 S 行因下一跳不可达（FRR 特性）；`ip route` 族与 `write memory` 语法确认（running-config 可见）"，`exec_level` 若为 skeleton 升 `verified-cli`。**不判绿**。
4. **贴回显 + 回填**：同上，`verified_model=Docker(FRR, vtysh)`。

> 纪律提醒：实验室判定同样遵守 verify-guide 四档标准与"宁红勿绿"——语法核对档**永远不判绿**，涉及该厂商真实行为的结论以真机/模拟器验证为准。

## 五、常见问题（FAQ）

**1. 端口被占用（2222/2223）**
报错 `bind: address already in use`。查占用：`netstat -ano | findstr 2222`（Windows）或 `ss -tlnp | grep 2222`（Linux）。改宿主端口即可——编辑 `lab/docker-compose.yml` 里对应 `ports`（如 `"127.0.0.1:2322:22"`），指南里 ssh 命令跟着改；容器内 22 不变。

**2. 镜像拉取失败（超时 / not found）**
`frrouting/frr` 在 Docker Hub、`ghcr.io/nokia/srlinux` 在 GitHub Container Registry。国内网络可给 Docker 配 registry mirror（Docker Desktop → Settings → Docker Engine）：
```json
{ "registry-mirrors": ["https://docker.m.daocloud.io"] }
```
ghcr 镜像可用代理前缀方式拉取后改 tag（镜像站可用性随时间变化，方法为准）：
```bash
docker pull ghcr.m.daocloud.io/nokia/srlinux:26.7
docker tag ghcr.m.daocloud.io/nokia/srlinux:26.7 ghcr.io/nokia/srlinux:26.7
```
公司内网环境先确认代理放行了对应域名。

**3. 容器重建 / 状态污染**
- 全量重置（最常用）：`docker compose -f lab/docker-compose.yml down -v && docker compose -f lab/docker-compose.yml up -d`
- 只重建单个服务：`docker compose -f lab/docker-compose.yml up -d --force-recreate linux-ubuntu`
- 改了 Dockerfile：`up -d --build`（不删数据，仅在需要干净状态时配合 down -v）

**4. srlinux 迟迟不 healthy**
SR Linux mgmt server 冷启动约 1-2 分钟，属正常；`docker logs ntbx-lab-srlinux` 看进度。若宿主机内存不足被 OOM，调低其他容器占用或提升 `mem_limit`。

**5. centos7 容器没有 systemd（systemctl 类条目怎么办）**
centos7 的 systemd（219）不支持 cgroup v2：在 cgroup v2 宿主（现代 Docker 默认）上 PID 1 存活但**任何服务都起不来**（实测 journald/sshd 全部缺位），属结构性不兼容，无法绕过。因此本实验室的 centos7 容器由 **sshd 前台直跑**（compose `command: /usr/sbin/sshd -D`），ssh 登录、yum、ss/tcpdump 等排查条目全部可用；`systemctl` 类条目在该容器**不可验证**——按语法核对档（不判绿）或走真机，`systemctl is-active` 类查询会报 "Failed to get D-Bus connection: Operation not permitted"。Ubuntu 22.04 容器（systemd 249）无此限制，systemctl 类条目可在 ubuntu 容器验证。需要干净环境直接 `down -v` 重建。
