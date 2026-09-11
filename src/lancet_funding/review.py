"""Generate a NEW top-five inspection workbook, never an exclusion rule.

The ranking reproduces groupby(start_date.year).nlargest(5, funding_usd).
For exact ties we sort by grant ID first, making repeat runs deterministic. Original
notebook display-only sort_values did not alter the workbook; we document the order.
Local regex tags are diagnostic approximations from the original TagGenerator, not
replacements for the server's keyword membership.
"""
from pathlib import Path
import re
import pandas as pd
from .io import read_table,parse_list,write_json,sha256,stamp,InputError
from .queries import keyword_profile
from .excel import export_pair

def word_pattern(kw):
    kw=kw.lower().strip().replace('"','').replace('*',r'\w*').replace('?',r'.?')
    return kw[:-1]+r'(?:y|ies)' if kw.endswith('y') else kw+r'(?:s|es)?'

def search_pattern(term):
    term=term.lower().strip()
    if 'and' in term and '(' in term and 'cyclon' in term:
        return r'(?=.*\bcyclon\w*\b)(?=.*\b(climate|tropical|storm)\b)'
    if '~' in term:
        phrase,prox=term.split('~');words=phrase.replace('"','').split()
        if len(words)==2:
            p1,p2=map(word_pattern,words);gap=r'\W+(?:\w+\W+){0,'+str(int(prox))+r'}?'
            return r'\b(?:'+p1+gap+p2+'|'+p2+gap+p1+r')\b'
    return r'\b'+r'\W+'.join(map(word_pattern,term.replace('"','').split()))+r'\b'

def tag_grants(df):
    out=df.copy();profile=keyword_profile('historical_notebook')
    def has(value,prefixes):
        return int(any(isinstance(c,dict) and str(c.get('name','')).startswith(prefixes) for c in parse_list(value)))
    for code in ['32','42']:
        out['is_anzsrc_'+code]=out['category_for_2020'].map(lambda x:has(x,(code,)))
    for code in range(1,5):
        out['is_uoa_'+str(code)]=out['category_uoa'].map(lambda x:has(x,(str(code)+' ',f'A0{code}')))
    out['is_hrcs_non_empty']=out['category_hrcs_hc'].map(lambda x:int(bool(parse_list(x))))
    text=(out.title.fillna('').astype(str)+' '+out.abstract.fillna('').astype(str)).str.lower().str.replace('\n',' ',regex=False).str.replace('\r',' ',regex=False)
    for target,terms in [('matched_climate_terms',profile['climate_terms']),('matched_health_terms',profile['health_terms'])]:
        compiled=[(term.replace('"',''),re.compile(search_pattern(term))) for term in terms]
        out[target]=text.map(lambda s:' | '.join(term for term,pattern in compiled if pattern.search(s)))
    out['year']=pd.to_datetime(out.start_date,errors='coerce').dt.year
    return out

def top_five(df):
    work=df.copy();work['year']=pd.to_datetime(work.start_date,errors='coerce').dt.year
    work['funding_usd']=pd.to_numeric(work.funding_usd,errors='coerce')
    work=work[work.year.notna()].sort_values('id',kind='stable')
    selected=[sub.nlargest(5,'funding_usd',keep='first') for _,sub in work.groupby('year',sort=True)]
    result=pd.concat(selected,ignore_index=True) if selected else work.iloc[:0].copy()
    result['year']=result.year.astype(int)
    result['rank_within_year']=result.groupby('year').cumcount()+1
    result['purpose']='Inspection sample only; NOT an automatic exclusion'
    return result

def generate_review(cfg,run_dir):
    run=Path(run_dir);raw=read_table(run/'raw/numerators/Scenario_14_Any_Three.csv')
    tagged=tag_grants(raw)
    export_pair(tagged,run/'review/Scenario_14_Any_Three_TAGGED')
    ranked=top_five(tagged)
    export_pair(ranked,run/'review/top5_values')
    fixed=read_table(cfg['manual_exclusions'])
    by_id=tagged[['id','funding_usd','year']].rename(columns={'funding_usd':'current_funding_usd','year':'current_date_year'})
    comparison=fixed.merge(by_id,on='id',how='left',validate='many_to_one',indicator=True)
    comparison['found_in_current_s14']=comparison['_merge'].eq('both')
    comparison['in_current_top5']=comparison.id.isin(ranked.id)
    comparison['current_minus_historical_usd']=comparison.current_funding_usd-comparison.funding_usd
    comparison['historical_id_repeated']=comparison.id.duplicated(keep=False)
    comparison=comparison.drop(columns='_merge')
    export_pair(comparison,run/'review/historical_list_reconciliation')
    write_json({'created_utc':stamp(),'source_sha256':sha256(run/'raw/numerators/Scenario_14_Any_Three.csv'),
                'historical_list_sha256':sha256(cfg['manual_exclusions']),
                'sample_rows':len(ranked),'historical_rows':len(fixed),'historical_unique_ids':int(fixed.id.nunique()),
                'fresh_sample_changes_exclusions':False,'tie_break':'funding descending, then grant ID ascending',
                'historical_decision_process_recovered':False},run/'review/review_manifest.json')
    return {'sample':ranked,'reconciliation':comparison}
