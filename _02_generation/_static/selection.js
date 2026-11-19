export function bindMultiSelect({container, rows, key, selected, select, changed}) {
  let anchor = null;
  let gesture = null;
  let suppressClick = false;
  let frame = null;

  function sync() {
    for (const row of rows()) {
      const checked = selected(key(row));
      row.classList.toggle('is-selected', checked);
      const checkbox = row.querySelector('input[type=checkbox]');
      if (checkbox) checkbox.checked = checked;
    }
  }

  function range(items, start, end) {
    const first = items.findIndex(row => key(row) === start);
    const last = items.findIndex(row => key(row) === end);
    if (last < 0) return [];
    return first < 0 ? [items[last]] : items.slice(Math.min(first,last),Math.max(first,last)+1);
  }

  function rowFor(target) {
    if (!(target instanceof Element) || target.closest('button,a,select,textarea,summary,input:not([type=checkbox])')) return;
    return rows().find(row => row.contains(target));
  }

  container.addEventListener('click', event => {
    const row = rowFor(event.target);
    if (!row) return;
    event.stopPropagation();
    if (suppressClick) {
      event.preventDefault();
      suppressClick = false;
      queueMicrotask(sync);
      return;
    }
    if (!event.target.matches('input[type=checkbox]')) event.preventDefault();
    const id = key(row);
    const checked = !selected(id);
    const targets = event.shiftKey && anchor ? range(rows(),anchor,id) : [row];
    for (const target of targets) select(key(target),checked);
    if (!event.shiftKey || !anchor) anchor = id;
    changed();
    queueMicrotask(sync);
  }, true);

  container.addEventListener('keydown', event => {
    if (event.key !== ' ' || !event.shiftKey) return;
    const row = rowFor(event.target);
    if (!row) return;
    event.preventDefault();
    const id = key(row);
    const checked = !selected(id);
    for (const target of range(rows(),anchor || id,id)) select(key(target),checked);
    anchor ||= id;
    changed();
    sync();
  });

  function paint() {
    if (!gesture?.dragging) return;
    const items = rows();
    if (!items.length) return;
    const last = items.find(row => row.getBoundingClientRect().bottom > gesture.y) || items.at(-1);
    const ids = new Set(range(items,gesture.anchor,key(last)).map(key));
    for (const row of items) {
      const id = key(row);
      select(id,ids.has(id) ? gesture.checked : gesture.baseline.get(id) || false);
    }
    changed();
    sync();
  }

  function scroll() {
    if (!gesture?.dragging) return;
    const bounds = container.getBoundingClientRect();
    const top = Math.max(0,bounds.top);
    const bottom = Math.min(innerHeight,bounds.bottom);
    const distance = gesture.y < top+32 ? -12 : gesture.y > bottom-32 ? 12 : 0;
    if (distance) {
      if (container.scrollHeight > container.clientHeight && getComputedStyle(container).overflowY !== 'visible') {
        container.scrollTop += distance;
      } else {
        window.scrollBy(0,distance);
      }
      paint();
    }
    frame = requestAnimationFrame(scroll);
  }

  container.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.pointerType !== 'mouse') return;
    const row = rowFor(event.target);
    if (!row) return;
    suppressClick = false;
    const id = key(row);
    gesture = {pointer:event.pointerId,x:event.clientX,y:event.clientY,startY:event.clientY,
      anchor:event.shiftKey && anchor ? anchor : id,checked:!selected(id),
      baseline:new Map(rows().map(item => [key(item),selected(key(item))])),dragging:false};
  });

  document.addEventListener('pointermove', event => {
    if (!gesture || event.pointerId !== gesture.pointer) return;
    gesture.y = event.clientY;
    if (!gesture.dragging && Math.hypot(event.clientX-gesture.x,event.clientY-gesture.startY) < 5) return;
    if (!gesture.dragging) {
      gesture.dragging = true;
      anchor = gesture.anchor;
      container.classList.add('selecting-rows');
      container.setPointerCapture(event.pointerId);
      frame = requestAnimationFrame(scroll);
    }
    event.preventDefault();
    paint();
  });

  function finish(event) {
    if (!gesture || event.pointerId !== gesture.pointer) return;
    if (gesture.dragging) {
      suppressClick = true;
      if (container.hasPointerCapture(event.pointerId)) container.releasePointerCapture(event.pointerId);
    }
    gesture = null;
    cancelAnimationFrame(frame);
    container.classList.remove('selecting-rows');
  }
  document.addEventListener('pointerup',finish);
  document.addEventListener('pointercancel',finish);
  return {sync};
}
