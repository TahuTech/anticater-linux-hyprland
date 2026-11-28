# Progress — anticater-linux-hyprland

Status terakhir: 2026-11-28. Snapshot agar sesi berikutnya lanjut tanpa kehilangan konteks.

> **Sesi 2026-11-28 — akar masalah wheel ketemu & diperbaiki.** Bukan "Hyprland
> membuang axis perangkat sintesis": **warp absolut (`hl.dsp.cursor.move`)
> membunuh event axis berikutnya** dari perangkat uinput (terukur, kontrol
> mentah JATUH tepat sesudah warp). Satu motion relatif kecil dari perangkat
> yang sama memulihkannya. Fix: `Actor.nudge()` sebelum aksi, warp dilewati
> bila pointer sudah di jendela fokus, `BTN_LEFT/RIGHT/MIDDLE` didaftarkan di
> uinput (sebelumnya klik dari aksi `mouse` dibuang kernel), dan arah
> `REL_WHEEL` dibetulkan (map lama terbalik). Terverifikasi end-to-end di klien
> GTK; **daemon live sudah di-restart 12:26:47 (pid 1857029, kode baru, grab
> `/dev/input/event5` OK)**; **terverifikasi di Brave dengan config asli** lewat
> knob palsu (`/tmp/uji3/knob_brave.py`): log daemon `rotate_right brave-browser
> -> scroll ['up']` / `rotate_left -> scroll ['down']`, dan konten halaman benar
> bergulir lalu kembali persis (screenshot `grim`). **Putaran knob FISIK
> dikonfirmasi pemilik 2026-11-28 12:31** — scroll di Brave jalan (log daemon live
> `rotate_left/right brave-browser -> scroll ['down'/'up']`). Fix selesai & aktif.

> **Uji 0.57.2-2.1 selesai (dari kantor, tanpa knob)** — wheel sintetis masih
> JATUH (same as 0.57.2-2). Workaround PageUp/PageDown dipertahankan; profil
> Spotify tidak diubah. Detail: lihat "Probe uji wheel" + Pending #2.
> **(Sudah dijelaskan 2026-11-28: penyebabnya penalti setelah warp.)**

> **Sesi rumah 2026-11-27**: tipe aksi `sequence` (kantor) diuji 8 kasus — 2 bug
> diperbaiki (dry-run tidak menurun ke step; langkah non-dict bisa crash
> `AttributeError`). Daemon direstart dengan kode baru (grab knob OK).
> GUI smoke HTTP 200; `makepkg -f` OK. Detail lihat tabel Selesai.

## Selesai ✅

