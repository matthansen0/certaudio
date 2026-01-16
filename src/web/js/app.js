/**
 * Certification Audio Learning Platform
 * Main Application Module
 */

// Configuration
const API_BASE = '/api';
const STORAGE_KEY_PROGRESS = 'certaudio_progress';
const STORAGE_KEY_USER = 'certaudio_user';
const STORAGE_KEY_LAST_CERT = 'certaudio_last_cert';

const FALLBACK_CERTIFICATIONS = [
    { id: 'ai-102', name: 'AI-102: Azure AI Engineer' },
    { id: 'az-204', name: 'AZ-204: Azure Developer' },
    { id: 'az-104', name: 'AZ-104: Azure Administrator' },
    { id: 'az-900', name: 'AZ-900: Azure Fundamentals' },
    { id: 'dp-700', name: 'DP-700: Fabric Data Engineer' },
];

// State
let state = {
    certificationId: 'ai-102',
    audioFormat: 'instructional',
    episodes: {},
    domains: {},
    currentEpisode: null,
    userId: null,
    progress: {},
    isPlaying: false,
};

// DOM Elements
const elements = {
    certSelect: document.getElementById('certSelect'),
    formatSelect: document.getElementById('formatSelect'),
    episodeList: document.getElementById('episodeList'),
    progressSummary: document.getElementById('progressSummary'),
    progressFill: document.getElementById('progressFill'),
    episodeTitle: document.getElementById('episodeTitle'),
    episodeDomain: document.getElementById('episodeDomain'),
    episodeTopics: document.getElementById('episodeTopics'),
    audioElement: document.getElementById('audioElement'),
    btnPlay: document.getElementById('btnPlay'),
    btnRewind: document.getElementById('btnRewind'),
    btnForward: document.getElementById('btnForward'),
    timeCurrent: document.getElementById('timeCurrent'),
    timeTotal: document.getElementById('timeTotal'),
    progressTrack: document.getElementById('progressTrack'),
    progressPlayed: document.getElementById('progressPlayed'),
    progressHandle: document.getElementById('progressHandle'),
    speedSelect: document.getElementById('speedSelect'),
    markComplete: document.getElementById('markComplete'),
    btnTranscript: document.getElementById('btnTranscript'),
    transcriptPanel: document.getElementById('transcriptPanel'),
    transcriptContent: document.getElementById('transcriptContent'),
    btnCloseTranscript: document.getElementById('btnCloseTranscript'),
    signInBtn: document.getElementById('signInBtn'),
};

// ============================================
// Initialization
// ============================================

async function init() {
    // Load user ID from storage or generate anonymous ID
    state.userId = localStorage.getItem(STORAGE_KEY_USER) || generateUserId();
    localStorage.setItem(STORAGE_KEY_USER, state.userId);
    
    // Load local progress
    state.progress = JSON.parse(localStorage.getItem(STORAGE_KEY_PROGRESS) || '{}');
    
    // Set up event listeners
    setupEventListeners();

    // Load certifications list (dynamic) and pick last selection if available
    await loadCertifications();
    
    // Load episodes for default certification
    await loadEpisodes();
}

function generateUserId() {
    return 'anon_' + Math.random().toString(36).substring(2, 15);
}

