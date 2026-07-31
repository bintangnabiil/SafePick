# Folder Model InsightFace

Folder ini digunakan untuk menyimpan model AI berbasis ONNX yang dipakai oleh
InsightFace.

Model tidak di-commit ke Git karena ukurannya besar. Saat deploy ke perangkat
baru, pastikan model `buffalo_sc` tersedia di folder ini.

## Model Default

SafePick menggunakan paket model InsightFace `buffalo_sc` karena ukurannya kecil
dan lebih cocok untuk perangkat edge seperti Raspberry Pi.

## Instalasi Manual

1. Download `buffalo_sc.zip` dari:

   <https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip>

2. Extract isi ZIP ke folder `models/` hingga strukturnya menjadi:

```text
SafePick/
`-- models/
    |-- README.md
    `-- buffalo_sc/
        |-- det_500m.onnx
        `-- w600k_mbf.onnx
```

Model `buffalo_l` tidak direkomendasikan untuk Raspberry Pi karena jauh lebih
berat dan dapat memperlambat proses scan.
