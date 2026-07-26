"""`arboili` — command-line entry point for every ARBOILI extractor.

Defaults come from `config.yml`; any flag given on the command line wins over
the file. Each subcommand wraps the same `extract()` function the notebook
calls, so the CLI and `data_collection.ipynb` stay in step.

    uv run arboili --list                    # show configured sources
    uv run arboili sinan                     # one source
    uv run arboili sinan --to-year 2023      # override a single setting
    uv run arboili all                       # everything except gt-related
    uv run arboili ebc --query chikungunya   # ad-hoc EBC scrape
    uv run arboili transform sinan           # build the SINAN case series

All extractors are idempotent and resumable, so re-running is always safe.
Transformations are pure functions of the downloaded files, so they are too.
"""

from __future__ import annotations

import argparse
import logging
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..common import ExtractResult
from ..config import Config, as_date, load_config, pick

if TYPE_CHECKING:
    from ..pipeline.sinan import SinanTransformResult

log = logging.getLogger("arboili")

# Sources run by `arboili all`, in pipeline order. gt-related is deliberately
# excluded — it is an ~18-hour job that should be started on its own.
ALL_SOURCES = ["sinan", "gt-search", "climate", "bulletins", "ebc"]

# Mirrors src.pipeline.sinan.SPECS. Spelled out here so building the parser
# doesn't import polars — `arboili --help` should stay instant.
SPEC_NAMES = ("dengue", "chikungunya")


# ---------------------------------------------------------------------------
# Per-source runners
# ---------------------------------------------------------------------------

def run_sinan(cfg: Config, args: Namespace) -> ExtractResult:
    from .epidemiological.sinan_dengue import extract

    settings = cfg.source("sinan")
    return extract(
        out_dir=cfg.resolve(pick(args.output_dir, settings.get("out_dir"),
                                 "data/epidemiological/SINAN")),
        from_year=pick(args.from_year, settings.get("from_year"), 2010),
        to_year=pick(args.to_year, settings.get("to_year"), datetime.now().year),
        keep_zip=args.keep_zip or bool(settings.get("keep_zip", False)),
    )


def run_gt_search(cfg: Config, args: Namespace) -> ExtractResult:
    from .google_trends.extract_gt_search import extract

    settings = cfg.source("gt_search")
    return extract(
        reference_date=as_date(pick(args.reference_date, settings.get("reference_date"))),
        out_dir=cfg.resolve(pick(args.output_dir, settings.get("out_dir"), "data/google_trends")),
        popular_terms_path=cfg.reference("popular_terms"),
        fu_path=cfg.reference("federative_units"),
        sleep=pick(args.sleep, settings.get("sleep"), 2.0),
    )


def run_gt_related(cfg: Config, args: Namespace) -> ExtractResult:
    from .google_trends.extract_gt_related import extract

    settings = cfg.source("gt_related")
    return extract(
        start_month=pick(args.start_month, settings.get("start_month"), "2020-01"),
        out_dir=cfg.resolve(pick(args.output_dir, settings.get("out_dir"), "data/google_trends")),
        fu_path=cfg.reference("federative_units"),
        popular_terms_path=cfg.reference("popular_terms"),
        sleep=pick(args.sleep, settings.get("sleep"), 5.0),
    )


def run_climate(cfg: Config, args: Namespace) -> ExtractResult:
    from .climate.download_inmet_data import extract

    settings = cfg.source("climate")
    return extract(
        out_dir=cfg.resolve(pick(args.output_dir, settings.get("out_dir"), "data/climate")),
        from_year=pick(args.from_year, settings.get("from_year"), 2000),
        to_year=pick(args.to_year, settings.get("to_year"), datetime.now().year),
    )


def run_bulletins(cfg: Config, args: Namespace) -> ExtractResult:
    from .bulletins.download_boletins import extract

    settings = cfg.source("bulletins")
    return extract(
        out_dir=cfg.resolve(pick(args.output_dir, settings.get("out_dir"), "data/bulletins")),
        from_year=pick(args.from_year, settings.get("from_year"), 2019),
        to_year=pick(args.to_year, settings.get("to_year"), datetime.now().year),
    )


