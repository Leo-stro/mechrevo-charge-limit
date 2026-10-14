<#
  probe_charge_channels.ps1 —— 机械革命 / 同方系笔记本「电池充电上限」通道只读探测

  这个脚本只做三件事：报告机型、试打开免管理员 EC 通道并只读几个寄存器、枚举厂商 ACPI-WMI 电池类。
  全程不写任何寄存器、不写注册表、不改电源设置。

  用法（建议用管理员 PowerShell 跑，信息最全）：
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/probe_charge_channels.ps1

  开 issue 报机型时，把本脚本的完整输出贴上来即可。
#>

$ErrorActionPreference = 'SilentlyContinue'

function Say([string]$s) { Write-Host $s }
function Ok([string]$s)  { Write-Host "  [OK]   $s" -ForegroundColor Green }
function Bad([string]$s) { Write-Host "  [--]   $s" -ForegroundColor DarkGray }

$BS = [char]92          # 反斜杠：设备/命名空间路径一律用它拼，避免转义歧义
$devPaths = @()
$devPaths += ('' + $BS + $BS + '.' + $BS + 'ACPIDriver')
$devPaths += ('' + $BS + $BS + '.' + $BS + 'ACPIH')
$inouId = 'ACPI' + $BS + 'INOU0000'
$wmiNs  = 'root' + $BS + 'WMI'

# --- Win32 常量 ---
# 注意：PowerShell 会把 0xC0000000 这类超过 Int32 的字面量解析成负的 Int32，
# 直接 [uint32] 转换会抛异常（异常被吞掉后参数变 0，表现为「打不开设备」）。用字符串解析规避。
$GENERIC_RW    = [Convert]::ToUInt32('C0000000', 16)
$OPEN_EXISTING = [Convert]::ToUInt32('3', 16)
$SHARE_RW      = [Convert]::ToUInt32('3', 16)
$IOCTL_EC_READ = [Convert]::ToUInt32('9C40A488', 16)

Say "=============================================="
Say " 0) 机型信息（开 issue 请贴这一段）"
Say "=============================================="
$cs   = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS
Say ("  厂商     : " + $cs.Manufacturer)
Say ("  型号     : " + $cs.Model)
Say ("  平台串   : " + $cs.SystemFamily)
Say ("  BIOS     : " + $bios.SMBIOSBIOSVersion)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Say ("  管理员   : " + $isAdmin)

Say ""
Say "=============================================="
Say (" 1) 通道 A：Uniwill 免管理员驱动通道（" + $devPaths[0] + "）")
Say "    能读回值 = 本项目可以直接用（不需要管理员）"
Say "=============================================="
$pnp = @(Get-PnpDevice | Where-Object { $_.InstanceId -like ($inouId + '*') })
if ($pnp.Count -gt 0) { Ok ($inouId + " 设备存在（状态: " + (($pnp | ForEach-Object { $_.Status }) -join ',') + "）") }
else { Bad ($inouId + " 设备不存在 —— 这台机器的 ACPI 里没有 Uniwill H2RAM 接口") }

$drvSys = Join-Path $env:SystemRoot ('System32' + $BS + 'drivers' + $BS + 'UWACPIDriver.sys')
if (Test-Path $drvSys) { Ok "UWACPIDriver.sys 已安装" }
else { Bad "UWACPIDriver.sys 未找到 —— 装一次官方机械革命控制中心后它才会出现" }

