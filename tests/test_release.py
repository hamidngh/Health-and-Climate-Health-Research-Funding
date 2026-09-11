import importlib.util
from pathlib import Path
import json
import nbformat
from lancet_funding.workflow import ROOT

spec=importlib.util.spec_from_file_location('publication_check',ROOT/'scripts/check_public.py')
check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)

def test_public_allowlist_is_safe():
    count,issues=check.scan()
    assert count>40 and issues==[]

def test_private_paths_and_literal_keys_blocked():
    assert check.file_issues('.env',b'DIMENSIONS_API_KEY=synthetic-secret-never-publish\n')
    assert check.file_issues('private/report.docx',b'not-a-real-report')
    assert check.file_issues('src/test.py',b'api' + b'_key="' + b'A'*32 + b'"')
    assert check.file_issues('.env.example',b'DIMENSIONS_API_KEY=XXXXXXX\n')==[]

def test_walkthrough_valid_and_unexecuted():
    p=ROOT/'notebooks/01_dimensions_to_outputs.ipynb'
    nb=nbformat.read(p,as_version=4);nbformat.validate(nb)
    for cell in nb.cells:
        if cell.cell_type=='code':
            assert cell.execution_count is None and not cell.outputs
            compile(cell.source,'walkthrough-cell','exec')
