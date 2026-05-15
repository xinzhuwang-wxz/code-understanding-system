/**
 * Matrix View — Adjacency matrix for file×file dependency heatmap.
 * Rows = source files, Columns = target files.
 * Cell color intensity = number of dependencies between files.
 * Uses D3.js rendered to SVG.
 */
class MatrixView {
    constructor(containerEl) {
        this.container = containerEl;
        this.svg = null;
        this.g = null;
        this.graphData = null;
        this.matrix = [];
        this.fileList = [];
        this.edgeColors = {};

        this._initSvg();
        this._initZoom();
    }

    _initSvg() {
        this.svg = d3.select(this.container)
            .append("svg")
            .attr("width", "100%")
            .attr("height", "100%")
            .style("display", "block");

        this.svg.append("rect")
            .attr("width", "100%")
            .attr("height", "100%")
            .attr("fill", "#060e1a");

        this.g = this.svg.append("g");
    }

    _initZoom() {
        const self = this;
        this.zoom = d3.zoom()
            .scaleExtent([0.2, 6])
            .on("zoom", (event) => {
                self.g.attr("transform", event.transform);
            });
        this.svg.call(this.zoom);
    }

    setData(graphData) {
        this.graphData = graphData;
        this.edgeColors = graphData.edge_colors || {};
        this._buildMatrix(graphData);
        this.render();
    }

    _buildMatrix(graphData) {
        const nodes = graphData.nodes;
        const edges = graphData.edges;

        // Collect unique file paths from nodes
        const fileSet = new Map(); // filePath -> {path, nodeCount, edgeCount}
        for (const n of nodes) {
            const fp = n.file_path || "(no file)";
            if (!fileSet.has(fp)) {
                fileSet.set(fp, { path: fp, nodeCount: 0, edgeCount: 0 });
            }
            fileSet.get(fp).nodeCount++;
        }

        // Build file-level dependency matrix from edges
        const edgeMap = {}; // "srcFile->tgtFile" -> count
        for (const e of edges) {
            const srcId = typeof e.source === "object" ? e.source.id : e.source;
            const tgtId = typeof e.target === "object" ? e.target.id : e.target;
            const srcNode = this._findNode(nodes, srcId);
            const tgtNode = this._findNode(nodes, tgtId);
            if (!srcNode || !tgtNode) continue;

            const srcFile = srcNode.file_path || "(no file)";
            const tgtFile = tgtNode.file_path || "(no file)";
            const key = srcFile + "|||" + tgtFile;

            if (!edgeMap[key]) edgeMap[key] = 0;
            edgeMap[key]++;
        }

        // Sort files by dependency count (most connected first)
        const filePaths = Array.from(fileSet.keys());
        const depCount = {};
        for (const key of Object.keys(edgeMap)) {
            const [src, tgt] = key.split("|||");
            depCount[src] = (depCount[src] || 0) + edgeMap[key];
            depCount[tgt] = (depCount[tgt] || 0) + edgeMap[key];
        }

        filePaths.sort((a, b) => (depCount[b] || 0) - (depCount[a] || 0));

        // Limit to top 100 files for readability
        this.fileList = filePaths.slice(0, 100);

        // Build matrix
        this.matrix = [];
        this.edgeMap = edgeMap;
    }

    _findNode(nodes, id) {
        for (const n of nodes) {
            if (n.id === id) return n;
        }
        return null;
    }

