# Probe

Dua probe untuk verifikasi device di hardware nyata (dongle / kabel / BLE).

## `probe_evdev.py` — input (gestur)

```bash
sudo pacman -S python-evdev          # satu-satunya dependensi
python3 probe_evdev.py --list        # deteksi device ANTICATER
python3 probe_evdev.py               # pantau event gestur (Ctrl-C berhenti)
python3 probe_evdev.py --duration 30
```

Yang diverifikasi: kode key default pabrik — 114/115 (rotasi), 113 (tekan),
224/225 (tekan+rotasi). Paste hasil `--list` dan ringkasan event ke issue
kalau ada yang aneh.

## `probe_hidraw.py` — konfigurasi & LED (zero dependensi)

```bash
python3 probe_hidraw.py --list                          # deteksi interface vendor
python3 probe_hidraw.py --query-led                     # baca mode + warna LED
python3 probe_hidraw.py --set-led 3 --color ff0000      # mode 3, semua LED merah
python3 probe_hidraw.py --set-led 3 --colors ff0000,00ff00,0000ff,ffffff,ff00ff,00ffff
python3 probe_hidraw.py --set-led 1 --variant knobctl   # format alternate (CH57x)
python3 probe_hidraw.py --blink ff0000 --blink-ms 500 --times 8
python3 probe_hidraw.py --sniff 120                     # cari report berkala (baterai?)
python3 probe_hidraw.py --query-layer 1                 # baca binding di memori device
python3 probe_hidraw.py --probe-battery                 # opcode query spekulatif
python3 probe_hidraw.py --hexdump                       # report descriptor
```

### Checklist verifikasi di device

Status nyata (terverifikasi 2026-11-27 di dongle):

- Dongle `514c:4155` = interface keyboard **input-only** (tanpa 0xFF00).
  Input gestur OK; config/LED **tidak bisa** lewat dongle.
- Channel config/LED baru ada saat **device dicolok langsung via kabel**
  (kemungkinan VID:PID `514c:8850` + interface vendor 0xFF00 — belum
  terverifikasi di hardware).

Urutan tes:

1. `probe_evdev.py --list` — node `514c:4155 … Keyboard` muncul ✓
2. `probe_evdev.py` — putar/tekan knob: keycode 113-115/224-225 muncul ✓
3. Colok device via **kabel**, lalu `probe_hidraw.py --list` —
   muncul node dengan kolom `VENDOR = ya` (descriptor `06 00 ff`) ✓
4. `--query-led` mengembalikan mode + 6 warna ✓
5. `--set-led 3 --color <warna>` mengubah ring LED ✓
6. `--sniff` + putar/tekan/colok-lepas: ada paket masuk; perhatikan paket
   yang muncul berkala saat idle (kandidat persen baterai)
7. `--query-layer` / `--probe-battery` (eksperimen)

### Permission

`/dev/hidraw*` dan `/dev/input/event*` perlu akses baca-tulis. Cara:

```bash
sudo cp ../rules/60-anticater.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input $USER   # lalu logout/login
# atau jalankan probe dengan sudo (sementara)
```

### Catatan penting

- Hanya **satu** program boleh memegang interface vendor HID. Tutup
  `anticater-web` / official app sebelum menjalankan probe.
- Mode LED persist di memori device; kalau device tidak merespons lagi,
  cabut dan colok kembali.
- `--variant knobctl` ada karena dua varian firmware dari VID/PID yang sama
  terdokumentasikan dengan layout berbeda (lihat docs/device-protocol.md).
  Default `vk01` adalah yang terbukti jalan di Linux (WebHID/Chromium).