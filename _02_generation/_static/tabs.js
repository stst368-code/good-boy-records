export function bindTabs({list, entries, label, initial, onSelect}) {
  list.setAttribute('role', 'tablist');
  list.setAttribute('aria-label', label);

  function select(name) {
    for (const entry of entries) {
      const active = entry.name === name;
      entry.button.setAttribute('aria-selected', String(active));
      entry.button.tabIndex = active ? 0 : -1;
      entry.panel.hidden = !active;
    }
    return onSelect(name);
  }

  entries.forEach(({name, button, panel}, index) => {
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', panel.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', button.id);
    button.addEventListener('click', () => select(name));
    button.addEventListener('keydown', event => {
      const last = entries.length - 1;
      const target = {
        ArrowLeft: index === 0 ? last : index - 1,
        ArrowRight: index === last ? 0 : index + 1,
        Home: 0,
        End: last,
      }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      entries[target].button.focus();
    });
  });
  for (const {name, button, panel} of entries) {
    button.setAttribute('aria-selected', String(name === initial));
    button.tabIndex = name === initial ? 0 : -1;
    panel.hidden = name !== initial;
  }
  return select;
}
