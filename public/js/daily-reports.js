/* Phase DT-2a: Laporan Harian karyawan (page-daily-mine).
   Endpoint: /api/daily-reports/{mine,today,{rid},{rid}/items,{rid}/submit,{rid}/feedback}
   Palet: Luxury UMAR (gold+charcoal+cream). */
(function() {
  const DR_COLORS = {
    gold: '#B8860B', goldBright: '#F8BE20', charcoal: '#1D1D1B',
    cream: '#FFFDF7', gray500: '#6B7280', gray700: '#374151',
    red: '#DC2626', green: '#166534', amber: '#B45309',
  };

  // State module (per user session)
  const DR = {
    report: null,         // current today's report {id, status, summary_text, mood, ...}
    items: [],            // task items
    feedback: [],         // feedback thread
    history: [],          // 30-hari history
    initialized: false,
  };

  // XSS guard -- nama task/desc/summary bebas diketik user, escape sebelum inject.
  function drEsc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function drFetch(url, opts) {
    return (window.authFetch || fetch)(url, opts);
  }

  function drFormatDate(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('id-ID', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  }

  function drFormatShort(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('id-ID', { day: '2-digit', month: 'short' });
  }

  function drFormatDateTime(iso) {
    if (!iso) return '-';
    const d = new Date(iso.replace(' ', 'T') + 'Z');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('id-ID', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  // -------------------- MINI PANEL (Phase DT-2b) --------------------
  // Populates `.dr-mini-panel` elements di 5 home page. Ambil 1 hit ke
  // /today (upsert idempotent) supaya panel selalu punya row draft yg
  // valid utk klik "Buka" -- konsisten dgn initDailyMine.
  window.initDailyMineMini = async function() {
    const panels = document.querySelectorAll('.dr-mini-panel');
    if (!panels.length) return;
    try {
      const r = await drFetch('/api/daily-reports/today', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      if (!r.ok) return;
      const report = await r.json();
      // Ambil detail utk hitung item count
      const detailR = await drFetch(`/api/daily-reports/${report.id}`);
      const detail = detailR.ok ? await detailR.json() : { items: [] };
      const items = detail.items || [];
      const done = items.filter(t => t.status === 'Done').length;
      const total = items.length;
      panels.forEach(panel => {
        const stEl = panel.querySelector('.dr-mini-status');
        const prEl = panel.querySelector('.dr-mini-progress');
        if (stEl) {
          stEl.textContent = report.status || 'Draft';
          stEl.style.color = report.status === 'Submitted' ? '#166534' : '#92400E';
        }
        if (prEl) prEl.textContent = `${done}/${total} task selesai`;
      });
    } catch (e) { /* silent */ }
  };

  // -------------------- INIT --------------------
  window.initDailyMine = async function() {
    document.getElementById('dr-today-date').textContent =
      drFormatDate(new Date().toISOString().slice(0, 10));
    // Load atau create hari ini
    await drLoadOrCreateToday();
    await drLoadHistory();
    if (typeof lucide !== 'undefined') lucide.createIcons();
  };

  async function drLoadOrCreateToday() {
    try {
      const r = await drFetch('/api/daily-reports/today', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error('Gagal load laporan hari ini.');
      const report = await r.json();
      DR.report = report;
      // Ambil detail lengkap (items + feedback)
      await drLoadDetail(report.id);
    } catch (e) {
      console.error('[dr] loadToday error', e);
    }
  }

  async function drLoadDetail(rid) {
    try {
      const r = await drFetch(`/api/daily-reports/${rid}`);
      if (!r.ok) return;
      const detail = await r.json();
      DR.report = detail;
      DR.items = detail.items || [];
      DR.feedback = detail.feedback || [];
      drRender();
    } catch (e) {
      console.error('[dr] loadDetail error', e);
    }
  }

  async function drLoadHistory() {
    try {
      const r = await drFetch('/api/daily-reports/mine?days=30');
      if (!r.ok) return;
      DR.history = await r.json();
      drRenderHistory();
    } catch (e) {}
  }

  // -------------------- RENDER --------------------
  function drRender() {
    if (!DR.report) return;

    // Header status
    const statusEl = document.getElementById('dr-today-status');
    if (statusEl) {
      const isSubmitted = DR.report.status === 'Submitted';
      statusEl.textContent = isSubmitted ? 'Submitted' : 'Draft';
      if (isSubmitted) {
        statusEl.style.background = '#DCFCE7';
        statusEl.style.color = '#166534';
        statusEl.style.borderColor = '#16A34A';
      } else {
        statusEl.style.background = '#FEF3C7';
        statusEl.style.color = '#92400E';
        statusEl.style.borderColor = '#D97706';
      }
    }

    // Summary + mood
    document.getElementById('dr-summary').value = DR.report.summary_text || '';
    document.getElementById('dr-mood').value = DR.report.mood || '';

    // Lock inputs kalau Submitted
    const locked = DR.report.status === 'Submitted';
    document.getElementById('dr-summary').disabled = locked;
    document.getElementById('dr-mood').disabled = locked;
    document.getElementById('dr-btn-add-task').disabled = locked;
    document.getElementById('dr-btn-submit').disabled = locked;
    document.getElementById('dr-btn-submit').style.opacity = locked ? '0.5' : '1';
    document.getElementById('dr-btn-add-task').style.opacity = locked ? '0.5' : '1';

    drRenderTasks();
    drRenderFeedback();
  }

  function drRenderTasks() {
    const el = document.getElementById('dr-task-list');
    const progressEl = document.getElementById('dr-task-progress');
    if (!el) return;

    const items = DR.items || [];
    const done = items.filter(t => t.status === 'Done').length;
    if (progressEl) progressEl.textContent = `${done}/${items.length} selesai`;

    if (!items.length) {
      el.innerHTML = '<p class="text-xs italic text-center py-6" style="color:#9CA3AF;">Belum ada task hari ini. Klik "Task Baru" di atas.</p>';
      return;
    }

    const locked = DR.report && DR.report.status === 'Submitted';
    el.innerHTML = items.map(t => {
      const isDone = t.status === 'Done';
      const isSkipped = t.status === 'Skipped';
      const isProg = t.status === 'InProgress';
      const statusColor = isDone ? DR_COLORS.green :
                         isProg ? DR_COLORS.gold :
                         isSkipped ? DR_COLORS.gray500 : DR_COLORS.gray700;
      const priorityBadge = t.priority === 'high'
        ? `<span class="text-[9px] font-bold px-1.5 py-0.5 rounded ml-1" style="background:#FEE2E2;color:${DR_COLORS.red};">HIGH</span>`
        : t.priority === 'low'
        ? `<span class="text-[9px] font-bold px-1.5 py-0.5 rounded ml-1" style="background:#F3F4F6;color:${DR_COLORS.gray500};">LOW</span>`
        : '';
      const linkLabel = t.linked_entity_type
        ? `<span class="text-[10px] px-1.5 py-0.5 rounded ml-1" style="background:#EEF2FF;color:#3730A3;">${drEsc(t.linked_entity_type)}#${Number(t.linked_entity_id) || 0}</span>`
        : '';

      // Icon status toggle (bukan check checkbox biar lebih mobile-friendly)
      const toggleIcon = isDone
        ? `<button ${locked ? 'disabled' : ''} onclick="drToggleItemStatus(${t.id})" title="Batalkan status Done" style="color:${DR_COLORS.green};">
             <i data-lucide="check-circle-2" class="w-5 h-5"></i>
           </button>`
        : `<button ${locked ? 'disabled' : ''} onclick="drToggleItemStatus(${t.id})" title="Tandai selesai" style="color:${DR_COLORS.gray500};">
             <i data-lucide="circle" class="w-5 h-5"></i>
           </button>`;

      return `<div class="border rounded-lg p-3 flex items-start gap-3" style="background:#FAFAF9;border-color:#E5E7EB;">
        <div class="mt-0.5 shrink-0">${toggleIcon}</div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center flex-wrap gap-1">
            <b class="text-sm ${isDone ? 'line-through' : ''}" style="color:${statusColor};">${drEsc(t.title)}</b>
            ${priorityBadge}
            ${linkLabel}
          </div>
          ${t.description ? `<p class="text-[11px] mt-1" style="color:${DR_COLORS.gray500};">${drEsc(t.description)}</p>` : ''}
          <p class="text-[10px] mt-1" style="color:${DR_COLORS.gray500};">Status: <b>${drEsc(t.status)}</b>${t.completed_at ? ` &middot; selesai ${drFormatDateTime(t.completed_at)}` : ''}</p>
        </div>
        <div class="shrink-0 flex items-center gap-1">
          <button ${locked ? 'disabled' : ''} onclick="drEditItem(${t.id})" class="text-[10px] p-1 rounded" title="Edit" style="color:${DR_COLORS.gray500};">
            <i data-lucide="pencil" class="w-3.5 h-3.5"></i>
          </button>
          <button ${locked ? 'disabled' : ''} onclick="drDeleteItem(${t.id})" class="text-[10px] p-1 rounded" title="Hapus" style="color:${DR_COLORS.red};">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>`;
    }).join('');

    if (typeof lucide !== 'undefined') lucide.createIcons();
  }

  function drRenderFeedback() {
    const el = document.getElementById('dr-feedback-thread');
    const countEl = document.getElementById('dr-feedback-count');
    if (!el) return;

    const list = DR.feedback || [];
    if (countEl) countEl.textContent = `${list.length} komentar`;

    if (!list.length) {
      el.innerHTML = '<p class="text-xs italic text-center py-4" style="color:#9CA3AF;">Belum ada feedback dari manajemen.</p>';
      return;
    }

    el.innerHTML = list.map(c => {
      const isMgmt = c.is_from_management === 1 || c.is_from_management === true;
      const bg = isMgmt ? '#FEF3C7' : '#EEF2FF';
      const border = isMgmt ? '#D97706' : '#3730A3';
      const label = isMgmt ? 'Manajemen' : (c.user_name || 'Saya');
      const align = isMgmt ? 'items-start' : 'items-end';
      return `<div class="flex ${align} flex-col">
        <div class="max-w-[85%] rounded-lg p-2.5 border-l-2" style="background:${bg};border-color:${border};">
          <p class="text-[10px] font-bold" style="color:${border};">${drEsc(label)}</p>
          <p class="text-xs mt-0.5" style="color:${DR_COLORS.charcoal};">${drEsc(c.comment_text)}</p>
          <p class="text-[9px] mt-1" style="color:${DR_COLORS.gray500};">${drFormatDateTime(c.created_at)}</p>
        </div>
      </div>`;
    }).join('');
  }

  function drRenderHistory() {
    const el = document.getElementById('dr-history-list');
    if (!el) return;
    const list = (DR.history || []).filter(r =>
      !DR.report || r.id !== DR.report.id
    );
    if (!list.length) {
      el.innerHTML = '<p class="text-xs italic text-center py-4" style="color:#9CA3AF;">Belum ada riwayat.</p>';
      return;
    }
    el.innerHTML = list.map(r => {
      const submitted = r.status === 'Submitted';
      const badgeBg = submitted ? '#DCFCE7' : '#FEF3C7';
      const badgeColor = submitted ? '#166534' : '#92400E';
      const doneCount = Number(r.done_count) || 0;
      const totalCount = Number(r.item_count) || 0;
      return `<div class="flex items-center justify-between p-2 border rounded" style="background:#FAFAF9;border-color:#E5E7EB;">
        <div class="flex items-center gap-2">
          <span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:${badgeBg};color:${badgeColor};">${drEsc(r.status)}</span>
          <span class="text-xs font-medium" style="color:${DR_COLORS.charcoal};">${drFormatShort(r.report_date)}</span>
        </div>
        <div class="text-[10px]" style="color:${DR_COLORS.gray500};">${doneCount}/${totalCount} task selesai</div>
      </div>`;
    }).join('');
  }

  // -------------------- ACTIONS --------------------
  window.drSaveSummary = async function() {
    if (!DR.report) return;
    if (DR.report.status === 'Submitted') {
      alert('Laporan sudah di-submit, tidak bisa diubah.');
      return;
    }
    const summary = document.getElementById('dr-summary').value;
    const mood = document.getElementById('dr-mood').value || null;
    try {
      const r = await drFetch(`/api/daily-reports/${DR.report.id}`, {
        method: 'PUT',
        body: JSON.stringify({ summary_text: summary, mood: mood }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || err.error || 'Gagal simpan ringkasan.');
      }
      DR.report.summary_text = summary;
      DR.report.mood = mood;
      document.getElementById('dr-summary-hint').textContent = 'Tersimpan.';
      setTimeout(() => {
        const h = document.getElementById('dr-summary-hint');
        if (h) h.textContent = 'Otomatis tersimpan saat klik "Simpan" atau "Submit".';
      }, 2000);
    } catch (e) { alert(e.message); }
  };

  window.drSubmitReport = async function() {
    if (!DR.report) return;
    if (DR.report.status === 'Submitted') {
      alert('Laporan sudah di-submit.');
      return;
    }
    // Auto-save summary dulu supaya submit tidak ketinggalan
    await window.drSaveSummary();
    if (!confirm('Submit laporan hari ini? Setelah submit tidak bisa diubah.')) return;
    try {
      const r = await drFetch(`/api/daily-reports/${DR.report.id}/submit`, {
        method: 'POST',
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(data.detail || data.error || 'Gagal submit.');
      }
      alert(data.message || 'Laporan berhasil di-submit.');
      await drLoadDetail(DR.report.id);
      await drLoadHistory();
    } catch (e) { alert('Error: ' + e.message); }
  };

  // Task Item CRUD via modal
  window.drAddItemModal = function() {
    if (DR.report && DR.report.status === 'Submitted') {
      alert('Laporan sudah di-submit, tidak bisa tambah task.');
      return;
    }
    document.getElementById('dr-item-modal-title').textContent = 'Task Baru';
    document.getElementById('dr-item-id').value = '';
    document.getElementById('dr-item-title').value = '';
    document.getElementById('dr-item-desc').value = '';
    document.getElementById('dr-item-priority').value = 'medium';
    document.getElementById('dr-item-status').value = 'Pending';
    document.getElementById('dr-item-link-type').value = '';
    document.getElementById('dr-item-link-id').value = '';
    document.getElementById('dr-item-modal').classList.remove('hidden');
  };

  window.drCloseItemModal = function() {
    document.getElementById('dr-item-modal').classList.add('hidden');
  };

  window.drEditItem = function(iid) {
    const item = DR.items.find(t => t.id === iid);
    if (!item) return;
    document.getElementById('dr-item-modal-title').textContent = 'Edit Task';
    document.getElementById('dr-item-id').value = item.id;
    document.getElementById('dr-item-title').value = item.title || '';
    document.getElementById('dr-item-desc').value = item.description || '';
    document.getElementById('dr-item-priority').value = item.priority || 'medium';
    document.getElementById('dr-item-status').value = item.status || 'Pending';
    document.getElementById('dr-item-link-type').value = item.linked_entity_type || '';
    document.getElementById('dr-item-link-id').value = item.linked_entity_id || '';
    document.getElementById('dr-item-modal').classList.remove('hidden');
  };

  window.drSaveItem = async function() {
    if (!DR.report) return;
    const title = document.getElementById('dr-item-title').value.trim();
    if (!title) {
      alert('Judul task wajib diisi.');
      return;
    }
    const linkType = document.getElementById('dr-item-link-type').value || null;
    const linkIdRaw = document.getElementById('dr-item-link-id').value;
    const linkId = linkIdRaw ? parseInt(linkIdRaw, 10) : null;
    if (linkType && (!linkId || linkId <= 0)) {
      alert('ID entity wajib diisi kalau pilih link type.');
      return;
    }
    const body = {
      title: title,
      description: document.getElementById('dr-item-desc').value || null,
      priority: document.getElementById('dr-item-priority').value,
      status: document.getElementById('dr-item-status').value,
      linked_entity_type: linkType,
      linked_entity_id: linkId,
    };
    const iid = document.getElementById('dr-item-id').value;
    try {
      let r;
      if (iid) {
        r = await drFetch(`/api/daily-reports/items/${iid}`, {
          method: 'PUT',
          body: JSON.stringify(body),
        });
      } else {
        r = await drFetch(`/api/daily-reports/${DR.report.id}/items`, {
          method: 'POST',
          body: JSON.stringify(body),
        });
      }
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(data.detail || data.error || 'Gagal simpan task.');
      }
      window.drCloseItemModal();
      await drLoadDetail(DR.report.id);
    } catch (e) { alert('Error: ' + e.message); }
  };

  window.drToggleItemStatus = async function(iid) {
    const item = DR.items.find(t => t.id === iid);
    if (!item) return;
    const nextStatus = item.status === 'Done' ? 'Pending' : 'Done';
    try {
      const r = await drFetch(`/api/daily-reports/items/${iid}`, {
        method: 'PUT',
        body: JSON.stringify({ status: nextStatus }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || err.error || 'Gagal update.');
      }
      await drLoadDetail(DR.report.id);
    } catch (e) { alert(e.message); }
  };

  window.drDeleteItem = async function(iid) {
    if (!confirm('Hapus task ini?')) return;
    try {
      const r = await drFetch(`/api/daily-reports/items/${iid}`, {
        method: 'DELETE',
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || err.error || 'Gagal hapus.');
      }
      await drLoadDetail(DR.report.id);
    } catch (e) { alert(e.message); }
  };

  window.drPostFeedback = async function() {
    if (!DR.report) return;
    const inputEl = document.getElementById('dr-feedback-input');
    const text = (inputEl.value || '').trim();
    if (!text) {
      alert('Isi komentar dulu.');
      return;
    }
    try {
      const r = await drFetch(`/api/daily-reports/${DR.report.id}/feedback`, {
        method: 'POST',
        body: JSON.stringify({ comment_text: text }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(data.detail || data.error || 'Gagal kirim komentar.');
      }
      inputEl.value = '';
      await drLoadDetail(DR.report.id);
    } catch (e) { alert('Error: ' + e.message); }
  };
})();
