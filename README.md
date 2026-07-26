# arbovirus-surveillance-data

Reproducible data pipeline for digital surveillance of arboviruses (dengue,
chikungunya) and respiratory syndromes (SARI/influenza, COVID-19) in Brazil.
It collects and transforms a multi-source dataset linking official
epidemiological case counts (SINAN, SIVEP-Gripe), digital search behaviour
(Google Trends), meteorological data (INMET), Ministry of Health bulletins,
and news coverage (Agência Brasil/EBC) — stratified by federative unit (26
states + DF) and aligned temporally for surveillance modelling.

## Status

- [x] Collection — all six sources (SINAN, SIVEP, Google Trends search + related, climate, bulletins, EBC news)
- [x] Pipeline — SINAN (`arboili transform sinan`): case-level CSVs → weekly case series
- [ ] Pipeline — SIVEP-Gripe (SARI): case-level → weekly series
- [ ] Pipeline — Google Trends: align weekly/monthly series to the epi-week calendar
- [ ] Pipeline — Climate: station ZIPs → daily/weekly series by state
- [ ] Pipeline — Bulletins: PDF text extraction (currently stored raw, unparsed)
- [ ] Pipeline — EBC news: article text extraction (currently stored raw, unparsed)
- [ ] Final merge: join all series into one `Arbo_SARI_disease_table`

## Project structure

```
├── src/
│   ├── collection/       ← extractors: raw data in, nothing transformed
│   │   ├── cli.py        ← `arboili` CLI entry point
│   │   ├── epidemiological/   ← SINAN downloader (MoH S3)
│   │   ├── google_trends/     ← pytrends search + related topics/queries
│   │   ├── climate/           ← INMET annual ZIP downloader
│   │   ├── bulletins/         ← MoH bulletin PDF scraper
│   │   └── ebc/                ← Agência Brasil news scraper
│   │
│   ├── pipeline/          ← post-collection transformations
│   │   └── sinan/         ← case-level CSVs → weekly case series (`arboili transform sinan`)
│   │
│   ├── config.py          ← config.yml loader, shared by collection & pipeline
│   └── common.py          ← shared ExtractResult type
│
├── tests/                 ← unit + integrity tests, run on committed fixtures
├── notebooks/             ← interactive runs of the collection pipeline
├── config.yml             ← all extraction/transformation settings
└── data/                  ← downloaded & transformed data (git-ignored)
```

## Concerns per source

| Source | Concern |
|---|---|
| SINAN (dengue cases) | S3 availability varies by year; case-level, aggregated to weekly by `pipeline/sinan` |
| SIVEP-Gripe (SARI) | Separate registry, processed independently from SINAN |
| Google Trends search | Relative index (0–100), not absolute volume; rate-limited, resumable |
| Google Trends related | ~13k requests, ~18h resumable run; saves after every request |
| Climate (INMET) | Large ZIPs (50–200 MB each); raw ZIPs left unextracted |
| Bulletins | Scraper depends on MoH's Plone CMS pagination; PDFs stored raw, unparsed |
| EBC news | Raw HTML only, no text-extraction pipeline yet; resumable via cursor |

## Installation

The project is managed with [uv](https://docs.astral.sh/uv/). Install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then clone the repository and create the environment:

```bash
git clone https://github.com/caio-olubini/arbovirus-surveillance-data
cd arbovirus-surveillance-data
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock` and builds a virtual environment in `.venv/` with exactly the pinned dependency versions. Python 3.12 or newer is required; uv will fetch a suitable interpreter automatically if none is present.

No API keys or credentials are needed — every source is a public endpoint.

## Usage

All extractors are exposed through the `arboili` command. Run it with `uv run`, which uses the project environment without needing to activate it:

```bash
uv run arboili --list
```

| Command | Description | Approx. runtime |
|---|---|---|
| `uv run arboili --list` | Show configured sources and their settings | instant |
| `uv run arboili sinan` | SINAN dengue yearly case CSVs from the MoH S3 bucket | ~10 min |
| `uv run arboili gt-search` | Google Trends weekly search index, 5-year window | ~5 min |
| `uv run arboili gt-related` | Google Trends monthly related topics & queries | ~18 h |
| `uv run arboili climate` | INMET annual meteorological ZIPs | ~30 min |
| `uv run arboili bulletins` | Ministry of Health epidemiological bulletin PDFs | ~20 min |
| `uv run arboili ebc` | Agência Brasil news articles | ~1 h per query |
| `uv run arboili all` | Runs sinan, gt-search, climate, bulletins, and ebc in order | ~2 h |
| `uv run arboili transform sinan` | SINAN case-level CSVs → weekly case series | ~11 s |

`all` deliberately excludes `gt-related`, which is an ~18-hour job better started on its own. Within `all`, a source that fails is logged and the run continues to the next one.

Every extractor is idempotent and resumable: already-downloaded files are skipped, progress is tracked in a `manifest.csv` (or `manifest.jsonl`) per source, and interrupting a run is safe — re-running picks up where it left off.

Pass `--verbose` for debug logging, and `--help` on any subcommand for its full flag list.

### Configuration

Defaults for every source live in [`config.yml`](config.yml) — year ranges, output directories, request delays, the Google Trends reference date, and the list of EBC search queries. Command-line flags override the file for one-off runs:

```bash
uv run arboili sinan --to-year 2023          # narrow the year range
uv run arboili gt-related --sleep 10         # back off harder on rate limits
uv run arboili ebc --query chikungunya       # ad-hoc scrape → data/news/chikungunya/
uv run arboili --config other.yml all        # use a different config file
```

Precedence is command-line flag → `config.yml` value → built-in default.

### Tests

```bash
uv run pytest                     # unit + integrity tests, runs on committed fixtures
uv run pytest -m integration      # re-runs the integrity checks on the full series
```

## Data sources

| Source | Origin | Coverage | Granularity |
|---|---|---|---|
| SINAN dengue | MoH open-data S3 bucket | 2010–2024 | case-level → weekly by state |
| SIVEP-Gripe (SARI) | MoH | varies–2024 | weekly by state |
| Google Trends search | pytrends | 2019-12–2024-12 | weekly, 27 UFs + BR |
| Google Trends related | pytrends | 2020-01–present | monthly, 27 UFs + BR |
| Climate | INMET historical archive | 2000–present | daily by station |
| Bulletins | gov.br/saude | 2019–2026 | weekly, national |
| News | Agência Brasil (EBC) | varies–present | article-level, national |

Google Trends values are relative indices (0–100, normalised per request window), not absolute search counts. Disease terms are queried through Freebase topic IDs rather than free text — see `data/google_trends/popular_terms.csv`.
