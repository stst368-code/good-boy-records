"""Build an editable visual brief from canonical metadata; never rewrite YAML."""
import hashlib
import re

FAMILIES = {'comfyui', 'qwen-2.1', 'moss-image', 'flux'}


def brief(metadata, family='comfyui', direction='', references=(), seed=0):
    if family not in FAMILIES:
        raise ValueError('Choose ComfyUI or a recorded historical model family')
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError('Artwork seed must be an integer from 0 to 2**63-1')
    title = str(metadata.get('title') or '').strip()
    style = str(metadata.get('style') or metadata.get('style_primary') or metadata.get('version') or '').strip()
    cluster = str(metadata.get('cluster') or '').strip()
    lines = [line.strip() for line in str(metadata.get('lyrics') or '').splitlines()
             if line.strip() and not re.fullmatch(r'\[[^\]]+\]', line.strip())]
    # Keep a reproducible, bounded narrative excerpt; do not pretend this is an LLM summary.
    excerpt = '\n'.join(dict.fromkeys(lines))[:1800]
    story = str(metadata.get('story') or '').strip()[:1400]
    refs = list(dict.fromkeys(references))
    prompt = f'''Create one square album cover for the song "{title}".
Musical treatment: {cluster} / {style}.
Narrative supplied by the songwriter: {story or 'Use the lyric excerpt below.'}
Lyric excerpt (narrative context, not text to print):
{excerpt}
Visual direction: {direction.strip() or 'Translate the mood and narrative into a distinctive visual composition; avoid generic genre clip art.'}
Typography: the song title "{title}" is the only permitted printed text. Do not add artist names, logos or watermarks.
References: use supplied images for palette, technique and composition; retain this song's own narrative and title.'''
    return {'version': 1, 'family': family, 'width': 1024, 'height': 1024,
            'seed': str(seed), 'direction': direction.strip(), 'prompt': prompt, 'reference_ids': refs,
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'status': 'brief_only', 'needs': 'Supply the artwork ComfyUI API workflow before image diffusion; its actual model and reference bindings will be recorded.'}
