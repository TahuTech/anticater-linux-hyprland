#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Uji satu-jalan: apakah event sintetis (wheel/klik) sampai ke aplikasi di sesi Hyprland ini.

Dipakai untuk memverifikasi perbaikan Hyprland setelah update — tidak butuh knob
ANTICATER, injeksi via uinput langsung (python-evdev).

Cara pakai: jalankan DI DALAM sesi Hyprland (terminal biasa):
    python3 probes/probe_synthetic_input.py

Yang diuji (window jendela uji XWayland, pointer & fokus diarahkan otomatis):
  [1] tombol  KEY_PAGEDOWN     -> kontrol fokus (0.57.2: sampai)
  [2] motion  REL_X            -> kontrol (0.57.2: kursor bergerak, enter-notify)
  [3] wheel   REL_WHEEL x3     -> gejala bug (0.57.2: JATUH)
  [4] klik    BTN_LEFT         -> info tambahan (0.57.2: sampai)

Keluar: 0 = wheel sampai (perbaikan), 1 = wheel masih jatuh (workaround tetap),
        2 = window uji tidak ditemukan / error. Key & klik hanya info tambahan
        (listener GTK terkadang flaky di 0.57.2, bukan penentu veredik).

Catatan implementasi (kondisi 0.57.2 di mesin ini):
  - Jangan `Gtk.Widget.add_events`/mask — membuat GTK berhenti menerima SEMUA
    event (teruji: key ikut hilang). Default mask GTK sudah cukup.
  - Nama event Gdk pakai underscore: ENTER_NOTIFY, KEY_PRESS, BUTTON_PRESS, dst.
"""

import json
import os
import subprocess
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

os.environ.setdefault("GDK_BACKEND", "x11")  # window uji via Xwayland (deterministik)

import evdev

WINDOW_TITLE = "uji sintetis"
events = []          # [(detik, nama_event)]
lock = threading.Lock()


def rec(name):
    with lock:
        events.append((time.monotonic(), name))


class Window(Gtk.Window):
    """Listener minimal — pola yang terbukti stabil (tanpa add_events)."""
    def __init__(self):
        super().__init__(title=WINDOW_TITLE)
        self.set_default_size(480, 360)
        for sig in ("enter-notify-event", "motion-notify-event",
                    "button-press-event", "scroll-event", "key-press-event"):
            self.connect(sig, self.on_ev)
        self.show_all()

    def on_ev(self, w, ev):
        rec(ev.type.name)
        return False


def hyprctl(args):
    try:
        out = subprocess.run(["hyprctl"] + args, capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return ""


def hyprctl_json(args):
    try:
        return json.loads(hyprctl(args))
    except Exception:
        return None


def focus_and_center():
    """Fokuskan window uji; kembalikan (x, y) tengahnya dari geometri aktif."""
    addr = None
    for _ in range(12):  # tunggu window terdaftar (race dgn mapping)
        clients = hyprctl_json(["-j", "clients"]) or []
        addr = next((c["address"] for c in clients
                     if c.get("title") == WINDOW_TITLE), None)
        if addr:
            break
        time.sleep(0.25)
    if not addr:
        return None
    hyprctl(["dispatch", 'hl.dsp.focus({ window = "address:%s" })' % addr])
    time.sleep(0.5)
    win = hyprctl_json(["-j", "activewindow"]) or {}
    at, size = win.get("at"), win.get("size")
    if not at or not size:
        return None
    return (at[0] + size[0] // 2, at[1] + size[1] // 2)


def warp(x, y):
    hyprctl(["dispatch", "hl.dsp.cursor.move({ x = %d, y = %d })" % (x, y)])


def main():
    exit_code = [2]
    Window()
    print("READY — window uji terbuka, jalankan urutan...", flush=True)

    def run_sequence():
        try:
            center = focus_and_center()
            if not center:
                print("GAGAL: window uji tidak terfokus (cek sesi Hyprland).", flush=True)
                return
            time.sleep(0.5)
            warp(*center)
            time.sleep(0.5)
            warp(center[0] + 80, center[1] + 60)  # geser sedikit supaya enter tercatat
            time.sleep(0.3)

            ui = evdev.UInput(name="wt", events={
                evdev.ecodes.EV_KEY: [evdev.ecodes.BTN_LEFT, evdev.ecodes.KEY_PAGEDOWN],
                evdev.ecodes.EV_REL: [evdev.ecodes.REL_X, evdev.ecodes.REL_WHEEL],
            })
            try:
                def rel(c, v):
                    ui.write(evdev.ecodes.EV_REL, c, v)
                    ui.syn()

                def key(c, v):
                    ui.write(evdev.ecodes.EV_KEY, c, v)
                    ui.syn()

                print("[1] tombol PageDown (kontrol fokus)", flush=True)
                key(evdev.ecodes.KEY_PAGEDOWN, 1); time.sleep(0.05)
                key(evdev.ecodes.KEY_PAGEDOWN, 0)
                time.sleep(0.5)

                print("[2] motion REL_X", flush=True); rel(evdev.ecodes.REL_X, 12); time.sleep(0.6)
                print("[3] wheel REL_WHEEL turun x3", flush=True)
                for _ in range(3):
                    rel(evdev.ecodes.REL_WHEEL, 1)
                    time.sleep(0.15)
                time.sleep(0.6)
                print("[4] klik kiri (tahan 50ms)", flush=True)
                key(evdev.ecodes.BTN_LEFT, 1); time.sleep(0.05); key(evdev.ecodes.BTN_LEFT, 0)
                time.sleep(0.8)
            finally:
                ui.close()

            names = [n for _, n in events]
            motion_ok = any("ENTER" in n or "MOTION" in n for n in names)
            wheel_ok = any("SCROLL" in n for n in names)
            click_ok = any("BUTTON" in n for n in names)
            key_ok = any("KEY" in n for n in names)

            print("\n=== HASIL (veredik = wheel; key/klik info karena listener GTK "
                  "terkadang flaky di 0.57.2) ===", flush=True)
            print("  wheel   REL_WHEEL    : %s" % ("SAMPAI — sintetis didukung!" if wheel_ok else "JATUH (bug Hyprland)"), flush=True)
            print("  motion  REL_X        : %s (kontrol, info)" % ("SAMPAI" if motion_ok else "terlewat"), flush=True)
            print("  tombol  KEY_PAGEDOWN : %s (kontrol, info)" % ("SAMPAI" if key_ok else "terlewat"), flush=True)
            print("  klik    BTN_LEFT     : %s (info)" % ("SAMPAI" if click_ok else "terlewat"), flush=True)
            print("  (event tercatat: %s)" % (", ".join(sorted(set(names))) if names else "tidak ada"), flush=True)
            if wheel_ok:
                print("\nKESIMPULAN: wheel sintetis SUDAH didukung — kembalikan profil "
                      "Spotify ke type: scroll, lalu konfirmasi manual: putar knob di "
                      "Spotify.", flush=True)
                exit_code[0] = 0
            else:
                print("\nKESIMPULAN: wheel sintetis masih dibuang — pertahankan workaround "
                      "PageUp/PageDown (config saat ini).", flush=True)
                exit_code[0] = 1
        finally:
            GLib.idle_add(Gtk.main_quit)

    threading.Thread(target=run_sequence, daemon=True).start()
    Gtk.main()
    return exit_code[0]


if __name__ == "__main__":
    sys.exit(main())