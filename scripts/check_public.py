#!/usr/bin/env python3
"""Fail closed on staged data/credentials/outputs. This is not a full secret scanner."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
DIRS={'src','config','docs','scripts','tests','notebooks','.github','.githooks'}
FILES={'LICENSE','LICENSE.md','LICENSE.txt','CITATION.cff','.env.example','.gitignore','.gitattributes','README.md','LICENSE_NOTICE.md','CITATION.md',
       'SECURITY.md','CONTRIBUTING.md','CHANGELOG.md','pyproject.toml','requirements.txt',
       'requirements-dev.txt','requirements-notebook.txt','run_pipeline.py'}

def allowed(path):
    p=Path(path)
    # The exporter walks the filesystem directly; .gitignore is not consulted.
    # Ignore known OS metadata only, never arbitrary binary files or all hidden files.
    ignored = {'__pycache__', '.pytest_cache', '.ipynb_checkpoints',
               '.DS_Store', '__MACOSX', 'Thumbs.db', 'desktop.ini'}
    if any(x in ignored or x.startswith('._') for x in p.parts):return False
    return (len(p.parts)>1 and p.parts[0] in DIRS) or str(p) in FILES

def clean_notebook(data):
    nb=json.loads(data.decode('utf-8'))
    # Explicitly tagged local repair/diagnostic cells are omitted from the exported
    # copy only. The working notebook is never modified by the exporter.
    nb['cells'] = [cell for cell in nb.get('cells', [])
                   if 'local-only' not in cell.get('metadata', {}).get('tags', [])]
    for cell in nb['cells']:
        if cell.get('cell_type')=='code':cell['outputs']=[];cell['execution_count']=None
        cell['metadata']={}
        cell.pop('attachments',None)
    nb['metadata']={k:v for k,v in nb.get('metadata',{}).items() if k in {'kernelspec','language_info'}}
    return (json.dumps(nb,indent=1,ensure_ascii=False)+'\n').encode('utf-8')

def file_issues(path,data):
    p=Path(path);problems=[]
    if not allowed(path):problems.append('path is outside the code-only allowlist')
    if p.suffix.lower() not in {'.py','.md','.json','.ipynb','.yml','.yaml','.txt','.toml','.sh','.ps1','.example',''} and str(p) not in FILES:
        problems.append('extension is not part of this code-only release')
    if p.suffix.lower() in {'.csv','.xlsx','.xls','.xlsm','.jsonl','.parquet','.docx','.doc','.pdf','.zip','.pyc','.png','.jpg','.svg'}:
        problems.append('data/workbook/report/output must remain private')
    try:text=data.decode('utf-8')
    except UnicodeDecodeError:return problems+['unexpected binary file']
    texts=[text]
    if p.suffix=='.ipynb':
        try:
            nb=json.loads(text)
            if any(c.get('outputs') or c.get('execution_count') is not None or c.get('attachments') for c in nb.get('cells',[])):
                problems.append('notebook contains saved output, execution counts or attachments')
            texts.extend(''.join(c.get('source',[])) for c in nb.get('cells',[]))
            for cell in nb.get('cells', []):
                if cell.get('cell_type') != 'code':continue
                try:tree=ast.parse(''.join(cell.get('source', [])))
                except SyntaxError:continue
                installers={'apply_recipient_unknown_fix.py','apply_release_diagnostics_fix.py'}
                if any(isinstance(node,ast.Constant) and isinstance(node.value,str)
                       and Path(node.value).name in installers for node in ast.walk(tree)):
                    problems.append('notebook references a standalone repair installer excluded from the release; remove the temporary cell or tag it local-only')
        except ValueError:problems.append('invalid notebook JSON')
    for text in texts:
        for match in re.finditer(r'(?im)^\s*DIMENSIONS_API_KEY\s*=\s*["\']?([^\s"\'\n]+)',text):
            if match.group(1)!='XXXXXXX':problems.append('non-placeholder credential assignment')
        for match in re.finditer(r'(?i)(?:api_key|apikey)\s*=\s*["\']([A-Za-z0-9_\-]{8,})["\']',text):
            if match.group(1)!='XXXXXXX' and not match.group(1).startswith('synthetic'):
                problems.append('possible hardcoded API key literal')
        if re.search(r'\bgrant\.\d{5,}\b',text):problems.append('possible real grant-level identifier')
    return list(dict.fromkeys(problems))

def scan(staged=False,release=False):
    issues=[];count=0
    if staged:
        cp=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True)
        if cp.returncode:raise ValueError('No Git index. Initialise Git and stage only the code.')
        names=[n for n in cp.stdout.decode().split('\0') if n]
        content=lambda n:subprocess.check_output(['git','show',':'+n],cwd=ROOT)
    else:
        names=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and allowed(p.relative_to(ROOT))]
        content=lambda n:(ROOT/n).read_bytes()
    for name in sorted(names):
        count+=1
        for issue in file_issues(name,content(name)):issues.append(name+': '+issue)
    return count,issues

def main():
    p=argparse.ArgumentParser();p.add_argument('--staged',action='store_true');p.add_argument('--release',action='store_true')
    args=p.parse_args()
    try:count,issues=scan(args.staged,args.release)
    except (ValueError,subprocess.CalledProcessError) as exc:print(str(exc),file=sys.stderr);return 2
    if issues:
        print('PUBLICATION CHECK FAILED')
        for issue in issues:print('  '+issue)
        return 1
    print(f'Checked {count} code/documentation files. No prohibited data, outputs or credential literals detected.')
    return 0
if __name__=='__main__':raise SystemExit(main())
