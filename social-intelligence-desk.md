# Social Intelligence Desk

## 1. Arah Produk

Social Intelligence Desk adalah sistem khusus untuk mengamati dan mengukur **pembentukan crowd di sekitar token-token pilihan Lana**.

Sistem ini tidak bertugas menemukan token dari seluruh pasar dan tidak menggantikan market intelligence yang sudah berjalan di `Lana-Migration`.

Pembagian tanggung jawabnya:

```text
Lana-Migration
= menentukan token mana yang secara struktur pasar perlu diawasi

Social Intelligence Desk
= mendeteksi bagaimana crowd terbentuk di sekitar token tersebut
```

Pertanyaan utama sistem bukan:

> Token apa yang akan pump?

Namun:

> Pada token pilihan Lana, apakah crowd sedang terbentuk, siapa yang membentuknya, apakah pertumbuhannya organik atau terkoordinasi, narasi apa yang menyebar, dan crowd sudah berada pada fase mana?

Social Intelligence memberikan **social evidence** kepada Lana. Output-nya bukan kepastian arah harga dan bukan trigger trading yang berdiri sendiri.

---

## 2. Temuan dari Production Lana-Migration

Pemeriksaan read-only terhadap deployment `Lana-Migration` di Contabo menunjukkan bahwa Lana telah mempunyai komponen pemilihan dan pemantauan token, antara lain:

- `crime_watchlist`
- `crime_repeat_offenders`
- `crime_phase_state`
- `crime_phase_events`
- `dsc_screener`
- `dsc_screener_history`
- lifecycle dan phase engine
- OI, order-flow, dan market enrichment
- registry token yang berulang kali mengalami ignition

Contoh repeat offenders yang tersimpan di production:

- MYX
- SIREN
- BULLA
- COAI
- AIA
- RAVE
- TA
- SOON
- PUMPBTC
- FHE
- LAB
- PIPPIN
- PIEVERSE
- GIGGLE
- BLESS
- PROMPT
- JELLYJELLY
- ALPINE
- AIOT

Registry tersebut juga memiliki informasi seperti:

- jumlah historical ignition
- peak multiple
- tier
- species
- median gap antarepisode
- live ignition count
- waktu live ignition terakhir

Beberapa kelompok data dalam `crime_watchlist` terlihat lebih lama daripada registry dan phase engine terbaru. Karena itu, integrasi universe tidak boleh hanya bergantung pada satu tabel. Social Intelligence harus menerima universe gabungan melalui kontrak yang eksplisit dan read-only.

---

## 3. Scope dan Batas Sistem

### 3.1 Yang dibangun dalam Social Intelligence Desk

- Adapter universe dari Lana-Migration
- Binance Square data provider
- Importer fixture, JSON, dan CSV
- Normalisasi posting dan akun
- Resolusi ticker dan alias token
- Near-duplicate detection
- Account behavior profiling
- Coordination/campaign detection
- Social time-series feature engine
- Narrative intelligence
- Crowd lifecycle engine
- Explainable crowd scoring
- Alert engine
- Dashboard operator
- Historical crowd replay dan evaluasi
- API enrichment untuk Lana-Migration

### 3.2 Yang tetap menjadi tanggung jawab Lana-Migration

- Market-wide token discovery
- Repeat-offender registry
- Gainers dan market anomaly screening
- OI dan funding analysis
- Order-flow analysis
- Market lifecycle/phase engine
- Trade-plan generation
- Entry dan exit logic
- Position sizing dan risk management
- Paper maupun live trading

### 3.3 Non-goals

Social Intelligence Desk tidak akan:

- Menempatkan order trading
- Menyimpan API key dengan izin trading
- Mengklaim dapat memastikan aktivitas Market Maker
- Menganggap OI naik selalu bullish
- Menganggap mention tinggi selalu organik
- Membuat konten untuk meniru Lana
- Melakukan impersonasi
- Mengkoordinasikan posting atau engagement
- Memanipulasi perhatian retail
- Melewati CAPTCHA, autentikasi, access control, atau anti-bot platform

---

