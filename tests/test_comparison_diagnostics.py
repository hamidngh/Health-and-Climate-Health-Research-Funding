"""Synthetic comparison evidence; diagnostics never force differences to PASS."""
from pathlib import Path
import json
import sys
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import diagnose_comparisons as diagnostic

METRICS = ['health_count', 'climate_count', 'health_funding_usd', 'climate_funding_usd']
COLUMNS = ['scenario_id','year','country','funder_org','metric','baseline','current','difference','status']


def rows(status='PASS', *, year=2024, country='United Kingdom', org='Synthetic Funder', value=1):
    return [dict(scenario_id='14', year=year, country=country, funder_org=org, metric=m,
                 baseline='' if status=='MISSING_BASELINE' else value,
                 current='' if status=='MISSING_CURRENT' else value,
                 difference=0, status=status) for m in METRICS]


def test_status_breakdown_and_zero_key_rows_not_passed():
    source = rows() + rows('MISSING_BASELINE', year=1990, value=0) + rows('FAIL', year=2020, value=20)
    summary, details = diagnostic.read_master_comparison(diagnostic.csv_bytes(source, COLUMNS), 'funder')
    assert summary['tested_cells'] == 12 and summary['cells_not_passing'] == 8
    assert summary['status_counts'] == {'FAIL':4,'MISSING_BASELINE':4,'PASS':4}
    assert summary['unmatched_key_characteristics'] == [
        {'status':'MISSING_BASELINE','characteristic':'all_metrics_exactly_zero','key_rows':1}]
    assert any(r['period']=='outside_2010_2024' for r in details)
    assert {r['scenario_group'] for r in details} == {'main_14'}


def test_nonfinite_recipient_unknown_is_identified_not_passed():
    source = rows(country='Unknown')
    source.append({**source[-1], 'metric':'climate_funding_usd_full'})
    for row in source:
        row.pop('funder_org')
        if row['metric'].startswith('health_'):
            row['baseline']=0; row['current']=''; row['status']='NONFINITE'
    summary, _ = diagnostic.read_master_comparison(
        diagnostic.csv_bytes(source,[c for c in COLUMNS if c!='funder_org']), 'recipient')
    assert summary['Unknown_cell_status_counts'] == {'NONFINITE':2,'PASS':3}
    assert summary['cells_not_passing'] == 2


def test_duplicate_comparison_cells_fail():
    source = rows()
    with pytest.raises(ValueError,match='repeated metric/key'):
        diagnostic.read_master_comparison(diagnostic.csv_bytes(source+source,COLUMNS),'funder')


def test_bands_distinguish_values_from_missing_input():
    source = [
        dict(year=2024, metric='climate_count', bound='min', baseline=1,current=1,status='PASS'),
        dict(year=2024, metric='climate_count', bound='max', baseline=2,current=3,status='DIFFERENCE_OR_MISSING_INPUT'),
        dict(year=2023, metric='climate_count', bound='max', baseline='',current=3,status='DIFFERENCE_OR_MISSING_INPUT'),
    ]
    summary = diagnostic.read_band_comparison(diagnostic.csv_bytes(source,list(source[0])))
    assert summary['diagnostic_counts']=={
        'PASS':1,'FINITE_BOUND_DIFFERENCE':1,'MISSING_NONFINITE_OR_INCOMPLETE_SCENARIO_INPUT':1}


def test_collector_only_writes_private_zip_and_detects_stale_input(tmp_path):
    root=tmp_path/'project'; run=root/'runs/example'
    validation=run/'validation'; validation.mkdir(parents=True)
    (run/'raw').mkdir(); (run/'raw/no_read.jsonl').write_text('raw confidential content')
    (root/'.env').write_text('secret outside collector allowlist')
    (root/'private/baseline').mkdir(parents=True)
    baseline=root/'private/baseline/CLEANED_RAW_FUNDER_DATASET.xlsx'
    baseline.write_bytes(b'opaque baseline bytes: only hashed, not parsed')
    current=run/'results/processed/funder_master.csv'
    current.parent.mkdir(parents=True); current.write_text('changed after comparison')
    comparison=validation/'funder_comparison.csv'
    comparison.write_bytes(diagnostic.csv_bytes(rows(),COLUMNS))
    saved = {'tested_cells':4,'cells_not_passing':0,'status_counts':{'PASS':4},
             'baseline_sha256':diagnostic.digest(baseline),'current_sha256':'0'*64}
    (validation/'funder_comparison_summary.json').write_text(json.dumps(saved))
    protected={p:diagnostic.digest(p) for p in root.rglob('*') if p.is_file()}
    output, overview=diagnostic.collect(run,root=root)
    assert output == validation/'comparison_diagnostics.zip'
    assert overview['underlying_input_hash_checks']['funder']['current_master']['state']=='STALE_OR_DIFFERENT_INPUT'
    assert overview['comparisons']['funder']['counts_match_saved_summary'] is True
    assert all(diagnostic.digest(p)==sha for p,sha in protected.items())
    with zipfile.ZipFile(output) as archive:
        names=archive.namelist()
        assert 'DIAGNOSTIC_OVERVIEW.json' in names
        assert 'CELL_STATUS_BREAKDOWN.csv' in names
        assert 'validation/funder_comparison.csv' in names
        assert not any('.env' in n or n.startswith('raw/') or n.endswith('.xlsx') for n in names)
    # Re-running does not grow the archive recursively or change its inputs.
    diagnostic.collect(run,root=root)
    assert all(diagnostic.digest(p)==sha for p,sha in protected.items())


def test_no_comparisons_requires_step8(tmp_path):
    (tmp_path/'validation').mkdir()
    with pytest.raises(ValueError,match='Step 8'):
        diagnostic.collect(tmp_path,root=tmp_path)
