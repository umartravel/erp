/*
 * Phase SS-2.1 (2026-09-21): UI renderer untuk page Sync Supabase.
 *
 * Panggil renderSupabaseSyncPage() dari showPage('supabase-sync').
 * 3 section: Status card, Log table, Soft-deleted list.
 */

let _syncLastLoad = 0;

async function renderSupabaseSyncPage() {
    if (Date.now() - _syncLastLoad < 500) return;
    _syncLastLoad = Date.now();
    await Promise.all([
        _loadSyncStatus(),
        _loadSyncLog(),
        _loadSoftDeleted(),
    ]);
}

async function _loadSyncStatus() {
    try {
        const res = await authFetch('/supabase/sync/status');
        const data = await res.json();
        const state = data.state || {};
        const lastSync = state.last_sync_ts?.value || '(belum pernah)';
        const lastRunCount = {
            i: state.last_run_inserted?.value || '0',
            u: state.last_run_updated?.value || '0',
            sd: state.last_run_soft_deleted?.value || '0',
            e: state.last_run_errors?.value || '0',
        };
        const cfgBadge = data.config_available
            ? '<span class="text-xs font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded">CONFIG OK</span>'
            : '<span class="text-xs font-bold text-red-700 bg-red-100 px-2 py-0.5 rounded">CONFIG MISSING</span>';
        document.getElementById('ss-status-card').innerHTML = `
            <div class="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <div class="text-xs font-bold text-gray-500 uppercase mb-1">Last Sync</div>
                    <div class="text-lg font-semibold" style="color:var(--umar-charcoal);">${_esc(lastSync)}</div>
                    <div class="text-xs text-gray-500 mt-1">Config: ${cfgBadge}</div>
                </div>
                <div class="grid grid-cols-4 gap-3 text-center">
                    <div>
                        <div class="text-2xl font-bold text-emerald-700">${lastRunCount.i}</div>
                        <div class="text-[10px] uppercase tracking-wider text-emerald-600 font-bold">Inserted</div>
                    </div>
                    <div>
                        <div class="text-2xl font-bold text-blue-700">${lastRunCount.u}</div>
                        <div class="text-[10px] uppercase tracking-wider text-blue-600 font-bold">Updated</div>
                    </div>
                    <div>
                        <div class="text-2xl font-bold text-amber-700">${lastRunCount.sd}</div>
                        <div class="text-[10px] uppercase tracking-wider text-amber-600 font-bold">Soft-Del</div>
                    </div>
                    <div>
                        <div class="text-2xl font-bold text-red-700">${lastRunCount.e}</div>
                        <div class="text-[10px] uppercase tracking-wider text-red-600 font-bold">Errors</div>
                    </div>
                </div>
            </div>
            <div class="mt-4 pt-3 border-t text-xs text-gray-600">
                Soft-deleted rows saat ini: <b>${data.soft_deleted_current}</b>
                (tag jamaah yg tidak lagi di Supabase closings)
            </div>
        `;
    } catch (e) {
        document.getElementById('ss-status-card').innerHTML =
            `<div class="text-red-600 text-sm">Gagal load status: ${_esc(e.message)}</div>`;
    }
}

async function _loadSyncLog(actionFilter) {
    try {
        const q = actionFilter ? `?limit=50&action=${encodeURIComponent(actionFilter)}` : '?limit=50';
        const res = await authFetch('/supabase/sync/log' + q);
        const data = await res.json();
        const entries = data.entries || [];
        if (entries.length === 0) {
            document.getElementById('ss-log-body').innerHTML =
                '<tr><td colspan="4" class="text-center text-gray-400 py-6 text-sm">Belum ada sync log.</td></tr>';
            return;
        }
        const rows = entries.map(e => {
            const actionColor = {
                insert: 'text-emerald-700 bg-emerald-100',
                update: 'text-blue-700 bg-blue-100',
                soft_delete: 'text-amber-700 bg-amber-100',
                error: 'text-red-700 bg-red-100',
                restore: 'text-purple-700 bg-purple-100',
            }[e.action] || 'text-gray-700 bg-gray-100';
            return `<tr class="hover:bg-gray-50">
                <td class="px-3 py-2 text-xs text-gray-600 font-mono">${_esc(e.run_ts || '')}</td>
                <td class="px-3 py-2 text-xs"><span class="${actionColor} px-2 py-0.5 rounded font-bold uppercase">${_esc(e.action)}</span></td>
                <td class="px-3 py-2 text-xs font-mono">${_esc(e.external_id || '-')}</td>
                <td class="px-3 py-2 text-xs text-red-600">${_esc(e.error_msg || '')}</td>
            </tr>`;
        }).join('');
        document.getElementById('ss-log-body').innerHTML = rows;
    } catch (e) {
        document.getElementById('ss-log-body').innerHTML =
            `<tr><td colspan="4" class="text-red-600 text-center py-4">${_esc(e.message)}</td></tr>`;
    }
}

