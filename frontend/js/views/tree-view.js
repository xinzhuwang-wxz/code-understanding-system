/**
 * Tree View — Hierarchical tree/radial visualization for call chains.
 * Shows file→class→function hierarchy with expandable/collapsible nodes.
 * Uses D3.js tree layout rendered to SVG.
 */
class TreeView {
    constructor(containerEl) {
        this.container = containerEl;
        this.svg = null;
        this.g = null;
        this.graphData = null;
        this.root = null;
        this.nodeMap = {};
        this.nodeColors = {};
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

        // Background
        this.svg.append("rect")
            .attr("width", "100%")
            .attr("height", "100%")
            .attr("fill", "#060e1a");

        // Defs for gradients
        const defs = this.svg.append("defs");
        const filter = defs.append("filter")
            .attr("id", "tree-glow")
            .attr("x", "-50%").attr("y", "-50%")
            .attr("width", "200%").attr("height", "200%");
        filter.append("feGaussianBlur")
            .attr("stdDeviation", "3")
            .attr("result", "blur");
        filter.append("feMerge")
            .selectAll("feMergeNode")
            .data(["blur", "SourceGraphic"])
            .enter().append("feMergeNode")
            .attr("in", d => d);

        this.g = this.svg.append("g");
    }

    _initZoom() {
        const self = this;
        this.zoom = d3.zoom()
            .scaleExtent([0.1, 4])
            .on("zoom", (event) => {
                self.g.attr("transform", event.transform);
            });
        this.svg.call(this.zoom);
    }

    setData(graphData) {
        this.graphData = graphData;
        this.nodeColors = graphData.node_colors || {};
        this.edgeColors = graphData.edge_colors || {};

        // Build node map
        this.nodeMap = {};
        for (const n of graphData.nodes) {
            this.nodeMap[n.id] = n;
        }

        // Build hierarchy
        this.root = this._buildHierarchy(graphData);
        this.render();
    }

