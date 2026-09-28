# 神州数码（DCN 网络设备） 命令种子库占位目录

本目录预留给该厂商的命令条目，目前**暂无内容**（本轮只建目录占位）。

## 怎么往这里加条目

1. 在本目录下新建 `<任意名字>.json`，结构与 seed_data/cisco.json 完全一致：

```json
{
  "format": "netcmdl - command library",
  "schema": 1,
  "count": 1,
  "entries": [
    {
      "uuid": "",
      "device_type": "防火墙",
      "vendor": "神州数码",
      "os_family": "具体 OS 版本族",
      "models": "适用型号",
      "category": "VLAN",
      "title": "条目标题",
      "description": "一句话说明",
      "commands": "命令全文，参数占位写 {{vlan_id:10}}",
      "params": [
        {"name": "vlan_id", "label": "VLAN ID", "default": "10",
         "required": true, "validate": "vlan", "example": "10"}
      ],
      "notes": "坑点备注，一行一条",
      "verified": 0
    }
  ]
}
```

2. 也可以在程序里用『命令库管理器 → 新建条目』界面化录入，然后
   『导出 .nlb』，把导出的文件放进本目录即可（导入逻辑同样会读它）。

## 注意

- `uuid` 留空即可，导入时程序会自动生成；**同一个 UUID 会被合并去重**，冲突时保留"已验证"那条。
- 所有条目导入后一律为 `verified=0`（灰色徽章），必须经真机验证后手动标记为已验证。
- 种子库目录支持子目录，程序会递归扫描本目录下的 `.json` 文件。

生成方式建议：先在测试设备上敲通命令 → 再录入 → 标记已验证。**不要在未验证前用于生产设备。**
