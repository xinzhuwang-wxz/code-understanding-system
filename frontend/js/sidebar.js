/**
 * Sidebar controls: node/edge type filters, search, display options, stats.
 */
class SidebarController {
    constructor(graphRenderer) {
        this.graph = graphRenderer;
        this.graphData = null;
        this._onViewChange = null;

        this.nodeTypesSection = document.getElementById("node-types-section");
        this.edgeTypesSection = document.getElementById("edge-types-section");
        this.displaySection = document.getElementById("display-options-section");
        this.statsSection = document.getElementById("stats-section");
        this.viewSelectorSection = document.getElementById("view-selector-section");

        this.nodeTypesList = document.getElementById("node-types-list");
        this.edgeTypesList = document.getElementById("edge-types-list");
        this.statsContent = document.getElementById("stats-content");
        this.searchInput = document.getElementById("search-input");

        this._initDisplayToggles();
        this._initSearch();
        this._initViewSelector();
    }

    onViewChange(fn) {
        this._onViewChange = fn;
    }

    _initViewSelector() {
        const selector = document.getElementById("view-selector");
        if (!selector) return;
        selector.addEventListener("change", () => {
            if (this._onViewChange) {
                this._onViewChange(selector.value);
            }
        });
    }

    populate(graphData) {
        this.graphData = graphData;
        this._buildNodeTypeFilters(graphData);
        this._buildEdgeTypeFilters(graphData);
        this._buildStats(graphData);

        this.nodeTypesSection.style.display = "";
        this.edgeTypesSection.style.display = "";
        this.displaySection.style.display = "";
        this.statsSection.style.display = "";
        if (this.viewSelectorSection) this.viewSelectorSection.style.display = "";
    }

    _buildNodeTypeFilters(data) {
        const counts = data.node_type_counts || {};
        const colors = data.node_colors || {};
        this.nodeTypesList.innerHTML = "";

        const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
        for (const [type, count] of sorted) {
            const color = colors[type] || "#78909c";
            const row = this._createFilterRow(type, color, count, true, (checked) => {
                this.graph.setNodeTypeVisibility(type, checked);
            });
            this.nodeTypesList.appendChild(row);
        }
    }

    _buildEdgeTypeFilters(data) {
        const counts = data.edge_type_counts || {};
        const colors = data.edge_colors || {};
        this.edgeTypesList.innerHTML = "";

        const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
        for (const [type, count] of sorted) {
            const color = colors[type] || "#90a4ae";
            const label = this._edgeTypeLabel(type);
            const row = this._createFilterRow(label, color, count, true, (checked) => {
                this.graph.setEdgeTypeVisibility(type, checked);
            });
            this.edgeTypesList.appendChild(row);
        }
    }

    _edgeTypeLabel(raw) {
        const map = {
            calls: "calls →", imports: "imports →", contains: "contains →",
            FileContains: "file contains →", Contains: "class contains →",
            inherits: "inherits →", invokes: "invokes →", references: "references →",
            decorates: "decorates →", handles: "handles →", depends_on: "depends on →",
            reads: "reads →", writes: "writes →", extends: "extends →",
            implements: "implements →", data_flows_to: "flows to →",
        };
        return map[raw] || raw;
    }

    _createFilterRow(label, color, count, checked, onChange) {
        const row = document.createElement("label");
        row.className = "filter-row";
        row.setAttribute("aria-label", `Toggle ${label} visibility`);

        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = checked;
        cb.setAttribute("aria-label", `${label} filter`);
        cb.addEventListener("change", () => onChange(cb.checked));

        const swatch = document.createElement("span");
        swatch.className = "filter-swatch";
        swatch.style.background = color;
        swatch.style.boxShadow = `0 0 6px ${color}`;

        const labelEl = document.createElement("span");
        labelEl.className = "filter-label";
        labelEl.textContent = label;

        const countEl = document.createElement("span");
        countEl.className = "filter-count";
        countEl.textContent = count;

        row.appendChild(cb);
        row.appendChild(swatch);
        row.appendChild(labelEl);
        row.appendChild(countEl);
        return row;
    }

