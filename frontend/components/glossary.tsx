"use client";

import katex from "katex";
import "katex/dist/katex.min.css";
import { useMemo, useState } from "react";

type DefinitionEntry = {
  term: string;
  definition: string;
};

type FormulaEntry = {
  term: string;
  summary: string;
  formulas: string[];
  /** Plain-language walkthrough of symbols / steps. */
  explain: string;
  /** Worked numeric example (e.g. HFT). */
  example: string;
};

const definitions: DefinitionEntry[] = [
  { term: "Active Universe", definition: "Daftar token yang sedang dipantau berdasarkan pilihan dan phase dari Lana-Migration." },
  { term: "Attention", definition: "Seberapa besar perhatian Square terhadap token: jumlah post, kecepatan kenaikan mention, dan engagement. Skor 0–100 dari window 60 menit (bukan X Radar)." },
  { term: "Breadth", definition: "Seberapa luas crowd yang ikut membicarakan token. Banyak author unik berarti breadth lebih luas. Skor 0–100 dari post Square 60 menit." },
  { term: "Authenticity", definition: "Perkiraan kualitas/keorganikan aktivitas akun. Ini bukan bukti identitas manusia. Skor 0–100 dari umur akun, duplikasi, dan konsentrasi author." },
  { term: "Coordination", definition: "Indikasi beberapa akun memposting teks, timing, atau narasi yang mirip/serempak. Skor 0–100; tinggi sering berarti spam/seed sempit." },
  { term: "Data Confidence", definition: "Seberapa lengkap dan segar data yang tersedia. Confidence rendah berarti jangan menarik kesimpulan kuat." },
  { term: "Freshness", definition: "Umur data Square terakhir yang diterima desk. LIVE bukan berarti harga atau crowd pasti benar." },
  { term: "Crowd State", definition: "Tahap pembentukan crowd Square: Dormant, Seeding, Emerging, atau Broadening (diturunkan dari skor Attention/Breadth/Coordination)." },
  { term: "DORMANT", definition: "Aktivitas sosial dekat baseline dan belum menunjukkan anomali berarti (Attention < 20)." },
  { term: "SEEDING", definition: "Narasi mulai ditanam oleh sedikit akun; perhatian bisa naik tetapi crowd belum meluas (Coordination ≥ 58 atau Breadth < 35)." },
  { term: "EMERGING", definition: "Perhatian mulai bertambah dan mulai mendapat respons dari author di luar seed awal (Breadth < 65 atau unique authors < 12)." },
  { term: "BROADENING", definition: "Partisipasi menyebar ke banyak author atau cluster yang lebih independen (Breadth tinggi dan unique authors ≥ 12)." },
  { term: "NO_DATA", definition: "Belum ada observasi Square yang cocok untuk token ini, atau belum ada ingestion run." },
  { term: "INSUFFICIENT_DATA", definition: "Ada observasi, tetapi mentions < 3 dalam window 60 menit — belum cukup untuk menyimpulkan crowd state." },
  { term: "STALE", definition: "Data terlalu lama; collector belum menerima feed baru dalam batas waktu yang ditentukan (source age > 15 menit)." },
  { term: "Lana Phase", definition: "Phase market dari Lana-Migration: misalnya Ignition, Squeeze, Exhaustion, atau Dump." },
  { term: "IGNITION", definition: "Market mulai bergerak/menyala menurut engine Lana. Ini bukan jaminan arah harga." },
  { term: "SQUEEZE", definition: "Phase tekanan/pergerakan kuat setelah ignition menurut engine Lana." },
  { term: "EXHAUSTION", definition: "Momentum market mulai kehilangan tenaga atau menunjukkan tanda kelelahan." },
  { term: "DUMP", definition: "Market berada dalam phase penurunan setelah puncak/pergerakan sebelumnya." },
  { term: "P0 / P1", definition: "Priority pemantauan. P0 adalah prioritas tertinggi; P1 biasanya repeat offender atau watchlist penting." },
  { term: "Mention", definition: "Satu post Square yang menyebut ticker/token yang ada dalam universe Lana (window skor: 60 menit)." },
  { term: "Unique Author", definition: "Jumlah akun berbeda yang membuat post tentang token dalam suatu periode." },
  { term: "Author Concentration", definition: "Porsi post yang dibuat oleh sedikit author teratas (top-5 share). Tinggi berarti crowd lebih sempit." },
  { term: "Duplicate Ratio", definition: "Proporsi post yang sama atau hampir sama (normalized text). Tinggi dapat mengindikasikan spam atau koordinasi." },
  { term: "Ingestion", definition: "Proses menerima, memvalidasi, membersihkan, dan menyimpan data Square." },
  { term: "Collector", definition: "Service pasif yang mengamati response feed/search dari Chrome yang Anda buka sendiri." },
  { term: "Source Health", definition: "Status koneksi dan kesegaran data dari collector Square." },
  { term: "Event Time", definition: "Waktu post dibuat di Square. Berbeda dari ingestion time, yaitu waktu desk menerima post." },
  { term: "Provenance", definition: "Jejak asal data: source, post ID, timestamp, dan versi score yang digunakan." },
  { term: "Score Version", definition: "Versi formula yang menghitung skor (saat ini crowd-v0.1.0)." },
  { term: "X Narrative", definition: "Ringkasan volume percakapan di X untuk token. Saat sumbernya X Radar, angkanya official daily counts, bukan sample harvest." },
  { term: "X Radar", definition: "Fitur Premium+ di x.com/i/radar. Desk memakainya sebagai oracle volume: jumlah post per hari untuk query cashtag $SYMBOL." },
  { term: "Official Count", definition: "Jumlah post harian yang dilaporkan X Radar sendiri. Ini sumber otoritatif volume; beda dari harvested sample yang hanya subset post." },
  { term: "Harvested Sample", definition: "Sample post yang dipanen lewat search/collector. Berguna sebagai evidence, tetapi biasanya jauh lebih kecil dari official count." },
  { term: "7D Median/Day", definition: "Median jumlah post harian pada hari-hari yang sudah selesai (bukan hari berjalan). Dipakai sebagai baseline untuk membandingkan hari ini." },
  { term: "VS Median / Acceleration (X)", definition: "Rasio post hari ini ÷ median 7 hari (X Radar). ≥2× SURGING, ≥1.3× ELEVATED, ≤0.5× QUIET, selain itu STEADY." },
  { term: "SURGING", definition: "Volume X hari ini ≥ 2× median harian 7 hari. Percakapan cashtag sedang lonjak tajam dibanding minggu ini." },
  { term: "ELEVATED", definition: "Volume X hari ini ≥ 1.3× median 7 hari, tetapi di bawah ambang surging. Lebih ramai dari biasanya, belum ekstrem." },
  { term: "STEADY", definition: "Volume X hari ini di kisaran normal vs median 7 hari (di atas 0.5× dan di bawah 1.3×), atau belum ada rasio yang bisa dihitung." },
  { term: "QUIET", definition: "Volume X hari ini ≤ 0.5× median 7 hari. Cashtag relatif sepi dibanding baseline mingguan." },
  { term: "NOT_SCANNED", definition: "Belum ada harvest/baseline X untuk token ini di jendela waktu yang dipantau; bukan berarti sepi di X." },
  { term: "Capture Ratio", definition: "Harvested posts ÷ official X Radar posts. Rendah berarti sample evidence hanya cuplikan kecil dari volume resmi." },
  { term: "Daily Granularity", definition: "X Radar (langganan ini) hanya memberi hitungan per hari, bukan per jam. Tidak ada unique authors atau impressions dari Radar." },
  { term: "Active (Kanban)", definition: "Token sedang ada di kanban Lana (IGNITION/SQUEEZE/EXHAUSTION/DUMP) dan tampil di radar live." },
  { term: "Archived", definition: "Token sudah keluar kanban Lana. Baseline X, post, dan history disimpan, tetapi tidak lagi ditampilkan di radar live." },
];

