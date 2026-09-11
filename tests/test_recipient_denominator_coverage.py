"""Regression tests for absent Unknown-recipient denominator buckets.

All grants and amounts are fabricated. No live extraction or credentials required.
"""
import numpy as np
import pandas as pd
import pytest
from lancet_funding.io import InputError, read_table, write_csv, sha256
from lancet_funding.cleaning import rebuild_recipient, normalise_cleaned
from lancet_funding.analysis import (check_master, add_ratios, attach_geography,
                                     scopes, equity_tables, methodological_bands)
from lancet_funding.denominator_coverage import (HEALTH, UNAVAILABLE,
    unavailable_mask, sum_metric_groups)
from lancet_funding.scenarios import REPORT_IDS, MAIN
from lancet_funding.workflow import _complete_years


def grant(gid='grant.synthetic.unknown', country=None, amount=100., year=2024, sid=MAIN):
    names=[] if country is None else [country]
    return dict(id=gid, scenario_id=sid, year=year, funding_usd=amount,
        funding_known=True, research_org_countries=[{'name':c} for c in names],
        research_orgs=[], funder_orgs=[])


def denominator(country='GLOBAL_TOTAL', count=10, amount=1000., year=2024, sid=MAIN):
    return dict(Scenario=sid, Year=year, Country=country, Count=count, Funding_USD=amount)


def exclusions(ids=None):
    return pd.DataFrame({'id':ids or ['grant.synthetic.excluded.not.in.numerator']})


def rebuilt():
    return rebuild_recipient(pd.DataFrame([grant()]),
        pd.DataFrame([denominator()]),exclusions())[0]


def test_unknown_missing_bucket_retains_both_funding_conventions_and_global():
    master=rebuilt()
    unknown=master.loc[master.country.eq('Unknown')].iloc[0]
    assert unknown[UNAVAILABLE] and not unknown.denominator_row_present
    assert unknown[HEALTH].isna().all()
    assert unknown.climate_count==1
    assert unknown.climate_funding_usd==unknown.climate_funding_usd_full==100.
    q=check_master(master,'recipient')
    assert q['unavailable_recipient_denominator_cells']==1
    assert q['max_fractional_country_funding_minus_global_abs']==0
    gl=master.loc[master.country.eq('GLOBAL_TOTAL')].iloc[0]
    assert gl.health_count==10 and gl.health_funding_usd==1000.


def test_roundtrip_preserves_explicit_unavailable_values(tmp_path):
    p=tmp_path/'master.csv';write_csv(rebuilt(),p)
    out=normalise_cleaned(read_table(p),'recipient')
    assert check_master(out,'recipient')['unavailable_recipient_denominator_cells']==1
    missing=add_ratios(out,'recipient').loc[out.country.eq('Unknown')].iloc[0]
    assert missing[['count_share','funding_share','count_share_legacy_zero','funding_share_legacy_zero']].isna().all()


@pytest.mark.parametrize('case',['named_absent','named_zero','global_absent','global_zero','unknown_observed_zero','named_overfunding'])
def test_real_inconsistencies_still_raise(case):
    named=case.startswith('named')
    num=pd.DataFrame([grant(country='Synthetic Country' if named else None)])
    den=[denominator()]
    if case=='global_absent':den=[denominator('Synthetic Country')]
    if case=='global_zero':den=[denominator(count=0,amount=0)]
    if case=='unknown_observed_zero':den.append(denominator('Unknown',count=0,amount=0))
    if case=='named_zero':den.append(denominator('Synthetic Country',count=0,amount=0))
    if case=='named_overfunding':den.append(denominator('Synthetic Country',count=5,amount=99.))
    master,_=rebuild_recipient(num,pd.DataFrame(den),exclusions())
    with pytest.raises(InputError,match='climate counts exceed|climate USD exceeds'):
        check_master(master,'recipient')


