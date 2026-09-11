"""External, private numerical targets; demo success is never report verification."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .io import InputError, require_columns, unique_keys

def compare(values: pd.DataFrame, specification: dict, *, data_label: str) -> pd.DataFrame:
    require_columns(values,['kind','metric','value','unit'],'computed values')
    unique_keys(values,['kind','metric'],'computed values')
    if data_label=='synthetic' and specification.get('target_set')!='synthetic':
        raise InputError('Synthetic data cannot be checked against report targets.')
    if not specification.get('targets'):raise InputError('No numerical targets supplied; no validation can pass.')
    index=values.set_index(['kind','metric'])
    result=[]
    for target in specification['targets']:
        status='UNRESOLVED' if target.get('unresolved_reason') else None
        key=(target['kind'],target['metric'])
        actual=float(index.loc[key,'value']) if key in index.index else np.nan
        expected=target.get('expected');tol=target.get('absolute_tolerance')
        if tol is not None and (not np.isfinite(float(tol)) or float(tol)<0):
            raise InputError('Target absolute_tolerance must be finite and nonnegative.')
        if expected is not None and not np.isfinite(float(expected)):
            raise InputError('Target expected value must be finite when supplied.')
        if status is None:
            if key not in index.index:status='MISSING'
            elif target.get('unit')!=index.loc[key,'unit']:status='UNIT_MISMATCH'
            elif expected is None or tol is None:status='UNRESOLVED'
            elif not np.isfinite(actual):status='NONFINITE'
            else:status='PASS' if abs(actual-float(expected))<=float(tol) else 'FAIL'
        result.append({**target,'actual':actual,'difference':actual-expected if expected is not None else np.nan,'status':status})
    return pd.DataFrame(result)
