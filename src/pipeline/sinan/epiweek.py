"""Map a date onto the first day of its Brazilian epidemiological week.

Isolated in its own module because it is the highest-risk line in the port: an
off-by-one here shifts every observation in the series by up to six days, and
nothing downstream would fail loudly.

Brazilian epidemiological weeks run Sunday to Saturday, and the R pipeline this
replaces used `lubridate::floor_date(x, unit = "week")`, whose default
`week_start = 7` anchors on Sunday. The output is a *date* (the Sunday itself),
not a YYYYWW week number — the raw SINAN `SEM_NOT`/`SEM_PRI` columns hold week
numbers, but the published table keys on dates, so those columns go unused.
"""

from __future__ import annotations

import polars as pl


def floor_to_sunday(column: str) -> pl.Expr:
    """Expression flooring `column` to the Sunday that starts its epi week.

    Do NOT substitute `dt.truncate("1w")`: polars anchors week windows on
    Monday, so it returns a date one day later than R for every input. Verified
    against polars 1.35.1.

    `dt.weekday()` is ISO (Monday=1 ... Sunday=7), so `weekday % 7` is exactly
    the number of days back to the preceding Sunday — and it is 0 for a Sunday,
    which makes the operation idempotent. Nulls propagate, matching R.
    """
    days_since_sunday = pl.col(column).dt.weekday() % 7
    return (pl.col(column) - pl.duration(days=days_since_sunday)).cast(pl.Date)