## 4. Arsitektur Konseptual

```text
┌────────────────────────────┐
│       Lana-Migration       │
│                            │
│ Watchlist                  │
│ Repeat Offenders           │
│ Market Phase               │
│ DSC Screener               │
│ Manual Priority            │
└─────────────┬──────────────┘
              │ Token Universe Contract
              ▼
┌────────────────────────────┐
│     Priority Scheduler     │
│                            │
│ P0 Active                  │
│ P1 Repeat Offender         │
│ P2 Watchlist               │
│ P3 Dormant Baseline        │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│   Binance Square Provider  │
│                            │
│ Live Authorized Adapter    │
│ JSON/CSV Import            │
│ Historical Fixtures        │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│  Social Intelligence Core  │
│                            │
│ Mention Resolution         │
│ Account Profiling          │
│ Deduplication              │
│ Coordination Detection     │
│ Narrative Classification   │
│ Crowd Features             │
│ Lifecycle Classification   │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│        Crowd Desk          │
│                            │
│ Crowd Radar                │
│ Token Crowd Map            │
│ Campaign Detector          │
│ Narrative Tape             │
│ Alerts and Replay          │
└─────────────┬──────────────┘
              │ Social Enrichment API
              ▼
┌────────────────────────────┐
│       Lana-Migration       │
│ Market + Social Evidence   │
└────────────────────────────┘
```

---

## 5. Token Universe Contract

Social Intelligence tidak menentukan universe secara mandiri. Lana-Migration mengirimkan token beserta konteks prioritasnya.

Contoh input:

```json
{
  "symbol": "BULLA",
  "canonical_pair": "BULLAUSDT",
  "source": "repeat_offender",
  "lana_phase": "IGNITION",
  "priority": 95,
  "n_ignitions": 4,
  "repeat_offender_tier": "T2",
  "last_live_ignition": "2026-07-31T02:10:34Z",
  "active": true
}
```

### 5.1 Sumber pembentuk universe

Urutan sumber prioritas:

1. Token dengan market phase aktif
2. Token dengan live ignition
3. Repeat offenders
4. Active crime watchlist
5. Top candidates dari DSC screener
6. Token pilihan manual operator

### 5.2 Aturan sinkronisasi

- Integrasi bersifat read-only dari sisi Social Intelligence.
- Social Intelligence tidak mengubah tabel produksi Lana secara langsung.
- Gunakan API internal atau versioned snapshot sebagai kontrak utama.
- Setiap token membawa `source`, `priority`, dan `effective_at`.
- Token tidak langsung dihapus ketika keluar dari watchlist; token dipindahkan ke baseline monitoring selama retention window.
- Konflik alias harus diselesaikan melalui canonical symbol dan exchange pair.

---

## 6. Priority Scheduler

Karena data/API diusahakan gratis atau minimal, semua token tidak dipantau dengan frekuensi sama.

### P0 — Active Phase

Kriteria:

- Live ignition
- Lana phase berada pada ignition, squeeze, atau status kritis lainnya
- Manual emergency priority

Perilaku:

- Frekuensi pengumpulan tertinggi
- Target interval 15–60 detik jika akses data mengizinkan
- Feature recomputation segera setelah batch masuk
- Alert lifecycle real-time

### P1 — Repeat Offender

Kriteria:

- Terdaftar dalam `crime_repeat_offenders`
- Memiliki beberapa historical ignition

Perilaku:

- Target interval sekitar 2–5 menit
- Tetap membangun baseline meski crowd sedang dormant
- Otomatis naik ke P0 jika terdapat aktivitas Lana atau social anomaly

### P2 — Active Watchlist

Kriteria:

- Token aktif dalam watchlist
- Kandidat top screener tetapi belum memiliki fase kritis

Perilaku:

- Target interval sekitar 5–15 menit
- Naik prioritas jika attention acceleration melewati threshold

### P3 — Dormant Baseline

Kriteria:

- Token historis yang tidak sedang aktif

Perilaku:

- Sampling 30–60 menit
- Menjaga baseline harian dan time-of-day
- Tidak menghasilkan alert kecuali ditemukan anomali kuat