def run_ebc(cfg: Config, args: Namespace) -> ExtractResult:
    """Run one scrape per configured query (or a single ad-hoc --query)."""
    from .ebc.scraper import scrape

    settings = cfg.source("ebc")
    base_dir = cfg.resolve(pick(args.output_dir, settings.get("out_dir"), "data/news"))

    if args.query:
        queries = [{"name": args.query, "query": args.query}]
    else:
        queries = settings.get("queries") or []
    if not queries:
        raise SystemExit("No EBC queries configured. Add sources.ebc.queries to "
                         f"{cfg.path}, or pass --query.")

    combined = ExtractResult(downloaded=0, existing=0, failed=0, manifest_path=base_dir)
    for entry in queries:
        term = entry["query"]
        name = entry.get("name", term)
        log.info("EBC scrape: %s", term)
        result = scrape(Namespace(
            query=term,
            output_dir=base_dir / name,
            site=pick(args.site, settings.get("site"), "agenciabrasil"),
            types=pick(args.types, settings.get("types"), ["noticia", "pagina"]),
            per_page=pick(args.per_page, settings.get("per_page"), 100),
            max_pages=pick(args.max_pages, settings.get("max_pages"), 10_000),
            delay=pick(args.delay, settings.get("delay"), 1.0),
            restart=args.restart,
        ))
        combined.downloaded += result.downloaded
        combined.existing += result.existing
        combined.failed += result.failed
        combined.manifest_path = result.manifest_path
    return combined


def run_transform_sinan(cfg: Config, args: Namespace) -> "SinanTransformResult":
    """Aggregate the downloaded SINAN CSVs into the weekly case series."""
    from ..pipeline.sinan import SPECS, transform

    settings = cfg.pipeline("sinan")
    spec = SPECS[pick(args.disease, settings.get("disease"), "dengue")]
    default_dir = "data/epidemiological/SINAN"

    return transform(
        in_dir=cfg.resolve(pick(args.input_dir, settings.get("in_dir"), default_dir)),
        out_dir=cfg.resolve(pick(args.output_dir, settings.get("out_dir"), default_dir)),
        fu_path=cfg.reference("federative_units"),
        spec=spec,
        from_year=pick(args.from_year, settings.get("from_year")),
        to_year=pick(args.to_year, settings.get("to_year")),
        dif_lower=pick(args.dif_lower, settings.get("dif_lower"), -180),
        dif_upper=pick(args.dif_upper, settings.get("dif_upper"), 1),
        formats=pick(args.formats, settings.get("formats"), ["parquet", "csv_gz"]),
    )


RUNNERS = {
    "sinan": run_sinan,
    "gt-search": run_gt_search,
    "gt-related": run_gt_related,
    "climate": run_climate,
    "bulletins": run_bulletins,
    "ebc": run_ebc,
}