    _buildStats(data) {
        const stats = data.stats || {};
        const nodeTypes = Object.keys(data.node_type_counts || {}).length;
        const edgeTypes = Object.keys(data.edge_type_counts || {}).length;

        this.statsContent.innerHTML = `
            <div>Nodes: <span class="stat-value">${stats.total_nodes || 0}</span></div>
            <div>Edges: <span class="stat-value">${stats.total_edges || 0}</span></div>
            <div>Node types: <span class="stat-value">${nodeTypes}</span></div>
            <div>Edge types: <span class="stat-value">${edgeTypes}</span></div>
        `;
    }

    _initDisplayToggles() {
        document.getElementById("toggle-labels").addEventListener("change", (e) => {
            this.graph.setShowLabels(e.target.checked);
        });
        document.getElementById("toggle-arrows").addEventListener("change", (e) => {
            this.graph.setShowArrows(e.target.checked);
        });
        document.getElementById("toggle-freeze").addEventListener("change", (e) => {
            this.graph.setFrozen(e.target.checked);
        });
    }

    _initSearch() {
        let debounce = null;
        let apiDebounce = null;

        const searchBtn = document.getElementById("search-btn");

        // On typing: show local matches in graph (fast feedback)
        this.searchInput.addEventListener("input", () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => this._performLocalSearch(), 150);

            clearTimeout(apiDebounce);
            apiDebounce = setTimeout(() => this._performSearchAPI(), 800);
        });

        // On Enter or search button: immediate backend search
        this.searchInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                clearTimeout(debounce);
                clearTimeout(apiDebounce);
                this._performSearchAPI();
            }
        });

        if (searchBtn) {
            searchBtn.addEventListener("click", () => {
                clearTimeout(debounce);
                clearTimeout(apiDebounce);
                this._performSearchAPI();
            });
        }

        // AI Ask
        const aiAskInput = document.getElementById("ai-ask-input");
        const aiAskBtn = document.getElementById("ai-ask-btn");
        if (aiAskBtn && aiAskInput) {
            aiAskBtn.addEventListener("click", () => this._performAIAsk());
            aiAskInput.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && e.metaKey) {
                    e.preventDefault();
                    this._performAIAsk();
                }
            });
        }
    }

    _performLocalSearch() {
        const query = this.searchInput.value.trim().toLowerCase();
        if (!query || !this.graphData) {
            this.graph.setSearchMatches([]);
            return;
        }
        // Fast client-side highlight in the force graph
        const matches = [];
        for (const n of this.graphData.nodes) {
            const label = (n.label || "").toLowerCase();
            const path = (n.file_path || "").toLowerCase();
            if (label.includes(query) || path.includes(query)) {
                matches.push(n.id);
            }
        }
        this.graph.setSearchMatches(matches);
    }

    async _performSearchAPI() {
        const query = this.searchInput.value.trim();
        if (!query) return;

        // Agent trace: search event
        if (window.agentTrace) {
            window.agentTrace.trace('search', { query: query.substring(0, 60) });
        }

        // Breadcrumb: search event
        if (window.breadcrumb) {
            window.breadcrumb.pushSearch(query);
        }

        try {
            const resp = await fetch("/api/search", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query, node_type: "", max_results: 20 }),
            });
            const data = await resp.json();

            // Also highlight matches in force graph
            if (data.results && this.graph) {
                const matchIds = data.results.map(r => r.node_id);
                this.graph.setSearchMatches(matchIds);
            }

            // Agent trace: search complete
            if (window.agentTrace) {
                window.agentTrace.trace('search', {
                    query: query.substring(0, 60),
                    results: data.total || 0,
                    latency: data.latency_ms,
                });
            }

            if (data.results && data.results.length > 0) {
                this._showSearchResults(data);
            }
        } catch (err) {
            console.warn("Search API error:", err);
            if (window.agentTrace) {
                window.agentTrace.trace('error', { message: 'Search API: ' + err.message });
            }
        }
    }

    _showSearchResults(data) {
        const panel = document.getElementById("detail-panel");
        const header = panel.querySelector(".dp-header");
        const body = panel.querySelector(".dp-body");

        const layers = data.layers || data.layers_consulted || [];
        const escalation = data.escalation || data.escalation_path || [];
        const latency = data.latency_ms || data.total_latency_ms || 0;

        header.innerHTML = `
            <h2 style="font-size:14px;margin:0 0 4px;">Search: "${this._esc(data.query)}"</h2>
            <p class="dp-meta">
                ${data.total || 0} results · ${latency.toFixed(0)}ms
                ${layers.length ? ' · ' + layers.join(' → ') : ''}
                ${escalation.length ? '<br><small>' + escalation.join(', ') + '</small>' : ''}
            </p>`;

        let html = '<ul class="search-results" style="list-style:none;padding:0;margin:0;">';
        for (const r of data.results) {
            const typeColor = this._nodeColor(r.type);
            html += `
                <li class="search-result-item"
                    onclick="window.codeKG.showNode('${this._esc(r.node_id)}')">
                    <div style="display:flex;align-items:center;gap:6px;">
                        <span class="sr-type-badge" style="background:${typeColor};">${r.type || '?'}</span>
                        <strong class="sr-name">${this._esc(r.label)}</strong>
                        ${r.score ? `<span class="sr-score">${r.score.toFixed(3)}</span>` : ''}
                    </div>
                    <div class="sr-location">
                        ${this._esc(r.file_path || '')}:${r.line_number || 0}
                        <span class="sr-source">${r.source || ''}</span>
                    </div>
                    ${r.docstring ? `<div class="sr-docstring">${this._esc(r.docstring).substring(0, 150)}</div>` : ''}
                </li>`;
        }
        html += "</ul>";
        body.innerHTML = html;

        panel.removeAttribute("aria-hidden");
        panel.style.display = "";
    }

    _esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

    _nodeColor(type) {
        const map = { function: '#58a6ff', class: '#f78166', module: '#8b949e',
            file: '#6e7681', method: '#a5d6ff', variable: '#d2a8ff', config: '#ffa657' };
        return map[type] || '#58a6ff';
    }

    async _performAIAsk() {
        const question = document.getElementById("ai-ask-input")?.value?.trim();
        const answerEl = document.getElementById("ai-answer");
        if (!question || !answerEl) return;

        answerEl.style.display = "";
        answerEl.innerHTML = '<span style="color:#58a6ff;">✨ Thinking...</span>';

        try {
            const resp = await fetch("/api/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question, max_context: 5 }),
            });
            const data = await resp.json();

            let html = `<div style="margin-bottom:8px;color:#58a6ff;font-weight:600;">🤖 AI Response</div>`;
            html += `<div style="margin-bottom:12px;line-height:1.6;">${data.answer || 'No answer available.'}</div>`;

            if (data.references && data.references.length > 0) {
                html += `<div style="margin-bottom:4px;color:#8b949e;font-size:10px;">📎 Related code (${data.references.length}):</div>`;
                for (const r of data.references.slice(0, 5)) {
                    html += `<div class="ai-ref" style="font-size:10px;padding:2px 0;color:#6e7681;cursor:pointer;"`;
                    html += ` onclick="window.codeKG.showNode('${this._esc(r.node_id)}')">`;
                    html += `${this._esc(r.label)} <span style="color:#484f58;">@ ${this._esc(r.file_path)}:${r.line_number}</span>`;
                    html += r.score ? ` <span style="color:#58a6ff;font-size:9px;">${r.score.toFixed(2)}</span>` : '';
                    html += `</div>`;
                }
            }

            answerEl.innerHTML = html;

            if (window.agentTrace) {
                window.agentTrace.trace('ai_ask', { question, references: data.references?.length || 0 });
            }
            if (window.breadcrumb) {
                window.breadcrumb.pushSearch(`AI: ${question.substring(0, 40)}`);
            }
        } catch (err) {
            answerEl.innerHTML = `<div style="color:#f85149;">AI Ask failed: ${err.message}</div>`;
        }
    }
}
