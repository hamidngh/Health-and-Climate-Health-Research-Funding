#!/usr/bin/env python3
"""Collect PRIVATE comparison evidence without rerunning analysis or calling an API.

Reads an explicit allowlist of existing aggregate comparison/result files. Writes
only validation/comparison_diagnostics.zip, using an atomic replacement. It does
not read .env, grant-level raw records or workbook contents. Baseline workbooks
and current comparison inputs are hashed, when present, solely to detect stale
comparison reports. The ZIP contains unpublished aggregate values: NOT for GitHub.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'PASS', 'FAIL', 'NONFINITE', 'MISSING_BASELINE', 'MISSING_CURRENT'}
METRICS = {'health_count', 'climate_count', 'health_funding_usd', 'climate_funding_usd'}
INPUTS = (
    'validation/funder_comparison.csv',
    'validation/recipient_comparison.csv',
    'validation/global_band_comparison.csv',
    'validation/funder_comparison_summary.json',
    'validation/recipient_comparison_summary.json',
    'validation/global_band_comparison_summary.json',
    'results/tables/main_global.csv',
    'results/tables/global_bands.csv',
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def number(value: str | None) -> float:
    if value is None or not str(value).strip():
        return float('nan')
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError('A comparison value is not numeric. Regenerate Step 8 first.') from exc


def scenario(value: str) -> str:
    v = number(value)
    if not math.isfinite(v) or not v.is_integer() or int(v) not in (1,2,3,4,5,6,7,11,12,13,14):
        raise ValueError('Expected canonical retained scenario IDs in the comparison CSV.')
    return f'{int(v):02d}'


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def geography(country: str) -> str:
    # Diagnostic strata only: these labels are NOT used to merge or change data.
    if country == 'GLOBAL_TOTAL':
        return 'GLOBAL_TOTAL'
    if country == 'Unknown':
        return 'Unknown'
    return 'named_country'


def read_master_comparison(data: bytes, kind: str) -> tuple[dict, list[dict]]:
    metrics = METRICS | ({'climate_funding_usd_full'} if kind == 'recipient' else set())
    keys = ['scenario_id', 'year', 'country'] + (['funder_org'] if kind == 'funder' else [])
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    required = set(keys) | {'metric', 'baseline', 'current', 'status'}
    if not required <= set(reader.fieldnames or []):
        raise ValueError(f'{kind}: comparison CSV is missing required columns.')
    counts = Counter()
    detail = Counter()
    unknown_counts = Counter()
    seen = set()
    years = {'baseline': set(), 'current': set()}
    extra_keys: dict[tuple, list] = {}
    for row in reader:
        s = scenario(row['scenario_id'])
        yr = number(row['year'])
        if not math.isfinite(yr) or not yr.is_integer():
            raise ValueError(f'{kind}: invalid year in comparison.')
        yr = int(yr)
        status, metric = row['status'], row['metric']
        if status not in STATUSES or metric not in metrics:
            raise ValueError(f'{kind}: unknown comparison status or metric; no interpretation was guessed.')
        key = (s, yr, row['country']) + ((row['funder_org'],) if kind == 'funder' else ())
        unique = key + (metric,)
        if unique in seen:
            raise ValueError(f'{kind}: repeated metric/key in comparison CSV. Do not sum duplicate comparisons.')
        seen.add(unique)
        counts[status] += 1
        group = 'main_14' if s == '14' else 'alternatives'
        geo = geography(row['country'])
        window = '2010_2024' if 2010 <= yr <= 2024 else 'outside_2010_2024'
        detail[(s, group, yr, window, geo, metric, status)] += 1
        if geo == 'Unknown':
            unknown_counts[status] += 1
        if status != 'MISSING_BASELINE':
            years['baseline'].add(yr)
        if status != 'MISSING_CURRENT':
            years['current'].add(yr)
        old, new = number(row['baseline']), number(row['current'])
        if status in {'MISSING_BASELINE', 'MISSING_CURRENT'}:
            side_value = new if status == 'MISSING_BASELINE' else old
            extra = extra_keys.setdefault((status,) + key, [set(), True, True])
            extra[0].add(metric)
            extra[1] = extra[1] and math.isfinite(side_value)
            extra[2] = extra[2] and math.isfinite(side_value) and side_value == 0
    if not counts:
        raise ValueError(f'{kind}: comparison CSV is empty.')
    zero_padding = Counter()
    for key, (found, finite, zero) in extra_keys.items():
        if found != metrics:
            label = 'incomplete_metric_set'
        elif not finite:
            label = 'contains_nonfinite_metric'
        elif zero:
            label = 'all_metrics_exactly_zero'
        else:
            label = 'at_least_one_nonzero_metric'
        zero_padding[(key[0], label)] += 1
    summary = {
        'tested_cells': sum(counts.values()),
        'cells_not_passing': sum(n for status, n in counts.items() if status != 'PASS'),
        'status_counts': dict(sorted(counts.items())),
        'Unknown_cell_status_counts': dict(sorted(unknown_counts.items())),
        'years_present_in_file_rows': {side: sorted(v) for side, v in years.items()},
        'unmatched_key_characteristics': [
            {'status': status, 'characteristic': label, 'key_rows': n}
            for (status, label), n in sorted(zero_padding.items())
        ],
        'note': 'Counts above are metric cells or explicitly labelled key rows, NOT grant counts. '
                'An all-zero unmatched row is diagnosed but is not automatically changed to PASS.',
    }
    rows = [dict(kind=kind, scenario_id=s, scenario_group=group, year=year,
                 period=window, country_class=geo, metric=metric, status=status, cells=n)
            for (s, group, year, window, geo, metric, status), n in sorted(detail.items())]
    return summary, rows


def read_band_comparison(data: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    if not {'year', 'metric', 'bound', 'baseline', 'current', 'status'} <= set(reader.fieldnames or []):
        raise ValueError('Band comparison CSV is missing required columns.')
    counts = Counter()
    seen = set()
    for row in reader:
        key = (row['year'], row['metric'], row['bound'])
        if key in seen:
            raise ValueError('Repeated year/metric/bound in band comparison.')
        seen.add(key)
        old, new = number(row['baseline']), number(row['current'])
        if row['status'] == 'PASS':
            category = 'PASS'
        elif math.isfinite(old) and math.isfinite(new):
            category = 'FINITE_BOUND_DIFFERENCE'
        else:
            category = 'MISSING_NONFINITE_OR_INCOMPLETE_SCENARIO_INPUT'
        counts[category] += 1
    return {'tested_bound_cells': sum(counts.values()), 'diagnostic_counts': dict(sorted(counts.items())),
            'note': 'The original band routine combines numerical differences and missing/invalid inputs '
                    'under one failure label. This breakdown does not alter its status or tolerance.'}


def selected_json(path: Path, keys: tuple[str, ...]) -> dict:
    if not path.is_file():
        return {'availability': 'not_found'}
    source = json.loads(path.read_text(encoding='utf-8'))
    return {key: source[key] for key in keys if key in source}


def hash_check(path: Path, expected: str | None) -> dict:
    if not expected:
        return {'state': 'no_hash_in_comparison_summary'}
    if not path.is_file():
        return {'state': 'file_not_found_at_expected_location'}
    actual = digest(path)
    return {'state': 'matches' if actual == expected else 'STALE_OR_DIFFERENT_INPUT',
            'recorded_sha256': expected, 'present_sha256': actual}


def collect(run_dir: Path, *, root: Path = ROOT) -> tuple[Path, dict]:
    run = run_dir.expanduser().resolve()
    if not (run / 'validation').is_dir():
        raise ValueError('No validation directory in this run. Run notebook Step 8 first.')
    blobs: dict[str, bytes] = {}
    missing = []
    for rel in INPUTS:
        source = run / rel
        if not source.is_file():
            missing.append(rel)
            continue
        if source.is_symlink():
            raise ValueError(f'Refusing a symlinked diagnostic input: {rel}')
        blobs[rel] = source.read_bytes()
    if not any('validation/' + kind + '_comparison.csv' in blobs for kind in ('funder', 'recipient')):
        raise ValueError('No complete master comparison CSV found. Run notebook Step 8 first.')
    overview = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'privacy': 'PRIVATE diagnostic bundle; aggregate research results, NOT for GitHub.',
        'no_api_calls': True,
        'no_analysis_or_exclusion_changes': True,
        'missing_optional_files': missing,
        'analysis_metadata': selected_json(run / 'results/run_manifest.json',
             ('completed_utc', 'software_version', 'data_label', 'main_scenario', 'scenarios',
              'historical_report_reproduction_certified', 'source_snapshot_sha256')),
        'extraction_metadata': selected_json(run / 'raw/COMPLETE.json', ('completed_utc', 'status')),
        'comparisons': {},
        'underlying_input_hash_checks': {},
    }
    detail_rows = []
    baseline_names = {'funder': 'CLEANED_RAW_FUNDER_DATASET.xlsx', 'recipient': 'CLEANED_MERGED_MASTER.xlsx'}
    for kind in ('funder', 'recipient'):
        rel = f'validation/{kind}_comparison.csv'
        if rel not in blobs:
            continue
        summary, rows = read_master_comparison(blobs[rel], kind)
        detail_rows.extend(rows)
        overview['comparisons'][kind] = summary
        summary_rel = f'validation/{kind}_comparison_summary.json'
        original = json.loads(blobs[summary_rel].decode('utf-8')) if summary_rel in blobs else {}
        summary['counts_match_saved_summary'] = (
            original.get('tested_cells') == summary['tested_cells']
            and original.get('cells_not_passing') == summary['cells_not_passing']
            and original.get('status_counts') == summary['status_counts']
        ) if original else None
        overview['underlying_input_hash_checks'][kind] = {
            'current_master': hash_check(run / f'results/processed/{kind}_master.csv', original.get('current_sha256')),
            'baseline_at_notebook_default_location': hash_check(root / 'private/baseline' / baseline_names[kind], original.get('baseline_sha256')),
        }
    rel = 'validation/global_band_comparison.csv'
    if rel in blobs:
        overview['comparisons']['bands'] = read_band_comparison(blobs[rel])
        original = json.loads(blobs.get('validation/global_band_comparison_summary.json', b'{}').decode('utf-8'))
        overview['underlying_input_hash_checks']['bands'] = {
            'current_bands': hash_check(run / 'results/tables/global_bands.csv', original.get('current_sha256')),
            'baseline_at_notebook_default_location': hash_check(root / 'private/baseline/CLEANED_PUBLICATION_DATASET.xlsx', original.get('baseline_sha256')),
        }
    overview['included_file_fingerprints'] = [
        {'path_relative_to_run': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for rel, data in sorted(blobs.items())
    ]
    blobs['DIAGNOSTIC_OVERVIEW.json'] = json_bytes(overview)
    columns = ['kind', 'scenario_id', 'scenario_group', 'year', 'period', 'country_class', 'metric', 'status', 'cells']
    blobs['CELL_STATUS_BREAKDOWN.csv'] = csv_bytes(detail_rows, columns)
    blobs['READ_ME_FIRST.md'] = b'''# Private historical-comparison diagnostics\n\nThis ZIP is for review of aggregate numerical discrepancies, NOT for GitHub.\nIt contains no API key, .env, raw grant export or baseline workbook.\nThe collector makes no API calls and changes no analysis, tolerance or exclusions.\n\nRead DIAGNOSTIC_OVERVIEW.json and CELL_STATUS_BREAKDOWN.csv, then the full\ncomparison CSVs. Source comparison statuses are unchanged. PASS is a matched\nfinite numeric cell within the original tolerance; FAIL is a matched finite\nnumeric difference; MISSING_BASELINE/MISSING_CURRENT are unmatched keys;\nNONFINITE is an uncomparable matched metric (including missing values).\nBand failures are separated into finite differences versus nonfinite/incomplete\ninputs. The report-window stratum is 2010-2024; the other stratum includes ALL\nother comparison years, not just years outside the configured extraction.\n\nAll-zero extra keys may be explicit zero padding, but are not declared equivalent\nto omitted source rows. Unknown-recipient cells are identified, not dropped.\nA STALE_OR_DIFFERENT_INPUT hash check means Step 8 must be rerun against the\nintended files before interpreting that comparison. Different baseline/current\nhashes alone do not measure numerical discrepancy (e.g. XLSX versus CSV).\n\nThe collector deliberately does not infer the scientific cause of a discrepancy,\nmerge country/funder aliases, revise grant selection or force a passing result.\n'''
    target = run / 'validation/comparison_diagnostics.zip'
    fd, temp = tempfile.mkstemp(prefix='.comparison_diagnostics_', suffix='.tmp', dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for rel, data in sorted(blobs.items()):
                archive.writestr(rel, data)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return target, overview


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    path, overview = collect(args.run_dir)
    print('No API calls. Analysis, exclusions, raw data and original comparison reports are unchanged.')
    for kind, summary in overview['comparisons'].items():
        print(kind + ':', json.dumps(summary.get('status_counts', summary.get('diagnostic_counts', {})), sort_keys=True))
        for row in summary.get('unmatched_key_characteristics', []):
            print(' ', row['status'], row['characteristic'] + ':', row['key_rows'], 'key rows')
    stale = any(check.get('state') == 'STALE_OR_DIFFERENT_INPUT'
                for kind in overview['underlying_input_hash_checks'].values() for check in kind.values())
    if stale:
        print('WARNING: at least one underlying input changed after comparison. Rerun Step 8 against the intended files.')
    print('PRIVATE diagnostic ZIP (not for GitHub):', path)


if __name__ == '__main__':
    main()