    _buildHierarchy(graphData) {
        const nodes = graphData.nodes;
        const edges = graphData.edges;

        // Build adjacency for parent-child relationships
        const children = {}; // nodeId -> [child nodeIds]
        const parentOf = {}; // nodeId -> parentNodeId

        // Strategy: group by containment/file relationships
        // 1. Group by file_path
        const byFile = {};
        const fileNodes = {};
        for (const n of nodes) {
            const fp = n.file_path || "";
            if (n.type === "file") {
                fileNodes[n.id] = n;
            }
            if (!byFile[fp]) byFile[fp] = [];
            byFile[fp].push(n);
        }

        // 2. Create virtual root
        const root = {
            id: "__root__",
            label: this.graphData.repo_name || "Repository",
            type: "root",
            children: [],
        };

        // 3. Build directory hierarchy from file paths
        const dirMap = { "": root };
        const allPaths = new Set();
        for (const n of nodes) {
            if (n.file_path) allPaths.add(n.file_path);
        }

        // Create directory nodes from file paths
        for (const fp of allPaths) {
            const parts = fp.split("/");
            let currentPath = "";
            for (let i = 0; i < parts.length - 1; i++) {
                const prevPath = currentPath;
                currentPath += (currentPath ? "/" : "") + parts[i];
                if (!dirMap[currentPath]) {
                    const dirNode = {
                        id: "dir:" + currentPath,
                        label: parts[i],
                        type: "directory",
                        file_path: currentPath,
                        children: [],
                    };
                    dirMap[currentPath] = dirNode;
                    const parent = dirMap[prevPath] || root;
                    if (!parent.children) parent.children = [];
                    parent.children.push(dirNode);
                }
            }
        }

        // 4. Place file nodes under their directories
        for (const fp of allPaths) {
            const parts = fp.split("/");
            const dirPath = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
            const parentDir = dirMap[dirPath] || root;

            // Find or create file node
            let fileNode = null;
            for (const n of nodes) {
                if (n.file_path === fp && n.type === "file") {
                    fileNode = { ...n, children: [] };
                    break;
                }
            }
            if (!fileNode) {
                // Check if there's exactly one node with this file_path that isn't a "file" type
                const fileNodesInPath = nodes.filter(n => n.file_path === fp);
                if (fileNodesInPath.length > 0) {
                    fileNode = {
                        id: "file:" + fp,
                        label: parts[parts.length - 1],
                        type: "file",
                        file_path: fp,
                        children: [],
                    };
                }
            }

            if (fileNode) {
                if (!parentDir.children) parentDir.children = [];
                // Avoid duplicate
                if (!parentDir.children.find(c => c.id === fileNode.id)) {
                    parentDir.children.push(fileNode);
                }
            }
        }

        // 5. Place classes under their files
        for (const n of nodes) {
            if (n.type === "class") {
                const fp = n.file_path || "";
                // Find parent: either a file node or the directory
                let parent = null;
                // Try to find the file node
                for (const c of this._flattenChildren(root)) {
                    if (c.type === "file" && c.file_path === fp) {
                        parent = c;
                        break;
                    }
                }
                if (!parent) {
                    // Put under directory
                    const parts = fp.split("/");
                    const dirPath = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
                    parent = dirMap[dirPath] || root;
                }
                if (!parent.children) parent.children = [];
                const classNode = { ...n, children: [] };
                if (!parent.children.find(c => c.id === classNode.id)) {
                    parent.children.push(classNode);
                }
            }
        }

        // 6. Place functions/methods under their classes or files
        for (const n of nodes) {
            if (n.type === "function" || n.type === "method") {
                const fp = n.file_path || "";
                // Check if this function is called by or belongs to a class
                let parent = null;

                // Look for a class in the same file that calls this function
                for (const e of edges) {
                    const srcId = typeof e.source === "object" ? e.source.id : e.source;
                    const tgtId = typeof e.target === "object" ? e.target.id : e.target;
                    const srcNode = this.nodeMap[srcId];
                    if (tgtId === n.id && srcNode && srcNode.type === "class") {
                        parent = this._findInTree(root, srcId);
                        if (parent) break;
                    }
                }

                // If not found, try to find class in same file
                if (!parent) {
                    for (const c of this._flattenChildren(root)) {
                        if (c.type === "class" && c.file_path === fp) {
                            parent = c;
                            break;
                        }
                    }
                }

                // Fall back to file or directory
                if (!parent) {
                    for (const c of this._flattenChildren(root)) {
                        if (c.type === "file" && c.file_path === fp) {
                            parent = c;
                            break;
                        }
                    }
                }
                if (!parent) {
                    const parts = fp.split("/");
                    const dirPath = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
                    parent = dirMap[dirPath] || root;
                }

                if (!parent.children) parent.children = [];
                const funcNode = { ...n, children: [] };
                if (!parent.children.find(c => c.id === funcNode.id)) {
                    parent.children.push(funcNode);
                }
            }
        }

        // 7. Place remaining nodes under their files
        for (const n of nodes) {
            if (n.type !== "file" && n.type !== "class" && n.type !== "function" && n.type !== "method") {
                const fp = n.file_path || "";
                let parent = null;
                for (const c of this._flattenChildren(root)) {
                    if (c.type === "file" && c.file_path === fp) {
                        parent = c;
                        break;
                    }
                }
                if (!parent) {
                    const parts = fp.split("/");
                    const dirPath = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
                    parent = dirMap[dirPath] || root;
                }
                if (!parent.children) parent.children = [];
                if (!parent.children.find(c => c.id === n.id)) {
                    parent.children.push({ ...n, children: [] });
                }
            }
        }

        // 8. Clean up empty children arrays
        this._cleanTree(root);

        return root;
    }

    _flattenChildren(node) {
        const result = [];
        if (node.children) {
            for (const c of node.children) {
                result.push(c);
                result.push(...this._flattenChildren(c));
            }
        }
        return result;
    }

