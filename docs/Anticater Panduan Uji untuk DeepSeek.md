# Anticater: Panduan Uji untuk DeepSeek

Nov 27, 2026 · @Tahutech

## Konteks dan aturan keselamatan

Anticater adalah daemon Linux (`daemon/anticaterd.py`) plus GUI web (`gui/anticater-gui.py`) untuk mengonfigurasi knob fisik dengan profil per aplikasi (global dan per `WM_CLASS`). Config ada di `config/app-mapping.json`; sistem yang dipakai adalah Linux dengan Hyprland.

Aturan yang harus dipatuhi selama menguji:

- Jangan menyentuh, mematikan, atau menyalakan ulang daemon dan GUI milik pemilik yang sedang berjalan.
- Jangan mengubah `config/app-mapping.json` asli. Selalu uji dengan salinan config dan port GUI yang bebas.
- Jangan memperbaiki kode dan jangan commit. Tugas ini hanya menguji dan melaporkan.
- Jangan mengubah format profil.

## Status: yang sudah selesai

Tahap 1 sampai 3 sudah dikerjakan dan lolos uji otomatis di Chrome headless, ditambah uji manual satu tombol numpad di perangkat sungguhan (`KEY_KP5`, code 76) yang lolos.

- Redesain GUI: tab profil, knob besar, panel editor di samping knob, satu warna aksen.
- Perbaikan tombol Simpan (pesan gagal selalu muncul) dan penanganan kutip pada command.
- Pemilih tombol visual: keyboard virtual ANSI penuh dengan numpad dan baris multimedia, sinkron dengan input teks dan perekam.
- Perbaikan daemon: tombol di profil app kini terdaftar ke uinput, `mute` diterima, modifier kanan (`rightctrl`, `rightshift`, `rightalt`, `rightmeta`) bisa dipilih dan direkam.

Belum terbukti atau belum dikerjakan: klik mouse (`BTN_LEFT`, `BTN_RIGHT`, `BTN_MIDDLE`) kemungkinan tidak pernah didaftarkan ke uinput; daemon crash pada config manual tertentu; browser selain Chrome belum diuji; keyboard visual untuk tipe aksi Urutan belum ada.

## Persiapan uji yang aman

1. Pastikan tidak ada daemon kedua: jalankan `pgrep -af anticaterd`. Kalau pemilik masih menjalankan daemon, minta ia menghentikannya dulu.
2. Buat salinan config: `cp config/app-mapping.json ~/uji.json`.
3. Jalankan GUI dengan salinan itu: `python3 gui/anticater-gui.py --config ~/uji.json --open`.
4. Untuk membaca event dari perangkat virtual: `python3 -m evdev.evtest`, lalu pilih perangkat `anticaterd`. Butuh grup `input` atau `sudo`. Perangkat dibuat ulang setiap config berubah, jadi buka `evtest` sesudah menyimpan.
5. Gunakan `kitty` (atau terminal lain, sesuaikan `WM_CLASS`) sebagai jendela fokus. Profil app hanya aktif saat jendelanya fokus.
6. Setelah selesai: Ctrl+C, hapus `~/uji.json`, dan jangan ubah config asli.

## Daftar uji

Semua uji memakai salinan config dari persiapan di atas. Kolom hasil adalah yang diharapkan; catat apa yang sebenarnya terjadi.

| ID | Uji | Langkah | Hasil yang diharapkan |
| --- | --- | --- | --- |
| U1 | Numpad di profil app | Profil `kitty`, Putar kanan = tombol `kp5`, simpan, nyalakan daemon, putar knob ke kanan | `evtest` menampilkan code 76 (`KEY_KP5`), value 1 lalu 0 |
| U2 | Modifier kanan | Satu uji per tombol di profil app: `rightctrl`, `rightshift`, `rightalt`, `rightmeta` | Code 97, 54, 100, 126 berturut-turut, value 1 lalu 0 |
| U3 | Mute | Profil global, Putar kiri = tombol `mute`, simpan, putar knob | Code 113 (`KEY_MUTE`), value 1 lalu 0; simpan tidak ditolak |
| U4 | Perekam | Tab Shortcut, tekan Rekam, tekan 5 di numpad, lalu `\`, lalu Mute | `kp5` (bukan `5`), `\` terekam, Mute terekam dan bisa disimpan |
| U5 | Klik mouse (hanya diagnosis) | Profil app, Tekan = Mouse klik kiri, simpan, tekan knob, amati `evtest` | Perkiraan: tidak ada `BTN_LEFT` (code 272) meski log daemon menulis "klik mouse". Catat hasilnya, jangan memperbaiki |
| U6 | Config manual rusak | Di salinan config, ubah satu slot jadi `"type": "mouse", "keys": []`, jalankan daemon di instance terpisah | Perkiraan: daemon crash saat mulai. Simpan traceback |
| U7 | Config manual rusak 2 | Slot `"type": "key"` dengan `"keys": null`, gerakkan knob | Perkiraan: daemon crash saat gestur dipakai. Simpan traceback |
| U8 | GUI: simpan gagal | Hentikan server GUI lalu klik Simpan; tulis command dengan kutip tidak tertutup | Bar bawah menampilkan "Gagal menyimpan: ..."; kutip tidak tertutup ditolak dengan penjelasan |
| U9 | GUI: kutip command | Command `notify-send 'halo dunia'`, simpan, muat ulang, edit, simpan lagi | Tampil utuh dan tiap argumen satu chip; file hasil simpan tidak memecah argumen |
| U10 | GUI di browser lain | Ulangi U8 dan U9, lalu cek tata letak di Firefox dan Chromium | Fungsi sama; tidak ada elemen yang saling menimpa |
| U11 | Tata letak | Cek di 1920x1080, 1440x900, dan 1100 px lebar | 1920x1080 tanpa scroll; keyboard di samping knob mulai 1440 px, di bawahnya di bawah itu |
| U12 | Pindah fokus | Dengan profil berbeda untuk dua app, pindah fokus antar jendela, putar knob di tiap jendela | Aksi yang terkirim mengikuti profil jendela yang fokus; global dipakai bila tidak ada profil app |

## Cara melaporkan hasil

Untuk setiap ID uji, tulis satu blok pendek:

- Status: LULUS, GAGAL, atau TIDAK BISA DIUJI (beserta alasannya).
- Bukti: keluaran `evtest`, potongan log daemon, atau traceback, secukupnya.
- Perbedaan dari hasil yang diharapkan, bila ada.

Di akhir, beri ringkasan tiga baris: berapa uji lulus, berapa gagal, dan temuan baru yang belum tercantum di dokumen ini. Jangan memperbaiki apa pun, jangan commit, dan jangan menyentuh config atau daemon asli.
