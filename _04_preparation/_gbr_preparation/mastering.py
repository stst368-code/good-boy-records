"""Preset definitions and FFmpeg routing shared by the app and the CPU worker."""
import math


PRESETS = {
    'tape_warmth': {
        'name': 'Tape Warmth', 'description': 'Soft saturation, rounder lows and a rolled-off top.',
        'strength': 60, 'cutoff': 10500,
        'filters': 'highpass=f=30,bass=f=135:g=1.5,lowpass=f={cutoff},volume=2.4,asoftclip=type=tanh:threshold=.80,volume=.50',
    },
    'lofi_radio': {
        'name': 'Lo-Fi Radio', 'description': 'Narrow, grainy mids like a small radio speaker.',
        'strength': 75, 'cutoff': 3400,
        'filters': 'highpass=f=300,lowpass=f={cutoff},equalizer=f=1250:t=q:w=1.2:g=3,volume=2.5,asoftclip=type=tanh:threshold=.62,volume=.48',
    },
    'live_room': {
        'name': 'Live Room', 'description': 'Close reflections and a short, lively room tail.',
        'strength': 55, 'room': 'live',
    },
    'concert_hall': {
        'name': 'Concert Hall', 'description': 'A slower, wider reverb with space behind the mix.',
        'strength': 50, 'room': 'hall',
    },
    'slapback': {
        'name': 'Slapback Echo', 'description': 'A short, distinct vintage echo.',
        'strength': 55, 'room': 'slapback',
    },
    'mix_glue': {
        'name': 'Mix Glue', 'description': 'Gentle compression to settle peaks into the mix.',
        'strength': 55,
        'filters': 'acompressor=threshold=.13:ratio=2.4:attack=18:release=140:makeup=1.22',
    },
    'air_clarity': {
        'name': 'Air & Clarity', 'description': 'A little top-end lift and less low-mid cloudiness.',
        'strength': 50, 'cutoff': 6600,
        'filters': 'equalizer=f=285:t=q:w=1:g=-2.7,treble=f={cutoff}:g=3.5',
    },
    'bass_weight': {
        'name': 'Bass Weight', 'description': 'A fuller low end with subsonic rumble cut.',
        'strength': 50,
        'filters': 'highpass=f=30,bass=f=108:g=4.5',
    },
}
TOOLS = {
    'parametric_eq': {
        'name': 'Parametric EQ', 'description': 'Shape a frequency band with frequency, gain and Q.',
        'strength': 100, 'filters': 'equalizer=f={frequency}:t=q:w={q}:g={gain_db}',
        'parameters': {'frequency': ('Frequency / Hz', 30, 20000, 10, 1000),
                       'gain_db': ('Gain / dB', -24, 24, .5, 0), 'q': ('Q', .1, 10, .1, 1)},
    },
    'compressor': {
        'name': 'Compressor', 'description': 'Control dynamics with threshold, ratio, attack and release.',
        'strength': 100,
        'filters': 'acompressor=threshold={threshold}:ratio={ratio}:attack={attack_ms}:release={release_ms}:makeup={makeup}',
        'parameters': {'threshold_db': ('Threshold / dB', -60, 0, 1, -18),
                       'ratio': ('Ratio', 1, 20, .1, 3), 'attack_ms': ('Attack / ms', .1, 200, .1, 15),
                       'release_ms': ('Release / ms', 10, 2000, 10, 150), 'makeup_db': ('Makeup / dB', 0, 18, .5, 0)},
    },
    'low_pass': {
        'name': 'Low-pass filter', 'description': 'Roll off the top end. Automate the blend into a section.',
        'strength': 100, 'filters': 'lowpass=f={frequency}',
        'parameters': {'frequency': ('Cutoff / Hz', 40, 20000, 10, 1800)},
    },
    'high_pass': {
        'name': 'High-pass filter', 'description': 'Remove low frequencies and build a thinner transition.',
        'strength': 100, 'filters': 'highpass=f={frequency}',
        'parameters': {'frequency': ('Cutoff / Hz', 30, 10000, 10, 250)},
    },
    'delay': {
        'name': 'Delay', 'description': 'A timed echo with adjustable delay and decay.',
        'strength': 40, 'filters': 'aecho=1:.6:{delay_ms}:{decay}',
        'parameters': {'delay_ms': ('Delay / ms', 20, 1500, 10, 250), 'decay': ('Decay', .05, .9, .05, .4)},
    },
}
PRESETS.update(TOOLS)
MAX_EFFECTS = 12

