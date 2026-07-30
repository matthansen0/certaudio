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
        await loadVoices();
        await Promise.all([refreshCourses(), refreshJobs(), refreshAdmins()]);
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
        await loadVoices();
        await Promise.all([refreshCourses(), refreshJobs(), refreshAdmins()]);
    } catch (err) {
        setError('claim-error', err.message);
    } finally {
        el('claim-submit').disabled = false;
    }
});

// -------------------------------------------------------------------- courses
const VOICE_ROLES = ['instructional', 'podcastHost', 'podcastExpert'];

const store = {
    courses: [],
    current: null,
    voices: [],
    rates: null,
    defaults: null,
};

function isDragonHd(voice) {
    return Boolean(voice) && (voice.includes('DragonHD') || voice.includes(':Dragon'));
}

function money(value) {
    return `$${value.toFixed(2)}`;
}

function selectedVoices() {
    const voices = {};
    VOICE_ROLES.forEach((role) => {
        const node = el(`voice-${role}`);
        if (node && node.value) voices[role] = node.value;
    });
    return voices;
}

// Mirrors pipeline/cost.py, but driven by rates the server sends so the two
// cannot drift. Recomputed locally to keep the figure live as dropdowns change.
function computeEstimate() {
    const course = store.current;
    if (!course || !course.unitCount || !store.rates) return null;

    const rates = store.rates;
    const defaults = store.defaults;
    const format = el('job-format').value;
    const voices = selectedVoices();
    const rateFor = (v) => (isDragonHd(v) ? rates.dragonHdPerMChar : rates.neuralPerMChar);

    const ttsRate = format === 'podcast'
        ? (rateFor(voices.podcastHost) + rateFor(voices.podcastExpert)) / 2
        : rateFor(voices.instructional);

    const measured = course.measuredCharsPerEpisode;
    const charsPerEpisode = measured > 0
        ? measured
        : defaults.wordsPerEpisode * defaults.charsPerWord;

    const tts = (course.unitCount * charsPerEpisode * ttsRate) / 1e6;
    const llm = (course.unitCount * (
        defaults.gptInputTokensPerEpisode * rates.gptInputPerMTok
        + defaults.gptOutputTokensPerEpisode * rates.gptOutputPerMTok
    )) / 1e6;

    return {
        total: tts + llm,
        tts,
        llm,
        episodes: course.unitCount,
        basis: measured > 0 ? 'measured from previous runs' : 'estimated from typical episode length',
    };
}

function renderEstimate() {
    const estimate = computeEstimate();
    if (!estimate) {
        el('estimate-value').textContent = '—';
        el('estimate-detail').textContent = 'Index the course first to get an episode count.';
        return;
    }
    el('estimate-value').textContent = money(estimate.total);
    el('estimate-detail').textContent =
        `${estimate.episodes} episodes · ${money(estimate.tts)} speech + ${money(estimate.llm)} model · ${estimate.basis}`;
}

function renderIndexAge(course) {
    const node = el('index-age');
    if (!course.lastDiscoveryAt) {
        node.textContent = 'Never indexed. Index first to discover content and get an episode count.';
        return;
    }
    const days = Math.floor((Date.now() - new Date(course.lastDiscoveryAt).getTime()) / 86400000);
    const when = days <= 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
    const words = (course.totalWords || 0).toLocaleString();
    node.textContent =
        `Content indexed ${when} — ${course.unitCount} episodes from ${words} words. `
        + 'Re-index if Microsoft Learn has changed since.';
}

function toggleVoiceFields() {
    const isPodcast = el('job-format').value === 'podcast';
    el('field-voice-instructional').hidden = isPodcast;
    el('field-voice-host').hidden = !isPodcast;
    el('field-voice-expert').hidden = !isPodcast;
}

async function loadVoices() {
    try {
        const { voices } = await api('portal/voices');
        store.voices = voices;
    } catch {
        store.voices = [];
    }
    VOICE_ROLES.forEach((role) => {
        const node = el(`voice-${role}`);
        if (!node) return;
        node.innerHTML = store.voices.map((v) =>
            `<option value="${escapeHtml(v.shortName)}">${escapeHtml(v.displayName)}`
            + `${v.isDragonHD ? ' (HD)' : ''} — ${escapeHtml(v.shortName)}</option>`
        ).join('');
        node.addEventListener('change', renderEstimate);
    });
}

