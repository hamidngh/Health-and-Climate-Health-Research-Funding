"""Publication excludes known OS metadata without disabling the safety checks."""
import json
from pathlib import Path
import sys
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_public
import package_release


@pytest.mark.parametrize('relative', [
    'src/.DS_Store', 'src/lancet_funding/.DS_Store', 'docs/._README.md',
    'notebooks/__MACOSX/example', 'tests/Thumbs.db', 'config/desktop.ini',
])
def test_known_metadata_is_not_exportable(relative):
    assert not check_public.allowed(relative)
    # The staged/index scanner must still reject an accidentally tracked file.
    assert check_public.file_issues(relative, b'\xffmetadata')


def test_export_omits_metadata_but_keeps_source(tmp_path, monkeypatch):
    source = tmp_path / 'project'
    (source / 'src').mkdir(parents=True)
    (source / 'src/test.py').write_text('print(1)\n')
    for name in ['.DS_Store', '._test.py', 'Thumbs.db', 'desktop.ini']:
        (source / 'src' / name).write_bytes(b'\xff\xfeOS metadata')
    monkeypatch.setattr(package_release, 'ROOT', source)
    target = package_release.package(tmp_path / 'public.zip')
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ['lancet-funding-reproducibility/src/test.py']
    assert (source / 'src/.DS_Store').exists()  # no deletion required


@pytest.mark.parametrize('name,content', [('bad.json', b'\xff'), ('data.csv', b'a,b\n1,2\n')])
def test_non_metadata_binary_or_data_still_stops_export(tmp_path, monkeypatch, name, content):
    source = tmp_path / 'project'
    (source / 'src').mkdir(parents=True)
    (source / 'src' / name).write_bytes(content)
    monkeypatch.setattr(package_release, 'ROOT', source)
    with pytest.raises(ValueError):
        package_release.package(tmp_path / 'public.zip')


def test_local_only_cells_removed_from_export_copy_only():
    notebook = {'cells': [
        {'cell_type': 'code', 'metadata': {'tags': ['local-only']},
         'source': ['print("local repair")'], 'outputs': [], 'execution_count': None},
        {'cell_type': 'code', 'metadata': {}, 'source': ['print(2)'],
         'outputs': [{'text': 'private'}], 'execution_count': 1},
    ], 'metadata': {}}
    original = json.dumps(notebook).encode()
    clean = json.loads(check_public.clean_notebook(original))
    assert len(clean['cells']) == 1
    assert clean['cells'][0]['source'] == ['print(2)']
    assert clean['cells'][0]['outputs'] == []
    assert clean['cells'][0]['execution_count'] is None
    assert json.loads(original) == notebook
