'use strict';
(function () {
  const $ = (id) => document.getElementById(id);
  const pct = (v) => (v === null || v === undefined ? '—' : Math.round(v * 100) + '%');
  const num = (v) => (v === null || v === undefined ? '—' : String(v));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const DEMO_TOKEN = 'demo-metrics-token-for-local-only-000000';

  function status(text, error) {
    const el = $('status');
    el.textContent = text || '';
    el.hidden = !text;
    el.classList.toggle('error', !!error);
  }

  function kpi(id, value, cls) {
    const el = $(id);
    el.textContent = value;
    if (cls !== undefined) el.className = 'kpi-value ' + cls;
  }

  function targetBar(barId, value, target) {
    const fill = $(barId);
    fill.style.width = value === null ? '0' : Math.min(100, Math.round(value * 100)) + '%';
    fill.classList.toggle('bad', value !== null && value < target);
  }

  function bars(id, rows, alt) {
    const box = $(id);
    box.replaceChildren();
    const max = Math.max(1, ...rows.map((r) => r.count));
    rows.forEach((r) => {
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML = `<div class="label"><span>${esc(r.name)}</span><span class="sub">${esc(r.sub || '')}</span></div><b>${r.count}</b>` +
        `<div class="track"><div class="fill${alt ? ' alt' : ''}" style="width:${(r.count / max) * 100}%"></div></div>`;
      box.appendChild(row);
    });
    if (!rows.length) box.innerHTML = '<p class="empty">Нет данных за период.</p>';
  }

  function seriesChart(series) {
    const W = 900, H = 220, padL = 34, padB = 26, padT = 10;
    const n = series.length;
    const max = Math.max(1, ...series.map((d) => Math.max(d.sessions, d.views, d.ratings)));
    const x = (i) => padL + (i + 0.5) * ((W - padL) / n);
    const y = (v) => padT + (H - padT - padB) * (1 - v / max);
    const path = (key) => series.map((d, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(d[key]).toFixed(1)).join(' ');
    const cols = { sessions: '#1C3F24', views: '#5FB233', ratings: '#B9DD8C' };
    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Динамика по дням">`;
    for (let g = 0; g <= 4; g++) {
      const v = Math.round((max * g) / 4), yy = y(v).toFixed(1);
      svg += `<line x1="${padL}" x2="${W}" y1="${yy}" y2="${yy}" stroke="#E6EAE6"/><text class="axis" x="${padL - 6}" y="${(+yy) + 4}" text-anchor="end">${v}</text>`;
    }
    const barW = Math.max(2, (W - padL) / n * 0.28);
    series.forEach((d, i) => {
      svg += `<rect x="${(x(i) - barW / 2).toFixed(1)}" y="${y(d.views).toFixed(1)}" width="${barW.toFixed(1)}" height="${(H - padB - y(d.views)).toFixed(1)}" fill="${cols.views}" opacity=".35"/>`;
    });
    for (const key of ['sessions', 'views', 'ratings']) {
      svg += `<path d="${path(key)}" fill="none" stroke="${cols[key]}" stroke-width="2.5" stroke-linejoin="round"/>`;
    }
    const step = Math.ceil(n / 8);
    let lastLabel = -step;
    series.forEach((d, i) => {
      if ((i % step === 0 || i === n - 1) && i - lastLabel >= step / 2) {
        lastLabel = i;
        svg += `<text class="axis" x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${d.date.slice(5)}</text>`;
      }
    });
    $('series').innerHTML = svg + '</svg>';
  }

  function table(id, rows, render, emptyId) {
    const body = $(id).querySelector('tbody');
    body.replaceChildren();
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.innerHTML = render(r);
      body.appendChild(tr);
    });
    if (emptyId) $(emptyId).hidden = rows.length > 0;
  }

  function fmtTime(iso) {
    return iso ? iso.slice(5, 16).replace('T', ' ') : '—';
  }

  function render(m) {
    const s = m.summary;
    kpi('k-sessions', num(s.sessions));
    $('k-participants').textContent = num(s.participants);
    kpi('k-appeals', num(s.appeals));
    $('k-per-session').textContent = s.appeals_per_session === null ? '—' : s.appeals_per_session.toFixed(1);
    kpi('k-coverage', pct(s.feedback_coverage), s.coverage_ok === null ? 'none' : s.coverage_ok ? 'ok' : 'bad');
    $('k-ratings').textContent = num(s.ratings);
    targetBar('b-coverage', s.feedback_coverage, m.targets.feedback_coverage);
    kpi('k-helpfulness', pct(s.helpfulness), s.helpfulness_ok === null ? 'none' : s.helpfulness_ok ? 'ok' : 'bad');
    targetBar('b-helpfulness', s.helpfulness, m.targets.helpfulness);
    kpi('k-gaps', num(m.gaps.length));
    $('k-miss').textContent = num(s.search_miss);
    $('k-unknown').textContent = num(s.unknown_input);

    seriesChart(m.series);
    bars('sections', m.sections.map((x) => ({ name: x.name, count: x.views, sub: x.helpfulness === null ? '' : 'полезность ' + pct(x.helpfulness) })));
    bars('reasons', m.reasons.map((x) => ({ name: x.name, count: x.count })), true);

    table('top', m.top_cards, (c) => `<td class="id">${esc(c.id)}</td><td>${esc(c.title)}</td><td class="num">${c.views}</td><td class="num">${c.rated}</td>` +
      `<td class="num ${c.helpfulness === null ? '' : c.helpfulness >= m.targets.helpfulness ? 'ok-text' : 'bad-text'}">${pct(c.helpfulness)}</td>`);
    table('gaps', m.gaps, (c) => `<td class="id">${esc(c.id)}</td><td>${esc(c.title)}</td><td class="num bad-text">${c.not_helped}</td>` +
      `<td>${Object.entries(c.reasons).map(([k, v]) => `<span class="pill warn">${esc(k)} · ${v}</span>`).join('') || '—'}</td>`, 'gaps-empty');

    $('k-labeled').textContent = num(s.participants_labeled) + ' из ' + num(s.participants);
    table('participants', m.participants, (u) => `<td class="id">${esc(u.id)}</td>` +
      `<td class="${u.label ? '' : 'muted'}">${u.label ? esc(u.label) : 'без подписи'}</td>` +
      `<td class="num">${u.sessions}</td><td class="num">${u.appeals}</td><td class="num">${u.rated}</td>` +
      `<td class="num ${u.helpfulness === null ? '' : u.helpfulness >= m.targets.helpfulness ? 'ok-text' : 'bad-text'}">${pct(u.helpfulness)}</td>` +
      `<td class="muted">${fmtTime(u.first_seen)}</td><td class="muted">${fmtTime(u.last_seen)}</td>`);

    $('o-errors').textContent = num(s.material_errors);
    $('o-expired').textContent = num(s.delivery_expired);
    $('o-pending').textContent = num(m.delivery && m.delivery.pending);
    $('o-attachments').textContent = num(s.attachments);
    $('o-mode').textContent = m.mode === 'production' ? 'production' : 'demo (Rooms не подключён)';
    $('period').textContent = `${m.period.since.slice(0, 10)} — ${m.period.until.slice(0, 10)} (UTC)`;
    $('kpis').hidden = false;
    $('charts').hidden = false;
  }

  let days = '30';

  async function load() {
    const token = $('token').value.trim();
    status('Загрузка…');
    try {
      const r = await fetch(`/metrics/product?days=${encodeURIComponent(days)}`, {
        headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(12000),
      });
      if (r.status === 401) return status('Неверный METRICS_TOKEN.', true);
      if (!r.ok) return status('Сервер вернул ошибку ' + r.status + '.', true);
      render(await r.json());
      status('');
      try { sessionStorage.setItem('metrics_token', token); } catch (e) { /* ignore */ }
    } catch (e) {
      status('Не удалось получить данные: ' + e.message, true);
    }
  }

  // Подсказки «i» на KPI: текст из data-info, показывается по наведению/фокусу.
  document.querySelectorAll('.kpi[data-info]').forEach((k) => {
    const tip = document.createElement('span');
    tip.className = 'info';
    tip.textContent = k.dataset.info;
    k.tabIndex = 0;
    k.appendChild(tip);
  });
  $('auth').addEventListener('submit', (e) => { e.preventDefault(); load(); });
  $('days').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-days]');
    if (!b) return;
    days = b.dataset.days;
    $('days').querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    load();
  });
  let saved = '';
  try { saved = sessionStorage.getItem('metrics_token') || ''; } catch (e) { /* ignore */ }
  $('token').value = saved || DEMO_TOKEN;
  load();
})();
