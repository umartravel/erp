/* ============================================================================
 * UMAR Session Monitor -- Phase 6g
 *
 * Idle timeout + auto-logout untuk melindungi data jamaah (PII).
 * Dipasangkan dengan sessionStorage (browser close = logout otomatis).
 *
 * Aturan:
 *   - Idle 29 menit -> tampil modal warning dengan countdown 60 detik
 *   - Idle 30 menit total -> auto logout + reload ke login screen
 *   - Aktivitas apapun (mouse/keyboard/scroll/touch) reset timer
 *   - Klik tombol "Tetap Login" di modal juga reset timer
 *
 * Dependency: butuh function global logout() di index.html yang meng-clear
 * sessionStorage + reload.
 *
 * API:
 *   umarSessionStart()  - mulai tracking (dipanggil sekali setelah login OK)
 *   umarSessionStop()   - stop tracking (dipanggil di logout)
 *   umarSessionReset()  - manual reset timer (jarang dipakai)
 * ==========================================================================*/

const UMAR_SESSION_IDLE_MINUTES = 30;
const UMAR_SESSION_WARN_SECONDS = 60;

let __umarSessionIdleTimer = null;
let __umarSessionWarnTimer = null;
let __umarSessionCountdownInterval = null;
let __umarSessionRunning = false;

const _UMAR_ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'click'];

function _umarClearAllSessionTimers() {
    if (__umarSessionIdleTimer) { clearTimeout(__umarSessionIdleTimer); __umarSessionIdleTimer = null; }
    if (__umarSessionWarnTimer) { clearTimeout(__umarSessionWarnTimer); __umarSessionWarnTimer = null; }
    if (__umarSessionCountdownInterval) { clearInterval(__umarSessionCountdownInterval); __umarSessionCountdownInterval = null; }
}

function _umarHideSessionWarning() {
    const modal = document.getElementById('umar-session-warning');
    if (modal) modal.remove();
}

function _umarBuildSessionWarningModal() {
    _umarHideSessionWarning();
    const overlay = document.createElement('div');
    overlay.id = 'umar-session-warning';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;padding:1rem;';
    overlay.innerHTML = `
        <div style="background:#fff;border-radius:1rem;padding:1.5rem 1.75rem;max-width:420px;width:100%;box-shadow:0 20px 50px rgba(0,0,0,0.25);">
            <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1rem;">
                <div style="width:44px;height:44px;border-radius:999px;background:#FEF3C7;display:flex;align-items:center;justify-content:center;">
                    <span style="font-size:24px;">⏰</span>
                </div>
                <div>
                    <h3 style="font-size:1rem;font-weight:700;color:#1D1D1B;margin:0;">Sesi akan berakhir</h3>
                    <p style="font-size:0.75rem;color:#6B7280;margin:2px 0 0;">Tidak ada aktivitas selama ${UMAR_SESSION_IDLE_MINUTES - 1} menit.</p>
                </div>
            </div>
            <p style="font-size:0.875rem;color:#374151;margin-bottom:1rem;line-height:1.5;">
                Sesi Anda akan otomatis berakhir dalam
                <span id="umar-session-countdown" style="font-weight:700;color:#B91C1C;">${UMAR_SESSION_WARN_SECONDS}</span>
                detik. Klik tombol di bawah untuk melanjutkan.
            </p>
            <div style="display:flex;gap:0.5rem;justify-content:flex-end;">
                <button type="button" id="umar-session-logout-now" style="padding:0.5rem 1rem;border-radius:0.5rem;font-size:0.8125rem;font-weight:600;color:#6B7280;background:#F3F4F6;border:none;cursor:pointer;">Logout Sekarang</button>
                <button type="button" id="umar-session-continue" style="padding:0.5rem 1.25rem;border-radius:0.5rem;font-size:0.8125rem;font-weight:700;color:#78350F;background:#F8BE20;border:none;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,0.1);">Tetap Login</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    document.getElementById('umar-session-continue').addEventListener('click', () => {
        _umarHideSessionWarning();
        umarSessionReset();
    });
    document.getElementById('umar-session-logout-now').addEventListener('click', () => {
        _umarHideSessionWarning();
        _umarPerformAutoLogout('Anda memilih logout.');
    });
}

function _umarShowSessionWarning() {
    _umarBuildSessionWarningModal();
    let remaining = UMAR_SESSION_WARN_SECONDS;
    const countdownEl = document.getElementById('umar-session-countdown');
    if (__umarSessionCountdownInterval) clearInterval(__umarSessionCountdownInterval);
    __umarSessionCountdownInterval = setInterval(() => {
        remaining -= 1;
        if (countdownEl) countdownEl.textContent = remaining;
        if (remaining <= 0) {
            clearInterval(__umarSessionCountdownInterval);
            __umarSessionCountdownInterval = null;
            _umarPerformAutoLogout('Sesi berakhir karena tidak ada aktivitas.');
        }
    }, 1000);
}

function _umarPerformAutoLogout(reason) {
    _umarHideSessionWarning();
    _umarClearAllSessionTimers();
    __umarSessionRunning = false;
    _UMAR_ACTIVITY_EVENTS.forEach(evt => document.removeEventListener(evt, _umarOnActivity, true));
    try {
        sessionStorage.removeItem('token');
        sessionStorage.removeItem('user');
        sessionStorage.setItem('_umar_logout_reason', reason || 'auto-logout');
    } catch (e) {}
    // Reload ke login screen. Reason ditampilkan di banner login (index.html cek key ini).
    location.reload();
}

function _umarOnActivity() {
    // Kalau modal warning aktif, aktivitas tidak reset timer -- user harus klik tombol.
    if (document.getElementById('umar-session-warning')) return;
    umarSessionReset();
}

function umarSessionReset() {
    if (!__umarSessionRunning) return;
    _umarClearAllSessionTimers();
    _umarHideSessionWarning();
    const idleMs = UMAR_SESSION_IDLE_MINUTES * 60 * 1000;
    const warnMs = idleMs - (UMAR_SESSION_WARN_SECONDS * 1000);
    __umarSessionWarnTimer = setTimeout(_umarShowSessionWarning, warnMs);
    __umarSessionIdleTimer = setTimeout(() => {
        _umarPerformAutoLogout('Sesi berakhir karena tidak ada aktivitas.');
    }, idleMs);
}

function umarSessionStart() {
    if (__umarSessionRunning) return;
    __umarSessionRunning = true;
    _UMAR_ACTIVITY_EVENTS.forEach(evt =>
        document.addEventListener(evt, _umarOnActivity, {passive: true, capture: true}));
    umarSessionReset();
}

function umarSessionStop() {
    __umarSessionRunning = false;
    _umarClearAllSessionTimers();
    _umarHideSessionWarning();
    _UMAR_ACTIVITY_EVENTS.forEach(evt => document.removeEventListener(evt, _umarOnActivity, true));
}
