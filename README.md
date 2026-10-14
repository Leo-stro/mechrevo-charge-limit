# 机械革命 / 同方笔记本 电池充电上限（Windows）

> 机械革命控制台里的「电池保护模式」设了不生效？想在 Windows 上让电池**插电只充到 50%/60%**？
> 这个项目用**纯软件**做到，且**写入后重启、关机、拔电源都不丢**——不需要开机自启、不需要常驻进程、不需要管理员权限。

适用机型：**采用 Uniwill（同方 / TongFang）EC 固件的笔记本** —— 机械革命（Mechrevo）、XMG、TUXEDO、Eluktronics，以及神舟 / 火影等部分同方模具机型。

实测机型：**机械革命 无界15X（WUJIE15XA，Ryzen 7 8745HS，BIOS N.1.14MRO50）**

> ⚠️ 机械革命内部平台并不统一：星耀 14、无界 14 Pro 等机型的 EC 体系与本项目不同，先看下文「找不到 EC？先分清你属于哪一类」自查，别直接照抄寄存器表。

![界面截图](assets/screenshot.png)

---

## 问题：官方开关为什么不管用

- 机械革命控制中心的「电池保护模式」只有三档：长效 100% / 均衡 80% / 工作站 60%，**给不了 50%**；
- 更麻烦的是，**很多机型上设置了完全没效果**，电池照样充到 100%。

