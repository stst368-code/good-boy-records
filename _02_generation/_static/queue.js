import {bindTabs} from './tabs.js';
import {bindMultiSelect} from './selection.js';

const tabs = [
  ['queued', 'Queue', 'No runs in the queue.'],
  ['completed', 'Completed', 'No completed runs.'],
  ['cancelled', 'Cancelled', 'No cancelled runs.'],
  ['failed', 'Failed', 'No failed runs.'],
  ['attention', 'Needs attention', 'No interrupted runs or unconfirmed cancellations.'],
];
const pageSize = 50;
const phases = {
  prepared: 'Queued',
  preparing: 'Preparing',
  worker_queue: 'Queued on worker',
  running: 'Running',
  finalising: 'Finalising',
  cancel_requested: 'Cancel requested',
  cancelling: 'Cancelling',
  finishing_to_cancel: 'Finishing before cancellation',
  cancel_unconfirmed: 'Cancellation unconfirmed',
  reconnect: 'Reconnect needed',
  interrupted: 'Interrupted',
  awaiting_status: 'Awaiting status',
  succeeded: 'Completed',
  cancelled: 'Cancelled',
  failed: 'Failed',
};
const stages = {
  comfy_waiting: 'Waiting for ComfyUI',
  serverless_queued: 'Queued on RunPod',
  starting_worker: 'Starting worker',
  conditioning: 'Text conditioning',
  diffusion: 'Sampling',
  decode: 'Decoding audio',
  write_flac: 'Writing FLAC',
  downloading: 'Downloading audio',
  publishing: 'Saving recording',
  cancel_after_generation: 'Discarding output after the worker finishes',
};
const dateFormat = new Intl.DateTimeFormat(undefined, {day: 'numeric', month: 'short', year: 'numeric'});
const clockFormat = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

export function phaseLabel(run) {
  if (run.phase === 'worker_queue' && run.stage === 'comfy_queued') return 'Queued in ComfyUI';
  return phases[run.phase] || (run.status !== 'running' && phases[run.status]) || 'Awaiting status';
}

