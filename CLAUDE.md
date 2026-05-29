# ARBOILI Data Paper — Claude Context

## Project Goal

ARBOILI is a **reproducible data paper** on digital surveillance of arboviruses (dengue, chikungunya) and respiratory syndromes (SARI/influenza, COVID-19) in Brazil. The primary objective is to collect, transform, and publish a multi-source dataset linking:

- **Epidemiological case counts** (SINAN/SIVEP official notified cases)
- **Digital search behavior** (Google Trends weekly/monthly search interest)
- **Climate data** (INMET meteorological stations)
- **Health bulletins** (Brazilian Ministry of Health PDFs)
- **News articles** (EBC/Agência Brasil press coverage)

All analyses are stratified by Brazilian federative unit (28 states + DF) and aligned temporally for surveillance modeling.

---

## Directory Tree

```
Arboili_datapaper/
├── CLAUDE.md                             ← this file
├── README.md                             ← one-line project description
├── article.pdf                           ← final research article
├── data_collection.ipynb                 ← main orchestration notebook (run this)
│
├── data/
│   ├── epidemiological/
│   │   ├── SINAN/                        ← yearly dengue case CSVs (2010–2024)
│   │   ├── SIVEP/                        ← SARI case data
│   │   ├── Arbo_SARI_disease_table.csv   ← merged arbovirus + SARI (841 KB, final output)
│   │   └── br_federative_units.csv       ← 28 states reference table (CODE, NAME, ABR, REGION)
│   │
│   ├── google_trends/
│   │   ├── GoogleTrends_search.csv       ← 5-year weekly search index, all states (6.5 MB)
│   │   ├── GoogleTrends_related_topic.csv← monthly related topics 2020+ (6.3 MB)
│   │   ├── GoogleTrends_related_query.csv← monthly related queries 2020+ (3.3 MB)
│   │   ├── popular_terms.csv             ← controlled vocabulary: diseases + symptoms in PT
│   │   ├── manifest_search.csv           ← extraction progress log
│   │   └── manifest_related.csv          ← extraction progress log
│   │
│   ├── climate/
│   │   ├── <year>.zip                    ← INMET annual station data (2000–present)
│   │   ├── manifest.csv                  ← download status
│   │   └── failures.csv                  ← failed downloads
│   │
│   ├── bulletins/
│   │   ├── 2019/ … 2026/                 ← ~280 epidemiological bulletin PDFs
│   │   ├── manifest.csv                  ← crawl index (77.9 KB)
│   │   └── failures.csv
│   │
│   └── news/
│       └── dengue/
│           ├── state.json                ← scraping cursor/metadata
│           ├── manifest.jsonl            ← article index (one JSON per line)
│           ├── listings/                 ← raw search result HTML
│           └── articles/                 ← saved article HTML
│
├── src/                                  ← Python extraction modules
│   ├── common.py                         ← ExtractResult dataclass (shared return type)
│   ├── epidemiological/
│   │   └── sinan_dengue.py               ← downloads SINAN CSVs from MoH S3
│   ├── google_trends/
│   │   ├── gtrends_api.py                ← pytrends wrapper + helpers
│   │   ├── extract_gt_search.py          ← 5-year weekly search index extractor
│   │   └── extract_gt_related.py         ← monthly related topics & queries extractor
│   ├── climate/
│   │   └── download_inmet_data.py        ← INMET annual ZIP downloader
│   ├── bulletins/
│   │   └── download_boletins.py          ← MoH bulletin PDF scraper
│   └── ebc/
│       ├── scraper.py                    ← EBC/Agência Brasil news scraper
│       ├── http_client.py                ← HTTP session + retry logic
│       ├── models.py                     ← Article, State dataclasses
│       ├── parsers.py                    ← HTML parsing utilities
│       └── storage.py                    ← file I/O + manifest management
│
└── r/
    ├── functions/
    │   ├── fun.R
    │   ├── getGraph2.R
    │   ├── getTopQueries2.R
    │   └── getTopTopics2.R
    └── scripts/
        ├── 1_extract_data.R
        ├── 2_transform_sinan_data.R      ← aggregates raw SINAN yearly CSVs → final table
        ├── 2_1_transform_sivep_data.R    ← processes SIVEP SARI data
        ├── 3_extract_GT_api.R            ← original R version of GT search extraction
        ├── 3_1_extract_related_search_GT_api.R
        ├── 4_prepare_final_disease_table.R ← merges all sources into Arbo_SARI_disease_table.csv
        └── align_gtrends_curve.R         ← temporal alignment of GT curves
```

---

## Data Sources & Their Concerns

### 1. SINAN — Epidemiological Cases
- **Source**: Brazilian Ministry of Health S3 bucket
- **URL pattern**: `https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINAN/Dengue/csv/DENGBR<YY>.csv.zip`
- **Coverage**: 2010–2024 yearly case-level CSVs; 2025 not yet published
- **Diseases**: Dengue, Chikungunya (separate datasets)
- **Output**: `data/epidemiological/SINAN/*.csv` → transformed by `r/scripts/2_transform_sinan_data.R`
- **Concerns**:
  - S3 availability varies; run the smoke-test cell in `data_collection.ipynb` to probe live years
  - Raw files are case-level (one row per patient); aggregation to weekly/state-level is done in R
  - SIVEP data (SARI hospitalizations) is processed separately via `2_1_transform_sivep_data.R`

