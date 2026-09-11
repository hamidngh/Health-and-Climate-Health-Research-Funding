"""Strict local I/O and reproducibility metadata (no credentials in manifests)."""
from __future__ import annotations
import ast
import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

class InputError(ValueError):
    """An input is missing, ambiguous, incomplete or inconsistent."""

def require_columns(df: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = set(columns) - set(df.columns)
    if missing:
        raise InputError(f"{label}: missing columns {sorted(missing)}")

def read_table(path: str | Path, *, sheet: str | int = 0) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"Required input not found: {p}. See docs/DATA_DICTIONARY.md.")
    if p.suffix.lower() == '.csv':
        return pd.read_csv(p, low_memory=False)
    if p.suffix.lower() in {'.xlsx', '.xlsm'}:
        return pd.read_excel(p, sheet_name=sheet, engine='openpyxl')
    if p.suffix.lower() == '.jsonl':
        return pd.read_json(p, lines=True)
    raise InputError(f"Unsupported input format: {p.suffix}; use CSV, XLSX or JSONL.")

def write_csv(df: pd.DataFrame, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, float_format='%.17g')

def read_json(path: str | Path) -> Any:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"Required JSON file not found: {p}")
    return json.loads(p.read_text(encoding='utf-8'))

def write_json(value: Any, path: str | Path) -> None:
    def convert(v: Any) -> Any:
        if isinstance(v, dict): return {str(k): convert(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)): return [convert(x) for x in v]
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating, float)): return float(v) if np.isfinite(v) else None
        if isinstance(v, (np.bool_,)): return bool(v)
        if isinstance(v, Path): return str(v)
        return v
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(convert(value), indent=2, ensure_ascii=False, allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(p)

def parse_list(value: Any, *, label: str = 'nested value') -> list:
    if isinstance(value, list): return value
    if value is None or (isinstance(value, float) and np.isnan(value)): return []
    s = str(value).strip()
    if s in {'', 'nan', 'None', 'null', '[]'}: return []
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        try: obj = ast.literal_eval(s)
        except (ValueError, SyntaxError) as exc:
            raise InputError(f"Cannot parse {label}; expected a JSON/Python-literal list.") from exc
    if not isinstance(obj, list):
        raise InputError(f"{label} must be a list, not {type(obj).__name__}.")
    return obj

def numeric(df: pd.DataFrame, cols: list[str], label: str, *, missing_zero: bool = False) -> pd.DataFrame:
    df = df.copy()
    require_columns(df, cols, label)
    for col in cols:
        converted = pd.to_numeric(df[col], errors='coerce')
        bad = converted.isna() & df[col].notna()
        if bad.any(): raise InputError(f"{label}.{col}: {int(bad.sum())} non-numeric values.")
        if missing_zero: converted = converted.fillna(0)
        if converted.isna().any() or not np.isfinite(converted).all():
            raise InputError(f"{label}.{col}: missing/non-finite values are not allowed.")
        if (converted < -1e-7).any(): raise InputError(f"{label}.{col}: negative values.")
        df[col] = converted
    return df

def unique_keys(df: pd.DataFrame, keys: list[str], label: str) -> None:
    require_columns(df, keys, label)
    if df[keys].isna().any().any(): raise InputError(f"{label}: missing key values in {keys}.")
    dup = df.duplicated(keys, keep=False)
    if dup.any():
        raise InputError(f"{label}: {int(dup.sum())} duplicate-key rows in {keys}; resolve duplicate exports, do not sum them.")

def infer_year(df: pd.DataFrame, policy: str = 'legacy_date_first') -> pd.Series:
    """Legacy priority is date column, not row-wise fallback. Fail instead of dropping NaT."""
    if 'start_date' in df:
        year = pd.to_datetime(df['start_date'], errors='coerce').dt.year
        if policy == 'coalesce_start_year' and 'start_year' in df:
            year = year.fillna(pd.to_numeric(df['start_year'], errors='coerce'))
    elif 'start_year' in df: year = pd.to_numeric(df['start_year'], errors='coerce')
    elif 'Derived_Year' in df: year = pd.to_numeric(df['Derived_Year'], errors='coerce')
    elif 'Year' in df: year = pd.to_numeric(df['Year'], errors='coerce')
    else: raise InputError('No grant start date/year field found.')
    if year.isna().any() or ((year % 1) != 0).any():
        raise InputError('Unresolved grant years. Legacy notebooks silently lost these rows. Review them or explicitly choose coalesce_start_year.')
    return year.astype(int)

def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def file_manifest(paths: list[str | Path]) -> list[dict]:
    # Local manifests remain in ignored outputs; only basenames are recorded.
    return [{'name': Path(p).name, 'sha256': sha256(p), 'bytes': Path(p).stat().st_size}
            for p in sorted(set(map(str, paths))) if Path(p).is_file()]

def environment() -> dict:
    versions = {}
    for name in ['pandas', 'numpy', 'matplotlib', 'openpyxl', 'requests', 'python-dotenv']:
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    return {'python': platform.python_version(), 'packages': versions}

def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()
