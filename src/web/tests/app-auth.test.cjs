const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../js/app.js'), 'utf8');

function player(respond) {
    const nodes = new Map();
    const calls = [];
    const storage = new Map();
    function element() {
        return {
            hidden: false, disabled: false, innerHTML: '', textContent: '',
            style: {}, value: '', paused: true,
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener() {}, appendChild() {}, removeAttribute() {},
            querySelector: () => element(),
            pause() { this.paused = true; },
            load() {},
        };
    }
    const context = vm.createContext({
        console,
        document: {
            getElementById(id) {
                if (!nodes.has(id)) nodes.set(id, element());
                return nodes.get(id);
            },
            addEventListener() {},
            createElement: element,
            querySelectorAll: () => [],
            querySelector: () => element(),
            body: { style: {} },
        },
        localStorage: {
            getItem: key => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, value),
        },
        window: { innerWidth: 1200, location: { pathname: '/' } },
        fetch: async (url, options) => {
            calls.push(url);
            return { ok: true, status: 200, redirected: false, ...respond(url, options) };
        },
    });
    vm.runInContext(source, context);
    return { nodes, calls, run: code => vm.runInContext(code, context) };
}

function signedInResponse(url) {
    if (url === '/.auth/me') {
        return { json: async () => ({ clientPrincipal: { userId: 'user-1' } }) };
    }
    if (url.endsWith('/certifications')) {
        return { json: async () => ({ certifications: [{ id: 'ai-102', name: 'AI' }] }) };
    }
    if (url.includes('/episodes/')) {
        return { json: async () => ({ domains: {}, totalHours: 0 }) };
    }
    return { json: async () => ({ progress: {}, isAdmin: false }) };
}

test('anonymous visitors see a sign-in gate without any API requests', async () => {
    const app = player(() => ({ json: async () => ({ clientPrincipal: null }) }));
    await app.run('init()');
    assert.deepEqual(app.calls, ['/.auth/me']);
    assert.equal(app.nodes.get('signinGate').hidden, false);
    assert.equal(app.nodes.get('audioPlayer').hidden, true);
    assert.equal(app.nodes.get('certSelect').disabled, true);
    assert.equal(app.nodes.get('formatSelect').disabled, true);
    assert.match(app.nodes.get('episodeList').innerHTML, /Sign in/);
    await app.run('loadCertifications(); loadEpisodes();');
    assert.deepEqual(app.calls, ['/.auth/me']);
});

test('signed-in visitors load certifications and episodes', async () => {
    const app = player(signedInResponse);
    await app.run('init()');
    assert.ok(app.calls.includes('/api/certifications'));
    assert.ok(app.calls.includes('/api/episodes/ai-102/instructional'));
    assert.equal(app.nodes.get('signinGate').hidden, true);
    assert.equal(app.nodes.get('certSelect').disabled, false);
    assert.equal(app.nodes.get('formatSelect').disabled, false);
});

for (const endpoint of ['certifications', 'episodes']) {
    for (const status of [401, 403, 302, 0]) {
        test(`${endpoint} auth failure (${status}) shows gate without parsing login HTML`, async () => {
            let parsed = false;
            const app = player((url, options) => {
                if (url.includes(`/api/${endpoint}`)) {
                    assert.equal(options.redirect, 'manual');
                    return {
                        ok: status === 302,
                        status: status === 302 ? 200 : status,
                        type: status === 0 ? 'opaqueredirect' : 'basic',
                        redirected: status === 302,
                        json: async () => { parsed = true; throw new Error('Login HTML'); },
                    };
                }
                return signedInResponse(url);
            });
            await app.run('init()');
            assert.equal(parsed, false);
            assert.equal(app.run('state.isAuthenticated'), false);
            assert.equal(app.nodes.get('signinGate').hidden, false);
            assert.equal(app.nodes.get('certSelect').disabled, true);
            assert.equal(app.nodes.get('audioPlayer').hidden, true);
            if (endpoint === 'certifications') {
                assert.equal(app.calls.some(url => url.includes('/episodes/')), false);
            }
        });
    }
}
