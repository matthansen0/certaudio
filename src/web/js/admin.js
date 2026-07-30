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

// ---------------------------------------------------------------------- toasts
const TOAST_MS = 6000;

function toast(message, kind = 'info') {
    const node = document.createElement('div');
    node.className = `admin-toast admin-toast-${kind}`;
    node.innerHTML = `<span>${escapeHtml(message)}</span>`
        + '<button type="button" class="admin-toast-close" aria-label="Dismiss">&times;</button>';
    const remove = () => node.remove();
    node.querySelector('.admin-toast-close').addEventListener('click', remove);
    el('toasts').appendChild(node);
    if (kind !== 'error') setTimeout(remove, TOAST_MS);
}

// Errors go to both: inline for the field that caused them, toast so they are
// noticed when the relevant panel has scrolled out of view.
function fail(errorId, err) {
    setError(errorId, err.message);
    toast(err.message, 'error');
}

/** Run an async action with the button showing a spinner and disabled. */
async function withBusy(button, action) {
    if (!button) return action();
    const wasDisabled = button.disabled;
    button.dataset.busy = 'true';
    button.disabled = true;
    try {
        return await action();
    } finally {
        delete button.dataset.busy;
        button.disabled = wasDisabled;
    }
}

function skeleton(rows = 3) {
    return `<div class="admin-skeleton" aria-hidden="true">${
        '<div class="admin-skeleton-row"></div>'.repeat(rows)
    }</div>`;
}

function emptyState(title, detail) {
    return `<div class="admin-empty"><span class="admin-empty-title">${escapeHtml(title)}</span>`
        + `<span>${escapeHtml(detail)}</span></div>`;
}

// ----------------------------------------------------------------------- views
const VIEWS = ['courses', 'jobs', 'admins'];

function showView(name) {
    VIEWS.forEach((view) => {
        el(`view-${view}`).hidden = view !== name;
    });
    document.querySelectorAll('[data-view]').forEach((button) => {
        button.setAttribute('aria-current', String(button.dataset.view === name));
    });
}

document.querySelectorAll('[data-view]').forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.view));
});

