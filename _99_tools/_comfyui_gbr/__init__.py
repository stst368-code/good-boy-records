"""GBR's experimental ComfyUI workspace; no models or database opened here."""
from .nodes import NODE_CLASS_MAPPINGS,NODE_DISPLAY_NAME_MAPPINGS
WEB_DIRECTORY='./_web'
try:
    from server import PromptServer
except ImportError:
    PromptServer=None
if PromptServer is not None:
    from .bridge import register
    register(PromptServer.instance.routes)
__all__=['NODE_CLASS_MAPPINGS','NODE_DISPLAY_NAME_MAPPINGS','WEB_DIRECTORY']