ROOMS = {
    # Predelay, decay time, diffuse energy, then individual reflections.
    'live': (.019, .58, .44, ((.009, .19), (.016, .14), (.028, -.12), (.043, .10), (.061, .06))),
    'hall': (.040, 2.4, .65, ((.031, .16), (.046, .13), (.062, -.10), (.083, .09), (.104, .05))),
    'slapback': (0, 0, 0, ((.115, .34), (.170, -.07))),
}


def preset_catalogue():
    fields = ('name', 'description', 'strength')
    return {
        'presets': [{'id': key, **{field: value[field] for field in fields},
                     'parameters': [{'id': name, 'label': options[0], 'min': options[1], 'max': options[2],
                                     'step': options[3], 'default': options[4]}
                                    for name, options in value.get('parameters', {}).items()]}
                    for key, value in PRESETS.items()],
        'max_effects': MAX_EFFECTS,
    }


def validate_effects(entries):
    if not isinstance(entries, list) or len(entries) > MAX_EFFECTS:
        raise ValueError(f'Choose up to {MAX_EFFECTS} mastering effects')
    effects = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'preset', 'strength', 'bypassed', 'parameters', 'range', 'automation'}:
            raise ValueError('Invalid mastering effect')
        preset, strength = entry.get('preset'), entry.get('strength')
        if not isinstance(preset, str) or preset not in PRESETS:
            raise ValueError('Unknown mastering effect preset')
        if type(strength) is not int or not 0 <= strength <= 100:
            raise ValueError('Effect strength must be 0–100%')
        bypassed = entry.get('bypassed', False)
        if type(bypassed) is not bool:
            raise ValueError('Invalid effect bypass')
        effect = {'preset': preset, 'strength': strength, 'bypassed': bypassed}
        controls = PRESETS[preset].get('parameters', {})
        values = entry.get('parameters', {})
        if not isinstance(values, dict) or set(values) - set(controls):
            raise ValueError('Unknown effect parameter')
        if controls:
            effect['parameters'] = {}
            for key, (_, low, high, _, default) in controls.items():
                value = values.get(key, default)
                if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError('Invalid effect parameter: ' + key)
                effect['parameters'][key] = value
        if entry.get('range') is not None:
            region = entry['range']
            if not isinstance(region, dict) or set(region) - {'start', 'end', 'fade_in', 'fade_out'}:
                raise ValueError('Invalid effect range')
            region = {key: region.get(key, 0) for key in ('start', 'end', 'fade_in', 'fade_out')}
            if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 7200 for value in region.values()):
                raise ValueError('Effect range must use finite seconds')
            if region['end'] <= region['start'] or region['fade_in'] + region['fade_out'] > region['end'] - region['start']:
                raise ValueError('Effect range needs an end after its start and fades that fit inside it')
            effect['range'] = region
        points = entry.get('automation', [])
        if not isinstance(points, list) or len(points) > 32:
            raise ValueError('Use at most 32 automation points per effect')
        if points:
            previous = -1
            effect['automation'] = []
            for point in points:
                if not isinstance(point, dict) or set(point) != {'time', 'value'}:
                    raise ValueError('Automation points need time and value')
                time, value = point['time'], point['value']
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in (time, value)) or not 0 <= time <= 7200 or time <= previous or not 0 <= value <= 100:
                    raise ValueError('Automation times must increase; strength must be 0–100%')
                previous = time
                effect['automation'].append({'time': time, 'value': value})
        effects.append(effect)
    return effects


