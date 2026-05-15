/**
 * Agent Trace View — records and displays analysis/search/exploration history.
 *
 * Each "trace" is a timestamped event:
 *   - analysis started/completed (repo, stats)
 *   - search executed (query, results count)
 *   - node explored (node id, file, line)
 *   - view switched (from → to)
 */
class AgentTraceView {
    constructor() {
        this._traces = [];
        this._containerId = 'agent-trace-content';
        this._initFromStorage();
    }

    _initFromStorage() {
        try {
            const saved = localStorage.getItem('codekg_agent_traces');
            if (saved) this._traces = JSON.parse(saved);
        } catch (_) {}
    }

    _save() {
        try {
            // Keep last 500 traces max
            if (this._traces.length > 500) {
                this._traces = this._traces.slice(-500);
            }
            localStorage.setItem('codekg_agent_traces', JSON.stringify(this._traces));
        } catch (_) {}
    }

    /** Record a new trace event */
    trace(type, data) {
        this._traces.push({
            ts: new Date().toISOString(),
            type,
            ...data,
        });
        this._save();
    }

    /** Render the trace log */
    render() {
        const el = document.getElementById(this._containerId);
        if (!el) return;

        if (this._traces.length === 0) {
            el.innerHTML = '<div style="color:#8b949e;font-style:italic;padding:20px;">No traces yet. Analyze a repo, search, or explore nodes to see the agent trace.</div>';
            return;
        }

        const icons = {
            analyze_start: '🔍', analyze_done: '✅',
            search: '🔎', node_explore: '📌',
            view_switch: '👁️', error: '❌',
        };

        el.innerHTML = this._traces.slice().reverse().map(t => {
            const icon = icons[t.type] || '•';
            const time = t.ts ? new Date(t.ts).toLocaleTimeString() : '';
            let detail = '';

            switch (t.type) {
                case 'analyze_start':
                    detail = `Analyzing: <strong>${t.repo || '?'}</strong>`;
                    break;
                case 'analyze_done':
                    detail = `Analysis complete — ${t.nodes || 0} nodes, ${t.edges || 0} edges`;
                    break;
                case 'search':
                    detail = `Search: <code>${t.query || ''}</code> → ${t.results || 0} results (${t.latency || '?'}ms)`;
                    break;
                case 'node_explore':
                    detail = `Explored <strong>${t.label || '?'}</strong> @ ${t.file || ''}:${t.line || ''}`;
                    break;
                case 'view_switch':
                    detail = `Switched view: ${t.from || '?'} → ${t.to || '?'}`;
                    break;
                case 'error':
                    detail = `Error: <span style="color:#f85149;">${t.message || ''}</span>`;
                    break;
                default:
                    detail = JSON.stringify(t);
            }

            return `
                <div class="trace-entry">
                    <span style="color:#8b949e;">${time}</span>
                    <span style="margin:0 6px;">${icon}</span>
                    ${detail}
                </div>`;
        }).join('');
    }

    clear() {
        this._traces = [];
        this._save();
        this.render();
    }
}

// Global instance
window.agentTrace = new AgentTraceView();
