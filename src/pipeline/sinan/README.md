# ARBOILI Transformation Pipeline

Documentation of how raw notification records become the analysis-ready
historical series. This describes **data flow and decision rules**, not code —
it is intended as source material for the methods section of a paper.

The reference implementation is the SINAN dengue pipeline (`src/pipeline/sinan/`).
The transformation follows the methodology of the ARBOILI data descriptor:

> Borges, M. E., Codeço, C. T., Machado, D. & Almeida, A. *Epidemiological and
> digital syndromic surveillance data on dengue, chikungunya, and SARI in
> Brazil.* **Scientific Data** 13, 68 (2026).
> https://doi.org/10.1038/s41597-025-06155-6
> Dataset: https://doi.org/10.5281/zenodo.17102575

Where this pipeline diverges from the published R implementation, the divergence
is stated explicitly and justified. Those points are collected in
[§7 Divergences](#7-divergences-from-the-published-implementation).

---

## 1. Objective and scope

**Goal.** Reconstruct the historical time series of dengue case notifications in
Brazil, aggregated by epidemiological week and federative unit, from case-level
records published by the Ministry of Health — **and extend it beyond the
published window.**

The article's SINAN coverage ends in 2024. This pipeline processes every yearly
file available, currently through 2026, producing a series that is two years
longer than the published one. Reproducing the article is the *validation
strategy*, not the endpoint: agreement across 2010–2024 is what licenses trust in
the years the article does not cover.

Consequently, no step in this pipeline is bounded by 2024. The article's window
appears in exactly one place — an automated check asserting it is complete and
gap-free (§6) — and nowhere in the transformation itself.

**Input.** Yearly case-level exports from SINAN (*Sistema de Informação de
Agravos de Notificação*), Brazil's notifiable disease registry — one row per
notified patient, one file per processing year, 2010–2026. Roughly 28.6 million
records across 17 files. New years require no code change: a file appearing in
the input directory is picked up on the next run.

**Output.** One row per unique combination of (data-entry week, notification
week, symptom-onset week, final classification, federative unit), with a case
count. Approximately 1.06 million rows.

**Deliberately out of scope.** This pipeline produces *case counts only*. The
published R implementation additionally carries fourteen symptom columns
(fever, myalgia, rash, and so on), which support the article's symptom-level
analysis of search behaviour. Those serve a different research question and are
excluded here — a decision that removes roughly two thirds of the columns read
from disk and, with them, several schema complications that apply only to
symptom fields.

Also out of scope: SIVEP-Gripe (SARI), Google Trends, climate, bulletins, news,
and the final multi-source merge into `Arbo_SARI_disease_table`.

---

## 2. Source data and its structure

SINAN records are downloaded from the Ministry of Health's open data
infrastructure as one compressed CSV per year. Each file holds every dengue
notification *processed* in that year.

Two structural properties of the source drive most of the design:

**Files are named by processing year, not by event year.** A notification filed
in late December 2012 but keyed into the system in January 2013 may appear in
either file. Consequently, a single epidemiological week can be split across two
yearly files. This is handled in §4.5.

**The export schema changed twice over the covered period.** Three distinct
generations exist:

| Period | Columns | Data-entry date (`DT_DIGITA`) |
|---|---|---|
| 2010–2013 | 66 | present and populated |
| 2014–2019 | 119 | **column absent entirely** |
| 2020 | 121 | column present, **every value empty** |
| 2021–2026 | 121 | present and populated |

The availability gap in the data-entry date therefore spans **2014–2020**, and it
is **non-monotonic**: the field is present, then unavailable, then present again.
The article records this in its Table 1 footnote ("Missing variable in the
databases for the years 2014 to 2020").

This matters methodologically because the 2020 case is invisible to a schema
inspection — the column is declared and simply carries no data. Any rule
expressed as a threshold ("available from year *N* onward") is wrong for this
field regardless of which threshold is chosen. The pipeline therefore declares
availability as an explicit set of affected years, and an automated check
verifies the resulting nulls fall exactly where expected.

Five fields are read from each record; everything else is discarded at read time:

| Field | Meaning |
|---|---|
| `DT_DIGITA` | date the notification was keyed into the system |
| `DT_NOTIFIC` | date the notification form was completed |
| `DT_SIN_PRI` | date of first symptoms |
| `SG_UF_NOT` | federative unit of notification (numeric IBGE code) |
| `CLASSI_FIN` | final case classification |

---

## 3. Design constraints

**Volume against memory.** The seventeen input files total approximately 7 GB
uncompressed, with the largest single file (2024) at 1.7 GB and 6.6 million
records. Available working memory on the development machine was roughly 6 GB.
Reading the corpus into memory is therefore not possible, and the pipeline is
structured around that constraint rather than treating it as an optimisation.

Three mechanisms address it:

1. **Column projection at read time.** Only the five required fields are parsed;
   the remaining 61–116 columns are never materialised. Because the retained
   fields are dates and short codes, the relevant payload of even the largest
   file is a small fraction of its size on disk.
2. **Year-at-a-time processing.** Each yearly file is aggregated to its summary
   form before the next is opened. No two years of case-level records are ever
   resident simultaneously.
3. **Streaming aggregation.** The grouping operation processes each file in
   chunks rather than materialising it whole.

Observed result: the full corpus is transformed in approximately 12 seconds with
a peak memory footprint of 2.1 GB.

**Reproducibility.** The transformation is a pure function of the input files and
the stated parameters. It holds no state between runs, and re-running it
overwrites its outputs with identical content.

---

## 4. The transformation, step by step

### 4.1 Selection

For each yearly file, read the five fields listed in §2. Where the data-entry
date is unavailable for that year (2014–2020, per the availability set), it is
represented as missing rather than absent, so that every year yields the same
structure and the years can be combined.

**Rule:** missing is not zero. A year with no data-entry date produces *null*
values, not a default date. The distinction is preserved through to the published
output, where roughly 190,000 rows carry a null data-entry week — an honest
record of the source gap rather than a fabricated value.

### 4.2 Date parsing

The three date fields are parsed from ISO format. SINAN represents missing dates
as empty fields, and a small number of malformed values occur; both become null
rather than aborting the run. This mirrors the coercion behaviour of the original
R implementation.

### 4.3 Consistency filtering

Records are retained when the interval between symptom onset and notification
satisfies:

```
-180 days  <  (symptom onset − notification date)  <  1 day
```

Both bounds are strict. The rule has two distinct purposes:

- **The lower bound** removes data-entry errors. The article describes excluding
  records where the interval "exceeded 180 days," attributing such gaps to
  errors "such as data entry errors or incorrect patient date of birth entries in
  the symptom onset field." A birth date mistakenly entered as the onset date
  produces an interval of years, and is caught here.
- **The upper bound** removes records where symptom onset is dated *after* the
  notification itself — chronologically impossible, and therefore also an entry
  error.

Records with a missing onset or notification date fail the comparison and are
excluded, consistent with the original implementation.

**Effect on the corpus:** 69,476 of 28,638,473 records are excluded, or 0.24%.

**A note for the methods section.** The article's prose describes only the
180-day rule; the upper bound is present in the published R code but not in the
text. This pipeline implements both, because the objective is to reproduce the
published series. Both bounds are exposed as parameters, so the looser reading —
excluding only intervals beyond 180 days in either direction — can be selected
without modifying the transformation. The default reproduces the published
dataset.

### 4.4 Temporal alignment to epidemiological weeks

Each of the three dates is mapped to the **first day of its epidemiological
week**. The article defines this unit as "a standardized time unit used in public
health surveillance, typically defined as a seven-day period that starts on
Sunday and ends on Saturday, facilitating consistent reporting and analysis of
disease trends across different regions' timeframes."

**Rule:** each date is replaced by the Sunday on or before it. A date already
falling on a Sunday is unchanged.

Two representational choices are worth recording:

- **Weeks are represented as dates, not week numbers.** The output carries the
  Sunday that begins the week (e.g. `2024-01-07`), not an ordinal such as
  `2024W01`. This matches the published dataset and, more importantly, makes
  temporal joins with other sources direct: the article notes that Google Trends
  weekly data is likewise keyed to Sunday, "which aligns with the start of the
  epidemiological week, enabling a direct comparison between search volume for
  that week and the number of reported cases."
- **The source's own week columns are not used.** SINAN files carry native week
  numbers (`SEM_NOT`, `SEM_PRI`) in `YYYYWW` form. These are ignored in favour of
  deriving weeks from the dates, so that all three time axes are computed by one
  consistent rule and the year-boundary convention is explicit rather than
  inherited.

This step is the most error-sensitive in the pipeline: an incorrect week anchor
would displace every observation in the series by up to six days while producing
output that looks entirely plausible. It is consequently isolated, and verified
against year boundaries and leap days, with an automated check confirming that
every date in the finished series falls on a Sunday.

### 4.5 Aggregation

Records are grouped by the five key fields — the three epidemiological weeks, the
final classification, and the federative unit — and counted.

A second aggregation follows, after all years are combined. This is required
because of the file-naming property described in §2: the epidemiological week
beginning Sunday 30 December 2012 extends into January 2013, so cases in that
week are distributed across the 2012 and 2013 files. Aggregating each year
independently emits that group twice, with the count divided between the two
rows. Totals remain correct, but the series contains duplicate keys.

**Effect on the corpus:** 468 group keys were affected, all in the week spanning
the 2012–2013 boundary. The second aggregation merges them.

This is worth documenting as a general hazard of the approach: any pipeline that
partitions by calendar year while aggregating by epidemiological week will
encounter it, because the two calendars do not align.

### 4.6 Geographic labelling

The numeric IBGE code identifying the federative unit is replaced by its
two-letter abbreviation (`35` → `SP`), using the reference table of Brazil's 27
federative units (26 states and the Federal District). Codes absent from the
reference table are dropped rather than retained as unlabelled.

### 4.7 Ordering and output

Rows are sorted by federative unit, then by the three week columns, then by
classification, with nulls ordered last so that the result is deterministic.

The series is written in two formats: a columnar format as the primary artefact
for downstream analysis, and a compressed CSV for portability and compatibility
with the existing R scripts.

---

## 5. Output

| Property | Value |
|---|---|
| Rows | 1,057,243 |
| Cases represented | 28,568,997 |
| Federative units | 27 |
| Coverage | Aug 2009 – Jul 2026 (symptom-onset weeks) |
| Article window | 2010–2024 (reproduced) |
| Extension | 2025 complete, 2026 partial |
| Runtime | ~12 s |
| Peak memory | 2.1 GB |

### Schema

| Column | Type | Description |
|---|---|---|
| `ew_recorded` | date | First day of the epidemiological week of data entry. Null for 2014–2020 (see §2). |
| `ew_notification` | date | First day of the epidemiological week of notification. |
| `ew_symptom_onset` | date | First day of the epidemiological week of symptom onset. The primary time axis for surveillance analysis. |
| `final_classification` | integer | SINAN final case classification. Null where not yet classified. |
| `state_abbrev` | text | Federative unit of notification. |
| `case_count` | integer | Number of notifications in this combination. |

Three columns are deliberately nullable, each representing a distinct kind of
real-world absence: an unavailable source field (`ew_recorded`), a case not yet
adjudicated (`final_classification`), and — in the wider dataset — a period
outside a source's coverage. None is filled with a placeholder.

### Retaining unclassified cases

Rows with no final classification are retained rather than dropped. A case
awaiting adjudication is epidemiologically meaningful, particularly in recent
weeks: the article observes that "the most recent weeks in the dataset are always
incomplete due to reporting lags," and that recent cases "may be reclassified or
discarded after further epidemiological investigation." Discarding unclassified
records would understate exactly the period that nowcasting work targets.

### Deriving case counts

The series retains all classifications, including discarded cases
(`final_classification = 5`), so that downstream users can apply their own case
definition. To reproduce the article's dengue counts, exclude discarded records —
per its Table 5, the dengue criterion is "all dengue cases from SINAN-DENGUE,
except 'discarded' records." Applying that rule yields:

| Year | Cases | Status |
|---|---|---|
| 2010 | 1,015,861 | article window |
| 2011 | 743,886 | article window |
| 2012 | 618,320 | article window |
| 2013 | 1,448,599 | article window |
| 2014 | 590,788 | article window |
| 2015 | 1,712,992 | article window |
| 2016 | 1,484,901 | article window |
| 2017 | 244,092 | article window |
| 2018 | 272,434 | article window |
| 2019 | 1,552,924 | article window |
| 2020 | 935,323 | article window |
| 2021 | 539,782 | article window |
| 2022 | 1,404,368 | article window |
| 2023 | 1,696,058 | article window |
| 2024 | 6,540,531 | article window |
| **2025** | **1,613,560** | **extension — complete (52 weeks)** |
| **2026** | **419,222** | **extension — partial (28 weeks, to 12 Jul)** |

A small residue (13,275 cases) falls in 2009: onset weeks belonging to cases
notified in early 2010. It is retained rather than truncated, since the series is
keyed on symptom onset and those are real observations.

Through 2024 these reproduce Brazil's documented dengue epidemiology — the 2013
and 2015–2016 epidemic peaks, the 2017–2018 trough, and the exceptional 2024
season, which the article notes for its "unusually high incidence of dengue
cases" when discussing validation against TabNet. That agreement is the basis for
treating 2025 and 2026 as sound.

**2025 is a complete season** and can be analysed as such: 1.6 million cases, a
sharp fall from 2024's record but still above the 2021 trough. **2026 is
partial**, covering 28 weeks to 12 July, and must not be compared with full years.

### Complete versus partial years

Completeness is **measured**, not inferred from the calendar. The pipeline counts
distinct epidemiological weeks per year and flags only those falling short of a
full set. Being recent does not make a year partial: 2025 has all 52 weeks and is
reported as complete, while 2026 is flagged.

This distinction matters for an extended series. A rule of the form "anything
after the article window is provisional" would attach a spurious data-quality
caveat to 2025 — a complete year — and would need revising every January.

### Keeping the series current

The series grows by re-running the transformation after downloading newly
published files. Nothing in the pipeline enumerates years: input files are
discovered by name, and each is routed through the schema rules of §2 according
to the year it encodes.

Two consequences follow. Re-running is safe at any time — the transformation is
a pure function of its inputs, so it recomputes the whole series and overwrites
the previous output rather than appending. And re-running is *advisable* even
without new files, because SINAN revises historical records: cases are
reclassified, and previously unclassified notifications are adjudicated. A year
already published can therefore change, which is why the pipeline rebuilds the
full series rather than incrementally extending it.

The one case requiring attention is a new schema generation. If a future export
adds, removes, or empties a field the pipeline reads, the availability rules in
§2 need updating; the pipeline logs a warning when a file's structure disagrees
with what it expects, and the validation checks in §6 are designed to fail rather
than silently produce nulls.

---

## 6. Validation

Validation operates at two scales, running the same checks against both a small
committed sample and the full series, so that verification does not depend on
having the complete corpus available.

**Structural checks.** Column names and order; uniqueness of the group key (the
check that detected the year-boundary duplication in §4.5); all case counts
strictly positive.

**Temporal checks.** Every date in every week column falls on a Sunday; symptom
onset never follows notification; no missing weeks anywhere from 2010 to the end
of the series, since a gap would indicate a week lost in transformation rather
than a week without dengue in Brazil. Continuity is verified across the full
processed range, extension years included — that is the segment with no external
reference to check against, so it needs the structural guarantees most. The
series currently runs 884 consecutive weeks without a gap.

**Domain checks.** All federative units appear; all classification codes lie
within SINAN's documented range; the data-entry week is null across 2014–2020 and
populated on either side.

**Plausibility checks.** Annual case totals fall within a range consistent with
national surveillance figures — a guard against a filter silently discarding most
of the corpus, without hard-coding values that legitimately change as SINAN
records are revised. Applied to every complete year, extension years included;
partial years are excluded from the comparison, since a half-season legitimately
totals less than a full one.

**Extension-specific checks.** Two guard the extension explicitly: that the
series does in fact reach past the article's last year (so a future change cannot
quietly truncate it back to the reproduced window), and that a complete year
beyond that boundary is not reported as partial.

Two observations from validation deserve recording, as both are cases where the
data contradicted a reasonable prior:

- **Classification code `0`** appears from 2022 onward and is not among the codes
  documented in the article's Table 6. It is treated as valid; the check verifies
  the plausible code range rather than enforcing the published list.
- **The 2020 data-entry field** is declared but empty, as described in §2. This
  was detected by validation, not by schema inspection.

---

## 7. Divergences from the published implementation

Recorded explicitly, since they affect comparability with the published dataset.

**1. Symptom columns are excluded.** Scope decision (§1). The published
intermediate table carries fourteen additional count columns.

**2. Federative unit is labelled, not coded.** The published table names this
column `state_abbrev` but stores the numeric IBGE code. This pipeline stores the
abbreviation, making the column name accurate. Consumers expecting numeric codes
must adjust.

**3. Row ordering differs.** The published implementation sorts within each year
before concatenating; this pipeline sorts once over the combined series. The two
outputs contain the same rows with the same values, but not in the same order.
Comparisons should be made on sorted data rather than byte-for-byte.

**4. Year-boundary duplication is resolved.** Described in §4.5. The published
implementation aggregates per year without a subsequent merge, so the same
condition would produce duplicate keys there.

**5. Implementation language.** The original is R (`data.table`, `dplyr`,
`lubridate`); this is Python with a streaming columnar engine, adopted for the
memory constraint in §3. The transformation rules are unchanged. Note that the
original script's input pattern does not match the filenames the current
downloader produces, so it cannot run against the present data layout without
modification.

---

## 8. Known limitations

Limitations of the transformation itself, beyond those the article documents for
SINAN as a source (underreporting, regional heterogeneity in reporting practice,
and reclassification of recent cases).

**Reporting delay is not corrected.** The most recent weeks are structurally
incomplete. The article recommends that analyses "incorporate statistical methods
such as nowcasting, which allow for the adjustment of case estimates for the most
recent dates." No such adjustment is applied here; the series reports what was
notified, and correcting for delay is left to downstream analysis. The three
separate time axes are retained precisely so that delay can be modelled.

**The current year is always incomplete.** The most recent file covers a partial
season by construction and is flagged as such. This is a property of when the
pipeline is run, not of the extension — 2025 is complete and unflagged.

**Extension years carry no external cross-validation.** Agreement with the
article's figures can only be checked through 2024. For 2025 onward the series
rests on the structural and plausibility checks in §6 plus the fact that the same
unmodified transformation reproduces fifteen prior years. Where an independent
comparison is needed, TabNet publishes case numbers by year and federative unit,
and the article's Fig. 5 documents that comparison for the reproduced window.

**Espírito Santo is depressed in 2020–2022.** The article states that dengue and
chikungunya notifications for this state "stopped being reported in SINAN
starting in 2020, as they were transferred to a dedicated system called 'Sistema
de Informação em Saúde e-SUS Vigilância em Saúde (VS)'."

The present data qualifies that statement. ES counts collapse for 2020–2022
(7,011 / 15,498 / 11,159 cases) against 80,668 in 2019, but recover to 138,361 in
2023 and 138,030 in 2024 — consistent with reporting into SINAN having resumed
after the article's analysis window was fixed. Anyone reproducing the article's
figures should treat ES as unusable for 2020–2022; treating it as unusable from
2020 onward, as the article's unqualified wording implies, would now discard two
complete and apparently sound years.

The pipeline applies no correction in either case; this is documented so the
choice sits with the analyst.

**Aggregation is irreversible.** Individual-level detail — age, sex, municipality,
laboratory results — is discarded. Any analysis requiring finer stratification
must return to the source files.

**Chikungunya is not implemented.** The transformation is parameterised by
disease and the structure accommodates chikungunya, but no chikungunya source
files have been obtained, so that path is unexercised and its schema tiers
unverified.
