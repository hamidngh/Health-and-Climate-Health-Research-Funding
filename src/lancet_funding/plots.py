"""Main-approach lines, methodological envelopes, and scenario-family comparisons.

Each Matplotlib figure has a single axes and a CSV source. PNG panels are assembled
into convenience dashboards without adding hidden transformations. No report image
is redistributed. The 2025 segment is dashed to follow the historical presentation.
"""
from __future__ import annotations
from pathlib import Path
import html
import re
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter, MaxNLocator, StrMethodFormatter
from PIL import Image
from .io import write_csv
from .scenarios import MAIN, BY_ID
from .denominator_coverage import unavailable_mask

LABELS={'climate_funding_usd':'Climate-related health funding (USD million)',
        'health_funding_usd':'All health research funding (USD million)',
        'funding_share':'Climate-related share of health funding',
        'climate_count':'Climate-related grants / attributions',
        'health_count':'All health grants / attributions',
        'count_share':'Climate-related share of health grants',
        'health_funder_count':'Funders with health grants',
        'climate_funder_count':'Funders with climate-related health grants',
        'funder_share':'Share of funders with climate-related health grants'}

def slug(s):return re.sub(r'[^A-Za-z0-9_-]+','_',str(s)).strip('_')

def panel(data,metric,out,name,title,*,lo,hi,synthetic=False,hue=None,bands=None,overlays=None,zero_policy='historical_zero'):
    data=data[data.year.between(lo,hi)].copy()
    if data.empty:return None
    fig=plt.figure(figsize=(7.8,5.2));ax=fig.add_axes([.16,.17,.80,.65])
    scale=1e6 if 'funding_usd' in metric else 1.
    sources=[]
    if bands is not None:
        band=bands[bands.metric.eq(metric)&bands.year.between(lo,hi)].sort_values('year')
        if len(band):
            ax.fill_between(band.year,band.band_min/scale,band.band_max/scale,alpha=.22,
                            label='Methodological range',zorder=1)
            source=band.copy();source['series']='Methodological range';sources.append(source)
            data=data.merge(band[['year','main']],on='year',how='left',validate='one_to_one')
            data[metric]=data['main'] # uses the selected documented zero-denominator policy
    if overlays is not None:
        for sid,part in overlays[overlays.year.between(lo,hi)].groupby('scenario_id'):
            col=metric+'_legacy_zero' if zero_policy=='historical_zero' and metric+'_legacy_zero' in part else metric
            part=part.sort_values('year')
            ax.plot(part.year,part[col]/scale,linewidth=1.1,alpha=.8,label=BY_ID[str(sid)].label,zorder=2)
            source=part[['year',col]].rename(columns={col:'value'});source['series']=BY_ID[str(sid)].label;sources.append(source)
    groups=data.groupby(hue,sort=True) if hue else [('Main Search Approach',data)]
    for label,part in groups:
        part=part.sort_values('year');normal=part[part.year<=2024];tail=part[part.year>=2024]
        ax.plot(normal.year,normal[metric]/scale,linewidth=2.8,label=str(label),zorder=5)
        # Same cycle entry as solid segment, without choosing a colour palette.
        if len(tail)>1:
            line=ax.lines[-1]
            ax.plot(tail.year,tail[metric]/scale,linewidth=2.8,linestyle='--',color=line.get_color(),zorder=5)
        source=part[['year',metric]].rename(columns={metric:'value'});source['series']=str(label);sources.append(source)
    unavailable_only=(metric in {'health_count','health_funding_usd','count_share','funding_share'} and data[metric].isna().all() and unavailable_mask(data).any())
    if unavailable_only:
        ax.text(.5,.5,'Comparable health denominator unavailable\nUnknown-recipient grants are retained in numerator totals',
                transform=ax.transAxes,ha='center',va='center',fontsize=9,wrap=True)
    ax.set_xlim(lo if lo!=hi else lo-1,hi if lo!=hi else hi+1)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True,nbins=8))
    ax.tick_params(axis='x',rotation=35);ax.grid(True,alpha=.25)
    ax.set_xlabel('Grant start year');ax.set_ylabel(LABELS[metric],fontsize=9)
    if metric.endswith('_share'):ax.yaxis.set_major_formatter(PercentFormatter(1))
    elif metric.endswith('_count'):
        ax.yaxis.set_major_locator(MaxNLocator(integer=True,nbins=6))
        ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    if unavailable_only:ax.set_yticks([])  # No artificial numeric scale for unavailable data.
    ax.set_title(('SYNTHETIC TEST DATA\n' if synthetic else '')+title,fontsize=10,pad=13)
    ax.legend(fontsize=7,loc='best')
    fig.text(.16,.025,'Range = sensitivity across search definitions, not a confidence interval.' if bands is not None else
             'Local calculation; award values assigned to start year.',fontsize=8)
    out.mkdir(parents=True,exist_ok=True)
    for ext in ['png','svg','pdf']:fig.savefig(out/(name+'.'+ext),dpi=160)
    plt.close(fig)
    source=pd.concat(sources,ignore_index=True);source['metric']=metric
    source['values_in_csv']='USD or fraction or count, before display scaling'
    write_csv(source,out/(name+'_data.csv'))
    return name+'.png'


