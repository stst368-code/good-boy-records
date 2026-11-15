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
        'presets': [{'id': key, **{field: value[field] for field in fields}} for key, value in PRESETS.items()],
        'max_effects': MAX_EFFECTS,
    }


def validate_effects(entries):
    if not isinstance(entries, list) or len(entries) > MAX_EFFECTS:
        raise ValueError(f'Choose up to {MAX_EFFECTS} mastering effects')
    effects = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'preset', 'strength', 'bypassed'}:
            raise ValueError('Invalid mastering effect')
        preset, strength = entry.get('preset'), entry.get('strength')
        if not isinstance(preset, str) or preset not in PRESETS:
            raise ValueError('Unknown mastering effect preset')
        if type(strength) is not int or not 0 <= strength <= 100:
            raise ValueError('Effect strength must be 0–100%')
        bypassed = entry.get('bypassed', False)
        if type(bypassed) is not bool:
            raise ValueError('Invalid effect bypass')
        effects.append({'preset': preset, 'strength': strength, 'bypassed': bypassed})
    return effects


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


def effect_graph(effects, source, audio, directory, next_input, afir_gain):
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
            filters.append(f'[{send}]' + preset['filters'].format(cutoff=cutoff) + f'[{wet}]')
            dry_amount = 1 - amount
        filters.append(f'[{direct}][{wet}]amix=inputs=2:duration=first:normalize=0:weights={dry_amount:.4f} {amount:.4f}[{output}]')
        source = output
    return source, filters, impulses
