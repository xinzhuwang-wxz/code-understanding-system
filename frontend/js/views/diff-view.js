/**
 * Diff View — displays git diff analysis results.
 * Fetches /api/impact or /api/diff and renders changed files, entities, impact summary.
 */
class DiffView {
    constructor() {
        this._containerId = 'diff-content';
    }

    async analyze(repoPath, commitRange) {
        const el = document.getElementById(this._containerId);
        if (!el) return;

        el.innerHTML = '<div class="spinner" style="margin:20px auto;"></div><p style="text-align:center;color:#8b949e;">Analyzing diff...</p>';

        try {
            const resp = await fetch('/api/impact', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_path: repoPath, commit_range: commitRange }),
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.statusText);
            const data = await resp.json();
            this._render(data);
        } catch (err) {
            el.innerHTML = `<div style="color:#f85149;padding:20px;">Diff analysis failed: ${err.message}</div>`;
        }
    }

    _render(data) {
        const el = document.getElementById(this._containerId);
        if (!el) return;

        const riskColor = {
            low: '#3fb950', medium: '#d29922', high: '#f85149', critical: '#f85149',
        };
        const riskBg = riskColor[data.risk_level] || '#8b949e';

        let html = `
            <div style="margin-bottom:16px;padding:12px;background:#161b22;border-radius:6px;border-left:3px solid ${riskBg};">
                <div style="font-size:14px;font-weight:bold;color:#c9d1d9;">
                    Risk: <span style="color:${riskBg};">${(data.risk_level || '?').toUpperCase()}</span>
                    &nbsp;·&nbsp; ${data.total_affected_files || 0} files affected
                </div>
                <div style="color:#8b949e;margin-top:4px;font-size:12px;">Range: ${data.commit_range || '?'}</div>
            </div>
        `;

        if (data.summary) {
            html += `<div style="margin-bottom:12px;padding:10px;background:#0d1117;border-radius:4px;font-size:13px;color:#c9d1d9;line-height:1.5;">${data.summary}</div>`;
        }

        // Changed files
        if (data.changed_files && data.changed_files.length > 0) {
            html += '<h3 style="color:#58a6ff;font-size:14px;margin:12px 0 6px;">Changed Files</h3>';
            html += '<div style="max-height:200px;overflow-y:auto;">';
            data.changed_files.forEach(f => {
                const icon = f.change_type === 'added' ? '+' : f.change_type === 'deleted' ? '-' : '~';
                const color = f.change_type === 'added' ? '#3fb950' : f.change_type === 'deleted' ? '#f85149' : '#d29922';
                html += `<div style="font-size:12px;padding:3px 0;color:#c9d1d9;font-family:monospace;">
                    <span style="color:${color};">${icon}</span> ${f.path || f}
                </div>`;
            });
            html += '</div>';
        }

        // Changed entities
        if (data.changed_entities && data.changed_entities.length > 0) {
            html += '<h3 style="color:#58a6ff;font-size:14px;margin:12px 0 6px;">Changed Entities</h3>';
            data.changed_entities.slice(0, 20).forEach(e => {
                html += `<div style="font-size:12px;padding:3px 0;color:#c9d1d9;">
                    <strong>${e.name || '?'}</strong>
                    <span style="color:#8b949e;"> (${e.type || '?'}) @ ${e.file || ''}</span>
                </div>`;
            });
        }

        // Dependents
        if (data.direct_dependents && data.direct_dependents.length > 0) {
            html += '<h3 style="color:#58a6ff;font-size:14px;margin:12px 0 6px;">Direct Dependents</h3>';
            data.direct_dependents.slice(0, 20).forEach(d => {
                html += `<div style="font-size:12px;padding:2px 0;color:#8b949e;font-family:monospace;">${d.name || d}</div>`;
            });
        }

        // Cascading impact
        if (data.cascading_impact && data.cascading_impact.length > 0) {
            html += '<details style="margin-top:8px;"><summary style="color:#d29922;cursor:pointer;font-size:13px;">Cascading Impact (${data.cascading_impact.length})</summary>';
            html += '<div style="max-height:150px;overflow-y:auto;margin-top:4px;">';
            data.cascading_impact.slice(0, 30).forEach(c => {
                html += `<div style="font-size:11px;padding:2px 0;color:#8b949e;">${c.file || c}: ${c.affected || c.reason || ''}</div>`;
            });
            html += '</div></details>';
        }

        if (data.diff_summary) {
            html += `<div style="margin-top:12px;padding:10px;background:#0d1117;border-radius:4px;font-size:12px;color:#8b949e;line-height:1.4;">${data.diff_summary}</div>`;
        }

        el.innerHTML = html;
    }

    /** Render entity impact (non-diff mode) */
    renderEntityImpact(data) {
        const el = document.getElementById(this._containerId);
        if (!el) return;

        let html = `<h3 style="color:#58a6ff;font-size:14px;margin:0 0 8px;">Entity Impact: ${data.node_id || '?'}</h3>`;
        html += `<div style="color:#8b949e;font-size:12px;margin-bottom:12px;">${data.total_affected || 0} nodes affected</div>`;

        if (data.dependencies && data.dependencies.length > 0) {
            html += '<h4 style="color:#c9d1d9;font-size:12px;">Depends On</h4>';
            data.dependencies.slice(0, 15).forEach(d => {
                html += `<div style="font-size:11px;padding:2px 0;">${d.label || d.id} @ ${d.file_path || ''}</div>`;
            });
        }
        if (data.dependents && data.dependents.length > 0) {
            html += '<h4 style="color:#c9d1d9;font-size:12px;margin-top:8px;">Dependents</h4>';
            data.dependents.slice(0, 15).forEach(d => {
                html += `<div style="font-size:11px;padding:2px 0;">${d.label || d.id} @ ${d.file_path || ''}</div>`;
            });
        }
        el.innerHTML = html;
    }
}

// Global instance
window.diffView = new DiffView();