function setupEventListeners() {
    // Certification and format selection
    elements.certSelect.addEventListener('change', async (e) => {
        state.certificationId = e.target.value;
        localStorage.setItem(STORAGE_KEY_LAST_CERT, state.certificationId);
        await loadEpisodes();
    });
    
    elements.formatSelect.addEventListener('change', async (e) => {
        state.audioFormat = e.target.value;
        await loadEpisodes();
    });
    
    // Player controls
    elements.btnPlay.addEventListener('click', togglePlay);
    elements.btnRewind.addEventListener('click', () => seek(-15));
    elements.btnForward.addEventListener('click', () => seek(15));
    
    // Audio element events
    elements.audioElement.addEventListener('timeupdate', updateProgress);
    elements.audioElement.addEventListener('loadedmetadata', updateDuration);
    elements.audioElement.addEventListener('ended', onEpisodeEnd);
    elements.audioElement.addEventListener('play', () => updatePlayButton(true));
    elements.audioElement.addEventListener('pause', () => updatePlayButton(false));
    
    // Progress bar seeking
    elements.progressTrack.addEventListener('click', seekToPosition);
    
    // Speed control
    elements.speedSelect.addEventListener('change', (e) => {
        elements.audioElement.playbackRate = parseFloat(e.target.value);
    });
    
    // Mark complete
    elements.markComplete.addEventListener('change', (e) => {
        if (state.currentEpisode) {
            updateEpisodeProgress(state.currentEpisode.id, e.target.checked);
        }
    });
    
    // Transcript
    elements.btnTranscript.addEventListener('click', toggleTranscript);
    elements.btnCloseTranscript.addEventListener('click', () => {
        elements.transcriptPanel.classList.remove('visible');
    });
}

// ============================================
// Certifications
// ============================================

function setCertOptions(certs, selectedId) {
    // Replace options
    elements.certSelect.innerHTML = '';

    // Ensure stable ordering
    const sorted = [...certs].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
    for (const cert of sorted) {
        const opt = document.createElement('option');
        opt.value = cert.id;
        opt.textContent = cert.name || cert.id;
        elements.certSelect.appendChild(opt);
    }

    // Select desired cert if present, else pick first.
    const candidate = selectedId && sorted.some(c => c.id === selectedId)
        ? selectedId
        : (sorted[0]?.id || '');
    if (candidate) {
        elements.certSelect.value = candidate;
        state.certificationId = candidate;
        localStorage.setItem(STORAGE_KEY_LAST_CERT, state.certificationId);
    }
}