def dashboard(out,names,target,columns=2):
    paths=[out/(s+'.png') for s in names]
    if not paths or not all(p.is_file() for p in paths):return
    panels=[Image.open(p).convert('RGB') for p in paths];w,h=panels[0].size
    canvas=Image.new('RGB',(w*columns,h*((len(panels)+columns-1)//columns)))
    for i,im in enumerate(panels):canvas.paste(im,((i%columns)*w,(i//columns)*h));im.close()
    canvas.save(out/(target+'.png'));canvas.save(out/(target+'.pdf'),resolution=160.)


def all_figures(annual,cohort,tables,out,cfg,*,synthetic=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);entries=[]
    four=['climate_funding_usd','funding_share','climate_count','count_share']
    six=['health_funding_usd','climate_funding_usd','funding_share','health_count','climate_count','count_share']
    lo,hi=cfg['start_year'],cfg['end_year']
    def draw(data,metrics,prefix,title,section,low=lo,high=hi,bands=None,hue=None,overlays=None):
        names=[]
        for metric in metrics:
            name=slug(prefix+'_'+metric)
            path=panel(data,metric,out,name,title,lo=low,hi=high,synthetic=synthetic,hue=hue,bands=bands,
                       overlays=overlays,zero_policy=cfg['band_zero_denominator'])
            if path:entries.append((section,path,LABELS[metric]+' — '+title));names.append(name)
        return names
    for kind in ['funder','recipient']:
        sub=annual[annual.kind.eq(kind)];main=sub[sub.scenario_id.eq(MAIN)]
        b=tables['methodological_bands'];b=b[b.kind.eq(kind)]
        names=draw(main[main.scope_system.eq('Global')],four,'main_'+kind,'Global: '+kind+' | 1990–2025',
                   'Main indicator — '+kind,low=cfg['main_figure_start'],high=cfg['main_figure_end'],bands=b[b.scope_system.eq('Global')])
        dashboard(out,names,'main_'+kind+'_dashboard')
        if cfg.get('generate_report_window',True):
            names=draw(main[main.scope_system.eq('Global')],four,'report_window_'+kind,'Global: '+kind+' | 2010–2024',
                       'Historical report window',max(lo,2010),min(hi,2024),bands=b[b.scope_system.eq('Global')])
            dashboard(out,names,'report_window_'+kind+'_dashboard')
        for (system,scope),part in main.groupby(['scope_system','scope']):
            if system=='Global':continue
            if cfg.get('annex_scopes','all')=='core' and system not in {'WHO','HDI','Lancet'}:continue
            band=b[b.scope_system.eq(system)&b.scope.eq(scope)]
            prefix=slug(kind+'_'+system+'_'+scope)
            names=draw(part,six,prefix,system+': '+scope+' | '+kind,'Geographic annex — '+kind,bands=band)
            dashboard(out,names,prefix+'_dashboard',columns=3)
        # Scenario-family overlays for global and the three regions used in the report.
        if cfg.get('annex_families',True):
            for (system,scope),part in sub.groupby(['scope_system','scope']):
                if not (system=='Global' or system=='WHO' and scope in {'Americas','Europe','Western Pacific'}):continue
                band=b[b.scope_system.eq(system)&b.scope.eq(scope)]
                main_part=part[part.scenario_id.eq(MAIN)]
                for family in ['Baselines','Intersections','Unions']:
                    ids=[s.id for s in BY_ID.values() if s.family==family]
                    prefix=slug(kind+'_'+system+'_'+scope+'_'+family)
                    names=draw(main_part,six,prefix,scope+': '+family+' | '+kind,'Scenario families — '+kind,
                               bands=band,overlays=part[part.scenario_id.isin(ids)])
                    dashboard(out,names,prefix+'_dashboard',columns=3)
        for system in ['HDI','Lancet']:
            group=main[(main.scope_system==system)&~main.scope.isin(['Unmapped','Other'])]
            draw(group,['climate_funding_usd','climate_count'],kind+'_'+system+'_distribution',system+' distribution: '+kind,
                 'Distribution',hue='scope')
        draw(tables[kind+'_types'],['climate_funding_usd','climate_count'],kind+'_types','Funder types: '+kind,
             'Funder types',hue='funder_type')
    main=cohort[cohort.scenario_id.eq(MAIN)&cohort.scope_system.eq('Global')]
    band=tables['active_funder_bands'];band=band[band.scope_system.eq('Global')]
    names=draw(main,four,'active_'+str(cfg['active_funder_year']),
               'Funders reporting in '+str(cfg['active_funder_year'])+' | all retained approaches',
               'Endpoint reporting-funder check',bands=band)
    dashboard(out,names,'active_funder_dashboard')
    cov=tables['funder_coverage_scopes'];cb=tables['funder_coverage_bands']
    for (system,scope),part in cov[cov.scenario_id.eq(MAIN)].groupby(['scope_system','scope']):
        if cfg.get('annex_scopes','all')=='core' and system not in {'Global','WHO','HDI','Lancet'}:continue
        draw(part,['health_funder_count','climate_funder_count','funder_share'],slug('coverage_'+system+'_'+scope),
             'Reporting funders: '+scope,'Funder coverage',bands=cb[cb.scope_system.eq(system)&cb.scope.eq(scope)])
    title='Synthetic pipeline test run' if synthetic else 'Main approach and methodological bands — local outputs'
    page=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
          '<title>'+html.escape(title)+'</title>',
          '<style>body{font:16px system-ui;max-width:1400px;margin:2rem auto;padding:1rem}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1.2rem}figure{margin:0}img{width:100%}figcaption{font-size:.85rem} @media(max-width:700px){.grid{grid-template-columns:1fr}}</style>',
          '<h1>'+html.escape(title)+'</h1><p>1990–2025. Main line: Scenario 14. Default envelope: all eleven retained approaches, matching the historical plotting code. Ten-alternative-only bounds are also in the tables. This is not a confidence interval. The 2025 segment is dashed following the historical presentation. Historical numerical equality has not been certified.</p>']
    for section in dict.fromkeys(e[0] for e in entries):
        page+=['<h2>'+html.escape(section)+'</h2><div class="grid">']
        for sec,path,caption in entries:
            if sec==section:
                stem=Path(path).stem
                page.append(f'<figure><img loading="lazy" src="{path}" alt="{html.escape(caption)}"><figcaption>{html.escape(caption)} — <a href="{stem}.svg">SVG</a> · <a href="{stem}.pdf">PDF</a> · <a href="{stem}_data.csv">source CSV</a></figcaption></figure>')
        page.append('</div>')
    page.append('</html>');(out/'index.html').write_text('\n'.join(page),encoding='utf-8')
    return len(entries)