function renderCourseList() {
    const container = el('course-list');
    if (!store.courses.length) {
        container.className = 'admin-muted';
        container.textContent = 'No courses yet. Add one below.';
        return;
    }
    container.className = '';
    container.innerHTML = `
        <table class="admin-table">
            <thead><tr><th>Course</th><th>Format</th><th>Episodes</th><th>Last generated</th><th>State</th><th></th></tr></thead>
            <tbody>
                ${store.courses.map((c) => `<tr>
                    <td>${escapeHtml(c.displayName || c.id)}</td>
                    <td>${escapeHtml(c.audioFormat || '—')}</td>
                    <td>${c.episodeCount || 0}</td>
                    <td class="admin-muted">${escapeHtml((c.lastGeneratedAt || '—').replace('T', ' ').slice(0, 16))}</td>
                    <td>${c.published === false ? '<span class="admin-muted">unpublished</span>' : 'published'}</td>
                    <td><button class="admin-button admin-button-secondary"
                                data-course-id="${escapeHtml(c.id)}">Manage</button></td>
                </tr>`).join('')}
            </tbody>
        </table>
    `;
    container.querySelectorAll('[data-course-id]').forEach((button) => {
        button.addEventListener('click', () => openCourse(button.dataset.courseId));
    });
}

async function refreshCourses() {
    try {
        const { courses, rates } = await api('portal/courses');
        store.courses = courses;
        if (rates) store.rates = rates;
        renderCourseList();
    } catch (err) {
        setError('job-error', err.message);
    }
}

function renderFacts(course) {
    const voices = course.voices || {};
    const breakdown = course.formatBreakdown || {};
    const breakdownText = Object.keys(breakdown).length
        ? Object.entries(breakdown)
            .map(([name, s]) => `${name}: ${s.episodes}`)
            .join(', ')
        : '—';
    const rows = [
        ['Certification', course.id],
        ['Format', course.audioFormat || '—'],
        ['Episodes by format', breakdownText],
        ['Voice', voices.instructional || '—'],
        ['Podcast voices', voices.podcastHost ? `${voices.podcastHost} / ${voices.podcastExpert}` : '—'],
        ['Episodes', course.episodeCount || 0],
        ['Total audio', course.totalDurationSeconds
            ? `${(course.totalDurationSeconds / 3600).toFixed(1)} hours` : '—'],
        ['Last generated', (course.lastGeneratedAt || '—').replace('T', ' ').slice(0, 16)],
        ['Last estimate', course.lastEstimateUsd != null ? money(course.lastEstimateUsd) : '—'],
        ['Last actual cost', course.lastActualUsd != null ? money(course.lastActualUsd) : '—'],
    ];
    el('course-facts').innerHTML = rows.map(([label, value]) =>
        `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`
    ).join('');
}

