# Folder Runtime Wajah SafePick

File di folder ini dibuat otomatis saat enrollment wajah berjalan. Isinya tidak
di-commit ke git karena termasuk data biometrik dan data operasional sekolah.

## Struktur

```text
face_db/
|-- embeddings.npy    # Embedding wajah orang tua/penjemput
|-- labels.json       # Metadata mapping embedding
|-- .qr_key           # Secret lokal token QR
|-- .embeddings_key   # Secret lokal file embedding
`-- snapshots/        # Snapshot enrollment
```

Metadata siswa, orang tua, token QR, akun admin, dan log utama disimpan di
database MySQL `safepick`.

## Catatan Deploy

Folder ini cukup dibiarkan kosong saat source code diserahkan ke sekolah.
File runtime akan dibuat ulang otomatis setelah admin melakukan enrollment
wajah dan generate token QR melalui UI Admin.
