from itertools import product
import json
import numpy as np
import pandas as pd
import pytest
from lancet_funding.scenarios import membership,REPORT_IDS,COEFFICIENTS,PRIMITIVES,canonical
from lancet_funding.io import InputError,infer_year,parse_list,read_table,sha256
from lancet_funding.analysis import ratio,attach_geography,equity_tables
from lancet_funding.queries import keyword_profile,base_query,category_filters
from lancet_funding.cleaning import recipient_aggregate,classification_veto,rebuild_funder,rebuild_recipient
from lancet_funding.reference import check_references,import_exclusions
from lancet_funding.http import HttpDsl
from fake_dimensions import categories

@pytest.mark.parametrize('bits',list(product([False,True],repeat=4)))
def test_any_three_truth_table(bits):
    h,a,r,u=bits
    actual=membership(*bits)
    expected={'01':h,'02':a,'03':r,'04':u,'05':h and a,'06':h and r,'07':h and u,
              '11':h or a,'12':h or r,'13':h or u,'14':sum(bits)>=3}
    assert actual==expected
    flags=dict(zip('HARU',bits))
    for sid,coefficients in COEFFICIENTS.items():
        assert sum(w*int(all(flags[t] for t in term)) for term,w in coefficients.items())==int(actual[sid])
    assert sum([h and a and r,h and a and u,h and r and u,a and r and u])-3*int(all(bits))==int(sum(bits)>=3)

def test_only_final_eleven_exist():
    assert REPORT_IDS==('01','02','03','04','05','06','07','11','12','13','14')
    assert len(PRIMITIVES)==12 and list(COEFFICIENTS)==list(REPORT_IDS)
    assert canonical('Main Search Approach')=='14'
    assert canonical('Scenario_01_CKW&HKW')=='01'
    for excluded in ['08','09','10']:
        with pytest.raises(InputError):canonical(excluded)

def test_query_preserves_historical_literals():
    profile=keyword_profile('historical_notebook');cats=category_filters(*categories())
    assert profile['search_index'] is None
    assert '"heat?wave"' in profile['climate_terms']
    assert '"health?care"' in profile['health_terms']
    assert not profile['climate_exclusions']
    with pytest.raises(InputError):keyword_profile('appendix_specification')
    a=base_query('HAR',profile,cats,climate=True)
    b=base_query('HAR',profile,cats)
    c=base_query('ARU',profile,cats,climate=True)
    assert 'climate change' in a and 'public health' in a
    assert 'climate change' not in b and 'public health' in b
    assert 'climate change' in c and 'public health' not in c
    assert 'not category_for_2020' in a

def test_zero_denominator_is_undefined():
    values=ratio([0,1,2],[0,0,4]);assert np.isnan(values[:2]).all() and values[2]==.5

def test_missing_date_is_not_silently_changed():
    with pytest.raises(InputError):infer_year(pd.DataFrame({'start_date':[None],'start_year':[2024]}))

def test_classification_subcodes_excluded():
    data=pd.DataFrame({'category_for_2020':[[{'name':'300901 Synthetic'}],[{'name':'3201 Synthetic'}]]})
    assert classification_veto(data).tolist()==[True,False]

def test_recipient_full_count_fractional_usd():
    data=pd.DataFrame([{'id':'synthetic','scenario_id':'14','year':2024,'funding_usd':100.,'Constructed_Recipient_Country':'A; B'}])
    out=recipient_aggregate(data).set_index('country')
    assert out.loc['GLOBAL_TOTAL','climate_count']==1
    assert out.loc[['A','B'],'climate_count'].sum()==2
    assert out.loc[['A','B'],'climate_funding_usd'].sum()==100.
    assert out.loc[['A','B'],'climate_funding_usd_full'].sum()==200.

@pytest.mark.parametrize('value',['', 'XXXXXXX', 'xxxxxxx'])
def test_placeholder_rejected(value):
    with pytest.raises(InputError):HttpDsl(value,'https://app.dimensions.ai')

@pytest.mark.parametrize('url',['http://app.dimensions.ai','https://attacker.example','https://app.dimensions.ai@attacker.example','https://app.dimensions.ai/path'])
def test_credentials_not_sent_to_arbitrary_endpoint(url):
    with pytest.raises(InputError):HttpDsl('synthetic-key',url)

def test_reference_gate_and_hash(inputs):
    cfg,_,_=inputs
    assert check_references(cfg)['manual_exclusions']['rows']==2
    from pathlib import Path
    with Path(cfg['manual_exclusions']).open('a') as f:f.write('\n')
    with pytest.raises(InputError):check_references(cfg)

def test_sample_not_automatically_exclusions(inputs,tmp_path):
    cfg,_,_=inputs
    with pytest.raises(InputError):import_exclusions(tmp_path/'unknown.xlsx',cfg,all_rows=False)


def test_legacy_missing_date_omission_is_audited(tmp_path):
    from lancet_funding.cleaning import load_numerators
    from lancet_funding.io import write_csv
    data=pd.DataFrame({'id':['grant.synthetic.good','grant.synthetic.missing'],
                       'start_date':['2024-01-01',None],'start_year':[2024,2024],'funding_usd':[10,20]})
    path=tmp_path/'grants.csv';write_csv(data,path)
    manifest=tmp_path/'manifest.csv'
    write_csv(pd.DataFrame([{'scenario_id':'14','path':str(path),'classification_exclusions_applied':True}]),manifest)
    grants,_=load_numerators(manifest)
    assert grants.id.tolist()==['grant.synthetic.good']
    assert grants.attrs['omitted_missing_start_date'][0]['id']=='grant.synthetic.missing'
