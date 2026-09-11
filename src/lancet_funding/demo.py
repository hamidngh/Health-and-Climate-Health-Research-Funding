"""Deterministic in-memory test transport. These are fabricated grants, not data."""
from __future__ import annotations
from collections import defaultdict
from types import SimpleNamespace
import copy
import re
import pandas as pd
from lancet_funding.queries import resource


def categories():
    f=[{'id':'A32','name':'32 Biomedical and Clinical Sciences'}, {'id':'A42','name':'42 Health Sciences'}]
    f += [{'id':'X'+p,'name':p+' Synthetic exclusion category'} for p in resource('filters.json')['anzsrc_exclusion_prefixes']]
    u=[{'id':f'U{i}','name':f'{i} Synthetic health assessment'} for i in range(1,5)]
    return f,u


def universe(years=range(2010,2026)):
    bad=resource('filters.json')['excluded_funders']
    names=['Synthetic US Fund','Synthetic UK Fund','Synthetic Pacific Fund']+bad
    countries=['United States','United Kingdom','Australia','United States','United States']
    orgs=[{'id':f'grid.synthetic.{i}','name':name,'country_name':c,'types':['Government' if i%2==0 else 'Nonprofit']}
          for i,(name,c) in enumerate(zip(names,countries))]
    rows=[]
    for year in years:
        specs=[(1,1,1,1,True,100.,[0],['United States','Nigeria']),
               (1,1,1,0,True,50.,[1],['United Kingdom']),
               (1,1,0,1,True,30.,[2],['Australia']),
               (1,0,1,1,True,None,[0],['Nigeria']),
               (0,1,1,1,True,20.,[1],['United Kingdom']),
               (1,1,0,0,True,90.,[0],['United States']),
               (1,1,1,1,False,1000.,[0],['United States']),
               (1,1,1,1,True,500.,[0],['United States']),
               (1,1,1,1,True,70.,[3],['United States']),
               (1,1,1,1,True,80.,[4],['United States']),
               (1,1,1,1,True,45.,[0,1],['United States','United Kingdom'])]
        for i,(h,a,r,u,c,amount,fs,cs) in enumerate(specs):
            award=None if amount is None else amount*(1+(year-2010)/100)
            rows.append({'id':f'grant.synthetic.{year}.{i}','title':'SYNTHETIC TEST GRANT',
                         'abstract':'Fabricated unit-test record; not a Dimensions grant.',
                         'start_date':f'{year}-04-12','end_date':f'{year+2}-04-12','start_year':year,
                         'funding_usd':award,'funding_currency':'USD',
                         'funder_orgs':[copy.deepcopy(orgs[j]) for j in fs],
                         'funder_org_name':orgs[fs[0]]['name'],
                         'funder_org_countries':[{'id':orgs[j]['country_name'],'name':orgs[j]['country_name']} for j in fs],
                         'research_org_countries':[{'id':v,'name':v} for v in cs],
                         'research_orgs':[{'id':'grid.rec.'+str(j),'name':'Synthetic recipient','country_name':v} for j,v in enumerate(cs)],
                         'category_for_2020':[{'id':'A32','name':'32 Biomedical and Clinical Sciences'}] if a else [],
                         'category_uoa':[{'id':'U1','name':'1 Synthetic health assessment'}] if u else [],
                         'category_hrcs_hc':[{'id':'R1','name':'Synthetic health category'}] if r else [],
                         'category_hrcs_rac':[], '_h':bool(h),'_a':bool(a),'_r':bool(r),'_u':bool(u),'_climate':c})
    return rows