async function openCourse(certId) {
    setError('course-error', null);
    setError('job-error', null);
    try {
        const { course, rates, defaults } = await api(`portal/courses/${encodeURIComponent(certId)}`);
        store.current = course;
        store.rates = rates;
        store.defaults = defaults;

        el('course-title').textContent = course.displayName || course.id;
        el('course-name').value = course.displayName || '';
        el('course-exam-url').value = course.examUrl || '';
        el('course-published').checked = course.published !== false;
        if (course.audioFormat) el('job-format').value = course.audioFormat;

        VOICE_ROLES.forEach((role) => {
            const node = el(`voice-${role}`);
            const value = (course.voices || {})[role];
            if (node && value) node.value = value;
        });

        renderFacts(course);
        renderIndexAge(course);
        toggleVoiceFields();
        renderEstimate();
        show('course-detail');
        el('course-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
        setError('job-error', err.message);
    }
}

el('course-close').addEventListener('click', () => {
    hide('course-detail');
    hide('confirm-panel');
    store.current = null;
});

el('job-format').addEventListener('change', () => {
    toggleVoiceFields();
    renderEstimate();
});

el('course-save').addEventListener('click', async () => {
    if (!store.current) return;
    setError('course-error', null);
    try {
        await api(`portal/courses/${encodeURIComponent(store.current.id)}`, {
            method: 'PATCH',
            body: JSON.stringify({
                displayName: el('course-name').value.trim(),
                examUrl: el('course-exam-url').value.trim(),
                published: el('course-published').checked,
            }),
        });
        await refreshCourses();
        await openCourse(store.current.id);
    } catch (err) {
        setError('course-error', err.message);
    }
});

el('course-delete').addEventListener('click', async () => {
    if (!store.current) return;
    const id = store.current.id;
    const confirmed = window.confirm(
        `Delete "${id}" and every episode, audio file, script and search document for it?\n\n`
        + 'This cannot be undone.'
    );
    if (!confirmed) return;

    setError('course-error', null);
    try {
        await api(`portal/courses/${encodeURIComponent(id)}`, { method: 'DELETE' });
        hide('course-detail');
        store.current = null;
        await refreshCourses();
    } catch (err) {
        setError('course-error', err.message);
    }
});

// ----------------------------------------------------------------------- jobs
async function submitJob(payload, errorTarget = 'job-error') {
    setError(errorTarget, null);
    try {
        await api('portal/jobs', { method: 'POST', body: JSON.stringify(payload) });
        await refreshJobs();
    } catch (err) {
        setError(errorTarget, err.message);
    }
}

el('run-index').addEventListener('click', () => {
    if (!store.current) return;
    submitJob({
        mode: 'index',
        certificationId: store.current.id,
        examUrl: el('course-exam-url').value.trim(),
    });
});

el('run-refresh').addEventListener('click', () => {
    if (!store.current) return;
    submitJob({
        mode: 'refresh',
        certificationId: store.current.id,
        audioFormat: el('job-format').value,
        voices: selectedVoices(),
        force: el('job-force').checked,
    });
});

el('run-generate').addEventListener('click', () => {
    if (!store.current) return;
    const estimate = computeEstimate();
    if (!estimate) {
        setError('job-error', 'Index this course before generating.');
        return;
    }
    el('confirm-summary').textContent =
        `Generate ${estimate.episodes} episodes for ${store.current.id} in `
        + `${el('job-format').value} format. Estimated cost ${money(estimate.total)} `
        + `(${estimate.basis}). This runs for several hours.`;
    show('confirm-panel');
    el('confirm-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
});

el('confirm-cancel').addEventListener('click', () => hide('confirm-panel'));

el('confirm-run').addEventListener('click', async () => {
    if (!store.current) return;
    hide('confirm-panel');
    await submitJob({
        mode: 'generate',
        certificationId: store.current.id,
        audioFormat: el('job-format').value,
        voices: selectedVoices(),
        force: el('job-force').checked,
    });
});

// --------------------------------------------------------------------- add new
el('new-index').addEventListener('click', async () => {
    const certificationId = el('new-cert').value.trim().toLowerCase();
    if (!certificationId) {
        setError('new-error', 'Enter a certification ID.');
        return;
    }
    await submitJob(
        { mode: 'index', certificationId, examUrl: el('new-exam-url').value.trim() },
        'new-error',
    );
    await refreshCourses();
});

el('new-test').addEventListener('click', async () => {
    await submitJob({ mode: 'index', certificationId: 'test' }, 'new-error');
    await refreshCourses();
});

function setRunButtonsDisabled(disabled) {
    ['run-index', 'run-generate', 'run-refresh', 'new-index', 'new-test'].forEach((id) => {
        const node = el(id);
        if (node) node.disabled = disabled;
    });
}

function renderActiveJob(job) {
    const container = el('active-job');
    if (!job) {
        container.className = 'admin-muted';
        container.textContent = 'No job running.';
        hide('redeploy-warning');
        setRunButtonsDisabled(false);
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
    setRunButtonsDisabled(true);
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
        await api(`portal/admins/${encodeURIComponent(adminId)}`, { method: 'DELETE' });
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