### Dynamic promotion

Token dapat dipromosikan berdasarkan:

- Perubahan phase dari Lana
- Mention velocity
- Unique-author acceleration
- Narrative burst
- Coordination cluster baru
- Manual operator action

Gunakan hysteresis dan cooldown agar token tidak terus berpindah priority akibat noise.

---

## 7. Model Crowd Intelligence

Social Intelligence mengukur enam dimensi utama.

## 7.1 Attention

Mengukur besar dan kecepatan perhatian terhadap token.

Fitur utama:

- Mention count per 5m, 15m, 1h, 4h, dan 24h
- Unique posts
- Unique authors
- Mention velocity
- Mention acceleration
- Engagement velocity
- Share of voice terhadap token lain dalam universe
- Robust anomaly score terhadap baseline token
- Time-of-day adjusted z-score
- Burst intensity dan burst duration

Jumlah mention absolut tidak cukup. Sistem harus membandingkan aktivitas saat ini dengan perilaku normal token itu sendiri.

Contoh:

```text
Token A: 200 mentions/jam, baseline 180 → anomali rendah
Token B: 40 mentions/jam, baseline 3   → anomali tinggi
```

## 7.2 Breadth

Mengukur apakah perhatian berasal dari crowd yang luas atau hanya beberapa akun dominan.

Fitur utama:

- Unique-author count
- New-author growth
- Top-1, top-5, dan top-10 author share
- Author concentration/HHI
- Repeat-author ratio
- Community count
- Cross-community spread
- Reply participation
- Conversation depth
- Independent cluster growth

Contoh interpretasi:

```text
500 posting dari 8 akun
≠
500 posting dari 240 akun
```

Kasus pertama memiliki volume tinggi tetapi breadth rendah. Kasus kedua menunjukkan penyebaran crowd yang lebih luas.

## 7.3 Authenticity

Mengestimasi apakah crowd cenderung organik, otomatis, atau belum dapat ditentukan.

Fitur yang dapat digunakan jika tersedia secara sah:

- Umur akun
- Histori aktivitas
- Posting frequency
- Keragaman ticker yang dibahas
- Template similarity
- Duplicate-content ratio
- Burst behavior
- Reply-to-broadcast ratio
- Engagement abnormality
- Username/default-profile patterns
- Name-change history sebagai weak feature

Pergantian nama akun tidak boleh dianggap sebagai bukti manusia. Fitur tersebut hanya satu sinyal lemah di antara banyak sinyal.

Output harus berbentuk probabilistik:

```json
{
  "organic_likelihood": 0.68,
  "automation_likelihood": 0.21,
  "uncertain": 0.11
}
```

## 7.4 Coordination

Mendeteksi kemungkinan kampanye, orkestrasi, atau amplification network.

Fitur utama:

- Exact duplicate posts
- Near-duplicate text
- Shared URLs
- Shared images/media hashes
- Hashtag sequence similarity
- Ticker sequence similarity
- Posting-time synchrony
- Recurrent co-posting accounts
- Seed-account to amplifier pattern
- Engagement-ring pattern
- Cluster persistence across tokens

Social volume tinggi dapat berasal dari:

1. Retail yang benar-benar mulai masuk
2. Kampanye promosi
3. Bot spam
4. Coordinated narrative seeding
5. Kombinasi seeding terkoordinasi yang kemudian berkembang organik

Sistem harus membedakan kelima kondisi tersebut sejauh bukti memungkinkan.

## 7.5 Narrative

Analisis tidak berhenti pada sentiment positif atau negatif. Sistem harus mengidentifikasi **alasan token dibicarakan**.

Kategori awal:

- Listing speculation
- Partnership
- Product announcement
- Price action
- Whale/MM speculation
- Airdrop
- Meme/viral event
- Influencer call
- Exchange campaign
- Short squeeze
- Fundamental thesis
- Scam/risk warning
- Sell-the-news
- Copy-trading hype
- Unknown/general chatter

Fitur narrative:

