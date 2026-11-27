# Template discussion "Bugs - Input" — isi sendiri, pakai kata-katamu

> Draft material dari pengujian nyata (TahuTech, CachyOS). Repo hyprwm/Hyprland
> **melarang laporan yang ditulis AI** (checkbox: "I wrote this request with my
> own hands…") — tulis ulang dengan kata-kata sendiri sebelum post via
> https://github.com/hyprwm/Hyprland/discussions/new?category=Bugs%20-%20Input
>
> Diskusi yang pertama (AI, tanpa format) ditutup otomatis: #17114 (strike 1/3).

---

**Hyprland version**
```
Hyprland 0.57.2 built from branch v0.57.2 at commit 11cbc6c577b84ffb1edf4df3fc5b6128c3ae6947 clean (VERSION: bump to 0.57.2).
Date: Mon Nov 9 11:05:46 2026
Tag: v0.57.2, commits: 8015
Aquamarine 0.16.0, Hyprutils 0.14.4, Hyprcursor 0.1.14, Hyprlang 0.6.8
```

**Is this a regression?**
Don't know — first tried this on 0.57.2. Related reports: #15320 (virtual-pointer motion,
since fixed), #15423 (locked button state). My motion and key events from the same virtual
device work; only buttons and axes don't.

**What's the bug?**
Button and axis events from virtual input devices never reach any client
(neither XWayland nor native Wayland). Pointer motion and keyboard events from the very
same virtual device work fine. Reproduced with uinput (python-evdev) and with XTEST
(python-xlib against Xwayland). `hyprctl -j devices` lists the device; kernel receives
the events; clients receive nothing.

**Reproduction steps**
1. Run a tiny GTK3 window (XWayland, `GDK_BACKEND=x11`) that logs `enter-notify-event`,
   `button-press/release-event`, `scroll-event`; put the pointer over it.
2. From a uinput device (`name="repro"`, EV_KEY BTN_LEFT + EV_REL REL_X/REL_WHEEL/
   REL_WHEEL_HI_RES) send:
   - `REL_X 10` → `enter-notify` logged, cursor moves (works)
   - `BTN_LEFT 1` + `BTN_LEFT 0` → nothing (also tried press held 30/100/150 ms)
   - `REL_WHEEL 1` (and `REL_WHEEL_HI_RES 120`, alone or together, gaps 0.15–0.5 s) → nothing
3. Same result with `xtest.fake_input(d, X.ButtonPress/Release, 1|4|5)`.
4. As a control: send `KEY_PAGEDOWN` from the same uinput device → delivered (a Chromium
   app reacted to a Ctrl+Tab chord from it as well).

Result: only `enter-notify` appears in the listener log; no button press/release, no scroll.

**Attachments**
(optional) `hyprctl -j devices` output shows the uinput device under `mice:` and
`keyboards:`; full logs available on request.

**I acknowledge that:** ☐ searched discussions/issues first (found #15320, #15423 — 15423 by @atomicptr ± same family) · ☐ read Code of Conduct · ☐ wrote this with my own hands