| Item | Detail |
|---|---|
| Fase 1 — pointer & fokus | Aksi scroll/mouse diarahkan ke tengah window fokus (`hl.dsp.cursor.move` absolut + `focus_center`); profil Spotify. `626ce38` |
| Fase 2 — scroll Spotify | Akar masalah **versi lama** (koreksi 2026-11-28: bukan kompositor membuang axis, tapi penalti pasca-warp — lihat baris "Akar masalah wheel"). Workaround saat itu: profil Spotify → `key` `pageup`/`pagedown` (terverifikasi user). `c021cc1` |
| Packaging | `packaging/PKGBUILD` (AUR `anticater-linux-hyprland-git`), terverifikasi `makepkg -f`. Daemon: config juga dicari di `/etc/anticater/app-mapping.json`. `4a2ebb3` |
| Autostart | `exec-once` ditambahkan ke `~/.config/hypr/custom.lua` (ML4W, di **luar repo**): `hl.on("hyprland.start")` → jalankan daemon. Efektif saat login berikut |
| Repro Hyprland | Draft sesuai template `bugs-input` (post manual, lihat Pending). `d927ebc` |
| Ketahanan cabut/colok | **Fix 1 (0c1f4c0) TIDAK cukup** — ditemukan 2026-11-27: `dev.read()` evdev mengembalikan *generator*; fd dibaca lazy saat iterasi, di luar guard. Crash `OSError No such device` tetap terjadi saat knob dicabut. **Fix 2 (commit ini)**: iterasi dimasukkan ke dalam `try/except OSError` → reconnect. Diuji: daemon hidup tanpa device (loop "knob tidak ditemukan") |
| Probe uji wheel | `probes/probe_synthetic_input.py` — sekali-jalan, tanpa knob (injeksi uinput): veredik wheel SAMPAI/JATUH (exit 0/1). 3× di 0.57.2 + 1× di **0.57.2-2.1** (2026-11-27, dari kantor): wheel JATUH konsisten |
| Tipe aksi `sequence` | Daemon: langkah `key`/`shortcut`/`mouse`/`scroll`/`command` berurutan (e6381b3, kantor). Diuji 8 kasus (2026-11-27): validasi, sequence bersarang ditolak, kunci/tipe tak dikenal, urutan dry-run per-step, eksekusi mock urut, langkah rusak aman. Fix: dry-run menurun ke step + guard langkah non-dict (sebelumnya crash). Commit ini |
| Restart daemon | Instance kode lama (881157) dimatikan; kode baru jalan 2026-11-27: config 3 app OK, uinput siap, grab `/dev/input/event6` (knob) OK |
| Build package | `makepkg -f` OK (`e6381b3-1`); pkgver dari `pkgver()` (git describe); `.SRCINFO` disinkronkan; artefak build masuk `.gitignore` |
| **Akar masalah wheel** | **Terukur 2026-11-28** (`/tmp/uji3/t_persist.py`, `t_nop.py`, `probe_input.py`): warp absolut `hl.dsp.cursor.move` membuat event axis (`REL_WHEEL`/`REL_HWHEEL`) yang dikirim **tepat sesudahnya** tidak diterima klien; satu motion relatif dari perangkat uinput yang sama memulihkannya. Warp tanpa perubahan posisi (no-op) dan warp yang benar-benar memindahkan kursor sama-sama mematikan axis berikutnya. Jadi diagnosa lama "Hyprland membuang axis perangkat sintesis" salah sasaran: yang khusus adalah axis pasca-warp |
| **Fix wheel: nudge + skip warp** | `Actor.nudge()` (REL_X `+3`/`-3`, netto ~0 px) dipanggil sebelum setiap aksi mouse/scroll; `with_pointer_on_focus` melewati warp bila pointer sudah di dalam rect jendela fokus, dan memulihkan posisi pointer di akhir agar sisa motion tidak menumpuk. Terverifikasi: (a) jalur tanpa warp lewat daemon penuh → `SCROLL` sampai; (b) jalur warp (pointer di luar jendela, kontrol mentah JATUH) → `SCROLL` + `BUTTON_PRESS` sampai, `cursorpos` kembali ke titik awal |
| **Uinput: tombol mouse** | `Actor.MOUSE_BUTTONS` (BTN_LEFT/RIGHT/MIDDLE) kini ikut didaftarkan di uinput; sebelumnya `EV_KEY` hanya keyset config sehingga tulis `BTN_LEFT` dari aksi `mouse` dibuang kernel. Terverifikasi: `BUTTON_PRESS 1` sampai ke klien |
| **Arah scroll terbalik** | Ukur `/tmp/uji3/t_dir.py`: `REL_WHEEL +1` → GDK UP, `-1` → DOWN (Wayland & XWayland sama); `REL_HWHEEL +1` → LEFT di klien Wayland tetapi RIGHT di XWayland. Map lama (`up`→`-1`, `down`→`+1`) terbalik di sumbu vertikal → dibetulkan. Sumbu mendatar dibiarkan mengikuti konvensi evdev/X11 dan didokumentasikan (tak bisa benar di kedua backend) |
| **Uji Brave, config asli** | `2026-11-28 12:27` `/tmp/uji3/knob_brave.py`: knob palsu (nama/vendor beda → daemon pemilik tak bisa menggrabnya) → daemon kode baru `--config config/app-mapping.json --device <knob palsu>` → Brave fokus. Log: `rotate_right brave-browser -> scroll ['up']` + `rotate_left brave-browser -> scroll ['down']`. Bukti `grim` area jendela: setelah putar kanan pane markdown bergulir (konten lebih awal tampil, ±65k piksel berubah), setelah putar kiri **kembali persis** (beda 0,000 di luar kursor). Pointer diselewengkan ke tengah jendela → gulir masuk ke pane di bawah pointer. Fokus pengguna dipulihkan ke jendela asal (`google-chrome`, ws6). **Lalu dikonfirmasi pemilik dengan knob FISIK (2026-11-28 12:31:08–12:31:10)**: log daemon live `rotate_left brave-browser -> scroll ['down']` + `rotate_right brave-browser -> scroll ['up']` ×3, dan scroll di Brave memang jalan |