export function stepText(run) {
  if (!(Number.isFinite(run.step) && run.steps > 0)) return '';
  const unit = run.progress_unit ?? (run.stage === 'diffusion' ? 'steps' : '');
  return `${run.step} / ${run.steps}${unit ? ' ' + unit : ''}`;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function timestamp(cell, label, value) {
  if (!value) return;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return;
  const line = node('div', 'queue-time');
  const time = node('time', '', clockFormat.format(date));
  time.dateTime = value;
  time.title = date.toLocaleString();
  time.append(' ', node('span', 'queue-date', dateFormat.format(date)));
  line.append(node('span', 'queue-time-label', label), time);
  cell.append(line);
}

function workerDetails(run) {
  if (!run.worker_id) return '';
  if (run.backend === 'local') return 'This PC';
  const address = run.worker_id.split('|')[1];
  if (run.backend === 'comfyui' && address?.includes('://')) {
    const endpoint = new URL(address);
    if (['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname)) return `ComfyUI · this PC${endpoint.port ? ' · ' + endpoint.port : ''}`;
    if (endpoint.hostname.endsWith('.proxy.runpod.net')) return `ComfyUI · ${endpoint.hostname.split('.')[0]}`;
    return `ComfyUI · ${endpoint.host}`;
  }
  if (run.worker_id.startsWith('runpod|')) return 'RunPod · ' + address;
  return address || run.worker_id;
}

function cancellable(run) {
  return run.status === 'prepared' || run.status === 'running';
}

export function initQueue({api, toast}) {
  const element = node('section', 'generation-run-queue');
  const list = node('div', 'generation-tabs generation-queue-tabs');
  const entries = tabs.map(([name, title, empty]) => {
    const button = node('button', '', title);
    button.id = 'genRunTab-' + name;
    button.type = 'button';
    const count = node('span', 'generation-tab-count');
    button.append(' ', count);
    const panel = node('section', 'generation-queue-panel');
    panel.id = 'genRunPanel-' + name;
    list.append(button);
    return {name, button, panel, count, empty};
  });
  element.append(list, ...entries.map(entry => entry.panel));

  const content = node('div');
  const filters = node('div', 'queue-filters');
  const search = node('input');
  search.type = 'search';
  search.maxLength = 200;
  search.placeholder = 'Track title or job ID';
  search.setAttribute('aria-label', 'Find generation runs');
  const order = node('select');
  order.setAttribute('aria-label', 'Order queued runs');
  order.append(new Option('Newest jobs first', 'newest'), new Option('Oldest jobs first', 'dispatch'));
  const toolbar = node('div', 'queue-toolbar-actions');
  const actions = node('div', 'queue-actions');
  const pickAll = node('input');
  pickAll.type = 'checkbox';
  const pickLabel = node('label', 'queue-select-all');
  pickLabel.append(pickAll, node('span', '', 'Select page'));
  const health = node('span', 'queue-health');
  const notice = node('p', 'queue-notice');
  notice.setAttribute('role', 'status');
  const table = node('div', 'generation-queue-table');
  const grid = node('table');
  grid.setAttribute('aria-label', 'Generation runs');
  const head = grid.createTHead().insertRow();
  for (const title of ['Select', 'Track', 'Status / progress', 'Timing', 'Job ID']) {
    const column = node('th', '', title === 'Select' ? '' : title);
    column.scope = 'col';
    if (title === 'Select') column.append(node('span', 'sr-only', title));
    head.append(column);
  }
  const body = grid.createTBody();
  table.append(grid);

  let tab = 'queued';
  let searchText = '';
  let searchTimer;
  let offset = 0;
  let sequence = 0;
  let pending = null;
  let displayed = [];
  const selected = new Map();
  let canShuffle = false;
  let acting = false;
  let lastUpdate = null;
  const rows = new Map();
  const selection = bindMultiSelect({
    container:table,
    rows:()=>acting ? [] : [...body.children].filter(row=>displayed.some(run=>run.id===row.dataset.run && cancellable(run))),
    key:row=>row.dataset.run,
    selected:id=>selected.has(id),
    select(id,checked) {
      if (checked) selected.set(id,displayed.find(run=>run.id===id));
      else selected.delete(id);
    },
    changed:syncSelection,
  });

  function button(title, handler, className = '') {
    const control = node('button', className, title);
    control.type = 'button';
    control.onclick = () => Promise.resolve(handler()).catch(error => toast(error.message));
    return control;
  }

  async function changeQueue(handler) {
    if (acting) return;
    acting = true;
    syncSelection();
    try {
      await handler();
    } finally {
      acting = false;
      syncSelection();
      await refresh(true);
    }
  }

  const cancel = button('Cancel selected', async () => {
    const runIds = [...selected.keys()];
    if (!runIds.length || !window.confirm(`Cancel ${runIds.length} selected runs?`)) return;
    await changeQueue(async () => {
      const result = await api('/generation/queue/cancel', {run_ids:runIds});
      selected.clear();
      toast([
        result.cancelled && `${result.cancelled} cancelled`,
        result.requested && `${result.requested} cancellation requests sent`,
        result.unavailable && `${result.unavailable} already finished or unavailable`,
      ].filter(Boolean).join(' · '));
    });
  }, 'queue-cancel');
  const reorder = action => changeQueue(() => api('/generation/queue/order', {
    action, run_ids: [...selected.keys()],
  }));
  const promote = button('Move to front', () => reorder('promote'));
  promote.title = 'Move selected queued runs to the front of their jobs';
  const shuffle = button('Shuffle within jobs', () => reorder('shuffle'));
  const range = node('span', 'queue-range');
  const previous = button('Previous', () => changePage(offset - pageSize));
  const next = button('Next', () => changePage(offset + pageSize));
  const pager = node('div', 'queue-pagination');
  pager.append(previous, next);
  const footer = node('div', 'queue-footer');
  footer.append(range, pager);
  actions.append(pickLabel, promote, shuffle, cancel);
  filters.append(search, order);
  toolbar.append(actions, health);
  content.append(filters, toolbar, notice, table, footer);
  entries[0].panel.append(content);
  table.hidden = footer.hidden = true;

  function resetPage(newOffset = 0) {
    offset = Math.max(0, newOffset);
    displayed = [];
    selected.clear();
    table.hidden = footer.hidden = true;
    notice.hidden = false;
    notice.textContent = 'Loading runs…';
    syncSelection();
  }

  function changePage(newOffset) {
    resetPage(newOffset);
    return refresh();
  }

  const selectTab = bindTabs({
    list, entries, label: 'Run status', initial: tab,
    onSelect(name) {
      if (name === tab) return;
      tab = name;
      entries.find(entry => entry.name === tab).panel.append(content);
      return changePage(0)?.catch(error => toast(error.message));
    },
  });

  search.oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchText = search.value.trim();
      changePage(0)?.catch(error => toast(error.message));
    }, 180);
  };
  order.onchange = () => changePage(0)?.catch(error => toast(error.message));

  function syncSelection() {
    const selectable = displayed.filter(cancellable);
    const checked = selectable.filter(run => selected.has(run.id)).length;
    pickAll.disabled = acting || !selectable.length;
    pickAll.checked = checked > 0 && checked === selectable.length;
    pickAll.indeterminate = checked > 0 && !pickAll.checked;
    actions.hidden = tab !== 'queued' && tab !== 'attention';
    promote.hidden = shuffle.hidden = order.hidden = tab !== 'queued';
    cancel.textContent = selected.size ? `Cancel selected (${selected.size})` : 'Cancel selected';
    cancel.disabled = acting || !selected.size;
    promote.disabled = acting || ![...selected.values()].some(run => run.status === 'prepared');
    shuffle.disabled = acting || !canShuffle;
    for (const [id, row] of rows) {
      row.pick.checked = selected.has(id);
      row.pick.disabled = acting;
    }
    selection.sync();
  }

  pickAll.onchange = () => {
    for (const run of displayed.filter(cancellable)) {
      if (pickAll.checked) selected.set(run.id, run);
      else selected.delete(run.id);
    }
    syncSelection();
  };

  function createRow(run) {
    const row = node('tr', 'queue-row');
    row.dataset.run = run.id;
    const select = node('td', 'queue-pick');
    const pick = node('input');
    pick.type = 'checkbox';
    pick.setAttribute('aria-label', 'Select ' + run.title + ' · ' + run.id.slice(0, 8));
    pick.onchange = () => {
      if (pick.checked) selected.set(run.id, displayed.find(item => item.id === run.id));
      else selected.delete(run.id);
      syncSelection();
    };
    select.append(pick);
    const track = node('th', 'queue-track');
    track.scope = 'row';
    const title = node('strong', '', run.title);
    const metadata = [run.style, run.variant_id && 'Variant ' + run.variant_id].filter(Boolean);
    track.append(title, node('div', 'queue-track-meta', metadata.join(' · ')));
    const activity = node('td', 'queue-activity');
    const badge = node('span', 'queue-state');
    const worker = node('span', 'queue-worker');
    const details = node('div', 'queue-stage');
    const bar = node('progress', 'queue-progress');
    bar.setAttribute('aria-label', 'Progress for ' + run.title);
    const error = node('details', 'queue-error');
    const errorText = node('p');
    error.append(node('summary', '', 'View error'), errorText);
    activity.append(badge, details, bar, worker, error);
    const timing = node('td', 'queue-timing');
    const job = node('td', 'queue-job');
    const code = node('code', '', run.batch_id.slice(0, 8));
    code.title = run.batch_id;
    job.append(code);
    row.append(select, track, activity, timing, job);
    return {row, pick, badge, details, bar, worker, error, errorText, timing, signature: null};
  }

  function updateRow(record, run) {
    const signature = JSON.stringify(run);
    if (record.signature === signature) return;
    record.signature = signature;
    record.row.dataset.phase = run.phase;
    record.pick.hidden = !cancellable(run);
    record.badge.textContent = phaseLabel(run);
    const steps = stepText(run);
    const stage = run.message || run.node_label || stages[run.stage];
    record.details.textContent = [stage || (run.phase === 'running' && run.backend === 'comfyui' ? 'Waiting for node updates' : ''), steps]
      .filter(Boolean).join(' · ');
    record.details.hidden = !record.details.textContent;
    record.worker.textContent = workerDetails(run);
    record.worker.hidden = !record.worker.textContent;
    record.bar.hidden = !steps;
    if (steps) {
      record.bar.max = run.steps;
      record.bar.value = run.step;
    }
    record.errorText.textContent = run.error || '';
    record.error.hidden = !(run.error && run.phase === 'failed');
    record.timing.replaceChildren();
    if (run.started_at) timestamp(record.timing, 'Started', run.started_at);
    else if (run.dispatched_at && run.phase !== 'worker_queue') timestamp(record.timing, 'Dispatched', run.dispatched_at);
    else if (!run.completed_at) timestamp(record.timing, 'Queued', run.queued_at);
    timestamp(record.timing, 'Finished', run.completed_at);
  }

  function render(data) {
    displayed = data.runs;
    canShuffle = data.can_shuffle;
    const entriesById = new Map(displayed.map(run => [run.id, run]));
    for (const [id, record] of rows) {
      if (entriesById.has(id)) continue;
      record.row.remove();
      rows.delete(id);
    }
    for (const id of selected.keys()) {
      const run = entriesById.get(id);
      if (run && cancellable(run)) selected.set(id, run);
      else selected.delete(id);
    }
    const focus = body.contains(document.activeElement) ? document.activeElement : null;
    let cursor = body.firstChild;
    for (const run of displayed) {
      if (!rows.has(run.id)) rows.set(run.id, createRow(run));
      const record = rows.get(run.id);
      updateRow(record, run);
      if (record.row !== cursor) body.insertBefore(record.row, cursor);
      cursor = record.row.nextSibling;
    }
    if (focus?.isConnected && document.activeElement !== focus) focus.focus({preventScroll: true});

    for (const entry of entries) entry.count.textContent = data.counts[entry.name].toLocaleString();
    const count = data.counts[tab];
    range.textContent = count ? `${offset + 1}–${offset + displayed.length} of ${count.toLocaleString()}` : '';
    previous.disabled = offset === 0;
    next.disabled = offset + displayed.length >= count;
    pager.hidden = count <= pageSize;
    table.hidden = footer.hidden = !displayed.length;
    notice.hidden = !!displayed.length;
    notice.textContent = displayed.length ? '' : entries.find(entry => entry.name === tab).empty;
    lastUpdate = new Date();
    health.textContent = tab==='queued'
      ? `${data.waiting.toLocaleString()} waiting · ${(count - data.waiting).toLocaleString()} with workers`
      : 'Updated ' + clockFormat.format(lastUpdate);
    syncSelection();
  }

  function refresh(force = false) {
    if (element.hidden) return;
    const parameters = new URLSearchParams({status:tab,offset,order:order.value});
    if (searchText) parameters.set('q',searchText);
    const key = parameters.toString();
    if (!force && pending?.key === key) return pending.promise;
    const request = ++sequence;
    if (table.hidden && notice.hidden) {
      notice.hidden = false;
      notice.textContent = 'Loading runs…';
    }
    const promise = api('/generation/queue?' + key).then(data => {
      if (request !== sequence) return;
      const count = data.counts[tab];
      if (offset && !data.runs.length) {
        offset = Math.max(0, Math.floor((count - 1) / pageSize) * pageSize);
        return refresh();
      }
      render(data);
      return data;
    }).catch(error => {
      if (request !== sequence) return;
      health.textContent = lastUpdate ? 'Last update ' + clockFormat.format(lastUpdate) : '';
      notice.hidden = false;
      notice.textContent = 'Could not refresh the queue. ' + error.message;
      throw error;
    }).finally(() => {
      if (pending?.request === request) pending = null;
    });
    pending = {key, request, promise};
    return promise;
  }

  notice.textContent = 'Loading runs…';
  syncSelection();
  return {
    element,
    refresh,
    showQueue() {
      sequence++;
      pending = null;
      clearTimeout(searchTimer);
      search.value = searchText = '';
      if (tab !== 'queued') selectTab('queued')?.catch(error => toast(error.message));
      else resetPage();
    },
    setVisible(visible) {
      element.hidden = !visible;
      if (!visible) {
        sequence++;
        pending = null;
      }
    },
  };
}
