"""RunPod account credential in an OS-backed vault; never in application JSON."""
import hashlib
import os
import time


class AccountCredential:
    def __init__(self,generation):self.g=generation;self.cached=None;self.expires=0

    @property
    def account(self):return hashlib.sha256(str(self.g.review.config.path.resolve()).encode()).hexdigest()

    def vault(self):
        try:
            import keyring
            backend=keyring.get_keyring()
            # Only native credential stores. Never silently use a plaintext fallback.
            module=type(backend).__module__
            if backend.priority>0 and module.startswith(('keyring.backends.Windows','keyring.backends.macOS','keyring.backends.SecretService','keyring.backends.kwallet')):
                return keyring
        except Exception:pass
        raise ValueError('No secure system keyring is available. Unlock/install the OS credential store, or provide RUNPOD_API_KEY in the environment.')

    def get(self):
        value=self.g.secrets.get('api_key') or os.environ.get('RUNPOD_API_KEY')
        if value:return value
        if time.monotonic()<self.expires:return self.cached
        try:self.cached=self.vault().get_password('good-boy-records.runpod',self.account)
        except Exception:self.cached=None
        self.expires=time.monotonic()+30
        return self.cached

    def save(self,key):
        if not isinstance(key,str) or not key.strip() or len(key)>2000 or any(c.isspace() for c in key.strip()):
            raise ValueError('Enter a RunPod API key')
        try:self.vault().set_password('good-boy-records.runpod',self.account,key.strip())
        except ValueError:raise
        except Exception:raise ValueError('The system keyring could not save the RunPod key. Unlock the OS credential store and try again.') from None
        self.g.secrets.pop('api_key',None)
        self.cached=key.strip();self.expires=time.monotonic()+30
