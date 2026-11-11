(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { locations: [], selectedId: null, busy: false };
  const els = {
    apiStatus: $('api-status'), visibleCount: $('visible-count'), updatedAt: $('updated-at'),
    pending: $('metric-pending'), high: $('metric-high'), approved: $('metric-approved'), rejected: $('metric-rejected'),
    loading: $('loading-state'), error: $('error-state'), errorMessage: $('error-message'), empty: $('empty-state'),
    emptyTitle: $('empty-title'), emptyMessage: $('empty-message'), list: $('records-list'), notice: $('notice'),
    search: $('search-input'), filter: $('status-filter'), detail: $('detail-panel'), backdrop: $('detail-backdrop'),
    detailTitle: $('detail-title'), detailId: $('detail-id'), detailContent: $('detail-content'), reviewControls: $('review-controls'),
    reviewer: $('reviewer-input'), notes: $('review-notes'), toast: $('toast')
  };
  const escapePath = (ref) => String(ref || '').split('/').map(encodeURIComponent).join('/');
  const evidenceUrl = (ref) => ref ? `/evidence/${escapePath(ref)}` : null;
  const text = (value, fallback = 'Not available') => value === null || value === undefined || value === '' ? fallback : String(value);
  const num = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  const severity = (issue) => String(issue.severity_level || issue.representative?.severity?.level || 'unknown').toLowerCase();
  const confidence = (issue) => Number(issue.representative?.confidence);
  const score = (issue) => Number(issue.priority?.score);
  const gps = (issue) => issue.representative?.gps || issue.observations?.find((x) => x.gps)?.gps || null;
  const statusLabel = (value) => String(value || 'unknown').replaceAll('_', ' ');
  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  }
  function button(label, className, handler) {
    const node = element('button', `button ${className}`, label);
    node.type = 'button'; node.addEventListener('click', handler); return node;
  }
  function showToast(message, isError = false) {
    els.toast.textContent = message; els.toast.className = `toast${isError ? ' toast-error' : ''}`; els.toast.hidden = false;
    window.clearTimeout(showToast.timer); showToast.timer = window.setTimeout(() => { els.toast.hidden = true; }, 3600);
  }
  function setBusyLoading(value) { els.loading.hidden = !value; if (value) { els.error.hidden = true; els.empty.hidden = true; els.list.hidden = true; } }
  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    return body;
  }
  async function loadLocations({ keepDetail = true } = {}) {
    setBusyLoading(true); els.error.hidden = true; els.empty.hidden = true; els.notice.hidden = true;
    try {
      const data = await requestJson('/locations?limit=500');
      if (!Array.isArray(data.locations)) throw new Error('The API response did not contain a locations list.');
      state.locations = data.locations;
      els.apiStatus.textContent = 'API connected'; document.querySelector('.status-dot').classList.add('online');
      els.updatedAt.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
      renderMetrics(); renderList();
      if (keepDetail && state.selectedId) {
        const selected = state.locations.find((item) => item.issue_id === state.selectedId);
        if (selected) await openDetail(selected.issue_id, false); else closeDetail();
      }
    } catch (error) {
      els.apiStatus.textContent = 'API unavailable'; document.querySelector('.status-dot').classList.remove('online');
      els.errorMessage.textContent = error.message || 'Unexpected API error.'; els.error.hidden = false; els.list.hidden = true;
      els.empty.hidden = true;
    } finally { setBusyLoading(false); }
  }
  function renderMetrics() {
    els.visibleCount.textContent = String(state.locations.length);
    els.pending.textContent = String(state.locations.filter((x) => !['approved', 'rejected'].includes(x.status)).length);
    els.high.textContent = String(state.locations.filter((x) => severity(x) === 'high' || x.requires_human_review).length);
    els.approved.textContent = String(state.locations.filter((x) => x.status === 'approved').length);
    els.rejected.textContent = String(state.locations.filter((x) => x.status === 'rejected').length);
  }
  function matchesFilter(issue) {
    const q = els.search.value.trim().toLowerCase();
    const filter = els.filter.value;
    if (filter !== 'all' && issue.status !== filter) return false;
    if (!q) return true;
    const point = gps(issue);
    const haystack = [issue.issue_id, issue.class, issue.status, severity(issue), point?.lat, point?.lon, point?.lng, issue.representative?.track_id].filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(q);
  }
  function renderList() {
    const filtered = state.locations.filter(matchesFilter);
    els.list.replaceChildren(); els.visibleCount.textContent = String(filtered.length);
    if (state.locations.length === 0) {
      els.emptyTitle.textContent = 'No inspection records yet';
      els.emptyMessage.textContent = 'No records have been returned by the API. Submit a road video through POST /analyze to create real findings.';
      els.empty.hidden = false; els.list.hidden = true; return;
    }
    if (!filtered.length) {
      els.emptyTitle.textContent = 'No findings match these filters';
      els.emptyMessage.textContent = 'Try a different search term or choose another status.';
      els.empty.hidden = false; els.list.hidden = true; return;
    }
    els.empty.hidden = true; els.list.hidden = false;
    filtered.forEach((issue, index) => els.list.appendChild(renderCard(issue, index + 1)));
  }
  function renderCard(issue, rank) {
    const card = element('article', 'record-card');
    const thumbWrap = element('div', 'thumb-wrap');
    const ref = issue.representative?.evidence_ref || issue.observations?.find((x) => x.evidence_ref)?.evidence_ref;
    const src = evidenceUrl(ref);
    if (src) {
      const img = element('img', 'evidence-thumb'); img.src = src; img.alt = `Evidence image for ${text(issue.class, 'road damage')}`; img.loading = 'lazy';
      img.addEventListener('error', () => { const fallback = evidencePlaceholder('Evidence image unavailable'); thumbWrap.replaceChildren(fallback); }); thumbWrap.appendChild(img);
    } else thumbWrap.appendChild(evidencePlaceholder('No evidence image attached'));
    thumbWrap.appendChild(element('span', 'rank-pill', `RANK #${rank}`)); card.appendChild(thumbWrap);
    const main = element('div', 'record-main');
    const top = element('div', 'record-topline'); top.appendChild(element('h3', '', issue.class || 'Unclassified damage'));
    top.appendChild(element('span', `badge badge-${severity(issue)}`, `${severity(issue)} severity`));
    top.appendChild(element('span', `badge badge-${issue.status}`, statusLabel(issue.status))); main.appendChild(top);
    main.appendChild(element('p', 'record-id', issue.issue_id));
    const point = gps(issue); const coords = point ? `${num(point.lat, 5)}, ${num(point.lon ?? point.lng, 5)}` : 'GPS unavailable · follow-up needed';
    const location = element('p', 'record-location'); location.appendChild(element('strong', '', 'GPS  ')); location.appendChild(document.createTextNode(coords)); main.appendChild(location);
    const facts = element('div', 'record-facts');
    const conf = confidence(issue);
    facts.appendChild(fact('Confidence', Number.isFinite(conf) ? `${Math.round(conf * 100)}%` : '—', Number.isFinite(conf) ? conf : null));
    facts.appendChild(fact('Damage size', issue.representative?.severity?.area_ratio != null ? `${(Number(issue.representative.severity.area_ratio) * 100).toFixed(1)}%` : 'Unknown'));
    facts.appendChild(fact('Recurrence', `${issue.recurrence_count || issue.observations?.length || 1} observation${(issue.recurrence_count || issue.observations?.length || 1) === 1 ? '' : 's'}`));
    facts.appendChild(fact('Traffic', issue.traffic_importance ?? 'Unknown')); main.appendChild(facts); card.appendChild(main);
    const side = element('div', 'record-side'); side.appendChild(element('span', 'priority-label', 'PRIORITY SCORE'));
    const scoreEl = element('div', 'priority-value'); scoreEl.appendChild(document.createTextNode(Number.isFinite(score(issue)) ? num(score(issue), 1) : '—')); scoreEl.appendChild(element('small', '', ' / 100')); side.appendChild(scoreEl);
    const track = element('div', 'priority-track'); const fill = element('span'); fill.style.width = `${Math.max(0, Math.min(100, Number.isFinite(score(issue)) ? score(issue) : 0))}%`; track.appendChild(fill); side.appendChild(track);
    side.appendChild(element('p', 'recurrence', `${issue.observations?.length || issue.recurrence_count || 1} linked observation(s)`));
    const actions = element('div', 'record-actions'); actions.appendChild(button('View details & trace', 'button-outline', () => openDetail(issue.issue_id)));
    if (!['approved', 'rejected'].includes(issue.status)) {
      actions.appendChild(button('Review finding', 'button-primary', () => openDetail(issue.issue_id)));
    }
    side.appendChild(actions); card.appendChild(side); return card;
  }
  function evidencePlaceholder(message) { const p = element('div', 'evidence-placeholder'); p.appendChild(element('span', '', '▧')); p.appendChild(element('small', '', message)); return p; }
  function fact(label, value, confidenceValue = null) {
    const wrap = element('div', 'fact'); wrap.appendChild(element('span', 'fact-label', label)); wrap.appendChild(element('span', 'fact-value', value));
    if (confidenceValue !== null) { const track = element('div', 'confidence-track'); const fill = element('div', 'confidence-fill'); fill.style.width = `${Math.max(0, Math.min(100, confidenceValue * 100))}%`; track.appendChild(fill); wrap.appendChild(track); }
    return wrap;
  }
  async function openDetail(id, fetchTrace = true) {
    const issue = state.locations.find((x) => x.issue_id === id); if (!issue) return;
    state.selectedId = id; els.detail.hidden = false; els.backdrop.hidden = false; els.detailTitle.textContent = issue.class || 'Road damage'; els.detailId.textContent = id;
    els.detailContent.replaceChildren(element('p', 'muted', 'Loading decision trace…'));
    const reviewed = ['approved', 'rejected'].includes(issue.status); els.reviewControls.hidden = reviewed;
    els.reviewer.value = ''; els.notes.value = '';
    try {
      const traceData = fetchTrace ? await requestJson(`/trace/${encodeURIComponent(id)}`) : await requestJson(`/trace/${encodeURIComponent(id)}`);
      renderDetail(issue, traceData.trace || []);
    } catch (error) { els.detailContent.replaceChildren(element('p', 'error-text', `Could not load decision trace: ${error.message}`)); }
  }
  function renderDetail(issue, trace) {
    els.detailContent.replaceChildren();
    const ref = issue.representative?.evidence_ref || issue.observations?.find((x) => x.evidence_ref)?.evidence_ref;
    const src = evidenceUrl(ref);
    if (src) { const img = element('img', 'detail-evidence'); img.src = src; img.alt = 'Road damage evidence'; img.addEventListener('error', () => img.replaceWith(evidencePlaceholder('Evidence image unavailable'))); els.detailContent.appendChild(img); }
    const stats = element('div', 'detail-stats');
    stats.appendChild(detailStat('Severity', severity(issue).toUpperCase())); stats.appendChild(detailStat('Confidence', Number.isFinite(confidence(issue)) ? `${Math.round(confidence(issue) * 100)}%` : '—')); stats.appendChild(detailStat('Priority', Number.isFinite(score(issue)) ? `${num(score(issue), 1)} / 100` : '—')); els.detailContent.appendChild(stats);
    const point = gps(issue); const gpsText = point ? `${text(point.lat)}, ${text(point.lon ?? point.lng)}` : 'No GPS coordinates available';
    appendSectionText(els.detailContent, 'Location & recurrence', `GPS: ${gpsText}\nObservations: ${issue.recurrence_count || issue.observations?.length || 1}\nStatus: ${statusLabel(issue.status)}\nTraffic importance: ${text(issue.traffic_importance, 'Not supplied')}`);
    if (issue.priority) {
      const components = issue.priority.components || {}; const weights = issue.priority.weights_used || {};
      const lines = Object.entries(components).map(([key, value]) => `${statusLabel(key)}: ${value == null ? 'not supplied' : num(Number(value) * 100, 1) + '%'}${weights[key] != null ? ` (weight ${(Number(weights[key]) * 100).toFixed(1)}%)` : ''}`);
      appendSectionText(els.detailContent, 'Priority score breakdown', `Score: ${num(score(issue), 2)} / 100\n${lines.join('\n')}`);
    }
    els.detailContent.appendChild(element('h3', 'section-title', `Decision trace · ${trace.length} event(s)`));
    if (!trace.length) { els.detailContent.appendChild(element('p', 'muted', 'No trace events were returned for this finding.')); return; }
    const list = element('div', 'trace-list');
    trace.forEach((event, index) => {
      const item = element('article', 'trace-item'); item.appendChild(element('h4', '', `${index + 1}. ${statusLabel(event.decision || 'decision event')}`));
      const fields = element('div', 'trace-grid');
      [['Evidence', event.evidence], ['Decision', event.decision], ['Action', event.action], ['Outcome', event.outcome]].forEach(([label, value]) => {
        const field = element('div', 'trace-field'); field.appendChild(element('b', '', `${label}:`));
        const val = typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : text(value); field.appendChild(element('p', '', val)); fields.appendChild(field);
      }); item.appendChild(fields); list.appendChild(item);
    }); els.detailContent.appendChild(list);
  }
  function detailStat(label, value) { const node = element('div', 'detail-stat'); node.appendChild(element('span', '', label)); node.appendChild(element('strong', '', value)); return node; }
  function appendSectionText(parent, title, content) { parent.appendChild(element('h3', 'section-title', title)); parent.appendChild(element('div', 'trace-item', content)); }
  function closeDetail() { state.selectedId = null; els.detail.hidden = true; els.backdrop.hidden = true; }
  async function submitReview(decision) {
    const id = state.selectedId; if (!id || state.busy) return;
    const confirmed = window.confirm(`${decision === 'approved' ? 'Approve' : 'Reject'} this road-damage finding? This decision will be recorded in the audit trace.`);
    if (!confirmed) return;
    state.busy = true; const controls = [...els.reviewControls.querySelectorAll('button')]; controls.forEach((b) => { b.disabled = true; });
    try {
      const result = await requestJson(`/locations/${encodeURIComponent(id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, reviewer: els.reviewer.value.trim() || null, notes: els.notes.value.trim() || null }) });
      const index = state.locations.findIndex((item) => item.issue_id === id);
      if (index >= 0) state.locations[index] = result.location;
      renderMetrics(); renderList();
      showToast(`Finding ${decision} and audit trace updated.`);
      await openDetail(id);
    } catch (error) { showToast(`Review could not be saved: ${error.message}`, true); }
    finally { state.busy = false; controls.forEach((b) => { b.disabled = false; }); }
  }
  $('refresh-button').addEventListener('click', () => loadLocations()); $('retry-button').addEventListener('click', () => loadLocations());
  els.search.addEventListener('input', renderList); els.filter.addEventListener('change', renderList);
  $('close-detail').addEventListener('click', closeDetail); els.backdrop.addEventListener('click', closeDetail);
  $('approve-button').addEventListener('click', () => submitReview('approved')); $('reject-button').addEventListener('click', () => submitReview('rejected'));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeDetail(); });
  loadLocations({ keepDetail: false });
})();
