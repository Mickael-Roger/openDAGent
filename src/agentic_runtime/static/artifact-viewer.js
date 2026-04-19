// Shared artifact viewer modal logic.
// Expects the modal HTML to already be in the page (rendered by the template).

async function openArtifactModal(artifactId) {
    const modal   = document.getElementById('artifact-modal');
    const keyEl   = document.getElementById('artifact-modal-key');
    const metaEl  = document.getElementById('artifact-modal-meta');
    const bodyEl  = document.getElementById('artifact-modal-body');
    const dlLink  = document.getElementById('artifact-modal-download');

    bodyEl.innerHTML = '<p style="color:var(--muted);padding:20px">Loading…</p>';
    modal.hidden = false;

    let meta;
    try {
        const res = await fetch(`/api/artifacts/${artifactId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        meta = await res.json();
    } catch (e) {
        bodyEl.innerHTML = `<p style="color:#ffc4cf;padding:20px">Failed to load artifact: ${e.message}</p>`;
        return;
    }

    keyEl.textContent  = meta.artifact_key;
    metaEl.textContent = `${meta.type} · v${meta.version} · ${meta.status}`;
    dlLink.href        = `/api/artifacts/${artifactId}/content`;
    dlLink.download    = meta.file_name || meta.artifact_key;

    const contentUrl = `/api/artifacts/${artifactId}/content`;

    if (meta.preview_kind === 'image') {
        bodyEl.innerHTML = '';
        const img = document.createElement('img');
        img.src   = contentUrl;
        img.alt   = meta.artifact_key;
        img.style.cssText = 'max-width:100%;max-height:70vh;display:block;margin:auto;border-radius:8px';
        bodyEl.appendChild(img);

    } else if (meta.preview_kind === 'text') {
        try {
            const res = await fetch(contentUrl);
            const text = await res.text();
            const pre  = document.createElement('pre');
            pre.className = 'artifact-code-block';
            pre.textContent = text;
            bodyEl.innerHTML = '';
            bodyEl.appendChild(pre);
        } catch (e) {
            bodyEl.innerHTML = `<p style="color:#ffc4cf;padding:20px">Failed to load content: ${e.message}</p>`;
        }

    } else {
        bodyEl.innerHTML = `
            <div style="padding:32px;text-align:center">
                <p style="color:var(--muted);margin-bottom:16px">This artifact type cannot be previewed inline.</p>
                <a class="btn-primary" href="${contentUrl}" download="${meta.file_name || meta.artifact_key}">Download file</a>
            </div>`;
    }
}

// Wire up close button + backdrop click
document.addEventListener('DOMContentLoaded', () => {
    const modal    = document.getElementById('artifact-modal');
    if (!modal) return;
    const backdrop = modal.querySelector('.artifact-modal-backdrop');
    const closeBtn = document.getElementById('artifact-modal-close');

    const closeModal = () => { modal.hidden = true; };
    closeBtn?.addEventListener('click', closeModal);
    backdrop?.addEventListener('click', closeModal);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
});
