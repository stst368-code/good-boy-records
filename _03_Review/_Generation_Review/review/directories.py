"""Host-side directory picker; browser file handles are not server paths."""
import os
from pathlib import Path
import string


def listing(config, path=None):
    roots = [str(Path(letter+':\\')) for letter in string.ascii_uppercase if Path(letter+':\\').is_dir()] if os.name == 'nt' else ['/']
    folder = config.resolve(path) if path else config.root
    if not folder.is_dir():
        raise ValueError('Folder not found on the server. Enter a full folder path or choose another folder.')
    try:
        children = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
    except OSError as exc:
        raise ValueError('Cannot browse this folder. Check its permissions or enter another path.') from exc
    return {'path': str(folder), 'parent': str(folder.parent) if folder.parent != folder else None,
            'roots': roots, 'directories': [{'name': p.name, 'path': str(p)} for p in children]}
