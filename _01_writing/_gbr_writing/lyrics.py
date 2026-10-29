"""Canonical MiniMax section markers for explicitly saved Writing lyrics."""
import re
import unicodedata


SECTION_ALIASES = {
    'intro': ('intro', 'introduction', 'opening', 'opening section', 'preamble', 'spoken intro'),
    'verse': ('verse', 'vers', 'stanza', 'couplet', 'rap', 'spoken', 'spoken word'),
    'pre-chorus': ('pre chorus', 'prechorus', 'pre chrous', 'pre refrain', 'pre hook', 'build', 'build up', 'buildup', 'lift'),
    'chorus': ('chorus', 'chrous', 'refrain', 'hook', 'chorous'),
    'post-chorus': ('post chorus', 'postchorus', 'post chrous', 'post refrain', 'post hook', 'after chorus'),
    'bridge': ('bridge', 'middle eight', 'middle 8', 'middle8', 'middleeight'),
    'instrumental': ('instrumental', 'inst', 'interlude', 'break', 'breakdown', 'gap', 'silence',
                     'pause', 'rest', 'transition', 'turnaround', 'music break', 'musical break', 'dance break', 'drop'),
    'solo': ('solo', 'guitar solo', 'piano solo', 'drum solo', 'drums solo', 'bass solo',
             'sax solo', 'saxophone solo', 'violin solo', 'synth solo', 'instrument solo'),
    'outro': ('outro', 'ending', 'end', 'coda', 'closing', 'fade out', 'fadeout', 'conclusion', 'spoken outro'),
}
ALIASES = sorted(((alias,section) for section,aliases in SECTION_ALIASES.items() for alias in aliases),
                 key=lambda item:len(item[0]),reverse=True)
TAG = re.compile(r'\[([^\[\]\r\n]*)\]')


def section_name(text):
    # Descriptions after a colon cannot override the section, e.g. verse: solo.
    label=unicodedata.normalize('NFKC',text.split(':',1)[0]).lower()
    label=re.sub(r'[^\w]+',' ',label.replace('_',' ')).strip()
    label=re.sub(r'(?<=[^\W\d_])(?=\d)',' ',label)
    label=re.sub(r'^(?:\d+(?:st|nd|rd|th)?|[ivx]+)\s+','',label)
    for candidate in (label,re.sub(r'^(?:(?:final|last|main|repeated|repeat|first|second|third|fourth)\s+)+','',label)):
        for alias,section in ALIASES:
            if candidate==alias or candidate.startswith(alias+' '):return section
    if re.search(r'\bsolo\b',label):return 'solo'
    return None


def normalize_lyrics(value):
    unknown=[]
    def replace(match):
        section=section_name(match[1])
        if section is None:
            unknown.append(match[0][:100]);return ''
        return '\n['+section+']\n'
    result=TAG.sub(replace,value)
    if unknown:
        raise ValueError('Unrecognised lyric section tag(s): '+', '.join(dict.fromkeys(unknown))+
                         '. Use '+', '.join('['+tag+']' for tag in SECTION_ALIASES)+'.')
    # Remove recognised markers before checking unclosed/nested brackets.
    remainder=re.sub(r'\[(?:'+ '|'.join(re.escape(tag) for tag in SECTION_ALIASES)+r')\]','',result)
    if '[' in remainder or ']' in remainder:raise ValueError('Malformed lyric section tag: close each tag with matching square brackets')
    return '\n'.join(line.strip().lower() for line in result.splitlines() if line.strip())