async function _loadSoftDeleted() {
    try {
        const res = await authFetch('/supabase/sync/soft-deleted');
        const data = await res.json();
        const rows = data.soft_deleted || [];
        if (rows.length === 0) {
            document.getElementById('ss-softdel-body').innerHTML =
                '<tr><td colspan="5" class="text-center text-gray-400 py-6 text-sm">Tidak ada row tag missing. Data UMAR align dengan Supabase.</td></tr>';
            return;
        }
        const html = rows.map(r => `<tr class="hover:bg-red-50">
            <td class="px-3 py-2 text-xs font-mono">${_esc(r.external_id)}</td>
            <td class="px-3 py-2 text-xs">${_esc(r.name)}</td>
            <td class="px-3 py-2 text-xs text-gray-600">${_esc(r.package_type || '-')}</td>
            <td class="px-3 py-2 text-xs font-mono text-gray-500">${_esc(r.supabase_missing_since)}</td>
            <td class="px-3 py-2 text-xs">
                <button onclick="ssRestore('${_esc(r.external_id).replace(/'/g, "\\'")}')"
                        class="px-3 py-1 bg-amber-600 text-white rounded text-xs font-bold hover:bg-amber-700">
                    Restore
                </button>
            </td>
        </tr>`).join('');
        document.getElementById('ss-softdel-body').innerHTML = html;
    } catch (e) {
        document.getElementById('ss-softdel-body').innerHTML =
            `<tr><td colspan="5" class="text-red-600 text-center py-4">${_esc(e.message)}</td></tr>`;
    }
}

async function ssRunSync() {
    if (!confirm('Trigger sync incremental Supabase closings -> UMAR jamaah?')) return;
    const btn = document.getElementById('ss-btn-run');
    btn.disabled = true; btn.textContent = 'Sync...';
    try {
        const res = await authFetch('/supabase/sync/run', { method: 'POST' });
        const data = await res.json();
        showToast(`Sync selesai: ${data.inserted}i / ${data.updated}u / ${data.errors}e dari ${data.rows_processed} rows`);
        await renderSupabaseSyncPage();
    } catch (e) {
        showToast(`Sync gagal: ${e.message}`, 'error');
    } finally {
        btn.disabled = false; btn.textContent = 'Run Sync Now';
    }
}

async function ssRunFullBackfill() {
    if (!confirm('FULL BACKFILL: query SEMUA Supabase closings + upsert ke UMAR. Bisa lambat (~1 menit untuk 400+ rows). Lanjut?')) return;
    const btn = document.getElementById('ss-btn-backfill');
    btn.disabled = true; btn.textContent = 'Backfill...';
    try {
        const res = await authFetch('/supabase/sync/full-backfill', { method: 'POST' });
        const data = await res.json();
        showToast(`Full backfill: ${data.inserted}i / ${data.updated}u / ${data.soft_deleted}sd / ${data.errors}e dari ${data.rows_processed} rows`);
        await renderSupabaseSyncPage();
    } catch (e) {
        showToast(`Backfill gagal: ${e.message}`, 'error');
    } finally {
        btn.disabled = false; btn.textContent = 'Full Backfill';
    }
}

async function ssRestore(extId) {
    if (!confirm(`Restore ext_id=${extId} balik ke Supabase closings?`)) return;
    try {
        const res = await authFetch('/supabase/restore/' + encodeURIComponent(extId), { method: 'POST' });
        const data = await res.json();
        showToast(data.message || 'Restore OK');
        await renderSupabaseSyncPage();
    } catch (e) {
        showToast(`Restore gagal: ${e.message}`, 'error');
    }
}

function _esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
}
