# ARBOILI — Digital Surveillance of Arboviruses in Brazil

ARBOILI is a reproducible data paper on digital surveillance of arboviruses (dengue, chikungunya) and respiratory syndromes (SARI/influenza, COVID-19) in Brazil. It collects, transforms, and publishes a multi-source dataset that links official epidemiological case counts (SINAN and SIVEP-Gripe), digital search behaviour (Google Trends weekly search interest plus monthly related topics and queries), meteorological observations (INMET), Ministry of Health epidemiological bulletins, and press coverage from Agência Brasil (EBC). Every source is stratified by Brazilian federative unit — 26 states, the Federal District, and a national aggregate — and aligned temporally so the series can be used directly for surveillance modelling. Python modules under `src/` handle extraction; R scripts under `r/` handle transformation into the final analysis table.

## Installation

The project is managed with [uv](https://docs.astral.sh/uv/). Install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then clone the repository and create the environment:

```bash
git clone <repository-url>
cd dengue-data-collection
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

`all` deliberately excludes `gt-related`, which is an ~18-hour job better started on its own. Within `all`, a source that fails is logged and the run continues to the next one.

Every extractor is idempotent and resumable: already-downloaded files are skipped, progress is tracked in a `manifest.csv` (or `manifest.jsonl`) per source, and interrupting a run is safe — re-running picks up where it left off. This matters most for `gt-related`, which saves after every successful request and exits gracefully on an HTTP 429.

Pass `--verbose` for debug logging, and `--help` on any subcommand for its full flag list.

### Configuration

Defaults for every source live in [`config.yml`](config.yml) — year ranges, output directories, request delays, the Google Trends reference date, and the list of EBC search queries. This file records the canonical run used for the data paper.

Command-line flags override the file for one-off runs, without editing it:

```bash
uv run arboili sinan --to-year 2023          # narrow the year range
uv run arboili gt-related --sleep 10         # back off harder on rate limits
uv run arboili ebc --query chikungunya       # ad-hoc scrape → data/news/chikungunya/
uv run arboili --config other.yml all        # use a different config file
```

Precedence is command-line flag → `config.yml` value → built-in default. Relative paths in the config are resolved against the project root.

### Notebook

[`data_collection.ipynb`](data_collection.ipynb) runs the same extractors interactively, reading its settings from `config.yml` so the notebook and CLI stay in step. It also carries the validation section — offline helper tests, a live Google Trends smoke test, an S3 availability probe, and a final inventory of every output file. Launch it with:

```bash
uv run jupyter lab            # or open the file in VS Code and pick the .venv kernel
```

### R transformation scripts

After extraction, run the R scripts in `r/scripts/` in numeric order (`1_` → `2_` → `3_` → `4_`) to aggregate the raw case-level CSVs and merge every source into the final `data/epidemiological/Arbo_SARI_disease_table.csv`. The R side is not managed by uv and needs its own R installation.

## Project layout

```
├── config.yml              ← all extraction settings
├── pyproject.toml          ← dependencies and the `arboili` entry point
├── data_collection.ipynb   ← interactive pipeline + validation
├── article.pdf             ← the research article
│
├── src/                    ← Python extraction modules
│   ├── cli.py              ← `arboili` command
│   ├── config.py           ← config.yml loader
│   ├── common.py           ← shared ExtractResult type
│   ├── epidemiological/    ← SINAN downloader
│   ├── google_trends/      ← pytrends wrappers + two extractors
│   ├── climate/            ← INMET downloader
│   ├── bulletins/          ← MoH bulletin scraper
│   └── ebc/                ← Agência Brasil news scraper
│
├── data/                   ← all downloaded data (git-ignored)
└── r/                      ← transformation scripts and functions
```

## Data sources

| Source | Origin | Coverage | Granularity |
|---|---|---|---|
| SINAN dengue | MoH open-data S3 bucket | 2010–present | case-level → weekly by state |
| SIVEP-Gripe (SARI) | MoH | varies–2024 | weekly by state |
| Google Trends search | pytrends | 2019-12–2024-12 | weekly, 27 UFs + BR |
| Google Trends related | pytrends | 2020-01–present | monthly, 27 UFs + BR |
| Climate | INMET historical archive | 2000–present | daily by station |
| Bulletins | gov.br/saude | 2019–2026 | weekly, national |
| News | Agência Brasil (EBC) | varies–present | article-level, national |

Google Trends values are relative indices (0–100, normalised per request window), not absolute search counts. Disease terms are queried through Freebase topic IDs rather than free text so results are unambiguous across spellings — see `data/google_trends/popular_terms.csv`.

## Notes and caveats

- **Rate limiting.** Google Trends throttles heavy traffic. Both GT extractors sleep between requests (2 s for search, 5 s for related); raise `sleep` in `config.yml` if you see HTTP 429s.
- **Disk space.** Individual INMET ZIPs run 50–200 MB, and the full SINAN series is several GB.
- **Upstream fragility.** The bulletin scraper depends on the Ministry of Health's Plone CMS pagination and can break if the site is restructured. SINAN S3 availability varies by year — the notebook's probe cell reports which years are live.
- **Unparsed sources.** Bulletin PDFs and EBC article HTML are stored raw; no text-extraction pipeline exists for them yet.
- **urllib3 pin.** `pytrends` 4.9.2 calls `Retry(method_whitelist=...)`, which was removed in urllib3 2.x, so the project pins `urllib3<2`. Without it, every `gt-search` request fails with a `TypeError`.