@pytest.mark.parametrize('bad',['named_flag','observed_flag','fake_zero','missing_flag'])
def test_cannot_use_coverage_flag_to_hide_other_failures(bad):
    master=rebuilt();i=master.index[master.country.eq('Unknown')][0]
    if bad=='named_flag':master.loc[i,'country']='Synthetic Country'
    elif bad=='observed_flag':master.loc[i,'denominator_row_present']=True
    elif bad=='fake_zero':master.loc[i,HEALTH]=0.
    else:master=master.drop(columns=UNAVAILABLE)
    with pytest.raises(InputError):normalise_cleaned(master,'recipient')


def test_true_unknown_denominator_remains_comparable():
    master,_=rebuild_recipient(pd.DataFrame([grant()]),
        pd.DataFrame([denominator(),denominator('Unknown',count=2,amount=200.)]),exclusions())
    check_master(master,'recipient')
    row=add_ratios(master,'recipient').set_index('country').loc['Unknown']
    assert not row[UNAVAILABLE] and row.denominator_row_present
    assert row.count_share==row.funding_share==.5


def test_exclusions_still_subtracted_after_extraction_and_once_globally():
    grants=pd.DataFrame([grant(),grant('grant.synthetic.excluded',amount=50.)])
    raw_den=pd.DataFrame([denominator(count=10,amount=1000.)]);unchanged=raw_den.copy(deep=True)
    master,qa=rebuild_recipient(grants,raw_den,exclusions(['grant.synthetic.excluded']))
    pd.testing.assert_frame_equal(raw_den,unchanged)
    check_master(master,'recipient');gl=master.set_index('country').loc['GLOBAL_TOTAL']
    assert gl.health_count==9 and gl.health_funding_usd==950.
    assert gl.climate_count==1 and gl.climate_funding_usd==100.
    assert master.set_index('country').loc['Unknown',HEALTH].isna().all()
    assert qa['manual_exclusions_scope']=='all_retained_scenarios'


def test_unknown_deductions_still_reject_invalid_global_subtraction():
    grants=pd.DataFrame([grant(),grant('grant.synthetic.excluded',amount=500.)])
    den=pd.DataFrame([denominator(count=1,amount=200.)])
    with pytest.raises(InputError,match='manual deductions exceed'):
        rebuild_recipient(grants,den,exclusions(['grant.synthetic.excluded']))


def test_unavailable_unknown_bucket_covers_years_with_no_unknown_climate_grant():
    master,_=rebuild_recipient(pd.DataFrame([grant()]),
        pd.DataFrame([denominator(year=2023),denominator(year=2024)]),exclusions())
    u=master.loc[master.country.eq('Unknown')].set_index('year')
    assert set(u.index)=={2023,2024}
    assert u.loc[2023,'climate_count']==0 and u[HEALTH].isna().all().all()
    assert check_master(master,'recipient')['unavailable_recipient_denominator_cells']==2


def test_mixed_unmapped_scope_cannot_use_partial_health_denominator():
    grants=pd.DataFrame([grant(),grant('grant.synthetic.named',country='Synthetic Country',amount=50.)])
    master,_=rebuild_recipient(grants,pd.DataFrame([denominator(),denominator('Synthetic Country')]),exclusions())
    master,_=attach_geography(master,None)
    out=scopes(master,'recipient')
    un=out.loc[out.scope_system.eq('HDI') & out.scope.eq('Unmapped')].iloc[0]
    assert un.climate_count==2 and un.climate_funding_usd==150.
    assert un[HEALTH].isna().all() and np.isnan(un.funding_share_legacy_zero)
    known=out.loc[out.scope_system.eq('Country') & out.scope.eq('Synthetic Country')].iloc[0]
    assert known.health_count==10 and known.funding_share==.05
    eq=equity_tables(master,'recipient',[{'label':'test','start':2024,'end':2024}])
    amount=eq.loc[eq.scope_system.eq('HDI') & eq.metric.eq('climate_funding_usd')].iloc[0]
    assert amount.amount==150. and amount.share_of_global_pct==100.


