"""Private, key-wise numerical comparisons with the author's historical workbooks."""
from pathlib import Path
import numpy as np
import pandas as pd
from .io import InputError, read_table, write_json, sha256, stamp, unique_keys, require_columns
from .cleaning import FUNDER_ALIASES, RECIPIENT_ALIASES, METRICS
from .excel import export_pair


def canonical_master(path,kind,*,scenario_scope="main"):
    df=read_table(path)
    aliases=FUNDER_ALIASES if kind=='funder' else RECIPIENT_ALIASES
    df=df.rename(columns=aliases)
    require_columns(df,['scenario_id','year','country']+METRICS,'comparison input')
    from .scenarios import canonical, MAIN
    def maybe(v):
        try:return canonical(v)
        except InputError:return None
    df['scenario_id']=df.scenario_id.map(maybe)
    df=df[df.scenario_id.notna()].copy()
    if scenario_scope=='main':df=df[df.scenario_id.eq(MAIN)].copy()
    if df.empty:raise InputError('No retained rows found in comparison input.')
    df['year']=pd.to_numeric(df['year'],errors='raise').astype(int)
    if kind=='funder':df=df[df['country']!='GLOBAL_TOTAL']
    keys=['scenario_id','year','country']+(['funder_org'] if kind=='funder' else [])
    metrics=METRICS+(['climate_funding_usd_full'] if kind=='recipient' else [])
    require_columns(df,keys+metrics,'comparison input')
    unique_keys(df,keys,'comparison input')
    return df[keys+metrics],keys,metrics


def compare_master(baseline,current,kind,out,*,usd_tolerance=0.01,scenario_scope="main"):
    if not np.isfinite(usd_tolerance) or usd_tolerance<0:raise InputError('Invalid USD tolerance.')
    a,keys,metrics=canonical_master(baseline,kind,scenario_scope=scenario_scope);b,_,_=canonical_master(current,kind,scenario_scope=scenario_scope)
    merged=a.merge(b,on=keys,how='outer',suffixes=('_baseline','_current'),indicator=True,validate='one_to_one')
    rows=[]
    for r in merged.to_dict('records'):
        for metric in metrics:
            old,new=r[metric+'_baseline'],r[metric+'_current']
            if r['_merge']!='both':status='MISSING_CURRENT' if r['_merge']=='left_only' else 'MISSING_BASELINE'
            elif not np.isfinite(old) or not np.isfinite(new):status='NONFINITE'
            else:status='PASS' if abs(new-old)<=(0 if metric.endswith('_count') else usd_tolerance) else 'FAIL'
            rows.append({**{k:r[k] for k in keys},'metric':metric,'baseline':old,'current':new,
                         'difference':new-old,'status':status})
    result=pd.DataFrame(rows);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    export_pair(result,out/(kind+'_comparison'))
    failures=result[result['status']!='PASS'];export_pair(failures,out/(kind+'_differences'))
    summary={'checked_utc':stamp(),'kind':kind,'status':'PASS' if failures.empty else 'DIFFERENCES_FOUND',
             'baseline_sha256':sha256(baseline),'current_sha256':sha256(current),
             'usd_absolute_tolerance':usd_tolerance,'count_absolute_tolerance':0,
             'baseline_rows':len(a),'current_rows':len(b),'tested_cells':len(result),
             'cells_not_passing':len(failures),'status_counts':result['status'].value_counts().to_dict(),
             'scope':scenario_scope+' retained master-table comparison; not an entire report certification.'}
    write_json(summary,out/(kind+'_comparison_summary.json'))
    return summary


def compare_bands(baseline,current_bands,out,*,kind='funder',start_year=2010,end_year=2024,usd_tolerance=0.01):
    """Compare the old global plotting workbook against the NEW global envelope.

    The original funder plot used min/max of ALL retained rows, including main.
    Shares use the baseline's Ratio_* columns; no headline numbers are hardcoded.
    """
    from .scenarios import canonical,REPORT_IDS
    if kind!='funder':raise InputError('Historical band comparison expects the funder plotting workbook. Recipient master tables can be checked with compare --kind recipient.')
    old=read_table(baseline)
    metrics={'Climate_Health_Funding_Value':'climate_funding_usd','Climate_Health_Count':'climate_count',
             'Ratio_Funding':'funding_share','Ratio_Count':'count_share'}
    require_columns(old,['Scenario','Year','Geo_Scope']+list(metrics),'historical plotting workbook')
    def maybe(v):
        try:return canonical(v)
        except InputError:return None
    old['scenario_id']=old.Scenario.map(maybe)
    old=old[(old.Geo_Scope=='Global')&old.scenario_id.notna()&old.Year.between(start_year,end_year)]
    now=read_table(current_bands);now=now[(now.kind==kind)&(now.scope_system=='Global')&now.year.between(start_year,end_year)]
    records=[]
    for year in range(start_year,end_year+1):
        rows=old[old.Year.eq(year)]
        for before,metric in metrics.items():
            current=now[now.year.eq(year)&now.metric.eq(metric)]
            valid=set(rows.scenario_id)==set(REPORT_IDS) and len(rows)==len(REPORT_IDS) and len(current)==1
            for bound in ['min','max']:
                a=getattr(rows[before],bound)() if valid else float('nan')
                b=current.iloc[0]['all_11_'+bound] if len(current)==1 else float('nan')
                tol=usd_tolerance if metric.endswith('_usd') else 1e-12 if metric.endswith('_share') else 0
                passed=bool(valid and np.isfinite(a) and np.isfinite(b) and abs(a-b)<=tol)
                records.append({'year':year,'metric':metric,'bound':bound,'baseline':a,'current':b,
                                'difference':b-a,'status':'PASS' if passed else 'DIFFERENCE_OR_MISSING_INPUT'})
    table=pd.DataFrame(records);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    export_pair(table,out/'global_band_comparison')
    result={'status':'PASS' if table.status.eq('PASS').all() else 'DIFFERENCES_FOUND',
            'tested_cells':len(table),'cells_not_passing':int(table.status.ne('PASS').sum()),
            'start_year':start_year,'end_year':end_year,'comparison':'global historical all-eleven min/max',
            'baseline_sha256':sha256(baseline),'current_sha256':sha256(current_bands)}
    write_json(result,out/'global_band_comparison_summary.json');return result
