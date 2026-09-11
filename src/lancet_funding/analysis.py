"""Deterministic indicator tables; ratios are ratios of sums, never means of ratios."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .io import InputError, require_columns, unique_keys, read_table
from .queries import resource
from .scenarios import MAIN, REPORT_IDS
from .cleaning import METRICS
from .denominator_coverage import (UNAVAILABLE, unavailable_mask,
                                   validate_master_denominators, sum_metric_groups)

GROUPING_ALIASES={'Country Name to use':'country','Lancent Region':'lancet_region',
                  'WHO Region':'who_region','HDI Group 2025':'hdi_group'}

def ratio(numerator, denominator):
    n=np.asarray(numerator,dtype=float);d=np.asarray(denominator,dtype=float)
    return np.divide(n,d,out=np.full_like(n,np.nan,dtype=float),where=d>0)

def add_ratios(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    df=df.copy()
    base='climate_funding_usd_full' if kind=='recipient' else 'climate_funding_usd'
    df['count_share']=ratio(df['climate_count'],df['health_count'])
    df['funding_share']=ratio(df[base],df['health_funding_usd'])
    # Historical zero-filling applies only to observed zero denominators.
    # It must never turn structurally unavailable coverage into a 0% share.
    absent=unavailable_mask(df)
    df['count_share_legacy_zero']=df['count_share'].fillna(0.0).mask(absent)
    df['funding_share_legacy_zero']=df['funding_share'].fillna(0.0).mask(absent)
    return df

def attach_geography(df: pd.DataFrame, mapping: pd.DataFrame | None) -> tuple[pd.DataFrame,pd.DataFrame]:
    df=df.copy()
    aliases=resource('country_aliases.json')
    df['mapped_country']=df['country'].replace(aliases)
    if mapping is not None:
        mapping=mapping.rename(columns=GROUPING_ALIASES).copy()
        require_columns(mapping,['country','lancet_region','who_region','hdi_group'],'country groupings')
        unique_keys(mapping,['country'],'country groupings')
        for col in ['lancet_region','who_region','hdi_group']:
            lookup=mapping.set_index('country')[col]
            df[col]=df['mapped_country'].map(lookup)
    for col in ['lancet_region','who_region','hdi_group']:
        if col not in df:df[col]='Unmapped'
        df[col]=df[col].fillna('Unmapped')
        df.loc[df['country']=='GLOBAL_TOTAL',col]='Global'
    missing=df[(df['country']!='GLOBAL_TOTAL')&df[['lancet_region','who_region','hdi_group']].eq('Unmapped').any(axis=1)]
    missing=missing[['country','mapped_country','lancet_region','who_region','hdi_group']].drop_duplicates()
    return df,missing

def check_master(df: pd.DataFrame, kind: str) -> dict:
    df=validate_master_denominators(df,kind)
    absent=unavailable_mask(df)
    covered=df.loc[~absent]
    base='climate_funding_usd_full' if kind=='recipient' else 'climate_funding_usd'
    if (covered['climate_count']>covered['health_count']+1e-6).any():
        raise InputError(f'{kind}: climate counts exceed health counts; inspect query coverage/exclusions.')
    tol=np.maximum(1e-4,covered['health_funding_usd'].abs()*1e-9)
    if (covered[base]>covered['health_funding_usd']+tol).any():
        raise InputError(f'{kind}: climate USD exceeds health USD; inspect attribution, dates and exclusions.')
    quality={'rows':len(df),'scenarios':sorted(df['scenario_id'].unique()),'year_min':int(df['year'].min()),
             'year_max':int(df['year'].max()),'zero_health_funding_cells':int(df['health_funding_usd'].eq(0).sum()),
             'zero_health_count_cells':int(df['health_count'].eq(0).sum()),
             'unavailable_recipient_denominator_cells':int(absent.sum()),
             'unavailable_recipient_denominator_cells_with_climate_grants':int((absent & df.climate_count.gt(0)).sum()),
             'validated_observed_denominator_cells':int((~absent).sum())}
    if kind=='recipient':
        gl=df[df['country']=='GLOBAL_TOTAL'].set_index(['scenario_id','year'])['climate_funding_usd']
        co=df[df['country']!='GLOBAL_TOTAL'].groupby(['scenario_id','year'])['climate_funding_usd'].sum()
        comparison=gl.to_frame('global').join(co.rename('countries')).fillna(0)
        maxdiff=float((comparison['global']-comparison['countries']).abs().max()) if len(comparison) else 0
        quality['max_fractional_country_funding_minus_global_abs']=maxdiff
        if not np.allclose(comparison['global'],comparison['countries'],rtol=1e-9,atol=1e-3):
            raise InputError('Recipient fractional country funding does not reconcile to global funding.')
    return quality

def scopes(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    metrics=METRICS+(['climate_funding_usd_full'] if kind=='recipient' else [])
    frames=[]
    def group(sub, name, system):
        if sub.empty:return
        x=sum_metric_groups(sub,['scenario_id','year'],metrics)
        x['scope']=name;x['scope_system']=system;frames.append(x)
    if kind=='funder':group(df,'Global','Global')
    else:group(df[df['country']=='GLOBAL_TOTAL'],'Global','Global')
    country=df[df['country']!='GLOBAL_TOTAL']
    for name,sub in country.groupby('mapped_country',dropna=False):group(sub,str(name),'Country')
    if kind=='funder':
        global_sum=df.groupby(['scenario_id','year'])[metrics].sum()
        for name,sub in country.groupby('mapped_country',dropna=False):
            own=sub.groupby(['scenario_id','year'])[metrics].sum()
            row=global_sum.subtract(own.reindex(global_sum.index,fill_value=0)).reset_index()
            row['scope']=str(name);row['scope_system']='RoW';frames.append(row)
    for system,col in [('WHO','who_region'),('Lancet','lancet_region'),('HDI','hdi_group')]:
        for name,sub in country.groupby(col,dropna=False):group(sub,str(name),system)
    result=pd.concat(frames,ignore_index=True)
    result['kind']=kind
    return add_ratios(result,kind)


def headline_values(scope_data: pd.DataFrame) -> pd.DataFrame:
    main=scope_data[(scope_data['scenario_id']==MAIN)&(scope_data['scope_system']=='Global')].copy()
    records=[]
    units={'climate_funding_usd':'USD','health_funding_usd':'USD','climate_count':'grants_or_funder_attributions',
           'health_count':'grants_or_funder_attributions','funding_share_pct':'percent','count_share_pct':'percent'}
    for kind,data in main.groupby('kind'):
        data=data.set_index('year')
        for year,row in data.iterrows():
            values={m:row[m] for m in ['climate_funding_usd','health_funding_usd','climate_count','health_count']}
            values.update({'funding_share_pct':100*row['funding_share'],'count_share_pct':100*row['count_share']})
            for metric,value in values.items():
                records.append({'kind':kind,'metric':f'{metric}_{year}','value':value,'unit':units[metric]})
        for start,end in [(2016,2024),(2020,2024)]:
            if start not in data.index or end not in data.index:continue
            a,b=data.loc[start],data.loc[end]
            for metric in ['climate_funding_usd','climate_count']:
                val=100*(b[metric]/a[metric]-1) if a[metric]>0 else np.nan
                records.append({'kind':kind,'metric':f'{metric}_growth_pct_{start}_{end}','value':val,'unit':'percent'})
            records.append({'kind':kind,'metric':f'funding_share_fold_{start}_{end}',
                            'value':b['funding_share']/a['funding_share'] if a['funding_share']>0 else np.nan,'unit':'fold'})
            records.append({'kind':kind,'metric':f'funding_share_pp_change_{start}_{end}',
                            'value':100*(b['funding_share']-a['funding_share']),'unit':'percentage_points'})
    return pd.DataFrame(records)

def equity_tables(df: pd.DataFrame, kind: str, periods: list[dict]) -> pd.DataFrame:
    """Export alternative explicit share denominators; do not assert the report's unstated period."""
    main=df[df['scenario_id']==MAIN]
    out=[]
    for period in periods:
        lo=period.get('start',int(main['year'].min()));hi=period.get('end',int(main['year'].max()))
        sub=main[main['year'].between(lo,hi)]
        co=sub[sub['country']!='GLOBAL_TOTAL']
        gl=sub[sub['country']=='GLOBAL_TOTAL']
        for metric in ['climate_count','climate_funding_usd']:
            global_total=gl[metric].sum() if kind=='recipient' else co[metric].sum()
            attribution_total=co[metric].sum()
            for system,col in [('HDI','hdi_group'),('Lancet','lancet_region'),('WHO','who_region')]:
                for group,amount in co.groupby(col)[metric].sum().items():
                    out.append({'kind':kind,'period':period['label'],'start_year':lo,'end_year':hi,
                                'scope_system':system,'group':group,'metric':metric,'amount':amount,
                                'global_total':global_total,'country_attribution_total':attribution_total,
                                'share_of_global_pct':100*amount/global_total if global_total else np.nan,
                                'share_of_country_attributions_pct':100*amount/attribution_total if attribution_total else np.nan,
                                'report_period_and_denominator_confirmed':False})
    return pd.DataFrame(out)

