# Progress — anticater-linux-hyprland

Status terakhir: 2026-11-27. Snapshot agar sesi berikutnya lanjut tanpa kehilangan konteks.

> **Uji 0.57.2-2.1 selesai (dari kantor, tanpa knob)** — wheel sintetis masih
> JATUH (same as 0.57.2-2). Workaround PageUp/PageDown dipertahankan; profil
> Spotify tidak diubah. Detail: lihat "Probe uji wheel" + Pending #2.

> **Sesi rumah 2026-11-27**: tipe aksi `sequence` (kantor) diuji 8 kasus — 2 bug
> diperbaiki (dry-run tidak menurun ke step; langkah non-dict bisa crash
> `AttributeError`). Daemon direstart dengan kode baru (grab knob OK).
> GUI smoke HTTP 200; `makepkg -f` OK. Detail lihat tabel Selesai.

## Selesai ✅

| Item | Detail |
|---|---|
| Fase 1 — pointer & fokus | Aksi scroll/mouse diarahkan ke tengah window fokus (`hl.dsp.cursor.move` absolut + `focus_center`); profil Spotify. `626ce38` |
| Fase 2 — scroll Spotify | **Akar masalah**: Hyprland ≥ 0.55 membuang button & axis dari perangkat sintetis (uinput & XTEST; motion & key jalan). Workaround: profil Spotify → `key` `pageup`/`pagedown` (terverifikasi user). `c021cc1` |
| Packaging | `packaging/PKGBUILD` (AUR `anticater-linux-hyprland-git`), terverifikasi `makepkg -f`. Daemon: config juga dicari di `/etc/anticater/app-mapping.json`. `4a2ebb3` |
| Autostart | `exec-once` ditambahkan ke `~/.config/hypr/custom.lua` (ML4W, di **luar repo**): `hl.on("hyprland.start")` → jalankan daemon. Efektif saat login berikut |
| Repro Hyprland | Draft sesuai template `bugs-input` (post manual, lihat Pending). `d927ebc` |
| Ketahanan cabut/colok | Fix crash saat device hilang (`select` di luar guard → EBADF menembus; sekarang select & read keduanya dijaga, fd ditutup, loop reconnect). Commit ini |
| Probe uji wheel | `probes/probe_synthetic_input.py` — sekali-jalan, tanpa knob (injeksi uinput): veredik wheel SAMPAI/JATUH (exit 0/1). 3× di 0.57.2 + 1× di **0.57.2-2.1** (2026-11-27, dari kantor): wheel JATUH konsisten |
| Tipe aksi `sequence` | Daemon: langkah `key`/`shortcut`/`mouse`/`scroll`/`command` berurutan (e6381b3, kantor). Diuji 8 kasus (2026-11-27): validasi, sequence bersarang ditolak, kunci/tipe tak dikenal, urutan dry-run per-step, eksekusi mock urut, langkah rusak aman. Fix: dry-run menurun ke step + guard langkah non-dict (sebelumnya crash). Commit ini |
| Restart daemon | Instance kode lama (881157) dimatikan; kode baru jalan 2026-11-27: config 3 app OK, uinput siap, grab `/dev/input/event6` (knob) OK |
| Build package | `makepkg -f` OK (`e6381b3-1`); pkgver dari `pkgver()` (git describe); `.SRCINFO` disinkronkan; artefak build masuk `.gitignore` |

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
2. ~~**Uji `hyprland 0.57.2-2.1`**~~ **SELESAI** (2026-11-27, tanpa knob): wheel **JATUH**
   → workaround PageUp/PageDown dipertahankan, profil Spotify tetap.
3. ~~**Submit AUR**~~ **TERTUNDA — tidak ada akun AUR, dan registrasi akun AUR
   sedang ditutup** (dipause Arch; user tidak punya akun). `.SRCINFO` + PKGBUILD
   (`r1.g6a8309b`) sudah siap di `packaging/`. Sampai registrasi dibuka:
   **pasang lokal** `cd packaging && makepkg -si` (tanpa akun; PKGBUILD sudah
   terverifikasi `makepkg -f`). Setelah terpasang: ganti path di `custom.lua`
   ke `/usr/bin/anticaterd`. Submit AUR bisa menyusul saat pendaftaran dibuka
   (perlu: akun + SSH key `~/.ssh/id_ed25519.pub` + push).
4. **BLE battery**: `upower -d` (dongle input-only; kabel nol paket — belum ditest).
5. **Catatan saat lanjut**: daemon kini jalan dengan **kode baru** (restart 2026-11-27,
   pid 973898, grab knob OK) — bukan lagi kode crash-era. Sempat terlihat 2 perangkat
   uinput `anticaterd`/`anticaterd-2` (dua instance?) — pastikan hanya satu instance
   saat cek `pgrep -af anticaterd`.
6. Cleanup `/tmp`: sudah bersih (dicek 2026-11-27, tidak ada `/tmp/mkpkg*`/`/tmp/repro_body.md`).

## Referensi bug Hyprland

- [hyprwm/Hyprland #15423](https://github.com/hyprwm/Hyprland/discussions/15423) — button state nyangkut (keluarga sama, belum ada rilis fix).
- [hyprwm/Hyprland #15320](https://github.com/hyprwm/Hyprland/discussions/15320) — motion virtual device (difix #15003).
- Fakta lokal (uji ulang 2026-11-27, 0.57.2): **wheel sintetis (uinput REL_WHEEL) tidak sampai** klien XWayland (3× konsisten); motion REL_X & key sampai; **klik BTN_LEFT sampai** di listener tanpa mask (diagnosa lama "button dibuang" kemungkinan false negative — listener GTK bermask).
- **Jebakan GTK di 0.57.2**: `Gtk.Widget.add_events` (mask) membuat window berhenti menerima SEMUA event (enter pun mati; teruji). Listener harus tanpa mask + nama event Gdk pakai underscore (`ENTER_NOTIFY`, `KEY_PRESS`).
- `hyprctl dispatch hl.dsp.cursor.move({…})` mengembalikan 'ok' dan memang memindahkan kursor (cek `hyprctl cursorpos`); `hl.dsp.focus` memindahkan fokus + aktif.