### 2. Google Trends — Search Index
- **Source**: pytrends (unofficial Google Trends API, no key required)
- **Coverage**: 5-year rolling window ending 2024-12-31; weekly granularity
- **Scope**: 28 Brazilian states + national (BR), diseases + symptom terms
- **Terms**: Defined in `data/google_trends/popular_terms.csv` (Portuguese names + Freebase topic IDs)
- **Output**: `data/google_trends/GoogleTrends_search.csv` (6.5 MB)
- **Concerns**:
  - Google Trends values are relative (0–100 scale, normalized per request window)
  - Rate limiting: HTTP 429 is common; extraction sleeps 2s per request (~3–5 min total)
  - Must use Freebase topic IDs (`/m/XXXXX`) for disease terms, not free text — see `popular_terms.csv`
  - Values are filled with 0 for (date, location, topic) combinations with no data
  - Resumable: manifest tracks progress, safe to re-run

### 3. Google Trends — Related Topics & Queries
- **Source**: pytrends `related_topics()` and `related_queries()`
- **Coverage**: Monthly from 2020-01 to present
- **Scope**: 28 states + BR, diseases (dengue, chikungunya, influenza, COVID-19)
- **Output**: `GoogleTrends_related_topic.csv` (6.3 MB) + `GoogleTrends_related_query.csv` (3.3 MB)
- **Concerns**:
  - ~13,400 HTTP requests total; 5s sleep per request ≈ **18 hours runtime**
  - Highly resumable — saves after every successful request; safe to interrupt
  - HTTP 429 triggers a full save + graceful exit; restart from where it left off

### 4. Climate — INMET Meteorological Data
- **Source**: INMET (Instituto Nacional de Meteorologia) historical archive
- **URL pattern**: `https://portal.inmet.gov.br/uploads/dadoshistoricos/<year>.zip`
- **Coverage**: 2000–present (annual ZIPs with station-level daily data)
- **Content**: Temperature, humidity, rainfall, wind speed, solar radiation
- **Concerns**:
  - Individual ZIP files can be 50–200 MB; ensure disk space
  - Retry logic built in (3 attempts, 1s backoff); idempotent
  - Raw ZIPs are not unzipped in the pipeline — downstream R scripts handle that

### 5. Health Bulletins — Ministry of Health PDFs
- **Source**: `https://www.gov.br/saude/pt-br/centrais-de-conteudo/publicacoes/boletins/epidemiologicos`
- **Coverage**: 2019–2026, ~280 weekly epidemiological bulletins
- **Concerns**:
  - Scraper relies on Plone CMS pagination — can break if MoH restructures the site
  - PDF URLs must be resolved through a chain of redirects (listing → item page → PDF link)
  - Not all entries are dengue-specific; bulletins cover all notifiable diseases
  - Currently not parsed — PDFs are stored raw for future NLP/extraction work

### 6. EBC News Articles — Agência Brasil
- **Source**: EBC search API (`busca.ebc.com.br`)
- **Query**: "dengue" (and optionally other arboviruses)
- **Concerns**:
  - Scraper saves raw HTML; no text extraction pipeline exists yet
  - Resumable via `state.json` cursor; never re-fetches already-saved articles
  - 1s delay between requests; EBC search results are paginated (100/page)
  - `manifest.jsonl` is append-only — check for duplicates if re-scraping from scratch

---

## Key Vocabulary

| Term | Meaning |
|------|---------|
| SINAN | Sistema de Informação de Agravos de Notificação — Brazil's notifiable disease registry |
| SIVEP-Gripe | Sistema de Vigilância Epidemiológica da Gripe — SARI hospitalization registry |
| SARI | Severe Acute Respiratory Infection |
| INMET | Instituto Nacional de Meteorologia — Brazilian weather authority |
| EBC | Empresa Brasil de Comunicação — state news agency (Agência Brasil) |
| Arboviruses | Arthropod-borne viruses: dengue, chikungunya, zika (all in scope) |
| Freebase ID | Google Knowledge Graph topic ID (e.g. `/m/09wsg`) used for unambiguous GT queries |
| GT | Google Trends |
| federative unit | Brazilian state-level administrative unit (26 states + DF = 27 total + BR national) |

---

## Running the Pipeline

The canonical entry point is `data_collection.ipynb`. Run cells top to bottom:

1. **Setup** — creates `data/` subdirectories
2. **SINAN** — downloads yearly dengue CSVs
3. **Google Trends search** — ~5 min
4. **Google Trends related** — ~18 hours (resumable, run overnight)
5. **Climate** — downloads INMET ZIPs
6. **Bulletins** — scrapes MoH PDFs
7. **EBC news** — scrapes Agência Brasil articles
8. **Validation** — smoke tests + output inventory

After Python extraction, run R scripts in order (`1_` → `2_` → `3_` → `4_`) to produce the final `Arbo_SARI_disease_table.csv`.

All extractors are **idempotent** — safe to re-run. Progress is tracked in `manifest.csv` files per source.

---

## Temporal & Geographic Scope

| Source | Start | End | Granularity | Geography |
|--------|-------|-----|-------------|-----------|
| SINAN dengue | 2010 | 2024 | yearly files, weekly aggregation in R | state |
| SIVEP SARI | varies | 2024 | weekly | state |
| GT search | 2019-12 | 2024-12 | weekly | state + BR |
| GT related | 2020-01 | present | monthly | state + BR |
| Climate | 2000 | present | daily (station) | station → state |
| Bulletins | 2019 | 2026 | weekly (irregular) | national |
| EBC news | varies | present | article-level | national |
