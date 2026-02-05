/**
 * Study Partner - AI Chat Module
 * Interactive exam prep assistant using Azure OpenAI
 */

// Configuration
const API_BASE = '/api';
const MAX_MESSAGE_LENGTH = 2000;

// State
let state = {
    certificationId: 'ai-102',
    messages: [],
    isLoading: false,
};

// DOM Elements
const elements = {
    certSelect: document.getElementById('certSelect'),
    chatWelcome: document.getElementById('chatWelcome'),
    chatMessages: document.getElementById('chatMessages'),
    chatForm: document.getElementById('chatForm'),
    chatInput: document.getElementById('chatInput'),
    btnSend: document.getElementById('btnSend'),
    charCount: document.getElementById('charCount'),
    honeypot: document.getElementById('honeypot'),
};

// =============================================================================
// INITIALIZATION
// =============================================================================

async function init() {
    await loadCertifications();
    await loadConfig();
    setupEventListeners();
    restoreState();
}

async function loadConfig() {
    // Load rate limit config from server
    try {
        const response = await fetch(`${API_BASE}/chat/config`);
        if (response.ok) {
            const config = await response.json();
            state.rateLimitPerHour = config.rateLimitPerHour || 50;
        }
    } catch (e) {
        // Config endpoint not available - use defaults
        console.debug('Config not loaded');
    }
}

function setupEventListeners() {
    // Certification selector
    elements.certSelect.addEventListener('change', handleCertChange);
    
    // Chat form
    elements.chatForm.addEventListener('submit', handleSubmit);
    
    // Auto-resize textarea
    elements.chatInput.addEventListener('input', handleInputChange);
    
    // Enter to send (Shift+Enter for newline)
    elements.chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            elements.chatForm.dispatchEvent(new Event('submit'));
        }
    });
    
    // Suggestion chips
    document.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const prompt = chip.dataset.prompt;
            if (prompt) {
                elements.chatInput.value = prompt;
                handleInputChange();
                elements.chatForm.dispatchEvent(new Event('submit'));
            }
        });
    });
}

function restoreState() {
    // Restore last selected certification
    const lastCert = localStorage.getItem('certaudio_study_cert');
    if (lastCert && elements.certSelect.querySelector(`option[value="${lastCert}"]`)) {
        elements.certSelect.value = lastCert;
        state.certificationId = lastCert;
    }
    
    // Restore chat history for this certification
    const history = localStorage.getItem(`certaudio_chat_${state.certificationId}`);
    if (history) {
        try {
            state.messages = JSON.parse(history);
            if (state.messages.length > 0) {
                showChatMessages();
                state.messages.forEach(msg => renderMessage(msg, false));
            }
        } catch (e) {
            console.error('Failed to restore chat history:', e);
        }
    }
}

function saveState() {
    localStorage.setItem('certaudio_study_cert', state.certificationId);
    localStorage.setItem(`certaudio_chat_${state.certificationId}`, JSON.stringify(state.messages));
}

// =============================================================================
// CERTIFICATIONS
// =============================================================================

async function loadCertifications() {
    try {
        const response = await fetch(`${API_BASE}/certifications`);
        if (!response.ok) throw new Error('Failed to load certifications');
        
        const data = await response.json();
        const certifications = data.certifications || [];
        
        if (certifications.length === 0) {
            // Use fallback list
            const fallback = [
                { id: 'ai-102', name: 'AI-102: Azure AI Engineer' },
                { id: 'az-204', name: 'AZ-204: Azure Developer' },
                { id: 'az-104', name: 'AZ-104: Azure Administrator' },
                { id: 'az-900', name: 'AZ-900: Azure Fundamentals' },
            ];
            populateCertSelect(fallback);
        } else {
            populateCertSelect(certifications);
        }
    } catch (error) {
        console.error('Error loading certifications:', error);
        // Use fallback
        populateCertSelect([
            { id: 'ai-102', name: 'AI-102: Azure AI Engineer' },
            { id: 'az-204', name: 'AZ-204: Azure Developer' },
        ]);
    }
}

function populateCertSelect(certifications) {
    elements.certSelect.innerHTML = certifications
        .map(c => `<option value="${c.id}">${c.name}</option>`)
        .join('');
    
    // Restore selection if available
    const lastCert = localStorage.getItem('certaudio_study_cert');
    if (lastCert && elements.certSelect.querySelector(`option[value="${lastCert}"]`)) {
        elements.certSelect.value = lastCert;
        state.certificationId = lastCert;
    } else if (certifications.length > 0) {
        state.certificationId = certifications[0].id;
    }
}

