from pathlib import Path
import ast
import importlib.util
import json
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("candidate_guard", ROOT / "scripts/check_public.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_public_notebook_has_no_standalone_installers_or_unconditional_diagnostics():
    nb = json.loads((ROOT / "notebooks/01_dimensions_to_outputs.ipynb").read_text())
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        code = "".join(cell["source"])
        tree = ast.parse(code)
        forbidden = {"apply_recipient_unknown_fix.py", "apply_release_diagnostics_fix.py",
                     "scripts/diagnose_comparisons.py"}
        assert not any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and n.value in forbidden for n in ast.walk(tree))


def test_export_cell_imports_subprocess_directly():
    nb = json.loads((ROOT / "notebooks/01_dimensions_to_outputs.ipynb").read_text())
    source = next("".join(c["source"]) for c in nb["cells"]
                  if c["cell_type"] == "code" and "scripts/package_release.py" in "".join(c["source"]))
    assert "import subprocess" in source


@pytest.mark.parametrize("installer", ["apply_recipient_unknown_fix.py", "apply_release_diagnostics_fix.py"])
def test_guard_rejects_active_missing_installer(installer):
    nb = {"cells": [{"cell_type": "code", "source": [f'patch = ROOT / "{installer}"'],
                     "metadata": {}, "outputs": [], "execution_count": None}], "metadata": {}}
    issues = guard.file_issues("notebooks/example.ipynb", json.dumps(nb).encode())
    assert any("standalone repair installer" in issue for issue in issues)


@pytest.mark.parametrize("name", ["LICENSE", "LICENSE.md", "LICENSE.txt", "CITATION.cff"])
def test_owner_approved_license_and_citation_paths_allowed(name):
    assert guard.allowed(name)
    assert guard.file_issues(name, b"Owner-approved plain text goes here.\n") == []