- Narrative share
- Narrative velocity
- Narrative concentration
- Narrative diversity
- First-seen time
- Seed accounts
- Amplifier accounts
- Narrative mutation
- Cross-language propagation
- Narrative-to-engagement conversion

Contoh perubahan yang penting:

```text
“listing speculation” meningkat dari 18% menjadi 61% dalam 45 menit
```

## 7.6 Crowd Lifecycle

Setiap token diberi state crowd:

```text
DORMANT
→ SEEDING
→ EMERGING
→ BROADENING
→ EUPHORIA
→ SATURATED
→ DISTRIBUTION
→ DECAY
```

### DORMANT

- Aktivitas berada dekat baseline
- Tidak ada narrative burst bermakna
- Breadth rendah dan stabil

### SEEDING

- Sejumlah kecil akun mulai menanam narasi
- Mention velocity naik tetapi unique-author growth masih rendah
- Coordination score bisa tinggi

### EMERGING

- Mention dan unique authors mulai naik
- Anomali sudah melewati baseline
- Narasi mulai mendapatkan respons di luar seed cluster

### BROADENING

- Pertumbuhan author meluas
- Konsentrasi author turun
- Muncul cluster independen
- Organic participation meningkat

### EUPHORIA

- Mention dan engagement velocity ekstrem
- Crowd expansion masih kuat
- Banyak akun baru bergabung

### SATURATED

- Attention tetap tinggi
- Pertumbuhan unique author mulai melambat
- Narrative diversity menurun
- Repetition meningkat

### DISTRIBUTION

- Promosi tetap tinggi tetapi respons organik melemah
- Seed/amplifier accounts kembali mendominasi
- Engagement quality turun
- Risiko crowd exhaustion meningkat

### DECAY

- Mention, engagement, dan unique-author activity turun
- Narrative kehilangan momentum
- Cluster crowd mulai menghilang

Transisi lifecycle menggunakan hysteresis, minimum duration, dan confidence threshold agar tidak berubah akibat noise sesaat.

---

## 8. Explainable Scoring

Sistem tidak menggunakan satu opaque pump score. Setiap token mempunyai panel skor yang dapat ditelusuri.

| Skor | Fungsi |
|---|---|
| `attention_score` | Besar, velocity, dan acceleration perhatian |
| `breadth_score` | Luas dan independensi partisipasi crowd |
| `authenticity_score` | Kemungkinan aktivitas bersifat organik |
| `coordination_score` | Intensitas orkestrasi atau amplification |
| `narrative_score` | Kekuatan dan penyebaran narasi dominan |
| `conversion_score` | Kemampuan narasi mengubah lurkers menjadi participant |
| `saturation_risk` | Kemungkinan crowd sudah terlalu penuh |
| `data_confidence` | Freshness, coverage, dan kualitas observasi |

Contoh ringkasan:

```text
Token: BULLA
Crowd State: BROADENING
Crowd Strength: 78/100
Organic Confidence: 64/100
Coordination Risk: 31/100
Saturation Risk: 19/100
Data Confidence: 87/100
```

Setiap skor harus menampilkan kontribusi faktor:

```text
Attention Score: 84
+ mention z-score              +24
+ unique-author acceleration   +21
+ engagement velocity          +18
+ share-of-voice growth        +14
- duplicate ratio               -6
- stale sample penalty          -3
```

### Data-confidence gating

Sistem tidak boleh menghasilkan kesimpulan kuat ketika:

- Collector tertinggal
- Coverage token tidak lengkap
- Jumlah observasi terlalu kecil
- Metadata akun tidak tersedia
- Baseline historis belum cukup
- Terjadi collection gap

Missing data harus dibedakan secara eksplisit dari nilai nol.

---

## 9. Social Enrichment untuk Lana

Contoh output per token:

