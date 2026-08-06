"use client";

import { useMemo, useState } from "react";

const terms = [
  ["Active Universe", "Daftar token yang sedang dipantau berdasarkan pilihan dan phase dari Lana-Migration."],
  ["Attention", "Seberapa besar perhatian Square terhadap token: jumlah post, kecepatan kenaikan mention, dan engagement."],
  ["Breadth", "Seberapa luas crowd yang ikut membicarakan token. Banyak author unik berarti breadth lebih luas."],
  ["Authenticity", "Perkiraan kualitas/keorganikan aktivitas akun. Ini bukan bukti identitas manusia."],
  ["Coordination", "Indikasi beberapa akun memposting teks, timing, atau narasi yang mirip/serempak."],
  ["Data Confidence", "Seberapa lengkap dan segar data yang tersedia. Confidence rendah berarti jangan menarik kesimpulan kuat."],
  ["Freshness", "Umur data Square terakhir yang diterima desk. LIVE bukan berarti harga atau crowd pasti benar."],
  ["Crowd State", "Tahap pembentukan crowd: Dormant, Seeding, Emerging, atau Broadening."],
  ["DORMANT", "Aktivitas sosial dekat baseline dan belum menunjukkan anomali berarti."],
  ["SEEDING", "Narasi mulai ditanam oleh sedikit akun; perhatian bisa naik tetapi crowd belum meluas."],
  ["EMERGING", "Perhatian mulai bertambah dan mulai mendapat respons dari author di luar seed awal."],
  ["BROADENING", "Partisipasi menyebar ke banyak author atau cluster yang lebih independen."],
  ["NO_DATA", "Belum ada observasi Square yang cocok untuk token ini."],
  ["INSUFFICIENT_DATA", "Ada observasi, tetapi jumlahnya belum cukup untuk menyimpulkan crowd state dengan kuat."],
  ["STALE", "Data terlalu lama; collector belum menerima feed baru dalam batas waktu yang ditentukan."],
  ["Lana Phase", "Phase market dari Lana-Migration: misalnya Ignition, Squeeze, Exhaustion, atau Dump."],
  ["IGNITION", "Market mulai bergerak/menyala menurut engine Lana. Ini bukan jaminan arah harga."],
  ["SQUEEZE", "Phase tekanan/pergerakan kuat setelah ignition menurut engine Lana."],
  ["EXHAUSTION", "Momentum market mulai kehilangan tenaga atau menunjukkan tanda kelelahan."],
  ["DUMP", "Market berada dalam phase penurunan setelah puncak/pergerakan sebelumnya."],
  ["P0 / P1", "Priority pemantauan. P0 adalah prioritas tertinggi; P1 biasanya repeat offender atau watchlist penting."],
  ["Mention", "Satu post yang menyebut ticker/token yang ada dalam universe Lana."],
  ["Unique Author", "Jumlah akun berbeda yang membuat post tentang token dalam suatu periode."],
  ["Author Concentration", "Porsi post yang dibuat oleh sedikit author teratas. Tinggi berarti crowd lebih sempit."],
  ["Duplicate Ratio", "Proporsi post yang sama atau hampir sama. Tinggi dapat mengindikasikan spam atau koordinasi."],
  ["Ingestion", "Proses menerima, memvalidasi, membersihkan, dan menyimpan data Square."],
  ["Collector", "Service pasif yang mengamati response feed/search dari Chrome yang Anda buka sendiri."],
  ["Source Health", "Status koneksi dan kesegaran data dari collector Square."],
  ["Event Time", "Waktu post dibuat di Square. Berbeda dari ingestion time, yaitu waktu desk menerima post."],
  ["Provenance", "Jejak asal data: source, post ID, timestamp, dan versi score yang digunakan."],
  ["Score Version", "Versi formula yang menghitung skor. Berguna agar hasil historis dapat direproduksi."],
];

export function Glossary({ onBack }: { onBack: () => void }) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => terms.filter(([term, definition]) => `${term} ${definition}`.toLowerCase().includes(query.toLowerCase())), [query]);
  return <main className="glossaryPage"><div className="appShell"><header className="topbar"><div className="brand"><span className="mark">SI</span><div><b>SOCIAL INTELLIGENCE</b><small>DESK GLOSSARY</small></div></div><button className="backButton" onClick={onBack}>← BACK TO RADAR</button></header><section className="glossaryContent"><div className="glossaryIntro"><div><label>OPERATOR REFERENCE</label><h1>DESK GLOSSARY</h1><p>Penjelasan istilah yang muncul di Crowd Radar. Gunakan halaman ini untuk membaca arti data sebelum mengambil kesimpulan.</p></div><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search terms…" aria-label="Search glossary terms" /></div><div className="glossaryGrid">{filtered.map(([term, definition]) => <article key={term}><h2>{term}</h2><p>{definition}</p></article>)}</div></section></div></main>;
}
