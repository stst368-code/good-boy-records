"""Run purpose lives in the existing frozen request, not a second generation ledger."""


def purpose(specification):
    value = specification.get('purpose', 'production')
    if not isinstance(value, str) or value not in {'production', 'experiment'}:
        raise ValueError('Run purpose must be production or experiment')
    return value


def label(specification):
    value = specification.get('experiment_label', '')
    if not isinstance(value, str) or len(value) > 160:
        raise ValueError('Experiment name must be at most 160 characters')
    return value.strip()