```json
{
  "symbol": "BULLA",
  "observed_at": "2026-08-04T10:00:00Z",
  "crowd_state": "BROADENING",
  "crowd_state_confidence": 0.84,
  "attention_score": 84,
  "breadth_score": 72,
  "authenticity_score": 61,
  "coordination_score": 28,
  "narrative_score": 77,
  "conversion_score": 69,
  "saturation_risk": 19,
  "data_confidence": 91,
  "top_narrative": "short squeeze speculation",
  "evidence": {
    "mentions_15m": 143,
    "unique_authors_15m": 67,
    "mention_acceleration": 3.8,
    "new_author_growth": 2.4,
    "duplicate_ratio": 0.12,
    "top5_author_share": 0.19
  },
  "caveats": [
    "Account age unavailable for 21% of authors"
  ],
  "score_version": "crowd-v1.0.0"
}
```

Lana dapat menggunakan enrichment tersebut bersama market evidence, tetapi Social Intelligence tidak mengeluarkan instruksi entry secara mandiri.

---

## 10. Alert Engine

Alert harus menggambarkan perubahan keadaan, bukan hanya threshold statis.

### Alert utama

- `SEEDING_DETECTED`
- `CROWD_EMERGING`
- `CROWD_BROADENING`
- `EUPHORIA_DETECTED`
- `SATURATION_RISING`
- `DISTRIBUTION_RISK`
- `CROWD_DECAY`
- `COORDINATION_SPIKE`
- `ORGANIC_BREAKOUT_FROM_SEEDING`
- `ATTENTION_WITHOUT_BREADTH`
- `NARRATIVE_SHIFT`
- `DATA_QUALITY_DEGRADED`

### Contoh alert

```text
BULLA — CROWD BROADENING

Attention: 84 (+31 dalam 15m)
Breadth: 72 (+22 dalam 15m)
Unique authors: 67
Top-5 author share: 19%
Coordination risk: 28
Dominant narrative: Short squeeze speculation
Data confidence: 91%

Interpretation:
Perhatian tidak hanya meningkat; partisipasi mulai menyebar ke akun dan
cluster independen. Belum terlihat saturation yang tinggi.
```

### Noise control

- Deduplication
- Cooldown
- Hysteresis
- Minimum state duration
- Severity levels
- Alert acknowledgement
- Alert invalidation
- Per-token suppression
- Priority-aware thresholds

---

## 11. User Interface

## 11.1 Crowd Radar

Tampilan utama seluruh token pilihan Lana:

```text
TOKEN  LANA PHASE  CROWD STATE  ATTENTION  BREADTH  AUTH  COORD  SAT  Δ15M
BULLA  IGNITION    BROADENING    84         72       61    28     19   +31
MYX    WATCH       SEEDING       62         21       38    76     12   +18
RAVE   WATCH       DORMANT       14         12       70     9      5    -2
```

Filter:

- Lana phase
- Crowd state
- Priority
- Repeat-offender tier
- Attention anomaly
- Coordination risk
- Authenticity
- Narrative
- Data confidence

## 11.2 Token Crowd Map

Halaman detail token berisi:

- Timeline mentions
- Unique authors
- Attention velocity
- Breadth
- Author concentration
- Coordination clusters
- Narrative changes
- Engagement quality
- Crowd lifecycle timeline
- Lana market-phase markers
- Underlying posts dan provenance
- Score decomposition
- Data-quality indicators

## 11.3 Campaign Detector

Menampilkan:

- Seed accounts
- Amplifier accounts
- Copy clusters
- Shared URLs/media
- Posting synchrony
- Cluster graph
- Cross-token campaign overlap
- Organic-versus-coordinated evolution

## 11.4 Narrative Tape

Feed terminal real-time:

```text
10:02 BULLA — 17 new unique authors in 5m
10:03 BULLA — narrative shifted to “short squeeze”
10:05 MYX   — duplicate cluster detected across 12 accounts
10:08 LAB   — attention rising, breadth remains weak
10:11 RAVE  — crowd state DORMANT → SEEDING
```

## 11.5 Lifecycle Monitor

- Current crowd state
- State confidence
- Previous state
- Transition time
- Evidence that caused transition
- Expected invalidation conditions
- Comparison to market phase Lana

## 11.6 System Health

