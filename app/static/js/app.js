
const $ = id => document.getElementById(id);

// FIX 1: Let FastAPI generate the conversation UUID.
// This works on both HTTP and HTTPS.
let conversationId =
    sessionStorage.getItem('atlas_conversation_id') || null;

const toast = msg => {
    const el = $('toast');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => el.style.display = 'none', 4000);
};

// FIX 2: New chat no longer calls crypto.randomUUID().
$('newChat').onclick = () => {
    conversationId = null;
    sessionStorage.removeItem('atlas_conversation_id');
    $('messages').replaceChildren();
    $('welcome').classList.remove('hidden');
    toast('Started a new conversation');
};

const api = async (path, options = {}) => {
    const res = await fetch(path, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        }
    });
    const raw = await res.text();
    let data = {};
    try { data = raw ? JSON.parse(raw) : {}; } catch (_) { /* show proxy/server text below */ }
    if (!res.ok) {
        const bodyMessage = data.detail || data.message || raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
        throw Error(`HTTP ${res.status}${bodyMessage ? `: ${bodyMessage.slice(0, 220)}` : ` ${res.statusText}`}`);
    }
    return data;
};

function view(name) {
    ['chat', 'docs', 'approval', 'admin'].forEach(x => {
        $(x + 'View').classList.toggle('hidden', x !== name);
        $(x + 'Tab').classList.toggle('active', x === name);
    });

    $('heading').textContent = {
        chat: 'Knowledge chat',
        docs: 'Documents',
        approval: 'Approvals',
        admin: 'Workspace metrics'
    }[name];

    if (name === 'docs') loadDocs();
    if (name === 'admin') loadMetrics();
    if (name === 'approval') loadApprovals();
}

$('chatTab').onclick = () => view('chat');
$('brandHome').onclick = e => { e.preventDefault(); view('chat'); };
$('docsTab').onclick = () => view('docs');
$('adminTab').onclick = () => view('admin');
$('approvalTab').onclick = () => view('approval');
$('refreshApprovals').onclick = loadApprovals;
$('refreshDocs').onclick = loadDocs;
$('refreshMetrics').onclick = loadMetrics;

function addMessage(content, kind, sources = []) {
    $('welcome').classList.add('hidden');
    const el = document.createElement('div');
    el.className = 'message ' + kind;
    el.textContent = content;

    if (sources.length) {
        const wrap = document.createElement('div');
        wrap.className = 'sources';

        sources.forEach(s => {
            const d = document.createElement('details');
            const title = document.createElement('summary');

            title.textContent =
                `[${s.index}] ${s.title} — page ${s.page}`;

            const excerpt = document.createElement('p');
            excerpt.textContent = s.excerpt;

            d.append(title, excerpt);
            wrap.append(d);
        });

        el.append(wrap);

        const feedback = document.createElement('div');
        feedback.className = 'feedback-form';
        let rating = 0;

        const helpful = document.createElement('button');
        helpful.type = 'button';
        helpful.textContent = '\u{1F44D} Helpful';
        helpful.setAttribute('aria-label', 'Thumbs up: helpful');
        const notHelpful = document.createElement('button');
        notHelpful.type = 'button';
        notHelpful.textContent = '\u{1F44E} Not helpful';
        notHelpful.setAttribute('aria-label', 'Thumbs down: not helpful');
        [helpful, notHelpful].forEach(button => {
            button.className = 'feedback-rating';
            button.onclick = () => {
                rating = button === helpful ? 1 : -1;
                helpful.classList.toggle('selected', rating === 1);
                notHelpful.classList.toggle('selected', rating === -1);
            };
        });

        const note = document.createElement('textarea');
        note.maxLength = 500;
        note.placeholder = 'Write a review (optional)';
        const submit = document.createElement('button');
        submit.type = 'button';
        submit.className = 'feedback-submit';
        submit.textContent = 'Send feedback';
        const status = document.createElement('span');
        status.className = 'feedback-status';

        submit.onclick = async () => {
            submit.disabled = true;
            try {
                await api('/api/feedback', {
                    method: 'POST',
                    body: JSON.stringify({ rating, note: note.value.trim() })
                });
                status.textContent = 'Feedback saved';
                helpful.disabled = notHelpful.disabled = note.disabled = true;
            } catch (e) {
                submit.disabled = false;
                status.textContent = e.message;
            }
        };

        feedback.append(helpful, notHelpful, note, submit, status);
        el.append(feedback);
    }

    $('messages').append(el);

    $('messages').scrollTo({
        top: $('messages').scrollHeight,
        behavior: 'smooth'
    });
}