class FakeDimensions:
    endpoint='in-memory-synthetic-transport'
    def __init__(self,rows=None):self.rows=rows or universe();self.calls=[]
    def query(self,q):
        self.calls.append(q)
        f,u=categories()
        if 'search grants return category_for_2020' in q:obj={'category_for_2020':f}
        elif 'search grants return category_uoa' in q:obj={'category_uoa':u}
        else:
            rows=self.rows
            if 'public health' in q:rows=[r for r in rows if r['_h']]
            if 'climate change' in q:rows=[r for r in rows if r['_climate']]
            if '"A32"' in q:rows=[r for r in rows if r['_a']]
            if '"U1"' in q:rows=[r for r in rows if r['_u']]
            if 'category_hrcs_hc is not empty' in q:rows=[r for r in rows if r['_r']]
            if 'not funder_org_name' in q:
                bad=resource('filters.json')['excluded_funders'];rows=[r for r in rows if r['funder_org_name'] not in bad]
            m=re.search(r'start_year\s*=\s*(\d{4})',q)
            if m:rows=[r for r in rows if r['start_year']==int(m.group(1))]
            if 'start_date is empty' in q:rows=[r for r in rows if r['start_date'] is None]
            for op in ['>=','<=']:
                m=re.search(r'start_date '+op+r' "([0-9-]+)"',q)
                if m:rows=[r for r in rows if r['start_date'] and ((r['start_date']>=m.group(1)) if op=='>=' else (r['start_date']<=m.group(1)))]
            limit=int(re.findall(r'limit (\d+)',q)[-1]) if 'limit ' in q else 20
            if 'return grants[' in q:
                offset=int(re.search(r'skip (\d+)',q).group(1)) if 'skip ' in q else 0
                obj={'grants':[{k:v for k,v in r.items() if not k.startswith('_')} for r in sorted(rows,key=lambda r:r['id'])[offset:offset+limit]],
                     '_stats':{'total_count':len(rows)}}
            else:
                field=re.search(r'return (\w+)',q).group(1);grouped={}
                for row in rows:
                    if field=='start_year':items=[{'id':row['start_year'],'name':str(row['start_year'])}]
                    elif field=='funder_orgs':items=row['funder_orgs']
                    elif field=='research_org_countries':items=row['research_org_countries']
                    else:raise AssertionError('Unexpected test facet '+field)
                    for item in items:
                        key=item['id']
                        if key not in grouped:grouped[key]={**item,'count':0,'funding':0.}
                        grouped[key]['count']+=1;grouped[key]['funding']+=row['funding_usd'] or 0.
                obj={field:sorted(grouped.values(),key=lambda r:(-r['count'],str(r['id'])))[:limit]}
        return SimpleNamespace(json=copy.deepcopy(obj),errors=None)


def references(rows):
    chosen=[r for r in rows if r['id'] in {'grant.synthetic.2016.7','grant.synthetic.2020.7'}]
    ex=pd.DataFrame([{'id':r['id'],'year':r['start_year'],'funding_usd':r['funding_usd'],
                      'funder_org_name':r['funder_org_name'],'funder_org_countries':str(r['funder_org_countries'])} for r in chosen])
    mapping=pd.DataFrame([
        {'Country Name to use':'United States of America','Lancent Region':'Northern America','WHO Region':'Americas','HDI Group 2025':'Very High'},
        {'Country Name to use':'United Kingdom','Lancent Region':'Europe','WHO Region':'Europe','HDI Group 2025':'Very High'},
        {'Country Name to use':'Australia','Lancent Region':'Oceania','WHO Region':'Western Pacific','HDI Group 2025':'Very High'},
        {'Country Name to use':'Nigeria','Lancent Region':'Africa','WHO Region':'Africa','HDI Group 2025':'Low'}])
    return ex,mapping


def prepare_demo(cfg,run_dir):
    """Prepare fabricated input/reference files only in the requested demo run."""
    from pathlib import Path
    from .io import write_csv
    from .reference import import_exclusions,import_groupings
    from .dimensions import Client
    run=Path(run_dir);settings=dict(cfg);rows=universe(range(cfg['start_year'],cfg['end_year']+1))
    ref=run/'synthetic_inputs'
    for key in ['manual_exclusions','country_groupings','reference_status']:
        settings[key]=str(ref/(key+('.json' if key=='reference_status' else '.csv')))
    ex,groups=references(rows);write_csv(ex,ref/'source_exclusions.csv');write_csv(groups,ref/'source_groups.csv')
    # Reuse unchanged imports, so their timestamped status does not drift between reruns.
    if not Path(settings['reference_status']).exists():
        import_exclusions(ref/'source_exclusions.csv',settings,all_rows=True)
        import_groupings(ref/'source_groups.csv',settings)
    settings['pause_seconds']=0
    return settings,Client(run/'raw/cache',dsl=FakeDimensions(rows),pause=0)
