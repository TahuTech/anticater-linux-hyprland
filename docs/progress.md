# Progress — anticater-linux-hyprland

Status terakhir: 2026-11-27. Snapshot agar sesi berikutnya lanjut tanpa kehilangan konteks.

> **Uji 0.57.2-2.1 selesai (dari kantor, tanpa knob)** — wheel sintetis masih
> JATUH (same as 0.57.2-2). Workaround PageUp/PageDown dipertahankan; profil
> Spotify tidak diubah. Detail: lihat "Probe uji wheel" + Pending #2.

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

## Environment (penting untuk sesi berikut)

- Repo: `~/Documents/Code/anticater-linux-hyprland` (`TahuTech/anticater-linux-hyprland`, main). Head: lihat `git log`.
- CachyOS + Hyprland **0.57.2-2.1** (update sudah terpasang; build `11cbc6c`, 9 Nov 2026).
- Config live: `config/app-mapping.json` — daemon log `/tmp/anticaterd.log`; hot-reload config otomatis.
- `KEY_BY_NAME` di daemon **terbalik** (nama→kode, bukan sebaliknya) — jangan "diperbaiki".
- Commit: `git -c user.name="tahutech" -c user.email="tahutech@users.noreply.github.com" commit -m …` lalu `git push`. Sudo dilarang (password user).
- `gh` terautentikasi (TahuTech) — untuk baca/posting GitHub.
- Konfig Hyprland = Lua (`hyprland.lua`, ML4W), titik custom = `custom.lua`.

## Pending / Next Steps

1. **Post ulang discussion Hyprland** (manual oleh user, UI web, kata-kata sendiri):
   `https://github.com/hyprwm/Hyprland/discussions/new?category=Bugs%20-%20Input`
   — template `bugs-input.yml` melarang laporan AI + 3 checkbox wajib. Draft material:
   `docs/hyprland-repro-0.57-synthetic-input.md`. Draf lama #17114 ditutup bot (strike 1/3), hapus lewat API dilarang.
2. ~~**Uji `hyprland 0.57.2-2.1`**~~ **SELESAI** (2026-11-27, tanpa knob): wheel **JATUH**
   → workaround PageUp/PageDown dipertahankan, profil Spotify tetap.
3. **Submit AUR**: repo `anticater-linux-hyprland-git` **belum ada** di AUR (ls-remote:
   host key rejection — belum pernah dibuat/di-push). User perlu: buat paket via
   https://aur.archlinux.org/packages (login TahuTech) → tambah SSH key `~/.ssh/id_ed25519.pub`
   ke akun → baru `makepkg --printsrcinfo > .SRCINFO` + push (semua tanpa knob, bisa dari kantor).
   Setelah terpasang: ganti path di `custom.lua` ke `/usr/bin/anticaterd`.
4. **BLE battery**: `upower -d` (dongle input-only; kabel nol paket — belum ditest).
5. **Catatan saat lanjut**: sempat terlihat 2 perangkat uinput `anticaterd`/`anticaterd-2` (dua
   instance?) dan service `o2ctl` `anticaterd` exited (crash cabut-knob, sudah difix). Instance
   live berjalan dengan kode lama — restart sekali dengan kode fix bila knob dipakai terus.
   Saat ini daemon **tidak jalan** (`pgrep` kosong) — tidak ada knob, tidak perlu.
6. Cleanup `/tmp`: sudah bersih (dicek 2026-11-27, tidak ada `/tmp/mkpkg*`/`/tmp/repro_body.md`).

## Referensi bug Hyprland

- [hyprwm/Hyprland #15423](https://github.com/hyprwm/Hyprland/discussions/15423) — button state nyangkut (keluarga sama, belum ada rilis fix).
- [hyprwm/Hyprland #15320](https://github.com/hyprwm/Hyprland/discussions/15320) — motion virtual device (difix #15003).
- Fakta lokal (uji ulang 2026-11-27, 0.57.2): **wheel sintetis (uinput REL_WHEEL) tidak sampai** klien XWayland (3× konsisten); motion REL_X & key sampai; **klik BTN_LEFT sampai** di listener tanpa mask (diagnosa lama "button dibuang" kemungkinan false negative — listener GTK bermask).
- **Jebakan GTK di 0.57.2**: `Gtk.Widget.add_events` (mask) membuat window berhenti menerima SEMUA event (enter pun mati; teruji). Listener harus tanpa mask + nama event Gdk pakai underscore (`ENTER_NOTIFY`, `KEY_PRESS`).
- `hyprctl dispatch hl.dsp.cursor.move({…})` mengembalikan 'ok' dan memang memindahkan kursor (cek `hyprctl cursorpos`); `hl.dsp.focus` memindahkan fokus + aktif.