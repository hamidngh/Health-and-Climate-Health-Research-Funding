"""Command-line entry point. The default scientific workflow starts at the API."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from dotenv import load_dotenv
from .io import InputError, read_json, environment, stamp, write_json
from .workflow import ROOT, load_config, run_all, extract_stage, analyse_stage
from .reference import import_exclusions, import_groupings, check_references, prepare_local_inputs


def parser():
    p=argparse.ArgumentParser(description='Dimensions API to main approach, ten alternatives and methodological bands.')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init',help='Create a local ignored .env from the safe placeholder example.')
    for name in ['doctor','import-exclusions','import-groupings','extract','analyse','run','compare','compare-bands','prepare-inputs']:
        q=sub.add_parser(name)
        q.add_argument('--config',default=None,help='Defaults to config/pipeline.json in this repository.')
        if name in ['extract','analyse','run','compare','compare-bands','prepare-inputs']:
            q.add_argument('--run-dir',default='runs/report_check')
        if name in ['analyse','run']:q.add_argument('--no-figures',action='store_true')
        if name=='doctor':q.add_argument('--live',action='store_true',help='Test API authentication and fields using small main-approach queries.')
        if name in ['import-exclusions','import-groupings']:
            q.add_argument('--file',required=True);q.add_argument('--sheet',default='0')
        if name=='import-exclusions':
            q.add_argument('--all-rows-are-exclusions',action='store_true')
            q.add_argument('--decision-column');q.add_argument('--exclude-values',nargs='+')
        if name in ['compare','compare-bands']:
            q.add_argument('--kind',choices=['funder'] if name=='compare-bands' else ['funder','recipient'],required=True)
            q.add_argument('--baseline',required=True)
            q.add_argument('--usd-tolerance',type=float,default=0.01)
            if name=='compare':q.add_argument('--scenarios',choices=['main','all'],default='main')
            else:
                q.add_argument('--start-year',type=int,default=2010);q.add_argument('--end-year',type=int,default=2024)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    load_dotenv(ROOT/'.env',override=False)
    try:
        if args.command=='init':
            path=ROOT/'.env'
            if path.exists():print('Local .env already exists; it was not overwritten.')
            else:
                path.write_text((ROOT/'.env.example').read_text(),encoding='utf-8')
                try:path.chmod(0o600)
                except OSError:pass
                print('Created .env with DIMENSIONS_API_KEY=XXXXXXX. Replace it locally; never commit .env.')
            (ROOT/'private').mkdir(exist_ok=True)
            return 0
        cfg=load_config(args.config)
        if args.command=='prepare-inputs':
            status=prepare_local_inputs(cfg)
            print('Private input workbooks imported and hash-verified. No data are made public.');return 0
        if args.command.startswith('import-'):
            sheet=int(args.sheet) if args.sheet.isdigit() else args.sheet
            if args.command=='import-exclusions':
                count=import_exclusions(args.file,cfg,all_rows=args.all_rows_are_exclusions,
                                        decision_column=args.decision_column,exclude_values=args.exclude_values,sheet=sheet)
            else:count=import_groupings(args.file,cfg,sheet=sheet)
            print(f'Imported {count} records into private reference storage and recorded its hash.')
            return 0
        if args.command=='doctor':
            print(json.dumps(environment(),indent=2))
            try:check_references(cfg);print('Reference inputs: imported and hash-verified.')
            except InputError as exc:print('Reference inputs: NOT READY. '+str(exc))
            key=os.environ.get('DIMENSIONS_API_KEY','').strip()
            print('API key: '+('placeholder or missing' if not key or key.upper()=='XXXXXXX' else 'present; value not displayed'))
            if args.live:
                from .dimensions import Client,FIELDS
                from .queries import category_filters,base_query,keyword_profile,where
                out=ROOT/'runs'/'diagnostics'/stamp().replace(':','').replace('.','')
                c=Client(out/'cache',pause=cfg['pause_seconds'],timeout=cfg['request_timeout_seconds'])
                cats=category_filters(c.facet('search grants','category_for_2020'),c.facet('search grants','category_uoa',limit=200))
                q=where(base_query('HAR',keyword_profile('historical_notebook'),cats,climate=True),f'start_year = {cfg["active_funder_year"]}')
                c.query(q+' return grants['+' + '.join(FIELDS)+'] sort by id asc limit 1')
                c.query(q+' return funder_orgs aggregate funding limit 1')
                c.query(q+' return research_org_countries aggregate funding limit 1')
                write_json({'status':'live_diagnostic_queries_completed','time_utc':stamp(),'full_extraction':False},out/'doctor.json')
                print('Live authentication, category mapping, grant fields and aggregation queries succeeded. This is not a complete extraction.')
            return 0
        if args.command=='extract':
            extract_stage(cfg,args.run_dir)
            print('Extraction complete. Run prepare-inputs then analyse; the fresh ranking is not an exclusion rule.')
            return 0
        if args.command in ['run','analyse']:
            function=run_all if args.command=='run' else analyse_stage
            result=function(cfg,args.run_dir,figures=not args.no_figures)
            print('Finished: '+str(Path(args.run_dir).resolve()/'results'))
            print(result['status'])
            return 0
        if args.command=='compare':
            from .comparison import compare_master
            run=Path(args.run_dir)
            current=run/'results'/'processed'/(args.kind+'_master.csv')
            summary=compare_master(args.baseline,current,args.kind,run/'validation',usd_tolerance=args.usd_tolerance,scenario_scope=args.scenarios)
            print(json.dumps(summary,indent=2));return 0 if summary['status']=='PASS' else 3
        if args.command=='compare-bands':
            from .comparison import compare_bands
            run=Path(args.run_dir)
            summary=compare_bands(args.baseline,run/'results/tables/global_bands.csv',run/'validation',kind=args.kind,start_year=args.start_year,end_year=args.end_year,usd_tolerance=args.usd_tolerance)
            print(json.dumps(summary,indent=2));return 0 if summary['status']=='PASS' else 3
    except InputError as exc:
        print('ERROR: '+str(exc),file=sys.stderr);return 2
    except KeyboardInterrupt:
        print('Interrupted. Successful API queries remain cached. Repeat the same command and run directory to resume.',file=sys.stderr);return 130
    except Exception as exc:
        # Arbitrary library exception messages may contain private file paths or records.
        print(f'Unexpected {type(exc).__name__}; no successful-run certificate was issued. Check input schemas and the last query log entry; never post credentials or raw data publicly.',file=sys.stderr)
        return 1
    return 0
