"""Public identity and separate main/hidden release catalogues."""
import hashlib
import json

POINTER = 'gbr/v2/catalogue.json'
HIDDEN_POINTER = 'gbr/v2/hidden/catalogue.json'


def destination(visibility='public'):
    if visibility not in {'public', 'hidden'}:raise ValueError('Choose Main player or Hidden room')
    return (HIDDEN_POINTER, 'gbr-hidden-releases-v1') if visibility == 'hidden' else (POINTER, 'gbr-public-releases-v1')


def release_url(settings, track, visibility='public'):
    return settings['website_url'].rstrip('/')+'/'+('hidden.html' if visibility=='hidden' else '')+'?s='+track['shareToken']


def read_catalogue(store, visibility='public'):
    pointer, format_name=destination(visibility)
    raw,etag=store.read(pointer)
    data=json.loads(raw) if raw else {'format':format_name,'tracks':[]}
    if data.get('format')!=format_name or not isinstance(data.get('tracks'),list) or not isinstance(data.get('withdrawn',[]),list):
        raise ValueError('Unknown release catalogue format; retained it')
    ids=[t.get('id') for t in data['tracks'] if isinstance(t,dict)]
    if len(ids)!=len(data['tracks']) or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('Invalid release catalogue; retained it')
    return data,etag


def write_catalogue(store, data, etag, visibility='public'):
    from .publishing import encoded
    pointer,_=destination(visibility);raw=encoded(data)
    snapshot=pointer.rsplit('/',1)[0]+'/catalogues/'+hashlib.sha256(raw).hexdigest()+'.json'
    try:store.compare_put(snapshot,raw,immutable=True)
    except Exception:
        saved,_=store.read(snapshot)
        if saved!=raw:raise
    store.compare_put(pointer,raw,etag=etag)


def remove_other(store, track, visibility, replace_release_id=None):
    """Remove the old placement before exposing a new one; retry fills any gap."""
    from .service import now
    other='public' if visibility=='hidden' else 'hidden'
    data,etag=read_catalogue(store,other)
    present=next((t for t in data['tracks'] if t['id']==track['id']),None)
    # Hidden placement also suppresses an older entry in the saved main catalogue.
    if not present and (other!='public' or track['id'] in data.get('withdrawn',[])):return
    if present and present.get('release_id') not in {None,track['release_id'],replace_release_id}:
        raise ValueError('A newer release replaced this preview; use that release instead')
    data['tracks']=[t for t in data['tracks'] if t['id']!=track['id']]
    data['withdrawn']=list(dict.fromkeys([*data.get('withdrawn',[]),track['id']]))
    data['generated_at']=now()
    write_catalogue(store,data,etag,other)


def share_token(track_id, version):
    value=1469598103934665603
    for character in f'm:{track_id}:{version}':value=((value^ord(character))*1099511628211)&((1<<64)-1)
    alphabet='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';result=''
    while value:value,digit=divmod(value,62);result=alphabet[digit]+result
    return (result or '0').zfill(8)[-8:]
