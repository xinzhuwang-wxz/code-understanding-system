/**
 * Sunburst View — Directory structure with file/node sizes.
 * Shows hierarchical disk/usage view of the repository.
 * Arc size = number of contained nodes.
 * Click to zoom into a directory, click center to zoom out.
 * Uses D3.js partition layout rendered to SVG.
 */
class SunburstView {
    constructor(containerEl) {
        this.container = containerEl;
        this.svg = null;
        this.g = null;
        this.graphData = null;
        this.root = null;
        this.nodeColors = {};
        this.focus = null;

        this._initSvg();
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

        // Defs for glow filter
        const defs = this.svg.append("defs");
        const filter = defs.append("filter")
            .attr("id", "sunburst-glow")
            .attr("x", "-20%").attr("y", "-20%")
            .attr("width", "140%").attr("height", "140%");
        filter.append("feGaussianBlur")
            .attr("stdDeviation", "2")
            .attr("result", "blur");
        filter.append("feMerge")
            .selectAll("feMergeNode")
            .data(["blur", "SourceGraphic"])
            .enter().append("feMergeNode")
            .attr("in", d => d);

        this.g = this.svg.append("g");
        this.centerLabel = this.g.append("text")
            .attr("text-anchor", "middle")
            .attr("fill", "#d8e4f0")
            .attr("font-size", "14px")
            .attr("font-weight", "600")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .style("pointer-events", "none")
            .style("opacity", 0);

        this.centerSubLabel = this.g.append("text")
            .attr("text-anchor", "middle")
            .attr("fill", "#7a92a8")
            .attr("font-size", "11px")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .style("pointer-events", "none")
            .style("opacity", 0);
    }

    setData(graphData) {
        this.graphData = graphData;
        this.nodeColors = graphData.node_colors || {};
        this._buildHierarchy(graphData);
        this.render();
    }

    _buildHierarchy(graphData) {
        const nodes = graphData.nodes;

        // Build directory hierarchy from file paths
        const root = {
            name: graphData.repo_name || "Repository",
            children: {},
            size: 0,
        };

        // Count node types per directory
        for (const n of nodes) {
            const fp = n.file_path || "";
            if (!fp) {
                root.size++;
                continue;
            }

            const parts = fp.split("/");
            let current = root;

            for (let i = 0; i < parts.length; i++) {
                const isFile = (i === parts.length - 1);
                const part = parts[i];

                if (!current.children[part]) {
                    current.children[part] = {
                        name: part,
                        children: {},
                        size: 0,
                        isFile: isFile,
                    };
                }
                current = current.children[part];
                current.size++;
            }
        }

        // Convert to D3 hierarchy format
        this.root = this._convertToD3(root);
    }

    _convertToD3(node) {
        const children = Object.values(node.children).map(c => this._convertToD3(c));
        if (children.length === 0 && node.size <= 1) {
            // Leaf node — keep as leaf
            delete node.children;
        }
        return {
            name: node.name,
            size: node.size || 1,
            children: children.length > 0 ? children : undefined,
            isFile: node.isFile,
        };
    }