def envelope(effect):
    """Sample-time expression, always in the original recording's coordinates."""
    points = effect.get('automation', [])
    curve = '1'
    if points:
        curve = f"{points[-1]['value'] / 100:.9g}"
        for first, second in reversed(list(zip(points, points[1:]))):
            ramp = f"({first['value']/100:.9g}+(t-{first['time']:.9g})*{(second['value']-first['value'])/100/(second['time']-first['time']):.9g})"
            curve = f"if(lt(t,{second['time']:.9g}),{ramp},{curve})"
        curve = f"if(lt(t,{points[0]['time']:.9g}),{points[0]['value']/100:.9g},{curve})"
    region = effect.get('range')
    if region:
        start, end = region['start'], region['end']
        gate = f"between(t,{start:.9g},{end:.9g})"
        if region['fade_in']:
            gate += f"*clip((t-{start:.9g})/{region['fade_in']:.9g},0,1)"
        if region['fade_out']:
            gate += f"*clip(({end:.9g}-t)/{region['fade_out']:.9g},0,1)"
        curve = f"({curve})*({gate})"
    return f"clip({effect['strength']/100:.9g}*({curve}),0,1)"


def room_impulse(room, rate, stereo):
    import numpy as np

    predelay, decay, energy, reflections = ROOMS[room]
    channels = 2 if stereo else 1
    length = math.ceil(rate * (max(predelay + decay, reflections[-1][0]) + .01))
    impulse = np.zeros((length, channels), dtype=np.float32)
    for channel in range(channels):
        if decay:
            begin = round(rate * (predelay + channel * .003))
            time = np.arange(length - begin) / rate
            noise = np.random.default_rng(671 + channel).standard_normal(len(time))
            noise = np.convolve(noise, [.08, .17, .25, .25, .17, .08], mode='same')
            envelope = np.minimum(1, time / .016) * np.exp(-6.9 * time / decay)
            tail = noise * envelope
            impulse[begin:, channel] = energy * tail / np.linalg.norm(tail)
        for index, (delay, amplitude) in enumerate(reflections):
            offset = channel * (.002 if index % 2 == 0 else -.002)
            impulse[round(rate * (delay + offset)), channel] += amplitude
    return impulse


def effect_graph(effects, source, audio, directory, next_input, afir_gain, time_offset=0):
    """Apply effects in rack order, preserving duration and the dry time origin."""
    filters, impulses = [], []
    for index, effect in enumerate(effects):
        preset = PRESETS[effect['preset']]
        amount = effect['strength'] / 100
        direct, send, wet, output = (f'{label}{index}' for label in ('direct', 'send', 'wet', 'effect'))
        filters.append(f'[{source}]asplit[{direct}][{send}]')
        if 'room' in preset:
            import soundfile as sf

            stereo = audio.channels == 2
            impulse = directory / f'effect-{index}.wav'
            sf.write(impulse, room_impulse(preset['room'], audio.samplerate, stereo), audio.samplerate, subtype='FLOAT')
            ir_format = 'input' if stereo else 'mono'
            filters.append(f'[{send}][{next_input + len(impulses)}:a]afir=dry=1:wet=1:irfmt={ir_format}:{afir_gain}[{wet}]')
            impulses.append(impulse)
            dry_amount = 1
        else:
            cutoff = min(preset.get('cutoff', 0), audio.samplerate * .44)
            values = dict(effect.get('parameters', {}))
            if 'frequency' in values: values['frequency'] = min(values['frequency'], audio.samplerate * .44)
            if effect['preset'] == 'compressor':
                values.update(threshold=10**(values['threshold_db']/20), makeup=10**(values['makeup_db']/20))
            filters.append(f'[{send}]' + preset['filters'].format(cutoff=cutoff, **values) + f'[{wet}]')
            dry_amount = 1 - amount
        if effect.get('range') or effect.get('automation'):
            curve = envelope(effect)
            if time_offset:
                import re
                curve=re.sub(r'\bt\b',f'(t+{time_offset:.9g})',curve)
            dry = '1' if 'room' in preset else f'1-({curve})'
            filters.extend([f"[{direct}]aeval=exprs='val(ch)*({dry})':c=same[dryauto{index}]",
                            f"[{wet}]aeval=exprs='val(ch)*({curve})':c=same[wetauto{index}]",
                            f'[dryauto{index}][wetauto{index}]amix=inputs=2:duration=first:normalize=0[{output}]'])
        else:
            filters.append(f'[{direct}][{wet}]amix=inputs=2:duration=first:normalize=0:weights={dry_amount:.4f} {amount:.4f}[{output}]')
        source = output
    return source, filters, impulses