// CHAT
$('chatForm').onsubmit = async e => {
    e.preventDefault();

    const q = $('question').value.trim();
    if (!q) return;

    addMessage(q, 'user');
    $('question').value = '';

    addMessage('Searching approved documents...', 'assistant');

    const loading = $('messages').lastElementChild;

    try {
        const r = await api('/api/chat', {
            method: 'POST',
            body: JSON.stringify({
                question: q,
                conversation_id: conversationId
            })
        });

        // FIX 3: Store the UUID returned by FastAPI.
        if (r.conversation_id) {
            conversationId = r.conversation_id;

            sessionStorage.setItem(
                'atlas_conversation_id',
                conversationId
            );
        }

        loading.remove();
        addMessage(r.answer, 'assistant', r.sources || []);

        if (r.status === 'pending_approval') {
            const btn = document.createElement('button');

            btn.textContent = 'Review approval request →';
            btn.onclick = () => view('approval');

            $('messages').lastElementChild.append(btn);
        }
    } catch (err) {
        loading.textContent = err.message;
        console.error('Chat request failed:', err);
    }
};

// Suggested questions
document.querySelectorAll('[data-q]').forEach(b => {
    b.onclick = () => {
        $('question').value = b.dataset.q;
        $('question').focus();
    };
});

// DOCUMENTS
async function loadDocs() {
    try {
        const r = await api('/api/documents');
        const root = $('docs');
        root.replaceChildren();
        if (!r.documents.length) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No documents yet. Upload an approved file to build your knowledge library.';
            root.append(empty);
            return;
        }
        for (const d of r.documents) {
            const row = document.createElement('article');
            row.className = 'document-row';
            const icon = document.createElement('span');
            icon.className = 'document-icon';
            icon.textContent = (d.title.split('.').pop() || 'DOC').toUpperCase().slice(0, 3);
            const info = document.createElement('span');
            info.className = 'document-info';
            const title = document.createElement('strong');
            title.textContent = d.title;
            const version = document.createElement('small');
            version.textContent = `Version ${d.current_version}`;
            info.append(title, version);
            const status = document.createElement('span');
            status.className = `doc-status ${String(d.status).toLowerCase()}`;
            status.textContent = d.status;
            row.append(icon, info, status);
            root.append(row);
        }
    } catch (e) {
        $('docs').textContent = e.message;
    }
}

document.querySelector('#file').addEventListener('change', e => {
    const file = e.target.files[0];
    const label = e.target.closest('.file-picker');
    if (file) label.firstChild.textContent = file.name;
});

// METRICS
async function loadMetrics() {
    try {
        const [metrics, guardrails] = await Promise.all([
            api('/api/admin/metrics'),
            api('/api/admin/guardrails')
        ]);
        const renderRows = (rootId, rows, emptyText) => {
            const root = $(rootId);
            root.replaceChildren();
            if (!rows.length) {
                const empty = document.createElement('span');
                empty.className = 'empty-state';
                empty.textContent = emptyText;
                root.append(empty);
                return;
            }
            for (const item of rows) {
                const row = document.createElement('div');
                row.className = 'metric-row';
                const label = document.createElement('span');
                label.textContent = item.label;
                const value = document.createElement('strong');
                value.textContent = item.value;
                row.append(label, value);
                root.append(row);
            }
        };
        const documentRows = metrics.documents || [];
        const total = documentRows.reduce((sum, row) => sum + Number(row.total || 0), 0);
        renderRows('metrics', [
            { label: 'Total documents', value: total },
            ...documentRows.map(row => ({ label: `${row.status} documents`, value: row.total }))
        ], 'No documents have been uploaded yet.');
        renderRows('guardrailMetrics', (guardrails.events || []).map(row => ({
            label: String(row.action).replaceAll('_', ' '),
            value: row.total
        })), 'No guardrail events recorded.');
    } catch (e) {
        $('metrics').textContent = e.message;
    }
}

