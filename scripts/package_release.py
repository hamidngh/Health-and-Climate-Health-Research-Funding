#!/usr/bin/env python3
"""Export only code/docs and clean notebooks, never local workbooks or run outputs."""
from pathlib import Path
import argparse
import zipfile
from check_public import ROOT,allowed,file_issues,clean_notebook

def package(output):
    files=[]
    for path in sorted(ROOT.rglob('*')):
        rel=path.relative_to(ROOT)
        if path.is_file() and allowed(rel):
            data=path.read_bytes()
            if path.suffix=='.ipynb':data=clean_notebook(data)
            issues=file_issues(str(rel),data)
            if issues:raise ValueError(str(rel)+': '+'; '.join(issues))
            files.append((rel,data))
    target=Path(output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for rel,data in files:z.writestr(str(Path('lancet-funding-reproducibility')/rel),data)
    return target

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='release/lancet-funding-code-only.zip')
    args=p.parse_args();print(package(args.output))
if __name__=='__main__':main()
