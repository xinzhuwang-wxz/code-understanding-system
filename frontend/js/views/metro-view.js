/**
 * MetroMap — SVG-based module data-flow visualization with click interaction.
 * Click a station → highlights connected stations + flow lines, dims others.
 */
class MetroMapView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.svg = null;
        this._data = null;
        this._stationById = new Map();
        this._flows = [];
        this._selectedStation = null;
    }

    init() {
        if (this.svg) return;
        const W = this.container.clientWidth;
        const H = this.container.clientHeight;

        this.svg = d3.select(this.container)
            .append('svg')
            .attr('width', W)
            .attr('height', H)
            .style('background', '#0d1117');

        // Defs: glow filter
        const defs = this.svg.append('defs');
        const filter = defs.append('filter').attr('id', 'metro-glow');
        filter.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'blur');
        const merge = filter.append('feMerge');
        merge.append('feMergeNode').attr('in', 'blur');
        merge.append('feMergeNode').attr('in', 'SourceGraphic');

        // Stronger glow for selected station
        const selFilter = defs.append('filter').attr('id', 'metro-glow-strong');
        selFilter.append('feGaussianBlur').attr('stdDeviation', '6').attr('result', 'blur');
        const selMerge = selFilter.append('feMerge');
        selMerge.append('feMergeNode').attr('in', 'blur');
        selMerge.append('feMergeNode').attr('in', 'SourceGraphic');

        this.g = this.svg.append('g');
        this.lineG = this.g.append('g').attr('class', 'metro-lines');
        this.stationG = this.g.append('g').attr('class', 'metro-stations');
        this.labelG = this.g.append('g').attr('class', 'metro-labels');

        // Zoom
        this.svg.call(d3.zoom()
            .scaleExtent([0.3, 3])
            .on('zoom', (e) => this.g.attr('transform', e.transform)));

        // Click on background to deselect
        this.svg.on('click', (e) => {
            if (e.target === this.svg.node()) {
                this._selectStation(null);
            }
        });
    }

    setData(nodes, edges) {
        this._data = { nodes, edges };
        if (!this.svg) this.init();
        this._render();
    }

    _render() {
        if (!this._data) return;
        const W = this.container.clientWidth;
        const H = this.container.clientHeight;
        this.svg.attr('width', W).attr('height', H);

        // Build metro stations from modules
        const modules = new Map();
        const dataFlows = [];

        this._data.nodes.forEach(n => {
            const mod = this._extractModule(n);
            if (!modules.has(mod)) {
                modules.set(mod, { id: mod, name: mod, size: 1, lang: n.type, nodes: [n] });
            } else {
                const m = modules.get(mod);
                m.size++;
                if (m.nodes.length < 5) m.nodes.push(n); // Keep first 5 nodes for detail
            }
        });

        this._data.edges.forEach(e => {
            const sMod = this._extractModule({ file_path: e.source });
            const tMod = this._extractModule({ file_path: e.target });
            if (sMod !== tMod) {
                dataFlows.push({ source: sMod, target: tMod, type: e.type });
            }
        });

        const stationList = Array.from(modules.values());
        const lineColors = d3.scaleOrdinal(d3.schemeCategory10);

        // Build adjacency for flow grouping
        const flowMap = new Map();
        dataFlows.forEach(f => {
            const key = `${f.source}|${f.target}`;
            if (!flowMap.has(key)) flowMap.set(key, { source: f.source, target: f.target, count: 0, types: new Set() });
            const entry = flowMap.get(key);
            entry.count++;
            entry.types.add(f.type);
        });
        const flows = Array.from(flowMap.values());

        // Layout stations in a circle
        const cx = W / 2, cy = H / 2;
        const radius = Math.min(W, H) * 0.38;
        stationList.forEach((s, i) => {
            const angle = (2 * Math.PI * i) / stationList.length - Math.PI / 2;
            s.x = cx + radius * Math.cos(angle);
            s.y = cy + radius * Math.sin(angle);
        });

        this._stationById = new Map(stationList.map(s => [s.id, s]));
        this._flows = flows;

        // Draw lines (bezier curves)
        this.lineG.selectAll('*').remove();
        flows.forEach((f, i) => {
            const src = this._stationById.get(f.source);
            const tgt = this._stationById.get(f.target);
            if (!src || !tgt) return;

            const mx = (src.x + tgt.x) / 2;
            const my = (src.y + tgt.y) / 2;
            const dx = tgt.x - src.x, dy = tgt.y - src.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            const offset = Math.min(dist * 0.3, 50);
            const nx = -dy / dist, ny = dx / dist;

            const path = d3.path();
            path.moveTo(src.x, src.y);
            path.quadraticCurveTo(mx + nx * offset, my + ny * offset, tgt.x, tgt.y);

            this.lineG.append('path')
                .attr('d', path)
                .attr('fill', 'none')
                .attr('stroke', lineColors(f.source))
                .attr('stroke-width', Math.min(1.5 + f.count * 0.5, 6))
                .attr('stroke-opacity', 0.4)
                .attr('stroke-linecap', 'round')
                .attr('data-flow-idx', i);

            // Arrowhead indicator
            this.lineG.append('circle')
                .attr('cx', tgt.x)
                .attr('cy', tgt.y)
                .attr('r', 3)
                .attr('fill', lineColors(f.source))
                .attr('opacity', 0.6)
                .attr('data-flow-idx', i);
        });

        // Draw stations with click handler
        const self = this;
        this.stationG.selectAll('*').remove();
        const stationGs = this.stationG.selectAll('g')
            .data(stationList)
            .join('g')
            .attr('transform', d => `translate(${d.x},${d.y})`)
            .style('cursor', 'pointer')
            .on('click', (event, d) => {
                event.stopPropagation();
                self._selectStation(d.id);
            });

        stationGs.append('circle')
            .attr('r', d => Math.max(8, Math.min(d.size * 1.5, 28)))
            .attr('fill', d => this._langColor(d.lang))
            .attr('stroke', '#fff')
            .attr('stroke-width', 2)
            .attr('filter', 'url(#metro-glow)');

        stationGs.append('title')
            .text(d => `${d.name}\n${d.size} files\n${d.nodes.slice(0,3).map(n => n.label).join(', ')}`);

        // Labels
        this.labelG.selectAll('*').remove();
        this.labelG.selectAll('text')
            .data(stationList)
            .join('text')
            .attr('x', d => d.x)
            .attr('y', d => d.y + Math.max(8, Math.min(d.size * 1.5, 28)) + 14)
            .attr('text-anchor', 'middle')
            .attr('fill', '#8b949e')
            .attr('font-size', '10px')
            .attr('font-family', 'monospace')
            .text(d => d.name.length > 14 ? d.name.slice(0, 13) + '…' : d.name);
    }

    _selectStation(stationId) {
        this._selectedStation = stationId;

        if (!stationId) {
            // Deselect — restore all
            this.stationG.selectAll('circle')
                .attr('stroke', '#fff')
                .attr('stroke-width', 2)
                .attr('filter', 'url(#metro-glow)')
                .transition().duration(200).attr('r', function(d) { return d3.select(this).attr('r'); });
            this.stationG.selectAll('g').select('circle')
                .attr('opacity', 1);
            this.lineG.selectAll('path').attr('stroke-opacity', 0.4);
            this.lineG.selectAll('circle').attr('opacity', 0.6);
            this.labelG.selectAll('text').attr('opacity', 1).attr('fill', '#8b949e');
            return;
        }

        const selected = this._stationById.get(stationId);
        if (!selected) return;

        // Find connected station IDs
        const connected = new Set([stationId]);
        this._flows.forEach(f => {
            if (f.source === stationId) connected.add(f.target);
            if (f.target === stationId) connected.add(f.source);
        });

        // Update stations: brighten connected, dim others
        this.stationG.selectAll('g').each(function(d) {
            const circle = d3.select(this).select('circle');
            if (d.id === stationId) {
                circle.transition().duration(300)
                    .attr('r', d => Math.max(10, Math.min(d.size * 2, 34)))
                    .attr('stroke', '#FFD700')
                    .attr('stroke-width', 4)
                    .attr('filter', 'url(#metro-glow-strong)');
                d3.select(this).raise();
            } else if (connected.has(d.id)) {
                circle.attr('stroke', '#58A6FF').attr('stroke-width', 3);
            } else {
                circle.attr('stroke', 'rgba(255,255,255,0.2)').attr('stroke-width', 1);
            }
        });

        // Update lines: brighten connected, dim others
        const self = this;
        this.lineG.selectAll('path').each(function(d, i) {
            const flow = self._flows[i];
            const connected = (flow && (flow.source === stationId || flow.target === stationId));
            d3.select(this).transition().duration(300)
                .attr('stroke-opacity', connected ? 0.85 : 0.08)
                .raise();
        });
        this.lineG.selectAll('circle').each(function(d, i) {
            const flow = self._flows[i];
            const connected = (flow && (flow.source === stationId || flow.target === stationId));
            d3.select(this).transition().duration(300)
                .attr('opacity', connected ? 0.9 : 0.1);
        });

        // Labels: brighten selected and connected
        this.labelG.selectAll('text')
            .attr('opacity', d => connected.has(d.id) ? 1 : 0.3)
            .attr('fill', d => d.id === stationId ? '#FFD700' : (connected.has(d.id) ? '#58A6FF' : '#8b949e'));
    }

    _extractModule(node) {
        const fp = node.file_path || '';
        const parts = fp.split('/');
        if (parts.length <= 1) return parts[0] || '(root)';
        return parts.slice(0, -1).join('/');
    }

    _langColor(type) {
        const map = {
            python: '#3572A5', javascript: '#F7DF1E', typescript: '#3178C6',
            rust: '#DEA584', go: '#00ADD8', function: '#58A6FF', class: '#F78166',
            module: '#8B949E', file: '#6E7681',
        };
        return map[type] || '#58A6FF';
    }

    resize() {
        if (this._data) {
            this._selectedStation = null;
            this._render();
        }
    }
}
