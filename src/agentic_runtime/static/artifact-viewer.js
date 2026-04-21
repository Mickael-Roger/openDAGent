// ── JSON tree renderer ──────────────────────────────────────────────────────
// Renders a collapsible, syntax-highlighted JSON tree using <details>/<summary>.

function renderJsonTree(value, maxDepth) {
    maxDepth = maxDepth === undefined ? 12 : maxDepth;

    function esc(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function render(val, depth) {
        if (depth > maxDepth) return '<span class="jt-str">"..."</span>';

        if (val === null) return '<span class="jt-null">null</span>';
        if (typeof val === 'boolean') return '<span class="jt-bool">' + val + '</span>';
        if (typeof val === 'number') return '<span class="jt-num">' + val + '</span>';
        if (typeof val === 'string') return '<span class="jt-str">"' + esc(val) + '"</span>';

        if (Array.isArray(val)) {
            if (val.length === 0) return '<span class="jt-brace">[]</span>';
            var html = '<details' + (depth < 2 ? ' open' : '') + '>';
            html += '<summary><span class="jt-brace">[</span> <span class="jt-count">' + val.length + ' items</span></summary>';
            for (var i = 0; i < val.length; i++) {
                html += '<div class="jt-indent">' + render(val[i], depth + 1);
                html += (i < val.length - 1 ? ',' : '') + '</div>';
            }
            html += '<span class="jt-brace">]</span></details>';
            return html;
        }

        if (typeof val === 'object') {
            var keys = Object.keys(val);
            if (keys.length === 0) return '<span class="jt-brace">{}</span>';
            var html = '<details' + (depth < 2 ? ' open' : '') + '>';
            html += '<summary><span class="jt-brace">{</span> <span class="jt-count">' + keys.length + ' keys</span></summary>';
            for (var i = 0; i < keys.length; i++) {
                html += '<div class="jt-indent"><span class="jt-key">"' + esc(keys[i]) + '"</span>: '
                    + render(val[keys[i]], depth + 1)
                    + (i < keys.length - 1 ? ',' : '') + '</div>';
            }
            html += '<span class="jt-brace">}</span></details>';
            return html;
        }

        return esc(String(val));
    }

    return '<div class="json-tree">' + render(value, 0) + '</div>';
}

// Try to detect and parse JSON, then render appropriately
function renderArtifactContent(text) {
    var trimmed = text.trim();
    // Attempt JSON parse
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
        try {
            var parsed = JSON.parse(trimmed);
            return renderJsonTree(parsed);
        } catch (e) {
            // Not valid JSON, fall through to text display
        }
    }
    // Plain text
    var pre = document.createElement('pre');
    pre.className = 'artifact-code-block';
    pre.textContent = text;
    return pre.outerHTML;
}


// ── Artifact modal ──────────────────────────────────────────────────────────

async function openArtifactModal(artifactId) {
    var modal   = document.getElementById('artifact-modal');
    var keyEl   = document.getElementById('artifact-modal-key');
    var metaEl  = document.getElementById('artifact-modal-meta');
    var bodyEl  = document.getElementById('artifact-modal-body');
    var dlLink  = document.getElementById('artifact-modal-download');

    bodyEl.innerHTML = '<p style="color:var(--ash);padding:20px">Loading...</p>';
    modal.hidden = false;

    var meta;
    try {
        var res = await fetch('/api/artifacts/' + artifactId);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        meta = await res.json();
    } catch (e) {
        bodyEl.innerHTML = '<p style="color:var(--red-text);padding:20px">Failed to load artifact: ' + e.message + '</p>';
        return;
    }

    keyEl.textContent  = meta.artifact_key;
    metaEl.textContent = meta.type + ' \u00B7 v' + meta.version + ' \u00B7 ' + meta.status;
    dlLink.href        = '/api/artifacts/' + artifactId + '/content';
    dlLink.download    = meta.file_name || meta.artifact_key;

    var contentUrl = '/api/artifacts/' + artifactId + '/content';

    if (meta.preview_kind === 'image') {
        bodyEl.innerHTML = '';
        var img = document.createElement('img');
        img.src   = contentUrl;
        img.alt   = meta.artifact_key;
        img.style.cssText = 'max-width:100%;max-height:70vh;display:block;margin:auto;border-radius:8px';
        bodyEl.appendChild(img);

    } else if (meta.preview_kind === 'text') {
        try {
            var res = await fetch(contentUrl);
            var text = await res.text();
            bodyEl.innerHTML = renderArtifactContent(text);
        } catch (e) {
            bodyEl.innerHTML = '<p style="color:var(--red-text);padding:20px">Failed to load content: ' + e.message + '</p>';
        }

    } else {
        bodyEl.innerHTML =
            '<div style="padding:32px;text-align:center">' +
            '<p style="color:var(--ash);margin-bottom:16px">This artifact type cannot be previewed inline.</p>' +
            '<a class="btn-primary" href="' + contentUrl + '" download="' + (meta.file_name || meta.artifact_key) + '">Download file</a>' +
            '</div>';
    }
}

// Wire up close button + backdrop
document.addEventListener('DOMContentLoaded', function() {
    var modal = document.getElementById('artifact-modal');
    if (!modal) return;
    var backdrop = modal.querySelector('.artifact-modal-backdrop');
    var closeBtn = document.getElementById('artifact-modal-close');

    function closeModal() { modal.hidden = true; }
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (backdrop) backdrop.addEventListener('click', closeModal);
    document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeModal(); });
});