    render() {
        if (this.fileList.length === 0) return;
        this.g.selectAll("*").remove();

        const files = this.fileList;
        const n = files.length;
        const cellSize = Math.max(8, Math.min(22, Math.floor(800 / n)));
        const totalSize = n * cellSize;
        const margin = { top: 160, left: 200, right: 40, bottom: 40 };

        const width = totalSize + margin.left + margin.right;
        const height = totalSize + margin.top + margin.bottom;

        this.svg.attr("viewBox", `0 0 ${width} ${height}`);

        // Color scale
        const maxVal = Math.max(1, ...Object.values(this.edgeMap));
        const colorScale = d3.scaleSequential(d3.interpolateYlOrRd)
            .domain([0, maxVal]);

        // Draw cells
        for (let i = 0; i < n; i++) {
            for (let j = 0; j < n; j++) {
                const key = files[i] + "|||" + files[j];
                const value = this.edgeMap[key] || 0;

                const cell = this.g.append("rect")
                    .attr("x", margin.left + j * cellSize)
                    .attr("y", margin.top + i * cellSize)
                    .attr("width", cellSize - 0.5)
                    .attr("height", cellSize - 0.5)
                    .attr("fill", value > 0 ? colorScale(value) : "#0a1628")
                    .attr("stroke", value > 0 ? "rgba(255,255,255,0.1)" : "rgba(255,255,255,0.02)")
                    .attr("stroke-width", 0.3)
                    .attr("rx", 1);

                if (value > 0) {
                    cell.append("title")
                        .text(`${this._shortPath(files[i])}\n→ ${this._shortPath(files[j])}\n${value} dependenc${value !== 1 ? "ies" : "y"}`);
                }

                // Hover highlight
                cell.on("mouseenter", function () {
                    d3.select(this)
                        .attr("stroke", "#00e5ff")
                        .attr("stroke-width", 2);
                }).on("mouseleave", function () {
                    d3.select(this)
                        .attr("stroke", value > 0 ? "rgba(255,255,255,0.1)" : "rgba(255,255,255,0.02)")
                        .attr("stroke-width", 0.3);
                });
            }
        }

        // Column labels (rotated)
        this.g.selectAll(".col-label")
            .data(files)
            .enter().append("text")
            .attr("class", "col-label")
            .attr("x", (d, i) => margin.left + i * cellSize + cellSize / 2)
            .attr("y", margin.top - 8)
            .attr("text-anchor", "start")
            .attr("transform", (d, i) => `rotate(-65, ${margin.left + i * cellSize + cellSize / 2}, ${margin.top - 8})`)
            .attr("fill", "#7a92a8")
            .attr("font-size", Math.max(8, cellSize * 0.7) + "px")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .text(d => this._shortPath(d));

        // Row labels
        this.g.selectAll(".row-label")
            .data(files)
            .enter().append("text")
            .attr("class", "row-label")
            .attr("x", margin.left - 8)
            .attr("y", (d, i) => margin.top + i * cellSize + cellSize / 2)
            .attr("text-anchor", "end")
            .attr("dominant-baseline", "middle")
            .attr("fill", "#7a92a8")
            .attr("font-size", Math.max(8, cellSize * 0.7) + "px")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .text(d => this._shortPath(d));

        // Axis labels
        this.g.append("text")
            .attr("x", margin.left + totalSize / 2)
            .attr("y", 20)
            .attr("text-anchor", "middle")
            .attr("fill", "#00bcd4")
            .attr("font-size", "13px")
            .attr("font-weight", "700")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .text("TARGET FILES →");

        this.g.append("text")
            .attr("x", -margin.top - totalSize / 2)
            .attr("y", 14)
            .attr("text-anchor", "middle")
            .attr("transform", "rotate(-90)")
            .attr("fill", "#00bcd4")
            .attr("font-size", "13px")
            .attr("font-weight", "700")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .text("SOURCE FILES →");

        // Self-dependency diagonal indicator
        for (let i = 0; i < n; i++) {
            const key = files[i] + "|||" + files[i];
            if (!this.edgeMap[key]) {
                this.g.append("rect")
                    .attr("x", margin.left + i * cellSize)
                    .attr("y", margin.top + i * cellSize)
                    .attr("width", cellSize - 0.5)
                    .attr("height", cellSize - 0.5)
                    .attr("fill", "none")
                    .attr("stroke", "rgba(255,255,255,0.05)")
                    .attr("stroke-width", 0.5);
            }
        }

        // Legend
        const legendX = margin.left + totalSize - 160;
        const legendY = margin.top + totalSize + 16;
        const legendGrad = this.svg.append("defs")
            .append("linearGradient")
            .attr("id", "matrix-legend-grad")
            .attr("x1", "0%").attr("y1", "0%")
            .attr("x2", "100%").attr("y2", "0%");
        legendGrad.append("stop").attr("offset", "0%").attr("stop-color", d3.interpolateYlOrRd(0));
        legendGrad.append("stop").attr("offset", "100%").attr("stop-color", d3.interpolateYlOrRd(1));

        this.g.append("rect")
            .attr("x", legendX)
            .attr("y", legendY)
            .attr("width", 140)
            .attr("height", 12)
            .attr("fill", "url(#matrix-legend-grad)")
            .attr("rx", 2);

        this.g.append("text")
            .attr("x", legendX)
            .attr("y", legendY + 24)
            .attr("fill", "#4a6278")
            .attr("font-size", "9px")
            .text("0");

        this.g.append("text")
            .attr("x", legendX + 130)
            .attr("y", legendY + 24)
            .attr("fill", "#4a6278")
            .attr("font-size", "9px")
            .attr("text-anchor", "end")
            .text(maxVal);
    }

    _shortPath(path) {
        if (!path || path === "(no file)") return "(none)";
        const parts = path.split("/");
        if (parts.length <= 2) return path;
        return parts.slice(-2).join("/");
    }

    resize() {
        if (this.graphData) this.render();
    }

    destroy() {
        if (this.svg) {
            this.svg.remove();
            this.svg = null;
        }
    }
}
