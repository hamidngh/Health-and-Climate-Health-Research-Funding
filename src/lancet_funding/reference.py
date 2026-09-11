"""Private historical inputs, imported without publishing them.

The fixed row list is used because the supplied analysis consumes it as exclusions.
Ranking new grants does NOT establish that they should be excluded.
"""
from pathlib import Path
import json
import pandas as pd
from .io import (InputError, read_table, read_json, write_json, write_csv,
                 require_columns, unique_keys, numeric, parse_list, sha256, stamp)
from .cleaning import load_exclusions
from .analysis import GROUPING_ALIASES

EXCLUSION_COLUMNS=['id','year','funding_usd','funder_org_name','funder_org_countries']
GROUP_COLUMNS=['country','lancet_region','who_region','hdi_group']


def _record(cfg, name, source, target, rows, rule):
    status_path=Path(cfg['reference_status'])
    status=read_json(status_path) if status_path.exists() else {}
    status[name]={'status':'imported','rows':int(rows),'csv_sha256':sha256(target),
                  'source_sha256':sha256(source),'imported_utc':stamp(),'selection_rule':rule,
                  'publication':'Private input; never included in the code-only release.'}
    write_json(status,status_path)


def import_exclusions(source, cfg, *, all_rows=False, decision_column=None, exclude_values=None, sheet=0):
    df=read_table(source,sheet=sheet)
    df.columns=[str(c).strip() for c in df.columns]
    if decision_column:
        require_columns(df,[decision_column],'review workbook')
        if not exclude_values:raise InputError('Provide explicit --exclude-values for the decision column.')
        allowed={str(x).strip().lower() for x in exclude_values}
        df=df[df[decision_column].astype(str).str.strip().str.lower().isin(allowed)].copy()
        rule=f'Rows selected by decision column {decision_column}; values '+', '.join(sorted(allowed))
    elif all_rows:
        rule='Use every supplied row as an exclusion, matching the historical analysis. This does not establish how the historical review decisions were made.'
    else:
        raise InputError('Confirm --all-rows-are-exclusions, or specify a decision column and exclusion values. A top-five sample alone is not an exclusion list.')
    if 'year' not in df:
        if 'Year' in df:df=df.rename(columns={'Year':'year'})
        elif 'start_date' in df:df['year']=pd.to_datetime(df['start_date'],errors='coerce').dt.year
        elif 'start_year' in df:df['year']=df['start_year']
    require_columns(df,EXCLUSION_COLUMNS,'historical exclusion-input workbook')
    df=df[EXCLUSION_COLUMNS].copy()
    df['source_row_number']=df.index+2
    df['funding_usd_was_missing']=df['funding_usd'].isna()
    df=df[df['id'].notna()].copy()
    df['id']=df['id'].astype(str).str.strip().str.strip('"\'')
    if df.empty or not df['id'].str.match(r'^grant\.[A-Za-z0-9.]+$').all():
        raise InputError('Exclusions must contain actual, nonempty Dimensions grant IDs.')
    # The supplied historical workbook contains an identical duplicated grant row.
    # Preserve row multiplicity: the original denominator uses nunique(id) but sum(USD).
    distinct=df.drop_duplicates(EXCLUSION_COLUMNS)
    unique_keys(distinct,['id'],'conflicting historical exclusion entries')
    df=numeric(df,['year'],'manual exclusions')
    df=numeric(df,['funding_usd'],'manual exclusions',missing_zero=True)
    if (df['year']%1!=0).any():raise InputError('Exclusion years must be integers.')
    df['year']=df['year'].astype(int)
    if df['funder_org_name'].isna().any():raise InputError('Exclusion funder names are missing.')
    df['funder_org_name']=df['funder_org_name'].astype(str).str.strip()
    for value in df['funder_org_countries']:
        rows=parse_list(value,label='exclusion funder countries')
        if not rows or any(not isinstance(r,dict) or not r.get('name') for r in rows):
            raise InputError('Each exclusion needs a parseable list of funder country names.')
    df['funder_org_countries']=df['funder_org_countries'].map(lambda x:json.dumps(parse_list(x),ensure_ascii=False))
    target=Path(cfg['manual_exclusions']);write_csv(df.sort_values('id'),target)
    _record(cfg,'manual_exclusions',source,target,len(df),rule)
    status=read_json(cfg['reference_status'])
    status['manual_exclusions'].update({'unique_ids':int(df.id.nunique()),
        'duplicate_rows_preserved':int(df.duplicated('id').sum()),
        'missing_funding_rows_summed_as_zero':int(df.funding_usd_was_missing.sum()),
        'duplicate_policy':'Identical repeated source rows retained for historical USD subtraction; counts use unique IDs.'})
    write_json(status,cfg['reference_status'])
    return len(df)