## Environment (penting untuk sesi berikut)

- Repo: `~/Documents/Code/anticater-linux-hyprland` (`TahuTech/anticater-linux-hyprland`, main). Head: lihat `git log`.
- CachyOS + Hyprland **0.57.2-2.1** (update sudah terpasang; build `11cbc6c`, 9 Nov 2026).
- Config live: `config/app-mapping.json` — daemon log `/tmp/anticaterd.log`; hot-reload config otomatis.
- `KEY_BY_NAME` di daemon **terbalik** (nama→kode, bukan sebaliknya) — jangan "diperbaiki".
- Commit: `git -c user.name="tahutech" -c user.email="tahutech@users.noreply.github.com" commit -m …` lalu `git push`. Sudo dilarang (password user).
- `gh` terautentikasi (TahuTech) — untuk baca/posting GitHub.
- Konfig Hyprland = Lua (`hyprland.lua`, ML4W), titik custom = `custom.lua`.

## Pending / Next Steps

1. ~~**Post ulang discussion Hyprland**~~ **TERKIRIM** — https://github.com/hyprwm/Hyprland/discussions/17115
   (judul: "Buttons and wheel event from device input never reach (motion and keys works)"),
   open, belum ada komentar bot (cek 2026-11-27). Jika ditutup: tulis ulang lagi, jangan copas
   (template larang AI; draf lama #17114 masih ada, 2 komentar — kemungkinan penutupan bot).
   Materi: `docs/hyprland-repro-0.57-synthetic-input.md` + draf suara manusia
   `docs/hyprland-repro-0.57-human-draft.md` (versi runtime `hyprctl version` 2026-11-27).
2. ~~**Uji `hyprland 0.57.2-2.1`**~~ **SELESAI** (2026-11-27, tanpa knob): wheel
   JATUH saat itu → workaround PageUp/PageDown dipertahankan. **Sudah usang
   2026-11-28**: penyebabnya penalti pasca-warp, sudah diperbaiki & terverifikasi.
   **Keputusan pemilik**: boleh kembalikan profil Spotify ke aksi `scroll`
   (`rotate_left` -> `down`, `rotate_right` -> `up`) lalu cabut workaround
   `pageup`/`pagedown` — belum dilakukan, config live belum disentuh.
3. ~~**Submit AUR**~~ **TERTUNDA — tidak ada akun AUR, dan registrasi akun AUR
   sedang ditutup** (dipause Arch; user tidak punya akun). `.SRCINFO` + PKGBUILD
   (`r1.g6a8309b`) sudah siap di `packaging/`. Sampai registrasi dibuka:
   **pasang lokal** `cd packaging && makepkg -si` (tanpa akun; PKGBUILD sudah
   terverifikasi `makepkg -f`). Setelah terpasang: ganti path di `custom.lua`
   ke `/usr/bin/anticaterd`. Submit AUR bisa menyusul saat pendaftaran dibuka
   (perlu: akun + SSH key `~/.ssh/id_ed25519.pub` + push).
4. **BLE battery**: `upower -d` (dongle input-only; kabel nol paket — belum ditest).
5. **Catatan saat lanjut**: daemon kini jalan dengan **kode fix wheel** (restart 2026-11-28
   12:26:47, pid 1857029, grab `/dev/input/event5`) — bukan lagi kode crash-era.
   Sempat terlihat 2 perangkat uinput `anticaterd`/`anticaterd-2` (dua instance?) —
   pastikan hanya satu instance saat cek `pgrep -af anticaterd`.
6. Cleanup `/tmp`: sudah bersih (dicek 2026-11-27, tidak ada `/tmp/mkpkg*`/`/tmp/repro_body.md`).
7. ~~**Restart daemon agar fix wheel aktif**~~ **SELESAI PENUH 2026-11-28** — restart 12:26:47
   (pid 1857029, kode fix, grab `/dev/input/event5`, config 4 app) + **knob fisik
   dikonfirmasi pemilik 12:31** (`rotate_left/right brave-browser -> scroll
   ['down'/'up']`, scroll Brave jalan). Semantik kode: `up` = gulir naik, `down` =
   gulir turun; kalau arah di config terasa terbalik, cukup tukar `keys` di
   `config/app-mapping.json` (bukan ubah kode).
8. **Arah gulir mendatar**: `left`/`right` mengikuti konvensi evdev/X11
   (`REL_HWHEEL +1` = kanan) sehingga terbalik di klien Wayland native.
   Belum ada profil yang memakai `left`/`right`, jadi belum diputuskan.

## Referensi bug Hyprland

- [hyprwm/Hyprland #15423](https://github.com/hyprwm/Hyprland/discussions/15423) — button state nyangkut (keluarga sama, belum ada rilis fix).
- [hyprwm/Hyprland #15320](https://github.com/hyprwm/Hyprland/discussions/15320) — motion virtual device (difix #15003).
- Fakta lokal (uji ulang 2026-11-27, 0.57.2): **wheel sintetis (uinput REL_WHEEL) tidak sampai** klien XWayland (3× konsisten); motion REL_X & key sampai; **klik BTN_LEFT sampai** di listener tanpa mask (diagnosa lama "button dibuang" kemungkinan false negative — listener GTK bermask).
- **Koreksi 2026-11-28** (lihat baris "Akar masalah wheel"): yang jatuh bukan axis perangkat sintesis secara umum, melainkan **axis pasca-warp absolut**. `probe_synthetic_input.py` mengirim `warp` sebelum injeksi wheel (baris `warp(*center)`), jadi veredik "JATUH konsisten" itu gejala penalti warp — bukan bukti kompositor membuang axis. Tanpa warp, wheel uinput sampai normal.
- **Probe satu-jalan belum bisa dipakai sebagai alat putusan**: pada uji ulang 2026-11-28 (desktop sedang dipakai) window ujinya hanya mencatat `ENTER_NOTIFY` — window uji tidak dijamin topmost/terfokus saat ada jendela fullscreen pengguna yang aktif. Alat verifikasi yang dipakai untuk fix wheel (semuanya memverifikasi judul window aktif + posisi kursor): `/tmp/uji3/t_dir.py` (arah axis vs GDK), `/tmp/uji3/t_warp_daemon.py` (jalur warp: kontrol mentah vs resep daemon), `/tmp/uji3/t_wheel_daemon.py` (pipa daemon penuh dengan knob palsu: config → grab → gestur → uinput → klien).
- **Jebakan GTK di 0.57.2** — klaim lama "`Gtk.Widget.add_events` (mask) mematikan SEMUA event" **tidak terkonfirmasi ulang 2026-11-28**: listener bermask pada window **fullscreen + terfokus** menerima `SCROLL`/`BUTTON_PRESS` di backend Wayland maupun X11 (`/tmp/uji3/t_dir.py`, `t_wheel_daemon.py`, `t_warp_daemon.py`). Yang membuat listener "buta" pada uji ulang adalah window uji tak topmost (jendela fullscreen pengguna sedang fokus), bukan mask. Nama event Gdk tetap pakai underscore (`ENTER_NOTIFY`, `KEY_PRESS`, `BUTTON_PRESS`, `SCROLL`).
- `hyprctl dispatch hl.dsp.cursor.move({…})` mengembalikan 'ok' dan memang memindahkan kursor (cek `hyprctl cursorpos`); `hl.dsp.focus` memindahkan fokus + aktif.