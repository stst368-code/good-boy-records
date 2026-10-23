"""Resolve recording names to songs without changing their metadata or catalogue."""
import csv
import hashlib
from pathlib import Path
import re
import unicodedata
from collections import defaultdict


def identifier(value):
    if isinstance(value,bool) or len(str(value or ''))>80 or not re.fullmatch(r'[0-9]+',str(value or '')):return None
    return str(int(value)) if int(value)>0 else None


def name_key(value):
    value=unicodedata.normalize('NFKC',str(value)).casefold().replace("'",'').replace('’','')
    return re.sub(r'[\W_]+','-',value).strip('-')


def base_name(value):
    value=str(value).strip();original=value
    if value.lower().endswith(('.flac','.wav','.yaml','.yml')):value=value.rsplit('.',1)[0]
    markers=[r'[-_]v(?:ariant)?[-_]?\d+(?=$|[-_])',
             r'[_-](?:encoder[_-]?cfg|cfg|steps?|seed|top[_-]?k|sampler[_-]?cfg)(?:[-_= ]+(?:-?\d|auto)|$)',
             r'__(?:g?[a-f0-9]{8,})(?:__|$)']
    starts=[m.start() for pattern in markers if (m:=re.search(pattern,value,re.I))]
    if starts:value=value[:min(starts)]
    return value.rstrip(' _-') or original


class SongNames:
    def __init__(self,rows,catalogue_root=None):
        self.labels={};self.by_generation={};self.aliases=defaultdict(set);self.prefixes=defaultdict(set)
        self.values={};self.records=list(rows);rows=self.records
        masters=[];variants=[]
        if catalogue_root:
            for filename,target in [('track_list.csv',masters),('variant_list.csv',variants),('variants_list.csv',variants)]:
                path=Path(catalogue_root)/filename
                try:
                    with path.open(encoding='utf-8-sig',newline='') as handle:target.extend(csv.DictReader(handle))
                except (OSError,UnicodeError,csv.Error):continue
        for row in masters:
            sid=identifier(row.get('song_id'))
            if not sid:continue
            key='song:'+sid
            label=(row.get('song_name') or row.get('title') or Path(row.get('filename','').replace('\\','/')).stem).strip()
            if label:self.labels[key]=label
        for row in masters+variants:
            sid=identifier(row.get('song_id'))
            if sid:
                for text in (row.get('song_name'),row.get('title'),Path(row.get('filename','').replace('\\','/')).stem):
                    if text:self.add_alias(text,'song:'+sid,prefix=True)
        # The indexer hashes numeric catalogue IDs for its songs foreign key.
        ids={identifier(r.get('catalogue_song_id')) for r in rows if r.get('catalogue_song_type') not in ('true','false')}
        ids.update(key[5:] for key in self.labels);ids.discard(None)
        hashed={hashlib.sha256(sid.encode()).hexdigest():'song:'+sid for sid in ids}
        candidates=defaultdict(list)
        for row in rows:
            sid=identifier(row.get('catalogue_song_id')) if row.get('catalogue_song_type') not in ('true','false') else None
            key='song:'+sid if sid else hashed.get(row.get('db_song_id'))
            if not key:continue
            self.by_generation[row['id']]=key;candidates[key].append(base_name(row['title']))
            for text in (row['title'],base_name(row['title']),row.get('original_filename',''),base_name(row.get('original_filename',''))):
                if text:self.add_alias(text,key,prefix=True)
        for key,labels in candidates.items():
            real=[text for text in labels if name_key(text) not in {'audio','track','unknown'}]
            self.labels.setdefault(key,min(real or labels,key=lambda text:(len(text),text.casefold())))
        for row in rows:
            if row['id'] in self.by_generation:continue
            key=self.lookup(row['title'],prefix=True) or self.lookup(row.get('original_filename',''),prefix=True)
            label=base_name(row['title'])
            key=key or 'name:'+name_key(label)
            self.by_generation[row['id']]=key;self.labels.setdefault(key,label)
        for row in rows:
            key=self.by_generation[row['id']]
            self.add_alias(row['title'],key);self.add_alias(base_name(row['title']),key)
        for key,label in self.labels.items():
            self.values[key]=key if key.startswith('song:') else label
            self.add_alias(label,key)

    def add_alias(self,text,key,prefix=False):
        normalized=name_key(text)
        if normalized:
            self.aliases[normalized].add(key)
            if prefix:self.prefixes[normalized].add(key)

    def lookup(self,text,prefix=False):
        normalized=name_key(text)
        candidates=self.aliases.get(normalized) or self.aliases.get(name_key(base_name(text)))
        if candidates:return next(iter(candidates)) if len(candidates)==1 else None
        if prefix:
            while '-' in normalized:
                normalized=normalized.rsplit('-',1)[0]
                candidates=self.prefixes.get(normalized)
                if candidates:return next(iter(candidates)) if len(candidates)==1 else None
        return None

    def resolve(self,value):
        if value.startswith('song:') and identifier(value[5:]):return 'song:'+identifier(value[5:])
        return self.lookup(value,prefix=True) or 'missing:'+value.casefold()

    def annotate(self,rows):
        result=[]
        for row in rows:
            row=dict(row);result.append({**row,'song_key':self.by_generation.get(row.get('id') or row.get('generation_id'))})
        return result

    def selection(self,values):
        result=[]
        for value in values:
            key=self.resolve(value);result.append(self.values.get(key,value))
        return list(dict.fromkeys(result))
