# anticater-linux-hyprland

Kontrol knob **ANTICATER VK-01** di Linux (target: CachyOS + Hyprland):
probe HID, dokumentasi protokol, dan (rencana) daemon remapping aksi
per-aplikasi.

Port Linux dari [dreamug/anticater-knob-control](https://github.com/dreamug/anticater-knob-control)
(macOS/Swift). Perangkat sudah di-reverse-engineer komunitas
([x0rloser](https://github.com/x0rloser/anticater_vk01),
[zackwag](https://github.com/zackwag/anticater-web),
[knobctl](https://github.com/ciiiii/knobctl)) — kita pakai temuan itu,
bukan bongkar ulang.

## Status

| Bagian | Status |
|---|---|
| Probe input (evdev) | ✅ siap + **terverifikasi di dongle** — `probes/probe_evdev.py` |
| Identifikasi hardware | ✅ dongle `514c:4155` terdeteksi; input-only — config/LED via kabel/BLE |
| Probe konfig/LED (hidraw) | ✅ siap — `probes/probe_hidraw.py` (tes di device via kabel) |
| Protokol (binding + LED) | ✅ terdokumentasi — `docs/device-protocol.md` |
| Saluran baterai | ⚠️ dongle: mustahil (input-only); tes `--sniff` saat kabel |
| Daemon per-app (evdev → hyprctl → ydotool/shell) | ⏳ rencana |
| GUI | ⏳ rencana |

## Mulai cepat

```bash
# 1. izin akses hidraw/input
sudo cp rules/60-anticater.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# (logout/login atau reboot sekali; user harus di grup `input`)

# 2. probe input (perlu python-evdev)
sudo pacman -S python-evdev
python3 probes/probe_evdev.py --list
python3 probes/probe_evdev.py

# 3. probe konfig/LED (device harus DICOLOK KABEL — dongle input-only)
python3 probes/probe_hidraw.py --list
python3 probes/probe_hidraw.py --query-led
python3 probes/probe_hidraw.py --set-led 3 --color ff0000
python3 probes/probe_hidraw.py --blink ff0000 --blink-ms 500 --times 8
python3 probes/probe_hidraw.py --sniff 120
```

Perintah detail: `probes/README.md`. Output probe (terutama `--list`,
`--query-led`, `--sniff`) dipakai untuk verifikasi device di hardware nyata.

## Struktur

```text
probes/
  probe_evdev.py       input: gestur knob via evdev
  probe_hidraw.py      konfig: LED, query, sniff baterai, binding
  README.md
docs/
  device-protocol.md   protokol HID konsolidasi (varian vk01 & knobctl)
rules/
  60-anticater.rules   udev: akses hidraw + input tanpa sudo
```

## Rencana daemon (belum dibuat)

1. Baca gestur dari evdev (device "ANTICATER_MINI", `EVIOCGRAB` eksklusif)
2. App fokus → `hyprctl -j activewindow` → `class` (pengganti bundle ID)
3. Config `app-mapping.json` — format kompatibel repo macOS, kunci = WM_CLASS
4. Eksekusi aksi: `ydotool` (shortcut/media/mouse/scroll), shell
   (`hyprctl dispatch`, `wpctl`, `playerctl`), noop
5. Status LED ring: warna per profil app; blink = transisi/lock;
   merah kedip = baterai lemah (hanya jika saluran baterai ditemukan)
6. Packaging PKGBUILD (AUR) + `exec-once` di `hyprland.conf`

## Kredit

- [dreamug/anticater-knob-control](https://github.com/dreamug/anticater-knob-control) — proyek asli
- [x0rloser/anticater_vk01](https://github.com/x0rloser/anticater_vk01) — RE read + LED
- [zackwag/anticater-web](https://github.com/zackwag/anticater-web) — protokol lengkap WebHID
- [ciiiii/knobctl](https://github.com/ciiiii/knobctl) — varian protokol CH57x
- [zerni/ha-ble-knob](https://github.com/zerni/ha-ble-knob) — input BLE Linux
- [frostvalley-hussey/vk01-anticater](https://github.com/frostvalley-hussey/vk01-anticater) — daemon macOS per-app