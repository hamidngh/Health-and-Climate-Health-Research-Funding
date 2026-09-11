from pathlib import Path
import importlib.util
import json
import sys
import zipfile
import numpy as np
import pandas as pd
import pytest
from lancet_funding.scenarios import REPORT_IDS,MAIN,membership,BY_ID
from lancet_funding.analysis import add_ratios,methodological_bands,active_funders
from lancet_funding.review import top_five,tag_grants
from lancet_funding.comparison import compare_bands
from lancet_funding.workflow import ROOT,publication_legacy
from lancet_funding.excel import export_xlsx
from lancet_funding.io import InputError,write_csv,read_table
from lancet_funding.demo import universe,references
from lancet_funding.reference import prepare_local_inputs


def band_input():
    rows=[]
    for index,s in enumerate(REPORT_IDS):
        rows.append(dict(scenario_id=s,year=2024,kind='funder',scope_system='Global',scope='Global',
                         climate_count=index+1,health_count=100*(index+1),
                         climate_funding_usd=(index+1)*10.,health_funding_usd=(index+1)*100.))
    return add_ratios(pd.DataFrame(rows),'funder')


def test_band_uses_scenario_ratios_not_extrema_ratios():
    data=band_input();b=methodological_bands(data)
    funding=b.set_index('metric').loc['funding_share']
    assert funding.band_min==pytest.approx(.1)==funding.band_max
    assert funding.band_min != data.climate_funding_usd.min()/data.health_funding_usd.max()
    assert b.n_scenarios.eq(11).all() and b.n_alternatives.eq(10).all()


def test_all11_and_ten_alternative_bounds_are_distinct():
    data=band_input();i=data.index[data.scenario_id.eq(MAIN)][0]
    data.loc[i,'climate_funding_usd']=600.
    data=add_ratios(data,'funder')
    a=methodological_bands(data).set_index('metric').loc['funding_share']
    b=methodological_bands(data,policy='alternatives_only').set_index('metric').loc['funding_share']
    assert a.band_max>a.alternative_max
    assert b.band_max==b.alternative_max
    assert a.all_11_max==b.all_11_max


def test_zero_handling_is_explicit_not_a_silent_ratio_change():
    data=band_input();data.loc[0,['climate_count','health_count','climate_funding_usd','health_funding_usd']]=0
    data=add_ratios(data,'funder')
    assert np.isnan(data.loc[0,'funding_share'])
    a=methodological_bands(data).set_index('metric').loc['funding_share']
    b=methodological_bands(data,zero_policy='undefined').set_index('metric').loc['funding_share']
    assert a.band_min==0 and b.band_min==pytest.approx(.1)
    assert a.n_defined_scenarios==10
    assert np.isnan(data.loc[0,'funding_share'])


def test_band_requires_all_eleven_and_unique_keys():
    data=band_input()
    with pytest.raises(InputError):methodological_bands(data.iloc[:-1])
    with pytest.raises(InputError):methodological_bands(pd.concat([data,data.iloc[[0]]]))


def test_topfive_matches_original_rule_without_mutating_input():
    data=pd.DataFrame(universe([1990,2024,2025]))
    data=data[[membership(r['_h'],r['_a'],r['_r'],r['_u'])[MAIN] and r['_climate'] for r in data.to_dict('records')]].copy()
    before=data.copy(deep=True)
    selected=top_five(data)
    expected=pd.concat([sub.nlargest(5,'funding_usd') for _,sub in data.groupby(pd.to_datetime(data.start_date).dt.year)])
    assert set(selected.id)==set(expected.id)
    assert selected.groupby('year').size().eq(5).all()
    assert selected.year.min()==1990 and selected.year.max()==2025
    pd.testing.assert_frame_equal(before,data)


def test_topfive_can_change_with_new_grants_and_uses_deterministic_ties():
    data=pd.DataFrame({'id':[f'grant.synthetic.{i}' for i in range(7)],'start_date':['2024-01-01']*7,'funding_usd':[10.]*7})
    first=top_five(data.sample(frac=1,random_state=12))
    assert first.id.tolist()==sorted(data.id)[:5]
    data.loc[6,'funding_usd']=50.
    assert data.loc[6,'id'] in set(top_five(data).id)
    assert data.loc[6,'id'] not in set(first.id)
    assert first.purpose.str.contains('NOT an automatic exclusion').all()


def test_review_diagnostic_regex_does_not_replace_api_membership():
    row=universe([2024])[0]
    # No keywords are present in the fabricated prose, but the API flags remain true.
    row.update({f'api_match_{s}':1 for s in 'HARU'})
    tagged=tag_grants(pd.DataFrame([row]))
    assert tagged.matched_health_terms.iloc[0]==''
    assert tagged.api_match_H.iloc[0]==1


def test_active_funder_cohort_uses_all_retained_approaches():
    df=band_input();df['funder_org']='A'
    extra=df[df.scenario_id.eq('01')].copy();extra['funder_org']='B'
    _,roster=active_funders(pd.concat([df,extra]),2024)
    assert set(roster.funder_org)=={'A','B'}


def test_private_workbook_setup_and_changed_input_gate(tmp_path):
    rows=universe([2016,2020]);ex,group=references(rows)
    ex_path=tmp_path/'private/top5_values.xlsx';group_path=tmp_path/'private/groupings.xlsx'
    export_xlsx({'data':ex},ex_path);export_xlsx({'data':group},group_path)
    cfg={'historical_exclusions_workbook':str(ex_path),'country_groupings_workbook':str(group_path),
         'manual_exclusions':str(tmp_path/'private/ref/ex.csv'),'country_groupings':str(tmp_path/'private/ref/group.csv'),
         'reference_status':str(tmp_path/'private/ref/status.json')}
    status=prepare_local_inputs(cfg)
    assert status['manual_exclusions']['rows']==2
    assert status['country_groupings']['rows']==4
    assert prepare_local_inputs(cfg)==status
    ex.loc[0,'funding_usd']+=1;export_xlsx({'data':ex},ex_path)
    with pytest.raises(InputError):prepare_local_inputs(cfg)


