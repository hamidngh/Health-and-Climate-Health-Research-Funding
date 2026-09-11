"""Rebuild the selected historical funder and recipient pipelines.

The two branches intentionally retain different historical exclusion/count rules.
They must not be silently combined into a newly defined indicator.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
from .io import (InputError, read_table, require_columns, parse_list, numeric,
                 unique_keys, infer_year, write_csv)
from .queries import resource
from .scenarios import canonical, REPORT_IDS, MAIN
from .denominator_coverage import (HEALTH, UNAVAILABLE, unavailable_mask,
                                   validate_master_denominators)

METRICS = ['health_count','climate_count','health_funding_usd','climate_funding_usd']
FUNDER_ALIASES = {
    'Scenario':'scenario_id','Year':'year','Country':'country','Funder_Org':'funder_org',
    'Health_Count':'health_count','Climate_Health_Count':'climate_count',
    'Health_Funding_Value':'health_funding_usd','Climate_Health_Funding_Value':'climate_funding_usd',
    'Funder_Type':'funder_type','WHO_Region':'who_region','Lancet_Region':'lancet_region','HDI_Region':'hdi_group',
}
RECIPIENT_ALIASES = {
    'Scenario':'scenario_id','Year':'year','Rec_Country':'country',
    'Total_Health_Count':'health_count','Climate_Related_Count':'climate_count',
    'Total_Health_Funding':'health_funding_usd',
    'Total_Climate_Related_Funding_Adjusted':'climate_funding_usd',
    'Total_Climate_Related_Funding':'climate_funding_usd_full',
    'WHO_Region':'who_region','Lancet_Region':'lancet_region','HDI_Region':'hdi_group',
}

def normalise_cleaned(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    df=df.rename(columns=FUNDER_ALIASES if kind=='funder' else RECIPIENT_ALIASES).copy()
    if df.columns.duplicated().any(): raise InputError('Both canonical and legacy column names are present; resolve them explicitly.')
    keys=['scenario_id','year','country']+(['funder_org'] if kind=='funder' else [])
    require_columns(df,keys+METRICS,kind+' master')
    if 'scenario_id' in df: df['scenario_id']=df['scenario_id'].map(canonical)
    df=df[df['scenario_id'].isin(REPORT_IDS)].copy()
    df=numeric(df,['year','climate_count','climate_funding_usd'],kind+' master')
    df=validate_master_denominators(df,kind)
    if ((df['year'] % 1)!=0).any():raise InputError('Years must be integers.')
    df['year']=df['year'].astype(int)
    if kind=='recipient':
        require_columns(df,['climate_funding_usd_full'],'recipient master')
        df=numeric(df,['climate_funding_usd_full'],'recipient master')
        if not (df['country']=='GLOBAL_TOTAL').any():
            raise InputError('Recipient data need separately deduplicated GLOBAL_TOTAL rows; do not sum full-credit countries.')
    else:
        # Historical global funder series is rebuilt from funder-country attributions.
        df=df[df['country']!='GLOBAL_TOTAL'].copy()
    unique_keys(df,keys,kind+' master')
    if MAIN not in set(df['scenario_id']):raise InputError('Main Search Approach (14) is absent.')
    return df

def flag(value) -> bool:
    return str(value).strip().lower() in {'true','1','yes'}

def classification_veto(df: pd.DataFrame) -> pd.Series:
    require_columns(df,['category_for_2020'],'unfiltered numerator')
    prefixes=tuple(resource('filters.json')['anzsrc_exclusion_prefixes'])
    return df['category_for_2020'].map(lambda v:any(
        isinstance(c,dict) and str(c.get('name','')).startswith(prefixes)
        for c in parse_list(v,label='ANZSRC categories')))

def load_numerators(manifest_path: str | Path, *, year_policy='legacy_date_first') -> tuple[pd.DataFrame,list[str]]:
    manifest=read_table(manifest_path)
    require_columns(manifest,['scenario_id','path','classification_exclusions_applied'],'scenario manifest')
    manifest['scenario_id']=manifest['scenario_id'].map(canonical)
    unique_keys(manifest,['scenario_id'],'scenario manifest')
    frames=[]; paths=[str(manifest_path)]; omitted=[]
    for row in manifest.to_dict('records'):
        s=row['scenario_id']
        if s not in REPORT_IDS:continue
        # Paths are relative to the repository working directory, never guessed from filenames.
        p=Path(row['path']);p=p if p.is_absolute() else Path(manifest_path).parent/p
        df=read_table(p);paths.append(str(p))
        require_columns(df,['id','funding_usd'],str(p))
        unique_keys(df,['id'],str(p))
        df['scenario_id']=s
        if not flag(row['classification_exclusions_applied']):df=df[~classification_veto(df)].copy()
        if year_policy=='legacy_date_first' and 'start_date' in df:
            # Original groupby silently omitted NaT-derived years. Preserve that
            # population, but expose every omitted record in an audit table.
            parsed=pd.to_datetime(df['start_date'],errors='coerce').dt.year
            for row in df.loc[parsed.isna()].to_dict('records'):
                omitted.append({'id':row['id'],'scenario_id':s,'start_date':row.get('start_date'),
                                'start_year':row.get('start_year'),'reason':'missing_or_invalid_start_date_legacy_omission'})
            df=df.loc[parsed.notna()].copy()
        df['year']=infer_year(df,year_policy)
        df['funding_known']=df['funding_usd'].notna()
        df=numeric(df,['funding_usd'],str(p),missing_zero=True)
        frames.append(df)
    if not frames:raise InputError('No retained scenario data found in the explicit manifest.')
    result=pd.concat(frames,ignore_index=True)
    if MAIN not in set(manifest['scenario_id']):raise InputError('Manifest must include scenario 14.')
    result.attrs['omitted_missing_start_date']=omitted
    return result,paths

def load_exclusions(path: str | Path) -> pd.DataFrame:
    df=read_table(path)
    require_columns(df,['id','year','funding_usd','funder_org_name','funder_org_countries'],'manual exclusions')
    # Source row multiplicity is a historical calculation input, not duplicate exports.
    cols=['id','year','funding_usd','funder_org_name','funder_org_countries']
    unique_keys(df.drop_duplicates(cols),['id'],'conflicting historical exclusions')
    df=numeric(df,['year'],'manual exclusions',missing_zero=False)
    df=numeric(df,['funding_usd'],'manual exclusions',missing_zero=True)
    if df.empty:raise InputError('Manual exclusion list is empty. Supply the historical list; do not regenerate the top-five sample as exclusions.')
    return df

def funder_rows(grants: pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    rows=[]; skipped=0
    rich='funder_orgs' in grants
    for row in grants.to_dict('records'):
        common={'id':row['id'],'scenario_id':row['scenario_id'],'year':row['year'],
                'amount':row['funding_usd'],'funding_known':row['funding_known']}
        if rich:
            orgs=parse_list(row.get('funder_orgs'),label='funder_orgs')
            orgs=[o for o in orgs if isinstance(o,dict)]
            if not orgs: skipped+=1;continue # This is the historical rich-metadata behaviour.
            names=[(o.get('name','Unknown'),o.get('country_name','Unknown')) for o in orgs]
            if len(names)!=len(set(names)):raise InputError('Repeated funder objects within a grant would inflate USD sums.')
            for o in orgs:
                types=o.get('types') or []
                rows.append({**common,'funder_org':o.get('name') or 'Unknown','country':o.get('country_name') or 'Unknown',
                             'funder_type':types[0] if types else 'Unclassified'})
        elif 'funder_org_name' in row and 'funder_org_countries' in row:
            countries=[c.get('name','Unknown') for c in parse_list(row['funder_org_countries']) if isinstance(c,dict)] or ['Unknown']
            for country in countries:
                rows.append({**common,'funder_org':str(row['funder_org_name']) if pd.notna(row['funder_org_name']) else 'Unknown',
                             'country':country,'funder_type':'Unclassified'})
        else:raise InputError('Raw grants need rich funder_orgs or legacy funder name/country fields.')
    cols=['id','scenario_id','year','amount','funding_known','funder_org','country','funder_type']
    return pd.DataFrame(rows,columns=cols),{'numerator_scenario_records_without_rich_funder_metadata':skipped}

def subtract(df: pd.DataFrame, deductions: pd.DataFrame, keys: list[str], *, scenario: str | None=None) -> pd.DataFrame:
    unique_keys(df,['scenario_id']+keys,'denominator')
    unique_keys(deductions,keys,'manual deductions')
    merged=df.merge(deductions,on=keys,how='left',validate='many_to_one')
    mask=pd.Series(True,index=merged.index) if scenario is None else merged['scenario_id'].eq(scenario)
    for col,ded in [('health_count','sub_count'),('health_funding_usd','sub_amount')]:
        merged.loc[mask,col]=merged.loc[mask,col]-merged.loc[mask,ded].fillna(0)
        if (merged[col]<-1e-5).any():
            raise InputError(f'Manual exclusions exceed {col} in a denominator cell; historical clipping would hide this mismatch.')
        merged[col]=merged[col].clip(lower=0)
    return merged.drop(columns=['sub_count','sub_amount'])

def merge_metrics(den: pd.DataFrame, num: pd.DataFrame, keys: list[str], kind: str) -> pd.DataFrame:
    unique_keys(den,keys,'denominator');unique_keys(num,keys,'numerator')
    df=den.merge(num,on=keys,how='outer',validate='one_to_one',indicator='_coverage_merge')
    metrics=METRICS+(['climate_funding_usd_full'] if kind=='recipient' else [])
    for m in metrics:df[m]=df[m].fillna(0)
    if kind=='recipient':
        df['denominator_row_present']=df['_coverage_merge'].ne('right_only')
        absent=df['country'].eq('Unknown') & ~df['denominator_row_present']
        df[UNAVAILABLE]=absent
        df.loc[absent,HEALTH]=np.nan
    df=df.drop(columns='_coverage_merge')
    df=df[df['scenario_id'].isin(REPORT_IDS)].copy()
    return df.sort_values(keys).reset_index(drop=True)

def rebuild_funder(grants: pd.DataFrame, den: pd.DataFrame, exclusions: pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    rows,qa=funder_rows(grants)
    qa['historical_exclusion_source_rows']=len(exclusions)
    qa['historical_exclusion_unique_ids']=int(exclusions.id.nunique())
    qa['historical_exclusion_duplicate_rows_preserved_for_funding_sum']=int(exclusions.id.duplicated().sum())
    excluded=set(exclusions['id'].astype(str));bad=resource('filters.json')['excluded_funders']
    mask=rows['scenario_id'].eq(MAIN)&rows['id'].astype(str).isin(excluded)
    qa['manual_exclusion_rows_removed_from_main']=int(mask.sum())
    rows=rows[~mask & ~rows['funder_org'].isin(bad)].copy()
    keys=['scenario_id','year','country','funder_org']
    num=rows.groupby(keys,dropna=False).agg(climate_count=('id','nunique'),climate_funding_usd=('amount','sum')).reset_index()
    den=den.rename(columns={'Scenario':'scenario_id','Year':'year','Country':'country','Funder_Org':'funder_org',
                            'Count':'health_count','Funding_USD':'health_funding_usd'}).copy()
    require_columns(den,keys+['health_count','health_funding_usd'],'funder denominator')
    den['scenario_id']=den['scenario_id'].map(canonical)
    den=den[(den['country']!='GLOBAL_TOTAL')&~den['funder_org'].isin(bad)&den['scenario_id'].isin(REPORT_IDS)].copy()
    den=numeric(den,['health_count','health_funding_usd'],'funder denominator')
    ex_rows=[]
    for row in exclusions.to_dict('records'):
        countries=[c.get('name') for c in parse_list(row['funder_org_countries']) if isinstance(c,dict) and c.get('name')]
        for c in countries:
            ex_rows.append({'id':row['id'],'year':row['year'],'country':c,'funder_org':row['funder_org_name'],'amount':row['funding_usd']})
    ex=pd.DataFrame(ex_rows)
    if ex.empty:raise InputError('Exclusion list has no parseable funder countries.')
    ded=ex.groupby(['year','country','funder_org']).agg(sub_count=('id','nunique'),sub_amount=('amount','sum')).reset_index()
    den=subtract(den,ded,['year','country','funder_org'],scenario=MAIN)
    result=merge_metrics(den,num,keys,'funder')
    types=rows[['funder_org','funder_type']].drop_duplicates()
    conflicts=types['funder_org'].duplicated(keep=False)
    qa['funders_with_conflicting_types']=sorted(types.loc[conflicts,'funder_org'].unique().tolist())
    # Historical dictionary used the last type encountered; use Unclassified for ambiguous types instead.
    types=types[~conflicts].set_index('funder_org')['funder_type']
    result['funder_type']=result['funder_org'].map(types).fillna('Unclassified')
    qa['funder_global_definition']='sum_of_full_credit_funder_country_attributions'
    qa['count_universe']='includes_zero_or_missing_funding_grants'
    return result,qa

def recipient_countries(row: dict) -> list[str]:
    value=row.get('Constructed_Recipient_Country')
    if value is not None and pd.notna(value) and str(value).strip() not in {'','nan'}:
        countries=[x.strip() for x in str(value).split(';') if x.strip()]
    else:
        countries=[c.get('name') for c in parse_list(row.get('research_org_countries')) if isinstance(c,dict)]
        if not any(countries):countries=[o.get('country_name') for o in parse_list(row.get('research_orgs')) if isinstance(o,dict)]
    return sorted(set(c for c in countries if c)) or ['Unknown']

def recipient_funder(row: dict) -> str:
    if row.get('Derived_Funder_Org') is not None and pd.notna(row['Derived_Funder_Org']):
        return str(row['Derived_Funder_Org']).strip()
    if 'funder_orgs' in row:
        orgs=parse_list(row.get('funder_orgs'))
        if orgs and isinstance(orgs[0],dict):return orgs[0].get('name','Unknown')
        return 'Unknown'
    return str(row.get('funder_org_name','Unknown'))

def recipient_aggregate(grants: pd.DataFrame) -> pd.DataFrame:
    """Global grants once; country counts/full USD are full credit, absolute USD fractional."""
    records=[]
    for row in grants.to_dict('records'):
        common={'scenario_id':row['scenario_id'],'year':row['year'],'id':row['id']}
        countries=recipient_countries(row); amount=row['funding_usd']
        records.append({**common,'country':'GLOBAL_TOTAL','full':amount,'adjusted':amount})
        for country in countries:
            records.append({**common,'country':country,'full':amount,'adjusted':amount/len(countries)})
    cols=['scenario_id','year','country','climate_count','climate_funding_usd_full','climate_funding_usd']
    if not records:return pd.DataFrame(columns=cols)
    df=pd.DataFrame(records)
    return df.groupby(['scenario_id','year','country'],dropna=False).agg(
        climate_count=('id','nunique'),climate_funding_usd_full=('full','sum'),climate_funding_usd=('adjusted','sum')).reset_index()

def rebuild_recipient(grants: pd.DataFrame, den: pd.DataFrame, exclusions: pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    bad=resource('filters.json')['excluded_funders']
    fp=grants[grants['id'].astype(str).isin(exclusions['id'].astype(str))].copy()
    keep=grants[~grants['id'].astype(str).isin(exclusions['id'].astype(str))].copy()
    names=keep.apply(lambda r:recipient_funder(r.to_dict()),axis=1)
    keep=keep[~names.isin(bad)].copy()
    zero=int(keep['funding_usd'].le(0).sum())
    keep=keep[keep['funding_usd']>0].copy() # Preserve the historical recipient population, not the report's assumed all-grant population.
    num=recipient_aggregate(keep)
    den=den.rename(columns={'Scenario':'scenario_id','Year':'year','Country':'country','Rec_Country':'country',
                            'Count':'health_count','Funding_USD':'health_funding_usd'}).copy()
    keys=['scenario_id','year','country'];require_columns(den,keys+['health_count','health_funding_usd'],'recipient denominator')
    den['scenario_id']=den['scenario_id'].map(canonical)
    den=den[den['scenario_id'].isin(REPORT_IDS)].copy()
    den=numeric(den,['health_count','health_funding_usd'],'recipient denominator')
    # Source recipient code subtracts only positive-funded manual exclusions, across all scenarios.
    ded=recipient_aggregate(fp[fp['funding_usd']>0])
    ded=ded.rename(columns={'climate_count':'sub_count','climate_funding_usd_full':'sub_amount'})
    ded=ded[keys+['sub_count','sub_amount']]
    if not ded.empty:
        # Here the scenario is part of the deduction key (unlike the funder pipeline).
        unique_keys(den,keys,'recipient denominator');unique_keys(ded,keys,'recipient deductions')
        den=den.merge(ded,on=keys,how='left',validate='one_to_one')
        for m,s in [('health_count','sub_count'),('health_funding_usd','sub_amount')]:
            den[m]-=den[s].fillna(0)
            if (den[m]<-1e-5).any():raise InputError('Recipient manual deductions exceed denominator; investigate rather than clipping.')
            den[m]=den[m].clip(lower=0)
        den=den.drop(columns=['sub_count','sub_amount'])
    result=merge_metrics(den,num,keys,'recipient')
    if unavailable_mask(result).any():
        # Once this unmeasured bucket is needed, represent it for every extracted
        # scenario/year, including years with no climate grant in the bucket.
        # Otherwise completing the time series would invent zero health funding
        # in years where the Unknown denominator was never observed.
        index=pd.MultiIndex.from_frame(den[['scenario_id','year']].drop_duplicates())
        existing=pd.MultiIndex.from_frame(result.loc[result.country.eq('Unknown'),['scenario_id','year']])
        gaps=index.difference(existing)
        if len(gaps):
            empty=gaps.to_frame(index=False)
            empty['country']='Unknown'
            for col in ['climate_count','climate_funding_usd','climate_funding_usd_full']:
                empty[col]=0.0
            for col in HEALTH:empty[col]=np.nan
            empty['denominator_row_present']=False
            empty[UNAVAILABLE]=True
            result=pd.concat([result,empty],ignore_index=True).sort_values(keys).reset_index(drop=True)
    qa={'manual_exclusions_scope':'all_retained_scenarios',
        'unavailable_unknown_denominator_cells':int(unavailable_mask(result).sum()),
        'unavailable_unknown_denominator_cells_with_climate_grants':int((unavailable_mask(result)&result.climate_count.gt(0)).sum()),
        'unknown_denominator_policy':'retain numerator and global attribution; blank unobserved health denominator and shares',
        'zero_or_missing_funding_scenario_records_removed':zero,
        'count_universe':'positive_funded_grants_only_in_numerator',
        'recipient_count_attribution':'one_full_count_per_distinct_country; global unique grants',
        'recipient_funding_attribution':'equal_fraction_per_distinct_country; unknown is a retained bucket'}
    return result,qa

def rebuild(config: dict) -> tuple[dict,list[str]]:
    if not config.get('denominator_classification_exclusions_applied',False):
        raise InputError('Confirm denominator classification exclusions in the local config before rebuilding.')
    grants,inputs=load_numerators(config['scenario_manifest'],year_policy=config.get('year_policy','legacy_date_first'))
    exclusions=load_exclusions(config['manual_exclusions']);inputs.append(config['manual_exclusions'])
    funder,qa=rebuild_funder(grants,read_table(config['funder_denominator']),exclusions)
    inputs.append(config['funder_denominator'])
    out=Path(config['output_dir']);out.mkdir(parents=True,exist_ok=True)
    write_csv(funder,out/'funder_master.csv')
    result={'funder_master':str(out/'funder_master.csv'),'quality':{'funder':qa},'raw_numerators':grants,
            'omitted_missing_start_date':grants.attrs.get('omitted_missing_start_date',[])}
    if config.get('recipient_denominator'):
        if not config.get('recipient_denominator_excludes_named_funders',False):
            raise InputError('Recipient denominator must already exclude the two named funders, as in the source API query.')
        recipient,rqa=rebuild_recipient(grants,read_table(config['recipient_denominator']),exclusions)
        inputs.append(config['recipient_denominator']);write_csv(recipient,out/'recipient_master.csv')
        result.update({'recipient_master':str(out/'recipient_master.csv')})
        result['quality']['recipient']=rqa
    return result,inputs