def active_funders(df: pd.DataFrame, year: int=2024) -> tuple[pd.DataFrame,pd.DataFrame]:
    mask=df['year'].eq(year)&df[METRICS].gt(0).any(axis=1)
    active=sorted(df.loc[mask,'funder_org'].unique())
    if not active:raise InputError(f'No funders active in {year}; cannot run robustness check.')
    cohort=pd.DataFrame({'funder_org':active,'anchor_year':year,'cohort_rule':'any_positive_metric_across_eleven_retained_approaches'})
    return df[df['funder_org'].isin(active)].copy(),cohort

def funder_coverage(df: pd.DataFrame) -> pd.DataFrame:
    keys=['scenario_id','year','country']
    h=df[df['health_count']>0].groupby(keys)['funder_org'].nunique().rename('health_funder_count')
    c=df[df['climate_count']>0].groupby(keys)['funder_org'].nunique().rename('climate_funder_count')
    return h.to_frame().join(c,how='outer').fillna(0).reset_index()



BAND_METRICS=METRICS+['funding_share','count_share']

def methodological_bands(data, *, policy='historical_all_11', zero_policy='historical_zero', metrics=None):
    """Annual metric-wise envelopes, never ratios of unrelated numerator/denominator extrema.

    Historical code grouped all eleven retained scenarios (including main). Also export
    the envelope of the ten alternatives alone. These are methodological ranges, not CIs.
    """
    from .scenarios import ALTERNATIVE_IDS
    metrics=metrics or BAND_METRICS
    if policy not in {'historical_all_11','alternatives_only'}:raise InputError('Invalid band policy.')
    if zero_policy not in {'historical_zero','undefined'}:raise InputError('Invalid zero-denominator band policy.')
    keys=['kind','scope_system','scope','year'];rows=[]
    unique_keys(data,keys+['scenario_id'],'band inputs')
    for key,part in data.groupby(keys,sort=True):
        if set(part['scenario_id'])!=set(REPORT_IDS):
            raise InputError('Band input lacks one or more completed scenarios: '+str(key))
        part=part.set_index('scenario_id')
        for metric in metrics:
            source=metric+'_legacy_zero' if zero_policy=='historical_zero' and metric+'_legacy_zero' in part else metric
            all_values=part[source]
            alt_values=all_values.loc[list(ALTERNATIVE_IDS)]
            selected=all_values if policy=='historical_all_11' else alt_values
            rows.append({**dict(zip(keys,key)),'metric':metric,'main':all_values.loc[MAIN],
                         'band_min':selected.min(),'band_max':selected.max(),
                         'alternative_min':alt_values.min(),'alternative_max':alt_values.max(),
                         'all_11_min':all_values.min(),'all_11_max':all_values.max(),
                         'n_scenarios':len(part),'n_alternatives':len(alt_values),
                         'n_defined_scenarios':int(part[metric].notna().sum()),
                         'n_defined_alternatives':int(part.loc[list(ALTERNATIVE_IDS),metric].notna().sum()),
                         'band_policy':policy,'zero_denominator_policy':zero_policy})
    return pd.DataFrame(rows)