Add-Type -Namespace EcProbe -Name Native -MemberDefinition @"
[DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
public static extern IntPtr CreateFileW(string lpFileName, uint dwDesiredAccess, uint dwShareMode, IntPtr lpSecurityAttributes, uint dwCreationDisposition, uint dwFlagsAndAttributes, IntPtr hTemplateFile);
[DllImport("kernel32.dll", SetLastError=true)]
public static extern bool DeviceIoControl(IntPtr hDevice, uint dwIoControlCode, byte[] lpInBuffer, uint nInBufferSize, byte[] lpOutBuffer, uint nOutBufferSize, out uint lpBytesReturned, IntPtr lpOverlapped);
[DllImport("kernel32.dll", SetLastError=true)]
public static extern bool CloseHandle(IntPtr hObject);
"@

function Read-EcOne([string]$path, [uint32]$addr) {
  $h = [EcProbe.Native]::CreateFileW($path, $GENERIC_RW, $SHARE_RW, [IntPtr]::Zero, $OPEN_EXISTING, [uint32]0, [IntPtr]::Zero)
  if ($h -eq [IntPtr]::Zero -or $h.ToInt64() -eq -1) {
    return @{ ok = $false; stage = 'open'; err = [Runtime.InteropServices.Marshal]::GetLastWin32Error(); val = -1 }
  }
  try {
    $in  = [BitConverter]::GetBytes($addr)
    $out = New-Object byte[] 4
    $ret = [uint32]0
    $okIo = [EcProbe.Native]::DeviceIoControl($h, $IOCTL_EC_READ, $in, 4, $out, 4, [ref]$ret, [IntPtr]::Zero)
    if ($okIo) { return @{ ok = $true; stage = 'ioctl'; err = 0; val = [int]$out[0] } }
    return @{ ok = $false; stage = 'ioctl'; err = [Runtime.InteropServices.Marshal]::GetLastWin32Error(); val = -1 }
  } finally { [void][EcProbe.Native]::CloseHandle($h) }
}

function Hex2($v) { if ($v -lt 0) { '??' } else { '{0:X2}' -f $v } }

$chanA = $false
foreach ($p in $devPaths) {
  $r = Read-EcOne $p ([uint32]0x0740)
  if ($r.ok) {
    Ok ("打开 " + $p + " 成功，只读 0x0740 = 0x" + (Hex2 $r.val) + "  → 通道可用，免管理员")
    $chanA = $true
    $v703 = (Read-EcOne $p ([uint32]0x07C3)).val
    $v770 = (Read-EcOne $p ([uint32]0x0770)).val
    $v7b9 = (Read-EcOne $p ([uint32]0x07B9)).val
    $v742 = (Read-EcOne $p ([uint32]0x0742)).val
    Say ("          只读快照: 0x07C3 = 0x" + (Hex2 $v703) + " , 0x0770 = 0x" + (Hex2 $v770) + " , 0x07B9 = 0x" + (Hex2 $v7b9) + " , 0x0742 = 0x" + (Hex2 $v742))
    Say  "          （这几个就是回滚要存档的原值；本脚本只读，不会写）"
    break
  } else {
    Bad ("无法使用 " + $p + "（阶段: " + $r.stage + "，Win32 错误码: " + $r.err + "）")
  }
}

Say ""
Say "=============================================="
Say (" 2) 通道 B：厂商 ACPI-WMI 电池接口（走 " + $wmiNs + "）")
Say "    枚举类名不需要管理员；读/写值需要管理员"
Say "=============================================="
$chanB = $false
$classes = @()
try {
  $classes = @(Get-CimClass -Namespace $wmiNs | Where-Object { $_.CimClassName -match 'Battery|Charge|EmdAcpi|AcpiTest|Rationing' })
} catch { }
if ($classes.Count -gt 0) {
  foreach ($c in $classes) {
    $m = @($c.CimClassMethods.Keys)
    if ($m.Count -gt 0) { Say ("  * " + $c.CimClassName + "    方法: " + ($m -join ', ')) }
    else { Say ("  * " + $c.CimClassName) }
    if ($c.CimClassName -match 'BatteryChargeRationing|Battery_Charge_Mode|AcpiTest_MULong') { $chanB = $true }
  }
  if ($chanB) { Ok "找到厂商电池 WMI 类 → 这台机器可以走 WMI 路线（需管理员）" }
  else { Bad "列出的类里没有已知的电池充放电控制类" }
} else {
  Bad ("在 " + $wmiNs + " 里没有匹配 Battery / Charge / EmdAcpi / AcpiTest 的类")
}

Say ""
Say "=============================================="
Say " 3) 结论"
Say "=============================================="
if ($chanA) {
  Say "  通道 A 可用 → 直接用本项目（免管理员，设置写进固件后重启不丢）。"
  Say "  下一步: 只读存档 0x0770 / 0x07C3 / 0x07B9 原值，再按 README 的写法设上限。"
} elseif ($chanB) {
  Say "  通道 A 不可用，但有厂商电池 WMI 类 → 走 WMI 路线："
  Say "  * 必须【先枚举实例，再在实例上 Invoke-CimMethod】，类级调用会报「无效的方法参数」；"
  Say "  * 全程需要管理员；"
  Say "  * 例: EmdAcpi_BatteryChargeRationing（GUID 6B40A935-...）"
  Say ("      Get : Invoke-CimMethod -InputObject (Get-CimInstance -Namespace " + $wmiNs + " -ClassName EmdAcpi_BatteryChargeRationing) -MethodName GetBatteryChargeRationing -Arguments @{Data=[uint64]0}")
  Say "      Set : ... -MethodName SetBatteryChargeRationing -Arguments @{Data=[uint64]80}    # 80 = 上限%"
  Say "  * 已知实测: 某 WUJIE14SE(7445HS) 上 Data = 上限百分比 0-100，0 = 充满；其他机型请先只读确认，别照搬。"
} else {
  Say "  两个通道都没有 → 这台机器不在本项目的适用范围里。"
  Say "  常见原因: ACPI 里没有 INOU0000 / ECRR / ECRW，限充由另一套体系实现"
  Say "  （例: 机械革命星耀14 = Insyde 固件 + ITE PMC2 端口 0x68/0x6C 的 T1/T2 双阈值，"
  Say "   读写需要 Ring0 端口访问，没有免驱动的 WMI 路）。"
  Say "  思路见 README「找不到 EC 怎么办」一节；不要盲刷 BIOS / EC。"
}
Say ""
Say "本脚本为只读探测，未向任何寄存器写入。"
