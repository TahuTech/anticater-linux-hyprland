#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe ANTICATER VK-01 lewat hidraw (dongle / kabel USB).

Zero dependensi (stdlib saja). Butuh akses baca-tulis ke /dev/hidraw*.
Kalau PermissionError: pasang rules/60-anticater.rules lalu reload udev,
atau jalankan dengan sudo.

Pemakaian:
    python3 probes/probe_hidraw.py --list
    python3 probes/probe_hidraw.py --query-led
    python3 probes/probe_hidraw.py --set-led 3 --color ff0000
    python3 probes/probe_hidraw.py --set-led 3 --colors ff0000,00ff00,0000ff,ffffff,ff00ff,00ffff
    python3 probes/probe_hidraw.py --set-led 1 --variant knobctl
    python3 probes/probe_hidraw.py --blink ff0000 --blink-ms 500 --times 8
    python3 probes/probe_hidraw.py --sniff 60
    python3 probes/probe_hidraw.py --query-layer 1
    python3 probes/probe_hidraw.py --probe-battery
    python3 probes/probe_hidraw.py --hexdump

Referensi protokol: docs/device-protocol.md
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import select
import sys
import time

REPORT_ID = 0x03
PAYLOAD_LEN = 64

LED_MODE_NAMES = {
    0: "off",
    1: "merah (constant)",
    2: "hijau (constant)",
    3: "custom 6 LED",
    4: "pelangi rotasi searah jarum jam",
    5: "pelangi rotasi berlawanan jarum jam",
}


# ---------------------------------------------------------------- sysfs

