"""The cover map keeps room between artwork and unifies published song aliases."""
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GalleryTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required for the gallery checks')
    def test_published_title_aliases_and_cover_spacing(self):
        source = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
global.window = {};
require(process.argv[1]);
const {groupTracks, spaceCovers, coverSize, overlaps} = window.GBRGallery;
const legacy = JSON.parse(fs.readFileSync(process.argv[2])).tracks;
const releases = [
  {id: 'gbr-9-2-test', displayTitle: 'johnny three fridges', composition: '9', shareToken: 'testlink'},
  {id: 'gbr-11-2-test', displayTitle: 'lie by the fire', composition: '11'},
];
const {groups} = groupTracks([...legacy, ...releases]);
assert.equal(groups.filter(group => group.title === 'Johnny Three Fridges').length, 1);
assert.equal(groups.find(group => group.title === 'Johnny Three Fridges').allItems.length, 8);
assert.equal(groups.find(group => group.title === 'Lie By The Fire').allItems.length, 10);
assert.equal(groups.flatMap(group => group.allItems).find(item => item.t.id === releases[0].id).t.shareToken, 'testlink');

const clustered = Array.from({length: 220}, (_, i) => ({
  baseX: 870 + Math.cos(i * 2.4) * 110, baseY: Math.sin(i * 2.4) * 180, seed1: i / 220,
}));
spaceCovers(clustered, {x: 0, y: 0}, 820);
assert.ok(coverSize(2.5, 1440, 900) > 340);
for (const zoom of [.14, .35, .65, 1.6, 2.5, 5]) {
  const size = coverSize(zoom, 1440, 900);
  const bounds = clustered.map((item, i) => ({
    // Include drift in opposite directions on adjacent art.
    x: (item.x + (i % 2 ? -4.2 : 4.2)) * zoom - size / 2,
    y: (item.y + (i % 3 ? -4.2 : 4.2)) * zoom - size / 2,
    width: size, height: size,
  }));
  for (let i = 0; i < bounds.length; i++)
    for (let j = i + 1; j < bounds.length; j++) assert.ok(!overlaps(bounds[i], bounds[j]));
}
"""
        subprocess.run(['node', '-e', source, str(ROOT / 'gallery.js'), str(ROOT / '_data/catalogue.json')], check=True)
