/* ============================================================================
 * Frontend Simulasi Paket (BOQ) -- Phase 1b.
 *
 * Modul terpisah dari Master Paket. Konsep: setiap paket bs punya beberapa BOQ
 * (Bill of Quantities) = breakdown biaya per line item + margin -> harga/pax.
 *
 * Bergantung ke fungsi global existing di index.html:
 *   - authFetch(path, opts)     -> fetch yang auto-attach Bearer + handle 401
 *   - currentUser               -> {id, role, name}
 *   - formatRp(n)               -> "Rp 1.234.567"
 *   - openModal(id) / closeModal(id) -> show/hide overlay modal
 *   - notyf / lucide.createIcons() -> toast + icon rerender
 * ==========================================================================*/
let __boqList = [];
let __boqFilterPackage = '';
let __boqFilterStatus = '';
let __boqCurrentDetail = null;   // {id, ...detail} yg lagi dibuka di modal
let __boqPackagesCache = null;   // list paket utk dropdown (lazy load)

const BOQ_CATEGORIES = [
    'hotel_mekkah', 'hotel_madinah', 'tiket', 'visa', 'muthawwif',
    'transport', 'konsumsi', 'ziyarah', 'handling', 'perlengkapan',
    'vaksin', 'asuransi', 'margin', 'lain',
];
const BOQ_CATEGORY_LABEL = {
    hotel_mekkah: 'Hotel Mekkah', hotel_madinah: 'Hotel Madinah',
    tiket: 'Tiket Pesawat', visa: 'Visa', muthawwif: 'Muthawwif/TL',
    transport: 'Transport', konsumsi: 'Konsumsi', ziyarah: 'Ziyarah',
    handling: 'Handling', perlengkapan: 'Perlengkapan',
    vaksin: 'Vaksin', asuransi: 'Asuransi', margin: 'Margin/Overhead',
    lain: 'Lain-lain',
};
const BOQ_UNITS = [
    { value: 'per_pax', label: 'per Pax' },
    { value: 'per_pax_per_day', label: 'per Pax/Hari' },
    { value: 'per_group', label: 'per Group (flat)' },
    { value: 'per_room_per_night', label: 'per Room/Malam' },
];
const BOQ_STATUS_BADGE = {
    'Draft':            { cls: 'bg-gray-100 text-gray-700', ico: 'edit-3' },
    'Pending Approval': { cls: 'bg-amber-100 text-amber-800', ico: 'clock' },
    'Approved':         { cls: 'bg-emerald-100 text-emerald-800', ico: 'check-circle' },
    'Rejected':         { cls: 'bg-red-100 text-red-700', ico: 'x-circle' },
};

const _isMgmt = () => ['admin', 'management'].includes(currentUser?.role);
const _isAuthor = () => ['admin', 'management', 'sales', 'ops'].includes(currentUser?.role);


/* ============================================================================
 * Init + list rendering
 * ==========================================================================*/
