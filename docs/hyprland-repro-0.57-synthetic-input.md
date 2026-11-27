# Draft issue: synthetic mouse button & axis events never reach clients (0.57.2); motion & key work

Siap tempel ke https://github.com/hyprwm/Hyprland/issues (kategori Bugs - Input).

---

## Summary

On Hyprland 0.57.2, **button and axis events from virtual input devices never reach clients**,
while pointer **motion** and **keyboard** events from the same devices work fine.
Reproduced with both `uinput` (via `python-evdev`) and XTEST (via `python-xlib`, Xwayland),
against both XWayland and native Wayland windows.

## Environment

- Hyprland `v0.57.2` (commit `11cbc6c577b84ffb1edf4df3fc5b6128c3ae6947`), Aquamarine 0.16.0, Hyprutils 0.14.4
- CachyOS, kernel `7.3.6-1-cachyos`, session: Hyprland on Wayland
- Test window: GTK3 (XWayland via `GDK_BACKEND=x11`); same result on native Wayland clients

## Repro

Terminal 1 — listener window that logs enter/click/scroll:

```python
#!/usr/bin/env python3
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

class W(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.set_wmclass("wheelgtk", "wheelgtk")
        self.set_default_size(400, 300)
        for s in ("scroll-event", "enter-notify-event",
                  "button-press-event", "button-release-event"):
            self.connect(s, self.e)
        self.show_all()
    def e(self, w, ev):
        print(f"{ev.type.name} at {int(ev.x)},{int(ev.y)}", flush=True)

W(); Gtk.main()
```

Terminal 2 — injector (pointer placed over the window first):

```python
#!/usr/bin/env python3
import evdev, time
ui = evdev.UInput(name="repro", events={
    evdev.ecodes.EV_KEY: [evdev.ecodes.BTN_LEFT, evdev.ecodes.KEY_PAGEDOWN],
    evdev.ecodes.EV_REL: [evdev.ecodes.REL_X, evdev.ecodes.REL_Y,
                          evdev.ecodes.REL_WHEEL, evdev.ecodes.REL_WHEEL_HI_RES]})
def rel(t, c, v): ui.write(evdev.ecodes.EV_REL, c, v); ui.syn()
def key(c, v):    ui.write(evdev.ecodes.EV_KEY, c, v); ui.syn()

rel(evdev.ecodes.REL_X, 10)                  # motion:      WORKS (enter-notify, cursor moves)
key(evdev.ecodes.BTN_LEFT, 1); key(evdev.ecodes.BTN_LEFT, 0)  # click:  silent (also held 30/100/150 ms)
rel(evdev.ecodes.REL_WHEEL, 1)               # wheel:       silent (also with REL_WHEEL_HI_RES = 120)
rel(evdev.ecodes.REL_WHEEL, -1)              #              (also 0.15-0.5 s gaps between ticks)
key(evdev.ecodes.KEY_PAGEDOWN, 1); key(evdev.ecodes.KEY_PAGEDOWN, 0)  # key: WORKS
```

XTEST variant (against Xwayland), same result (buttons 1/4/5 all silent):

```python
from Xlib import display, X
from Xlib.ext import xtest
d = display.Display()
xtest.fake_input(d, X.ButtonPress, 1); xtest.fake_input(d, X.ButtonRelease, 1); d.sync()
```

## Observed

| Event | uinput | XTEST |
|---|---|---|
| REL motion / MotionNotify | delivered (enter-notify, cursor moves, `hyprctl cursorpos` changes) | delivered |
| EV_KEY / KeyPress (also Ctrl+Tab chord) | delivered (Chromium switched tabs) | — |
| BTN_LEFT press+release (held 1/30/100/150 ms) | **never delivered** | **never delivered** |
| REL_WHEEL / REL_WHEEL_HI_RES (with & without hi-res, gaps 0.15-0.5 s) | **never delivered** | **never delivered** (Button 4/5) |

- `hyprctl -j devices` lists the uinput device (`mice: repro`, `keyboards: repro`);
  the kernel receives every event (verified with a second `evdev` reader on the same node).
- Listener logs only `enter-notify`; no `button-press`/`button-release`/`button` scroll.
- Physical mouse buttons and wheel work normally; not a settings issue (motion+keys share the same
  `input:` settings; also tested with no button/key binds for the device).
- Related: #15423 (stranded button state), #15320 (virtual-pointer motion, fixed by #15003).
  On #15320's fix (PR #15003) motion deliveries; buttons/axes still dropped here.

## Expected

Synthetic button press/release and wheel events delivered to the surface under the pointer, like motion and keys.
Happy to provide more logs (`HYPRLAND_LOG`/`wev`) or test a patch.