    render() {
        if (!this.root) return;

        this.g.selectAll(".sunburst-arc").remove();
        this.g.selectAll(".sunburst-label").remove();

        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        const radius = Math.min(width, height) / 2 - 10;

        this.g.attr("transform", `translate(${width / 2},${height / 2})`);

        // Create partition layout
        const partition = data => {
            const root = d3.hierarchy(data)
                .sum(d => d.size || 1)
                .sort((a, b) => b.value - a.value);
            return d3.partition()
                .size([2 * Math.PI, radius])
                .padding(1.5)(root);
        };

        const root = partition(this.root);
        this._rootData = root;
        this.focus = root;

        // Color scale
        const color = d3.scaleOrdinal()
            .domain(root.descendants().filter(d => d.depth <= 4).map(d => d.data.name))
            .range(d3.quantize(d3.interpolateRainbow, root.descendants().length + 1))
            .unknown("#1a3358");

        // Arc generator
        const arc = d3.arc()
            .startAngle(d => d.x0)
            .endAngle(d => d.x1)
            .padAngle(d => Math.min((d.x1 - d.x0) / 2, 0.005))
            .padRadius(radius / 2)
            .innerRadius(d => d.y0)
            .outerRadius(d => Math.max(d.y1 - 2, d.y0 + 1));

        // Draw arcs
        const self = this;
        this.g.selectAll(".sunburst-arc")
            .data(root.descendants().filter(d => d.depth > 0))
            .enter().append("path")
            .attr("class", "sunburst-arc")
            .attr("d", arc)
            .attr("fill", d => {
                if (d.data.isFile) return "#0f1f38";
                // Directory: use color based on depth and index
                let c = d;
                while (c.depth > 1) c = c.parent;
                return color(c.data.name);
            })
            .attr("fill-opacity", d => {
                if (d.data.isFile) return 0.3;
                return 0.65 + (d.depth * 0.06);
            })
            .attr("stroke", "#060e1a")
            .attr("stroke-width", 0.8)
            .attr("filter", "url(#sunburst-glow)")
            .on("click", (event, d) => this._zoomTo(event, d))
            .on("mouseenter", function (event, d) {
                d3.select(this)
                    .attr("stroke", "#00e5ff")
                    .attr("stroke-width", 2)
                    .attr("fill-opacity", 0.9);
                self._showTooltip(event, d);
            })
            .on("mouseleave", function (event, d) {
                d3.select(this)
                    .attr("stroke", "#060e1a")
                    .attr("stroke-width", 0.8)
                    .attr("fill-opacity", d.data.isFile ? 0.3 : 0.65 + (d.depth * 0.06));
                self._hideTooltip();
            })
            .append("title")
            .text(d => {
                const path = d.ancestors().reverse().map(a => a.data.name).join("/");
                return `${path}\n${d.value} node${d.value !== 1 ? "s" : ""}`;
            });

        // Labels for larger arcs
        this.g.selectAll(".sunburst-label")
            .data(root.descendants().filter(d => d.depth > 0 && (d.x1 - d.x0) > 0.2))
            .enter().append("text")
            .attr("class", "sunburst-label")
            .attr("transform", d => {
                const x = (d.x0 + d.x1) / 2 * 180 / Math.PI;
                const y = (d.y0 + d.y1) / 2;
                return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
            })
            .attr("dy", "0.35em")
            .attr("text-anchor", "middle")
            .attr("fill", "#d8e4f0")
            .attr("font-size", d => Math.max(8, Math.min(13, (d.x1 - d.x0) * 30)) + "px")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .attr("pointer-events", "none")
            .text(d => {
                const name = d.data.name;
                const maxLen = Math.floor((d.x1 - d.x0) * 12);
                if (name.length > maxLen && maxLen > 3) return name.substring(0, maxLen - 1) + "…";
                if (name.length > 20) return name.substring(0, 18) + "…";
                return name;
            });

        // Center label
        this._updateCenterLabel(root);
    }

    _zoomTo(event, d) {
        this.focus = (this.focus && this.focus === d) ? d.parent : d;

        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        const radius = Math.min(width, height) / 2 - 10;

        this.g.transition()
            .duration(750)
            .tween("scale", () => {
                const xd = d3.interpolate(this._rootData.x0, this.focus.x0);
                const yd = d3.interpolate(this._rootData.y0, this.focus.y0);
                const yr = d3.interpolate(this._rootData.y1 - this._rootData.y0, radius);
                return t => {
                    this._rootData.x0r = xd(t);
                    this._rootData.y0r = yd(t);
                    this._rootData.r = yr(t);
                    this._redrawZoomed();
                };
            })
            .on("end", () => {
                this._updateCenterLabel(this.focus);
            });
    }

    _redrawZoomed() {
        const root = this._rootData;
        const radius = root.r || (Math.min(this.container.clientWidth, this.container.clientHeight) / 2 - 10);

        const arc = d3.arc()
            .startAngle(d => Math.max(0, Math.min(2 * Math.PI, root.x0r + (d.x0 - root.x0) * 2 * Math.PI / (root.x1 - root.x0))))
            .endAngle(d => Math.max(0, Math.min(2 * Math.PI, root.x0r + (d.x1 - root.x0) * 2 * Math.PI / (root.x1 - root.x0))))
            .innerRadius(d => Math.max(0, root.y0r + (d.y0 - root.y0) * radius / (root.y1 - root.y0)))
            .outerRadius(d => Math.max(0, root.y0r + (d.y1 - root.y0) * radius / (root.y1 - root.y0)));

        this.g.selectAll(".sunburst-arc")
            .attr("d", arc);
    }

    _updateCenterLabel(d) {
        const name = d.data.name;
        const size = d.value;

        this.centerLabel
            .text(name.length > 20 ? name.substring(0, 18) + "…" : name)
            .style("opacity", 1);
        this.centerSubLabel
            .text(`${size} node${size !== 1 ? "s" : ""}` + (d.parent ? " · click to zoom out" : ""))
            .style("opacity", 1);
    }

    _showTooltip(event, d) {
        // Tooltip is handled by SVG <title> elements
    }

    _hideTooltip() {
        // No-op; <title> elements handle this
    }

    resize() {
        if (this.graphData) {
            // Re-render with new dimensions
            this.g.attr("transform", null);
            if (this._rootData) {
                const width = this.container.clientWidth;
                const height = this.container.clientHeight;
                this.g.attr("transform", `translate(${width / 2},${height / 2})`);
            }
            this.render();
        }
    }

    destroy() {
        if (this.svg) {
            this.svg.remove();
            this.svg = null;
        }
    }
}
