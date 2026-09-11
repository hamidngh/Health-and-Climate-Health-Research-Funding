"""Auditable Dimensions query plans; no regex substitutes for server membership."""
from __future__ import annotations
from importlib.resources import files
import json
from .io import InputError
from .scenarios import PRIMITIVES

def resource(name: str) -> dict:
    return json.loads(files('lancet_funding').joinpath('resources',name).read_text())

def keyword_profile(name: str) -> dict:
    lookup = {'historical_notebook':'keywords_historical.json'}
    if name not in lookup:
        raise InputError(f'Choose an explicit search profile from {list(lookup)}.')
    return resource(lookup[name])

def text_expression(profile: dict, *, health: bool, climate: bool) -> str:
    parts = []
    if climate:
        expression = '('+' OR '.join(profile['climate_terms'])+')'
        if profile['climate_exclusions']:
            expression = '('+expression+' AND NOT ('+' OR '.join(profile['climate_exclusions'])+'))'
        parts.append(expression)
    if health: parts.append('('+' OR '.join(profile['health_terms'])+')')
    return ' AND '.join(parts)

def category_filters(for_categories: list[dict], uoa_categories: list[dict]) -> dict[str,str]:
    settings = resource('filters.json')
    def ids_for(prefixes, categories):
        return sorted({str(x['id']) for x in categories if str(x.get('name','')).startswith(tuple(prefixes))})
    a = ids_for(settings['anzsrc_health_prefixes'],for_categories)
    ex = ids_for(settings['anzsrc_exclusion_prefixes'],for_categories)
    u = ids_for(settings['uoa_prefixes'],uoa_categories)
    absent = [p for p in settings['anzsrc_exclusion_prefixes'] + settings['anzsrc_health_prefixes']
              if not any(str(x.get('name','')).startswith(p) for x in for_categories)]
    if not a or not u or not ex or absent:
        raise InputError(f'Incomplete category mapping (missing prefixes {absent}); exclusions must not silently disappear.')
    for i in range(1,5):
        if not any(str(x.get('name','')).startswith((f'{i} ',f'A0{i}')) for x in uoa_categories):
            raise InputError(f'Missing UoA group {i}; check live metadata.')
    result = {'A':f'category_for_2020.id in {json.dumps(a)}',
              'R':'category_hrcs_hc is not empty',
              'U':f'category_uoa.id in {json.dumps(u)}',
              'exclude':f'not category_for_2020.id in {json.dumps(ex)}'}
    return result

def base_query(primitive: str, profile: dict, categories: dict, *, climate: bool=False,
               exclude_funders: bool=False) -> str:
    health, cats = PRIMITIVES[primitive]
    text = text_expression(profile,health=health,climate=climate)
    base = 'search grants'
    if text:
        if profile['search_index']: base += ' in '+profile['search_index']
        base += ' for '+json.dumps(text)
    filters = [f'({categories[x]})' for x in cats]+[f'({categories["exclude"]})']
    if exclude_funders:
        filters.append('not funder_org_name in '+json.dumps(resource('filters.json')['excluded_funders']))
    return base+' where '+' and '.join(filters)

def where(base: str, predicate: str) -> str:
    if ' return ' in base: raise InputError('Append filters before the return clause.')
    return base+(' and ' if ' where ' in base else ' where ')+f'({predicate})'
