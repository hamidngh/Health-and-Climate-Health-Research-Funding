#!/usr/bin/env python3
"""Clear execution outputs from publication notebooks. Does not remove literal keys."""
from check_public import ROOT,clean_notebook
for path in (ROOT/'notebooks').glob('*.ipynb'):
    path.write_bytes(clean_notebook(path.read_bytes()))
    print('Cleared outputs:',path.name)