def coverage_scopes(df, *, year_start=None, year_end=None):
    """Number of distinct reporting funder names per scenario/year and geography."""
    frames=[]
    groups=[('Global','Global',df)]
    for system,col in [('Country','mapped_country'),('WHO','who_region'),('Lancet','lancet_region'),('HDI','hdi_group')]:
        groups.extend((system,str(name),sub) for name,sub in df.groupby(col,dropna=False))
    for system,name,sub in groups:
        index=pd.MultiIndex.from_product([REPORT_IDS,range(int(df.year.min()) if year_start is None else int(year_start),(int(df.year.max()) if year_end is None else int(year_end))+1)],names=['scenario_id','year'])
        h=sub[sub.health_count>0].groupby(['scenario_id','year']).funder_org.nunique().reindex(index,fill_value=0)
        c=sub[sub.climate_count>0].groupby(['scenario_id','year']).funder_org.nunique().reindex(index,fill_value=0)
        out=h.rename('health_funder_count').to_frame().join(c.rename('climate_funder_count')).reset_index()
        out['funder_share']=ratio(out.climate_funder_count,out.health_funder_count)
        out['funder_share_legacy_zero']=out.funder_share.fillna(0.0)
        out['kind']='funder';out['scope_system']=system;out['scope']=name;frames.append(out)
    return pd.concat(frames,ignore_index=True)
