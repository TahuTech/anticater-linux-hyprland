#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anticaterd — daemon pemetaan knob ANTICATER per-aplikasi untuk Hyprland (Wayland).

Alur:
  evdev knob (grab eksklusif)  -> gestur  -> profil WM_CLASS  -> aksi
  hyprctl -j activewindow      -> class  -> app-mapping.json  -> key/shortcut/mouse/scroll/command
  LED ring (kabel): status profil — 2=hijau (global), 4=pelangi (profil app), 0=mati (berhenti)

Konfigurasi: app-mapping.json — format kompatibel proyek macOS
dreamug/anticater-knob-control; di Linux kunci app memakai WM_CLASS
(mis. "brave-browser"), bukan bundle-id.

Contoh:
  python3 anticaterd.py                      # jalankan
  python3 anticaterd.py --dry-run --no-grab  # uji tanpa eksekusi / grab
  python3 anticaterd.py --list-devices
  python3 anticaterd.py --check-config
"""

import argparse
import json
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from evdev import InputDevice, UInput, ecodes, list_devices

# evdev >=1.x: ecodes.KEY = {code: "KEY_NAMA"} — balik jadi nama -> code
KEY_BY_NAME = {name: code for code, name in ecodes.KEY.items()}

VID_ANTICATER = 0x514C  # LiQi / ANTICATER
NAME_KEYBOARD = "USB Composite Device Keyboard"
VID_PID_CABLE = (0x514C, 0x8850)

SLOT_NAMES = ("rotate_left", "rotate_right", "press",
              "press_rotate_left", "press_rotate_right")
SLOTS = {  # keycode evdev -> slot gestur (terverifikasi di hardware)
    114: "rotate_left",        # KEY_VOLUMEDOWN
    115: "rotate_right",       # KEY_VOLUMEUP
    113: "press",              # KEY_MUTE
    224: "press_rotate_left",  # KEY_BRIGHTNESSDOWN
    225: "press_rotate_right", # KEY_BRIGHTNESSUP
}
KEY_MUTE = 113

PRESS_GRACE = 0.18   # detik: jika rotasi muncul, batal aksi press (hold+rotate)
POLL_MS = 700        # interval cek fokus aktif
RETRY_S = 2.0        # jeda coba-buka ulang device

LED_MODE_GLOBAL = 2  # hijau
LED_MODE_APP = 4     # pelangi
LED_MODE_OFF = 0

# Nama kunci manusiawi -> stem nama KEY_* evdev
KEY_ALIASES = {
    "command": "LEFTMETA", "cmd": "LEFTMETA", "meta": "LEFTMETA",
    "super": "LEFTMETA", "win": "LEFTMETA",
    "control": "LEFTCTRL", "ctrl": "LEFTCTRL",
    "shift": "LEFTSHIFT",
    "option": "LEFTALT", "alt": "LEFTALT",
    "return": "ENTER", "enter": "ENTER",
    "escape": "ESC", "esc": "ESC",
    "space": "SPACE",
    "backspace": "BACKSPACE",
    "delete": "DELETE", "del": "DELETE",
    "tab": "TAB",
    "home": "HOME", "end": "END",
    "pageup": "PAGEUP", "pagedown": "PAGEDOWN",
    "insert": "INSERT",
    "volume_up": "VOLUMEUP", "volumeup": "VOLUMEUP",
    "volume_down": "VOLUMEDOWN", "volumedown": "VOLUMEDOWN",
    "play_pause": "PLAYPAUSE", "playpause": "PLAYPAUSE",
    "next_track": "NEXTSONG", "next": "NEXTSONG",
    "previous_track": "PREVIOUSSONG", "prev": "PREVIOUSSONG",
    "mute": "MUTE",
    "stop": "STOPCD",
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "bracketleft": "LEFTBRACE", "bracketright": "RIGHTBRACE",
    "comma": "COMMA", "period": "DOT", "slash": "SLASH",
    "backslash": "BACKSLASH", "semicolon": "SEMICOLON",
    "quote": "APOSTROPHE", "backquote": "GRAVE", "grave": "GRAVE",
    "minus": "MINUS", "equal": "EQUAL",
}

log = logging.getLogger("anticaterd")
stop = threading.Event()


# ---------- utilitas ----------

def keycode(name):
    """Nama kunci manusiawi -> keycode evdev. Raise KeyError bila tak dikenal."""
    base = KEY_ALIASES.get(name.strip().lower(), name.strip().lower())
    return KEY_BY_NAME["KEY_" + base.upper()]


def required_keycodes(config):
    """Kumpulkan semua keycode yang dipakai config + basis statis."""
    keys = {KEY_BY_NAME["KEY_" + n] for n in KEY_ALIASES.values()
            if "KEY_" + n in KEY_BY_NAME}
    for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
        n = ch.upper() if ch.isalpha() else ch
        if "KEY_" + n in KEY_BY_NAME:
            keys.add(KEY_BY_NAME["KEY_" + n])
    for i in range(1, 13):
        keys.add(KEY_BY_NAME["KEY_F%d" % i])
    for k in config:
        for slot in config[k].values():
            acts = [slot] if slot.get("type") != "sequence" else \
                [st for st in slot.get("steps", []) if isinstance(st, dict)]
            for a in acts:
                if a.get("type") in ("key", "shortcut"):
                    for n in a.get("keys", []):
                        try:
                            keys.add(keycode(n))
                        except KeyError:
                            pass  # divalidasi terpisah di --check-config
    return keys


# ---------- penemuan device ----------

def find_keyboard():
    """Cari node evdev knob: nama persis dulu, lalu vid 0x514c + nama Keyboard."""
    for path in list_devices():
        try:
            d = InputDevice(path)
        except OSError:
            continue
        try:
            if d.info.vendor == VID_ANTICATER and d.name == NAME_KEYBOARD:
                return path
        finally:
            d.close()
    for path in list_devices():
        try:
            d = InputDevice(path)
        except OSError:
            continue
        try:
            name = d.name.lower()
            if d.info.vendor == VID_ANTICATER and "keyboard" in name and "mouse" not in name:
                return path
        finally:
            d.close()
    return None


def hidraw_vendor_paths():
    """(/dev/hidrawN, vendor, product) dari interface HID dengan use page 0xFF00."""
    out = []
    base = Path("/sys/class/hidraw")
    if not base.exists():
        return out
    for entry in sorted(base.iterdir()):
        dev = entry / "device"
        rdesc = dev / "report_descriptor"
        uevent = dev / "uevent"
        if not rdesc.exists():
            continue
        try:
            data = rdesc.read_bytes()
        except OSError:
            continue
        if b"\x06\x00\xff" not in data:  # usage page vendor 0xFF00 (LE)
            continue
        try:
            line = next(l for l in uevent.read_text().splitlines() if l.startswith("HID_ID="))
            parts = line.split("=")[1].split(":")
            vendor, product = int(parts[1], 16), int(parts[2], 16)
        except (StopIteration, ValueError, IndexError):
            continue
        out.append(("/dev/" + entry.name, vendor, product))
    return out


# ---------- LED (write-only, efek; hanya via kabel) ----------

class Led:
    """Saluran LED via hidraw interface vendor (ada hanya saat kabel)."""

    def __init__(self, path):
        self.path = path
        try:
            self.fd = os.open(path, os.O_RDWR)
            os.write(self.fd, b"\xfb\xfb\xfb")  # init, sama seperti probe
            log.info("LED siap: %s", path)
        except OSError as e:
            self.fd = None
            log.warning("LED tidak bisa dibuka %s: %s", path, e)

    def set_mode(self, mode):
        if self.fd is None:
            return False
        try:
            os.write(self.fd, bytes([0xFE, 0xB0, 0x01, 0x08, 0, 0, 0, 0, 0, 0x01, 0x00,
                                     0x50 + mode]))
            os.write(self.fd, b"\xFD\xFE\xFF")  # finish
            return True
        except OSError as e:
            log.warning("tulis LED gagal: %s", e)
            return False

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


# ---------- konfigurasi ----------

def default_config_path():
    env = os.environ.get("ANTICATER_CONFIG")
    if env:
        return Path(env)
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    usr = xdg / "anticater" / "app-mapping.json"
    if usr.exists():
        return usr
    etc = Path("/etc/anticater/app-mapping.json")
    if etc.exists():
        return etc
    return Path(__file__).resolve().parent.parent / "config" / "app-mapping.json"


def load_config(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    def norm(mapping):
        return {s: mapping.get(s, {"type": "noop"}) for s in SLOT_NAMES}

    cfg = {
        "global": norm(raw.get("global", {})),
        "apps": {k: norm(v) for k, v in raw.get("apps", {}).items()},
    }
    if not raw.get("global"):
        log.warning("config tanpa bagian 'global' — semua slot fallback noop")
    return cfg


def check_action(app, slot, act, errs, depth=0):
    """Validasi satu aksi; sequence divalidasi per langkah (satu level)."""
    t = act.get("type", "noop")
    if t not in ("key", "shortcut", "mouse", "scroll", "command", "sequence", "noop"):
        errs.append(f"{app}/{slot}: tipe tak dikenal '{t}'")
        return
    if t in ("key", "shortcut", "command"):
        keys = act.get("keys")
        if not isinstance(keys, list) or not keys:
            errs.append(f"{app}/{slot}: butuh 'keys' (daftar)")
            return
        if t in ("key", "shortcut"):
            for n in keys:
                try:
                    keycode(n)
                except KeyError:
                    errs.append(f"{app}/{slot}: kunci tak dikenal '{n}'")
    elif t == "scroll":
        d = (act.get("keys") or ["down"])[0]
        if d not in ("up", "down", "left", "right"):
            errs.append(f"{app}/{slot}: arah scroll '{d}' tak dikenal")
    elif t == "mouse":
        btn = act.get("keys", ["left"])[0]
        if btn not in ("left", "right", "middle"):
            errs.append(f"{app}/{slot}: tombol mouse '{btn}' tak dikenal")
    elif t == "sequence":
        steps = act.get("steps")
        if not isinstance(steps, list) or not steps:
            errs.append(f"{app}/{slot}: sequence butuh 'steps' (daftar aksi)")
            return
        if depth > 0:
            errs.append(f"{app}/{slot}: sequence bersarang tak didukung")
            return
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                errs.append(f"{app}/{slot}[{i}]: langkah bukan objek aksi")
                continue
            check_action(app, f"{slot}[{i}]", step, errs, depth + 1)


def check_config(cfg):
    """Validasi: tipe aksi dikenal, nama kunci ada. Kembalikan daftar error."""
    errs = []
    for scope, items in (("global", [cfg["global"]]),
                         ("apps", [(k, v) for k, v in cfg["apps"].items()])):
        for entry in items:
            if scope == "global":
                app, slots = "global", entry
            else:
                app, slots = entry
            for slot, act in slots.items():
                check_action(app, slot, act, errs)
    return errs


# ---------- fokus window ----------

def get_active_class():
    """WM_CLASS window aktif via hyprctl. None bila gagal/tak ada window."""
    try:
        p = subprocess.run(["hyprctl", "-j", "activewindow"],
                           capture_output=True, text=True, timeout=3)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if p.returncode != 0:
        return None
    try:
        cls = json.loads(p.stdout).get("class") or None
    except json.JSONDecodeError:
        return None
    return cls if cls and cls != "invalid" else None


# ---------- arah pointer (untuk aksi mouse/scroll) ----------

def hyprctl_out(args):
    """Jalankan hyprctl dengan argumen; kembalikan stdout atau None."""
    try:
        p = subprocess.run(["hyprctl", *args], capture_output=True, text=True,
                           timeout=3)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def cursor_pos():
    """Posisi pointer (x, y) koordinat layout; None bila gagal."""
    out = hyprctl_out(["cursorpos"])
    if not out:
        return None
    try:
        x, y = out.strip().split(",")
        return int(x), int(y)
    except ValueError:
        return None


def focus_center():
    """Titik tengah window aktif; None bila gagal / tak ada jendela."""
    out = hyprctl_out(["-j", "activewindow"])
    if not out:
        return None
    try:
        j = json.loads(out)
        at, size = j.get("at"), j.get("size")
    except json.JSONDecodeError:
        return None
    if not at or not size:
        return None
    return at[0] + size[0] // 2, at[1] + size[1] // 2


def warp_pointer(target):
    """Warp pointer ke posisi absolut (x, y) — hl.dsp.cursor.move bersifat
    absolut (setara movecursor lama), presisi penuh selama koordinat berada
    di dalam monitor."""
    try:
        subprocess.run(["hyprctl", "dispatch",
                        "hl.dsp.cursor.move({ x = %d, y = %d })" % target],
                       capture_output=True, text=True, timeout=3)
        return True
    except subprocess.SubprocessError:
        return False


def with_pointer_on_focus(fn):
    """Jalankan fn dengan pointer di tengah window aktif, lalu kembalikan.

    Aksi mouse/scroll sintesis dikirim ke window di bawah pointer, bukan ke
    window fokus — pointer diarahkan dulu ke tengah jendela aktif agar gestur
    knob selalu mengenai aplikasi yang sedang dipakai.
    """
    cur = cursor_pos()
    center = focus_center()
    if not cur or not center:
        return fn()
    if not warp_pointer(center):
        return fn()
    time.sleep(0.05)  # biarkan kompositor memproses warp sebelum event input
    try:
        return fn()
    finally:
        warp_pointer(cur)


def resolve_actions(cfg, app):
    """Profil aktif: apps[app] -> apps[app.lower()] -> global."""
    if app:
        for candidate in (app, app.lower()):
            if candidate in cfg["apps"]:
                return cfg["apps"][candidate]
    return cfg["global"]


# ---------- aksi ----------

class Actor:
    """Sintesis input via uinput + eksekusi command lewat shell."""

    def __init__(self, keys):
        self._lock = threading.Lock()
        self._keys = set(keys)
        self.ui = None
        self._open()

    def _open(self):
        with self._lock:
            if self.ui is not None:
                try:
                    self.ui.close()
                except OSError:
                    pass
            try:
                self.ui = UInput(
                    events={
                        ecodes.EV_KEY: sorted(self._keys),
                        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y,
                                        ecodes.REL_WHEEL, ecodes.REL_HWHEEL],
                    },
                    name="anticaterd", vendor=VID_ANTICATER)
                log.info("uinput siap: %s", self.ui.name)
            except OSError as e:
                self.ui = None
                log.warning("uinput gagal (%s) — aksi key/shortcut/mouse/scroll nonaktif", e)

    def rebind(self, keys):
        """Buat ulang uinput dengan keyset baru (dipakai saat config hot-reload)."""
        self._keys = set(keys)
        self._open()
        log.info("uinput dibangun ulang (%d key)", len(self._keys))

    def chord(self, codes):
        with self._lock:
            if self.ui is None:
                return False
            for c in codes:
                self.ui.write(ecodes.EV_KEY, c, 1)
            self.ui.syn()
            for c in reversed(codes):
                self.ui.write(ecodes.EV_KEY, c, 0)
            self.ui.syn()
        return True

    def click(self, btn=ecodes.BTN_LEFT):
        with self._lock:
            if self.ui is None:
                return False
            self.ui.write(ecodes.EV_KEY, btn, 1)
            self.ui.syn()
            self.ui.write(ecodes.EV_KEY, btn, 0)
            self.ui.syn()
        return True

    def scroll(self, direction):
        with self._lock:
            if self.ui is None:
                return False
            axis, val = {
                "up": (ecodes.REL_WHEEL, -1),
                "down": (ecodes.REL_WHEEL, 1),
                "left": (ecodes.REL_HWHEEL, -1),
                "right": (ecodes.REL_HWHEEL, 1),
            }[direction]
            self.ui.write(ecodes.EV_REL, axis, val)
            self.ui.syn()
        return True

    def run_cmd(self, argv):
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log.warning("command gagal %s: %s", argv, e)

    def close(self):
        with self._lock:
            if self.ui is not None:
                self.ui.close()
                self.ui = None


def exec_action(actor, slot, act, app, dry_run):
    if not isinstance(act, dict):
        log.warning("%s/%s: lewati aksi bukan objek", app, slot)
        return
    t = act.get("type", "noop")
    desc = act.get("description", "")
    if t == "noop":
        return
    if t == "sequence":
        for i, step in enumerate(act.get("steps") or []):
            if not isinstance(step, dict):
                log.warning("%s/%s: lewati langkah %d bukan objek aksi", app, slot, i + 1)
                continue
            exec_action(actor, f"{slot}[{i+1}]", step, app, dry_run)
        return
    if dry_run:
        log.info("[dry-run] %-16s %-16s -> %s %s", slot, app or "?", t,
                 act.get("keys") or "")
        return
    if t in ("key", "shortcut"):
        try:
            codes = [keycode(n) for n in act.get("keys", [])]
        except KeyError as e:
            log.warning("%s/%s: lewati kunci tak dikenal %s", app, slot, e)
            return
        if actor.chord(codes):
            log.info("%-16s %-16s -> %s %s", slot, app or "?", t, act.get("keys"))
    elif t == "scroll":
        direction = (act.get("keys") or ["down"])[0]
        if direction not in ("up", "down", "left", "right"):
            log.warning("%s/%s: arah scroll tak dikenal '%s'", app, slot, direction)
            return
        with_pointer_on_focus(lambda: actor.scroll(direction))
        log.info("%-16s %-16s -> scroll %s (pointer -> fokus)", slot, app or "?",
                 act.get("keys"))
    elif t == "mouse":
        btn = (act.get("keys") or ["left"])[0]
        if btn not in ("left", "right", "middle"):
            log.warning("%s/%s: tombol mouse tak dikenal '%s'", app, slot, btn)
            return
        code = {"left": ecodes.BTN_LEFT, "right": ecodes.BTN_RIGHT,
                "middle": ecodes.BTN_MIDDLE}[btn]
        with_pointer_on_focus(lambda: actor.click(code))
        log.info("%-16s %-16s -> klik mouse %s (pointer -> fokus)", slot,
                 app or "?", btn)
    elif t == "command":
        actor.run_cmd(act["keys"])
        log.info("%-16s %-16s -> command %s", slot, app or "?", act["keys"])


# ---------- status global ----------

def make_ctx(cfg, args):
    """Bungkus state yang diubah thread (profil/LED) dalam objek sederhana."""
    class Ctx:
        pass
    ctx = Ctx()
    ctx.cfg = cfg
    ctx.args = args
    ctx.app = None
    ctx.pressed = False
    ctx.press_deadline = None
    ctx.config_path = None
    ctx.config_mtime = None
    ctx.led = None
    if not args.no_led and not args.dry_run:
        for path, vendor, product in hidraw_vendor_paths():
            if (vendor, product) == VID_PID_CABLE:
                ctx.led = Led(path)
                break
        if ctx.led is None:
            log.info("LED tidak tersedia (kemungkinan pakai dongle) — status via log saja")
    return ctx


# ---------- input loop ----------

def handle_key(ctx, actor, code, value):
    if value == 2:  # auto-repeat, abaikan
        return
    slot = SLOTS.get(code)
    app = ctx.app
    if code == KEY_MUTE:
        if value == 1:
            ctx.pressed = True
            ctx.press_deadline = time.monotonic() + PRESS_GRACE
        else:
            ctx.pressed = False
        return
    if slot is None or value != 1:
        return
    if slot in ("press_rotate_left", "press_rotate_right") and ctx.press_deadline:
        ctx.press_deadline = None  # hold+rotate: batal aksi press biasa
    try:
        act = resolve_actions(ctx.cfg, app)[slot]
    except KeyError:
        act = {"type": "noop"}
    exec_action(actor, slot, act, app, ctx.args.dry_run)


def open_device(path, grab):
    dev = InputDevice(path)
    if grab:
        dev.grab()  # EVIOCGRAB: hanya daemon yang menerima event knob
        log.info("grab: %s (%s)", path, dev.name)
    else:
        log.info("tanpa grab: %s (%s)", path, dev.name)
    return dev


def main_loop(ctx, actor, keyboard_path):
    dev = None
    pressed = False
    ctx.pressed = False
    ctx.press_deadline = None
    lock = threading.Lock()

    def focus_loop():
        while not stop.is_set():
            ctx.config_mtime = maybe_reload(ctx, actor)
            cls = get_active_class()
            if cls and cls != ctx.app:
                with lock:
                    ctx.app = cls
                    start = None  # LED update di bawah, di luar lock
                on_profile_change(ctx, cls, ctx.args.dry_run)
            elif cls is None and ctx.app is not None:
                with lock:
                    ctx.app = None
                on_profile_change(ctx, None, ctx.args.dry_run)
            stop.wait(POLL_MS / 1000)

    t = threading.Thread(target=focus_loop, name="focus", daemon=True)
    t.start()

    try:
        while not stop.is_set():
            if dev is None:
                path = keyboard_path if keyboard_path else find_keyboard()
                if path is None:
                    log.warning("knob tidak ditemukan — coba lagi %ss", RETRY_S)
                    stop.wait(RETRY_S)
                    continue
                try:
                    dev = open_device(path, not (ctx.args.dry_run or ctx.args.no_grab))
                except OSError as e:
                    log.warning("buka %s gagal: %s — coba lagi", path, e)
                    stop.wait(RETRY_S)
                    continue
            timeout = 0.2
            if ctx.press_deadline is not None:
                timeout = min(timeout, max(0.0, ctx.press_deadline - time.monotonic()))
            try:
                r, _, _ = select.select([dev.fd], [], [], timeout)
            except OSError:
                # fd mati (knob dicabut) — tutup & biarkan loop reconnect
                try:
                    dev.close()
                except OSError:
                    pass
                dev = None
                continue
            if r:
                try:
                    events = dev.read()
                except OSError:
                    try:
                        dev.close()
                    except OSError:
                        pass
                    dev = None
                    continue
                for e in events:
                    if e.type == ecodes.EV_KEY:
                        handle_key(ctx, actor, e.code, e.value)
            if ctx.press_deadline is not None and time.monotonic() >= ctx.press_deadline:
                ctx.press_deadline = None
                act = resolve_actions(ctx.cfg, ctx.app)["press"]
                exec_action(actor, "press", act, ctx.app, ctx.args.dry_run)
        return 0
    finally:
        if dev is not None:
            try:
                if not ctx.args.dry_run:
                    dev.ungrab()
            except OSError:
                pass
            dev.close()


def maybe_reload(ctx, actor):
    """Muat ulang config bila file berubah (agar edit GUI langsung aktif)."""
    try:
        m = Path(ctx.config_path).stat().st_mtime
    except OSError:
        return ctx.config_mtime
    if m == ctx.config_mtime:
        return m
    ctx.config_mtime = m
    try:
        new = load_config(ctx.config_path)
    except Exception as e:
        log.warning("config gagal dimuat ulang: %s", e)
        return m
    ctx.cfg = new
    actor.rebind(required_keycodes(new))
    log.info("config dimuat ulang: %s", ctx.config_path)
    on_profile_change(ctx, ctx.app, ctx.args.dry_run)
    return m


def on_profile_change(ctx, app, dry_run):
    """Set LED sesuai profil (hanya saat saluran LED ada / kabel terpasang)."""
    is_app = app is not None and any(
        c in ctx.cfg["apps"] for c in (app, app.lower()))
    if dry_run:
        log.info("profil: %-16s -> %s", app or "(kosong)",
                 "app" if is_app else "global")
        return
    if ctx.led is None:
        return
    if is_app:
        ctx.led.set_mode(LED_MODE_APP)   # pelangi = profil app aktif
    else:
        ctx.led.set_mode(LED_MODE_GLOBAL)  # hijau = global/default


# ---------- CLI ----------

def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="path app-mapping.json (default: %(default)s)")
    p.add_argument("--device", default=None, help="path evdev knob (default: auto-detect)")
    p.add_argument("--poll-ms", type=int, default=POLL_MS, help="interval cek fokus (ms)")
    p.add_argument("--dry-run", action="store_true", help="cetak aksi tanpa eksekusi/grab/LED")
    p.add_argument("--no-grab", action="store_true", help="jangan EVIOCGRAB (event tetap sampai Hyprland)")
    p.add_argument("--no-led", action="store_true", help="nonaktifkan sinkronisasi LED")
    p.add_argument("--list-devices", action="store_true", help="cetak perangkat knob + LED lalu keluar")
    p.add_argument("--check-config", action="store_true", help="validasi config lalu keluar")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    if args.list_devices:
        print("evdev knob:")
        print("  ", find_keyboard() or "(tidak ditemukan)")
        print("interface hidraw vendor 0xFF00:")
        for path, vendor, product in hidraw_vendor_paths():
            mark = " <-- LED" if (vendor, product) == VID_PID_CABLE else ""
            print(f"   {path}  {vendor:04x}:{product:04x}{mark}")
        print("uinput:", "/dev/uinput" if os.path.exists("/dev/uinput") else "TIDAK ADA")
        return 0

    cfg_path = Path(args.config) if args.config else default_config_path()
    cfg = load_config(cfg_path)
    log.info("config: %s (%d app)", cfg_path, len(cfg["apps"]))
    errs = check_config(cfg)
    for e in errs:
        log.warning("config: %s", e)
    if args.check_config:
        print(f"{cfg_path}: {'BERES' if not errs else '%d CATATAN' % len(errs)}")
        return 0

    keys = required_keycodes(cfg)
    if args.no_grab:
        log.warning("--no-grab: event knob tetap diteruskan ke Hyprland")

    actor = Actor(keys)
    ctx = make_ctx(cfg, args)
    ctx.config_path = str(cfg_path)
    try:
        ctx.config_mtime = cfg_path.stat().st_mtime
    except OSError:
        ctx.config_mtime = None

    def shutdown(signum, frame):
        log.info("menerima sinyal %d — berhenti", signum)
        stop.set()
        if ctx.led is not None:
            ctx.led.set_mode(LED_MODE_OFF)
            ctx.led.close()
        actor.close()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    return main_loop(ctx, actor, args.device)


if __name__ == "__main__":
    sys.exit(main())