async function initBoqPage() {
    if (!__boqPackagesCache) {
        try {
            const r = await authFetch('/packages');
            __boqPackagesCache = await r.json() || [];
        } catch (e) { __boqPackagesCache = []; }
    }
    _renderBoqPackageFilter();
    const addBtn = document.getElementById('boq-add-btn');
    if (addBtn) addBtn.classList.toggle('hidden', !_isAuthor());
    await fetchBoqList();
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function _renderBoqPackageFilter() {
    const filter = document.getElementById('boq-filter-package');
    const formSel = document.getElementById('boq-form-package');
    const opts = '<option value="">-- Semua Paket --</option>'
        + '<option value="null">Tanpa Paket (draft baru)</option>'
        + (__boqPackagesCache || []).map(p =>
            `<option value="${p.id}">${_esc(p.name)}</option>`).join('');
    if (filter) filter.innerHTML = opts;
    // Form select: pilihan "kosong" (tanpa paket) + list paket
    if (formSel) formSel.innerHTML = '<option value="">-- (BOQ paket baru) --</option>'
        + (__boqPackagesCache || []).map(p =>
            `<option value="${p.id}">${_esc(p.name)}</option>`).join('');
}

async function fetchBoqList() {
    const params = new URLSearchParams();
    if (__boqFilterPackage === 'null') {
        // client-side filter (endpoint tdk support IS NULL)
    } else if (__boqFilterPackage) {
        params.set('package_id', __boqFilterPackage);
    }
    if (__boqFilterStatus) params.set('status', __boqFilterStatus);
    try {
        const r = await authFetch('/boq' + (params.toString() ? '?' + params : ''));
        let rows = await r.json() || [];
        if (__boqFilterPackage === 'null') rows = rows.filter(x => !x.package_id);
        __boqList = rows;
        _renderBoqTable();
    } catch (e) {
        _toast('Gagal memuat BOQ: ' + (e.message || e), 'error');
    }
}

function _renderBoqTable() {
    const tbody = document.getElementById('table-boq');
    if (!tbody) return;
    if (!__boqList.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-8 text-center text-gray-400 text-sm">
            Belum ada BOQ. Klik <b>+ Tambah BOQ</b> untuk mulai bikin skenario harga.</td></tr>`;
        return;
    }
    tbody.innerHTML = __boqList.map(b => {
        const st = BOQ_STATUS_BADGE[b.status] || BOQ_STATUS_BADGE.Draft;
        return `<tr class="hover:bg-emerald-50/40">
            <td class="px-3 py-2">
                <div class="font-semibold text-gray-800">${_esc(b.name)}</div>
                <div class="text-[11px] text-gray-500">by ${_esc(b.created_by_name || '-')}</div>
            </td>
            <td class="px-3 py-2 text-sm">${b.package_name ? _esc(b.package_name) : '<i class="text-gray-400">-- (paket baru)</i>'}</td>
            <td class="px-3 py-2">
                <span class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full font-bold ${st.cls}">
                    <i data-lucide="${st.ico}" class="w-3 h-3"></i>${_esc(b.status)}
                </span>
            </td>
            <td class="px-3 py-2 text-center text-sm">${b.item_count || 0}</td>
            <td class="px-3 py-2 text-right text-xs text-gray-500">
                ${b.target_pax || 0} pax &middot; ${b.target_margin_pct || 0}%
            </td>
            <td class="px-3 py-2 text-[11px] text-gray-500">${_esc((b.updated_at || '').slice(0, 16))}</td>
            <td class="px-3 py-2 text-right space-x-1 whitespace-nowrap">
                <button onclick="openBoqDetail(${b.id})" class="text-xs px-2 py-1 rounded bg-blue-50 text-blue-700 hover:bg-blue-100" title="Detail"><i data-lucide="eye" class="w-3.5 h-3.5"></i></button>
                ${_canEdit(b) ? `<button onclick="openEditBoq(${b.id})" class="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700 hover:bg-gray-200" title="Edit header"><i data-lucide="edit-2" class="w-3.5 h-3.5"></i></button>` : ''}
                ${_isAuthor() ? `<button onclick="duplicateBoq(${b.id})" class="text-xs px-2 py-1 rounded bg-purple-50 text-purple-700 hover:bg-purple-100" title="Duplicate ke Draft baru"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>` : ''}
                ${_canDelete(b) ? `<button onclick="deleteBoq(${b.id})" class="text-xs px-2 py-1 rounded bg-red-50 text-red-700 hover:bg-red-100" title="Hapus"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>` : ''}
            </td>
        </tr>`;
    }).join('');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function _canEdit(b) {
    if (_isMgmt()) return true;
    return b.created_by === currentUser.id && b.status === 'Draft';
}
function _canDelete(b) {
    if (_isMgmt()) return true;
    return b.created_by === currentUser.id && b.status === 'Draft';
}


/* ============================================================================
 * Add / edit header modal
 * ==========================================================================*/
function openAddBoq() {
    document.getElementById('boq-modal-title').innerText = 'Tambah BOQ Baru';
    document.getElementById('boq-form-id').value = '';
    document.getElementById('boq-form-name').value = '';
    document.getElementById('boq-form-package').value = '';
    document.getElementById('boq-form-pax').value = 45;
    document.getElementById('boq-form-margin').value = 15;
    document.getElementById('boq-form-notes').value = '';
    document.getElementById('boq-form-items-wrapper').classList.remove('hidden');
    __boqFormItems = [_blankItem()];
    _renderBoqFormItems();
    openModal('modal-boq-form');
}

function openEditBoq(bid) {
    const b = __boqList.find(x => x.id === bid);
    if (!b) return;
    document.getElementById('boq-modal-title').innerText = 'Edit Header BOQ (items diedit di modal Detail)';
    document.getElementById('boq-form-id').value = bid;
    document.getElementById('boq-form-name').value = b.name || '';
    document.getElementById('boq-form-package').value = b.package_id || '';
    document.getElementById('boq-form-pax').value = b.target_pax || 45;
    document.getElementById('boq-form-margin').value = b.target_margin_pct || 15;
    document.getElementById('boq-form-notes').value = b.notes || '';
    document.getElementById('boq-form-items-wrapper').classList.add('hidden');
    openModal('modal-boq-form');
}

async function saveBoq() {
    const id = document.getElementById('boq-form-id').value;
    const body = {
        name: document.getElementById('boq-form-name').value.trim(),
        package_id: document.getElementById('boq-form-package').value ? parseInt(document.getElementById('boq-form-package').value) : null,
        target_pax: parseInt(document.getElementById('boq-form-pax').value) || 45,
        target_margin_pct: parseFloat(document.getElementById('boq-form-margin').value) || 0,
        notes: document.getElementById('boq-form-notes').value,
    };
    if (!body.name) return _toast('Nama BOQ wajib diisi.', 'error');
    if (!id) {
        const items = (__boqFormItems || []).filter(it => it.item_name && it.category);
        body.items = items;
    }
    try {
        const path = id ? `/boq/${id}` : '/boq';
        const method = id ? 'PUT' : 'POST';
        const r = await authFetch(path, { method, body: JSON.stringify(body) });
        const j = await r.json();
        _toast(j.message || 'Tersimpan.', 'success');
        closeModal('modal-boq-form');
        await fetchBoqList();
    } catch (e) {
        _toast('Simpan gagal: ' + (e.message || e), 'error');
    }
}

/* --- items repeater di modal Add --- */
let __boqFormItems = [];
function _blankItem() {
    return {
        category: 'hotel_mekkah', item_name: '', unit: 'per_pax',
        quantity: 1, unit_price: 0, vendor_name: '', note: '',
    };
}
function addBoqFormItem() {
    __boqFormItems.push(_blankItem());
    _renderBoqFormItems();
}
function removeBoqFormItem(idx) {
    __boqFormItems.splice(idx, 1);
    _renderBoqFormItems();
}
function _updateFormItemField(idx, field, value) {
    if (!__boqFormItems[idx]) return;
    if (['quantity', 'unit_price'].includes(field)) value = parseFloat(value) || 0;
    __boqFormItems[idx][field] = value;
    _renderBoqFormTotals();
}
function _renderBoqFormItems() {
    const tbody = document.getElementById('boq-form-items');
    if (!tbody) return;
    tbody.innerHTML = __boqFormItems.map((it, idx) => {
        const subtotal = (it.quantity || 0) * (it.unit_price || 0);
        return `<tr>
            <td class="px-1 py-1">
                <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateFormItemField(${idx},'category',this.value)">
                    ${BOQ_CATEGORIES.map(c => `<option value="${c}" ${it.category===c?'selected':''}>${BOQ_CATEGORY_LABEL[c]}</option>`).join('')}
                </select>
            </td>
            <td class="px-1 py-1"><input type="text" value="${_esc(it.item_name)}" class="w-full border rounded px-2 py-1 text-xs" placeholder="Nama item" oninput="_updateFormItemField(${idx},'item_name',this.value)"/></td>
            <td class="px-1 py-1">
                <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateFormItemField(${idx},'unit',this.value)">
                    ${BOQ_UNITS.map(u => `<option value="${u.value}" ${it.unit===u.value?'selected':''}>${u.label}</option>`).join('')}
                </select>
            </td>
            <td class="px-1 py-1"><input type="number" step="0.01" value="${it.quantity}" class="w-16 border rounded px-1 py-1 text-xs text-right" oninput="_updateFormItemField(${idx},'quantity',this.value)"/></td>
            <td class="px-1 py-1"><input type="number" value="${it.unit_price}" class="w-28 border rounded px-1 py-1 text-xs text-right" oninput="_updateFormItemField(${idx},'unit_price',this.value)"/></td>
            <td class="px-1 py-1 text-right text-xs text-gray-600 tabular-nums">${formatRp(subtotal)}</td>
            <td class="px-1 py-1 text-center"><button type="button" onclick="removeBoqFormItem(${idx})" class="text-red-600 hover:text-red-800 text-xs" title="Hapus"><i data-lucide="x" class="w-3.5 h-3.5"></i></button></td>
        </tr>`;
    }).join('') || `<tr><td colspan="7" class="text-center text-xs text-gray-400 py-3">Belum ada item. Klik + Tambah Item.</td></tr>`;
    _renderBoqFormTotals();
    if (typeof lucide !== 'undefined') lucide.createIcons();
}
function _renderBoqFormTotals() {
    const pax = Math.max(parseInt(document.getElementById('boq-form-pax').value) || 1, 1);
    const margin = parseFloat(document.getElementById('boq-form-margin').value) || 0;
    let totalGroup = 0;
    (__boqFormItems || []).forEach(it => {
        const sub = (it.quantity || 0) * (it.unit_price || 0);
        if (it.unit === 'per_pax' || it.unit === 'per_pax_per_day') totalGroup += sub * pax;
        else totalGroup += sub;
    });
    const costPerPax = Math.floor(totalGroup / pax);
    const marginAmt = Math.floor(costPerPax * (margin / 100));
    const pricePerPax = costPerPax + marginAmt;
    const el = document.getElementById('boq-form-totals');
    if (el) el.innerHTML = `
        <div><span class="text-gray-500">Total group:</span> <b class="tabular-nums">${formatRp(totalGroup)}</b></div>
        <div><span class="text-gray-500">Cost/pax:</span> <b class="tabular-nums">${formatRp(costPerPax)}</b></div>
        <div><span class="text-gray-500">Margin:</span> <b class="tabular-nums">${formatRp(marginAmt)}</b></div>
        <div class="text-emerald-700 border-l pl-3"><span class="text-gray-500">Harga/pax:</span> <b class="tabular-nums text-base">${formatRp(pricePerPax)}</b></div>`;
}


/* ============================================================================
 * Detail modal (items CRUD + workflow actions)
 * ==========================================================================*/
async function openBoqDetail(bid) {
    try {
        const r = await authFetch(`/boq/${bid}`);
        __boqCurrentDetail = await r.json();
        _renderBoqDetail();
        openModal('modal-boq-detail');
    } catch (e) {
        _toast('Gagal memuat detail: ' + (e.message || e), 'error');
    }
}

function _renderBoqDetail() {
    const b = __boqCurrentDetail;
    if (!b) return;
    const st = BOQ_STATUS_BADGE[b.status] || BOQ_STATUS_BADGE.Draft;
    const canEditItems = _canEdit(b);
    const isPending = b.status === 'Pending Approval';
    const isDraft = b.status === 'Draft';
    const isOwner = b.created_by === currentUser.id;

    document.getElementById('boq-detail-header').innerHTML = `
        <div class="flex flex-wrap items-start gap-3 justify-between">
            <div>
                <h3 class="text-lg font-bold text-gray-800">${_esc(b.name)}</h3>
                <div class="text-xs text-gray-500 mt-1">
                    ${b.package_name ? _esc(b.package_name) : '<i>Belum terikat ke paket (BOQ paket baru)</i>'}
                    &middot; Target: ${b.target_pax} pax &middot; Margin: ${b.target_margin_pct}%
                </div>
            </div>
            <span class="inline-flex items-center gap-1 px-3 py-1 text-xs rounded-full font-bold ${st.cls}">
                <i data-lucide="${st.ico}" class="w-3.5 h-3.5"></i>${_esc(b.status)}
            </span>
        </div>
        ${b.notes ? `<div class="mt-2 text-xs text-gray-600 bg-yellow-50 border-l-2 border-yellow-400 px-2 py-1">${_esc(b.notes)}</div>` : ''}
        ${b.review_note ? `<div class="mt-2 text-xs text-gray-700 bg-blue-50 border-l-2 border-blue-400 px-2 py-1"><b>Review:</b> ${_esc(b.review_note)}</div>` : ''}
        <div class="text-[11px] text-gray-400 mt-2">
            Dibuat oleh <b>${_esc(b.created_by_name || '-')}</b> ${_esc((b.created_at||'').slice(0,16))}
            ${b.submitted_at ? ` &middot; Submit ${_esc(b.submitted_at.slice(0,16))}` : ''}
            ${b.reviewed_by_name ? ` &middot; Review oleh <b>${_esc(b.reviewed_by_name)}</b> ${_esc((b.reviewed_at||'').slice(0,16))}` : ''}
        </div>`;

    const tbody = document.getElementById('boq-detail-items');
    tbody.innerHTML = (b.items || []).map(it => `
        <tr>
            <td class="px-2 py-1 text-xs">${_esc(BOQ_CATEGORY_LABEL[it.category] || it.category)}</td>
            <td class="px-2 py-1 text-xs">${_esc(it.item_name)}</td>
            <td class="px-2 py-1 text-xs text-gray-500">${_esc(it.unit)}</td>
            <td class="px-2 py-1 text-xs text-right tabular-nums">${it.quantity}</td>
            <td class="px-2 py-1 text-xs text-right tabular-nums">${formatRp(it.unit_price)}</td>
            <td class="px-2 py-1 text-xs text-right tabular-nums font-semibold">${formatRp(it.subtotal)}</td>
            <td class="px-2 py-1 text-xs text-gray-400">${_esc(it.vendor_name || '')}</td>
            <td class="px-2 py-1 text-right">
                ${canEditItems ? `
                    <button onclick="_editBoqItem(${it.id})" class="text-xs px-1.5 py-0.5 text-blue-600 hover:bg-blue-50 rounded" title="Edit"><i data-lucide="edit-2" class="w-3 h-3"></i></button>
                    <button onclick="_deleteBoqItem(${it.id})" class="text-xs px-1.5 py-0.5 text-red-600 hover:bg-red-50 rounded" title="Hapus"><i data-lucide="x" class="w-3 h-3"></i></button>
                ` : ''}
            </td>
        </tr>
    `).join('') || `<tr><td colspan="8" class="text-center py-4 text-xs text-gray-400">Belum ada item.</td></tr>`;

    const t = b.totals || {};
    document.getElementById('boq-detail-totals').innerHTML = `
        <div class="grid grid-cols-4 gap-2 text-xs">
            <div><span class="text-gray-500">Total group:</span> <b class="tabular-nums block">${formatRp(t.total_group_cost || 0)}</b></div>
            <div><span class="text-gray-500">Cost/pax:</span> <b class="tabular-nums block">${formatRp(t.cost_per_pax || 0)}</b></div>
            <div><span class="text-gray-500">Margin:</span> <b class="tabular-nums block">${formatRp(t.margin_amount || 0)}</b></div>
            <div class="text-emerald-700"><span class="text-gray-500">Harga/pax:</span> <b class="tabular-nums block text-base">${formatRp(t.price_per_pax || 0)}</b></div>
        </div>`;

    const addItemBtn = document.getElementById('boq-detail-add-item-btn');
    if (addItemBtn) addItemBtn.classList.toggle('hidden', !canEditItems);

    const actions = document.getElementById('boq-detail-actions');
    let btns = '';
    if (isDraft && (isOwner || _isMgmt())) {
        btns += `<button onclick="submitBoq(${b.id})" class="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold"><i data-lucide="send" class="w-3.5 h-3.5 inline"></i> Submit untuk Approval</button>`;
    }
    if (isPending && _isMgmt()) {
        btns += `<button onclick="_openReviewModal('approve')" class="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold"><i data-lucide="check" class="w-3.5 h-3.5 inline"></i> Approve</button>`;
        btns += `<button onclick="_openReviewModal('reject')" class="px-3 py-1.5 rounded bg-red-600 hover:bg-red-700 text-white text-xs font-bold"><i data-lucide="x" class="w-3.5 h-3.5 inline"></i> Reject</button>`;
    }
    if (isDraft && _isMgmt()) {
        btns += `<button onclick="_openReviewModal('approve')" class="px-3 py-1.5 rounded bg-emerald-500 hover:bg-emerald-600 text-white text-xs"><i data-lucide="fast-forward" class="w-3.5 h-3.5 inline"></i> Approve Langsung</button>`;
    }
    if (_isAuthor()) {
        btns += `<button onclick="duplicateBoq(${b.id})" class="px-3 py-1.5 rounded bg-purple-100 text-purple-700 hover:bg-purple-200 text-xs"><i data-lucide="copy" class="w-3.5 h-3.5 inline"></i> Duplicate</button>`;
    }
    actions.innerHTML = btns;

    if (typeof lucide !== 'undefined') lucide.createIcons();
}


/* ============================================================================
 * Item CRUD (dari modal Detail)
 * ==========================================================================*/
function addBoqDetailItem() { _openItemModal(null); }
function _editBoqItem(iid) {
    const it = (__boqCurrentDetail?.items || []).find(x => x.id === iid);
    if (it) _openItemModal(it);
}
async function _deleteBoqItem(iid) {
    if (!confirm('Hapus item ini?')) return;
    try {
        await authFetch(`/boq/${__boqCurrentDetail.id}/items/${iid}`, { method: 'DELETE' });
        _toast('Item dihapus.', 'success');
        await openBoqDetail(__boqCurrentDetail.id);
        await fetchBoqList();
    } catch (e) {
        _toast('Gagal hapus item: ' + (e.message || e), 'error');
    }
}
function _openItemModal(it) {
    document.getElementById('boq-item-modal-title').innerText = it ? 'Edit Item' : 'Tambah Item';
    document.getElementById('boq-item-id').value = it?.id || '';
    document.getElementById('boq-item-category').innerHTML = BOQ_CATEGORIES.map(c =>
        `<option value="${c}" ${it?.category===c?'selected':''}>${BOQ_CATEGORY_LABEL[c]}</option>`).join('');
    document.getElementById('boq-item-unit').innerHTML = BOQ_UNITS.map(u =>
        `<option value="${u.value}" ${it?.unit===u.value?'selected':''}>${u.label}</option>`).join('');
    document.getElementById('boq-item-name').value = it?.item_name || '';
    document.getElementById('boq-item-qty').value = it?.quantity ?? 1;
    document.getElementById('boq-item-price').value = it?.unit_price ?? 0;
    document.getElementById('boq-item-vendor').value = it?.vendor_name || '';
    document.getElementById('boq-item-note').value = it?.note || '';
    openModal('modal-boq-item');
}
async function saveBoqItem() {
    const iid = document.getElementById('boq-item-id').value;
    const body = {
        category: document.getElementById('boq-item-category').value,
        item_name: document.getElementById('boq-item-name').value.trim(),
        unit: document.getElementById('boq-item-unit').value,
        quantity: parseFloat(document.getElementById('boq-item-qty').value) || 0,
        unit_price: parseInt(document.getElementById('boq-item-price').value) || 0,
        vendor_name: document.getElementById('boq-item-vendor').value,
        note: document.getElementById('boq-item-note').value,
    };
    if (!body.item_name) return _toast('Nama item wajib.', 'error');
    try {
        const path = iid
            ? `/boq/${__boqCurrentDetail.id}/items/${iid}`
            : `/boq/${__boqCurrentDetail.id}/items`;
        await authFetch(path, { method: iid ? 'PUT' : 'POST', body: JSON.stringify(body) });
        _toast('Item tersimpan.', 'success');
        closeModal('modal-boq-item');
        await openBoqDetail(__boqCurrentDetail.id);
        await fetchBoqList();
    } catch (e) {
        _toast('Gagal simpan: ' + (e.message || e), 'error');
    }
}


/* ============================================================================
 * Workflow: submit / approve / reject / duplicate / delete
 * ==========================================================================*/
async function submitBoq(bid) {
    if (!confirm('Submit BOQ ini untuk review Management? Setelah submit tidak bisa edit lagi.')) return;
    try {
        await authFetch(`/boq/${bid}/submit`, { method: 'POST' });
        _toast('BOQ dikirim untuk approval.', 'success');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Submit gagal: ' + (e.message || e), 'error');
    }
}

let __boqReviewAction = null;
function _openReviewModal(action) {
    __boqReviewAction = action;
    document.getElementById('boq-review-title').innerText =
        action === 'approve' ? 'Approve BOQ' : 'Reject BOQ';
    document.getElementById('boq-review-note').value = '';
    document.getElementById('boq-review-note-label').innerText =
        action === 'reject' ? 'Alasan (wajib):' : 'Catatan (opsional):';
    document.getElementById('boq-review-submit-btn').className =
        'px-3 py-1.5 rounded text-white text-sm font-bold '
        + (action === 'approve' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700');
    document.getElementById('boq-review-submit-btn').innerText =
        action === 'approve' ? 'Approve' : 'Reject';
    openModal('modal-boq-review');
}
async function submitBoqReview() {
    const note = document.getElementById('boq-review-note').value.trim();
    if (__boqReviewAction === 'reject' && !note) {
        return _toast('Alasan reject wajib diisi.', 'error');
    }
    try {
        await authFetch(`/boq/${__boqCurrentDetail.id}/${__boqReviewAction}`,
            { method: 'POST', body: JSON.stringify({ review_note: note }) });
        _toast(__boqReviewAction === 'approve' ? 'BOQ disetujui.' : 'BOQ ditolak.', 'success');
        closeModal('modal-boq-review');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Gagal: ' + (e.message || e), 'error');
    }
}

async function duplicateBoq(bid) {
    const b = __boqList.find(x => x.id === bid) || __boqCurrentDetail;
    const name = prompt('Nama BOQ hasil duplikat:', (b?.name || 'BOQ') + ' (copy)');
    if (!name) return;
    try {
        const r = await authFetch(`/boq/${bid}/duplicate`,
            { method: 'POST', body: JSON.stringify({ name }) });
        const j = await r.json();
        _toast('Duplicated ke BOQ Draft baru #' + j.id, 'success');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Duplicate gagal: ' + (e.message || e), 'error');
    }
}

async function deleteBoq(bid) {
    if (!confirm('Hapus BOQ ini? Semua line items ikut ke-hapus.')) return;
    try {
        await authFetch(`/boq/${bid}`, { method: 'DELETE' });
        _toast('BOQ dihapus.', 'success');
        await fetchBoqList();
    } catch (e) {
        _toast('Hapus gagal: ' + (e.message || e), 'error');
    }
}


/* ============================================================================
 * util: escape + toast fallback
 * ==========================================================================*/
function _esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function _toast(msg, type) {
    if (typeof notyf !== 'undefined') {
        try { type === 'error' ? notyf.error(msg) : notyf.success(msg); return; } catch(e) {}
    }
    alert(msg);
}
