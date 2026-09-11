"""Default live extraction with query caching, bounded retries and coverage checks.

This module is tested against fake responses, NOT against an authenticated account.
Never treats a failed API request as a zero. Cached raw responses are private data.
"""
from __future__ import annotations
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import time
import pandas as pd
from .io import InputError, write_json, read_json, write_csv, stamp, environment, file_manifest, read_table, require_columns
from .queries import keyword_profile, category_filters, base_query, where, text_expression
from .scenarios import BY_ID, REPORT_IDS, COEFFICIENTS, membership

FIELDS = ['id','title','abstract','start_date','start_year','end_date','funding_usd',
          'funding_currency','funder_orgs','funder_org_name','funder_org_countries',
          'research_org_countries','research_orgs','category_for_2020','category_uoa',
          'category_hrcs_hc','category_hrcs_rac']

class Client:
    def __init__(self, cache: str | Path, *, dsl=None, retries: int=5, pause: float=2.2, timeout: float=180):
        self.cache = Path(cache); self.cache.mkdir(parents=True,exist_ok=True)
        self.retries = retries; self.pause = pause; self.queries = []
        self.query_log = self.cache.parent / 'query_log.jsonl'
        if dsl is None:
            key = os.environ.get('DIMENSIONS_API_KEY','').strip()
            if not key: raise InputError('Set DIMENSIONS_API_KEY in the environment. Credentials are not read from notebooks.')
            from .http import HttpDsl
            dsl = HttpDsl(key, os.environ.get('DIMENSIONS_ENDPOINT','https://app.dimensions.ai'), timeout=timeout)
        self.dsl = dsl

    def query(self, query: str) -> dict:
        endpoint = getattr(self.dsl,'endpoint','injected-test-transport')
        digest = hashlib.sha256((endpoint+'\n'+query).encode()).hexdigest()
        path = self.cache/(digest+'.json')
        self.queries.append({'sha256':digest,'query':query})
        with self.query_log.open('a',encoding='utf-8') as log:
            log.write(json.dumps({'time_utc':stamp(),'sha256':digest,'query':query,'cached':path.exists()})+'\n')
        if path.exists():
            obj = read_json(path)
            if obj.get('query') != query: raise InputError('Query cache mismatch.')
            return obj['response']
        last = None
        for attempt in range(self.retries):
            try:
                if self.pause: time.sleep(self.pause)
                result = self.dsl.query('set return_all_keys '+query)
                errors = getattr(result,'errors',None)
                obj = result.json
                if errors or obj.get('errors') or obj.get('_errors'):
                    raise InputError(f'API rejected query {digest[:12]}; inspect schema, permissions and query plan.')
                if obj.get('_warnings') or obj.get('warnings'):
                    raise InputError(f'API warning for query {digest[:12]}; resolve it before accepting results.')
                write_json({'query':query,'fetched_at_utc':stamp(),'response':obj},path)
                return obj
            except Exception as exc:
                # Do not print arbitrary service exceptions, which could include secrets.
                last = str(exc) if isinstance(exc, InputError) else type(exc).__name__
                if attempt+1 < self.retries and self.pause: time.sleep(self.pause*(2**attempt))
        raise InputError(f'Query {digest[:12]} failed after {self.retries} attempts: {last}. No partial result was accepted.')

    def total(self, base: str) -> int:
        obj = self.query(base+' return grants[id] limit 1')
        value = obj.get('_stats',{}).get('total_count')
        if value is None: raise InputError('Missing _stats.total_count; cannot certify retrieval completeness.')
        return int(value)

    def facet(self, base: str, field: str, *, funding: bool=False, limit: int=1000) -> list[dict]:
        query = base+f' return {field}'+(' aggregate funding' if funding else '')+f' limit {limit}'
        obj = self.query(query)
        if field not in obj: raise InputError(f'Missing expected facet {field}; refusing to treat a missing field as zero.')
        rows = obj[field]
        if len(rows) >= limit:
            raise InputError(f'{field} reached its {limit}-row limit. Partition the extraction; no truncated matrix was exported.')
        return rows

    def grants(self, base: str, *, ceiling: int=50000, page_size: int=1000) -> list[dict]:
        expected = self.total(base)
        if expected > ceiling: raise InputError('Slice exceeds download ceiling; use a smaller date slice.')
        rows = []
        for offset in range(0,expected,page_size):
            q = base+' return grants['+' + '.join(FIELDS)+f'] sort by id asc limit {page_size} skip {offset}'
            obj = self.query(q)
            if 'grants' not in obj: raise InputError('Missing grants response.')
            page = obj['grants']
            needed = min(page_size,expected-offset)
            if len(page) != needed: raise InputError('Short/long API page; possible snapshot drift or download limit.')
            rows.extend(page)
        if any(not isinstance(r.get('id'),str) or not r.get('id') for r in rows):
            raise InputError('Grant record missing its unique ID.')
        if len({r.get('id') for r in rows}) != expected:
            raise InputError('Duplicate/missing grant IDs across pages; repeat into a new cache.')
        return rows

    def grants_year(self, base: str, year: int) -> list[dict]:
        yearly = where(base,f'start_year = {year}')
        expected = self.total(yearly)
        if expected <= 50000: return self.grants(yearly)
        def split(lo: date, hi: date) -> list[dict]:
            q = where(yearly,f'start_date >= "{lo}" and start_date <= "{hi}"')
            n = self.total(q)
            if n <= 50000: return self.grants(q)
            if lo == hi: raise InputError(f'More than 50,000 grants on {lo}; add a non-date partition.')
            mid = lo+(hi-lo)//2
            return split(lo,mid)+split(mid+timedelta(days=1),hi)
        records = split(date(year,1,1),date(year,12,31))
        records += self.grants(where(yearly,'start_date is empty'))
        if len(records) != expected or len({r['id'] for r in records}) != expected:
            raise InputError('Year/date-slice mismatch; examine orphan and inconsistent dates.')
        return records

    def facet_year(self, base: str, year: int, field: str, *, limit: int=1000) -> list[dict]:
        """Split saturated facets into disjoint date intervals; never add overlaps."""
        yearly=where(base,f'start_year = {year}')
        def fetch(q):
            obj=self.query(q+f' return {field} aggregate funding limit {limit}')
            if field not in obj or not isinstance(obj[field],list):
                raise InputError(f'Missing or invalid {field} facet.')
            return obj[field]
        def merge(parts):
            result={}
            for rows in parts:
                for row in rows:
                    key=str(row['id'])
                    if key not in result:
                        result[key]=dict(row);result[key]['funding_usd']=funding(row)
                    else:
                        for attr in ['name','country_name']:
                            if result[key].get(attr)!=row.get(attr):
                                raise InputError('Facet metadata changed between disjoint slices.')
                        result[key]['count']+=row['count']
                        result[key]['funding_usd']+=funding(row)
            return [result[k] for k in sorted(result)]
        def split(lo,hi):
            q=where(yearly,f'start_date >= "{lo}" and start_date <= "{hi}"')
            rows=fetch(q)
            if len(rows)<limit:return rows
            if lo==hi:raise InputError('A one-day facet is saturated; explicit additional partition is required.')
            mid=lo+(hi-lo)//2
            return merge([split(lo,mid),split(mid+timedelta(days=1),hi)])
        rows=fetch(yearly)
        if len(rows)<limit:return rows
        dated=where(yearly,f'start_date >= "{year}-01-01" and start_date <= "{year}-12-31"')
        orphan=where(yearly,'start_date is empty')
        if self.total(dated)+self.total(orphan)!=self.total(yearly):
            raise InputError('Year/date inconsistency prevents safe facet partitioning.')
        missing=fetch(orphan)
        if len(missing)>=limit:raise InputError('Missing-date facet is saturated; no partial matrix accepted.')
        return merge([split(date(year,1,1),date(year,12,31)),missing])


