"""Read one existing R2 connection locally; never return its keys through the API."""
import configparser
import os
import re
import shutil
import subprocess

REMOTE=re.compile(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,63}')
ENDPOINT=re.compile(r'https://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/?')


def connection(name):
    name=str(name).removesuffix(':')
    if not REMOTE.fullmatch(name):raise ValueError('Choose a valid saved rclone connection name')
    executable=shutil.which('rclone')
    if not executable:raise ValueError('Rclone was not found on this device. Start GBR where rclone is on PATH, or enter R2 keys manually.')
    try:
        result=subprocess.run([executable,'config','show',name,'--ask-password=false'],
            stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('Could not read the saved rclone connection. Check that rclone works from this Windows account.') from None
    if result.returncode:
        # CLI output can contain credentials. It must never reach logs or HTTP errors.
        raise ValueError('Rclone could not open the saved connection. Check its name and unlock an encrypted configuration through the existing rclone password environment.')
    try:
        config=configparser.ConfigParser(interpolation=None)
        config.read_string(result.stdout.decode('utf-8-sig'))
        values=dict(config[name])
    except (configparser.Error,KeyError,UnicodeError):
        raise ValueError('The saved rclone connection could not be read; its configuration was left unchanged.') from None
    for key in ('type','provider','endpoint','access_key_id','secret_access_key','session_token','env_auth'):
        variable='RCLONE_CONFIG_'+name.upper()+'_'+key.upper()
        if variable in os.environ:values[key]=os.environ[variable]
    if values.get('type','').casefold()!='s3':raise ValueError('Select an S3 / Cloudflare R2 connection in rclone')
    endpoint=values.get('endpoint','').strip()
    if endpoint and '://' not in endpoint:endpoint='https://'+endpoint
    if not ENDPOINT.fullmatch(endpoint):raise ValueError('The selected rclone connection does not point to a Cloudflare R2 S3 endpoint')
    key,secret=values.get('access_key_id',''),values.get('secret_access_key','')
    token=values.get('session_token','')
    if not key and not secret and values.get('env_auth','').casefold() in {'true','1','yes'}:
        key,secret=os.environ.get('AWS_ACCESS_KEY_ID',''),os.environ.get('AWS_SECRET_ACCESS_KEY','')
        token=os.environ.get('AWS_SESSION_TOKEN','')
    if not key or not secret:raise ValueError('This rclone connection has no readable R2 key pair. Check its existing credentials or use manual R2 setup.')
    return {'endpoint':endpoint,'_access_key':key,'_secret_key':secret,'_session_token':token}