- Provider status
- Last successful collection
- Lag
- Rate-limit state
- Queue depth
- Token coverage
- Account-metadata coverage
- Historical baseline readiness
- Collection gaps

---

## 12. Data Acquisition Strategy

Binance Square access adalah risiko feasibility terbesar.

Provider interface harus mempunyai tiga adapter:

1. **Fixture provider** untuk development dan deterministic tests
2. **JSON/CSV import provider** sebagai fallback operasional
3. **Authorized live provider** jika tersedia endpoint/feed yang didokumentasikan atau diizinkan

Sistem tidak boleh:

- Membypass CAPTCHA
- Membypass autentikasi
- Menghindari access restriction
- Mengakali anti-bot platform
- Menggunakan akun pihak lain tanpa izin

Jika live access belum tersedia, pengembangan intelligence engine tetap berjalan menggunakan fixtures dan imports.

Setiap observasi menyimpan:

- Event time
- Ingestion time
- Source
- Source record ID
- Collection method
- Data-quality status
- Raw payload reference
- Parser version

---

## 13. Data Model Awal

Entitas utama:

### Universe

- `lana_universe_snapshots`
- `tracked_tokens`
- `token_aliases`
- `priority_transitions`

### Social observations

- `social_posts`
- `social_accounts`
- `account_snapshots`
- `post_mentions`
- `engagement_snapshots`
- `post_media`
- `post_links`

### Intelligence

- `duplicate_clusters`
- `coordination_clusters`
- `cluster_memberships`
- `narratives`
- `post_narratives`
- `feature_snapshots`
- `crowd_state_snapshots`
- `score_snapshots`

### Operations

- `alerts`
- `alert_events`
- `ingestion_runs`
- `source_health`
- `collection_gaps`
- `dead_letter_records`
- `model_versions`

Semua hasil turunan membawa:

- Feature version
- Score version
- Model/rule version
- Event-time window
- Data-confidence value
- Provenance

---

## 14. Evaluasi

Sistem harus diuji secara historis dan prospektif.

### Pertanyaan evaluasi

- Apakah state `EMERGING` mendahului perluasan crowd?
- Apakah `BROADENING` lebih informatif daripada mention count saja?
- Apakah coordinated seeding kadang berkembang menjadi crowd organik?
- Apakah saturation mendahului crowd decay?
- Seberapa sering attention spike hanya berasal dari duplicate cluster?
- Apa hubungan crowd lifecycle dengan market phase Lana?
- Apakah Social Intelligence memberikan lead time tambahan terhadap Lana?

### Metrik

- State-transition precision
- Alert precision dan false-positive rate
- Lead time terhadap market phase
- Unique-author growth after alert
- Narrative persistence
- Coordination-cluster persistence
- Crowd-state duration
- Transition calibration
- Data-confidence calibration
- Forward return distribution sebagai evaluasi sekunder, bukan label utama

### Baseline pembanding

- Mention count saja
- Unique-author count saja
- Sentiment sederhana
- Engagement count saja
- Random threshold
- Lana market signal tanpa social enrichment
- Lana market signal dengan social enrichment

Gunakan walk-forward evaluation dan event-time processing. Sistem replay tidak boleh membaca observasi masa depan.

---

## 15. Rekomendasi Stack

Untuk local Docker MVP:

- **Frontend:** Next.js, TypeScript, Tailwind
- **API:** FastAPI, Pydantic, SQLAlchemy
- **Workers:** Python background workers/scheduler
- **Database:** PostgreSQL; TimescaleDB jika cocok
- **Queue/cache:** Redis
- **NLP awal:** deterministic rules + embeddings lokal/hemat biaya
- **Clustering:** MinHash/SimHash untuk near-duplicate, graph clustering untuk coordination
- **Charts:** library time-series yang mendukung dense terminal display
- **Deployment:** Docker Compose

Gunakan modular monolith pada tahap awal. Tidak perlu Kafka atau microservices sebelum volume data membuktikan kebutuhannya.

---

## 16. Tahapan Implementasi

### Phase 0 — Data Feasibility