def funding(item: dict) -> float:
    for key in ['funding_usd','funding']:
        if key in item and item[key] is not None: return float(item[key])
    raise InputError('The funding aggregate is missing. Inspect the response; do not replace it with zero.')

def combine_primitives(records: pd.DataFrame, keys: list[str], scenarios: list[str], completed: set[str]) -> pd.DataFrame:
    """Absent facet rows can be zero ONLY after every required query has completed."""
    need = {p for s in scenarios for p in COEFFICIENTS[s]}
    if not need <= completed: raise InputError(f'Missing completed primitives: {sorted(need-completed)}')
    columns = keys+['Scenario','Count','Funding_USD']
    if records.empty: return pd.DataFrame(columns=columns)
    index = pd.MultiIndex.from_frame(records[keys].drop_duplicates())
    series = {}
    for p in need:
        for metric in ['Count','Funding_USD']:
            series[p,metric] = records[records['Primitive']==p].groupby(keys,dropna=False)[metric].sum().reindex(index,fill_value=0)
    frames = []
    for s in scenarios:
        out = index.to_frame(index=False)
        out['Scenario'] = BY_ID[s].name
        for metric in ['Count','Funding_USD']:
            result = sum(w*series[p,metric] for p,w in COEFFICIENTS[s].items())
            if (result < -1e-5).any(): raise InputError('Negative inclusion-exclusion result: mixed snapshots, truncated facets or inconsistent queries.')
            out[metric] = result.to_numpy().clip(min=0) # only floating-point roundoff may reach here
        frames.append(out)
    return pd.concat(frames,ignore_index=True)[columns]

