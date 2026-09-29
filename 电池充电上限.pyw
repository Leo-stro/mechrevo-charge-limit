# -*- coding: utf-8 -*-
"""电池充电上限 — 机械革命/同方(Uniwill)笔记本 EC 充电上限设置。

最终方案(已实测持久化):
  1) 写 0x07B9 = <上限%>           设置实时上限
  2) 临时写 0x07C3 = 0x04 停 2.5 秒再恢复原值
     —— 让固件在这几秒里把实时上限抄进存储区(0x087F),
        之后门控由存储值维持,重启/关机/拔电源都不丢,不需要任何后台程序。
  取消:上限写 0,再走一次同样的"临时开门控"让存储值也归零,门控自动回落。

注意(踩过的坑):
  - 判断是否生效要看 0x0742 的 bit2,但它有滞后,写入后需等几秒再读;
  - 取消时若只关 0x0770 而不清 0x07B9,电池会被卡住不充电;
  - 本程序不写 ROMID(0x0770),不改任何身份字节。
"""
import ctypes
import struct
import sys
import time
import tkinter as tk

# ---------- EC 访问 ----------
IOCTL_EC_READ = 0x9C40A488
IOCTL_EC_WRITE = 0x9C40A48C
DEVICE_PATHS = ["\\\\.\\ACPIDriver", "\\\\.\\ACPIH"]
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x3
OPEN_EXISTING = 3

REG_ROMID = 0x0770
REG_PLATFORM = 0x07C3
REG_GATE = 0x0742
REG_LIMIT = 0x07B9
STORE_MAGIC = 0x04        # 临时写入的值:让固件的门控条件成立
STORE_HOLD_SEC = 2.5      # 保持时长:给固件控制循环(每秒一次)留足执行时间
GATE_SETTLE_SEC = 7.0     # 之后等待门控位更新

LIMIT_CHOICES = [50, 60, 70, 80, 90, 100]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.DeviceIoControl.restype = ctypes.c_bool
kernel32.CloseHandle.restype = ctypes.c_bool


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def _open_driver():
    for path in DEVICE_PATHS:
        h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
        if h and h != ctypes.c_void_p(-1).value:
            return h
    return None


def _ec_read(h, addr):
    inb = (ctypes.c_ubyte * 4).from_buffer_copy(struct.pack("<I", addr))
    outb = (ctypes.c_ubyte * 4)()
    ret = ctypes.c_ulong(0)
    if not kernel32.DeviceIoControl(h, IOCTL_EC_READ, inb, 4, outb, 4,
                                    ctypes.byref(ret), None):
        raise OSError(ctypes.get_last_error())
    return struct.unpack("<I", bytes(outb))[0] & 0xFF


def _ec_write(h, addr, val):
    inb = (ctypes.c_ubyte * 8).from_buffer_copy(struct.pack("<II", addr, val))
    outb = (ctypes.c_ubyte * 4)()
    ret = ctypes.c_ulong(0)
    kernel32.DeviceIoControl(h, IOCTL_EC_WRITE, inb, 8, outb, 4,
                             ctypes.byref(ret), None)


def read_state():
    st = {"error": None, "pct": None, "ac": None, "charging": None,
          "limit": None, "gate": None, "romid": None, "platform": None}
    sps = SYSTEM_POWER_STATUS()
    if kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
        st["pct"] = sps.BatteryLifePercent if sps.BatteryLifePercent != 255 else None
        st["ac"] = sps.ACLineStatus == 1
        st["charging"] = bool(sps.BatteryFlag & 8) and sps.BatteryFlag != 255
    h = _open_driver()
    if h is None:
        st["error"] = "无法打开 EC 通道(未找到 UWACPIDriver 设备)"
        return st
    try:
        st["limit"] = _ec_read(h, REG_LIMIT) & 0x7F
        st["gate"] = (_ec_read(h, REG_GATE) >> 2) & 1
        st["romid"] = _ec_read(h, REG_ROMID)
        st["platform"] = _ec_read(h, REG_PLATFORM)
    except OSError as exc:
        st["error"] = f"EC 读取失败(err={exc})"
    finally:
        kernel32.CloseHandle(h)
    return st


