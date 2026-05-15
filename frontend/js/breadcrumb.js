/**
 * Breadcrumb navigation — shows "where you came from, where you are now".
 * Tracks: view selection → node selection → search terms.
 */
class Breadcrumb {
    constructor() {
        this.el = document.getElementById('breadcrumb');
        this._crumbs = [{ label: 'CodeKG', action: 'root' }];
        this._render();
    }

    /** Go to a specific view */
    pushView(viewName) {
        this._crumbs = this._crumbs.filter(c => c.action !== 'view');
        this._crumbs.push({ label: this._viewLabel(viewName), action: 'view', data: viewName });
        this._render();
    }

    /** Select a specific node */
    pushNode(nodeLabel, nodeId) {
        this._crumbs = this._crumbs.filter(c => c.action !== 'node');
        this._crumbs.push({ label: nodeLabel, action: 'node', data: nodeId });
        this._render();
    }

    /** Record a search */
    pushSearch(query) {
        this._crumbs.push({ label: `Search: "${query.substring(0, 20)}"`, action: 'search', data: query });
        this._render();
    }

    /** Go back to a specific crumb */
    popTo(index) {
        this._crumbs = this._crumbs.slice(0, index + 1);
        this._render();
        return this._crumbs[index];
    }

    _viewLabel(name) {
        const map = {
            force: '🔵 Force Graph', tree: '🌳 Tree View', matrix: '📊 Matrix',
            sunburst: '☀️ Sunburst', codecity: '🏙️ Code City', metro: '🚇 Metro Map',
            codepanel: '📝 Code Panel', 'agent-trace': '🤖 Agent Trace', diff: '📊 Diff Analysis',
        };
        return map[name] || name;
    }

    _render() {
        this.el.innerHTML = this._crumbs.map((c, i) => {
            const isLast = i === this._crumbs.length - 1;
            const sep = i > 0 ? '<span class="bc-sep">›</span>' : '';
            const cls = `bc-item${isLast ? ' bc-current' : ''}`;
            return `${sep}<span class="${cls}" data-idx="${i}">${c.label}</span>`;
        }).join('');

        // Click handlers
        const self = this;
        this.el.querySelectorAll('.bc-item').forEach(el => {
            el.addEventListener('click', () => {
                const idx = parseInt(el.dataset.idx);
                if (idx !== this._crumbs.length - 1) {
                    const crumb = this.popTo(idx);
                    if (crumb.action === 'view' && window.codeKG) {
                        window.codeKG.switchView(crumb.data);
                    }
                }
            });
        });
    }
}

// Global breadcrumb instance
window.breadcrumb = new Breadcrumb();