根因在 EC 固件的充电控制循环里（逆向伪代码，来自 [w568w](https://gist.github.com/w568w/957976b59906e0ce5d6c13ad342e1593)）：

```c
bool state_is(uint8_t v) { return xram[0x07C3] == v || xram[0x0770] == v; }

bool limit_enabled = state_is(4) || state_is(5);   // 产品线门控
if (!limit_enabled) {
    uint8_t stored = xram[0x087F] & 0x7f;           // 固件存储区
    if (stored == 0 || stored > 100) { disable_control(); return; }
}
enable_control();   // 使用实时上限 xram[0x07B9]
store_limit();      // 实时上限 → 存储区
```

固件只对 `0x07C3`（平台值）或 `0x0770`（ROM ID 首字节）为 4/5 的产品线开放限充。
平台值不匹配 + ROM ID 是出厂空槽 `0xFF` 时，**门控永远不成立，`0x07B9` 写了也没人执行**。

## 解决思路：不要钉门控，要触发固件存储

常见做法是硬钉 `0x0770 = 0x04` 骗过门控（[losewayy 的项目](https://github.com/losewayy/uniwill-ec-charge-limit)），但这条路：

- 改了「机型身份字节」，有副作用风险
- 值**易失**，必须常驻 watcher 或开机重放

本项目走另一条路：**让门控条件临时成立几秒，固件就会把实时上限抄进存储区；之后门控由存储值自己维持**。用完即还，零残留：

```
1. 写 0x07B9 = <上限%>                # 设定实时上限
2. 写 0x07C3 = 0x04,保持 2.5 秒        # 临时让门控成立,固件趁机 store_limit()
3. 写回 0x07C3 = 原值（本机 0x07）     # 用完即还
4. 等 7 秒后读 0x0742 bit2 = 1 → 成功
```

### 实测持久性

| 场景 | 结果 |
|---|---|
| 重启 | ✅ 保持 |
| 关机 + 拔电源 + 断电 10 分钟 | ✅ 保持 |
| 之后插电，从 48% 充起 | ✅ 精确停在 50%（`Charging=False` / `ChargeRate=0`） |
| 需要后台程序吗 | ❌ 完全不需要（实测全程无任何软件运行） |
| 需要管理员权限吗 | ❌ 不需要 |
| 动过身份字节吗 | ❌ 没有，`0x0770` 始终是 `0xFF` |

> 注：EC 由电池持续供电，所以只有**电池被放到彻底没电**时才会丢——那种情况下重新点一次按钮即可。

## 使用

```powershell
pythonw 电池充电上限.pyw
```

界面提供：

- 状态四格：当前电量 / 充电状态 / 设定上限 / 限充门控
- 档位按钮：50 / 60 / 70 / 80 / 90 / 100 %
- 「取消限充（恢复充满）」：一键清除实时上限与固件存储值，电池恢复充电（实测 12 秒内 `ChargeRate=34034 mW`）

**依赖**：Python 3（标准库即可，需要 tkinter）+ 一条能访问 EC 的通道（见下节）。

## 两条访问通道（决定你要装什么）

| 通道 | 需要什么 | 可写地址 | 本机实测 |
|---|---|---|---|
| **`\\.\ACPIDriver`（H2RAM）** ← 本项目默认 | 机械革命控制中心提供的 `UWACPIDriver.sys`，**免管理员** | `0x0700–0x07FF` | ✅ 精确停充、可逆、重启不丢 |
| **ACPI WMI 邮箱**（`root\WMI` → `AcpiTest_MULong.GetSetULong`） | **管理员权限**，不需要 OEM 驱动 | 含 `0x0800` 以上（如存储区 `0x087F`） | ✅ 只读已实测（读回 `0x087F = 50`，与驱动通道逐项吻合）；写入路径**未验证** |

第二条来自固件自带的官方 WMI 邮箱（Uniwill 的 AMW0/WMBC 链路，Linux 内核驱动走的就是它）。
理论上它更强 —— 能直接读写存储区 `0x087F`，不必用「临时开门控 2.5 秒」的技巧 —— 但它需要管理员权限，
所以本项目默认不走。实测它读回的 `0x087F = 50`，**直接印证了本方法的原理**：上限确实被固件写进了存储区，
所以才能跨重启持久。详见 [`docs/FINDINGS.md`](docs/FINDINGS.md) 第 6 节。

### 那个驱动从哪来（请不要转发）

- **官方**：机械革命控制中心。按机型提供，从机械革命官网下载中心选你的型号下载；装上它，
  `\\.\ACPIDriver` 才会出现。
- **开源替代**：[OpenRevo](https://github.com/faintonce/open-revo) —— 轻量控制中心，宣称零官方组件依赖。
- ❌ **不要把本机的 `UWACPIDriver.sys` 转发给别人**：它是 OEM 专有、带签名的**内核驱动**，
  授权只随你购买的那台整机。转发既有版权问题，也让对方背上「安装来路不明内核驱动」的风险。
  让对方自己装控制中心，或者走上面的 WMI 通道。

## 找不到 EC？先分清你属于哪一类

评论区最常出现的反馈是「按 README 走，在我机器上找不到 EC」——**多半不是操作问题，而是机型平台不统一**。同为机械革命，模具和 EC 体系并不是一套：

| 你的机器 | 平台 | 能否直接用本项目 | 判据 |
|---|---|---|---|
| 无界 15X（本项目实测机） | Uniwill/同方 + AMI BIOS | ✅ 免管理员 | ACPI 有 `INOU0000`，`\\.\ACPIDriver` 打得开 |
| 无界 14 SE（7445HS）等同方/Emdoor 血统 | 同系 EC，但 Windows 下**没有** `INOU0000` 设备 | ⚠️ 改走厂商 ACPI-WMI（需管理员） | `ACPI\PNP0C14\HWMI` 下有 `EmdAcpi_BatteryChargeRationing` 等类 |
| 无界 14X（GX4HRXL / 14XA） | Uniwill，EC XRAM 走独立的 4 KiB MMIO 窗口 | ⚠️ 寄存器语义相近、通道不同 | 有 `INOU0000`，但访问路径要自己解决 |
| 无界 14 Pro（WUJIE14 Series-HPT） | 固件只走 `WMID.WMAA`，DSDT 里没有 `INOU` | ❌ 大概率用不了（未见实测成功记录） | 先跑下面的探测脚本 |
| 机械革命 星耀 14（P916F-STX） | **另一套体系**：Insyde H2O 固件 + ITE IT5571 EC | ❌ 用不了 | ACPI 里没有 `INOU0000` / `ECRR` / `ECRW`；限充是 ITE PMC2 命令族的 T1/T2 双阈值（EC XRAM `0x0D13` / `0x0D14`，端口 I/O `0x68` / `0x6C`），需要内核态端口访问 |

> 星耀 14 一行来自第三方逆向记录（[YMGPwcca/Mechrevo-Xingyao-14](https://github.com/YMGPwcca/Mechrevo-Xingyao-14)）：该记录明确否证了「通用 Uniwill 偏移 `0x07B9`/`0x07D0` 适用于本机」和「存在 `INOU0000`/`ECRR`/`ECRW`」两条假设。无界 14 Pro 一行属于**推断**（同批机型的公开资料里都没有 Windows 免驱动通道），尚未见实测报告。

### 30 秒自查（只读，不改任何东西）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/probe_charge_channels.ps1
```

输出三段：机型信息 → 免管理员通道能否打开（能读回 `0x0740` 就说明能用）→ `root\WMI` 里有没有厂商电池类。**它不写任何寄存器、不写注册表、不改电源设置。** 开 issue 报机型时，把整段输出贴上来即可。

### 没有 EC 通道时，思路按这个顺序走

1. **先看厂商 ACPI-WMI 电池类**（探测脚本第 2 段）。
   - 有 `EmdAcpi_BatteryChargeRationing`（GUID `6B40A935-...`）→ 走它。要点：**必须先枚举实例、再在实例上 `Invoke-CimMethod`**（类级调用会报「无效的方法参数」），且需要管理员权限。已实测的编码（WUJIE14SE / 7445HS）：`SetBatteryChargeRationing(Data)` 的 `Data` 就是上限百分比 0–100，`0` = 充满 —— 与 EC `0x07B9` 的语义一致。其他机型请先只读确认，别照搬数字。
   - 有 `AcpiTest_MULong.GetSetULong` → 就是本项目这台机器的第二通道（可读写 `0x0800` 以上的存储区，例如 `0x087F`）。
2. **看官方控制中心是不是「有实现、没入口」**。多台机器的官方 `GCUService` 里其实实现了 `BatteryProtection2`（含 `Set|ReadBatteryChargingLimit_Up` / `_Down`、`CHARGING_UP_LIMIT` / `DOWN_LIMIT` / `RECOVERY` 一类命令），只是 UI 没给这台机型开。这种情况下「设了不生效」多半是入口被隐藏，或者写入被 EC 门控静默吃掉 —— 值得顺这条线挖。
3. **回到 EC 直写的前提**：ACPI 里有 `INOU0000`，**并且**有一条能到 H2RAM 的通道（官方驱动提供的 `\\.\ACPIDriver`，或 WMI 邮箱）。两个前提缺一个，就不是「换个参数」能解决的。
4. **不属于本项目、但方向要讲清**：像星耀 14 那样把限充做在 ITE PMC2 端口上的机型，Windows 下写这类寄存器需要内核态端口 I/O（厂商驱动或 Ring0 工具）——本项目不做这件事，也不建议普通用户自己上。Linux 侧已有完整的逆向记录可参考。
5. **不要做的事**：不要为了限充盲刷 BIOS / EC；不要把别人的 OEM 内核驱动转来转去；网上那种「万能 EC 写工具」（WinRing0 一类）既有签名/授权问题，也可能把充电参数写坏。Linux 内核文档明确警告：部分机型阈值接口实现有缺陷，强行启用**可能损伤电池**。

> 有实测结果欢迎开 issue，附上 `tools/probe_charge_channels.ps1` 的完整输出（机型 + 通道 A/B 结论），这样兼容表能一起补全。

## 给 AI agent 的落地说明

> 这一节写给「把仓库地址丢给 agent、让它自己动手」的场景。人用请直接看上面的图形界面工具。

### 0. 先自查，不要跳过

| 检查项 | 通过条件 |
|---|---|
| 机型 | 采用 Uniwill/同方 EC（机械革命 / XMG / TUXEDO / Eluktronics / 部分神舟火影）。**其他品牌到此为止** |
| 访问通道 | `\\.\ACPIDriver` 能打开（免管理员），或以管理员身份能取到 `AcpiTest_MULong` 实例 |
| 只读探测 | 能读出 `0x0770` / `0x07C3` / `0x07B9` / `0x0742` |

三条都过再往下。**不要盲写寄存器** —— 先只读，再存档，最后才写。

### 1. 最小执行顺序

1. **先读并存档原值**：`0x0770`、`0x07C3`、`0x07B9`（回滚要用）
2. 写 `0x07B9 = 目标百分比`（50 → `0x32`）
3. 写 `0x07C3 = 0x04`，保持 2.5 秒，**然后恢复原值**（本机为 `0x07`）
4. 等 7 秒，读 `0x0742` bit2 = 1 → 门控已开
5. 验证：把电量放到上限以下 → 插电 → 应精确停在目标值（`Charging=False`、`ChargeRate=0`）
6. **取消时必须写 `0x07B9 = 0` 并再触发一次存储** —— 只关门控、不清上限，会让电池卡住不充电

完整寄存器语义、门控伪代码、踩坑清单见 [`docs/FINDINGS.md`](docs/FINDINGS.md)。

### 2. 文件直链（中文文件名必须 URL 编码）

```
README.md     https://raw.githubusercontent.com/Leo-stro/mechrevo-charge-limit/main/README.md
逆向记录       https://raw.githubusercontent.com/Leo-stro/mechrevo-charge-limit/main/docs/FINDINGS.md
主程序         https://raw.githubusercontent.com/Leo-stro/mechrevo-charge-limit/main/%E7%94%B5%E6%B1%A0%E5%85%85%E7%94%B5%E4%B8%8A%E9%99%90.pyw
整仓 ZIP       https://github.com/Leo-stro/mechrevo-charge-limit/archive/refs/heads/main.zip
自查脚本       https://raw.githubusercontent.com/Leo-stro/mechrevo-charge-limit/main/tools/probe_charge_channels.ps1
```

（未编码的中文路径会直接报错 —— 这是最容易踩的一脚。）

## 取消 / 还原

点界面上的「取消限充（恢复充满）」即可。它会：

1. 把 `0x07B9` 写 0
2. 再走一次"临时开门控"，让固件把「无限制」也写进存储区
3. 门控自动回落 → 恢复原厂行为

**不需要管理员权限、不需要重启、不留残留。**

> ⚠️ 只写 `0x0770 = 0xFF`（或只关任一门控条件）而**不清 `0x07B9`** 是不够的：`0x07B9` 残留旧值会让固件继续认为"已达上限"，表现是**电池完全不充电**（实测插电挂 30 分钟、`ChargeRate` 一直是 0）。这个坑已写进代码。

## 适用与不适用

| | 说明 |
|---|---|
| ✅ 适用 | 采用 Uniwill/同方 EC 的机型（机械革命、XMG、TUXEDO、Eluktronics、部分神舟/火影） |
| ⚠️ 需先探测 | 同产品线不同型号的寄存器语义可能微调，**应先只读探测再写入**（见 `docs/FINDINGS.md`） |
| ❌ 不适用 | 联想 / 戴尔 / 惠普 / 华硕 / 宏碁 / 苹果等 —— EC 固件完全不同，寄存器地址表对不上。这类机型请用原厂工具（Lenovo Vantage、Dell Power Manager、MyASUS 等） |
| ⛔ 同品牌也可能不适用 | 机械革命内部平台不统一：星耀 14 是 Insyde 固件 + ITE PMC2（另一套体系），无界 14 Pro / 14 SE 在 Windows 下没有 `INOU0000` 通道。**先跑 [`tools/probe_charge_channels.ps1`](tools/probe_charge_channels.ps1) 自查**（见上文「找不到 EC？」一节） |

## 风险与免责

- 本方案写入的是**未公开的 EC 寄存器**，属于社区逆向成果，非官方支持的功能。
- Linux 内核文档对同类操作有明确警告：*某些设备未正确实现充电阈值接口，强行启用**可能损坏电池***。本项目已在 WUJIE15XA 上实测正常（精确停充、可逆、温度风扇无异常），但**长期风险无法保证**。
- 写入的都是易失 RAM（存储区除外），**不刷固件、不碰闪存**。
- 作者不对任何硬件损坏、数据丢失负责，操作自负风险。

## 致谢

- [w568w](https://gist.github.com/w568w/957976b59906e0ce5d6c13ad342e1593) —— EC 固件充电逻辑逆向的奠基工作（`0x07C3` / `0x0770` / `0x087F` 语义原始出处）
- [losewayy/uniwill-ec-charge-limit](https://github.com/losewayy/uniwill-ec-charge-limit) —— 门控钉扎方案与 IOCTL 协议
- [ArchWiki: Mechrevo WUJIE14X](https://wiki.archlinux.org/title/Mechrevo_WUJIE14X) —— 临时改 `0x07C3` 触发存储的原始做法
- [tuxedocomputers/tuxedo-drivers](https://github.com/tuxedocomputers/tuxedo-drivers) —— WMI mailbox 协议参考
- [Linux `uniwill-laptop` 驱动](https://docs.kernel.org/next/admin-guide/laptops/uniwill-laptop.html) —— 权威寄存器表与功能位定义

## 许可

MIT
