# Draf laporan (suara manusia) — untuk DIKETIK manual di browser

> Bukan untuk di-copas: saat mengetik, ubah kalimat "What's the bug?" dan
> "Is this a regression?" dengan alur bahasamu sendiri. Blok versi dan langkah
> repro adalah data, boleh sama persis. Centang checkbox "own hands" hanya
> kalau memang kamu yang mengetik dan merangkai kalimatnya.

---

**Hyprland version**

```
Hyprland 0.57.2 built from branch v0.57.2 at commit 11cbc6c577b84ffb1edf4df3fc5b6128c3ae6947 clean (VERSION: bump to 0.57.2).
Date: Mon Nov 9 11:05:46 2026
Tag: v0.57.2, commits: 8015

Libraries:
Hyprgraphics: built against 0.6.0, system has 0.7.0
Hyprutils: built against 0.14.3, system has 0.14.4
Hyprcursor: built against 0.1.13, system has 0.1.14
Hyprlang: built against 0.6.8, system has 0.6.8
Aquamarine: built against 0.16.0, system has 0.16.0

Version ABI string: 11cbc6c577b84ffb1edf4df3fc5b6128c3ae6947_aq_0.16_hu_0.14_hg_0.6_hc_0.1_hlg_0.6
no flags were set
```

**Is this a regression?**

Don't know, this is the first Hyprland I ever tried this on. I found two
related reports: #15320 (virtual pointer motion, since fixed) and #15423
(button state stuck). Mine is different though: motion and keyboard events
from the same virtual device work fine, so it's not the whole device being
dropped.

**What's the bug?**

Buttons and the scroll wheel sent from a virtual input device never reach
any client. Pointer motion and keys from the very same device are delivered
normally. I tried both uinput (python-evdev) and XTEST (python-xlib against
Xwayland) with the same result: no button press and no wheel event ever
appear in the target window, neither XWayland nor native Wayland. `hyprctl -j
devices` shows the device, the kernel gets the events, but the client gets
nothing.

**Reproduction steps**

1. Open a small GTK3 window under XWayland (`GDK_BACKEND=x11`) that logs
   enter-notify, button press/release and scroll events. Move the pointer
   over it.
2. Create a uinput device (name "repro") with EV_KEY BTN_LEFT, EV_REL REL_X,
   REL_WHEEL and REL_WHEEL_HI_RES. First send REL_X 10: the cursor moves and
   the window logs enter-notify, so the device itself works.
3. Send BTN_LEFT down, then up. Nothing in the log. I also tried holding the
   press 30, 100 and 150 ms. Still nothing.
4. Send REL_WHEEL 1, and REL_WHEEL_HI_RES 120 — alone and together, with
   gaps from 0.15 to 0.5 s. Nothing.
5. Repeat the same with `xtest.fake_input()` using ButtonPress/Release for
   buttons 1, 4 and 5 against Xwayland. Same result.
6. As a control, send KEY_PAGEDOWN from the same uinput device: it arrives.
   A Chromium window even reacted to a Ctrl+Tab chord sent from that device.

The listener log only ever shows enter-notify. No button press/release, no
scroll.

**Attachments**

`hyprctl -j devices` lists the uinput device under `mice:` and `keyboards:`.
Listener log excerpt available on request.

**I acknowledge that:**

(tiga checkbox dicentang manual di form — centang hanya jika benar)