def test_completion_bands_and_periods_do_not_refill_missing_denominators():
    rows=[];den=[]
    for s in REPORT_IDS:
        rows.append(grant(sid=s));den.append(denominator(sid=s))
    master,_=rebuild_recipient(pd.DataFrame(rows),pd.DataFrame(den),exclusions())
    mapped,_=attach_geography(master,None)
    annual=_complete_years(scopes(mapped,'recipient'),1990,2025)
    unknown=annual.loc[annual.scope_system.eq('Country') & annual.scope.eq('Unknown')]
    assert len(unknown)==36*11 and unknown[HEALTH].isna().all().all()
    assert unknown.count_share_legacy_zero.isna().all()
    assert unknown.loc[unknown.year.eq(1990),'climate_count'].eq(0).all()
    b=methodological_bands(annual)
    ub=b.loc[b.scope_system.eq('Country') & b.scope.eq('Unknown') & b.metric.isin(['count_share','funding_share','health_count','health_funding_usd'])]
    assert ub[['main','band_min','band_max','alternative_min','alternative_max','all_11_min','all_11_max']].isna().all().all()
    p=sum_metric_groups(unknown,['scenario_id'],['climate_count','climate_funding_usd']+HEALTH)
    assert p[HEALTH].isna().all().all() and p.climate_count.eq(1).all()
    gl=annual.loc[annual.scope_system.eq('Global') & annual.year.eq(2024)]
    assert gl.count_share.eq(.1).all() and gl.funding_share.eq(.1).all()


def test_full_mock_pipeline_with_unknown_uses_existing_extraction(inputs):
    from lancet_funding.dimensions import Client
    from lancet_funding.workflow import extract_stage, analyse_stage
    cfg,api,run=inputs
    for g in api.rows:
        if g['id'].endswith('.1') or g['id']=='grant.synthetic.2016.7':
            g['research_org_countries']=[];g['research_orgs']=[]
    client=Client(run/'raw/cache',dsl=api,pause=0)
    extract_stage(cfg,run,client=client)
    raw_before={str(p.relative_to(run/'raw')):sha256(p) for p in (run/'raw').rglob('*') if p.is_file()}
    inputs_before={key:sha256(cfg[key]) for key in ['manual_exclusions','country_groupings','reference_status']}
    calls_before=len(api.calls)
    manifest=analyse_stage(cfg,run,figures=False,data_label='synthetic')
    assert manifest['status']=='complete_analysis_not_historical_validation'
    assert len(api.calls)==calls_before
    assert raw_before=={str(p.relative_to(run/'raw')):sha256(p) for p in (run/'raw').rglob('*') if p.is_file()}
    assert inputs_before=={key:sha256(cfg[key]) for key in inputs_before}
    cov=read_table(run/'results/tables/recipient_denominator_coverage.csv')
    assert len(cov) and cov.country.eq('Unknown').all() and cov[HEALTH].isna().all().all()
    main=read_table(run/'results/tables/main_global.csv')
    f=main.loc[main.kind.eq('funder') & main.year.eq(2016)].iloc[0]
    r=main.loc[main.kind.eq('recipient') & main.year.eq(2016)].iloc[0]
    assert f.climate_count==7 and r.climate_count==5
    assert f.climate_funding_usd==pytest.approx((100+50+30+20+45*2)*1.06)
    assert r.climate_funding_usd==pytest.approx((100+50+30+20+45)*1.06)


@pytest.mark.parametrize('metric',['health_count','funding_share'])
def test_unavailable_only_plot_is_labelled_not_a_zero_line(tmp_path,metric):
    from lancet_funding.plots import panel
    master,_=attach_geography(rebuilt(),None)
    data=scopes(master,'recipient')
    data=data.loc[data.scope_system.eq('Country') & data.scope.eq('Unknown')]
    name='synthetic_unknown_'+metric
    path=panel(data,metric,tmp_path,name,'Synthetic Unknown recipient bucket',lo=2024,hi=2024,synthetic=True)
    assert path==name+'.png'
    assert 'Comparable health denominator unavailable' in (tmp_path/(name+'.svg')).read_text()
    assert read_table(tmp_path/(name+'_data.csv'))['value'].isna().all()
