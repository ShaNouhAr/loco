/* Le serveur valide les droits et leurs prérequis ; le formulaire fonctionne aussi sans JS. */
// Traductions fournies par app.js.
const tr = window.t || ((s, v) => Object.keys(v || {}).reduce((o, k) => o.split('%(' + k + ')s').join(v[k]), s));
(() => {
  const form = document.querySelector('[data-permission-form]');
  if (!form) return;
  const boxes = new Map([...form.querySelectorAll('[data-permission]')].map(box => [box.value, box]));
  const groups = [...form.querySelectorAll('[data-permission-group]')];
  const search = form.querySelector('[data-permission-search]');
  const filter = form.querySelector('[data-permission-filter]');
  const feedback = form.querySelector('[data-permission-feedback]');
  const required = box => JSON.parse(box.dataset.requires);
  const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr');
  const original = new Set([...boxes.values()].filter(b => b.checked).map(b => b.value));
  form.querySelectorAll('[data-permission-tools]').forEach(el => { el.hidden = false; });
  function select(box) {
    if (!box || box.disabled || box.checked) return;
    box.checked = true;
    required(box).forEach(key => select(boxes.get(key)));
  }
  function removeDependents() {
    let changed;
    do {
      changed = false;
      for (const candidate of boxes.values()) {
        if (candidate.checked && !candidate.disabled && required(candidate).some(key => !boxes.get(key)?.checked)) {
          candidate.checked = false; changed = true;
        }
      }
    } while (changed);
  }
  function refresh() {
    const query = normalize(search.value.trim()), mode = filter.value;
    let visible = 0, selected = 0, sensitive = 0, modified = 0;
    for (const box of boxes.values()) {
      const row = box.closest('.permission-option');
      if (box.checked) { selected++; if (row.dataset.sensitive === 'true') sensitive++; }
      if (box.checked !== original.has(box.value)) modified++;
      row.hidden = !normalize(row.textContent).includes(query) || (mode === 'selected' && !box.checked) || (mode === 'sensitive' && row.dataset.sensitive !== 'true');
      if (!row.hidden) visible++;
    }
    for (const group of groups) {
      const all = [...group.querySelectorAll('[data-permission]')];
      const shown = all.filter(box => !box.closest('.permission-option').hidden);
      const eligible = shown.filter(box => !box.disabled);
      const count = all.filter(box => box.checked).length;
      group.hidden = shown.length === 0;
      group.querySelector('[data-permission-count]').textContent = `${count} / ${all.length} accordés`;
      const nav = form.querySelector(`[data-permission-nav="${group.dataset.permissionGroup}"]`);
      nav.hidden = group.hidden;
      nav.querySelector('[data-permission-nav-count]').textContent = `${count}/${all.length}`;
      const toggle = group.querySelector('[data-permission-category]');
      if (toggle) {
        const checked = eligible.filter(box => box.checked).length;
        toggle.checked = eligible.length > 0 && checked === eligible.length;
        toggle.indeterminate = checked > 0 && checked < eligible.length;
        toggle.disabled = eligible.length === 0;
        toggle.nextElementSibling.textContent = toggle.checked ? tr('Tout désélectionner') : tr('Tout sélectionner');
        toggle.title = tr('Agit sur les droits visibles de cette catégorie.');
      }
    }
    form.querySelector('[data-permission-empty]').hidden = visible > 0;
    form.querySelector('[data-permission-total]').textContent = `${selected} / ${boxes.size} droits accordés · ${sensitive} sensibles`;
    form.querySelector('[data-permission-summary]').textContent = `${selected} droit${selected === 1 ? " accordé" : "s accordés"}${modified ? ` · ${modified} modifications` : ''}`;
  }
  form.addEventListener('change', event => {
    const target = event.target;
    const before = new Map([...boxes].map(([key, box]) => [key, box.checked]));
    const directlyChanged = new Set();
    if (target.matches('[data-permission]')) {
      directlyChanged.add(target.value);
      if (target.checked) required(target).forEach(key => select(boxes.get(key)));
      else removeDependents();
    } else if (target.matches('[data-permission-category]')) {
      const eligible = [...target.closest('[data-permission-group]').querySelectorAll('[data-permission]')].filter(box => !box.disabled && !box.closest('.permission-option').hidden);
      eligible.forEach(box => { directlyChanged.add(box.value); if (target.checked) select(box); else box.checked = false; });
      if (!target.checked) removeDependents();
    }
    const automatic = [...boxes].filter(([key, box]) => !directlyChanged.has(key) && before.get(key) !== box.checked).length;
    if (directlyChanged.size) feedback.textContent = automatic
      ? (automatic === 1 ? tr('%(num)s droit lié ajusté automatiquement, y compris dans les autres catégories.', { num: automatic })
                         : tr('%(num)s droits liés ajustés automatiquement, y compris dans les autres catégories.', { num: automatic }))
      : tr('Les droits nécessaires à une action sont sélectionnés automatiquement.');
    refresh();
  });
  search.addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
  search.addEventListener('input', refresh);
  filter.addEventListener('change', refresh);
  refresh();
})();