    _findInTree(node, id) {
        if (node.id === id) return node;
        if (node.children) {
            for (const c of node.children) {
                const found = this._findInTree(c, id);
                if (found) return found;
            }
        }
        return null;
    }

    _cleanTree(node) {
        if (node.children && node.children.length === 0) {
            delete node.children;
        }
        if (node.children) {
            for (const c of node.children) {
                this._cleanTree(c);
            }
        }
        return node;
    }

    render() {
        if (!this.root) return;

        this.g.selectAll("*").remove();

        const width = this.container.clientWidth;
        const height = this.container.clientHeight;

        // Use D3 tree layout
        const hierarchy = d3.hierarchy(this.root);

        const treeLayout = d3.tree()
            .size([height - 120, width - 280])
            .separation((a, b) => (a.parent === b.parent ? 1 : 1.3));

        treeLayout(hierarchy);

        // Center the tree
        const offsetX = 140;
        const offsetY = 60;

        // Draw edges
        this.g.selectAll(".tree-edge")
            .data(hierarchy.links())
            .enter().append("path")
            .attr("class", "tree-edge")
            .attr("d", d => {
                return `M${d.source.y + offsetX},${d.source.x + offsetY}
                        C${(d.source.y + d.target.y) / 2 + offsetX},${d.source.x + offsetY}
                         ${(d.source.y + d.target.y) / 2 + offsetX},${d.target.x + offsetY}
                         ${d.target.y + offsetX},${d.target.x + offsetY}`;
            })
            .attr("fill", "none")
            .attr("stroke", d => {
                const type = d.target.data.type;
                return this.edgeColors[type] || "#1a3358";
            })
            .attr("stroke-width", 1.2)
            .attr("stroke-opacity", 0.5);

        // Draw nodes
        const nodeG = this.g.selectAll(".tree-node")
            .data(hierarchy.descendants())
            .enter().append("g")
            .attr("class", "tree-node")
            .attr("transform", d => `translate(${d.y + offsetX},${d.x + offsetY})`)
            .style("cursor", d => (d.children || d._children) ? "pointer" : "default")
            .on("click", (event, d) => this._toggleNode(event, d));

        // Node circles
        nodeG.append("circle")
            .attr("r", d => this._nodeSize(d))
            .attr("fill", d => this.nodeColors[d.data.type] || "#78909c")
            .attr("stroke", d => d3.color(this.nodeColors[d.data.type] || "#78909c").brighter(0.5))
            .attr("stroke-width", 1.5)
            .attr("filter", "url(#tree-glow)")
            .attr("opacity", 0.9);

        // Node labels
        nodeG.append("text")
            .attr("dy", d => this._nodeSize(d) + 12)
            .attr("text-anchor", "middle")
            .attr("fill", "#d8e4f0")
            .attr("font-size", "11px")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .text(d => {
                const label = d.data.label || "";
                return label.length > 24 ? label.substring(0, 22) + "…" : label;
            });

        // Type labels (smaller, below name)
        nodeG.append("text")
            .attr("dy", d => this._nodeSize(d) + 25)
            .attr("text-anchor", "middle")
            .attr("fill", "#4a6278")
            .attr("font-size", "9px")
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")
            .text(d => d.data.type !== "root" ? d.data.type : "");

        // Tooltips
        nodeG.append("title")
            .text(d => `${d.data.label || d.data.id}\nType: ${d.data.type}${d.data.file_path ? `\n${d.data.file_path}` : ""}`);
    }

    _nodeSize(d) {
        const type = d.data.type;
        const map = {
            "root": 8,
            "directory": 5,
            "file": 6,
            "class": 7,
            "function": 4,
            "method": 4,
        };
        return map[type] || 4;
    }

    _toggleNode(event, d) {
        if (d.children) {
            d._children = d.children;
            d.children = null;
        } else if (d._children) {
            d.children = d._children;
            d._children = null;
        }
        this.render();
    }

    resize() {
        if (this.root) this.render();
    }

    destroy() {
        if (this.svg) {
            this.svg.remove();
            this.svg = null;
        }
    }
}