def _store_trigger(h):
    """临时开门控 2.5 秒,让固件把当前实时上限抄进存储区;然后恢复平台值。

    返回 (是否成功恢复, 恢复后的平台值)。
    """
    if _ec_read(h, REG_PLATFORM) in (STORE_MAGIC, 5):
        return True, _ec_read(h, REG_PLATFORM)      # 门控本来就开着,无需临时改动
    original = _ec_read(h, REG_PLATFORM)
    _ec_write(h, REG_PLATFORM, STORE_MAGIC)
    time.sleep(STORE_HOLD_SEC)
    _ec_write(h, REG_PLATFORM, original)
    time.sleep(0.3)
    restored = _ec_read(h, REG_PLATFORM)
    if restored != original:                        # 恢复失败重试一次
        _ec_write(h, REG_PLATFORM, original)
        time.sleep(0.5)
        restored = _ec_read(h, REG_PLATFORM)
    return restored == original, restored


def apply_limit(limit):
    """设置上限:写实时上限 -> 触发固件存储 -> 等门控置位。返回 (成功?, 说明)"""
    h = _open_driver()
    if h is None:
        return False, "无法打开 EC 通道"
    try:
        _ec_write(h, REG_LIMIT, limit & 0x7F)
        ok, plat = _store_trigger(h)
        if not ok:
            return False, f"平台寄存器(0x07C3)恢复失败,当前值 {plat:#04x},请手动检查"
        time.sleep(GATE_SETTLE_SEC)
        gate = (_ec_read(h, REG_GATE) >> 2) & 1
        live = _ec_read(h, REG_LIMIT) & 0x7F
        if gate != 1:
            return False, "写入完成,但门控未置位(固件可能未响应),请重试一次"
        if live != limit:
            return False, f"上限被固件改成了 {live}%,未达到 {limit}%"
        return True, f"已设为 {limit}% 并写入固件(重启/关机都不失效)"
    except OSError as exc:
        return False, f"EC 写入失败(err={exc})"
    finally:
        kernel32.CloseHandle(h)


def clear_limit():
    """取消上限:实时上限归零 -> 触发存储 -> 等门控回落。返回 (成功?, 说明)"""
    h = _open_driver()
    if h is None:
        return False, "无法打开 EC 通道"
    try:
        _ec_write(h, REG_LIMIT, 0x00)
        ok, plat = _store_trigger(h)
        if not ok:
            return False, f"平台寄存器(0x07C3)恢复失败,当前值 {plat:#04x},请手动检查"
        time.sleep(GATE_SETTLE_SEC)
        gate = (_ec_read(h, REG_GATE) >> 2) & 1
        if gate == 1:
            return False, "已解除上限,但门控未回落;电池仍可能受限,请再点一次"
        return True, "已取消限充,电池恢复充满"
    except OSError as exc:
        return False, f"EC 写入失败(err={exc})"
    finally:
        kernel32.CloseHandle(h)