function enterConsole() {
    hide('admin-gate');
    show('admin-console');
    el('course-list').innerHTML = skeleton();
    el('job-history').innerHTML = skeleton(2);
    el('admin-list').innerHTML = skeleton(2);
    el('active-job').innerHTML = skeleton(1);
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
        enterConsole();
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

el('copy-bootstrap').addEventListener('click', async () => {
    try {
        await navigator.clipboard.writeText(el('bootstrap-command').textContent);
        toast('Command copied to the clipboard.', 'success');
    } catch {
        toast('Could not copy. Select the text and copy it manually.', 'error');
    }
});

el('claim-submit').addEventListener('click', async (event) => {
    const token = el('claim-token').value.trim();
    if (!token) {
        setError('claim-error', 'Enter the bootstrap token.');
        return;
    }
    setError('claim-error', null);
    await withBusy(event.currentTarget, async () => {
        try {
            await api('portal/claim', { method: 'POST', body: JSON.stringify({ token }) });
            hide('admin-claim');
            enterConsole();
            toast('You are now an administrator.', 'success');
            await loadVoices();
            await Promise.all([refreshCourses(), refreshJobs(), refreshAdmins()]);
        } catch (err) {
            fail('claim-error', err);
        }
    });
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
    el('count-courses').textContent = store.courses.length;
    if (!store.courses.length) {
        container.innerHTML = emptyState(
            'No courses yet',
            'Add a certification below to discover and index its content.',
        );
        return;
    }
    container.innerHTML = `
        <div class="admin-table-wrap">
        <table class="admin-table">
            <thead><tr>
                <th scope="col">Course</th><th scope="col">Format</th>
                <th scope="col">Episodes</th><th scope="col">Last generated</th>
                <th scope="col">State</th><th scope="col"><span class="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>
                ${store.courses.map((c) => `<tr>
                    <td data-label="Course">${escapeHtml(c.displayName || c.id)}</td>
                    <td data-label="Format">${escapeHtml(c.audioFormat || '—')}</td>
                    <td data-label="Episodes">${c.episodeCount || 0}</td>
                    <td data-label="Last generated">${escapeHtml((c.lastGeneratedAt || '—').replace('T', ' ').slice(0, 16))}</td>
                    <td data-label="State">${c.published === false ? 'unpublished' : 'published'}</td>
                    <td><button type="button" class="admin-button admin-button-secondary admin-button-small"
                                data-course-id="${escapeHtml(c.id)}">Manage</button></td>
                </tr>`).join('')}
            </tbody>
        </table>
        </div>
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
        fail('job-error', err);
    }
}

// Discovery can succeed while covering only part of the exam. The report is the
// only place that difference is visible.
function renderDiscoveryReport(report) {
    const container = el('course-report');
    if (!report) {
        container.innerHTML = '';
        return;
    }
    const grade = report.coverageGrade || '?';
    const score = Number(report.coverageScore || 0).toFixed(0);
    const sources = Object.keys(report.sources || {}).join(', ') || 'none';
    const gaps = (report.gaps || []).slice(0, 8);

    container.innerHTML = `
        <div class="admin-report">
            <div class="admin-report-head">
                <span class="admin-grade" data-grade="${escapeHtml(grade)}">${escapeHtml(grade)}</span>
                <strong>Exam coverage ${escapeHtml(score)}%</strong>
                <span class="admin-muted" style="margin:0">resolved via ${escapeHtml(sources)}</span>
            </div>
            <div class="admin-report-stats">
                <span><strong>${report.resolvedPaths || 0}</strong> learning paths</span>
                <span><strong>${report.unitsDiscovered || 0}</strong> units</span>
                <span><strong>${report.unitsFailed || 0}</strong> failed downloads</span>
                <span><strong>${report.topicsCovered || 0}</strong> topics covered</span>
                <span><strong>${report.topicsSupplemented || 0}</strong> supplemented</span>
                <span><strong>${report.topicsUncovered || 0}</strong> uncovered</span>
            </div>
            ${(report.warnings || []).map((w) =>
                `<p class="admin-warning">${escapeHtml(w)}</p>`).join('')}
            ${gaps.length ? `<details>
                <summary class="admin-muted" style="cursor:pointer">Uncovered exam topics</summary>
                <ul class="admin-gap-list">${gaps.map((g) =>
                    `<li>${escapeHtml(g.topic || '')} <em>(${escapeHtml(g.skill || '')})</em></li>`).join('')}
                </ul></details>` : ''}
        </div>
    `;
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
        renderDiscoveryReport(course.discoveryReport);
        el('updates-report').innerHTML = '';
        renderIndexAge(course);
        toggleVoiceFields();
        renderEstimate();
        show('course-detail');
        el('course-detail').focus();
        el('course-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
        fail('job-error', err);
    }
}

el('course-close').addEventListener('click', () => {
    hide('course-detail');
    hide('confirm-panel');
    store.current = null;
});

el('check-updates').addEventListener('click', async (event) => {
    if (!store.current) return;
    const target = el('updates-report');
    target.innerHTML = skeleton(2);
    await withBusy(event.currentTarget, async () => {
        try {
            const r = await api(`portal/courses/${encodeURIComponent(store.current.id)}/updates`);
            renderUpdates(r);
        } catch (err) {
            target.innerHTML = '';
            fail('job-error', err);
        }
    });
});

function renderUpdates(r) {
    const target = el('updates-report');
    if (!r.tracked) {
        target.innerHTML = `<p class="admin-warning">Nothing is tracked yet for this course.
            Staleness is recorded when episodes are generated, so this stays empty until the
            next generate or refresh run.${r.untracked ? ` ${r.untracked} source(s) indexed.` : ''}</p>`;
        return;
    }
    const stale = r.staleEpisodes || [];
    if (!stale.length) {
        target.innerHTML = `<p class="admin-muted" style="margin-top:0.75rem">Up to date —
            ${r.unchangedSources} source(s) unchanged since these episodes were generated.
            ${r.errors ? `${r.errors} could not be checked.` : ''}</p>`;
        return;
    }
    target.innerHTML = `
        <div class="admin-report">
            <div class="admin-report-head">
                <strong>${stale.length} episode${stale.length === 1 ? '' : 's'} out of date</strong>
                <span class="admin-muted" style="margin:0">${r.changedSources} source(s) changed
                    upstream${r.errors ? `, ${r.errors} unreachable` : ''}</span>
            </div>
            <p class="admin-muted" style="margin:0">Run <strong>Refresh changed content</strong>
                to regenerate only the affected batches.</p>
            <ul class="admin-gap-list">${stale.slice(0, 12).map((e) =>
                `<li>${escapeHtml(e)}</li>`).join('')}</ul>
        </div>`;
}

el('job-format').addEventListener('change', () => {
    toggleVoiceFields();
    renderEstimate();
});

el('course-save').addEventListener('click', async (event) => {
    if (!store.current) return;
    setError('course-error', null);
    const url = el('course-exam-url').value.trim();
    const urlField = el('course-exam-url');
    if (url && !url.startsWith('https://learn.microsoft.com/')) {
        urlField.setAttribute('aria-invalid', 'true');
        setError('course-error', 'The exam URL must start with https://learn.microsoft.com/.');
        return;
    }
    urlField.removeAttribute('aria-invalid');
    await withBusy(event.currentTarget, async () => {
        try {
            await api(`portal/courses/${encodeURIComponent(store.current.id)}`, {
                method: 'PATCH',
                body: JSON.stringify({
                    displayName: el('course-name').value.trim(),
                    examUrl: url,
                    published: el('course-published').checked,
                }),
            });
            toast('Course details saved.', 'success');
            await refreshCourses();
            await openCourse(store.current.id);
        } catch (err) {
            fail('course-error', err);
        }
    });
});

el('course-delete').addEventListener('click', async (event) => {
    if (!store.current) return;
    const id = store.current.id;
    const confirmed = window.confirm(
        `Delete "${id}" and every episode, audio file, script and search document for it?\n\n`
        + 'This cannot be undone.'
    );
    if (!confirmed) return;

    setError('course-error', null);
    await withBusy(event.currentTarget, async () => {
        try {
            await api(`portal/courses/${encodeURIComponent(id)}`, { method: 'DELETE' });
            hide('course-detail');
            store.current = null;
            toast(`Deleted ${id}.`, 'success');
            await refreshCourses();
        } catch (err) {
            fail('course-error', err);
        }
    });
});

// ----------------------------------------------------------------------- jobs
async function submitJob(payload, errorTarget = 'job-error', button = null) {
    setError(errorTarget, null);
    await withBusy(button, async () => {
        try {
            await api('portal/jobs', { method: 'POST', body: JSON.stringify(payload) });
            toast(`Queued ${payload.mode} job for ${payload.certificationId}.`, 'success');
            showView('jobs');
            await refreshJobs();
        } catch (err) {
            fail(errorTarget, err);
        }
    });
}

el('run-index').addEventListener('click', (event) => {
    if (!store.current) return;
    submitJob({
        mode: 'index',
        certificationId: store.current.id,
        examUrl: el('course-exam-url').value.trim(),
    }, 'job-error', event.currentTarget);
});

el('run-refresh').addEventListener('click', (event) => {
    if (!store.current) return;
    submitJob({
        mode: 'refresh',
        certificationId: store.current.id,
        audioFormat: el('job-format').value,
        voices: selectedVoices(),
        force: el('job-force').checked,
    }, 'job-error', event.currentTarget);
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
    el('confirm-panel').focus();
});

el('confirm-cancel').addEventListener('click', () => hide('confirm-panel'));

el('confirm-run').addEventListener('click', async (event) => {
    if (!store.current) return;
    hide('confirm-panel');
    await submitJob({
        mode: 'generate',
        certificationId: store.current.id,
        audioFormat: el('job-format').value,
        voices: selectedVoices(),
        force: el('job-force').checked,
    }, 'job-error', event.currentTarget);
});

// --------------------------------------------------------------------- add new
el('new-index').addEventListener('click', async (event) => {
    const certificationId = el('new-cert').value.trim().toLowerCase();
    const field = el('new-cert');
    if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(certificationId)) {
        field.setAttribute('aria-invalid', 'true');
        setError('new-error', 'Enter a certification ID such as dp-700 (lowercase letters, digits and hyphens).');
        return;
    }
    field.removeAttribute('aria-invalid');
    await submitJob(
        { mode: 'index', certificationId, examUrl: el('new-exam-url').value.trim() },
        'new-error',
        event.currentTarget,
    );
    await refreshCourses();
});

el('new-test').addEventListener('click', async (event) => {
    await submitJob({ mode: 'index', certificationId: 'test' }, 'new-error', event.currentTarget);
    await refreshCourses();
});

function setRunButtonsDisabled(disabled) {
    ['run-index', 'run-generate', 'run-refresh', 'new-index', 'new-test'].forEach((id) => {
        const node = el(id);
        if (node) node.disabled = disabled;
    });
}

// An index job returns unit and coverage counts; only generate/refresh produce
// episodes. Reading episodesGenerated for both showed every index run as
// "0 generated", which reads as "found nothing".
function jobOutcome(job) {
    if (job.error) return escapeHtml(job.error.slice(0, 120));
    const result = job.result;
    if (!result) return '';
    if (job.mode === 'index') {
        const report = result.discoveryReport || {};
        const bits = [`${result.totalUnits ?? 0} units`, `${result.unitCount ?? 0} episodes`];
        if (result.totalWords) bits.push(`${Number(result.totalWords).toLocaleString()} words`);
        if (report.coverageGrade) bits.push(`coverage ${escapeHtml(report.coverageGrade)}`);
        if (report.unitsFailed) bits.push(`${report.unitsFailed} failed`);
        return escapeHtml(bits.join(' · '));
    }
    return `${result.episodesGenerated ?? 0} episodes generated`;
}

function renderJobLog(job) {
    const entries = job.log || [];
    if (!entries.length) return '';
    const rows = entries.slice(-12).reverse().map((e) =>
        `<li><span class="admin-log-time">${escapeHtml((e.at || '').slice(11, 19))}</span>`
        + `<span class="admin-log-phase">${escapeHtml(e.phase || '')}</span>`
        + `<span>${escapeHtml(e.message || '')}</span></li>`).join('');
    return `<details class="admin-log" open>
        <summary>Activity (${entries.length})</summary>
        <ul class="admin-log-list">${rows}</ul>
    </details>`;
}

function renderActiveJob(job) {
    const container = el('active-job');
    if (!job) {
        container.innerHTML = emptyState('No job running', 'Start one from a course.');
        hide('redeploy-warning');
        setRunButtonsDisabled(false);
        return;
    }

    const progress = job.progress || {};
    const total = progress.total || 0;
    const current = progress.current || 0;
    const pct = total > 0 ? Math.round((current / total) * 100) : 0;

    container.innerHTML = `
        <div class="admin-row admin-row-between" style="margin-top:0">
            <div><strong>${escapeHtml(job.mode)}</strong> &middot;
                 ${escapeHtml(job.certificationId)} / ${escapeHtml(job.audioFormat)}
                 <span class="admin-status admin-status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></div>
            <button type="button" id="cancel-job" class="admin-button admin-button-danger admin-button-small"
                    data-job-id="${escapeHtml(job.jobId)}">Cancel job</button>
        </div>
        <div class="admin-progress" role="progressbar" aria-valuenow="${pct}"
             aria-valuemin="0" aria-valuemax="100">
            <div class="admin-progress-bar" style="width:${pct}%"></div>
        </div>
        <p class="admin-muted" style="margin:0">${escapeHtml(job.phase || '')} &mdash;
             ${escapeHtml(progress.message || '')}${total ? ` (${current}/${total}${pct ? `, ${pct}%` : ''})` : ''}</p>
        ${renderJobLog(job)}
    `;
    container.querySelector('#cancel-job').addEventListener('click', (event) =>
        cancelJob(job.jobId, event.currentTarget));
    show('redeploy-warning');
    setRunButtonsDisabled(true);
}

async function cancelJob(jobId, button) {
    if (!window.confirm('Cancel this job? A run already in progress finishes its current step.')) {
        return;
    }
    setError('jobs-error', null);
    await withBusy(button, async () => {
        try {
            await api(`portal/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
            toast('Job cancelled.', 'success');
            await refreshJobs();
        } catch (err) {
            fail('jobs-error', err);
        }
    });
}

