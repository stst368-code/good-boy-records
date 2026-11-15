(() => {
  const tidy = value => String(value ?? '').normalize('NFC').trim().replace(/\s+/g, ' ');
  const titleKey = value => tidy(value).normalize('NFKC').toUpperCase().toLowerCase();
  function displayTitle(value) {
    const name = tidy(value);
    if (name !== name.toLowerCase() || name === name.toUpperCase()) return name;
    return name.replace(/(^|[^\p{L}\p{N}_'’])(\p{L})/gu, (_, prefix, letter) => prefix + letter.toUpperCase());
  }

  function groupTracks(tracks) {
    const roots = tracks.map((_, index) => index);
    function root(index) {
      while (roots[index] !== index) {
        roots[index] = roots[roots[index]];
        index = roots[index];
      }
      return index;
    }
    const aliases = new Map();
    tracks.forEach((track, index) => {
      // Older exports use a composition slug, newer releases use a song ID.
      // A case-equivalent title connects the two without changing either ID.
      const keys = [
        ['composition', titleKey(track.composition)],
        ['title', titleKey(track.displayTitle || track.title)],
      ];
      for (const [type, value] of keys) {
        if (!value) continue;
        const key = type + ':' + value;
        if (aliases.has(key)) roots[root(index)] = root(aliases.get(key));
        else aliases.set(key, index);
      }
    });
    const groups = new Map(), byIndex = [];
    tracks.forEach((track, index) => {
      const id = root(index);
      if (!groups.has(id)) {
        const title = displayTitle(track.displayTitle || track.title || track.composition || 'Untitled');
        groups.set(id, {key: titleKey(title) || String(index), title, allItems: []});
      }
      const group = groups.get(id);
      group.allItems.push({t: track, i: index});
      byIndex[index] = group;
    });
    return {groups: [...groups.values()], byIndex};
  }

  const COVER_WIDTH = 142;
  const PITCH = 166;
  const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

  function spaceCovers(nodes, anchor, exclusion) {
    const cells = new Map();
    const cell = value => Math.floor(value / PITCH);
    const key = (x, y) => x + ':' + y;
    function available(x, y) {
      if (Math.hypot(x - anchor.x, y - anchor.y) < exclusion) return false;
      const cx = cell(x), cy = cell(y);
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          for (const node of cells.get(key(cx + dx, cy + dy)) || []) {
            if (Math.max(Math.abs(node.x - x), Math.abs(node.y - y)) < PITCH) return false;
          }
        }
      }
      return true;
    }
    for (const node of nodes) {
      const originX = node.baseX, originY = node.baseY, offset = node.seed1 * Math.PI * 2;
      let x = originX, y = originY, step = 0;
      // Look outward from the seeded location so covers remain in their genre neighbourhood.
      while (!available(x, y)) {
        step += 1;
        const radius = PITCH * .53 * Math.sqrt(step), angle = step * GOLDEN_ANGLE + offset;
        x = originX + radius * Math.cos(angle);
        y = originY + radius * Math.sin(angle);
      }
      node.x = node.baseX = x;
      node.y = node.baseY = y;
      const bucket = key(cell(x), cell(y));
      if (!cells.has(bucket)) cells.set(bucket, []);
      cells.get(bucket).push(node);
    }
  }

  const coverSize = (zoom, width, height) => Math.min(COVER_WIDTH * zoom, Math.min(width, height) * .84, 720);
  const overlaps = (a, b, gap = 0) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;

  window.GBRGallery = {titleKey, displayTitle, groupTracks, spaceCovers, coverSize, overlaps};
})();
