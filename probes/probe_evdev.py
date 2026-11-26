#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe input dari ANTICATER knob (dongle / Bluetooth / kabel).

Membutuhkan python-evdev:
    sudo pacman -S python-evdev

Pemakaian:
    python3 probes/probe_evdev.py --list
    python3 probes/probe_evdev.py              # pasang ke device pertama yang cocok
    python3 probes/probe_evdev.py --duration 30
    python3 probes/probe_evdev.py --device /dev/input/eventX

Kode key default pabrik (evdev):
    rotasi kiri     -> KEY_VOLUMEDOWN (114)
    rotasi kanan    -> KEY_VOLUMEUP   (115)
    tekan           -> KEY_MUTE       (113)
    tekan+rot kiri  -> KEY_BRIGHTNESSDOWN (224)
    tekan+rot kanan -> KEY_BRIGHTNESSUP   (225)

Catatan: probe ini pasif (tidak grab). Daemon nanti memakai EVIOCGRAB
supaya event tidak bocor ke Hyprland / app lain.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python-evdev belum terpasang. Install:  sudo pacman -S python-evdev")

VENDOR_LQKJ = 0x514C    # LQKJ — dongle & kabel
PRODUCT_VK01 = 0x8850
VENDOR_APPLE = 0x05AC   # spoof Apple yang dipakai mode BLE
PRODUCT_APPLE = 0x022C

EXPECTED_KEYS = {
    114: "KEY_VOLUMEDOWN     -> rotasi kiri",
    115: "KEY_VOLUMEUP       -> rotasi kanan",
    113: "KEY_MUTE           -> tekan",
    224: "KEY_BRIGHTNESSDOWN -> tekan + rotasi kiri",
    225: "KEY_BRIGHTNESSUP   -> tekan + rotasi kanan",
}

BUS_NAMES = {0x03: "usb", 0x05: "bluetooth", 0x06: "virtual", 0x18: "i2c"}


def match_reason(dev: InputDevice) -> str | None:
    info = dev.info
    name = (dev.name or "").lower()
    if "anticater" in name:
        return "nama"
    if info.vendor == VENDOR_LQKJ:
        return f"vendor LiQi (514c:{info.product:04x})"
    if info.vendor == VENDOR_APPLE and info.product == PRODUCT_APPLE and "anticater" in name:
        return "vid:pid 05ac:022c (spoif Apple, BLE)"
    return None


def cmd_list() -> int:
    print(f"{'DEVICE':<22} {'BUS':<11} {'VID:PID':<12} {'NAMA':<32} MATCH")
    found = 0
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError as e:
            print(f"{path:<22} (tidak bisa dibuka: {e})")
            continue
        info = dev.info
        reason = match_reason(dev)
        if reason:
            found += 1
        bus = BUS_NAMES.get(info.bustype, hex(info.bustype))
        print(f"{path:<22} {bus:<11} {info.vendor:04x}:{info.product:04x} "
              f"{(dev.name or '')[:32]:<32} {reason or ''}")
        dev.close()
    if found == 0:
        print("\nBelum ada device ANTICATER terdeteksi.")
        print("Pastikan dongle/kabel tersambung, atau mode BLE sudah di-pairing.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    if args.device:
        device = InputDevice(args.device)
    else:
        matched = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            if match_reason(dev):
                matched.append(dev)
            else:
                dev.close()
        if not matched:
            print("Tidak ada device ANTICATER. Jalankan --list dulu.")
            return 1
        device = matched[0]
        for extra in matched[1:]:
            extra.close()

    info = device.info
    print(f"Memantau: {device.path}  nama={device.name!r}  "
          f"bus={BUS_NAMES.get(info.bustype, hex(info.bustype))}  "
          f"vid:pid={info.vendor:04x}:{info.product:04x}")
    print("\nKemampuan key (default pabrik):")
    caps = device.capabilities(verbose=False)
    keys = {k for t, lst in caps.items() if t == ecodes.EV_KEY for k in lst}
    for code, label in sorted(EXPECTED_KEYS.items()):
        ok = "OK" if code in keys else "-"
        print(f"  [{ok:>2}] {code:<4} {label}")
    print("\nPutar/tekan knob untuk melihat event. Ctrl-C untuk berhenti.")

    counts: dict[int, int] = {}
    start = time.monotonic()
    deadline = start + args.duration if args.duration else None
    try:
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY or event.value != 1:
                continue
            counts[event.code] = counts.get(event.code, 0) + 1
            name = ecodes.KEY.get(event.code, f"KEY_{event.code}")
            print(f"[{time.monotonic() - start:7.3f}] code={event.code:<4} "
                  f"{name:<28} value={event.value}")
            if deadline and time.monotonic() >= deadline:
                break
    except KeyboardInterrupt:
        pass
    finally:
        device.close()

    print("\nRingkasan (hanya keydown):")
    for code in sorted(counts):
        hint = EXPECTED_KEYS.get(code, "")
        print(f"  {code:<4} {ecodes.KEY.get(code, '?'):<28} x{counts[code]}  {hint}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="daftar semua device input")
    ap.add_argument("--device", help="/dev/input/eventX spesifik")
    ap.add_argument("--duration", type=int, metavar="DETIK",
                    help="berhenti otomatis setelah N detik")
    args = ap.parse_args()

    if args.list:
        return cmd_list()
    return cmd_watch(args)


if __name__ == "__main__":
    sys.exit(main())