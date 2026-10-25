"""A remotely configurable cover trigger; no second settings database."""
import json
import io
from urllib.parse import urlparse, unquote, quote

from .publishing import encoded
from .release_catalogue import read_catalogue
from .service import canonical, now

POINTER='gbr/v2/easter-egg.json'


class EasterEgg:
    def __init__(self,publisher):self.publisher=publisher

    def configuration(self):
        p=self.publisher;settings=p.settings();tracks={};hidden={};config={'enabled':False,'track_id':None}
        legacy=p.root/'_96_website/_data/catalogue.json'
        if legacy.is_file():
            saved=json.loads(legacy.read_text(encoding='utf-8-sig'))
            tracks={t['id']:t for t in saved.get('tracks',[])}
            hidden={t['id']:t for t in saved.get('easter',{}).get('tracks',[])}
        configured=bool(settings['endpoint'] and settings['bucket'])
        if configured:
            store=p.store();main,_=read_catalogue(store);secret,_=read_catalogue(store,'hidden')
            for target,data in ((tracks,main),(hidden,secret)):
                for identifier in data.get('withdrawn',[]):target.pop(identifier,None)
                for t in data['tracks']:
                    if t['id'] not in data.get('withdrawn',[]):target[t['id']]=t
            raw,_=store.read(POINTER)
            if raw:
                config=json.loads(raw)
                if config.get('format')!='gbr-easter-egg-v1':raise ValueError('Unknown cover trigger configuration; retained it')
        covers=[]
        for track in tracks.values():
            art=track.get('artwork_url')
            if not isinstance(art,str) or not art:continue
            if art.startswith('showcase/'):art=settings['public_base']+'/'+art
            u=urlparse(art)
            if u.scheme!='https' or not u.netloc or u.username or u.password:continue
            covers.append({'id':track['id'],'title':track.get('displayTitle') or track.get('title') or track['id'],
                           'variant':track.get('variant') or track.get('version') or '', 'artwork_url':art,
                           'preview_url':'/api/releases/easter-egg/covers/'+quote(track['id'],safe='')})
        covers.sort(key=lambda t:(t['title'].casefold(),t['variant'].casefold(),t['id']))
        return {'configured':configured,'trigger':{k:config.get(k) for k in ('enabled','track_id','artwork_url')},
                'covers':covers,'hidden_count':len(hidden),'url':settings['website_url'].rstrip('/')+'/hidden.html'}

    def cover(self,track_id):
        # Same-origin thumbnail keeps the app's existing strict image policy.
        # Read only an indexed object in the configured bucket, never a supplied URL.
        from PIL import Image, ImageOps
        covers=self.configuration()['covers'];cover=next((c for c in covers if c['id']==track_id),None)
        prefix=self.publisher.settings()['public_base'].rstrip('/')+'/'
        if not cover or not cover['artwork_url'].startswith(prefix):raise ValueError('Cover preview is unavailable from the configured media bucket')
        key=unquote(cover['artwork_url'][len(prefix):])
        raw,_=self.publisher.store().read(key)
        if not raw:raise ValueError('Cover preview is unavailable')
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>60_000_000:raise ValueError('Cover is too large to preview')
            image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((192,192))
            output=io.BytesIO();image.save(output,format='JPEG',quality=85)
        return output.getvalue()

    def save(self,track_id):
        p=self.publisher
        with p.lock:
            available=self.configuration()
            track=next((t for t in available['covers'] if t['id']==track_id),None)
            if track_id and not track:raise ValueError('Choose an available cover from the main player')
            store=p.store();raw,etag=store.read(POINTER)
            if raw and json.loads(raw).get('format')!='gbr-easter-egg-v1':raise ValueError('Unknown cover trigger configuration; retained it')
            value={'format':'gbr-easter-egg-v1','enabled':bool(track),'track_id':track_id or None,
                   'artwork_url':track['artwork_url'] if track else None,'generated_at':now()}
            try:store.compare_put(POINTER,encoded(value),etag=etag)
            except Exception:
                saved,_=store.read(POINTER)
                if saved!=encoded(value):raise
            p.db.execute("INSERT INTO review_history(action,details_json,created_at) VALUES('easter_egg_configured',?,?)",(canonical(value),now()))
            return {'saved':True,'trigger':value}
