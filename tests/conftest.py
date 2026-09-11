import pytest
from lancet_funding.workflow import load_config
from lancet_funding.io import write_csv
from lancet_funding.reference import import_exclusions, import_groupings
from fake_dimensions import universe,references,FakeDimensions

@pytest.fixture
def inputs(tmp_path):
    cfg=load_config();cfg['start_year']=2010;cfg['analysis_start']=2010;cfg['main_figure_start']=2010
    for k in ['manual_exclusions','country_groupings','reference_status']:
        cfg[k]=str(tmp_path/'reference'/('status.json' if k=='reference_status' else k+'.csv'))
    data=universe();ex,groups=references(data)
    write_csv(ex,tmp_path/'review.csv');write_csv(groups,tmp_path/'groups.csv')
    import_exclusions(tmp_path/'review.csv',cfg,all_rows=True)
    import_groupings(tmp_path/'groups.csv',cfg)
    return cfg,FakeDimensions(data),tmp_path/'run'
