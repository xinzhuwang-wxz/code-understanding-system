/**
 * Main application: wires the sidebar, graph renderer, detail panel, and API together.
 */
(function () {
    "use strict";

    const canvas = document.getElementById("graph-canvas");
    const tooltip = document.getElementById("tooltip");
    const repoInput = document.getElementById("repo-path");
    const analyzeBtn = document.getElementById("analyze-btn");
    const saveBtn = document.getElementById("save-btn");
    const loadBtn = document.getElementById("load-btn");
    const loadFileInput = document.getElementById("load-file-input");
    const statusMsg = document.getElementById("status-msg");
    const loadingOverlay = document.getElementById("loading-overlay");
    const detailPanelEl = document.getElementById("detail-panel");

    // View panels
    const viewForce = document.getElementById("view-force");
    const viewTree = document.getElementById("view-tree");
    const viewMatrix = document.getElementById("view-matrix");
    const viewSunburst = document.getElementById("view-sunburst");
    const viewCodecity = document.getElementById("view-codecity");
    const viewMetro = document.getElementById("view-metro");
    const viewCodepanel = document.getElementById("view-codepanel");

    const viewPanels = {
        force: viewForce,
        tree: viewTree,
        matrix: viewMatrix,
        sunburst: viewSunburst,
        codecity: viewCodecity,
        metro: viewMetro,
        codepanel: viewCodepanel,
    };

    const renderer = new GraphRenderer(canvas, tooltip);
    const sidebar = new SidebarController(renderer);
    const detailPanel = new DetailPanel(detailPanelEl, {
        onNavigate: (nodeId) => {
            renderer.zoomToNode(nodeId);
        },
        onHighlightPath: (nodeId, upstreamIds, downstreamIds) => {
            if (nodeId === null) {
                renderer.clearHighlightedPath();
            } else {
                renderer.setHighlightedPath(nodeId, upstreamIds, downstreamIds);
            }
        },
    });

    // Alternate views (lazy init)
    const views = {
        tree: null,
        matrix: null,
        sunburst: null,
        codecity: null,
        metro: null,
    };
    let codePanel = null;  // Monaco Editor, loaded on demand

    let currentView = "force";
    let currentGraphData = null;

    renderer.onNodeClick((node) => {
        if (renderer.highlightedNodeId === null) {
            detailPanel.close();
            renderer.clearHighlightedPath();
        } else {
            detailPanel.show(node.id);
        }
    });

    sidebar.onViewChange((viewName) => {
        switchView(viewName);
    });

    function switchView(viewName) {
        if (viewName === currentView) return;

        // Hide all panels
        for (const [name, panel] of Object.entries(viewPanels)) {
            panel.style.display = "none";
        }

        // Show selected panel
        const panel = viewPanels[viewName];
        if (panel) {
            panel.style.display = "";
        }

        currentView = viewName;

        // Code panel: lazy-load Monaco
        if (viewName === "codepanel") {
            if (!codePanel) {
                codePanel = new CodePanel("view-codepanel");
            }
            codePanel.init();
            return;
        }

        // Initialize graph views if data loaded
        if (viewName !== "force" && currentGraphData) {
            initAlternateView(viewName);
        }

        // Trigger resize for SVG/3D views
        if (viewName !== "force" && views[viewName]) {
            setTimeout(() => views[viewName].resize(), 50);
        }
    }

    function initAlternateView(viewName) {
        if (views[viewName]) {
            views[viewName].setData(currentGraphData);
            return;
        }

        const panel = viewPanels[viewName];
        if (!panel) return;

        let view;
        if (viewName === "tree") {
            view = new TreeView(panel);
        } else if (viewName === "matrix") {
            view = new MatrixView(panel);
        } else if (viewName === "sunburst") {
            view = new SunburstView(panel);
        } else if (viewName === "codecity") {
            view = new CodeCityView(panel.id || "view-codecity");
        } else if (viewName === "metro") {
            view = new MetroMapView(panel.id || "view-metro");
        }

        if (view) {
            views[viewName] = view;
            view.setData(currentGraphData);
        }
    }

    analyzeBtn.addEventListener("click", () => startAnalysis());
    repoInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") startAnalysis();
    });

    saveBtn.addEventListener("click", () => saveGraph());
    loadBtn.addEventListener("click", () => loadFileInput.click());
    loadFileInput.addEventListener("change", (e) => loadGraph(e));

    async function startAnalysis() {
        const repoPath = repoInput.value.trim();
        if (!repoPath) {
            setStatus("Please enter a repository path.", true);
            return;
        }

        analyzeBtn.disabled = true;
        loadingOverlay.style.display = "";
        setStatus("Analyzing...");

        try {
            const resp = await fetch("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ repo_path: repoPath }),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ detail: resp.statusText }));
                throw new Error(err.detail || "Analysis failed");
            }

            const data = await resp.json();

            if (!data.nodes || data.nodes.length === 0) {
                setStatus("No analyzable files found in the repository.", true);
                loadingOverlay.style.display = "none";
                analyzeBtn.disabled = false;
                return;
            }

            displayGraph(data);
            setStatus(`Loaded ${data.stats.total_nodes} nodes, ${data.stats.total_edges} edges`);
            loadTour(repoPath);
            loadQuestions(repoPath);
        } catch (err) {
            setStatus(err.message, true);
        } finally {
            loadingOverlay.style.display = "none";
            analyzeBtn.disabled = false;
        }
    }

    function displayGraph(data) {
        currentGraphData = data;
        detailPanel.close();
        renderer.setData(data);
        sidebar.populate(data);
        detailPanel.setGraphData(data);
        saveBtn.disabled = false;

        // Update alternate views if they're visible
        if (currentView !== "force" && views[currentView]) {
            views[currentView].setData(data);
        }
    }

    function saveGraph() {
        if (!currentGraphData) return;
        const json = JSON.stringify(currentGraphData, null, 2);
        const blob = new Blob([json], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        const name = (currentGraphData.repo_name || "code_graph") + ".json";
        a.download = name;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        setStatus("Graph saved to " + name);
    }

    function loadGraph(event) {
        const file = event.target.files[0];
        if (!file) return;

        loadingOverlay.style.display = "";
        setStatus("Loading...");

        const reader = new FileReader();
        reader.onload = function (e) {
            try {
                const data = JSON.parse(e.target.result);
                if (!data.nodes || !Array.isArray(data.nodes)) {
                    throw new Error("Invalid graph JSON: missing nodes array");
                }
                if (!data.edges || !Array.isArray(data.edges)) {
                    throw new Error("Invalid graph JSON: missing edges array");
                }
                displayGraph(data);
                setStatus(`Loaded ${data.stats?.total_nodes || data.nodes.length} nodes, ${data.stats?.total_edges || data.edges.length} edges from file`);
            } catch (err) {
                setStatus("Failed to load: " + err.message, true);
            } finally {
                loadingOverlay.style.display = "none";
                loadFileInput.value = "";
            }
        };
        reader.onerror = function () {
            setStatus("Failed to read file", true);
            loadingOverlay.style.display = "none";
        };
        reader.readAsText(file);
    }

    function setStatus(msg, isError) {
        statusMsg.textContent = msg;
        statusMsg.className = "status-msg" + (isError ? " error" : "");
    }

    // ─── Code Tour ────────────────────────────────────────────────

    const tourSection = document.getElementById("tour-section");
    const tourContent = document.getElementById("tour-content");
    const tourRefreshBtn = document.getElementById("tour-refresh-btn");

    async function loadTour(repoPath) {
        if (!repoPath) return;
        try {
            const resp = await fetch("/api/tour", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ repo_path: repoPath, max_stops: 10 }),
            });
            if (!resp.ok) return;
            const data = await resp.json();
            renderTour(data.stops || []);
        } catch (_) { /* best-effort */ }
    }

    function renderTour(stops) {
        if (!stops || stops.length === 0) {
            tourContent.innerHTML = '<div class="tour-empty">No tour available. Analyze a repo first.</div>';
            tourSection.style.display = "";
            return;
        }
        tourContent.innerHTML = stops.map(stop => {
            const icon = stop.type === "file" ? "📁" :
                        stop.type === "function" ? "⚡" :
                        stop.type === "entry" ? "🚪" : "📌";
            return `
                <div class="tour-stop" onclick="window.codeKG?.switchView('force'); window.codeKG?.zoomToNode('${stop.file_path}')">
                    <div class="tour-stop-title">${icon} ${stop.title || stop.file_path}</div>
                    <div class="tour-stop-desc">${stop.description || ""}</div>
                    <div class="tour-stop-meta">${stop.file_path || ""}</div>
                </div>
            `;
        }).join("");
        tourSection.style.display = "";
    }

    if (tourRefreshBtn) {
        tourRefreshBtn.addEventListener("click", () => {
            const repoPath = repoInput.value.trim();
            if (repoPath) loadTour(repoPath);
        });
    }

    // ─── Question Seeds ────────────────────────────────────────────

    const questionsSection = document.getElementById("questions-section");
    const questionsContent = document.getElementById("questions-content");
    const questionsRefreshBtn = document.getElementById("questions-refresh-btn");

    async function loadQuestions(repoPath) {
        if (!repoPath) return;
        try {
            const resp = await fetch("/api/questions", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ repo_path: repoPath, max_questions: 5 }),
            });
            if (!resp.ok) return;
            const data = await resp.json();
            renderQuestions(data.questions || []);
        } catch (_) { /* best-effort */ }
    }

    function renderQuestions(questions) {
        if (!questions || questions.length === 0) {
            questionsContent.innerHTML = '<div class="question-empty">No questions yet.</div>';
            questionsSection.style.display = "";
            return;
        }
        questionsContent.innerHTML = questions.map(q => `
            <div class="question-item" onclick="document.getElementById('search-input').value = '${q.question.replace(/'/g, "\\'")}'; document.getElementById('search-input').focus();">
                <div class="question-text">${q.question}</div>
                <span class="question-category">${q.category || "general"}</span>
            </div>
        `).join("");
        questionsSection.style.display = "";
    }

    if (questionsRefreshBtn) {
        questionsRefreshBtn.addEventListener("click", () => {
            const repoPath = repoInput.value.trim();
            if (repoPath) loadQuestions(repoPath);
        });
    }

    // Handle window resize for active view
    window.addEventListener("resize", () => {
        if (currentView !== "force" && views[currentView]) {
            views[currentView].resize();
        }
    });

    // Expose to detail-panel for "Open in Editor" button
    window.codeKG = {
        switchView,
        get codePanel() { return codePanel; },
        zoomToNode(filePath) {
            if (renderer && filePath) {
                renderer.zoomToNode(filePath);
            }
        },
        showNode(nodeId) {
            if (detailPanel && nodeId) {
                detailPanel.show(nodeId);
            }
        },
    };

    initMobileToggle();

    function initMobileToggle() {
        const sidebar = document.getElementById("sidebar");
        const btn = document.createElement("button");
        btn.className = "mobile-toggle";
        btn.setAttribute("aria-label", "Toggle sidebar");
        btn.textContent = "\u2630";
        document.body.appendChild(btn);

        btn.addEventListener("click", () => {
            sidebar.classList.toggle("open");
        });
    }
})();
