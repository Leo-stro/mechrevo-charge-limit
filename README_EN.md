# Battery Charge Limit for Mechrevo / TongFang (Uniwill) laptops on Windows

> The OEM "battery protection mode" in Mechrevo Control Console often does nothing — the battery keeps
> charging to 100%. This project sets a real charge limit (e.g. 50%) on Windows, and the setting
> **survives reboot, shutdown and unplugging the adapter** — no background service, no startup task,
> no admin rights required.

**Scope: laptops using the Uniwill / TongFang EC firmware family** (Mechrevo, XMG, TUXEDO, Eluktronics,
some Hasee / Huoying models). It is **not** a universal tool — Lenovo / Dell / HP / ASUS / Acer / Apple
use completely different EC firmware, and those vendors usually ship their own working utilities.

Tested on: **Mechrevo WUJIE15XA (Ryzen 7 8745HS, BIOS N.1.14MRO50)**

![UI](assets/screenshot.png)

## The problem

The EC firmware gates the charge-limit control loop behind a product-line check:

```c
bool state_is(uint8_t v) { return xram[0x07C3] == v || xram[0x0770] == v; }

bool limit_enabled = state_is(4) || state_is(5);
if (!limit_enabled) {
    uint8_t stored = xram[0x087F] & 0x7f;
    if (stored == 0 || stored > 100) { disable_control(); return; }
}
enable_control();   // uses live limit xram[0x07B9]
store_limit();      // live limit -> stored limit xram[0x087F]
```

On this machine `0x07C3 = 0x07` and `0x0770 = 0xFF`, so the gate never opens and writes to `0x07B9`
are silently ignored — that is why the OEM switch appears to do nothing.

## The approach: trigger the firmware's own store path

Instead of pinning the ROM ID (`0x0770 = 0x04`) — which touches an identity byte and is volatile —
this project briefly makes the gate condition true, so the firmware copies the live limit into its
stored-limit area. Afterwards the gate stays open on its own:

```
1. write 0x07B9 = <limit %>
2. write 0x07C3 = 0x04, hold 2.5 s      # gate temporarily true -> firmware runs store_limit()
3. write 0x07C3 back to its original value (0x07 on this machine), then read it back
4. wait 7 s, read 0x0742 bit2 == 1      # success
```

### Verified persistence

| Scenario | Result |
|---|---|
| Reboot | ✅ kept |
| Shutdown + unplug adapter + 10 min | ✅ kept |
| Then charging from 48% | ✅ stops exactly at 50% (`Charging=False`, `ChargeRate=0`) |
| Background process needed | ❌ none (nothing was running during the test) |
| Admin rights needed | ❌ none |
| Identity byte modified | ❌ no — `0x0770` stayed `0xFF` |

## Usage

```powershell
pythonw 电池充电上限.pyw
```

Buttons: 50 / 60 / 70 / 80 / 90 / 100 %, plus **"取消限充（恢复充满）"** (disable) which clears both the
live limit and the stored limit and immediately restores normal charging.

Requires Python 3 with tkinter, and the Mechrevo Control Console (it provides `UWACPIDriver.sys`,
device node `\\.\ACPIDriver`).

## Risk / disclaimer

Writes go to undocumented EC registers — community reverse-engineering, not a supported feature.
The Linux kernel documentation warns that forcing the charge-threshold interface on devices that do not
properly implement it *may damage the battery*. Verified working and reversible on WUJIE15XA, but no
long-term guarantee. All writes are volatile RAM (except the stored-limit area); no firmware flashing.
Use at your own risk.

## Credits

[w568w](https://gist.github.com/w568w/957976b59906e0ce5d6c13ad342e1593) ·
[losewayy/uniwill-ec-charge-limit](https://github.com/losewayy/uniwill-ec-charge-limit) ·
[ArchWiki: Mechrevo WUJIE14X](https://wiki.archlinux.org/title/Mechrevo_WUJIE14X) ·
[tuxedo-drivers](https://github.com/tuxedocomputers/tuxedo-drivers) ·
[Linux `uniwill-laptop` driver](https://docs.kernel.org/next/admin-guide/laptops/uniwill-laptop.html)

Technical details and pitfalls: [`docs/FINDINGS.md`](docs/FINDINGS.md)

MIT License
