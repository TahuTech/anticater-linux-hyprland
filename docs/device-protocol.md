# Protokol ANTICATER VK-01

Ringkasan hasil reverse-engineering komunitas, dikonsolidasi untuk port Linux.
Sumber: [x0rloser/anticater_vk01](https://github.com/x0rloser/anticater_vk01),
[zackwag/anticater-web](https://github.com/zackwag/anticater-web) (terverifikasi
base di fixture byte nyata, jalan di Linux via WebHID),
[ciiiii/knobctl](https://github.com/ciiiii/knobctl) (varian CH57x, layout beda),
[zerni/ha-ble-knob](https://github.com/zerni/ha-ble-knob).

## Identitas per transport

| Transport | VID:PID | Catatan |
|---|---|---|
| Dongle (USB 2.4GHz) | `0x514C:0x4155` (terverifikasi) | 1 interface HID input-only — tanpa channel config/LED |
| Kabel (USB) | `0x514C:0x8850` (dokumentasi komunitas) | 2 interface HID: vendor 0xFF00 + keyboard |
| Bluetooth (BLE) | spoof `0x05ac:0x022c` (Apple) | ini sebabnya app macOS hardcode ID Apple |

Serial dongle terverifikasi: `EB6012112xxxx` — prefix sama dengan capture
[x0rloser](https://github.com/x0rloser/anticater_vk01). Match di Linux
sebaiknya berdasarkan **vendor `0x514C`** (LiQi Technology = LQKJ, pemilik
merek ANTICATER; bundle app `COM.LQKJ.KEYBOARD.ANTICATER`) — product id beda
antar unit/penerima (`0x8850`, `0x4155`, …). Jangan match VID Apple
(tabrakan dengan hardware Apple asli).

## Struktur USB

**Kabel / device colok langsung** (dokumentasi komunitas, x0rloser):

```
Interface 0 — vendor HID: usage page 0xFF00, usage 0x01, report id 0x03,
              64-byte IN + 64-byte OUT.  -> channel konfigurasi & LED
Interface 1 — keyboard HID: report id 0x01/0x02/0x04/0x05.
              -> event input (gestur knob)
```

**Dongle 2.4GHz (terverifikasi di hardware, VID:PID `514c:4155`)**: hanya
satu interface HID keyboard/mouse/consumer (report id 0x01/0x02/0x04/0x05),
1 endpoint interrupt IN. Tidak ada interface vendor, tidak ada endpoint OUT →
**dongle tidak punya channel config/LED**; command `fb/fd/fe/fa` hanya jalan
saat device dicolok via kabel (atau BLE). Input gestur penuh tetap ada di
dongle: 2 node evdev — `… Keyboard` (keycode 113-115/224-225) dan `… Mouse`.

Akses interface vendor **eksklusif**: hanya satu program boleh memegangnya
(tutup official app / anticater-web saat daemon jalan). Mode LED persist di
memori device; kalau device "hang" tak mau menerima perintah → cabut-colok.

## Gestur (default pabrik, event evdev)

| Gestur | Consumer usage | evdev key |
|---|---|---|
| Rotasi kiri | VolumeDecrement (0xEA) | `KEY_VOLUMEDOWN` (114) |
| Rotasi kanan | VolumeIncrement (0xE9) | `KEY_VOLUMEUP` (115) |
| Tekan | Mute (0xE2) | `KEY_MUTE` (113) |
| Tekan + rotasi kiri | BrightnessDown (0x70) | `KEY_BRIGHTNESSDOWN` (224) |
| Tekan + rotasi kanan | BrightnessUp (0x6F) | `KEY_BRIGHTNESSUP` (225) |

Catatan: di mode BLE, kode ini tiba sebagai event HID over GATT — evdev tetap
bekerja (dibuktikan ha-ble-knob). Lag: device tidur agresif, ~1 detik untuk
koneksi ulang; klik pertama bisa tertelan — perilaku hardware.

## Format pesan (varian vk01/WebHID — default probe)

Semua pesan: report id `0x03` + payload 64 byte. Di sisi hidraw, tulis/baca
termasuk byte report id (65 byte di bus).

```text
init           : fb fb fb            (kirim dulu; tanpa ini device tak merespons)
                -> respons 64 byte (session info; hexdump penuh di probe)
save/finish    : fd fe ff            (setelah menulis binding)
bind           : fd <slot> <layer> <type> [flag-mouse] <len> 00 00 <code-low> 00 00 00 <code-high>
                slot: 2=rot kiri, 3=tekan, 4=rot kanan, 5=rot kiri+sambil tekan, 6=rot kanan+sambil tekan
                type: 1=keyboard, 2=media, 3=mouse (tidak berfungsi pada unit VK-01 rotary)
                layer: 1..3 (device punya 3 layer; firmware VK-01 umumnya hanya pakai 1)
LED write       : 3x paket: fe b0 <idx 0..2> <mode jika idx=0, else 00> + 18 byte (6 warna RGB)
                mode: 0=off 1=merah 2=hijau 3=custom 6 LED 4=pelangi CW 5=pelangi CCW
                (tanpa finish — sesuai anticater-web)
LED query       : fa b0 00  ->  fa <mode> <6x RGB>
Layer query     : fa 19 00 <layer>  ->  25 paket, satu per slot key
```

## Format pesan (varian knobctl/CH57x — firmware lebih baru)

Device dengan VID/PID sama tapi firmware CH57x memakai layout lain
(knobctl menangkapnya dari app vendor; mendukung binding mouse):

```text
init        : fb fb fb 01        -> fb 00 01
rgb mode    : fe b0 <layer+1> 08 00 00 00 00 00 01 00 <0x50+mode>   + finish fd fe ff
bind        : fd <key_id> <layer+1> <kind> ...  + finish
key_id knob : ccw=16, press=17, cw=18; slot 19-24 = tekan+rotasi
read query  : fa 0f 03 <layer+1>
```

Perbedaan kunci: number layer berbeda (`layer+1`), RGB write-only
(tidak bisa dibaca ulang), mouse binding jalan. `--variant knobctl`
di probe memakai format ini.

## Baterai — STATUS INVESTIGASI

- **BLE**: hampir pasti ada via GATT Battery Service (0x180F) → `upower -d`.
- **Dongle USB**: **tidak mungkin** — input-only, tanpa channel HID tambahan.
- **Kabel USB**: belum ada saluran yang terdokumentasi. Tidak ada usage page
  baterai di report descriptor; tidak ada opcode baterai di protokol publik.
  Kemungkinan: report berkala tersembunyi (cari lewat `--sniff` saat dicolok
  kabel) atau memang tidak ada di jalur USB.
- Fallback desain jika tidak ketemu: LED ring sebagai notifikasi state app /
  koneksi (bukan persen baterai).

## Referensi

- https://github.com/x0rloser/anticater_vk01 — Python RE, query & LED
- https://github.com/zackwag/anticater-web — WebHID configurator (Linux/Chromium), protokol lengkap di `protocol.js`
- https://github.com/ciiiii/knobctl — varian CH57x, `docs/protocol.md`
- https://github.com/zerni/ha-ble-knob — input BLE di Linux (evdev), keycode default
- https://github.com/frostvalley-hussey/vk01-anticater — daemon macOS, konsep layer per-app
- https://github.com/dreamug/anticater-knob-control — proyek asli (macOS) yang di-port