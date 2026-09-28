# placeholders/ —— 待扩充厂商占位目录

本目录存放"只建导航、暂无种子数据"的厂商占位。每个子目录对应该厂商未来
种子 JSON 的落位（文件名建议与目录名一致，如 `hillstone/hillstone.json`）。

> **扩充原则**：按《审计报告-种子库与主库覆盖度-20260924.md》任务4 缺口清单
> 滚动扩充，**暂不排期**。哪一家进入了实际交付范围，就先补哪一家。

## 厂商清单与定位

| # | 目录 | 厂商 | 定位归类 | 建议扩充依托场景 | 备注 |
|---|------|------|----------|------------------|------|
| 1 | anheng/ | 安恒信息（恒脑/明御系列） | 安全设备（防火墙/WAF/堡垒机等） | 防火墙树组 + 离网交付场景 | 交付常用产品线，规格**待确认**（按实际接入产品定） |
| 2 | arista/ | Arista（EOS） | 交换机（数据中心/园区） | 网络树组（renderer 已内置其接口命名规则 Et1） | EOS 命令风格接近 Cisco IOS |
| 3 | checkpoint/ | Check Point（Gaia） | 防火墙 | 防火墙树组（`db.TREE_HINT_GROUPS["firewall"]` 扩入即可） | Gaia CLISH 风格独特，需单独 CLI 骨架 |
| 4 | digitalchina/ | 神州数码（DCN） | 交换机/路由器 | 网络树组 | DCRS/DCNOS 命令风格接近 Cisco IOS |
| 5 | hillstone/ | 山石网科（StoneOS） | 防火墙 | 防火墙树组 | StoneOS CLI 骨架 + Web 对照 |
| 6 | maipu/ | 迈普（MyPower） | 路由器/交换机 | 网络树组 | 命令风格接近 Cisco IOS，**待确认**（以实际到手型号为准） |
| 7 | nsfocus/ | 绿盟（SAS/NIPS） | 安全设备（抗D/IPS/扫描器） | 防火墙树组 / 离网特供 | Web 控制台为主，CLI 覆盖度**待确认** |
| 8 | qianxin/ | 奇安信（NGFW/椒图等） | 安全设备 | 防火墙树组 | 依产品线定，**待确认** |
| 9 | venustech/ | 启明星辰（天阗/泰合） | 安全设备（IDS/IPS/SOC） | 防火墙树组 / 排查命令 | Web 控制台为主 |

## 约定

1. **分组挂靠**：排查树 vendor_hint 统一引用 `db.py → TREE_HINT_GROUPS` 三组：
   - `network`：cisco / huawei / h3c / ruijie / zte / juniper
   - `linux`：centos / ubuntu / kylin / openeuler
   - `firewall`：fortinet / paloalto / sangfor / topsec（**组已建好，暂无树**；
     新厂商进入后把 slug 追加进该组，防火墙树直接挂靠）
2. **数据格式**：与现有种子一致（schema 2：`format/schema/vendor_file/vendor_label/
   vendor_slug/verified_policy/entries|err_dict|trouble_trees`）。
3. **质量门槛**：新增条目后必跑 `python main.py --validate-seed`，全量通过才算入库。
4. **骨架条目**：未经真机核对的 CLI 命令标 `exec_level: "skeleton"`，commands 内
   注明"待真机核对"，UI 会以橙色『骨架』徽章标识并在首次复制时提醒。