function handleCertChange() {
    const newCert = elements.certSelect.value;
    if (newCert !== state.certificationId) {
        // Save current chat before switching
        saveState();
        
        // Switch certification
        state.certificationId = newCert;
        state.messages = [];
        
        // Clear chat UI
        elements.chatMessages.innerHTML = '';
        elements.chatWelcome.classList.remove('hidden');
        elements.chatMessages.classList.remove('active');
        
        // Try to restore history for new cert
        const history = localStorage.getItem(`certaudio_chat_${newCert}`);
        if (history) {
            try {
                state.messages = JSON.parse(history);
                if (state.messages.length > 0) {
                    showChatMessages();
                    state.messages.forEach(msg => renderMessage(msg, false));
                }
            } catch (e) {
                console.error('Failed to restore chat history:', e);
            }
        }
        
        saveState();
    }
}

// =============================================================================
// CHAT
// =============================================================================

function handleInputChange() {
    const input = elements.chatInput;
    const length = input.value.length;
    
    // Update character count
    elements.charCount.textContent = `${length}/${MAX_MESSAGE_LENGTH}`;
    
    // Auto-resize
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
    
    // Enable/disable send button
    elements.btnSend.disabled = state.isLoading || length === 0 || length > MAX_MESSAGE_LENGTH;
}

async function handleSubmit(e) {
    e.preventDefault();
    
    const message = elements.chatInput.value.trim();
    if (!message || state.isLoading) return;
    
    // Clear input
    elements.chatInput.value = '';
    handleInputChange();
    
    // Show chat area
    showChatMessages();
    
    // Add user message
    const userMessage = {
        role: 'user',
        content: message,
        timestamp: new Date().toISOString(),
    };
    state.messages.push(userMessage);
    renderMessage(userMessage);
    
    // Show typing indicator
    state.isLoading = true;
    elements.btnSend.disabled = true;
    const typingEl = showTypingIndicator();
    
    try {
        // Call chat API
        const requestBody = {
            certificationId: state.certificationId,
            message: message,
            history: state.messages.slice(-10).map(m => ({
                role: m.role,
                content: m.content,
            })),
        };
        
        // Include honeypot field (should be empty for real users)
        if (elements.honeypot) {
            requestBody.hp = elements.honeypot.value;
        }
        
        const response = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
        });
        
        // Remove typing indicator
        typingEl.remove();
        
        const data = await response.json().catch(() => ({}));
        
        // Check if Study Partner is not deployed
        if (data.not_deployed) {
            showNotDeployed();
            // Remove the user message we just added
            state.messages.pop();
            return;
        }
        
        // Check if rate limited
        if (data.rate_limited) {
            showRateLimited(data.rateLimit);
            // Remove the user message we just added
            state.messages.pop();
            return;
        }
        
        // Check if verification failed (honeypot triggered)
        if (data.verification_failed) {
            renderError('Request blocked. Please try again.');
            state.messages.pop();
            return;
        }
        
        if (!response.ok) {
            throw new Error(data.error || 'Failed to get response');
        }
        
        // Update rate limit display
        if (data.rateLimit) {
            updateRateLimitDisplay(data.rateLimit);
        }
        
        // Add assistant message
        const assistantMessage = {
            role: 'assistant',
            content: data.response,
            timestamp: new Date().toISOString(),
        };
        state.messages.push(assistantMessage);
        renderMessage(assistantMessage);
        
    } catch (error) {
        console.error('Chat error:', error);
        typingEl.remove();
        renderError(error.message || 'Something went wrong. Please try again.');
    } finally {
        state.isLoading = false;
        handleInputChange();
        saveState();
    }
}

function showChatMessages() {
    elements.chatWelcome.classList.add('hidden');
    elements.chatMessages.classList.add('active');
}

function renderMessage(message, scroll = true) {
    const isUser = message.role === 'user';
    const time = new Date(message.timestamp).toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
    
    const html = `
        <div class="message ${message.role}">
            <div class="message-avatar">
                ${isUser ? getUserIcon() : getAssistantIcon()}
            </div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-name">${isUser ? 'You' : 'Study Partner'}</span>
                    <span class="message-time">${time}</span>
                </div>
                <div class="message-text">${formatMessage(message.content)}</div>
            </div>
        </div>
    `;
    
    elements.chatMessages.insertAdjacentHTML('beforeend', html);
    
    if (scroll) {
        elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    }
}