const formulas: FormulaEntry[] = [
  {
    term: "ATTENTION",
    summary: "Skor perhatian Square 0–100 dari post 60 menit terakhir (bukan X). Menggabungkan volume mention, akselerasi vs baseline 3 jam sebelumnya, dan engagement rata-rata.",
    formulas: [
      String.raw`m = \#\{\text{posts in last } 60\text{ min}\}`,
      String.raw`\bar e = \dfrac{1}{m}\sum_{i=1}^{m}\bigl(\mathrm{likes}_i+\mathrm{comments}_i+\mathrm{shares}_i+\ln(1+\mathrm{views}_i)\bigr)`,
      String.raw`r_{\mathrm{base}} = \dfrac{\#\{\text{posts in prior } 3\text{ h}\}}{3}`,
      String.raw`a = \dfrac{m}{\max(1,\, r_{\mathrm{base}})}`,
      String.raw`\mathrm{Attention} = \mathrm{clamp}_{0}^{100}\!\Bigl(3m + 9\min(a,5) + 4\ln(1+\bar e)\Bigr)`,
    ],
    explain:
      "m = jumlah mention Square. a = akselerasi (m dibagi rate baseline 3 jam sebelumnya). ē = rata-rata likes+comments+shares+ln(1+views). Kontribusi UI: volume = 3m (cap 45), acceleration = 9·min(a,5), engagement = 4·ln(1+ē).",
    example:
      "Contoh HFT: m=4, a=0.71, ē=4.6 → volume 12.0 + acceleration 6.4 + engagement ≈6.9 = Attention ≈ 25.",
  },
  {
    term: "BREADTH",
    summary: "Skor sebaran crowd Square 0–100. Naik jika banyak author unik dan post tidak terpusat di top-5 author.",
    formulas: [
      String.raw`U = \#\{\text{unique authors}\}`,
      String.raw`s_5 = \dfrac{\sum_{\text{top 5 authors}} \mathrm{count}}{m}`,
      String.raw`\mathrm{Breadth} = \mathrm{clamp}_{0}^{100}\!\Bigl(4U + 35(1-s_5)\Bigr)`,
    ],
    explain:
      "U = author unik. s₅ = porsi post dari 5 author teratas (0–1). Komponen (1−s₅) mengukur seberapa “tidak terkonsentrasi” crowd.",
    example:
      "Contoh HFT: U=3, s₅=1.0 → 4·3 + 35·0 = Breadth = 12 (semua post dari ≤5 author).",
  },
  {
    term: "COORDINATION",
    summary: "Skor indikasi koordinasi/spam Square 0–100. Tinggi jika banyak teks duplikat dan author terpusat.",
    formulas: [
      String.raw`d = \dfrac{\sum_{t}\max(0,\, c_t - 1)}{m}\quad (c_t=\text{count of normalized text } t)`,
      String.raw`\mathrm{Coordination} = \mathrm{clamp}_{0}^{100}\!\Bigl(75d + 25 s_5\Bigr)`,
    ],
    explain:
      "d = duplicate ratio (normalized text yang berulang). Bobot duplikat (75) lebih besar daripada konsentrasi author (25).",
    example:
      "Contoh HFT: d=0.75, s₅=1.0 → 75·0.75 + 25·1 = 56.25 + 25 = Coordination ≈ 81.",
  },
  {
    term: "AUTHENTICITY",
    summary: "Skor keorganikan Square 0–100 (bukan verifikasi manusia). Lebih tinggi jika akun lebih “matang”, sedikit duplikat, dan author tersebar.",
    formulas: [
      String.raw`\rho = \dfrac{\#\{\mathrm{age}\ge 30\}}{\#\{\text{known ages}\}}\quad(\text{default }0.5\text{ if none known})`,
      String.raw`\mathrm{Authenticity} = \mathrm{clamp}_{0}^{100}\!\Bigl(55\rho + 30(1-d) + 15(1-s_5)\Bigr)`,
    ],
    explain:
      "ρ = porsi akun dengan umur ≥ 30 hari. Jika umur akun tidak tersedia, ρ default 0.5. d dan s₅ sama seperti di Coordination/Breadth.",
    example:
      "Contoh HFT: ρ default 0.5, d=0.75, s₅=1 → 55·0.5 + 30·0.25 + 15·0 = 27.5 + 7.5 + 0 = Authenticity = 35.",
  },
  {
    term: "clamp (skor 0–100)",
    summary: "Semua skor Square di-clamp ke [0, 100] lalu dibulatkan 1 desimal.",
    formulas: [
      String.raw`\mathrm{clamp}_{0}^{100}(x) = \mathrm{round}\!\bigl(\max(0,\min(100,x)),\, 1\bigr)`,
    ],
    explain: "Nilai di bawah 0 jadi 0; di atas 100 jadi 100; lalu round ke 1 digit desimal.",
    example: "clamp(81.25) = 81.3; clamp(−2) = 0; clamp(140) = 100.",
  },
  {
    term: "Crowd State (gate)",
    summary: "Urutan keputusan state Square setelah cek NO_DATA / STALE. Hanya satu cabang yang menang (dari atas ke bawah).",
    formulas: [
      String.raw`m < 3 \;\Rightarrow\; \texttt{INSUFFICIENT\_DATA}`,
      String.raw`\mathrm{Attention} < 20 \;\Rightarrow\; \texttt{DORMANT}`,
      String.raw`\mathrm{Coordination}\ge 58 \;\lor\; \mathrm{Breadth}< 35 \;\Rightarrow\; \texttt{SEEDING}`,
      String.raw`\mathrm{Breadth}< 65 \;\lor\; U < 12 \;\Rightarrow\; \texttt{EMERGING}`,
      String.raw`\text{otherwise}\;\Rightarrow\; \texttt{BROADENING}`,
    ],
    explain:
      "SEEDING menang jika koordinasi tinggi atau breadth sempit. EMERGING jika sebaran masih sedang. BROADENING butuh breadth tinggi dan ≥12 author unik.",
    example:
      "Contoh HFT: Attention≈25, Breadth=12, Coordination≈81 → Coordination≥58 ⇒ state = SEEDING.",
  },
  {
    term: "X Acceleration (VS Median)",
    summary: "State volume X Radar harian. Beda total dari acceleration Square di formula Attention.",
    formulas: [
      String.raw`\mathrm{median}_{7\mathrm{d}} = \mathrm{median}(p_{-6},\ldots,p_{-1})`,
      String.raw`\alpha_X = \dfrac{p_{\mathrm{today}}}{\mathrm{median}_{7\mathrm{d}}}`,
      String.raw`\alpha_X \ge 2 \Rightarrow \texttt{SURGING}`,
      String.raw`\alpha_X \ge 1.3 \Rightarrow \texttt{ELEVATED}`,
      String.raw`\alpha_X \le 0.5 \Rightarrow \texttt{QUIET}`,
      String.raw`\text{else}\;\Rightarrow\; \texttt{STEADY}`,
    ],
    explain:
      "p_today = official count hari ini. Median dihitung dari 6 hari selesai sebelumnya (hari berjalan tidak masuk median).",
    example:
      "Contoh HFT: p_today=237, median₇d=40 → α_X = 237/40 ≈ 5.92 ≥ 2 ⇒ SURGING.",
  },
  {
    term: "Capture Ratio",
    summary: "Berapa banyak official X Radar posts yang berhasil di-sample sebagai evidence harvest.",
    formulas: [
      String.raw`\mathrm{Capture} = \dfrac{\#\{\text{harvested posts}\}}{\#\{\text{official X Radar posts}\}}`,
    ],
    explain:
      "Harvested sample = post yang disimpan collector/search. Official = X Radar. Capture rendah ≠ volume X sepi; hanya sample tipis.",
    example:
      "Official 844, harvested 0 → Capture = 0 (0%). Evidence X kosong meski volume Radar besar.",
  },
];