# Transformation stages, run after collection. Keyed the same way as RUNNERS so
# `main()` dispatches both through one path.
TRANSFORMS = {
    "sinan": run_transform_sinan,
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_year_range(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from-year", type=int, help="Earliest year to fetch")
    parser.add_argument("--to-year", type=int, help="Latest year to fetch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arboili",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=Path, help="Path to config.yml (default: project root)")
    parser.add_argument("--list", action="store_true", help="List configured sources and exit")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", metavar="SOURCE")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--output-dir", type=Path, help="Override the configured output directory")
        return p

    p_sinan = add("sinan", "SINAN dengue yearly case CSVs")
    _add_year_range(p_sinan)
    p_sinan.add_argument("--keep-zip", action="store_true",
                         help="Keep the downloaded .zip next to the extracted CSV")

    p_gts = add("gt-search", "Google Trends weekly search index (~5 min)")
    p_gts.add_argument("--reference-date", help="End of the 5-year window, YYYY-MM-DD")
    p_gts.add_argument("--sleep", type=float, help="Seconds between requests")

    p_gtr = add("gt-related", "Google Trends related topics & queries (~18 h, resumable)")
    p_gtr.add_argument("--start-month", help="First month to fetch, YYYY-MM")
    p_gtr.add_argument("--sleep", type=float, help="Seconds between requests")

    _add_year_range(add("climate", "INMET annual meteorological ZIPs"))
    _add_year_range(add("bulletins", "Ministry of Health epidemiological bulletin PDFs"))

    p_ebc = add("ebc", "EBC / Agência Brasil news articles")
    p_ebc.add_argument("--query", "-q", help="Ad-hoc search term (skips configured queries)")
    p_ebc.add_argument("--site", help="EBC sub-site slug")
    p_ebc.add_argument("--types", nargs="+", help="Content types to include")
    p_ebc.add_argument("--per-page", type=int, help="Results per listing page")
    p_ebc.add_argument("--max-pages", type=int, help="Hard ceiling on pages to fetch")
    p_ebc.add_argument("--delay", type=float, help="Seconds between HTTP requests")
    p_ebc.add_argument("--restart", action="store_true",
                       help="Wipe state.json and manifest.jsonl before starting")

    p_all = sub.add_parser("all", help=f"Run, in order: {', '.join(ALL_SOURCES)}")
    p_all.add_argument("--output-dir", type=Path, help=argparse.SUPPRESS)

    p_transform = sub.add_parser(
        "transform", help="Turn downloaded data into analysis tables"
    )
    transform_sub = p_transform.add_subparsers(dest="stage", metavar="STAGE")

    p_tf_sinan = transform_sub.add_parser(
        "sinan", help="Weekly SINAN case series by epi week, state and classification"
    )
    p_tf_sinan.add_argument("--input-dir", type=Path, help="Where the yearly CSVs live")
    p_tf_sinan.add_argument("--output-dir", type=Path, help="Where to write the series")
    p_tf_sinan.add_argument("--disease", choices=sorted(SPEC_NAMES),
                            help="Which SINAN database to transform (default: dengue)")
    _add_year_range(p_tf_sinan)
    p_tf_sinan.add_argument("--dif-lower", type=int,
                            help="Lower bound, in days, on (symptom onset - notification)")
    p_tf_sinan.add_argument("--dif-upper", type=int,
                            help="Upper bound, in days, on (symptom onset - notification)")
    p_tf_sinan.add_argument("--formats", nargs="+", choices=["parquet", "csv_gz"],
                            help="Output formats to write")

    return parser


def _defaults_for(command: str, args: Namespace) -> Namespace:
    """Fill in the flags a subcommand doesn't define, so runners can read them all.

    `arboili all` reuses the same runners without their per-source flags; every
    missing flag becomes None, which `pick()` treats as "no override".
    """
    known = {
        "output_dir", "from_year", "to_year", "keep_zip", "reference_date", "sleep",
        "start_month", "query", "site", "types", "per_page", "max_pages", "delay", "restart",
        "input_dir", "disease", "dif_lower", "dif_upper", "formats",
    }
    merged = Namespace(**vars(args))
    for flag in known:
        if not hasattr(merged, flag):
            setattr(merged, flag, False if flag in {"keep_zip", "restart"} else None)
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)

    if args.list:
        print(f"Config: {cfg.path}\n")
        for name in RUNNERS:
            settings = cfg.source(name.replace("-", "_"))
            marker = " " if name in ALL_SOURCES else "*"
            print(f"{marker} {name:<12} {settings or '(no config block)'}")
        print("\n* not included in `arboili all` — long-running, start it separately")
        print("\nTransformations (arboili transform <stage>):")
        for name in TRANSFORMS:
            print(f"  {name:<12} {cfg.pipeline(name) or '(no config block)'}")
        return 0

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "transform":
        stage = getattr(args, "stage", None)
        if not stage:
            print(f"Pick a stage: {', '.join(TRANSFORMS)}", file=sys.stderr)
            return 1
        print(f"\n{'=' * 70}\ntransform {stage}\n{'=' * 70}", flush=True)
        result = TRANSFORMS[stage](cfg, _defaults_for(stage, args))
        print(result, flush=True)
        return 0 if result.failed == 0 else 1

    commands = ALL_SOURCES if args.command == "all" else [args.command]
    failed = 0
    for name in commands:
        print(f"\n{'=' * 70}\n{name}\n{'=' * 70}", flush=True)
        try:
            result = RUNNERS[name](cfg, _defaults_for(name, args))
        except Exception as error:  # keep `all` going when one source is down
            log.error("%s failed: %s", name, error)
            if args.command != "all":
                raise
            failed += 1
            continue
        print(result, flush=True)
        failed += result.failed

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
