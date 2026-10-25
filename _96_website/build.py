"""Build or preview the public GBR website without staging the private repository."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import webbrowser
sys.path.insert(0, str(Path(__file__).resolve().parent / '_tools'))
from export_platform import export as export_platform, validate as validate_platform
from build_making import stage as stage_making

ROOT = Path(__file__).resolve().parent
FORMAT = 'gbr-website-build-v1'


def read_json(path):
    return Path(path).read_text(encoding='utf-8-sig')


def catalogue_counts(data):
    if not isinstance(data, dict) or data.get('format') != 'gbr-sonic-catalogue-v1':
        raise ValueError('Expected a GBR sonic catalogue')
    main = data.get('tracks')
    if not isinstance(main, list) or data.get('trackCount') != len(main):
        raise ValueError('Catalogue trackCount does not match its tracks')
    easter = data.get('easter', {})
    hidden = easter.get('tracks', []) if isinstance(easter, dict) else None
    if not isinstance(hidden, list) or easter.get('count', len(hidden)) != len(hidden):
        raise ValueError('Hidden-track count does not match its tracks')
    for tracks in (main, hidden):
        ids = set()
        for track in tracks:
            identifier = track.get('id') if isinstance(track, dict) else None
            if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
                raise ValueError('Every catalogue track needs a unique stable ID')
            ids.add(identifier)
    return len(main), len(hidden)


def output_path(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if root.is_relative_to(output):
        raise ValueError('Build output cannot replace website source or its parent')
    if output.is_relative_to(root) and output.relative_to(root).parts[0] in {
        '_templates', '_analytics', '_how_its_made', '_data', '_tools', '_content', '_tests', '_showcase'}:
        raise ValueError('Build output cannot replace website content or media')
    if output.exists():
        marker = output / 'build-manifest.json'
        if not output.is_dir() or not marker.is_file() or json.loads(read_json(marker)).get('format') != FORMAT:
            raise ValueError('Output is not an existing GBR website build: ' + str(output))
    return output


def stage(root, target):
    main, hidden = catalogue_counts(json.loads(read_json(root / '_data/catalogue.json')))
    analysis = json.loads(read_json(root / '_data/_analytics/generations.json'))
    if not isinstance(analysis, dict) or analysis.get('format') != 'gbr-generation-analytics-v1' or not isinstance(analysis.get('generations'), list):
        raise ValueError('Invalid public analytics export')
    if analysis.get('count') != len(analysis['generations']):
        raise ValueError('Analytics count does not match its recordings')
    # Explicit public allow-list; never copy the repository, database or media.
    shutil.copyfile(root / '_data/catalogue.json', target / 'catalogue.json')
    shutil.copyfile(root / 'showcase-host.js', target / 'showcase-host.js')
    shutil.copyfile(root / 'releases.js', target / 'releases.js')
    shutil.copyfile(root / 'easter-egg.js', target / 'easter-egg.js')
    shutil.copyfile(root / 'rooms.js', target / 'rooms.js')
    for name in ('display.css', 'display.js'):
        shutil.copyfile(root / name, target / name)
    for name in ('index.html', 'landscape.html'):
        shutil.copyfile(root / '_templates/landscape.html', target / name)
    (target / 'analytics').mkdir()
    for name in ('index.html', 'analytics.js', 'analytics.css', 'platform.js'):
        shutil.copyfile(root / '_analytics' / name, target / 'analytics' / name)
    destination = target / 'data/analytics'
    destination.mkdir(parents=True)
    for name in ('generations.json', 'summary.json', 'cost-analysis.json', 'platform.json'):
        source = root / '_data/_analytics' / name
        if source.is_file():
            if name == 'platform.json':
                validate_platform(json.loads(read_json(source)))
            shutil.copyfile(source, destination / name)
    page = target / 'analytics/index.html'
    availability = 'available' if (destination / 'cost-analysis.json').is_file() else 'absent'
    platform_available = 'available' if (destination / 'platform.json').is_file() else 'absent'
    page.write_text(page.read_text(encoding='utf-8').replace('<body>', '<body data-cost-export="' + availability + '" data-platform-export="' + platform_available + '">', 1), encoding='utf-8')
    source = root / '_content/_folders'
    for document in sorted(source.rglob('*.md')):
        if document.is_symlink():
            raise ValueError('Knowledge documents must be regular files')
        path = target / 'content-source/folders' / document.relative_to(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(document, path)
    subprocess.run([sys.executable, str(root / '_tools/build_knowledge_manifest.py'),
                    str(target / 'knowledge-manifest.json')], cwd=root, check=True)
    subprocess.run([sys.executable, str(root / '_tools/integrate_analytics.py'),
                    str(target / 'index.html'), str(target / 'landscape.html')], cwd=root, check=True)
    making = stage_making(root, target)
    subprocess.run([sys.executable, str(root / '_tools/integrate_making.py'),
                    str(target / 'index.html'), str(target / 'landscape.html')], check=True)
    # A separate unlisted page reuses the player and its public asset allow-list.
    hidden_page=(target/'index.html').read_text(encoding='utf-8')
    hidden_page=hidden_page.replace('<body data-power="on">','<body data-power="on" data-library="hidden">',1)
    hidden_page=hidden_page.replace('<title>Good Boy Records — Sonic Landscape</title>',
                                    '<title>Good Boy Records — Secret Room</title><meta name="robots" content="noindex,nofollow">',1)
    (target/'hidden.html').write_text(hidden_page,encoding='utf-8')
    easter_page=hidden_page.replace('data-library="hidden"','data-library="easter"',1).replace('Good Boy Records — Secret Room','Good Boy Records — Easter Eggs',1)
    (target/'easter.html').write_text(easter_page,encoding='utf-8')
    (target / '.nojekyll').write_text('', encoding='utf-8')
    player = (target / 'index.html').read_text(encoding='utf-8')
    for anchor in ('crossorigin="anonymous"', 'id="knowledgeButton"', 'FALLBACK_ART', 'id="gbrAnalyticsButton"', 'id="gbrObservatory"'):
        if anchor not in player:
            raise ValueError('Player integration missing: ' + anchor)
    knowledge = json.loads(read_json(target / 'knowledge-manifest.json'))
    for document in knowledge['documents']:
        path = (target / document['path']).resolve()
        if not path.is_relative_to(target) or not path.is_file():
            raise ValueError('Invalid knowledge document path')
    manifest = {'format': FORMAT, 'tracks': main, 'hidden_tracks': hidden,
                'historical_analytics_records': len(analysis['generations']),
                'analytics_generated_at': analysis.get('generated_at'), 'knowledge_documents': len(knowledge['documents']), 'process_guide': making}
    if platform_available == 'available':
        current = json.loads(read_json(destination / 'platform.json'))
        manifest['platform_recordings'] = current['count']
        manifest['platform_exported_at'] = current['generated_at']
    (target / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def build(root=ROOT, output=None):
    root = Path(root).resolve()
    output = output_path(root, output or root / '_site')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.gbr-site-', dir=output.parent))
    previous = None
    try:
        manifest = stage(root, temporary)
        if output.exists():
            previous = output.with_name('.gbr-site-previous-' + uuid.uuid4().hex)
            output.rename(previous)
        try:
            temporary.rename(output)
        except OSError:
            if previous:
                previous.rename(output)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if previous:
        shutil.rmtree(previous)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '_site')
    parser.add_argument('--refresh-catalogue', action='store_true', help='Read a complete curated collection into the committed catalogue')
    parser.add_argument('--showcase', type=Path, default=ROOT / '_showcase')
    parser.add_argument('--publishing-manifest', type=Path, help='Gate a catalogue refresh using the app publishing manifest')
    parser.add_argument('--export-platform', action='store_true', help='Refresh public research from the shared database (read only)')
    parser.add_argument('--database', type=Path, default=ROOT.parent / '_98_database/gbr.sqlite3')
    parser.add_argument('--refresh-easter', action='store_true', help='Explicit read-only rclone refresh of hidden R2 metadata')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args(argv)
    try:
        output_path(ROOT, args.output)
        if args.publishing_manifest and not args.refresh_catalogue:
            raise ValueError('--publishing-manifest requires --refresh-catalogue')
        if args.export_platform:
            export_platform(args.database, styles=ROOT.parent / '_01_writing/_references/styles.csv')
        if args.refresh_catalogue:
            extra = ['--publishing-manifest', str(args.publishing_manifest.resolve())] if args.publishing_manifest else []
            subprocess.run([sys.executable, str(ROOT / '_tools/build_catalogue.py'), '--showcase', str(args.showcase.resolve()), *extra], check=True)
        if args.refresh_easter:
            subprocess.run([sys.executable, str(ROOT / '_tools/refresh_easter.py'), str(ROOT / '_data/catalogue.json')], check=True)
        print(json.dumps({'output': str(args.output.resolve()), **build(output=args.output)}))
        if args.serve:
            handler = partial(SimpleHTTPRequestHandler, directory=str(args.output.resolve()))
            with ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
                address = 'http://127.0.0.1:' + str(server.server_port) + '/'
                print('Preview: ' + address, flush=True)
                if args.open:
                    webbrowser.open(address)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Cannot build GBR website: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
