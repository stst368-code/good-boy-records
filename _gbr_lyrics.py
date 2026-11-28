"""Shared lyric language annotations, excluded from model conditioning."""
import re

MARKER = re.compile(r'\{([a-zA-Z]{2,3}(?:-[a-zA-Z]{2,8})?)\}')
CODE = re.compile(r'[a-z]{2,3}(?:-[a-z]{2,8})?')


def language_codes(value=None, lyrics='', default='en'):
    if value is None or value == '': supplied=[]
    elif isinstance(value,str): supplied=re.split(r'[,;+\s]+',value.strip().lower())
    elif isinstance(value,list) and all(isinstance(v,str) for v in value): supplied=[v.lower().strip() for v in value]
    else: raise ValueError('Enter language codes separated by commas, e.g. en, ro')
    supplied=[v for v in supplied if v]
    if any(not CODE.fullmatch(v) for v in supplied): raise ValueError('Use language codes, e.g. en, ro, de')
    result=list(dict.fromkeys([*supplied,*(m[1].lower() for m in MARKER.finditer(str(lyrics)))]))
    if len(result)>64: raise ValueError('Use up to 64 languages in a song')
    return result or [default]


def language_value(value=None, lyrics='', default='en'):
    return ', '.join(language_codes(value,lyrics,default))


def model_lyrics(lyrics):
    # Avoid turning adjacent words into one when a marker appears inline.
    text=str(lyrics or '')
    if not MARKER.search(text):return text
    result=[]
    for line in text.splitlines(keepends=True):
        if not MARKER.search(line):result.append(line);continue
        cleaned=MARKER.sub(' ',line)
        if not cleaned.strip():continue
        ending=line[len(line.rstrip('\r\n')):]
        result.append(cleaned.strip()+ending)
    return ''.join(result)
