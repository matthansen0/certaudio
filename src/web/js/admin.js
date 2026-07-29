/**
 * Admin portal.
 *
 * Access is decided server-side: every /api/portal/* route re-checks the caller
 * against the admin list. Hiding UI here is presentation only, not a control.
 */

const POLL_INTERVAL_MS = 5000;

const el = (id) => document.getElementById(id);
const show = (id) => el(id).removeAttribute('hidden');
const hide = (id) => el(id).setAttribute('hidden', '');

let pollTimer = null;

async function api(path, options = {}) {
    const response = await fetch(`/api/${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    let body = null;
    try {
        body = await response.json();
    } catch {
        body = null;
    }
    if (!response.ok) {
        throw new Error((body && body.error) || `Request failed (${response.status})`);
    }
    return body;
}

function setError(id, message) {
    const node = el(id);
    if (!message) {
        node.setAttribute('hidden', '');
        node.textContent = '';
        return;
    }
    node.textContent = message;
    node.removeAttribute('hidden');
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
}

// ------------------------------------------------------------------ bootstrap
async function init() {
    let status;
    try {
        status = await api('portal/status');
    } catch (err) {
        hide('admin-loading');
        show('admin-denied');
        el('denied-user').textContent = 'unknown';
        return;
    }

    hide('admin-loading');

    if (!status.authenticated) {
        window.location.href = '/.auth/login/aad?post_login_redirect_uri=/admin.html';
        return;
    }

    if (status.isAdmin) {
        show('admin-console');
        await Promise.all([refreshJobs(), refreshAdmins()]);
        return;
    }

    if (!status.bootstrapClaimed) {
        show('admin-claim');
        return;
    }

    el('denied-user').textContent = status.userDetails || 'this account';
    show('admin-denied');
}

el('claim-submit').addEventListener('click', async () => {
    const token = el('claim-token').value.trim();
    if (!token) {
        setError('claim-error', 'Enter the bootstrap token.');
        return;
    }
    setError('claim-error', null);
    el('claim-submit').disabled = true;
    try {
        await api('portal/claim', { method: 'POST', body: JSON.stringify({ token }) });
        hide('admin-claim');
        show('admin-console');
        await Promise.all([refreshJobs(), refreshAdmins()]);
    } catch (err) {
        setError('claim-error', err.message);
    } finally {
        el('claim-submit').disabled = false;
    }
});

// ----------------------------------------------------------------------- jobs
el('job-submit').addEventListener('click', async () => {
    const payload = {
        mode: el('job-mode').value,
        certificationId: el('job-cert').value.trim(),
        audioFormat: el('job-format').value,
        force: el('job-force').checked,
    };
    if (!payload.certificationId) {
        setError('job-error', 'Certification is required.');
        return;
    }
    setError('job-error', null);
    el('job-submit').disabled = true;
    try {
        await api('portal/jobs', { method: 'POST', body: JSON.stringify(payload) });
        await refreshJobs();
    } catch (err) {
        setError('job-error', err.message);
    } finally {
        el('job-submit').disabled = false;
    }
});

function renderActiveJob(job) {
    const container = el('active-job');
    if (!job) {
        container.className = 'admin-muted';
        container.textContent = 'No job running.';
        hide('redeploy-warning');
        el('job-submit').disabled = false;
        return;
    }

    const progress = job.progress || {};
    const total = progress.total || 0;
    const current = progress.current || 0;
    const pct = total > 0 ? Math.round((current / total) * 100) : 0;

    container.className = '';
    container.innerHTML = `
        <div><strong>${escapeHtml(job.mode)}</strong> &middot;
             ${escapeHtml(job.certificationId)} / ${escapeHtml(job.audioFormat)}
             <span class="admin-status admin-status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></div>
        <div class="admin-progress"><div class="admin-progress-bar" style="width:${pct}%"></div></div>
        <div class="admin-muted">${escapeHtml(job.phase || '')} &mdash;
             ${escapeHtml(progress.message || '')}${total ? ` (${current}/${total})` : ''}</div>
    `;
    show('redeploy-warning');
    el('job-submit').disabled = true;
}

function renderHistory(jobs) {
    const container = el('job-history');
    if (!jobs.length) {
        container.className = 'admin-muted';
        container.textContent = 'No jobs yet.';
        return;
    }
    container.className = '';
    container.innerHTML = `
        <table class="admin-table">
            <thead><tr><th>Started</th><th>Mode</th><th>Target</th><th>Status</th><th>Result</th></tr></thead>
            <tbody>
                ${jobs.map((job) => {
                    const started = job.startedAt || job.createdAt || '';
                    const result = job.error
                        ? escapeHtml(job.error.slice(0, 90))
                        : job.result
                            ? `${job.result.episodesGenerated ?? 0} generated`
                            : '';
                    return `<tr>
                        <td>${escapeHtml(started.replace('T', ' ').slice(0, 19))}</td>
                        <td>${escapeHtml(job.mode)}</td>
                        <td>${escapeHtml(job.certificationId)}/${escapeHtml(job.audioFormat)}</td>
                        <td><span class="admin-status admin-status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></td>
                        <td class="admin-muted">${result}</td>
                    </tr>`;
                }).join('')}
            </tbody>
        </table>
    `;
}

async function refreshJobs() {
    try {
        const { jobs } = await api('portal/jobs');
        const active = jobs.find((j) => j.status === 'queued' || j.status === 'running');
        renderActiveJob(active);
        renderHistory(jobs);

        if (active && !pollTimer) {
            pollTimer = setInterval(refreshJobs, POLL_INTERVAL_MS);
        } else if (!active && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    } catch (err) {
        setError('job-error', err.message);
    }
}

// --------------------------------------------------------------------- admins
async function refreshAdmins() {
    try {
        const { admins } = await api('portal/admins');
        const container = el('admin-list');
        if (!admins.length) {
            container.className = 'admin-muted';
            container.textContent = 'No administrators registered.';
            return;
        }
        container.className = '';
        container.innerHTML = `
            <table class="admin-table">
                <thead><tr><th>Account</th><th>Added by</th><th></th></tr></thead>
                <tbody>
                    ${admins.map((a) => `<tr>
                        <td>${escapeHtml(a.userDetails || a.id)}</td>
                        <td class="admin-muted">${escapeHtml(a.addedBy || '')}</td>
                        <td><button class="admin-button admin-button-danger"
                                    data-admin-id="${escapeHtml(a.id)}">Remove</button></td>
                    </tr>`).join('')}
                </tbody>
            </table>
        `;
        container.querySelectorAll('[data-admin-id]').forEach((button) => {
            button.addEventListener('click', () => removeAdmin(button.dataset.adminId));
        });
    } catch (err) {
        setError('admins-error', err.message);
    }
}

async function removeAdmin(adminId) {
    setError('admins-error', null);
    try {
        await api(`admin/admins/${encodeURIComponent(adminId)}`, { method: 'DELETE' });
        await refreshAdmins();
    } catch (err) {
        setError('admins-error', err.message);
    }
}

el('add-admin').addEventListener('click', async () => {
    const userDetails = el('new-admin').value.trim();
    if (!userDetails) {
        setError('admins-error', 'Enter an account address.');
        return;
    }
    setError('admins-error', null);
    try {
        await api('portal/admins', { method: 'POST', body: JSON.stringify({ userDetails }) });
        el('new-admin').value = '';
        await refreshAdmins();
    } catch (err) {
        setError('admins-error', err.message);
    }
});

init();
