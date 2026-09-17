'use strict';
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const DEMO_TOKEN = 'demo-metrics-token-for-local-only-000000';
  let state = null;
  let editing = null; // {card, queueItem}

  const token = () => $('token').value.trim();
  const who = () => $('who').value.trim();

  function status(text, error) {
    const el = $('status');
    el.textContent = text || '';
    el.hidden = !text;
    el.classList.toggle('error', !!error);
  }

  async function api(path, opts) {
    const r = await fetch(path, { ...opts, headers: { Authorization: 'Bearer ' + token(), ...(opts && opts.headers) } });
    if (r.status === 401) throw new Error('Неверный METRICS_TOKEN');
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || ('Ошибка ' + r.status));
    return body;
  }

  const post = (path, data) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

  function diffHtml(diff) {
    return diff.map(([op, t]) => op === 'delete' ? `<del>${esc(t)}</del>` : op === 'insert' ? `<ins>${esc(t)}</ins>` : esc(t)).join('');
  }

  function fmt(iso) { return iso ? iso.slice(0, 16).replace('T', ' ') : '—'; }

  // ------------------------------------------------------------ очередь
  function renderQueue() {
    const box = $('queue');
    box.replaceChildren();
    const items = state.queue;
    $('queue-empty').hidden = items.length > 0;
    const cardTitle = (id) => (state.cards.find((c) => c.id === id) || {}).title || '';
    items.forEach((it) => {
      const el = document.createElement('article');
      el.className = 'qi';
      const where = [it.source_title || it.source, 'версия ' + it.source_version, it.page ? 'стр. ' + it.page : null, it.unit ? 'фрагмент ' + it.unit : null].filter(Boolean).join(' · ');
      const kind = { changed: 'изменён', added: 'добавлен', removed: 'удалён' }[it.change] || it.change;
      el.innerHTML = `
        <div class="qi-head"><span class="tag ${esc(it.change)}">${kind}</span><span>${esc(where)}</span><span>${fmt(it.created)}</span></div>
        <div class="qi-card ${it.card_id ? '' : 'none'}">${it.card_id ? `<span class="id">${esc(it.card_id)}</span>${esc(cardTitle(it.card_id))} <span class="score">совпадение ${Math.round((it.score || 0) * 100)}%</span>` : 'Карточка не найдена — возможно, нужна новая тема или изменение к боту не относится'}</div>
        <div class="diff">${diffHtml(it.diff)}</div>
        <div class="qi-actions">
          <input placeholder="Комментарий (необязательно)" data-note>
          ${it.card_id ? `<button class="sm" data-act="open">Открыть карточку</button><button class="sm primary" data-act="confirmed">Текст актуален</button>` : ''}
          <button class="sm ghost" data-act="dismissed">Не относится</button>
        </div>`;
      el.querySelectorAll('button[data-act]').forEach((b) => b.addEventListener('click', () => act(it, b.dataset.act, el.querySelector('[data-note]').value)));
      box.appendChild(el);
    });
    const tb = $('resolved').querySelector('tbody');
    tb.replaceChildren();
    const label = { confirmed: 'актуально', edited: 'исправлено', dismissed: 'не относится' };
    [...state.resolved].reverse().forEach((it) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="muted">${fmt(it.resolved_at)}</td><td>${esc(it.source_title || it.source)}${it.page ? ', стр. ' + it.page : ''}</td><td class="id">${esc(it.card_id || '—')}</td><td>${label[it.status] || esc(it.status)}</td><td>${esc(it.resolved_by || '')}</td><td class="muted">${esc(it.note || '')}</td>`;
      tb.appendChild(tr);
    });
  }

  async function act(item, action, note) {
    if (action === 'open') return openEditor(item.card_id, item);
    if (!who()) return status('Укажите, кто работает (поле в шапке).', true);
    try {
      await post('/content/api/queue/resolve', { id: item.id, decision: action, who: who(), note });
      await load();
    } catch (e) { status(e.message, true); }
  }

  // ------------------------------------------------------------ карточки
  function renderCards() {
    const q = $('card-filter').value.trim().toLowerCase();
    const onlyDrafts = $('only-drafts').checked;
    const tb = $('cards').querySelector('tbody');
    tb.replaceChildren();
    let approved = 0, drafts = 0;
    state.cards.forEach((c) => {
      if (c.approved) approved += 1; else drafts += 1;
      if (onlyDrafts && c.approved) return;
      if (q && !(c.id.toLowerCase().includes(q) || c.title.toLowerCase().includes(q))) return;
      const badge = !c.available || c.status === 'retired' ? '<span class="badge off">снята</span>' : c.approved ? '<span class="badge ok">утверждена</span>' : '<span class="badge draft">ждёт утверждения</span>';
      const live = c.live_version ? (c.live_version === c.version ? c.version : `<span class="bad-text" title="бот выдаёт прежнюю версию">${esc(c.live_version)}</span>`) : '—';
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="id">${esc(c.id)}</td><td>${esc(c.title)}</td><td class="muted">${esc(c.source || '')}</td><td class="num">${esc(c.version)}</td><td class="num">${live}</td><td class="muted">${esc(c.date)}</td><td>${badge}</td><td>${esc(c.owner || '')}</td><td class="actions"><button class="sm" data-id="${esc(c.id)}">Открыть</button></td>`;
      tr.querySelector('button').addEventListener('click', () => openEditor(c.id, null));
      tb.appendChild(tr);
    });
    $('c-approved').textContent = approved;
    $('c-drafts').textContent = drafts;
  }

  // ------------------------------------------------------------ источники
  function renderSources() {
    const tb = $('sources').querySelector('tbody');
    tb.replaceChildren();
    $('sources-empty').hidden = state.sources.length > 0;
    state.sources.forEach((s) => {
      const refs = state.cards.filter((c) => c.source_key === s.key).length;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="id">${esc(s.key)}</td><td>${esc(s.title)}</td><td class="muted">${esc(s.filename)}</td><td class="num">${s.version}</td><td class="num">${s.units_count}</td><td class="muted">${fmt(s.uploaded)}</td><td class="num">${refs}</td>`;
      tb.appendChild(tr);
    });
  }

  $('src-key').addEventListener('change', () => { $('src-custom').hidden = $('src-key').value !== 'other'; });
  $('upload').addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = $('src-file').files[0];
    if (!file) return;
    const key = $('src-key').value === 'other' ? $('src-custom').value.trim() : $('src-key').value;
    $('upload-result').textContent = 'Загрузка и сравнение…';
    try {
      const q = new URLSearchParams({ key, title: $('src-title').value.trim(), filename: file.name });
      const r = await api('/content/api/sources/upload?' + q, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: await file.arrayBuffer() });
      $('upload-result').textContent = r.first_upload
        ? `Создан слепок «${r.source.title}» (версия 1, ${r.source.units_count} фрагментов). Следующая загрузка будет сравниваться с ним.`
        : r.changes === 0 ? 'Документ не изменился — слепок прежний.'
        : `Версия ${r.source.version}: изменено фрагментов — ${r.changes}, в очередь добавлено — ${r.queued}. Откройте вкладку «Очередь».`;
      $('src-file').value = '';
      await load();
    } catch (err) { $('upload-result').textContent = 'Ошибка: ' + err.message; }
  });

  // ------------------------------------------------------------ редактор
  async function openEditor(cardId, queueItem) {
    try {
      const card = await api('/content/api/cards/' + encodeURIComponent(cardId));
      editing = { card, queueItem };
      $('ed-title').textContent = card.id + ' · версия ' + card.version;
      $('ed-heading').value = card.title;
      $('ed-steps').value = card.steps.join('\n');
      $('ed-synonyms').value = card.synonyms.join(', ');
      $('ed-source').value = card.source || '';
      $('ed-url').value = card.url || '';
      $('ed-meta').textContent = `${card.approved ? 'Утверждена: ' + (card.owner || '') + ' ' + fmt(card.approved_at) : 'Не утверждена'} · дата ${card.date} · правок в истории: ${(card.history || []).length}`;
      const ctx = $('ed-context');
      if (queueItem) {
        ctx.hidden = false;
        ctx.innerHTML = `<b>Изменение источника:</b> ${esc(queueItem.source_title || queueItem.source)}${queueItem.page ? ', стр. ' + queueItem.page : ''}, фрагмент ${queueItem.unit}<div class="diff">${diffHtml(queueItem.diff)}</div>`;
      } else { ctx.hidden = true; }
      $('editor').hidden = false;
    } catch (e) { status(e.message, true); }
  }

  $('ed-close').addEventListener('click', () => { $('editor').hidden = true; editing = null; });
  $('editor').addEventListener('click', (e) => { if (e.target === $('editor')) { $('editor').hidden = true; editing = null; } });

  $('ed-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!who()) return status('Укажите, кто работает (поле в шапке).', true);
    const fields = {
      title: $('ed-heading').value.trim(),
      steps: $('ed-steps').value.split('\n').map((s) => s.trim()).filter(Boolean),
      synonyms: $('ed-synonyms').value.split(',').map((s) => s.trim()).filter(Boolean),
      source: $('ed-source').value.trim(),
      url: $('ed-url').value.trim(),
    };
    try {
      const r = await post('/content/api/cards/update', { id: editing.card.id, fields, editor: who() });
      if (editing.queueItem) await post('/content/api/queue/resolve', { id: editing.queueItem.id, decision: 'edited', who: who(), note: 'новая версия ' + r.card.version });
      status(r.reloaded ? `Сохранена версия ${r.card.version}; бот перечитал карточки.` : `Сохранена версия ${r.card.version}. Бот продолжит выдавать прежнюю версию, пока карточка не утверждена.`);
      $('editor').hidden = true; editing = null;
      await load();
    } catch (err) { status(err.message, true); }
  });

  $('ed-approve').addEventListener('click', async () => {
    if (!who()) return status('Укажите, кто утверждает (поле в шапке).', true);
    try {
      const r = await post('/content/api/cards/approve', { id: editing.card.id, reviewer: who(), approved: true });
      status(`Версия ${r.card.version} утверждена: ${r.card.owner}.` + (r.reloaded ? '' : ' Для выдачи в production все карточки должны быть утверждены.'));
      $('editor').hidden = true; editing = null;
      await load();
    } catch (err) { status(err.message, true); }
  });

  $('ed-revoke').addEventListener('click', async () => {
    try {
      await post('/content/api/cards/approve', { id: editing.card.id, reviewer: who(), approved: false });
      status('Утверждение снято.');
      $('editor').hidden = true; editing = null;
      await load();
    } catch (err) { status(err.message, true); }
  });

  // ------------------------------------------------------------ вкладки и загрузка
  $('tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-tab]');
    if (!b) return;
    $('tabs').querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach((t) => { t.hidden = t.id !== 'tab-' + b.dataset.tab; });
  });
  $('card-filter').addEventListener('input', renderCards);
  $('only-drafts').addEventListener('change', renderCards);

  async function load() {
    try {
      state = await api('/content/api/state');
      status('');
      $('c-queue').textContent = state.queue.length;
      $('c-cards').textContent = state.cards.length;
      $('c-sources').textContent = state.sources.length;
      $('mode').hidden = false;
      $('mode').textContent = state.mode === 'production' ? 'Режим production: правки применяются в боте только после утверждения всех карточек.' : 'Режим demo: правки видны в тестовой консоли сразу, черновики помечаются.';
      renderQueue(); renderCards(); renderSources();
      try { sessionStorage.setItem('metrics_token', token()); sessionStorage.setItem('who', who()); } catch (e) { /* ignore */ }
    } catch (e) { status(e.message, true); }
  }

  $('auth').addEventListener('submit', (e) => { e.preventDefault(); load(); });
  let saved = '', savedWho = '';
  try { saved = sessionStorage.getItem('metrics_token') || ''; savedWho = sessionStorage.getItem('who') || ''; } catch (e) { /* ignore */ }
  $('token').value = saved || DEMO_TOKEN;
  $('who').value = savedWho;
  load();
})();
