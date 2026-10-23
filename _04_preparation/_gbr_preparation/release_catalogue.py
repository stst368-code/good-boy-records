"""Public identity uses the original player's token algorithm."""
def share_token(track_id, version):
    value=1469598103934665603
    for character in f'm:{track_id}:{version}':value=((value^ord(character))*1099511628211)&((1<<64)-1)
    alphabet='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';result=''
    while value:value,digit=divmod(value,62);result=alphabet[digit]+result
    return (result or '0').zfill(8)[-8:]
