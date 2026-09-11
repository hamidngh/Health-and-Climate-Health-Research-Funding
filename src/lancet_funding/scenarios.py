"""Server-derived health sets and the eleven approaches used in the final band.

H = health keywords; A = ANZSRC 32/42; R = HRCS health categories;
U = UoA 1-4. Climate keywords and the category veto apply to every numerator.
IDs 08-10 are deliberately absent: they were not retained for the final band.
"""
from dataclasses import dataclass
import re
from .io import InputError

@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    label: str
    formula: str
    family: str

MAIN = '14'
SCENARIOS = [
    Scenario('01','Scenario_01_CKW&HKW','Baseline 1 (HKW)','H','Baselines'),
    Scenario('02','Scenario_02_CKW&ANZSRC','Baseline 2 (ANZSRC)','A','Baselines'),
    Scenario('03','Scenario_03_CKW&HRCS','Baseline 3 (HRCS)','R','Baselines'),
    Scenario('04','Scenario_04_CKW&UoA','Baseline 4 (UoA)','U','Baselines'),
    Scenario('05','Scenario_05_CKW&HKW&ANZSRC','Keyword Intersection 1 (HKW AND ANZSRC)','H AND A','Intersections'),
    Scenario('06','Scenario_06_CKW&HKW&HRCS','Keyword Intersection 2 (HKW AND HRCS)','H AND R','Intersections'),
    Scenario('07','Scenario_07_CKW&HKW&UoA','Keyword Intersection 3 (HKW AND UoA)','H AND U','Intersections'),
    Scenario('11','Scenario_11_CKW&HKW_OR_ANZSRC','Keyword Union 1 (HKW OR ANZSRC)','H OR A','Unions'),
    Scenario('12','Scenario_12_CKW&HKW_OR_HRCS','Keyword Union 2 (HKW OR HRCS)','H OR R','Unions'),
    Scenario('13','Scenario_13_CKW&HKW_OR_UoA','Keyword Union 3 (HKW OR UoA)','H OR U','Unions'),
    Scenario('14','Scenario_14_Any_Three','Main Search Approach','at least 3 of H,A,R,U','Main'),
]
BY_ID = {s.id:s for s in SCENARIOS}
REPORT_IDS = tuple(BY_ID)
ALTERNATIVE_IDS = tuple(s for s in REPORT_IDS if s != MAIN)

def canonical(value):
    v=str(value).strip()
    for s in SCENARIOS:
        if v in {s.id,str(int(s.id)),str(float(s.id)),s.name,s.label}:return s.id
    m=re.match(r'^Scenario_(\d+)(?:_|$)',v)
    if m and m.group(1).zfill(2) in BY_ID:return m.group(1).zfill(2)
    raise InputError(f'Unknown or non-retained scenario: {v}. Final band uses 01-07, 11-14.')

def membership(h,a,r,u):
    return {'01':bool(h),'02':bool(a),'03':bool(r),'04':bool(u),
            '05':bool(h and a),'06':bool(h and r),'07':bool(h and u),
            '11':bool(h or a),'12':bool(h or r),'13':bool(h or u),
            MAIN:sum(map(bool,(h,a,r,u)))>=3}

# Each primitive is a conjunction: whether H is required, then category predicates.
PRIMITIVES={'H':(True,()),'A':(False,('A',)),'R':(False,('R',)),'U':(False,('U',)),
            'HA':(True,('A',)),'HR':(True,('R',)),'HU':(True,('U',)),
            'HAR':(True,('A','R')),'HAU':(True,('A','U')),
            'HRU':(True,('R','U')),'ARU':(False,('A','R','U')),
            'HARU':(True,('A','R','U'))}
COEFFICIENTS={'01':{'H':1},'02':{'A':1},'03':{'R':1},'04':{'U':1},
              '05':{'HA':1},'06':{'HR':1},'07':{'HU':1},
              '11':{'H':1,'A':1,'HA':-1},'12':{'H':1,'R':1,'HR':-1},
              '13':{'H':1,'U':1,'HU':-1},
              MAIN:{'HAR':1,'HAU':1,'HRU':1,'ARU':1,'HARU':-3}}
# Compatibility description only; extraction gets H,A,R,U once and uses ID sets.
NUMERATOR_BRANCHES={s:list(COEFFICIENTS[s]) for s in REPORT_IDS}