def import_groupings(source, cfg, *, sheet=0):
    df=read_table(source,sheet=sheet)
    df.columns=[str(c).strip() for c in df.columns]
    df=df.rename(columns=GROUPING_ALIASES)
    require_columns(df,GROUP_COLUMNS,'country grouping workbook')
    df=df[GROUP_COLUMNS].dropna(subset=['country']).copy()
    for col in GROUP_COLUMNS:
        df[col]=df[col].fillna('Unmapped').astype(str).str.strip().replace({'':'Unmapped','#N/A':'Unmapped','N/A':'Unmapped'})
    df=df[df['country']!='Unmapped']
    if df.empty:raise InputError('Country grouping file has no country records.')
    unique_keys(df,['country'],'country groupings')
    target=Path(cfg['country_groupings']);write_csv(df.sort_values('country'),target)
    _record(cfg,'country_groupings',source,target,len(df),'Exact country and group labels from maintainer-supplied workbook; no inferred HDI groups.')
    return len(df)


def check_references(cfg):
    status=read_json(cfg['reference_status']) if Path(cfg['reference_status']).exists() else {}
    missing=[]
    for name in ['manual_exclusions','country_groupings']:
        entry=status.get(name,{})
        path=Path(cfg[name])
        if entry.get('status')!='imported' or not path.is_file():
            missing.append(name)
        elif entry.get('csv_sha256')!=sha256(path):
            raise InputError(f'{name} differs from its recorded hash. Re-import the approved source, rather than editing reference CSVs silently.')
    if missing:
        raise InputError('One-time maintainer reference import still required: '+', '.join(missing)+'. See docs/LOCAL_INPUTS.md. These files cannot be recreated from a Dimensions API key.')
    load_exclusions(cfg['manual_exclusions'])
    groups=read_table(cfg['country_groupings'])
    require_columns(groups,GROUP_COLUMNS,'country groupings');unique_keys(groups,['country'],'country groupings')
    return status


def prepare_local_inputs(cfg):
    """One-click setup for the local bundle. Reuse only hash-verified source inputs.

    A change to either workbook requires an explicit re-import, rather than a silent
    change to a running analysis. The historical file and the run/review output have
    different paths; their roles cannot be swapped by overwriting a common filename.
    """
    sources={'manual_exclusions':cfg['historical_exclusions_workbook'],
             'country_groupings':cfg['country_groupings_workbook']}
    status=read_json(cfg['reference_status']) if Path(cfg['reference_status']).exists() else {}
    for name,source in sources.items():
        if not Path(source).is_file():
            raise InputError('Local reference workbook missing: '+str(source)+
                             '. Use the local-run bundle or supply it separately; the public ZIP intentionally contains no data.')
        entry=status.get(name,{})
        if entry.get('status')=='imported':
            if entry.get('source_sha256')!=sha256(source):
                raise InputError('Reference workbook changed: '+name+'. Re-import it explicitly and use a new run directory.')
            continue
        if name=='manual_exclusions':import_exclusions(source,cfg,all_rows=True)
        else:import_groupings(source,cfg)
    return check_references(cfg)
