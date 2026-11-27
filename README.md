# anticater-linux-hyprland

Kontrol knob **ANTICATER VK-01** di Linux (target: CachyOS + Hyprland):
probe HID, dokumentasi protokol, dan daemon remapping aksi per-aplikasi.

Port Linux dari [dreamug/anticater-knob-control](https://github.com/dreamug/anticater-knob-control)
(macOS/Swift). Perangkat sudah di-reverse-engineer komunitas
([x0rloser](https://github.com/x0rloser/anticater_vk01),
[zackwag](https://github.com/zackwag/anticater-web),
[knobctl](https://github.com/ciiiii/knobctl)) — kita pakai temuan itu,
bukan bongkar ulang.

## Status

| Bagian | Status |
|---|---|
| Probe input (evdev) | ✅ terverifikasi di hardware (kabel `514c:8850`) |
| Probe konfig/LED (hidraw) | ✅ terverifikasi — varian CH57x/knobctl |
| Protokol (binding + LED) | ✅ terdokumentasi — `docs/device-protocol.md` |
| Daemon per-app (evdev → hyprctl → uinput/shell) | ✅ terverifikasi live (grab, profil, aksi, press+rotate) |
| GUI (editor config + kontrol daemon, web lokal) | ✅ siap — `gui/anticater-gui.py` |
| Saluran baterai USB | ❌ dongle input-only; kabel: sniff 30s = nol paket. BLE: belum ditest (`upower -d`) |
| Packaging (PKGBUILD) | ✅ siap — `packaging/PKGBUILD` (AUR: `anticater-linux-hyprland-git`; submit manual) |

## Daemon per-app — `daemon/anticaterd.py`

```
knob (evdev, grab eksklusif) → gestur → profil WM_CLASS → aksi
hyprctl -j activewindow      → class → app-mapping.json → key/shortcut/mouse/scroll/command
LED ring (saat kabel): hijau = profil global, pelangi = profil app; mati saat daemon berhenti
```

- **Input**: auto-detect evdev `514c` + Keyboard (`--device` untuk paksa path);
  `EVIOCGRAB` eksklusif — event knob tidak lagi sampai ke Hyprland/sistem.
- **Aksi**: sintesis input via **uinput** (python-evdev, tanpa ydotool):
  `key` = satu tombol/media, `shortcut` = kombinasi, `mouse` = klik,
  `scroll` = roda, `command` = argv shell. `noop` = abaikan.
  Klik/scroll otomatis mengarahkan pointer ke tengah window aktif lalu
  mengembalikannya — gestur selalu mengenai app fokus, bukan app di bawah
  kursor (lewat `hyprctl cursorpos` + `hl.dsp.cursor.move`).

  > **Catatan Hyprland ≥ 0.55** (diverifikasi di 0.57.2, CachyOS): button &
  > axis dari perangkat sintetis (uinput) **tidak dikirim ke aplikasi**
  > (regresi compositor, lihat hyprwm/Hyprland #15423 — motion & key tetap
  > jalan). Untuk scroll di profil app, pakai `key` PageUp/PageDown, bukan
  > `scroll`; `mouse`/`scroll` tetap didukung untuk environment lain
  > (X11, Hyprland lama).
- **Profil**: `apps[WM_CLASS]` → `apps[wmclass.lower()]` → `global`.
  `kitty`, `brave-browser`, `code` (VS Code), dll — kunci bebas isi sendiri.
- **Press+rotate**: aksi `press` ditunda 180 ms; jika rotasi datang saat knob
  ditahan, hanya aksi `press_rotate_*` yang jalan (tanpa `press` ganda).
- **LED**: write-only + efek saja (lihat docs); hanya bisa saat kabel.
- **Shutdown**: SIGINT/SIGTERM → ungrab + LED mati.

### Jalankan

```bash
# prasyarat: aturan udev di bawah + user di grup `input` + python-evdev
sudo pacman -S python-evdev

python3 daemon/anticaterd.py --check-config   # validasi config
python3 daemon/anticaterd.py --list-devices   # node knob + saluran LED
python3 daemon/anticaterd.py --dry-run        # uji tanpa eksekusi/grab
python3 daemon/anticaterd.py                  # jalankan (grab + eksekusi)
```

Auto-start di Hyprland (`hyprland.conf`):

```text
exec-once = python3 /path/ke/anticater-linux-hyprland/daemon/anticaterd.py
```

Config dicari berurutan: `$ANTICATER_CONFIG` → `~/.config/anticater/app-mapping.json`
→ `/etc/anticater/app-mapping.json` (default saat terpasang via paket) → `config/app-mapping.json`
(repo). `--config` untuk path khusus.

### Format config — `config/app-mapping.json`

Kompatibel dengan proyek macOS [dreamug/anticater-knob-control]
(https://github.com/dreamug/anticater-knob-control) `config/app-mapping.json`;
perbedaan: **kunci app = WM_CLASS** (bukan bundle-id), dan aksi `command`
memakai array argv (bukan string shell).

```jsonc
{
  "global": {
    "rotate_left":        { "type": "key",      "keys": ["volume_down"] },
    "rotate_right":       { "type": "key",      "keys": ["volume_up"] },
    "press":              { "type": "key",      "keys": ["play_pause"] },
    "press_rotate_left":  { "type": "key",      "keys": ["previous_track"] },
    "press_rotate_right": { "type": "key",      "keys": ["next_track"] }
  },
  "apps": {
    "brave-browser": {
      "rotate_left":       { "type": "shortcut", "keys": ["control", "shift", "tab"] },
      "rotate_right":      { "type": "shortcut", "keys": ["control", "tab"] },
      "press":             { "type": "shortcut", "keys": ["control", "r"] },
      "press_rotate_left": { "type": "shortcut", "keys": ["control", "left"] },
      "press_rotate_right":{ "type": "shortcut", "keys": ["control", "right"] }
    },
    "Spotify": {
      "rotate_left":       { "type": "key", "keys": ["pageup"] },
      "rotate_right":      { "type": "key", "keys": ["pagedown"] }
    }
  }
}
```

- Slot: `rotate_left`, `rotate_right`, `press`, `press_rotate_left`, `press_rotate_right`.
- Tipe: `key`/`shortcut` (nama kunci: `control`, `shift`, `alt`, `command`,
  huruf/angka, `f1..f12`, `tab`, `return`, media `volume_up`/`volume_down`/
  `play_pause`/`next_track`/`previous_track`, dll — cek `--check-config`),
  `mouse` (`keys: ["left"|"right"|"middle"]`), `scroll` (`keys: ["up"|"down"|"left"|"right"]`),
  `command` (`keys`: array argv, mis. `["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"]`),
  `noop`.
- `description` bebas, dipakai di log/GUI nanti.

## GUI — `gui/anticater-gui.py`

Editor `app-mapping.json` + kontrol daemon dalam satu antarmuka web lokal
(tanpa dependensi tambahan; validasi kunci dipinjam dari daemon):

```bash
python3 gui/anticater-gui.py --open     # buka http://127.0.0.1:8099 di browser
python3 gui/anticater-gui.py --config ~/.config/anticater/app-mapping.json
```

Fitur:

- Edit 5 slot gestur per profil (global & per WM_CLASS), simpan atomik
  (temp + rename); daemon **hot-reload** — edit langsung aktif tanpa restart
- Template siap pakai: Browser, Menulis kode, Media, Desain, Kliping
- Tambah app dari daftar WM_CLASS window aktif (`hyprctl clients`) atau manual
- Tombol nyalakan/matikan daemon + log live; badge fokus window aktif
- Aksi `command` ditulis sebagai string argv (dipecah `shlex` saat simpan)
- Saat GUI ditutup, daemon yang GUI ini hidupkan ikut dimatikan
  (daemon yang dijalankan terpisah tidak disentuh)

## Probe

```bash
# probe input (perlu python-evdev)
python3 probes/probe_evdev.py --list
python3 probes/probe_evdev.py

# probe konfig/LED (device harus DICOLOK KABEL — dongle input-only)
python3 probes/probe_hidraw.py --list
python3 probes/probe_hidraw.py --query-led
python3 probes/probe_hidraw.py --set-led 2 --variant knobctl
python3 probes/probe_hidraw.py --sniff 120
```

Perintah detail: `probes/README.md`.

## Struktur

```text
daemon/
  anticaterd.py          daemon per-app: evdev+EVIOCGRAB → hyprctl → aksi uinput/shell + LED status
config/
  app-mapping.json       config default (format kompatibel repo macOS)
gui/
  anticater-gui.py       editor web lokal + kontrol daemon (stdlib)
probes/
  probe_evdev.py         input: gestur knob via evdev
  probe_hidraw.py        konfig: LED, query, sniff baterai, binding
docs/
  device-protocol.md     protokol HID konsolidasi (varian vk01 & knobctl — CH57x terverifikasi)
rules/
  60-anticater.rules     udev: akses hidraw + input tanpa sudo
```

## Kredit

- [dreamug/anticater-knob-control](https://github.com/dreamug/anticater-knob-control) — proyek asli
- [x0rloser/anticater_vk01](https://github.com/x0rloser/anticater_vk01) — RE read + LED
- [zackwag/anticater-web](https://github.com/zackwag/anticater-web) — protokol lengkap WebHID
- [ciiiii/knobctl](https://github.com/ciiiii/knobctl) — varian protokol CH57x
- [zerni/ha-ble-knob](https://github.com/zerni/ha-ble-knob) — input BLE Linux
- [frostvalley-hussey/vk01-anticater](https://github.com/frostvalley-hussey/vk01-anticater) — daemon macOS per-app