function renderLatex(source: string): string {
  try {
    return katex.renderToString(source, {
      throwOnError: false,
      displayMode: true,
      strict: "ignore",
      output: "html",
    });
  } catch {
    return source;
  }
}

export function Glossary({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState<"definitions" | "formulas">("definitions");
  const [query, setQuery] = useState("");

  const filteredDefinitions = useMemo(
    () =>
      definitions.filter((entry) =>
        `${entry.term} ${entry.definition}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [query],
  );

  const filteredFormulas = useMemo(
    () =>
      formulas.filter((entry) =>
        `${entry.term} ${entry.summary} ${entry.explain} ${entry.example}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [query],
  );

  return (
    <main className="glossaryPage">
      <div className="appShell">
        <header className="topbar">
          <div className="brand">
            <span className="mark">SI</span>
            <div>
              <b>SOCIAL INTELLIGENCE</b>
              <small>DESK GLOSSARY</small>
            </div>
          </div>
          <button className="backButton" onClick={onBack}>
            ← BACK TO RADAR
          </button>
        </header>
        <section className="glossaryContent">
          <div className="glossaryIntro">
            <div>
              <label>OPERATOR REFERENCE</label>
              <h1>DESK GLOSSARY</h1>
              <p>
                Tab <b>DEFINITIONS</b> untuk arti istilah. Tab <b>FORMULAS</b> untuk persamaan skor
                (Square 60m + X Radar) dengan penjelasan dan contoh numerik.
              </p>
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search terms…"
              aria-label="Search glossary terms"
            />
          </div>

          <div className="glossaryTabs" role="tablist" aria-label="Glossary sections">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "definitions"}
              className={tab === "definitions" ? "active" : undefined}
              onClick={() => setTab("definitions")}
            >
              DEFINITIONS
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "formulas"}
              className={tab === "formulas" ? "active" : undefined}
              onClick={() => setTab("formulas")}
            >
              FORMULAS
            </button>
          </div>

          {tab === "definitions" ? (
            <div className="glossaryGrid" role="tabpanel">
              {filteredDefinitions.map((entry) => (
                <article key={entry.term}>
                  <h2>{entry.term}</h2>
                  <p>{entry.definition}</p>
                </article>
              ))}
              {filteredDefinitions.length === 0 ? (
                <p className="glossaryEmpty">No matching definitions.</p>
              ) : null}
            </div>
          ) : (
            <div className="glossaryFormulaList" role="tabpanel">
              {filteredFormulas.map((entry) => (
                <article key={entry.term} className="glossaryFormulaCard">
                  <h2>{entry.term}</h2>
                  <p className="glossarySummary">{entry.summary}</p>
                  <div className="glossaryFormula">
                    {entry.formulas.map((latex, index) => (
                      <div
                        key={`${entry.term}-${index}`}
                        className="glossaryFormulaLine"
                        dangerouslySetInnerHTML={{ __html: renderLatex(latex) }}
                      />
                    ))}
                  </div>
                  <div className="glossaryExplain">
                    <label>PENJELASAN</label>
                    <p>{entry.explain}</p>
                  </div>
                  <div className="glossaryExample">
                    <label>CONTOH</label>
                    <p>{entry.example}</p>
                  </div>
                </article>
              ))}
              {filteredFormulas.length === 0 ? (
                <p className="glossaryEmpty">No matching formulas.</p>
              ) : null}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