- Verifikasi metode akses Binance Square yang sah
- Tentukan fixture format
- Definisikan Lana Universe Contract
- Ambil snapshot universe read-only dari Lana
- Susun canonical token aliases

### Phase 1 — Vertical Slice Satu Token

Bangun satu alur lengkap:

```text
Lana universe
→ satu token prioritas
→ Square fixture/import
→ mention extraction
→ unique-author metrics
→ duplicate detection
→ attention/breadth/coordination scores
→ crowd state
→ Crowd Radar
```

Acceptance criteria:

- Satu token dapat ditelusuri dari input Lana hingga UI
- Semua skor memiliki evidence
- Missing data terlihat jelas
- Replay menghasilkan output deterministik

### Phase 2 — Multi-token Crowd Radar

- Priority scheduler
- Multi-window features
- Dynamic promotion
- Token Crowd Map
- Data freshness dan health monitoring

### Phase 3 — Coordination dan Narrative

- Near-duplicate clustering
- Seed/amplifier graph
- Shared media/link detection
- Narrative taxonomy
- Narrative shift detection
- Campaign Detector

### Phase 4 — Lifecycle dan Alerts

- Crowd lifecycle state machine
- Hysteresis dan minimum duration
- Alert engine
- Narrative Tape
- Generic webhook ke Lana

### Phase 5 — Historical Replay dan Validation

- Event-time replay
- Versioned scores
- Baseline comparison
- Crowd/market phase alignment
- Lead-time analysis
- False-positive review workflow

### Phase 6 — Hardening

- Authentication
- Secrets handling
- Retention policy
- Backup/restore
- Rate limiting
- Observability
- Operator runbook
- Failure/degraded-mode tests

---

## 17. MVP yang Direkomendasikan

MVP pertama harus sempit dan dapat dibuktikan:

1. Sinkronisasi token prioritas dari Lana
2. Pengumpulan/import Binance Square untuk token tersebut
3. Mention resolution
4. Unique-author dan author-concentration metrics
5. Duplicate/coordination detection dasar
6. Attention, Breadth, Coordination, dan Data Confidence scores
7. Empat state awal: `DORMANT`, `SEEDING`, `EMERGING`, `BROADENING`
8. Crowd Radar
9. Token detail timeline
10. Alert `SEEDING`, `EMERGING`, dan `BROADENING`
11. Export social enrichment untuk Lana
12. Deterministic replay

Fitur sentiment kompleks, graph lanjutan, dan klasifikasi narrative berbasis model dapat ditambahkan setelah kualitas collection dan baseline terbukti.

---

## 18. Prinsip Produk

1. **Crowd, bukan sekadar sentiment.**
2. **Perubahan relatif lebih penting daripada jumlah absolut.**
3. **Breadth lebih penting daripada volume mentah.**
4. **Coordination bukan otomatis buruk; harus dibedakan dari organic expansion.**
5. **Semua skor harus explainable dan dapat ditelusuri ke observasi.**
6. **Missing data tidak sama dengan zero.**
7. **Social signal adalah evidence, bukan kepastian.**
8. **Lana memilih token; Social Intelligence membaca crowd.**
9. **Tidak ada manipulasi atau impersonasi.**
10. **Validasi historis dan forward observation harus lebih dipercaya daripada narasi keberhasilan.**

---

## 19. Kesimpulan

Social Intelligence Desk berfungsi sebagai otak sosial yang melengkapi market intelligence Lana:

```text
Lana-Migration:
“Aset mana yang secara struktur pasar patut diawasi?”

Social Intelligence:
“Apakah crowd untuk aset tersebut sedang ditanam, tumbuh, meluas,
dikoordinasikan, jenuh, didistribusikan, atau mulai menghilang?”
```

Diferensiasi sistem terletak pada kemampuan mengubah percakapan Binance Square menjadi **crowd lifecycle yang terstruktur, explainable, dan dapat diuji**.

Prioritas implementasi pertama adalah vertical slice satu token:

```text
Universe sync dari Lana
→ data Square
→ attention/breadth/coordination
→ crowd state
→ Crowd Radar
→ enrichment kembali ke Lana
```
