from types import SimpleNamespace
import pandas as pd
import pytest
from lancet_funding.dimensions import Client, combine_primitives
from lancet_funding.io import InputError
from lancet_funding.queries import category_filters

class Fake:
    def __init__(self,response):self.response=response;self.calls=0
    def query(self,q):
        self.calls+=1
        obj=self.response(q) if callable(self.response) else self.response
        return SimpleNamespace(json=obj,errors=None)

def test_query_cache_avoids_second_request(tmp_path):
    api=Fake({'grants':[],'_stats':{'total_count':0}})
    c=Client(tmp_path,dsl=api,pause=0)
    assert c.total('search grants')==0
    assert c.total('search grants')==0 and api.calls==1

def test_missing_total_is_not_zero(tmp_path):
    c=Client(tmp_path,dsl=Fake({'grants':[]}),pause=0)
    with pytest.raises(InputError):c.total('search grants')

def test_facet_limit_fails_closed(tmp_path):
    c=Client(tmp_path,dsl=Fake({'funder_orgs':[{'name':'a'},{'name':'b'}]}),pause=0)
    with pytest.raises(InputError):c.facet('search grants','funder_orgs',limit=2)

def test_errors_retry_then_stop(tmp_path):
    fake=Fake({'errors':['bad query']})
    c=Client(tmp_path,dsl=fake,retries=3,pause=0)
    with pytest.raises(InputError):c.query('bad query')
    assert fake.calls==3 and not list(tmp_path.glob('*.json'))

def test_grant_pages_are_count_checked(tmp_path):
    def response(q):
        if 'grants[id] limit 1' in q:return {'grants':[{'id':'s0'}],'_stats':{'total_count':5}}
        offset=int(q.split(' skip ')[1])
        return {'grants':[{'id':f's{i}'} for i in range(offset,min(offset+2,5))]}
    c=Client(tmp_path,dsl=Fake(response),pause=0)
    assert len(c.grants('search grants',page_size=2))==5

def test_duplicate_pages_are_rejected(tmp_path):
    def response(q):
        if 'grants[id] limit 1' in q:return {'grants':[{'id':'s0'}],'_stats':{'total_count':2}}
        return {'grants':[{'id':'s0'}]}
    c=Client(tmp_path,dsl=Fake(response),pause=0)
    with pytest.raises(InputError):c.grants('search grants',page_size=1)

def test_missing_primitive_does_not_become_zero():
    records=pd.DataFrame([{'Year':2024,'Country':'Synthetic','Primitive':'HAR','Count':1,'Funding_USD':10}])
    with pytest.raises(InputError):combine_primitives(records,['Year','Country'],['14'],{'HAR'})

def test_four_way_core_subtracted_three_times():
    records=pd.DataFrame([{'Year':2024,'Country':'Synthetic','Primitive':p,'Count':1,'Funding_USD':10}
                          for p in ['HAR','HAU','HRU','ARU','HARU']])
    out=combine_primitives(records,['Year','Country'],['14'],set(records.Primitive))
    assert out.Count.iloc[0]==1 and out.Funding_USD.iloc[0]==10

def test_incomplete_category_mapping_is_fatal():
    with pytest.raises(InputError):category_filters([],[])


def test_no_grant_id_is_rejected(tmp_path):
    def response(q):
        if 'grants[id] limit 1' in q:return {'grants':[{}],'_stats':{'total_count':1}}
        return {'grants':[{}]}
    with pytest.raises(InputError):Client(tmp_path,dsl=Fake(response),pause=0).grants('search grants')


def test_warning_is_not_ignored(tmp_path):
    with pytest.raises(InputError):Client(tmp_path,dsl=Fake({'grants':[],'_warnings':['incomplete']}),pause=0,retries=1).total('search grants')


def test_negative_overlap_is_rejected():
    records=pd.DataFrame([{'Year':2024,'Country':'Synthetic','Primitive':p,'Count':1 if p=='HARU' else 0,'Funding_USD':10 if p=='HARU' else 0}
                          for p in ['HAR','HAU','HRU','ARU','HARU']])
    with pytest.raises(InputError):combine_primitives(records,['Year','Country'],['14'],set(records.Primitive))


def test_safe_facet_time_partition(tmp_path):
    from fake_dimensions import FakeDimensions,universe
    from lancet_funding.queries import base_query,category_filters,keyword_profile
    from fake_dimensions import categories
    rows=universe([2024]);rows=rows[:3]
    for row,month in zip(rows,[1,5,10]):row['start_date']=f'2024-{month:02}-01'
    fake=FakeDimensions(rows);client=Client(tmp_path,dsl=fake,pause=0)
    q=base_query('HAU',keyword_profile('historical_notebook'),category_filters(*categories()))
    out=client.facet_year(q,2024,'funder_orgs',limit=2)
    assert sum(r['count'] for r in out)==2
    assert len(out)==2
