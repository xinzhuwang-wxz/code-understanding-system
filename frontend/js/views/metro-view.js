/**
 * MetroMap — SVG-based module data-flow visualization.
 * Modules → stations (circles), data-flows → metro lines.
 * Uses D3.js (already loaded).
 */
class MetroMapView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.svg = null;
        this._data = null;
        this._simulation = null;
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

        this.g = this.svg.append('g');
        this.lineG = this.g.append('g').attr('class', 'metro-lines');
        this.stationG = this.g.append('g').attr('class', 'metro-stations');
        this.labelG = this.g.append('g').attr('class', 'metro-labels');

        // Zoom
        this.svg.call(d3.zoom()
            .scaleExtent([0.3, 3])
            .on('zoom', (e) => this.g.attr('transform', e.transform)));
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
                modules.set(mod, { id: mod, name: mod, size: 1, lang: n.type });
            } else {
                modules.get(mod).size++;
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

        const stationById = new Map(stationList.map(s => [s.id, s]));

        // Draw lines (bezier curves)
        this.lineG.selectAll('*').remove();
        flows.forEach(f => {
            const src = stationById.get(f.source);
            const tgt = stationById.get(f.target);
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
                .attr('stroke-linecap', 'round');

            // Arrowhead indicator
            this.lineG.append('circle')
                .attr('cx', tgt.x)
                .attr('cy', tgt.y)
                .attr('r', 3)
                .attr('fill', lineColors(f.source))
                .attr('opacity', 0.6);
        });

        // Draw stations
        this.stationG.selectAll('*').remove();
        this.stationG.selectAll('circle')
            .data(stationList)
            .join('circle')
            .attr('cx', d => d.x)
            .attr('cy', d => d.y)
            .attr('r', d => Math.max(8, Math.min(d.size * 1.5, 28)))
            .attr('fill', d => this._langColor(d.lang))
            .attr('stroke', '#fff')
            .attr('stroke-width', 2)
            .attr('filter', 'url(#metro-glow)')
            .append('title')
            .text(d => `${d.name}\n${d.size} files`);

        // Labels
        this.labelG.selectAll('*').remove();
        this.labelG.selectAll('text')
            .data(stationList)
            .join('text')
            .attr('x', d => d.x)
            .attr('y', d => d.y + d.size * 0.8 + 14)
            .attr('text-anchor', 'middle')
            .attr('fill', '#8b949e')
            .attr('font-size', '10px')
            .attr('font-family', 'monospace')
            .text(d => d.name.length > 14 ? d.name.slice(0, 13) + '…' : d.name);
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
        if (this._data) this._render();
    }
}