# ---------- 界面 ----------
BG = "#f5f6f8"
FG = "#333333"
MUTED = "#777777"
OK = "#1a7f37"
WARN = "#b45309"
BAD = "#b91c1c"
CARD = "#ffffff"
BORDER = "#dcdde1"
ACCENT = "#2f6feb"
FONT = "Microsoft YaHei UI"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("电池充电上限")
        self.configure(bg=BG)
        self.resizable(False, False)
        w = min(860, int(self.winfo_screenwidth() * 0.9))
        h = 540
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")
        self._busy = False
        self._flash = ""
        self._build()
        self.after(120, self.refresh)

    def _build(self):
        wrap = tk.Frame(self, bg=BG, padx=28, pady=16)
        wrap.pack(fill="both", expand=True)

        top = tk.Frame(wrap, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1, padx=20, pady=14)
        top.pack(fill="x")
        for c in (0, 2):
            top.grid_columnconfigure(c, minsize=95)
        for c in (1, 3):
            top.grid_columnconfigure(c, weight=1)
        self.vals = {}
        for r, (l1, k1, l2, k2) in enumerate([
                ("当前电量", "pct", "充电状态", "charge"),
                ("设定上限", "limit", "限充门控", "gate")]):
            tk.Label(top, text=l1, bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
                row=r, column=0, sticky="w", pady=4, padx=(0, 8))
            v1 = tk.Label(top, text="—", bg=CARD, fg=FG, font=(FONT, 13, "bold"))
            v1.grid(row=r, column=1, sticky="w", pady=4)
            tk.Label(top, text=l2, bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
                row=r, column=2, sticky="w", pady=4, padx=(28, 8))
            v2 = tk.Label(top, text="—", bg=CARD, fg=FG, font=(FONT, 13, "bold"))
            v2.grid(row=r, column=3, sticky="w", pady=4)
            self.vals[k1], self.vals[k2] = v1, v2

        tk.Label(wrap, text="选择充电上限", bg=BG, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w", pady=(16, 7))
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x")
        self.btns = {}
        for pct in LIMIT_CHOICES:
            b = tk.Button(row, text=f"{pct}%", width=7, font=(FONT, 10, "bold"),
                          relief="flat", bd=0, cursor="hand2",
                          command=lambda p=pct: self.on_apply(p))
            b.pack(side="left", padx=(0, 8), ipady=5)
            self.btns[pct] = b
        tk.Label(wrap, text="100% = 不限制充电(等同于取消)",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(5, 0))

        mid = tk.Frame(wrap, bg=BG, pady=14)
        mid.pack(fill="x")
        self.verdict = tk.Label(mid, text="", bg=BG, fg=FG, font=(FONT, 15, "bold"),
                                anchor="w", justify="left")
        self.verdict.pack(fill="x")
        self.detail = tk.Label(mid, text="", bg=BG, fg=MUTED, font=(FONT, 10),
                               anchor="w", justify="left", wraplength=780)
        self.detail.pack(fill="x", pady=(5, 0))

        bottom = tk.Frame(wrap, bg=BG)
        bottom.pack(side="bottom", fill="x")
        tk.Button(bottom, text="取消限充(恢复充满)", width=20, font=(FONT, 10),
                  relief="flat", bd=0, cursor="hand2", bg="#e8e8ea", fg=FG,
                  activebackground="#d8d8da", command=self.on_cancel).pack(side="left", ipady=4)
        tk.Button(bottom, text="刷新", width=10, font=(FONT, 10), relief="flat", bd=0,
                  cursor="hand2", bg="#e8e8ea", fg=FG, activebackground="#d8d8da",
                  command=self.refresh).pack(side="right", ipady=4)

    # ---- 动作 ----
    def _run(self, fn, *args):
        if self._busy:
            return
        self._busy = True
        try:
            self.verdict.config(text="正在写入固件,请稍候…", fg=MUTED)
            self.detail.config(text="")
            self.update_idletasks()
            ok, msg = fn(*args)
            self._flash = msg
        finally:
            self._busy = False
            self.refresh()

    def on_apply(self, limit):
        self._run(lambda: clear_limit() if limit >= 100 else apply_limit(limit))

    def on_cancel(self):
        self._run(clear_limit)

    def refresh(self):
        st = read_state()
        if st["error"]:
            self.verdict.config(text="⚠️ 读取失败", fg=BAD)
            self.detail.config(text=st["error"])
            return

        active = st["gate"] == 1 and 0 < st["limit"] <= 100
        cur = st["limit"] if active else None

        for pct, b in self.btns.items():
            on = (cur == pct) if pct < 100 else (cur is None)
            b.config(bg=ACCENT if on else "#e8e8ea",
                     fg="white" if on else FG,
                     activebackground=ACCENT if on else "#d8d8da",
                     activeforeground="white" if on else FG)

        pct = st["pct"]
        self.vals["pct"].config(text=f"{pct}%" if pct is not None else "—")
        if active:
            self.vals["limit"].config(text=f"{st['limit']}%")
            self.vals["gate"].config(text="已开启", fg=OK)
        else:
            self.vals["limit"].config(text="未限制")
            self.vals["gate"].config(text="未开启", fg=WARN)

        if not st["ac"]:
            charge_txt = "使用电池中"
        elif active and pct is not None and pct >= st["limit"]:
            charge_txt = "已停止(到上限)"
        elif st["charging"]:
            charge_txt = "充电中"
        else:
            charge_txt = "未充电"
        self.vals["charge"].config(text=charge_txt, fg=FG)

        flash, self._flash = self._flash, ""
        if not active:
            self.verdict.config(text="⚠️ 当前未限制充电", fg=WARN)
            self.detail.config(text=flash or "电池会一直充到 100%。点上方的百分比即可启用限充。")
        elif pct is not None and pct >= st["limit"]:
            self.verdict.config(
                text=f"✅ 限充已生效 — 电池充到 {st['limit']}% 就会停", fg=OK)
            self.detail.config(
                text=flash or f"当前 {pct}% 已到上限,插着电源也不会继续充。该设置已写入固件,重启、关机后依然有效。")
        else:
            self.verdict.config(
                text=f"✅ 限充已生效 — 电池充到 {st['limit']}% 就会停", fg=OK)
            self.detail.config(
                text=flash or f"当前 {pct}% 低于上限,正在充电;到 {st['limit']}% 自动停。")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        s = read_state()
        print({k: s[k] for k in ("pct", "limit", "gate", "romid", "platform",
                                 "ac", "charging", "error")})
        sys.exit(0)
    App().mainloop()