def read_file(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def uevent_value(uevent: bytes, key: str) -> str:
    for line in uevent.decode(errors="replace").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def hidraw_entries() -> list[dict]:
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.join(node, "device")
        entries = {
            "node": os.path.basename(node),
            "path": f"/dev/{os.path.basename(node)}",
            "hid_id": uevent_value(read_file(os.path.join(dev, "uevent")), "HID_ID"),
            "name": uevent_value(read_file(os.path.join(dev, "uevent")), "HID_NAME"),
            "desc": read_file(os.path.join(dev, "report_descriptor")),
        }
        found.append(entries)
    return found


def id_to_vid_pid(hid_id: str) -> str:
    # format: 0003:0000514C:00008850  (bus:vendor:product)
    m = re.search(r":([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})$", hid_id)
    if not m:
        return hid_id
    return f"{m.group(1)[4:]:<4}:{m.group(2)[4:]}"


def is_anticater(entry: dict) -> bool:
    hid_id = entry["hid_id"].lower()
    # vendor 0x514C = LiQi Technology (LQKJ) — pabrikan ANTICATER.
    # Unit di pasaran (VK-01 / MINI) beda product id (0x8850 / 0x4155, dst).
    return "514c" in hid_id or "anticater" in entry["name"].lower()


def is_vendor_interface(entry: dict) -> bool:
    # usage page 0xFF00 (vendor) di report descriptor -> interface config/LED
    return b"\x06\x00\xff" in entry["desc"]


# ---------------------------------------------------------------- hidraw io

def to_report(payload: bytes) -> bytes:
    if len(payload) > PAYLOAD_LEN:
        raise ValueError(f"payload > {PAYLOAD_LEN} byte")
    return bytes([REPORT_ID]) + payload.ljust(PAYLOAD_LEN, b"\x00")


def write_report(fd: int, payload: bytes) -> None:
    try:
        os.write(fd, to_report(payload))
    except OSError as e:
        sys.exit(
            f"Gagal menulis ke interface (errno {e.errno}): {e.strerror}.\n"
            "Kalau EINVAL: interface ini tidak punya report id 0x03 — kemungkinan\n"
            "dongle input-only (tanpa interface vendor 0xFF00). Colok device via\n"
            "kabel USB (atau pakai BLE) lalu coba lagi."
        )


def read_report(fd: int, timeout: float = 1.0) -> bytes | None:
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    return os.read(fd, 4096)


def open_rw(path: str) -> int:
    try:
        return os.open(path, os.O_RDWR)
    except PermissionError:
        sys.exit(
            f"Tidak bisa buka {path}: PermissionError.\n"
            "Solusi: pasang rules/60-anticater.rules lalu:\n"
            "  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
            "atau jalankan probe ini dengan sudo."
        )


def do_init(fd: int) -> None:
    """Init dulu sebelum command lain — tanpa ini device tidak merespons."""
    write_report(fd, b"\xfb\xfb\xfb")
    resp = read_report(fd, timeout=1.5)
    if resp is None:
        print("[i] init: tidak ada respons dalam 1.5s "
              "(mungkin bukan interface vendor, atau device tidur — putar knobnya).")
    else:
        print(f"[i] init response ({len(resp)} byte): {resp.hex(' ')}")


def pick_device(args) -> str:
    entries = hidraw_entries()
    cands = [e for e in entries if is_anticater(e)]
    if not cands:
        print("Tidak menemukan interface ANTICATER. Jalankan --list dulu.")
        print("Saran: dongle/kabel tersambung? udev rules sudah terpasang?")
        sys.exit(2)
    if args.device:
        return args.device
    vendor = [e for e in cands if is_vendor_interface(e)]
    if vendor:
        return vendor[0]["path"]
    print(f"[i] Interface vendor (usage page 0xFF00) tidak terdeteksi — ini "
          f"kemungkinan dongle input-only.")
    print(f"    Channel LED/binding hanya ada saat device dicolok via kabel. "
          f"Menggunakan {cands[0]['path']}...")
    return cands[0]["path"]


# ---------------------------------------------------------------- commands

def cmd_list() -> int:
    entries = hidraw_entries()
    if not entries:
        print("Tidak ada hidraw di sistem ini.")
        return 0
    print(f"{'NODE':<8} {'VID:PID':<14} {'NAMA':<28} {'VENDOR':<7} ANTICATER")
    for e in entries:
        vendor = "ya" if is_vendor_interface(e) else "-"
        mark = "YA" if is_anticater(e) else ""
        print(f"{e['node']:<8} {id_to_vid_pid(e['hid_id']):<14} "
              f"{(e['name'] or '')[:28]:<28} {vendor:<7} {mark}")
    return 0


def cmd_hexdump() -> int:
    for e in hidraw_entries():
        if not is_anticater(e):
            continue
        print(f"== {e['node']}  {e['path']}  {e['name']!r}  HID_ID={e['hid_id']}")
        print(hexdump(e["desc"]))
        print()
    return 0


def cmd_query_led(args, fd: int) -> int:
    do_init(fd)
    write_report(fd, b"\xfa\xb0\x00")
    resp = read_report(fd, timeout=1.5)
    if resp is None:
        print("Tidak ada respons untuk query LED (fa b0 00).")
        return 1
    print(f"raw ({len(resp)} byte): {resp.hex(' ')}")
    if len(resp) >= 3 and resp[1] == 0xFA:
        mode = resp[2]
        names = LED_MODE_NAMES
        print(f"mode LED saat ini: {mode} ({names.get(mode, 'tidak dikenal')})")
        if len(resp) >= 23:
            colors = [resp[3 + i * 3: 6 + i * 3] for i in range(6)]
            print("warna Mode 3:", " ".join(f"#{c.hex().upper()}" for c in colors))
    return 0


def parse_colors(arg: str) -> list[bytes]:
    parts = [p.strip() for p in arg.split(",")]
    colors = []
    for p in parts:
        if len(p) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", p):
            sys.exit(f"warna harus RRGGBB (mis. ff0000), dapat: {p!r}")
        colors.append(bytes.fromhex(p))
    if len(colors) == 1:
        colors = colors * 6
    if len(colors) != 6:
        sys.exit("--color/--colors butuh 1 atau 6 warna")
    return colors


def _set_led(fd: int, mode: int, colors: list[bytes]) -> None:
    for idx in range(3):
        p = bytearray(PAYLOAD_LEN)
        p[0] = 0xFE
        p[1] = 0xB0
        p[2] = idx
        p[3] = mode if idx == 0 else 0x00
        off = 4
        for c in colors:
            p[off], p[off + 1], p[off + 2] = c[0], c[1], c[2]
            off += 3
        os.write(fd, bytes([REPORT_ID]) + bytes(p))


def cmd_set_led(args, fd: int) -> int:
    do_init(fd)
    if args.variant == "knobctl":
        # format CH57x/knobctl: fe b0 <layer+1> 08 ... 50+mode, lalu finish
        msg = bytes([0xFE, 0xB0, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00,
                     0x01, 0x00, 0x50 + args.set_led])
        write_report(fd, msg)
        write_report(fd, b"\xfd\xfe\xff")
        print(f"knobctl variant: mode {args.set_led} terkirim.")
        return 0

    colors = parse_colors(args.color or args.colors)
    _set_led(fd, args.set_led, colors)
    hex_colors = ",".join(f"#{c.hex().upper()}" for c in colors)
    print(f"mode {args.set_led} terkirim (varian vk01/WebHID): {hex_colors}")
    return 0


def cmd_blink(args, fd: int) -> int:
    on = parse_colors(args.blink)[0]
    off = bytes.fromhex("000000")
    do_init(fd)
    for i in range(args.times):
        _set_led(fd, 3, [on] * 6)
        time.sleep(args.blink_ms / 1000.0)
        _set_led(fd, 3, [off] * 6)
        if i < args.times - 1:
            time.sleep(args.blink_ms / 1000.0)
    _set_led(fd, 3, [on] * 6)
    print(f"blink selesai: {args.times}x @ {args.blink_ms}ms, warna #{on.hex().upper()}")
    return 0


def cmd_sniff(args, fd: int) -> int:
    do_init(fd)
    print(f"Sniff {args.sniff}s... putar/tekan knob, colok/lepas kabel, "
          "atau biarkan idle (cari report berkala seperti baterai).")
    start = time.monotonic()
    deadline = start + args.sniff
    counts: dict[str, int] = {}
    first_seen: dict[str, tuple[float, bytes]] = {}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        resp = read_report(fd, timeout=min(remaining, 0.5))
        if resp is None:
            continue
        t = time.monotonic() - start
        sig = resp[:4].hex()
        counts[sig] = counts.get(sig, 0) + 1
        if sig not in first_seen:
            first_seen[sig] = (t, resp)
            print(f"[{t:7.1f}] (pertama) {resp.hex(' ')}")

    print("\nRingkasan paket masuk:")
    for sig, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        t0, data = first_seen[sig]
        print(f"  {sig}  x{n:<4} pertama @{t0:.1f}s: {data.hex(' ')[:88]}")
    print("\nCek baterai: paket yang muncul berkala dengan satu byte 0..100 ")
    print("di posisi tetap -> kemungkinan besar persentase baterai.")
    return 0


def cmd_query_layer(args, fd: int) -> int:
    do_init(fd)
    write_report(fd, bytes([0xFA, 0x19, 0x00, args.query_layer]))
    got = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(got) < 0x19:
        resp = read_report(fd, timeout=0.5)
        if resp is None or len(resp) < 2 or resp[1] != 0xFA:
            continue
        got.append(resp)
    print(f"respons: {len(got)}/25 (0x19)")
    for r in got:
        control = r[2]
        layer = r[3]
        ktype = r[4]
        code = (r[11] << 8) | r[8] if len(r) > 11 else r[8]
        print(f"  slot={control:<3} layer={layer} type={ktype} code={code:#06x}")
    return 0


# Query spekulatif: opcode yang belum terdokumentasi, dicoba untuk
# menemukan saluran baterai. Kemungkinan besar banyak yang tidak merespons.
BATTERY_QUERIES = [
    b"\xfa\xb1\x00", b"\xfa\xb1\x01", b"\xfa\xb2\x00", b"\xfa\x18\x00",
    b"\xfa\x1a\x00", b"\xfa\xff\x00", b"\xfa\x00\x00", b"\xfa\xb0\x01",
    b"\xfa\xb0\x02", b"\xfb\x00\x00", b"\xff\x00\x00", b"\xfa\x19\x01",
]


def cmd_probe_battery(args, fd: int) -> int:
    do_init(fd)
    print("Mencoba query spekulatif (opcode belum terdokumentasi).")
    hit = 0
    for q in BATTERY_QUERIES:
        write_report(fd, q)
        for _ in range(2):
            resp = read_report(fd, timeout=0.6)
            if resp is None:
                break
            hit += 1
            print(f"  query {q.hex(' '):<14} -> {resp.hex(' ')}")
    if hit == 0:
        print("Tidak ada respons untuk query spekulatif mana pun.")
        print("Kemungkinan saluran baterai tidak ada di jalur USB.")
    return 0


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:04x}  {hexpart:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="/dev/hidrawN spesifik (default: deteksi otomatis)")
    ap.add_argument("--variant", choices=["vk01", "knobctl"], default="vk01",
                    help="varian format protokol (default vk01/WebHID, terverifikasi di Linux)")

    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="daftar hidraw + deteksi device")
    group.add_argument("--hexdump", action="store_true", help="hexdump report descriptor")
    group.add_argument("--query-led", action="store_true", help="baca mode LED + warna (fa b0 00)")
    group.add_argument("--set-led", type=int, metavar="MODE",
                       help="set mode LED 0-5 (dengan --color/--colors untuk mode 3)")
    group.add_argument("--blink", metavar="RRGGBB",
                       help="simulasi blink: toggle warna / hitam (mode 3)")
    group.add_argument("--sniff", type=int, metavar="DETIK",
                       help="dump semua input report selama N detik")
    group.add_argument("--query-layer", type=int, choices=[1, 2, 3], metavar="LAYER",
                       help="baca binding layer (fa 19 00 <layer>)")
    group.add_argument("--probe-battery", action="store_true",
                       help="coba opcode query spekulatif untuk baterai")

    ap.add_argument("--color", metavar="RRGGBB", help="satu warna -> semua 6 LED (mode 3)")
    ap.add_argument("--colors", metavar="RRGGBB,...",
                    help="6 warna, satu per LED (mode 3)")
    ap.add_argument("--blink-ms", type=int, default=500, help="interval blink (ms)")
    ap.add_argument("--times", type=int, default=8, help="jumlah kedip")
    args = ap.parse_args()

    if args.set_led is not None and args.variant == "vk01" and not (args.color or args.colors):
        sys.exit("--set-led (varian vk01) butuh --color atau --colors")

    if args.list:
        return cmd_list()
    if args.hexdump:
        return cmd_hexdump()

    path = pick_device(args)
    fd = open_rw(path)
    try:
        if args.query_led:
            return cmd_query_led(args, fd)
        if args.set_led is not None:
            return cmd_set_led(args, fd)
        if args.blink:
            return cmd_blink(args, fd)
        if args.sniff:
            return cmd_sniff(args, fd)
        if args.query_layer:
            return cmd_query_layer(args, fd)
        if args.probe_battery:
            return cmd_probe_battery(args, fd)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())