async function loadCertifications() {
    const lastCert = localStorage.getItem(STORAGE_KEY_LAST_CERT) || state.certificationId;

    try {
        const response = await fetch(`${API_BASE}/certifications`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();

        const apiCerts = Array.isArray(payload?.certifications) ? payload.certifications : [];
        const normalized = apiCerts
            .filter(c => c && typeof c.id === 'string' && c.id.trim())
            .map(c => ({ id: c.id.toLowerCase(), name: c.name || c.id.toUpperCase() }));

        // Union with fallback so users can pick upcoming certs even before content exists.
        const byId = new Map();
        for (const c of [...FALLBACK_CERTIFICATIONS, ...normalized]) {
            if (!byId.has(c.id)) byId.set(c.id, c);
        }

        setCertOptions([...byId.values()], lastCert?.toLowerCase());
    } catch (err) {
        console.warn('Failed to load certifications from API; using fallback list.', err);
        setCertOptions(FALLBACK_CERTIFICATIONS, lastCert?.toLowerCase());
    }
}

// ============================================
// API Functions
// ============================================

async function loadEpisodes() {
    elements.episodeList.innerHTML = '<div class="loading">Loading episodes...</div>';
    
    try {
        const response = await fetch(
            `${API_BASE}/episodes/${state.certificationId}/${state.audioFormat}`
        );
        
        if (!response.ok) {
            throw new Error('Failed to load episodes');
        }
        
        const data = await response.json();
        state.domains = data.domains || {};
        
        // Flatten episodes for easy lookup
        state.episodes = {};
        Object.values(state.domains).forEach(episodes => {
            episodes.forEach(ep => {
                state.episodes[ep.id] = ep;
            });
        });
        
        renderEpisodeList();
        updateProgressSummary();
        
    } catch (error) {
        console.error('Error loading episodes:', error);
        elements.episodeList.innerHTML = `
            <div class="loading">
                <p>Unable to load episodes.</p>
                <p style="font-size: 0.875rem; margin-top: 0.5rem;">
                    Make sure the API is running and content has been generated.
                </p>
            </div>
        `;
    }
}

async function loadTranscript(episodeNumber) {
    try {
        const response = await fetch(
            `${API_BASE}/script/${state.certificationId}/${state.audioFormat}/${episodeNumber}`
        );
        
        if (!response.ok) {
            throw new Error('Transcript not available');
        }
        
        const markdown = await response.text();
        // Simple markdown to HTML conversion
        const html = markdown
            .replace(/^### (.*$)/gim, '<h3>$1</h3>')
            .replace(/^## (.*$)/gim, '<h2>$1</h2>')
            .replace(/^# (.*$)/gim, '<h1>$1</h1>')
            .replace(/\*\*(.*)\*\*/gim, '<strong>$1</strong>')
            .replace(/\*(.*)\*/gim, '<em>$1</em>')
            .replace(/\n/gim, '</p><p>');
        
        elements.transcriptContent.innerHTML = `<p>${html}</p>`;
        
    } catch (error) {
        elements.transcriptContent.innerHTML = '<p>Transcript not available.</p>';
    }
}

async function syncProgressToServer() {
    if (!state.userId || !state.currentEpisode) return;
    
    try {
        await fetch(
            `${API_BASE}/progress/${state.userId}/${state.certificationId}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    episodeId: state.currentEpisode.id,
                    completed: state.progress[state.currentEpisode.id]?.completed || false,
                    position: Math.floor(elements.audioElement.currentTime),
                }),
            }
        );
    } catch (error) {
        console.warn('Could not sync progress to server:', error);
    }
}

// ============================================
// UI Rendering
// ============================================

function renderEpisodeList() {
    const html = Object.entries(state.domains)
        .map(([domain, episodes]) => `
            <div class="domain-group">
                <div class="domain-title">${domain}</div>
                ${episodes.map(ep => renderEpisodeItem(ep)).join('')}
            </div>
        `)
        .join('');
    
    elements.episodeList.innerHTML = html || '<div class="loading">No episodes found.</div>';
    
    // Add click handlers
    document.querySelectorAll('.episode-item').forEach(item => {
        item.addEventListener('click', () => {
            const episodeId = item.dataset.episodeId;
            playEpisode(state.episodes[episodeId]);
        });
    });
}

function renderEpisodeItem(episode) {
    const isCompleted = state.progress[episode.id]?.completed;
    const isActive = state.currentEpisode?.id === episode.id;
    const duration = formatDuration(episode.durationSeconds);
    
    return `
        <div class="episode-item ${isCompleted ? 'completed' : ''} ${isActive ? 'active' : ''}"
             data-episode-id="${episode.id}">
            <div class="episode-number">${episode.sequenceNumber}</div>
            <div class="episode-details">
                <div class="episode-item-title">
                    ${episode.title}
                    ${episode.isAmendment ? '<span class="amendment-badge">Update</span>' : ''}
                </div>
                <div class="episode-duration">${duration}</div>
            </div>
        </div>
    `;
}

function updateProgressSummary() {
    const totalEpisodes = Object.keys(state.episodes).length;
    const completedEpisodes = Object.values(state.progress)
        .filter(p => p.completed).length;
    
    const percentage = totalEpisodes > 0 
        ? Math.round((completedEpisodes / totalEpisodes) * 100) 
        : 0;
    
    elements.progressSummary.querySelector('.progress-text').textContent = 
        `${completedEpisodes} / ${totalEpisodes} completed`;
    elements.progressFill.style.width = `${percentage}%`;
}

function updateNowPlaying(episode) {
    elements.episodeTitle.textContent = `Episode ${episode.sequenceNumber}: ${episode.title}`;
    elements.episodeDomain.textContent = episode.skillDomain || '';
    
    const topicsHtml = (episode.skillTopics || [])
        .map(topic => `<span class="topic-tag">${topic}</span>`)
        .join('');
    elements.episodeTopics.innerHTML = topicsHtml;
    
    elements.markComplete.checked = state.progress[episode.id]?.completed || false;
}

function updatePlayButton(isPlaying) {
    state.isPlaying = isPlaying;
    elements.btnPlay.querySelector('.icon-play').style.display = isPlaying ? 'none' : 'block';
    elements.btnPlay.querySelector('.icon-pause').style.display = isPlaying ? 'block' : 'none';
}

function updateProgress() {
    const current = elements.audioElement.currentTime;
    const duration = elements.audioElement.duration || 0;
    const percentage = duration > 0 ? (current / duration) * 100 : 0;
    
    elements.progressPlayed.style.width = `${percentage}%`;
    elements.progressHandle.style.left = `${percentage}%`;
    elements.timeCurrent.textContent = formatTime(current);
}

function updateDuration() {
    elements.timeTotal.textContent = formatTime(elements.audioElement.duration);
}

// ============================================
// Player Functions
// ============================================

function playEpisode(episode) {
    state.currentEpisode = episode;
    
    // Update UI
    updateNowPlaying(episode);
    renderEpisodeList(); // Re-render to show active state
    
    // Load audio
    const episodeNumber = String(episode.sequenceNumber).padStart(3, '0');
    const audioUrl = `${API_BASE}/audio/${state.certificationId}/${state.audioFormat}/${episodeNumber}`;
    
    elements.audioElement.src = audioUrl;
    elements.audioElement.load();
    
    // Resume from saved position if available
    const savedPosition = state.progress[episode.id]?.position || 0;
    if (savedPosition > 0) {
        elements.audioElement.currentTime = savedPosition;
    }
    
    // Auto-play
    elements.audioElement.play();
    
    // Load transcript
    loadTranscript(episodeNumber);
}

function togglePlay() {
    if (!state.currentEpisode) return;
    
    if (elements.audioElement.paused) {
        elements.audioElement.play();
    } else {
        elements.audioElement.pause();
        syncProgressToServer();
    }
}

function seek(seconds) {
    elements.audioElement.currentTime = Math.max(
        0,
        Math.min(
            elements.audioElement.duration,
            elements.audioElement.currentTime + seconds
        )
    );
}

function seekToPosition(event) {
    const rect = elements.progressTrack.getBoundingClientRect();
    const percentage = (event.clientX - rect.left) / rect.width;
    elements.audioElement.currentTime = percentage * elements.audioElement.duration;
}

function onEpisodeEnd() {
    // Mark as complete
    if (state.currentEpisode) {
        updateEpisodeProgress(state.currentEpisode.id, true);
    }
    
    // Auto-play next episode
    const currentNum = state.currentEpisode?.sequenceNumber;
    const nextEpisode = Object.values(state.episodes)
        .find(ep => ep.sequenceNumber === currentNum + 1);
    
    if (nextEpisode) {
        playEpisode(nextEpisode);
    }
}

function toggleTranscript() {
    elements.transcriptPanel.classList.toggle('visible');
}

// ============================================
// Progress Management
// ============================================

function updateEpisodeProgress(episodeId, completed) {
    state.progress[episodeId] = {
        ...state.progress[episodeId],
        completed,
        position: Math.floor(elements.audioElement.currentTime),
    };
    
    // Save to localStorage
    localStorage.setItem(STORAGE_KEY_PROGRESS, JSON.stringify(state.progress));
    
    // Update UI
    updateProgressSummary();
    renderEpisodeList();
    
    // Sync to server
    syncProgressToServer();
}

// ============================================
// Utilities
// ============================================

function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatDuration(seconds) {
    if (!seconds) return '';
    const mins = Math.floor(seconds / 60);
    return `${mins} min`;
}

// ============================================
// Start Application
// ============================================

document.addEventListener('DOMContentLoaded', init);