def extract(config: dict, *, client: Client | None=None) -> Path:
    out = Path(config['output_dir']); out.mkdir(parents=True,exist_ok=True)
    if (out/'extraction_manifest.json').exists():
        raise InputError('Completed extraction already exists; use a fresh output directory for a fresh snapshot.')
    profile = keyword_profile(config['search_profile'])
    scenarios = list(config.get('scenarios',REPORT_IDS))
    scenarios = [str(s).zfill(2) for s in scenarios]
    if any(s not in BY_ID for s in scenarios): raise InputError('Invalid canonical scenario ID.')
    years = list(range(int(config['start_year']),int(config['end_year'])+1))
    client = client or Client(out/'cache',pause=float(config.get('pause_seconds',2.2)), timeout=float(config.get('request_timeout_seconds',180)))
    cats_for = client.facet('search grants','category_for_2020')
    cats_uoa = client.facet('search grants','category_uoa',limit=200)
    cats = category_filters(cats_for,cats_uoa)
    write_json({'for_2020':cats_for,'uoa':cats_uoa,'filters':cats},out/'category_mapping.json')
    write_json(profile,out/'search_profile.json')
    manifest = []; by_id = {}; matched = {p:set() for p in 'HARU'}
    print('Stage 1: extracting four climate-plus-health base sets for all eleven numerators',flush=True)
    for primitive in 'HARU':
        q=base_query(primitive,profile,cats,climate=True)
        for year in years:
            print(f'  Numerator health set {primitive}, {year}',flush=True)
            for row in client.grants_year(q,year):
                key=row['id']
                if key in by_id and by_id[key]!=row:
                    raise InputError(f'Grant metadata changed between numerator sets ({key}); start a fresh run.')
                by_id[key]=row;matched[primitive].add(key)
    rows=[]
    for key in sorted(by_id):
        flags={p:key in matched[p] for p in 'HARU'}
        rows.append({**by_id[key],**{'api_match_'+p:int(v) for p,v in flags.items()}})
    columns=FIELDS+['api_match_'+p for p in 'HARU']
    union=pd.DataFrame(rows,columns=columns)
    for col in union:
        union[col]=union[col].map(lambda x:json.dumps(x,ensure_ascii=False) if isinstance(x,(list,dict)) else x)
    write_csv(union,out/'numerator_base_union.csv')
    masks={s:[] for s in scenarios}
    for row in rows:
        truth=membership(*(bool(row['api_match_'+p]) for p in 'HARU'))
        for s in scenarios:masks[s].append(truth[s])
    for s in scenarios:
        df=union.loc[masks[s]].copy() if len(union) else union.copy()
        p=out/'numerators'/(BY_ID[s].name+'.csv');write_csv(df,p)
        manifest.append({'scenario_id':s,'path':str(p.relative_to(out)),
                         'classification_exclusions_applied':True,'search_profile':profile['profile']})
    write_csv(pd.DataFrame(manifest),out/'scenario_manifest.csv')
    needed = sorted({p for s in scenarios for p in COEFFICIENTS[s]})
    for mode in ['funder','recipient']:
        if mode=='recipient' and not config.get('include_recipient',True): continue
        print(f'Stage 2: extracting twelve reusable {mode} denominator components',flush=True)
        records = []; completed = set()
        for primitive in needed:
            base = base_query(primitive,profile,cats,exclude_funders=(mode=='recipient'))
            for year in years:
                print(f'  Denominator {mode}, component {primitive}, {year}',flush=True)
                q = where(base,f'start_year = {year}')
                global_rows = client.facet(q,'start_year',funding=True,limit=100)
                if not global_rows:
                    global_rows=[{'id':year,'count':0,'funding':0.0}]
                for item in global_rows:
                    row={'Primitive':primitive,'Year':year,'Country':'GLOBAL_TOTAL','Count':item['count'],'Funding_USD':funding(item)}
                    if mode=='funder': row['Funder_Org']='GLOBAL_TOTAL'
                    records.append(row)
                field = 'funder_orgs' if mode=='funder' else 'research_org_countries'
                for item in client.facet_year(base,year,field):
                    row={'Primitive':primitive,'Year':year,'Country':item.get('country_name','Unknown') if mode=='funder' else item.get('name','Unknown'),
                         'Count':item['count'],'Funding_USD':funding(item)}
                    if mode=='funder': row['Funder_Org']=item.get('name','Unknown')
                    records.append(row)
            completed.add(primitive)
        keys=['Year','Country']+(['Funder_Org'] if mode=='funder' else [])
        write_csv(pd.DataFrame(records),out/f'{mode}_denominator_components.csv')
        df = combine_primitives(pd.DataFrame(records),keys,scenarios,completed)
        filename = 'Denominator_Funder_Level.csv' if mode=='funder' else 'Denominator_Matrix_Country_Level.csv'
        write_csv(df,out/filename)
    write_json(client.queries,out/'query_plan.json')
    write_json({'status':'complete_api_extraction_not_historical_verification','finished_utc':stamp(),
                'search_profile':profile,'years':years,'scenarios':scenarios,'environment':environment(),
                'query_count':len(client.queries),'unique_queries':len({q['sha256'] for q in client.queries}),
                'note':'The API is mutable. A completed extraction is not an immutable database snapshot or a report validation.',
                'files':file_manifest(list(out.glob('*.csv'))+list((out/'numerators').glob('*.csv')))},out/'extraction_manifest.json')
    return out

