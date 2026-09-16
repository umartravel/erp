/**
 * Phase EX-4: UI Kelola Kategori (admin CRUD Chart of Accounts subkategori).
 *
 * Halaman `page-finance-categories` (nav "Kelola Kategori", admin only).
 * Baca kategori dari `GET /api/finance/categories` (tree parent + subcategories,
 * unblocked di Phase EX-1). Mutasi lewat POST/PUT/DELETE yang admin-only.
 */

const FC = {
  activeGroup: 'expense',
  tree: { income: [], expense: [] },
};

async function fcLoad() {
  try {
    const res = await authFetch('/finance/categories');
    const data = await res.json();
    FC.tree = { income: data.income || [], expense: data.expense || [] };
    fcRender();
  } catch (e) {
    showToast('Gagal load kategori', 'error');
  }
}

function fcRender() {
  const list = FC.tree[FC.activeGroup] || [];
  const container = document.getElementById('fc-tree');
  const empty = document.getElementById('fc-empty');
  if (!container) return;

  if (list.length === 0) {
    container.innerHTML = '';
    empty?.classList.remove('hidden');
    return;
  }
  empty?.classList.add('hidden');

  const html = list.map(parent => {
    const subs = parent.subcategories || [];
    const subsHtml = subs.map(s => `
      <div class="flex items-center justify-between pl-10 pr-3 py-2 hover:bg-gray-50 border-l-2 border-gray-200 ml-4">
        <div class="flex items-center gap-2 min-w-0">
          <i data-lucide="corner-down-right" class="w-3.5 h-3.5 text-gray-400 shrink-0"></i>
          <span class="text-sm text-gray-800 truncate">${_fcEscape(s.name)}</span>
          <span class="text-[10px] text-gray-400 font-mono">#${s.id}</span>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button type="button" onclick="fcEditOpen(${s.id})" class="text-xs px-2 py-1 text-blue-600 hover:bg-blue-50 rounded">Edit</button>
          <button type="button" onclick="fcDelete(${s.id}, '${_fcEscapeJs(s.name)}')" class="text-xs px-2 py-1 text-red-600 hover:bg-red-50 rounded">Nonaktifkan</button>
        </div>
      </div>
    `).join('');
    return `
      <div class="border rounded-lg mb-2 overflow-hidden">
        <div class="flex items-center justify-between px-3 py-2.5 bg-gray-50">
          <div class="flex items-center gap-2 min-w-0">
            <i data-lucide="folder" class="w-4 h-4 text-amber-600 shrink-0"></i>
            <span class="font-bold text-sm text-gray-800 truncate">${_fcEscape(parent.name)}</span>
            <span class="text-[10px] text-gray-400 font-mono">#${parent.id}</span>
            <span class="text-[10px] text-gray-500">(${subs.length} sub)</span>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button type="button" onclick="fcEditOpen(${parent.id})" class="text-xs px-2 py-1 text-blue-600 hover:bg-blue-50 rounded">Edit</button>
            <button type="button" onclick="fcDelete(${parent.id}, '${_fcEscapeJs(parent.name)}')" class="text-xs px-2 py-1 text-red-600 hover:bg-red-50 rounded">Nonaktifkan</button>
          </div>
        </div>
        ${subsHtml}
      </div>
    `;
  }).join('');
  container.innerHTML = html;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function fcSwitchTab(group) {
  FC.activeGroup = group;
  document.querySelectorAll('.fc-tab').forEach(btn => {
    if (btn.dataset.group === group) {
      btn.style.background = 'var(--umar-gold)';
      btn.style.color = 'white';
      btn.classList.remove('bg-gray-100', 'text-gray-600');
    } else {
      btn.style.background = '';
      btn.style.color = '';
      btn.classList.add('bg-gray-100', 'text-gray-600');
    }
  });
  fcRender();
  fcPopulateParent();
}

function fcPopulateParent() {
  const sel = document.getElementById('fc-edit-parent');
  if (!sel) return;
  const group = document.getElementById('fc-edit-group')?.value || FC.activeGroup;
  const list = FC.tree[group] || [];
  const opts = ['<option value="">-- Tidak ada (parent baru) --</option>'];
  list.forEach(p => {
    opts.push(`<option value="${p.id}">${_fcEscape(p.name)}</option>`);
  });
  sel.innerHTML = opts.join('');
}

function fcAddOpen() {
  document.getElementById('fc-modal-title').textContent = 'Tambah Kategori';
  document.getElementById('fc-edit-id').value = '';
  document.getElementById('fc-edit-group').value = FC.activeGroup;
  document.getElementById('fc-edit-group').disabled = false;
  document.getElementById('fc-edit-parent').disabled = false;
  document.getElementById('fc-edit-name').value = '';
  document.getElementById('fc-edit-sort').value = '0';
  fcPopulateParent();
  document.getElementById('fc-edit-parent').value = '';
  openModal('modal-fc-edit');
}

function fcEditOpen(id) {
  let found = null, foundGroup = null, foundParentId = null;
  for (const grp of ['expense', 'income']) {
    for (const p of FC.tree[grp] || []) {
      if (p.id === id) { found = p; foundGroup = grp; foundParentId = null; break; }
      for (const s of p.subcategories || []) {
        if (s.id === id) { found = s; foundGroup = grp; foundParentId = p.id; break; }
      }
      if (found) break;
    }
    if (found) break;
  }
  if (!found) { showToast('Kategori tidak ditemukan', 'error'); return; }

  document.getElementById('fc-modal-title').textContent = `Edit: ${found.name}`;
  document.getElementById('fc-edit-id').value = id;
  document.getElementById('fc-edit-group').value = foundGroup;
  document.getElementById('fc-edit-group').disabled = true;
  document.getElementById('fc-edit-name').value = found.name;
  document.getElementById('fc-edit-sort').value = found.sort_order || 0;
  fcPopulateParent();
  document.getElementById('fc-edit-parent').value = foundParentId || '';
  document.getElementById('fc-edit-parent').disabled = true;
  openModal('modal-fc-edit');
}

async function fcSave(e) {
  e.preventDefault();
  const id = document.getElementById('fc-edit-id').value;
  const name = document.getElementById('fc-edit-name').value.trim();
  const group = document.getElementById('fc-edit-group').value;
  const parentRaw = document.getElementById('fc-edit-parent').value;
  const sort = parseInt(document.getElementById('fc-edit-sort').value, 10) || 0;
  if (!name) { showToast('Nama wajib diisi', 'error'); return; }
  try {
    if (id) {
      await authFetch(`/finance/categories/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ name, sort_order: sort }),
      });
      showToast('Kategori diperbarui');
    } else {
      const payload = { name, group_type: group, sort_order: sort };
      if (parentRaw) payload.parent_id = parseInt(parentRaw, 10);
      await authFetch('/finance/categories', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      showToast('Kategori dibuat');
    }
    closeModal('modal-fc-edit');
    document.getElementById('fc-edit-group').disabled = false;
    document.getElementById('fc-edit-parent').disabled = false;
    await fcLoad();
  } catch (err) {
    showToast(err.message || 'Gagal simpan kategori', 'error');
  }
}

async function fcDelete(id, name) {
  const ok = await showDialog({
    type: 'confirm',
    title: 'Nonaktifkan Kategori',
    message: `Kategori "${name}" akan di-nonaktifkan (soft-delete). Transaksi historis dgn kategori ini tetap tercatat, tapi kategori tidak muncul lagi di form baru. Lanjutkan?`,
    okText: 'Nonaktifkan',
    okClass: 'bg-red-600 hover:bg-red-700',
    icon: '<i data-lucide="trash-2" class="h-6 w-6 text-red-600"></i>',
    iconBg: 'bg-red-100',
  });
  if (!ok) return;
  try {
    await authFetch(`/finance/categories/${id}`, { method: 'DELETE' });
    showToast('Kategori di-nonaktifkan');
    await fcLoad();
  } catch (err) {
    showToast(err.message || 'Gagal hapus', 'error');
  }
}

function fcInit() {
  document.querySelectorAll('.fc-tab').forEach(btn => {
    btn.addEventListener('click', () => fcSwitchTab(btn.dataset.group));
  });
  document.getElementById('btn-fc-add')?.addEventListener('click', fcAddOpen);
  document.getElementById('form-fc-edit')?.addEventListener('submit', fcSave);
  fcLoad();
}

function _fcEscape(s) {
  return String(s || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);
}
function _fcEscapeJs(s) {
  return String(s || '').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

window.fcInit = fcInit;
window.fcLoad = fcLoad;
window.fcEditOpen = fcEditOpen;
window.fcDelete = fcDelete;
