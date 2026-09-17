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

    // Tombol "Buka Kembali" saat Submitted (owner atau admin/mgmt).
    const slot = document.getElementById('dr-btn-reopen-slot');
    if (slot) {
      if (locked) {
        const role = (window.currentUser && window.currentUser.role) || '';
        const isPriv = role === 'admin' || role === 'management';
        slot.innerHTML = `<button type="button" onclick="drReopen(${DR.report.id})"
          class="text-xs font-bold px-3 py-1.5 rounded"
          style="background:#FDE68A;color:#92400E;border:1px solid #D97706;">
          <span style="margin-right:4px;">&#8635;</span>Buka Kembali${isPriv ? ' (Admin)' : ''}
        </button>`;
      } else {
        slot.innerHTML = '';
      }
    }

    drRenderTasks();
    drRenderFeedback();
  }

  window.drReopen = async function(rid) {
    const reason = prompt(
      'Alasan buka kembali (min 5 karakter):\n\n'
      + 'Owner boleh reopen sendiri dalam 2 jam setelah submit.\n'
      + 'Setelah 2 jam, hanya admin/management yg boleh.');
    if (reason === null) return;
    const clean = (reason || '').trim();
    if (clean.length < 5) {
      alert('Alasan minimal 5 karakter.');
      return;
    }
    try {
      const res = await (window.authFetch || fetch)(
        `/api/daily-reports/${rid}/reopen`,
        {method: 'POST', headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({reason: clean})});
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || 'Gagal reopen');
      alert(d.message || 'Laporan berhasil dibuka kembali.');
      if (typeof initDailyMine === 'function') initDailyMine();
      if (typeof initDailyTeam === 'function') initDailyTeam();
    } catch (err) {
      alert('Gagal: ' + err.message);
    }
  };

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

  // ==========================================================================
  // Phase DT-3b: Laporan Tim (mgmt bird's-eye view)
  // ==========================================================================
  const DT = {
    date: null,       // yyyy-mm-dd currently loaded
    role: '',         // '' | 'sales' | 'ops' | 'finance' | 'management'
    users: [],        // rows dari /team endpoint
    detailRid: null,  // rid yg sedang dibuka di modal detail
  };

  const ROLE_LABEL = {
    sales: 'Sales', ops: 'Operasional', finance: 'Finance',
    management: 'Management', admin: 'Admin',
  };

  const STATUS_STYLE = {
    Submitted: { bg: '#DCFCE7', color: '#166534', border: '#16A34A', label: 'Submitted' },
    Draft: { bg: '#FEF3C7', color: '#92400E', border: '#D97706', label: 'Draft' },
    NotSubmitted: { bg: '#FEE2E2', color: '#991B1B', border: '#DC2626', label: 'Belum Lapor' },
  };

  const MOOD_LABEL = {
    productive: '🚀 Produktif', neutral: '😐 Netral',
    blocked: '⛔ Terhambat', off: '🌙 Off',
  };

  window.initDailyTeam = async function() {
    const dateEl = document.getElementById('dt-filter-date');
    const roleEl = document.getElementById('dt-filter-role');
    if (dateEl && !dateEl.value) {
      dateEl.value = new Date().toISOString().slice(0, 10);
    }
    DT.date = dateEl ? dateEl.value : new Date().toISOString().slice(0, 10);
    DT.role = roleEl ? roleEl.value : '';
    await drTeamLoad();
    if (typeof lucide !== 'undefined') lucide.createIcons();
  };

  window.drTeamRefresh = async function() {
    const dateEl = document.getElementById('dt-filter-date');
    const roleEl = document.getElementById('dt-filter-role');
    DT.date = (dateEl && dateEl.value) || new Date().toISOString().slice(0, 10);
    DT.role = roleEl ? roleEl.value : '';
    await drTeamLoad();
  };

  async function drTeamLoad() {
    try {
      const params = new URLSearchParams({ date: DT.date });
      if (DT.role) params.set('role', DT.role);
      const r = await drFetch(`/api/daily-reports/team?${params.toString()}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || err.error || 'Gagal load laporan tim.');
      }
      const data = await r.json();
      DT.users = data.users || [];
      drTeamRenderKpis(data.totals || {});
      drTeamRenderGrid(DT.users);
    } catch (e) {
      console.error('[dr-team] load error', e);
      const body = document.getElementById('dt-grid-body');
      if (body) {
        body.innerHTML = `<tr><td colspan="7" class="p-4 text-center text-xs" style="color:${DR_COLORS.red};">${drEsc(e.message)}</td></tr>`;
      }
    }
  }

  function drTeamRenderKpis(totals) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('dt-kpi-submitted', totals.submitted || 0);
    set('dt-kpi-total', totals.users || 0);
    set('dt-kpi-drafts', totals.drafts || 0);
    set('dt-kpi-not-submitted', totals.not_submitted || 0);
    set('dt-kpi-rate', `${totals.submit_rate_pct != null ? totals.submit_rate_pct : 0}%`);
  }

  function drTeamRenderGrid(users) {
    const body = document.getElementById('dt-grid-body');
    const countEl = document.getElementById('dt-grid-count');
    if (!body) return;
    if (countEl) countEl.textContent = `${users.length} baris`;

    if (!users.length) {
      body.innerHTML = '<tr><td colspan="7" class="p-4 text-center text-xs italic" style="color:#9CA3AF;">Tidak ada data untuk filter ini.</td></tr>';
      return;
    }

    body.innerHTML = users.map(u => {
      const style = STATUS_STYLE[u.status] || STATUS_STYLE.NotSubmitted;
      const statusBadge = `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full border" style="background:${style.bg};color:${style.color};border-color:${style.border};">${drEsc(style.label)}</span>`;
      const roleLabel = ROLE_LABEL[u.role] || u.role || '-';
      const mood = u.mood ? drEsc(MOOD_LABEL[u.mood] || u.mood) : '<span style="color:#D1D5DB;">-</span>';
      const summary = u.summary_text
        ? drEsc(u.summary_text.length > 60 ? u.summary_text.slice(0, 60) + '…' : u.summary_text)
        : '<span style="color:#D1D5DB;">-</span>';
      const total = Number(u.item_count) || 0;
      const done = Number(u.done_count) || 0;
      const taskCell = total > 0
        ? `<span class="text-xs font-bold" style="color:${done === total ? DR_COLORS.green : DR_COLORS.charcoal};">${done}/${total}</span>`
        : '<span class="text-xs" style="color:#D1D5DB;">-</span>';

      const nameSafe = drEsc(u.user_name || u.username || '-');
      const roleSafe = drEsc(roleLabel);
      // Pass rid saja -- name/role di-lookup dari DT.users di handler,
      // menghindari HTML entity decoding attack pada inline onclick string.
      const actionBtn = u.report_id
        ? `<button onclick="drTeamOpenDetail(${u.report_id})" class="text-[10px] font-bold px-2 py-1 rounded" style="background:${DR_COLORS.gold};color:white;">Detail</button>`
        : `<span class="text-[10px]" style="color:#9CA3AF;">tidak ada</span>`;

      return `<tr class="border-t hover:bg-yellow-50 transition-colors" style="border-color:#F3F4F6;">
        <td class="px-3 py-2"><b class="text-xs" style="color:${DR_COLORS.charcoal};">${nameSafe}</b></td>
        <td class="px-3 py-2"><span class="text-[10px] font-semibold px-2 py-0.5 rounded" style="background:#F3F4F6;color:#374151;">${roleSafe}</span></td>
        <td class="px-3 py-2">${statusBadge}</td>
        <td class="px-3 py-2 text-xs">${mood}</td>
        <td class="px-3 py-2 text-xs" style="color:#374151;">${summary}</td>
        <td class="px-3 py-2 text-center">${taskCell}</td>
        <td class="px-3 py-2 text-center">${actionBtn}</td>
      </tr>`;
    }).join('');
  }

  window.drTeamOpenDetail = async function(rid) {
    if (!rid) {
      alert('Karyawan ini belum lapor hari ini.');
      return;
    }
    // Lookup name/role dari state (avoid embed string di inline onclick)
    const row = DT.users.find(u => u.report_id === rid);
    const name = row ? (row.user_name || row.username || 'Detail Laporan') : 'Detail Laporan';
    const roleLabel = row ? (ROLE_LABEL[row.role] || row.role || '-') : '-';

    DT.detailRid = rid;
    document.getElementById('dt-detail-title').textContent = name;
    document.getElementById('dt-detail-subtitle').textContent =
      `${roleLabel} · ${drFormatDate(DT.date)}`;
    document.getElementById('dt-detail-summary').textContent = 'Memuat…';
    document.getElementById('dt-detail-tasks').innerHTML = '';
    document.getElementById('dt-detail-feedback').innerHTML = '';
    document.getElementById('dt-detail-comment').value = '';
    document.getElementById('dr-team-detail-modal').classList.remove('hidden');
    await drTeamLoadDetail(rid);
  };

  window.drTeamCloseDetail = function() {
    DT.detailRid = null;
    document.getElementById('dr-team-detail-modal').classList.add('hidden');
  };

  async function drTeamLoadDetail(rid) {
    try {
      const r = await drFetch(`/api/daily-reports/${rid}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || err.error || 'Gagal load detail.');
      }
      const detail = await r.json();
      document.getElementById('dt-detail-summary').textContent =
        detail.summary_text || '(Tidak ada ringkasan)';
      const tasksEl = document.getElementById('dt-detail-tasks');
      const items = detail.items || [];
      if (!items.length) {
        tasksEl.innerHTML = '<p class="text-xs italic" style="color:#9CA3AF;">Tidak ada task.</p>';
      } else {
        tasksEl.innerHTML = items.map(t => {
          const isDone = t.status === 'Done';
          const icon = isDone
            ? `<i data-lucide="check-circle-2" class="w-4 h-4 shrink-0" style="color:${DR_COLORS.green};"></i>`
            : `<i data-lucide="circle" class="w-4 h-4 shrink-0" style="color:${DR_COLORS.gray500};"></i>`;
          const priorityBadge = t.priority === 'high'
            ? `<span class="text-[9px] font-bold px-1.5 py-0.5 rounded ml-1" style="background:#FEE2E2;color:${DR_COLORS.red};">HIGH</span>`
            : '';
          const linkLabel = t.linked_entity_type
            ? `<span class="text-[10px] px-1.5 py-0.5 rounded ml-1" style="background:#EEF2FF;color:#3730A3;">${drEsc(t.linked_entity_type)}#${Number(t.linked_entity_id) || 0}</span>`
            : '';
          return `<div class="flex items-start gap-2 p-2 border rounded" style="background:#FAFAF9;border-color:#E5E7EB;">
            ${icon}
            <div class="flex-1 min-w-0">
              <div class="flex items-center flex-wrap gap-1">
                <b class="text-xs ${isDone ? 'line-through' : ''}" style="color:${DR_COLORS.charcoal};">${drEsc(t.title)}</b>
                ${priorityBadge}
                ${linkLabel}
                <span class="text-[10px] ml-auto" style="color:${DR_COLORS.gray500};">${drEsc(t.status)}</span>
              </div>
              ${t.description ? `<p class="text-[10px] mt-0.5" style="color:${DR_COLORS.gray500};">${drEsc(t.description)}</p>` : ''}
            </div>
          </div>`;
        }).join('');
      }
      const fbEl = document.getElementById('dt-detail-feedback');
      const fb = detail.feedback || [];
      if (!fb.length) {
        fbEl.innerHTML = '<p class="text-xs italic text-center" style="color:#9CA3AF;">Belum ada komentar.</p>';
      } else {
        fbEl.innerHTML = fb.map(c => {
          const isMgmt = c.is_from_management === 1 || c.is_from_management === true;
          const bg = isMgmt ? '#FEF3C7' : '#EEF2FF';
          const border = isMgmt ? '#D97706' : '#3730A3';
          const label = isMgmt ? 'Manajemen' : (c.user_name || 'Karyawan');
          return `<div class="rounded-lg p-2 border-l-2" style="background:${bg};border-color:${border};">
            <p class="text-[10px] font-bold" style="color:${border};">${drEsc(label)}</p>
            <p class="text-xs mt-0.5" style="color:${DR_COLORS.charcoal};">${drEsc(c.comment_text)}</p>
            <p class="text-[9px] mt-1" style="color:${DR_COLORS.gray500};">${drFormatDateTime(c.created_at)}</p>
          </div>`;
        }).join('');
      }
      if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
      document.getElementById('dt-detail-summary').textContent = 'Error: ' + e.message;
    }
  }

  // Phase DT-5: Digest PDF/XLSX download. Endpoint pakai authenticate_file_token
  // -- token dilewatkan via ?token= query string supaya <a target=_blank> jalan
  // tanpa header Authorization. Rentang default: 30 hari terakhir ending di
  // filter date. Filter role dibawa juga kalau di-set.
  function drTeamBuildExportUrl(fmt) {
    const dateEl = document.getElementById('dt-filter-date');
    const roleEl = document.getElementById('dt-filter-role');
    const dateTo = (dateEl && dateEl.value) || new Date().toISOString().slice(0, 10);
    const dTo = new Date(dateTo + 'T00:00:00');
    dTo.setDate(dTo.getDate() - 29);
    const dateFrom = dTo.toISOString().slice(0, 10);
    const role = roleEl ? roleEl.value : '';
    const token = encodeURIComponent(sessionStorage.getItem('token') || '');
    const params = [
      `date_from=${dateFrom}`, `date_to=${dateTo}`,
      `token=${token}`,
    ];
    if (role) params.push(`role=${encodeURIComponent(role)}`);
    return `/api/daily-reports/export.${fmt}?${params.join('&')}`;
  }

  window.drTeamDownloadPdf = function() {
    // inline disposition -> open tab baru
    window.open(drTeamBuildExportUrl('pdf'), '_blank');
  };

  window.drTeamDownloadXlsx = function() {
    // attachment disposition -> trigger anchor click download
    const a = document.createElement('a');
    a.href = drTeamBuildExportUrl('xlsx');
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  window.drTeamSendFeedback = async function() {
    if (!DT.detailRid) return;
    const inputEl = document.getElementById('dt-detail-comment');
    const text = (inputEl.value || '').trim();
    if (!text) {
      alert('Isi komentar dulu.');
      return;
    }
    try {
      const r = await drFetch(`/api/daily-reports/${DT.detailRid}/feedback`, {
        method: 'POST',
        body: JSON.stringify({ comment_text: text }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(data.detail || data.error || 'Gagal kirim.');
      }
      inputEl.value = '';
      await drTeamLoadDetail(DT.detailRid);
    } catch (e) {
      alert('Error: ' + e.message);
    }
  };
})();
