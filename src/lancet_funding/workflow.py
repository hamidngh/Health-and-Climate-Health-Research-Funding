"""Notebook-first API extraction through main indicator, bands, tables and figures."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
from . import __version__
from .io import (InputError, read_json, read_table, write_json, write_csv, sha256,
                 stamp, environment, file_manifest, parse_list)
from .dimensions import extract, Client, FIELDS
from .queries import resource
from .scenarios import MAIN, REPORT_IDS, BY_ID
from .cleaning import (rebuild, normalise_cleaned, METRICS, FUNDER_ALIASES,
                       RECIPIENT_ALIASES, recipient_funder, recipient_countries)
from .analysis import (attach_geography, check_master, scopes, headline_values,
                       equity_tables, active_funders, funder_coverage, add_ratios, methodological_bands, coverage_scopes)
from .reference import check_references
from .excel import export_pair, export_xlsx
from .denominator_coverage import HEALTH, UNAVAILABLE, unavailable_mask, sum_metric_groups

ROOT = Path(__file__).resolve().parents[2]


def load_config(path=None):
    p=Path(path or ROOT/'config/pipeline.json').resolve()
    cfg=read_json(p)
    if tuple(cfg.get('scenarios',[]))!=REPORT_IDS or cfg.get('include_recipient') is not True:
        raise InputError('This pipeline requires the eleven retained approaches (01-07, 11-14) and both attribution branches.')
    if cfg.get('search_profile')!='historical_notebook':
        raise InputError('This release preserves the selected historical executable keyword specification.')
    lo,hi=int(cfg['start_year']),int(cfg['end_year'])
    if cfg.get('band_policy') not in {'historical_all_11','alternatives_only'}:raise InputError('Invalid band_policy.')
    if cfg.get('band_zero_denominator') not in {'historical_zero','undefined'}:raise InputError('Invalid band_zero_denominator.')
    for a,b in [('analysis_start','analysis_end'),('main_figure_start','main_figure_end')]:
        if not lo<=int(cfg[a])<=int(cfg[b])<=hi:raise InputError(f'Invalid {a}/{b} range.')
    if not lo<=cfg['active_funder_year']<=hi:raise InputError('Cohort year must be in the extraction range.')
    for name in ['manual_exclusions','country_groupings','reference_status','historical_exclusions_workbook','country_groupings_workbook']:
        value=Path(cfg[name]);cfg[name]=str(value if value.is_absolute() else ROOT/value)
    return cfg


def _extraction_signature(cfg):
    keys=['start_year','end_year','scenarios','search_profile','include_recipient']
    code=Path(__file__).parent
    return {'settings':{k:cfg[k] for k in keys},'method_resources':{
        name:sha256(code/'resources'/name) for name in ['filters.json','keywords_historical.json']},
        'extractor_code':{name:sha256(code/name) for name in ['dimensions.py','queries.py','http.py','scenarios.py']}}


def _verify_outputs(manifest, base):
    for row in manifest.get('files',[]):
        p=base/row['path']
        if not p.is_file() or sha256(p)!=row['sha256']:
            raise InputError('Completed extraction was altered or a file is missing: '+row['path'])


def extract_stage(cfg, run_dir, *, client=None):
    run_dir=Path(run_dir).resolve();raw=run_dir/'raw';raw.mkdir(parents=True,exist_ok=True)
    signature=_extraction_signature(cfg)
    request=raw/'request.json'
    if request.exists():
        if read_json(request)['signature']!=signature:
            raise InputError('The requested extraction differs from this run. Use a new --run-dir; do not mix snapshots or query specifications.')
    else:write_json({'started_utc':stamp(),'signature':signature},request)
    complete=raw/'COMPLETE.json'
    if complete.exists():
        _verify_outputs(read_json(complete),raw)
        print('Reusing the hash-verified completed extraction. No new API queries.',flush=True)
        return raw
    if not (raw/'extraction_manifest.json').exists():
        work=dict(cfg);work['output_dir']=str(raw)
        extract(work,client=client)
    else:
        # An interruption during presentation exports is resumable without redownloading.
        em=read_json(raw/'extraction_manifest.json')
        for row in em['files']:
            matches=list(raw.rglob(row['name']))
            if len(matches)!=1 or sha256(matches[0])!=row['sha256']:
                raise InputError('Interrupted extraction CSVs do not match their manifest; use a fresh run directory.')
    for p in sorted(raw.glob('*.csv'))+sorted((raw/'numerators').glob('*.csv')):
        export_xlsx({'data':read_table(p)},p.with_suffix('.xlsx'))
    nested=['funder_orgs','funder_org_countries','research_org_countries','research_orgs',
            'category_for_2020','category_uoa','category_hrcs_hc','category_hrcs_rac']
    for path in sorted((raw/'numerators').glob('*.csv')):
        grants=read_table(path)
        with path.with_suffix('.jsonl').open('w',encoding='utf-8') as f:
            for row in grants.to_dict('records'):
                for key in nested:row[key]=parse_list(row.get(key),label=key)
                for key,value in row.items():
                    if isinstance(value,float) and not np.isfinite(value):row[key]=None
                f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    artifacts=[p for p in raw.rglob('*') if p.is_file() and 'cache' not in p.parts
               and p.name not in {'COMPLETE.json','query_log.jsonl'} and not p.name.endswith('.tmp')]
    write_json({'completed_utc':stamp(),'status':'complete_extraction_not_historical_validation',
                'files':[{'path':str(p.relative_to(raw)),'sha256':sha256(p)} for p in artifacts]},complete)
    return raw


def _complete_years(series, lo, hi):
    """Zero completed empty cells only after hash-verified successful extraction."""
    frames=[]
    metrics=METRICS+(['climate_funding_usd_full'] if 'climate_funding_usd_full' in series else [])
    index=pd.MultiIndex.from_product([REPORT_IDS,range(lo,hi+1)],names=['scenario_id','year'])
    for (kind,system,scope),group in series.groupby(['kind','scope_system','scope']):
        original=group.set_index(['scenario_id','year'])
        inserted=~index.isin(original.index)
        x=original.reindex(index)
        for col in metrics:
            if col in x:x.loc[inserted,col]=0.0
        if UNAVAILABLE not in x:x[UNAVAILABLE]=False
        # New empty years are zero numerator, but a scope requiring an unobserved
        # recipient bucket still has no comparable health denominator.
        x[UNAVAILABLE]=x[UNAVAILABLE].astype("boolean").fillna(bool(unavailable_mask(group).any())).astype(bool)
        x.loc[unavailable_mask(x),HEALTH]=np.nan
        x['kind']=kind;x['scope_system']=system;x['scope']=scope
        frames.append(add_ratios(x.reset_index(),kind))
    if not frames:raise InputError('No geographic series available.')
    return pd.concat(frames,ignore_index=True)


def _legacy(df, kind):
    mapping=FUNDER_ALIASES if kind=='funder' else RECIPIENT_ALIASES
    out=df.rename(columns={v:k for k,v in mapping.items()}).copy()
    out['Scenario']=out['Scenario'].map(lambda v:BY_ID[str(v)].label)
    return out



def publication_legacy(series,kind):
    out=_legacy(series,kind)
    def label(r):
        if r['scope_system']=='Global':return 'Global'
        if r['scope_system']=='Country':return r['scope']
        return {'WHO':'WHO_','Lancet':'Lancet_','HDI':'HDI_','RoW':'RoW_'}[r['scope_system']]+r['scope']
    out['Geo_Scope']=out.apply(label,axis=1)
    out['Ratio_Count']=out['count_share_legacy_zero']
    out['Ratio_Funding']=out['funding_share_legacy_zero']
    return out


def _exclusion_audit(grants, exclusions):
    fields=['id','funding_usd','start_date','start_year']
    current=grants.loc[grants.scenario_id.eq(MAIN),[c for c in fields if c in grants]].copy()
    current=current.rename(columns={c:'current_'+c for c in current if c!='id'})
    x=exclusions.merge(current,on='id',how='left',indicator=True,validate='many_to_one')
    x['historical_id_repeated']=x.id.duplicated(keep=False)
    x['found_in_current_numerator']=x['_merge'].eq('both')
    x['current_year']=pd.to_datetime(x.get('current_start_date'),errors='coerce').dt.year
    x['year_matches']=x['current_year']==x['year']
    x['current_minus_reviewed_usd']=pd.to_numeric(x.get('current_funding_usd'),errors='coerce')-x['funding_usd']
    return x.drop(columns='_merge')


def _recipient_types(grants, exclusions):
    bad=resource('filters.json')['excluded_funders'];excluded=set(exclusions['id'].astype(str));rows=[]
    for r in grants[grants.scenario_id.eq(MAIN)].to_dict('records'):
        if str(r['id']) in excluded or r['funding_usd']<=0 or recipient_funder(r) in bad:continue
        orgs=parse_list(r.get('funder_orgs'))
        types=(orgs[0].get('types') or []) if orgs else []
        rows.append({'year':r['year'],'funder_type':types[0] if types else 'Unclassified',
                     'id':r['id'],'amount':r['funding_usd']})
    if not rows:return pd.DataFrame(columns=['year','funder_type','climate_count','climate_funding_usd'])
    return pd.DataFrame(rows).groupby(['year','funder_type']).agg(
        climate_count=('id','nunique'),climate_funding_usd=('amount','sum')).reset_index()



def pin_inputs(cfg,run):
    target=run/'inputs';target.mkdir(parents=True,exist_ok=True)
    for key in ['manual_exclusions','country_groupings','reference_status']:
        src=Path(cfg[key]);dst=target/(key+src.suffix)
        if dst.exists():
            if sha256(dst)!=sha256(src):raise InputError('A reference input changed for this run: '+key+'. Use a new run directory.')
        else:shutil.copyfile(src,dst)


def analyse_stage(cfg, run_dir, *, figures=True, data_label='Dimensions live extract'):
    status=check_references(cfg)
    run_dir=Path(run_dir).resolve();raw=run_dir/'raw'
    if not (raw/'COMPLETE.json').exists():raise InputError('Run the extraction stage first; a complete extraction is required.')
    _verify_outputs(read_json(raw/'COMPLETE.json'),raw)
    if read_json(raw/'request.json')['signature']!=_extraction_signature(cfg):
        raise InputError('Extraction settings/code differ from this configuration. Restore the original configuration or start a new run.')
    pin_inputs(cfg,run_dir)
    from .review import generate_review
    review=generate_review(cfg,run_dir)
    print('Stage 3: applying the fixed historical row list; the new top-five sample is NOT excluded',flush=True)
    output=run_dir/'results'
    # Only this program-owned results directory is replaced; raw snapshots are retained.
    if output.exists():shutil.rmtree(output)
    output.mkdir(parents=True)
    work={'scenario_manifest':str(raw/'scenario_manifest.csv'),
          'manual_exclusions':cfg['manual_exclusions'],'funder_denominator':str(raw/'Denominator_Funder_Level.csv'),
          'recipient_denominator':str(raw/'Denominator_Matrix_Country_Level.csv'),
          'denominator_classification_exclusions_applied':True,
          'recipient_denominator_excludes_named_funders':True,
          'year_policy':cfg.get('year_policy','legacy_date_first'),'output_dir':str(output/'processed')}
    rebuilt,inputs=rebuild(work)
    mapping=read_table(cfg['country_groupings'])
    qa={'preparation':rebuilt['quality'],'reference_status':status,
        'historical_report_reproduction_certified':False,
        'zero_denominators':'Canonical ratios for observed zero denominators are missing; the explicit historical band variant may use zero. Unavailable recipient denominators remain blank in all variants.',
        'cohort_definition':'Positive health or climate metric in any of the eleven retained approaches at the endpoint; not a balanced panel.',
        'excluded_outputs':'The independent 169-health-funder comparator requires its separately curated roster or MA_vs_SF.xlsx; neither was supplied. It is not inferred from the API or confused with the endpoint-funder check.'}
    tables={};masters={};all_series=[];equity=[];missing=[]
    for kind in ['funder','recipient']:
        df=normalise_cleaned(read_table(rebuilt[kind+'_master']),kind)
        if not df.year.between(cfg['start_year'],cfg['end_year']).all():
            raise InputError(kind+': cleaned years fall outside the extraction window; inspect start_date/start_year and the fixed historical list. No out-of-range records were silently discarded.')
        df,miss=attach_geography(df,mapping);miss['kind']=kind;missing.append(miss)
        qa[kind]=check_master(df,kind);masters[kind]=df
        if kind=='recipient':
            absent=unavailable_mask(df)
            tables['recipient_denominator_coverage']=df.loc[absent].copy()
            if absent.any():
                populated=int((absent & df.climate_count.gt(0)).sum())
                print(f'Recipient coverage: {populated} numerator-bearing Unknown cells retained; '
                      'their unobserved health denominators and shares are blank. '
                      'Named-country and global validation remains active.',flush=True)
        export_pair(df,output/f'processed/{kind}_master')
        legacy=_legacy(df,kind)
        name='CLEANED_RAW_FUNDER_DATASET' if kind=='funder' else 'CLEANED_MERGED_MASTER'
        export_pair(legacy,output/f'{kind}/{name}')
        series=_complete_years(scopes(df,kind),cfg['start_year'],cfg['end_year'])
        all_series.append(series)
        export_pair(publication_legacy(series,kind),output/f'{kind}/CLEANED_PUBLICATION_DATASET')
        window=series[series['year'].between(cfg['analysis_start'],cfg['analysis_end'])]
        tables[kind+'_regions']=window[window['scope_system'].isin(['Global','WHO','Lancet','HDI'])]
        tables[kind+'_countries']=window[window['scope_system']=='Country']
        for label,part in [('TABLE_3_S14_Time_Series_by_Region',tables[kind+'_regions'])]:
            export_pair(part[part.scenario_id.eq(MAIN)],output/f'{kind}/{label}')
        equity.append(equity_tables(df,kind,cfg['equity_periods']))
    print('Stage 4: generating annual, regional, HDI, funder-type and robustness tables',flush=True)
    annual=pd.concat(all_series,ignore_index=True)
    tables['annual_all_scopes']=annual
    tables['all_global']=annual[annual['scope_system']=='Global']
    tables['main_global']=tables['all_global'][tables['all_global'].scenario_id.eq(MAIN)]
    bands=methodological_bands(annual,policy=cfg['band_policy'],zero_policy=cfg['band_zero_denominator'])
    tables['methodological_bands']=bands
    tables['global_bands']=bands[bands.scope_system.eq('Global')]
    tables['scenario_definitions']=pd.DataFrame([s.__dict__ for s in BY_ID.values()])
    tables['headline_values']=headline_values(annual)
    tables['equity_shares']=pd.concat(equity,ignore_index=True)
    tables['unmapped_countries']=pd.concat(missing,ignore_index=True)
    qa['unmapped_country_rows']=len(tables['unmapped_countries'])
    f=masters['funder']
    tables['funder_types']=f[f.scenario_id.eq(MAIN)].groupby(['year','funder_type'])[METRICS].sum().reset_index()
    from .cleaning import load_exclusions
    exclusions=load_exclusions(cfg['manual_exclusions'])
    tables['exclusion_reconciliation']=_exclusion_audit(rebuilt['raw_numerators'],exclusions)
    deduction_rows=[]
    for row in exclusions.to_dict('records'):
        for country in parse_list(row['funder_org_countries']):
            if isinstance(country,dict) and country.get('name'):
                deduction_rows.append({'id':row['id'],'year':row['year'],'country':country['name'],
                    'funder_org':row['funder_org_name'],'funding_usd':row['funding_usd']})
    deduction_frame=pd.DataFrame(deduction_rows)
    dk=['year','country','funder_org']
    historical=deduction_frame.groupby(dk).agg(source_rows=('id','size'),unique_ids=('id','nunique'),
        historical_funding_subtraction=('funding_usd','sum'))
    deduplicated=deduction_frame.drop_duplicates(['id']+dk).groupby(dk).funding_usd.sum().rename('id_deduplicated_funding_subtraction')
    table=historical.join(deduplicated)
    table['extra_subtraction_from_duplicate_rows']=table.historical_funding_subtraction-table.id_deduplicated_funding_subtraction
    table['applied_policy']='historical row-sum; the deduplicated alternative is diagnostic only'
    tables['historical_deduction_audit']=table.reset_index()
    tables['omitted_missing_start_date']=pd.DataFrame(rebuilt['omitted_missing_start_date'],columns=['id','scenario_id','start_date','start_year','reason'])
    qa['numerator_records_omitted_missing_start_date']=len(tables['omitted_missing_start_date'])
    tables['recipient_types']=_recipient_types(rebuilt['raw_numerators'],exclusions)
    export_pair(tables['funder_types'],output/'funder/TABLE_4_S14_Time_Series_by_Funder_Type')
    export_pair(tables['recipient_types'],output/'recipient/TABLE_4_S14_Time_Series_by_Funder_Type')
    r=masters['recipient']
    export_pair(_legacy(r[['scenario_id','year','country','climate_count','climate_funding_usd','climate_funding_usd_full']], 'recipient'),
                output/'recipient/CLEANED_CLIMATE_NUMERATOR')
    export_pair(_legacy(r[['scenario_id','year','country','health_count','health_funding_usd']], 'recipient'),
                output/'recipient/CLEANED_HEALTH_DENOMINATOR')
    tables['funder_coverage']=funder_coverage(f)
    tables['funder_coverage_scopes']=coverage_scopes(f,year_start=cfg['start_year'],year_end=cfg['end_year'])
    tables['funder_coverage_bands']=methodological_bands(tables['funder_coverage_scopes'],policy=cfg['band_policy'],zero_policy=cfg['band_zero_denominator'],metrics=['health_funder_count','climate_funder_count','funder_share'])
    subset,roster=active_funders(f,cfg['active_funder_year'])
    cohort=_complete_years(scopes(subset,'funder'),cfg['start_year'],cfg['end_year'])
    tables['active_funder_annual']=cohort;tables['active_funder_roster']=roster
    tables['active_funder_bands']=methodological_bands(cohort,policy=cfg['band_policy'],zero_policy=cfg['band_zero_denominator'])
    qa['active_funder_count']=len(roster)
    # Summed amounts for explicit periods, never interpreted as annual changes.
    period_rows=[]
    for period in cfg['equity_periods']:
        sub=annual[annual['year'].between(period['start'],period['end'])]
        p=sum_metric_groups(sub,['kind','scenario_id','scope_system','scope'],METRICS)
        p['period']=period['label'];p['start_year']=period['start'];p['end_year']=period['end']
        period_rows.append(p)
    tables['period_totals']=pd.concat(period_rows,ignore_index=True)
    us=f[(f['mapped_country']=='United States of America')&f.scenario_id.eq(MAIN)]
    top=us.groupby('funder_org')[METRICS].sum().reset_index().sort_values('climate_funding_usd',ascending=False).head(10)
    tables['top_10_us_funders']=top
    for name,df in tables.items():export_pair(df,output/'tables'/name,status=data_label+'; historical equality not certified')
    export_xlsx(tables,output/'ALL_TABLES.xlsx',status=data_label+'; historical equality not certified')
    # Review outputs are written separately before cleaning; they never modify the input list.
    qa['manual_exclusions_absent_from_current_numerator']=int((~tables['exclusion_reconciliation']['found_in_current_numerator']).sum())
    qa['manual_exclusions_with_changed_amount']=int(tables['exclusion_reconciliation']['current_minus_reviewed_usd'].fillna(0).abs().gt(0.01).sum())
    if figures:
        print('Stage 5: rendering main-approach figures, envelopes and scenario comparisons',flush=True)
        from .plots import all_figures
        qa['figure_panels']=all_figures(annual,cohort,tables,output/'figures',cfg,synthetic=data_label=='synthetic')
    write_json(qa,output/'quality_report.json')
    summary=['# Local main-approach and methodological-band run','',f'Data label: {data_label}',
             '','Extraction and analysis completed. Historical numerical equality has not been certified.',
             '','Read tables/main_global.csv and tables/headline_values.csv for numerators, denominators, shares and growth.',
             'Read tables/exclusion_reconciliation.csv and tables/unmapped_countries.csv before interpreting results.',
             'Read tables/recipient_denominator_coverage.csv for Unknown buckets with no comparable observed country denominator.',
             'Their numerators are retained; health denominators and within-health ratios are blank, including in legacy-zero bands.',
             'The ALL_TABLES.xlsx workbook collects all analytical tables. Figures are indexed in figures/index.html.',
             'All annual tables cover 1990-2025 by default; the optional historical figure crop is 2010-2024.',
             'The new review/top5_values.xlsx is an inspection sample, NOT the fixed historical exclusion input.',
             'The 169-selected-health-funders comparison is not generated: its roster/source workbook was not supplied.',
             '','The API snapshot and caches are local. Never upload runs/ or private/ to the public repository.']
    (output/'RUN_SUMMARY.md').write_text('\n'.join(summary)+'\n',encoding='utf-8')
    files=[p for p in output.rglob('*') if p.is_file()]
    manifest={'status':'complete_analysis_not_historical_validation','completed_utc':stamp(),
              'software_version':__version__,'data_label':data_label,'main_scenario':'14','scenarios':list(REPORT_IDS),
              'historical_report_reproduction_certified':False,'environment':environment(),
              'settings':{k:v for k,v in cfg.items() if k not in {'manual_exclusions','country_groupings','reference_status','historical_exclusions_workbook','country_groupings_workbook'}},
              'reference_inputs':file_manifest([cfg['manual_exclusions'],cfg['country_groupings']]),
              'source_snapshot_sha256':sha256(raw/'COMPLETE.json'),
              'software_files':file_manifest(list(Path(__file__).parent.glob('*.py'))+list((Path(__file__).parent/'resources').glob('*.json'))),
              'files':[{'path':str(p.relative_to(output)),'sha256':sha256(p)} for p in files]}
    write_json(manifest,output/'run_manifest.json')
    return manifest


def run_all(cfg, run_dir, *, client=None, figures=True, data_label='Dimensions live extract'):
    check_references(cfg) # Fail before a lengthy download if author decisions are missing.
    extract_stage(cfg,run_dir,client=client)
    return analyse_stage(cfg,run_dir,figures=figures,data_label=data_label)