function showTypingIndicator() {
    const html = `
        <div class="message assistant typing-message">
            <div class="message-avatar">
                ${getAssistantIcon()}
            </div>
            <div class="message-content">
                <div class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        </div>
    `;
    
    elements.chatMessages.insertAdjacentHTML('beforeend', html);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    
    return elements.chatMessages.querySelector('.typing-message');
}

function renderError(message) {
    const html = `
        <div class="message-error">
            ${escapeHtml(message)}
        </div>
    `;
    
    elements.chatMessages.insertAdjacentHTML('beforeend', html);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

function showNotDeployed() {
    // Hide chat UI, show not-deployed message
    elements.chatMessages.classList.remove('active');
    elements.chatWelcome.classList.remove('hidden');
    
    // Update welcome content to show not-deployed state
    elements.chatWelcome.innerHTML = `
        <div class="welcome-icon not-deployed-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
        </div>
        <h1>Study Partner Not Available</h1>
        <p>The Study Partner feature requires AI Search and AI Foundry to be deployed. Contact your administrator to enable this feature.</p>
        <div class="not-deployed-details">
            <p>To enable Study Partner, deploy with <code>enableStudyPartner=true</code></p>
            <p class="cost-note">Note: This adds ~$75+/month for Azure AI Search + AI Foundry agent</p>
        </div>
        <a href="index.html" class="btn-back-to-player">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                <polygon points="5 3 19 12 5 21 5 3"></polygon>
            </svg>
            Back to Audio Player
        </a>
    `;
    
    // Disable input
    elements.chatInput.disabled = true;
    elements.btnSend.disabled = true;
    elements.chatInput.placeholder = 'Study Partner is not available';
}

function showRateLimited(rateLimit) {
    // Show rate limit error message in chat
    const resetMinutes = rateLimit?.resetMinutes || 60;
    const html = `
        <div class="message-error rate-limit-error">
            <div class="rate-limit-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                    <circle cx="12" cy="12" r="10"></circle>
                    <polyline points="12 6 12 12 16 14"></polyline>
                </svg>
            </div>
            <div>
                <strong>Rate limit reached</strong>
                <p>You've reached the hourly limit of ${rateLimit?.limit || 50} questions. Please wait ${resetMinutes} minute${resetMinutes !== 1 ? 's' : ''} before trying again.</p>
            </div>
        </div>
    `;
    
    showChatMessages();
    elements.chatMessages.insertAdjacentHTML('beforeend', html);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    
    // Update the rate limit display
    updateRateLimitDisplay({ remaining: 0, resetMinutes, limit: rateLimit?.limit || 50 });
}

function updateRateLimitDisplay(rateLimit) {
    // Update or create rate limit indicator
    let indicator = document.getElementById('rateLimitIndicator');
    
    if (!indicator) {
        // Create the indicator element
        const inputHint = document.querySelector('.input-hint');
        if (inputHint) {
            indicator = document.createElement('span');
            indicator.id = 'rateLimitIndicator';
            indicator.className = 'rate-limit-indicator';
            inputHint.insertBefore(indicator, inputHint.firstChild);
        }
    }
    
    if (indicator && rateLimit) {
        const remaining = rateLimit.remaining;
        const limit = rateLimit.limit || 50;
        
        if (remaining <= 10) {
            indicator.className = 'rate-limit-indicator warning';
        } else {
            indicator.className = 'rate-limit-indicator';
        }
        
        indicator.textContent = `${remaining}/${limit} questions remaining`;
        indicator.title = `Rate limit resets in approximately ${rateLimit.resetMinutes} minutes`;
    }
}

// =============================================================================
// UTILITIES
// =============================================================================

function formatMessage(text) {
    // Basic markdown-like formatting
    let html = escapeHtml(text);
    
    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // Italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    
    // Lists (basic)
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/g, '<ul>$1</ul>');
    html = html.replace(/<\/ul>\s*<ul>/g, '');
    
    // Numbered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    
    // Paragraphs
    html = html.split('\n\n').map(p => {
        if (p.startsWith('<pre>') || p.startsWith('<ul>') || p.startsWith('<li>')) {
            return p;
        }
        return `<p>${p.replace(/\n/g, '<br>')}</p>`;
    }).join('');
    
    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function getUserIcon() {
    return `<svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
        <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
    </svg>`;
}

function getAssistantIcon() {
    return `<svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
    </svg>`;
}

// =============================================================================
// START
// =============================================================================

document.addEventListener('DOMContentLoaded', init);