function renderHistory(jobs) {
    const container = el('job-history');
    el('count-jobs').textContent = jobs.length;
    if (!jobs.length) {
        container.innerHTML = emptyState('No jobs yet', 'Index a course to run the first one.');
        return;
    }
    container.innerHTML = `
        <div class="admin-table-wrap">
        <table class="admin-table">
            <thead><tr>
                <th scope="col">Started</th><th scope="col">Mode</th><th scope="col">Target</th>
                <th scope="col">Status</th><th scope="col">Result</th>
            </tr></thead>
            <tbody>
                ${jobs.map((job) => {
                    const started = job.startedAt || job.createdAt || '';
                    return `<tr>
                        <td data-label="Started">${escapeHtml(started.replace('T', ' ').slice(0, 19))}</td>
                        <td data-label="Mode">${escapeHtml(job.mode)}</td>
                        <td data-label="Target">${escapeHtml(job.certificationId)}/${escapeHtml(job.audioFormat)}</td>
                        <td data-label="Status"><span class="admin-status admin-status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></td>
                        <td data-label="Result">${jobOutcome(job)}</td>
                    </tr>`;
                }).join('')}
            </tbody>
        </table>
        </div>
    `;
}

async function refreshJobs() {
    try {
        const { jobs } = await api('portal/jobs');
        const active = jobs.find((j) => j.status === 'queued' || j.status === 'running');
        renderActiveJob(active);
        renderHistory(jobs);
        setError('jobs-error', null);

        if (active && !pollTimer) {
            pollTimer = setInterval(refreshJobs, POLL_INTERVAL_MS);
        } else if (!active && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    } catch (err) {
        fail('jobs-error', err);
    }
}

// --------------------------------------------------------------------- admins
async function refreshAdmins() {
    try {
        const { admins } = await api('portal/admins');
        const container = el('admin-list');
        el('count-admins').textContent = admins.length;
        if (!admins.length) {
            container.innerHTML = emptyState('No administrators', 'Add one below.');
            return;
        }
        container.innerHTML = `
            <div class="admin-table-wrap">
            <table class="admin-table">
                <thead><tr>
                    <th scope="col">Account</th><th scope="col">Added by</th>
                    <th scope="col"><span class="sr-only">Actions</span></th>
                </tr></thead>
                <tbody>
                    ${admins.map((a) => `<tr>
                        <td data-label="Account">${escapeHtml(a.userDetails || a.id)}</td>
                        <td data-label="Added by">${escapeHtml(a.addedBy || '')}</td>
                        <td><button type="button" class="admin-button admin-button-danger admin-button-small"
                                    data-admin-id="${escapeHtml(a.id)}">Remove</button></td>
                    </tr>`).join('')}
                </tbody>
            </table>
            </div>
        `;
        container.querySelectorAll('[data-admin-id]').forEach((button) => {
            button.addEventListener('click', (event) =>
                removeAdmin(button.dataset.adminId, event.currentTarget));
        });
    } catch (err) {
        fail('admins-error', err);
    }
}

async function removeAdmin(adminId, button) {
    if (!window.confirm(`Remove ${adminId} from the administrators of this environment?`)) {
        return;
    }
    setError('admins-error', null);
    await withBusy(button, async () => {
        try {
            await api(`portal/admins/${encodeURIComponent(adminId)}`, { method: 'DELETE' });
            toast('Administrator removed.', 'success');
            await refreshAdmins();
        } catch (err) {
            fail('admins-error', err);
        }
    });
}

el('add-admin').addEventListener('click', async (event) => {
    const userDetails = el('new-admin').value.trim();
    const field = el('new-admin');
    if (!userDetails) {
        field.setAttribute('aria-invalid', 'true');
        setError('admins-error', 'Enter an account address.');
        return;
    }
    field.removeAttribute('aria-invalid');
    setError('admins-error', null);
    await withBusy(event.currentTarget, async () => {
        try {
            await api('portal/admins', { method: 'POST', body: JSON.stringify({ userDetails }) });
            el('new-admin').value = '';
            toast(`Added ${userDetails}.`, 'success');
            await refreshAdmins();
        } catch (err) {
            fail('admins-error', err);
        }
    });
});

init();
