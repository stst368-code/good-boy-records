"""Bounded experiment expansion. Every sampled choice is frozen in the batch preview."""
from decimal import Decimal, InvalidOperation
from itertools import product
import math
import secrets

from .parameters import PARAMETERS, INTEGERS, NUMBERS, SEEDS

MAX_RUNS = 10000
MAX_VALUES = 1000
SWEEP_FIELDS = PARAMETERS - {'caption', 'cluster', 'style', 'model'}


def choices(field, entered):
    if field not in SWEEP_FIELDS:
        raise ValueError('Unsupported experiment field: ' + field)
    if isinstance(entered, str):
        entered = entered.strip()
        if ':' in entered:
            if field not in INTEGERS | NUMBERS | SEEDS:
                raise ValueError(field + ': ranges are available for numeric fields only')
            pieces = entered.split(':')
            if len(pieces) != 3:
                raise ValueError(field + ': use start:stop:step (inclusive stop)')
            entered = dict(zip(('start', 'stop', 'step'), pieces))
        else:
            entered = [value.strip() for value in entered.split(',')]
    if isinstance(entered, dict):
        if set(entered) != {'start', 'stop', 'step'} or field not in INTEGERS | NUMBERS | SEEDS:
            raise ValueError(field + ': range requires start, stop and step')
        try:
            start, stop, step = (Decimal(str(entered[key])) for key in ('start', 'stop', 'step'))
            if not all(v.is_finite() for v in (start, stop, step)) or step == 0 or (stop-start) * step < 0:
                raise ValueError(field + ': invalid range direction or step')
            count = int((stop-start) // step) + 1
            if not 1 <= count <= MAX_VALUES:
                raise ValueError(field + ': choose at most 1000 values')
            entered = [str(start + i * step) for i in range(count)]
        except (InvalidOperation, OverflowError) as exc:
            raise ValueError(field + ': invalid numeric range') from exc
    if not isinstance(entered, list) or not 1 <= len(entered) <= MAX_VALUES:
        raise ValueError(field + ': supply 1 to 1000 values')
    result = []
    for value in entered:
        if isinstance(value, bool) or value is None or isinstance(value, (list, dict)) or value == '':
            raise ValueError(field + ': use scalar values')
        if field in SEEDS:
            if str(value) != 'auto' and (not str(value).isdigit() or not 0 <= int(value) < 2**64):
                raise ValueError(field + ': use auto or unsigned 64-bit seeds')
            value = str(value)
        elif field in INTEGERS | NUMBERS:
            try:
                number = Decimal(str(value))
                if not number.is_finite() or field in INTEGERS and number != number.to_integral_value():
                    raise ValueError(field + ': invalid number')
                value = int(number) if field in INTEGERS else float(number)
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(field + ': invalid number')
            except InvalidOperation as exc:
                raise ValueError(field + ': invalid number') from exc
        elif not isinstance(value, str):
            raise ValueError(field + ': use text values')
        result.append(value)
    return result


def expand(options, rng=None):
    options = options or {}
    if set(options) - {'repetitions', 'sweeps', 'groups', 'strategy', 'samples', 'seed_policy'}:
        raise ValueError('Unknown experiment option')
    repeats, samples = options.get('repetitions', 1), options.get('samples', 1)
    if any(type(v) is not int or not 1 <= v <= MAX_RUNS for v in (repeats, samples)):
        raise ValueError('Experiment quantities must be integers from 1 to 10000')
    strategy = options.get('strategy', 'grid')
    if strategy not in {'grid', 'sample'} or options.get('seed_policy', 'preserve') not in {'preserve', 'randomize'}:
        raise ValueError('Unknown experiment strategy or seed policy')
    sweeps = options.get('sweeps', {})
    if not isinstance(sweeps, dict):
        raise ValueError('Experiment sweeps must be an object')
    axes = [[{field:value} for value in choices(field, entered)] for field, entered in sweeps.items()]
    groups=options.get('groups',[])
    if not isinstance(groups,list) or len(groups)>20:raise ValueError('Use at most 20 linked parameter groups')
    used=set(sweeps)
    for group in groups:
        if not isinstance(group,list) or not 1<=len(group)<=MAX_VALUES:raise ValueError('A linked group needs 1 to 1000 combinations')
        fields=set(group[0]) if isinstance(group[0],dict) else set()
        if not fields or fields & used:raise ValueError('Each parameter belongs to one range or linked group')
        used.update(fields); rows=[]
        for row in group:
            if not isinstance(row,dict) or set(row)!=fields:raise ValueError('Linked combinations must contain the same fields')
            rows.append({key:choices(key,[value])[0] for key,value in row.items()})
        axes.append(rows)
    count = math.prod(map(len, axes)) if strategy == 'grid' else samples
    if count * repeats > MAX_RUNS:
        raise ValueError('Experiment exceeds 10000 generations per batch; narrow the ranges')
    rng = rng or secrets.SystemRandom()
    combinations = product(*axes) if strategy == 'grid' else (
        [rng.choice(values) for values in axes] for _ in range(samples))
    return [({key:value for row in values for key,value in row.items()}, index, repeat) for index, values in enumerate(combinations)
            for repeat in range(repeats)]