def test_band_comparison_detects_changed_endpoint(tmp_path):
    data=band_input();old=publication_legacy(data,'funder');bands=methodological_bands(data)
    write_csv(old,tmp_path/'baseline.csv');write_csv(bands,tmp_path/'bands.csv')
    assert compare_bands(tmp_path/'baseline.csv',tmp_path/'bands.csv',tmp_path/'test',start_year=2024,end_year=2024)['status']=='PASS'
    bands.loc[bands.metric.eq('climate_count'),'all_11_max']+=1;write_csv(bands,tmp_path/'bands.csv')
    assert compare_bands(tmp_path/'baseline.csv',tmp_path/'bands.csv',tmp_path/'test',start_year=2024,end_year=2024)['status']=='DIFFERENCES_FOUND'


def test_code_export_strips_notebook_outputs_and_excludes_data(tmp_path,monkeypatch):
    sys.path.insert(0,str(ROOT/'scripts'))
    import package_release
    source=tmp_path/'working';(source/'notebooks').mkdir(parents=True);(source/'private').mkdir()
    (source/'runs').mkdir();(source/'README.md').write_text('Code-only test repository')
    notebook={'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':[{'cell_type':'code','source':['print(1)'],
              'metadata':{},'execution_count':1,'outputs':[{'output_type':'stream','name':'stdout','text':'private output'}]}]}
    encoded=json.dumps(notebook).encode();p=source/'notebooks/test.ipynb';p.write_bytes(encoded)
    (source/'private/top5_values.xlsx').write_bytes(b'private source workbook')
    (source/'runs/data.csv').write_text('private data')
    (source/'.env').write_text('DIMENSIONS_API_KEY=synthetic-private-key')
    monkeypatch.setattr(package_release,'ROOT',source)
    zpath=package_release.package(tmp_path/'clean.zip')
    with zipfile.ZipFile(zpath) as z:
        names=z.namelist()
        assert len(names)==2
        assert not any('private' in n or 'runs' in n or n.endswith('.env') for n in names)
        clean=json.loads(z.read('lancet-funding-reproducibility/notebooks/test.ipynb'))
        assert clean['cells'][0]['outputs']==[] and clean['cells'][0]['execution_count'] is None
    assert p.read_bytes()==encoded


def test_historical_duplicate_and_missing_amounts_are_preserved(tmp_path):
    from lancet_funding.reference import import_exclusions
    from lancet_funding.cleaning import load_exclusions,rebuild_funder,load_numerators
    from lancet_funding.io import read_json
    rows=universe([2016,2020]);ex,groups=references(rows)
    ex.loc[1,'funding_usd']=np.nan
    ex=pd.concat([ex,ex.iloc[[0]]],ignore_index=True)
    source=tmp_path/'source.csv';write_csv(ex,source)
    cfg={'manual_exclusions':str(tmp_path/'ex.csv'),'reference_status':str(tmp_path/'status.json')}
    assert import_exclusions(source,cfg,all_rows=True)==3
    current=load_exclusions(cfg['manual_exclusions']);assert len(current)==3 and current.id.nunique()==2
    assert current.funding_usd.sum()==pytest.approx(2*ex.iloc[0].funding_usd)
    status=read_json(cfg['reference_status'])['manual_exclusions']
    assert status['duplicate_rows_preserved']==1 and status['missing_funding_rows_summed_as_zero']==1
    grant=rows[0].copy();grant.update(scenario_id='14',year=2016,funding_known=True)
    den=pd.DataFrame([dict(Scenario='14',Year=2016,Country='United States',Funder_Org=ex.iloc[0].funder_org_name,Count=10,Funding_USD=5000.)])
    rebuilt,qa=rebuild_funder(pd.DataFrame([grant]),den,current)
    cell=rebuilt.iloc[0]
    assert cell.health_count==9
    assert cell.health_funding_usd==pytest.approx(5000-2*ex.iloc[0].funding_usd)


def test_conflicting_repeated_ids_are_not_silently_reconciled(tmp_path):
    from lancet_funding.reference import import_exclusions
    ex,_=references(universe([2016,2020]));ex=pd.concat([ex,ex.iloc[[0]]],ignore_index=True)
    ex.loc[2,'funding_usd']+=1
    source=tmp_path/'bad.csv';write_csv(ex,source)
    cfg={'manual_exclusions':str(tmp_path/'ex.csv'),'reference_status':str(tmp_path/'status.json')}
    with pytest.raises(InputError):import_exclusions(source,cfg,all_rows=True)


def test_funder_coverage_keeps_explicit_1990_to_2025_zero_years():
    from lancet_funding.analysis import coverage_scopes
    data=band_input();data['funder_org']='Synthetic';data['mapped_country']='Synthetic'
    data['who_region']='Synthetic';data['lancet_region']='Synthetic';data['hdi_group']='Synthetic'
    out=coverage_scopes(data,year_start=1990,year_end=2025)
    gl=out[out.scope_system.eq('Global')]
    assert set(gl.year)==set(range(1990,2026)) and len(gl)==36*11
    assert gl.loc[gl.year.eq(1990),'health_funder_count'].eq(0).all()
