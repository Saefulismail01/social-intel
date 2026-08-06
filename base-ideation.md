**Sistem “Social Intelligence” milik Lana (@lanaaielsa)** intinya adalah sistem AI Agent yang memantau **opini publik (sentiment) di Binance Square** secara real-time, lalu digabungkan dengan data pasar Binance untuk mendeteksi sinyal Market Maker (MM) yang sedang “membangun sarang” (pump).

Lana sendiri pernah menjelaskan logikanya secara terbuka (meski detail teknis penuh tidak dibuka karena masih menghasilkan uang). Berikut ringkasan cara kerjanya berdasarkan penjelasan dia dan yang direplikasi orang lain:

### 1. Ide Dasar (Cognitive Edge)
- MM butuh retail investor (“ikan”) untuk pump harga.
- Banyak retail (terutama yang berbahasa Mandarin) menggunakan **Binance Square** sebagai sumber info utama.
- Jadi anomali volume postingan / sebutan ticker di Square sering **mendahului** pergerakan harga.
- Mirip konsep “ikut aliran dana on-chain”, tapi versi “ikut aliran opini publik”.

### 2. Data yang Dipantau
- **Volume postingan & sebutan $TICKER** di Binance Square (berapa sering suatu coin disebut).
- Struktur kerumunan (crowd): membedakan manusia vs bot (metode kasar Lana: akun yang pernah ganti nama = kemungkinan manusia; akun default + mass posting = bot).
- Anomali di **daftar gainers** (coin yang sedang naik tajam).
- Perubahan **Open Interest (OI)** 48 jam terakhir di kontrak futures Binance — cari yang OI naik besar tapi harga belum bereaksi signifikan.
- Preferensi token: yang baru listing dalam 6 bulan terakhir, atau yang punya sejarah volatilitas tinggi.
- Data tambahan: berita real-time, serta “distilled” gaya posting Twitter + riwayat wallet on-chain milik Lana sendiri (meski dia bilang efek distillasi ini masih diragukan).

### 3. Cara Kerja AI Agent
Lana memakai Claude (dan script) untuk:
- Scraping / monitoring postingan Square yang paling ramai.
- Menghitung token mana yang paling “panas” berdasarkan volume post + struktur akun.
- Mencari kandidat di gainers list + OI divergence.
- Entry dengan gaya **chase high** (berani kejar harga yang sudah naik), sambil pasang stop-loss ketat (awalnya 20%, kemudian diganti menjadi batas kerugian tetap misalnya max loss 200 USDT per posisi).
- Agent juga membantu posting di Square sendiri (dengan gaya yang mirip Lana) agar terlihat “asli” dan menarik lebih banyak perhatian.

### 4. Yang Direplikasi Orang Lain (contoh @crypto_pumpman)
- Pakai Playwright untuk scrape post Square real-time.
- Regex untuk hitung sebutan $TICKER (abaikan stablecoin).
- Gabungkan dengan CoinGecko Trending + ranking futures Binance → hitung skor “heat”.
- Monitor OI vs perubahan harga (contoh: OI +20% tapi harga cuma +3% = sinyal bagus).

### Catatan Penting
Sistem ini **bukan prediksi arah harga** murni, melainkan deteksi dini aktivitas MM lewat sinyal sosial + data orderflow. Lana menekankan pentingnya stop-loss karena chase high berisiko tinggi. Strategi seperti ini juga tidak selamanya valid — pasar bisa berubah.

Singkatnya: Lana mengubah “suara kerumunan di Binance Square” menjadi data terstruktur yang bisa dibaca AI Agent, lalu digabung dengan OI dan gainers list untuk mencari peluang entry.