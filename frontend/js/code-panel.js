/**
 * CodePanel — embedded Monaco Editor for source code preview.
 * Activated when user clicks "View Source" in detail panel.
 */
class CodePanel {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.editor = null;
        this._ready = false;
        this._loading = false;
        this._pending = [];
    }

    /** Load Monaco from CDN and initialize. */
    init() {
        if (this._ready || this._loading) return;
        if (typeof monaco === 'undefined') {
            this._loading = true;
            this._loadMonaco(() => {
                this._loading = false;
                this._createEditor();
            });
        } else {
            this._createEditor();
        }
    }

    _loadMonaco(cb) {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs/loader.js';
        script.onload = () => {
            require.config({
                paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs' }
            });
            require(['vs/editor/editor.main'], cb);
        };
        document.head.appendChild(script);
    }

    _createEditor() {
        if (this.editor) return;

        this.container.style.display = 'flex';
        this.container.style.flexDirection = 'column';

        // ── Toolbar ──
        const toolbar = document.createElement('div');
        toolbar.className = 'cp-toolbar';
        toolbar.innerHTML = `
            <button class="cp-back-btn" title="Back to graph view">← Back</button>
            <span class="cp-file-name" id="cp-filename"></span>
        `;
        toolbar.querySelector('.cp-back-btn').onclick = () => {
            if (window.codeKG && window.codeKG.switchView) {
                window.codeKG.switchView('force');
            }
        };
        this.container.appendChild(toolbar);

        // ── Editor area ──
        const el = document.createElement('div');
        el.style.flex = '1';
        el.style.width = '100%';
        this.container.appendChild(el);

        this.editor = monaco.editor.create(el, {
            value: '// Select a node and click "View Source"',
            language: 'plaintext',
            theme: 'vs-dark',
            readOnly: true,
            minimap: { enabled: true },
            scrollBeyondLastLine: false,
            automaticLayout: true,
            fontSize: 13,
            lineNumbers: 'on',
            renderWhitespace: 'selection',
        });
        this._ready = true;

        // drain pending
        for (const fn of this._pending) fn();
        this._pending = [];
    }

    /** Show source code in the editor. */
    showSource(code, language, filePath) {
        const lang = this._mapLanguage(filePath || '', language);
        // Update filename in toolbar
        const fnEl = document.getElementById('cp-filename');
        if (fnEl) fnEl.textContent = filePath || '';
        const act = () => {
            const model = this.editor.getModel();
            monaco.editor.setModelLanguage(model, lang);
            this.editor.setValue(code || '// (empty)');
        };
        if (!this._ready) {
            this._pending.push(act);
            this.init();
        } else {
            act();
        }
    }

    /** Clear the editor. */
    clear() {
        if (this.editor) {
            this.editor.setValue('// Select a node and click "View Source"');
        }
    }

    _mapLanguage(filePath, fallback) {
        const ext = (filePath || '').split('.').pop().toLowerCase();
        const map = {
            js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
            py: 'python', rs: 'rust', go: 'go', cpp: 'cpp', c: 'c', h: 'c',
            java: 'java', rb: 'ruby', php: 'php', swift: 'swift', kt: 'kotlin',
            scala: 'scala', cs: 'csharp', sql: 'sql', sh: 'shell', bash: 'shell',
            yaml: 'yaml', yml: 'yaml', json: 'json', xml: 'xml', html: 'html',
            css: 'css', scss: 'scss', md: 'markdown', toml: 'ini', cfg: 'ini',
        };
        return map[ext] || fallback || 'plaintext';
    }
}