// DOCUMENT UPLOAD
$('uploadForm').onsubmit = async e => {
    e.preventDefault();
    const f = $('file').files[0];
    if (!f) return;
    if (f.size > 10485760) {
        toast('Maximum file size is 10 MB');
        return;
    }

    let stage = 'Request an upload URL';
    const submit = e.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
        const r = await api('/api/documents/uploads', {
            method: 'POST',
            body: JSON.stringify({ filename: f.name })
        });

        stage = 'Upload file to S3';
        const up = await fetch(r.upload_url, {
            method: 'PUT',
            headers: r.headers,
            body: f
        });
        if (!up.ok) {
            const body = await up.text();
            throw Error(`S3 returned HTTP ${up.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
        }

        stage = 'Process and index document';
        toast('Uploaded. Processing the document...');
        await api('/api/documents/complete', {
            method: 'POST',
            body: JSON.stringify({ document_id: r.document_id })
        });
        toast('Document indexed and ready.');
        $('uploadForm').reset();
        const fileLabel = document.querySelector('.file-picker');
        if (fileLabel) fileLabel.firstChild.textContent = 'Choose a file';
        await loadDocs();
    } catch (err) {
        toast(`${stage}: ${err.message}`);
        console.error(`${stage} failed:`, err);
        loadDocs();
    } finally {
        submit.disabled = false;
    }
};

// APPROVALS
async function loadApprovals() {
    const root = $('approvalList');
    root.textContent = 'Loading approvals...';

    try {
        const response = await api('/api/approvals');

        if (!Array.isArray(response.approvals)) {
            throw Error(
                'Unexpected API response: approvals list missing'
            );
        }

        const approvals = response.approvals;

        const pending = approvals.filter(
            a => String(a.status).trim().toLowerCase() === 'pending'
        ).length;

        $('approvalSummary').textContent =
            `${pending} pending · ${approvals.length} total (most recent 100)`;

        root.replaceChildren();

        if (!approvals.length) {
            root.textContent =
                'No approval requests yet. Try asking the chatbot to delete a document.';
            return;
        }

        for (const a of approvals) {
            const card = document.createElement('article');
            card.className = 'approval-card';

            const header = document.createElement('div');
            header.className = 'approval-header';

            const title = document.createElement('strong');
            title.textContent = a.question;

            const badge = document.createElement('span');
            badge.className = 'approval-status ' + a.status;
            badge.textContent = a.status;

            header.append(title, badge);
            card.append(header);

            const meta = document.createElement('p');
            meta.textContent =
                `Requested by ${a.requester} · ${new Date(a.created_at).toLocaleString()}`;

            card.append(meta);

            if (
                String(a.status).trim().toLowerCase() === 'pending'
            ) {
                const actions = document.createElement('div');
                actions.className = 'approval-actions';

                for (const [label, approve] of [
                    ['Approve', true],
                    ['Reject', false]
                ]) {
                    const button = document.createElement('button');
                    button.textContent = label;

                    button.className = approve
                        ? 'approve-button'
                        : 'reject-button';

                    button.onclick = async () => {
                        const decision = approve ? 'approve' : 'reject';

                        if (!confirm(
                            `Are you sure you want to ${decision} this request? This records a decision only and does not execute the requested action.`
                        )) {
                            return;
                        }

                        button.disabled = true;

                        try {
                            const r = await api(
                                `/api/approvals/${encodeURIComponent(a.id)}/decision`,
                                {
                                    method: 'POST',
                                    body: JSON.stringify({ approve })
                                }
                            );

                            toast(
                                `Request ${r.status}. No action executed.`
                            );

                            await loadApprovals();

                        } catch (e) {
                            toast(e.message);
                            button.disabled = false;
                        }
                    };

                    actions.append(button);
                }

                card.append(actions);

            } else {
                const info = document.createElement('p');

                info.textContent =
                    `Reviewed by ${a.reviewer || 'unknown'} · ${a.reviewed_at ? new Date(a.reviewed_at).toLocaleString() : ''} · No action executed`;

                card.append(info);
            }

            root.append(card);
        }

    } catch (e) {
        root.textContent =
            `Unable to load approvals: ${e.message}. Check /api/approvals and your Uvicorn terminal.`;

        console.error('Approvals loading failed:', e);
    }
}
