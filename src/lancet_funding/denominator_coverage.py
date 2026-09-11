"""Explicit missing-denominator coverage, distinct from a measured zero.

The recipient-country facet need not include a bucket for grants whose recipient
country is unknown. Do not infer its denominator by subtracting country totals
from global: grants may have multiple recipient countries.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .io import InputError, numeric, require_columns

HEALTH = ["health_count", "health_funding_usd"]
UNAVAILABLE = "recipient_denominator_unavailable"


def boolean_column(df: pd.DataFrame, column: str) -> pd.Series:
    """Parse CSV-round-tripped Boolean flags; absent mixed-kind flags mean False."""
    if column not in df:
        return pd.Series(False, index=df.index, dtype=bool)
    text = df[column].astype("string").fillna("false").str.strip().str.lower()
    allowed = {"true", "false", "1", "0", "1.0", "0.0"}
    if not text.isin(allowed).all():
        raise InputError(f"Invalid Boolean coverage values in {column}.")
    return text.isin({"true", "1", "1.0"})


def unavailable_mask(df: pd.DataFrame) -> pd.Series:
    return boolean_column(df, UNAVAILABLE)


def validate_master_denominators(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Allow blanks ONLY for an explicitly unmatched recipient Unknown bucket.

    A missing named-country/global row, a genuinely observed zero denominator,
    malformed numbers and negative values are never granted this exception.
    """
    out = df.copy()
    require_columns(out, HEALTH, kind + " master")
    absent = unavailable_mask(out)
    if absent.any():
        if kind != "recipient" or not out.loc[absent, "country"].eq("Unknown").all():
            raise InputError("Unavailable master denominators are allowed only for recipient Unknown rows.")
        require_columns(out, ["denominator_row_present"], "recipient coverage")
        presence = out.loc[absent, "denominator_row_present"]
        if presence.isna().any() or boolean_column(out, "denominator_row_present")[absent].any():
            raise InputError("An observed denominator cannot be marked unavailable.")
        if out.loc[absent, HEALTH].notna().any().any():
            raise InputError("Unavailable Unknown denominators must be blank, not fabricated zeros or totals.")
    # Run the existing strict numerical validator on every observed cell. The
    # temporary zero only permits validation of the missing cells' dtype; it is
    # restored to NaN before the data leave this function.
    check = out.copy()
    check.loc[absent, HEALTH] = 0
    check = numeric(check, HEALTH, kind + " master")
    out[HEALTH] = check[HEALTH]
    if absent.any():
        out.loc[absent, HEALTH] = np.nan
    if UNAVAILABLE in out:
        out[UNAVAILABLE] = absent
    return out


def sum_metric_groups(df: pd.DataFrame, keys: list[str], metrics: list[str]) -> pd.DataFrame:
    """A scope/period requiring an unobserved bucket has no complete denominator.

    Numerators are retained and summed normally. Health denominators are blank,
    not partial sums, whenever a constituent's denominator is unavailable.
    """
    out = df.groupby(keys, dropna=False)[metrics].sum(min_count=1)
    flags = df[keys].copy()
    flags[UNAVAILABLE] = unavailable_mask(df)
    missing = flags.groupby(keys, dropna=False)[UNAVAILABLE].any()
    out[UNAVAILABLE] = missing.reindex(out.index, fill_value=False)
    for column in HEALTH:
        if column in out:
            out.loc[out[UNAVAILABLE], column] = np.nan
    return out.reset_index()
