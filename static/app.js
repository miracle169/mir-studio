/* ── Mir Studio — Frontend App ───────────────────────────────────────────── */

'use strict';

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  activeTab: 'create',
  platforms: new Set(['linkedin']),
  storyContext: null,      // { id, title }
  intelContext: null,      // { text, label }
  imageDescription: null,
  currentItemId: null,     // item ID of last generated content
  currentGenerated: {},    // { linkedin, newsletter, instagram }
  outputTab: null,         // currently shown output tab
  contentFilter: { linkedin: 'all', newsletter: 'all', instagram: 'all' },
  queueFilter: 'all',
  pendingPostItemId: null, // for the "did you post?" flow
  pendingPostPlatform: null,
  allContent: [],          // cached content list
  rawIdeasSaveTimer: null,
  recognition: null,       // SpeechRecognition instance
  isRecording: false,
};

// ── API helpers ────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || 'Request failed');
  }
  return res.json();
}

const GET  = (path)        => api('GET',    path, null);
const POST = (path, body)  => api('POST',   path, body);
const PUT  = (path, body)  => api('PUT',    path, body);
const DEL  = (path)        => api('DELETE', path, null);

// ── Toast ──────────────────────────────────────────────────────────────────
let toastTimer;
function toast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = 'none'; }, duration);
}

// ── XP flash ──────────────────────────────────────────────────────────────
function xpFlash(text = '+1 ⚡') {
  const el = document.createElement('div');
  el.className = 'xp-flash';
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 900);
}

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(tabName) {
  state.activeTab = tabName;

  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));

  document.getElementById(`tab-${tabName}`).classList.add('active');
  document.querySelector(`.nav-btn[data-tab="${tabName}"]`).classList.add('active');

  if (tabName === 'create')   loadContentSections();
  if (tabName === 'intel')    { loadIntel(); loadCreators(); }
  if (tabName === 'library')  { loadStories(); loadRawIdeas(); }
  if (tabName === 'queue')    loadQueue();
  if (tabName === 'settings') loadSettings();
}

// ── Platform pills ─────────────────────────────────────────────────────────
function setupPlatformPills() {
  document.querySelectorAll('.platform-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      const p = pill.dataset.platform;
      if (state.platforms.has(p)) {
        if (state.platforms.size === 1) return; // at least 1 must be active
        state.platforms.delete(p);
        pill.classList.remove('active');
      } else {
        state.platforms.add(p);
        pill.classList.add('active');
      }
      updateGenerateBtn();
    });
  });
}

// ── Tone pills ─────────────────────────────────────────────────────────────
function setupTonePills() {
  document.querySelectorAll('.tone-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.tone-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      state.tone = pill.dataset.tone;
    });
  });
}

// ── Voice capture ──────────────────────────────────────────────────────────
function setupVoiceCapture() {
  const btn = document.getElementById('voice-btn');
  const label = document.getElementById('voice-label');
  const textarea = document.getElementById('thought-input');

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    btn.title = 'Voice not supported in this browser. Use Chrome or Safari.';
    btn.style.opacity = '.5';
    return;
  }

  state.recognition = new SpeechRecognition();
  state.recognition.continuous = true;
  state.recognition.interimResults = true;
  state.recognition.lang = 'en-US';

  let finalTranscript = '';

  state.recognition.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i].transcript;
      if (e.results[i].isFinal) {
        finalTranscript += t + ' ';
      } else {
        interim = t;
      }
    }
    textarea.value = (finalTranscript + interim).trim();
    updateCharCount();
    updateGenerateBtn();
    scheduleRawIdeaSave('voice');
  };

  state.recognition.onerror = (e) => {
    if (e.error !== 'aborted') toast('Mic error: ' + e.error);
    stopRecording();
  };

  state.recognition.onend = () => {
    if (state.isRecording) state.recognition.start(); // restart for continuous
  };

  btn.addEventListener('click', () => {
    if (state.isRecording) {
      stopRecording();
    } else {
      finalTranscript = textarea.value ? textarea.value + ' ' : '';
      startRecording();
    }
  });

  function startRecording() {
    state.isRecording = true;
    state.recognition.start();
    btn.classList.add('recording');
    document.getElementById('voice-icon').textContent = '⏹️';
    label.textContent = 'Tap to stop';
    xpFlash('⚡ Capturing');
  }

  function stopRecording() {
    state.isRecording = false;
    state.recognition.stop();
    btn.classList.remove('recording');
    document.getElementById('voice-icon').textContent = '🎙️';
    label.textContent = 'Tap to speak';
    if (textarea.value.trim()) scheduleRawIdeaSave('voice');
  }
}

// ── Textarea & char count ──────────────────────────────────────────────────
function updateCharCount() {
  const textarea = document.getElementById('thought-input');
  const count = textarea.value.length;
  document.getElementById('char-count').textContent = `${count} / 2000`;
}

function updateGenerateBtn() {
  const textarea = document.getElementById('thought-input');
  const btn = document.getElementById('generate-btn');
  btn.disabled = textarea.value.trim().length < 5;
}

// ── Auto-save raw ideas (throttled) ───────────────────────────────────────
function scheduleRawIdeaSave(source = 'text') {
  clearTimeout(state.rawIdeasSaveTimer);
  state.rawIdeasSaveTimer = setTimeout(async () => {
    const thought = document.getElementById('thought-input').value.trim();
    if (thought.length < 5) return;
    await POST('/api/raw-ideas', {
      thought,
      source,
      platforms: [...state.platforms],
    });
    // update badge
    loadRawIdeasCount();
  }, 2000);
}

async function loadRawIdeasCount() {
  try {
    const data = await GET('/api/stats');
    const count = data.raw_ideas_count || 0;
    const badge = document.getElementById('library-badge');
    if (count > 0) {
      badge.textContent = count > 99 ? '99+' : count;
      badge.style.display = 'flex';
    }
    document.getElementById('raw-ideas-count').textContent = `${count} ideas`;
  } catch {}
}

// ── Photo upload ───────────────────────────────────────────────────────────
function setupPhotoUpload() {
  const input = document.getElementById('photo-input');
  const chip = document.getElementById('image-context-chip');
  const chipText = document.getElementById('image-context-text');
  const removeBtn = document.getElementById('remove-image');

  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;

    chipText.textContent = 'Describing image...';
    chip.style.display = 'flex';

    const formData = new FormData();
    formData.append('image', file);

    try {
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      if (data.description) {
        state.imageDescription = data.description;
        chipText.textContent = data.description.substring(0, 60) + (data.description.length > 60 ? '…' : '');
      } else {
        chipText.textContent = 'Could not describe image';
      }
    } catch {
      chip.style.display = 'none';
      toast('Image upload failed');
    }
  });

  removeBtn.addEventListener('click', () => {
    state.imageDescription = null;
    chip.style.display = 'none';
    input.value = '';
  });
}

// ── Story / Intel context chips ────────────────────────────────────────────
function setupContextChips() {
  // Story chip → open story sheet
  document.getElementById('story-chip').addEventListener('click', () => {
    openStorySheet();
  });

  document.getElementById('remove-story').addEventListener('click', () => {
    state.storyContext = null;
    document.getElementById('story-context-display').style.display = 'none';
  });

  // Intel chip → open intel sheet
  document.getElementById('intel-chip').addEventListener('click', () => {
    openIntelSheet();
  });

  document.getElementById('remove-intel').addEventListener('click', () => {
    state.intelContext = null;
    document.getElementById('intel-context-display').style.display = 'none';
  });
}

async function openStorySheet() {
  const overlay = document.getElementById('story-sheet-overlay');
  const list = document.getElementById('story-picker-list');
  list.innerHTML = '<div class="empty-state">Loading...</div>';
  overlay.style.display = 'flex';

  try {
    const stories = await GET('/api/stories');
    list.innerHTML = '';
    if (!stories.length) {
      list.innerHTML = '<div class="empty-state">No stories yet. Add one in Library.</div>';
      return;
    }
    stories.forEach(story => {
      const item = document.createElement('div');
      item.className = 'story-picker-item';
      item.innerHTML = `
        <div class="picker-item-title">${story.title}</div>
        <div class="picker-item-sub">${story.story_snippet.substring(0, 80)}…</div>
      `;
      item.addEventListener('click', () => {
        state.storyContext = { id: story.id, title: story.title };
        document.getElementById('story-context-label').textContent = story.title;
        document.getElementById('story-context-display').style.display = 'flex';
        overlay.style.display = 'none';
      });
      list.appendChild(item);
    });
  } catch {
    list.innerHTML = '<div class="empty-state">Could not load stories.</div>';
  }
}

async function openIntelSheet() {
  const overlay = document.getElementById('intel-sheet-overlay');
  const list = document.getElementById('intel-picker-list');
  list.innerHTML = '<div class="empty-state">Loading...</div>';
  overlay.style.display = 'flex';

  try {
    const data = await GET('/api/intel');
    list.innerHTML = '';

    const items = [
      ...(data.linkedin_intel||data.debates||[]).map(d => ({ label: `💼 ${d.topic}`, text: `LINKEDIN TRENDING: ${d.topic}. ${d.why_trending||d.sides||''}. Hook: ${d.hook||''}` })),
      ...(data.instagram_intel||data.trending||[]).map(d => ({ label: `📸 ${d.topic}`, text: `INSTAGRAM TRENDING: ${d.topic}. ${d.why_trending||''}. Hook: ${d.hook||''}` })),
      ...(data.content_gaps||data.gaps||[]).map(g => ({ label: `💡 ${g.angle}`, text: `GAP: ${g.angle}. ${g.why}` })),
      ...(data.suggested_angles||[]).map(a => ({ label: `✏️ ${(a.idea||a).substring(0, 50)}`, text: a.idea||a })),
    ];

    if (!items.length) {
      list.innerHTML = '<div class="empty-state">No intel yet. Refresh in the Intel tab first.</div>';
      return;
    }

    items.forEach(item => {
      const el = document.createElement('div');
      el.className = 'intel-picker-item';
      el.innerHTML = `<div class="picker-item-title">${item.label}</div>`;
      el.addEventListener('click', () => {
        state.intelContext = { text: item.text, label: item.label };
        document.getElementById('intel-context-label').textContent = item.label.substring(0, 50) + '…';
        document.getElementById('intel-context-display').style.display = 'flex';
        overlay.style.display = 'none';
      });
      list.appendChild(el);
    });
  } catch {
    list.innerHTML = '<div class="empty-state">Could not load intel.</div>';
  }
}

// ── Generation ─────────────────────────────────────────────────────────────
async function generate() {
  const thought = document.getElementById('thought-input').value.trim();
  if (!thought) return;

  const btn = document.getElementById('generate-btn');
  const btnText = document.getElementById('generate-btn-text');
  const spinner = document.getElementById('generate-spinner');

  btn.disabled = true;
  btnText.style.display = 'none';
  spinner.style.display = 'block';

  try {
    // Quick API key pre-check
    const status = await GET('/api/status').catch(() => null);
    if (status && !status.has_api_key) {
      throw new Error('No API key set. Go to ⚙️ Settings and add your Anthropic API key.');
    }

    const result = await POST('/api/generate', {
      thought,
      platforms: [...state.platforms],
      story_id: state.storyContext?.id || null,
      intel_context: state.intelContext?.text || '',
      image_description: state.imageDescription || '',
    });

    // Collect per-platform errors
    const errMessages = [];
    if (result.linkedin_error) errMessages.push(`LinkedIn: ${result.linkedin_error}`);
    if (result.newsletter_error) errMessages.push(`Newsletter: ${result.newsletter_error}`);
    if (result.instagram_error) errMessages.push(`Instagram: ${result.instagram_error}`);

    // If ALL platforms errored, treat it as a full failure
    if (errMessages.length > 0 && errMessages.length >= [...state.platforms].length) {
      throw new Error(errMessages.join(' | '));
    }

    // Show partial errors as warnings (not fatal)
    if (errMessages.length > 0) {
      toast('⚠️ ' + errMessages.join(' | '), 5000);
    }

    state.currentItemId = result.item_id;
    state.currentGenerated = {
      linkedin: result.linkedin || '',
      newsletter: result.newsletter || '',
      instagram: result.instagram || '',
    };

    renderOutputSection();
    xpFlash('✨ Generated');
    loadContentSections();

  } catch (err) {
    const msg = err.message || 'Unknown error';
    // If it looks like an API key issue, nudge the user to Settings
    if (msg.toLowerCase().includes('api key') || msg.toLowerCase().includes('authentication') || msg.toLowerCase().includes('401')) {
      toast('❌ API key error — go to ⚙️ Settings to verify your key', 6000);
    } else {
      toast('❌ ' + msg, 5000);
    }
    console.error('Generate error:', msg);
  } finally {
    btn.disabled = false;
    btnText.style.display = 'block';
    spinner.style.display = 'none';
    updateGenerateBtn();
  }
}

// ── Output section rendering ───────────────────────────────────────────────
function renderOutputSection() {
  const section = document.getElementById('output-section');
  const tabsEl = document.getElementById('output-tabs');
  const bodyEl = document.getElementById('output-body');

  const platforms = [...state.platforms].filter(p => state.currentGenerated[p]);
  if (!platforms.length) return;

  // Render tabs
  tabsEl.innerHTML = '';
  platforms.forEach((p, i) => {
    const btn = document.createElement('button');
    btn.className = 'output-tab' + (i === 0 ? ' active' : '');
    btn.dataset.platform = p;
    btn.textContent = { linkedin: '🔗 LinkedIn', newsletter: '📧 Newsletter', instagram: '📸 Instagram' }[p];
    btn.addEventListener('click', () => {
      document.querySelectorAll('.output-tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      state.outputTab = p;
      renderOutputBody(p);
    });
    tabsEl.appendChild(btn);
  });

  state.outputTab = platforms[0];
  renderOutputBody(platforms[0]);
  section.style.display = 'block';

  // Scroll to output
  section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderOutputBody(platform) {
  const bodyEl = document.getElementById('output-body');
  const content = state.currentGenerated[platform] || '';
  const wordCount = content.split(/\s+/).filter(Boolean).length;

  bodyEl.innerHTML = `
    <textarea class="output-content-area" id="output-textarea-${platform}">${content}</textarea>
    <div class="output-word-count">${wordCount} words</div>
  `;

  const textarea = bodyEl.querySelector('textarea');
  textarea.addEventListener('input', () => {
    state.currentGenerated[platform] = textarea.value;
    const wc = textarea.value.split(/\s+/).filter(Boolean).length;
    bodyEl.querySelector('.output-word-count').textContent = wc + ' words';
  });

  // Re-render action buttons
  const actionsEl = document.querySelector('.output-actions');
  actionsEl.innerHTML = `
    <button class="copy-post-btn" id="copy-post-btn-${platform}">
      📋 Copy &amp; Post
    </button>
    <button class="btn-outline" id="save-draft-btn">💾 Save Draft</button>
    <button class="btn-discard" id="discard-btn" title="Discard and tell us why">🗑 Discard</button>
  `;

  document.getElementById(`copy-post-btn-${platform}`).addEventListener('click', () => {
    copyAndConfirm(platform);
  });

  document.getElementById('save-draft-btn').addEventListener('click', async () => {
    if (state.currentItemId) {
      const updates = {};
      [...state.platforms].forEach(p => {
        const ta = document.getElementById(`output-textarea-${p}`);
        if (ta) {
          const key = p === 'linkedin' ? 'linkedin_content' : p === 'newsletter' ? 'newsletter_content' : 'instagram_script';
          updates[key] = ta.value;
        }
      });
      await PUT(`/api/content/${state.currentItemId}`, updates);
    }
    toast('💾 Saved as draft');
    loadContentSections();
  });

  document.getElementById('discard-btn').addEventListener('click', async () => {
    if (!state.currentItemId) return;
    const reason = prompt('Why are you discarding this? (optional — helps the app learn your taste)\n\nExamples: "too generic", "wrong tone", "sounds like AI", "too long"') ?? '';
    await POST(`/api/content/${state.currentItemId}/discard`, { reason, pattern_notes: reason });
    document.getElementById('output-section').style.display = 'none';
    state.currentItemId = null;
    state.currentGenerated = {};
    toast('🗑 Discarded — noted for next time');
    loadContentSections();
  });
}

// ── Copy & confirm ─────────────────────────────────────────────────────────
function copyAndConfirm(platform) {
  const content = state.currentGenerated[platform] || '';
  if (!content) return;

  navigator.clipboard.writeText(content).then(() => {
    state.pendingPostItemId = state.currentItemId;
    state.pendingPostPlatform = platform;
    document.getElementById('post-confirm-overlay').style.display = 'flex';
  }).catch(() => {
    // Fallback for non-HTTPS
    const ta = document.createElement('textarea');
    ta.value = content;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    state.pendingPostItemId = state.currentItemId;
    state.pendingPostPlatform = platform;
    document.getElementById('post-confirm-overlay').style.display = 'flex';
  });
}

function setupPostConfirm() {
  document.getElementById('post-confirm-yes').addEventListener('click', async () => {
    document.getElementById('post-confirm-overlay').style.display = 'none';
    if (!state.pendingPostItemId) return;

    try {
      const result = await POST(`/api/content/${state.pendingPostItemId}/post`, {
        platforms: state.pendingPostPlatform ? [state.pendingPostPlatform] : [...state.platforms],
      });

      launchConfetti();
      updateStreak(result.streak || 0);
      xpFlash('🎉 Posted!');
      toast('✅ Marked as posted! Keep going.', 3000);
      loadContentSections();
      loadStats();
    } catch (err) {
      toast('Could not mark as posted: ' + err.message);
    }
  });

  document.getElementById('post-confirm-no').addEventListener('click', () => {
    document.getElementById('post-confirm-overlay').style.display = 'none';
    toast('📋 Copied. Post it when you\'re ready!');
  });
}

// ── Confetti ───────────────────────────────────────────────────────────────
function launchConfetti() {
  const canvas = document.getElementById('confetti-canvas');
  const ctx = canvas.getContext('2d');
  canvas.style.display = 'block';
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;

  const colors = ['#007AFF', '#34C759', '#FF9500', '#FF3B30', '#AF52DE', '#FFD60A'];
  const pieces = Array.from({ length: 80 }, () => ({
    x: Math.random() * canvas.width,
    y: Math.random() * canvas.height - canvas.height,
    w: Math.random() * 10 + 5,
    h: Math.random() * 6 + 3,
    color: colors[Math.floor(Math.random() * colors.length)],
    speed: Math.random() * 3 + 2,
    angle: Math.random() * 360,
    spin: (Math.random() - .5) * 4,
  }));

  let frame = 0;
  const maxFrames = 90;

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    pieces.forEach(p => {
      ctx.save();
      ctx.translate(p.x + p.w / 2, p.y + p.h / 2);
      ctx.rotate((p.angle * Math.PI) / 180);
      ctx.fillStyle = p.color;
      ctx.globalAlpha = Math.max(0, 1 - frame / maxFrames);
      ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
      p.y += p.speed;
      p.angle += p.spin;
    });
    frame++;
    if (frame < maxFrames) requestAnimationFrame(draw);
    else {
      canvas.style.display = 'none';
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }

  requestAnimationFrame(draw);
}

// ── Stats / Streak ─────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const data = await GET('/api/stats');
    updateStreak(data.streak_count || 0);

    document.getElementById('queue-streak').textContent = data.streak_count || 0;
    document.getElementById('queue-this-week').textContent = data.weekly_posted || 0;
    document.getElementById('queue-total-li').textContent = data.total_linkedin || 0;
    document.getElementById('queue-total-ig').textContent = data.total_instagram || 0;

    document.getElementById('info-stories').textContent = data.stories_count || '—';
    document.getElementById('info-ideas').textContent = data.raw_ideas_count || '—';
    const total = (data.total_linkedin || 0) + (data.total_newsletter || 0) + (data.total_instagram || 0);
    document.getElementById('info-posts').textContent = total;

    loadRawIdeasCount();
  } catch {}
}

function updateStreak(count) {
  document.getElementById('streak-num').textContent = count;
  const badge = document.getElementById('streak-badge');
  badge.classList.toggle('active', count > 0);
}

// ── Content sections (Create tab, Zone 2) ─────────────────────────────────
async function loadContentSections() {
  try {
    const items = await GET('/api/content');
    state.allContent = items;
    renderContentSection('linkedin', 'linkedin-list', 'li-count', 'linkedin post');
    renderContentSection('newsletter', 'newsletter-list', 'nl-count', 'newsletter draft');
    renderContentSection('instagram', 'instagram-list', 'ig-count', 'reel script');
  } catch {}
}

function renderContentSection(platform, listId, countId, label) {
  const list = document.getElementById(listId);
  const countEl = document.getElementById(countId);
  const filter = state.contentFilter[platform];

  let items = state.allContent.filter(item => {
    try { return JSON.parse(item.platforms || '[]').includes(platform); } catch { return false; }
  });

  const total = items.length;
  countEl.textContent = `${total} ${label}${total !== 1 ? 's' : ''}`;

  if (filter !== 'all') items = items.filter(i => i.status === filter);

  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = `<div class="empty-state">No ${label}s ${filter !== 'all' ? `with status "${filter}"` : 'yet'}.</div>`;
    return;
  }

  items.forEach(item => {
    const contentKey = platform === 'linkedin' ? 'linkedin_content'
      : platform === 'newsletter' ? 'newsletter_content'
      : 'instagram_script';
    const content = item[contentKey] || '';
    const preview = content.substring(0, 120);
    const date = new Date(item.created_at).toLocaleDateString('en', { month: 'short', day: 'numeric' });

    const card = document.createElement('div');
    card.className = 'content-card';
    card.innerHTML = `
      <div class="card-meta">
        <span class="card-date">${date}</span>
        <span class="status-pill ${item.status}">${item.status}</span>
      </div>
      <div class="card-preview">${preview}${content.length > 120 ? '…' : ''}</div>
      <div class="card-actions">
        <button class="card-btn" data-action="expand" data-id="${item.id}" data-platform="${platform}">View</button>
        ${item.status !== 'posted' ? `<button class="card-btn green" data-action="mark-posted" data-id="${item.id}" data-platform="${platform}">✅ Posted</button>` : ''}
        ${item.status === 'posted' ? `<button class="card-btn" data-action="repurpose" data-id="${item.id}">♻️ Repurpose</button>` : ''}
        ${item.status !== 'posted' ? `<button class="card-btn red" data-action="discard-card" data-id="${item.id}">🗑 Discard</button>` : ''}
      </div>
    `;

    card.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const action = btn.dataset.action;
        const id = btn.dataset.id;
        const p = btn.dataset.platform;
        if (action === 'expand') openContentModal(id, p);
        if (action === 'mark-posted') markPostedFromCard(id, p);
        if (action === 'repurpose') repurposeItem(id, p);
        if (action === 'discard-card') discardFromCard(id);
      });
    });

    list.appendChild(card);
  });
}

// Section filter chips
function setupSectionFilters() {
  document.querySelectorAll('.filter-chip[data-section]').forEach(chip => {
    chip.addEventListener('click', () => {
      const section = chip.dataset.section;
      const filter = chip.dataset.filter;

      document.querySelectorAll(`.filter-chip[data-section="${section}"]`).forEach(c => c.classList.remove('active'));
      chip.classList.add('active');

      state.contentFilter[section] = filter;
      renderContentSection(
        section,
        `${section === 'linkedin' ? 'linkedin' : section === 'newsletter' ? 'newsletter' : 'instagram'}-list`,
        `${section === 'linkedin' ? 'li' : section === 'newsletter' ? 'nl' : 'ig'}-count`,
        section === 'linkedin' ? 'linkedin post' : section === 'newsletter' ? 'newsletter draft' : 'reel script'
      );
    });
  });
}

async function markPostedFromCard(itemId, platform) {
  try {
    const result = await POST(`/api/content/${itemId}/post`, { platforms: [platform] });
    launchConfetti();
    updateStreak(result.streak || 0);
    toast('✅ Posted! Streak: ' + result.streak + '🔥');
    loadContentSections();
    loadStats();
  } catch (err) {
    toast('Error: ' + err.message);
  }
}

function repurposeItem(itemId, platform) {
  const item = state.allContent.find(i => i.id === itemId);
  if (!item) return;
  const thought = item.raw_thought || '';
  document.getElementById('thought-input').value = thought + '\n[Repurposed from previous post — take a new angle]';
  updateCharCount();
  updateGenerateBtn();
  switchTab('create');
  document.querySelector('.capture-zone').scrollIntoView({ behavior: 'smooth' });
  toast('♻️ Ready to repurpose — edit and generate.');
}

async function discardFromCard(itemId) {
  const reason = prompt('Why discard? (optional — helps improve future content)\n\nExamples: "too generic", "wrong tone", "sounds like AI"') ?? '';
  try {
    await POST(`/api/content/${itemId}/discard`, { reason, pattern_notes: reason });
    state.allContent = state.allContent.filter(i => i.id !== itemId);
    await loadContentSections();
    toast('🗑 Discarded');
  } catch (e) {
    toast('Failed to discard — try again');
  }
}

// ── Content modal (expand + edit) ─────────────────────────────────────────
async function openContentModal(itemId, platform) {
  const item = state.allContent.find(i => i.id === itemId);
  if (!item) return;

  const platforms = JSON.parse(item.platforms || '[]');
  const modal = document.getElementById('content-modal');
  const tabsEl = document.getElementById('modal-platform-tabs');
  const editor = document.getElementById('content-modal-editor');
  const title = document.getElementById('content-modal-title');

  let activePlatform = platform;
  const platformLabels = { linkedin: '🔗 LinkedIn', newsletter: '📧 Newsletter', instagram: '📸 Instagram' };
  const contentKeys = { linkedin: 'linkedin_content', newsletter: 'newsletter_content', instagram: 'instagram_script' };

  title.textContent = 'Edit Post';

  function renderEditor() {
    editor.value = item[contentKeys[activePlatform]] || '';

    // Tabs
    tabsEl.innerHTML = '';
    platforms.forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'output-tab' + (p === activePlatform ? ' active' : '');
      btn.textContent = platformLabels[p];
      btn.addEventListener('click', () => {
        // Save current before switching
        item[contentKeys[activePlatform]] = editor.value;
        activePlatform = p;
        document.querySelectorAll('#modal-platform-tabs .output-tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        renderEditor();
      });
      tabsEl.appendChild(btn);
    });
  }

  renderEditor();
  modal.style.display = 'flex';

  // Save edits
  document.getElementById('modal-save-edit-btn').onclick = async () => {
    item[contentKeys[activePlatform]] = editor.value;
    const updates = {};
    platforms.forEach(p => { updates[contentKeys[p]] = item[contentKeys[p]] || ''; });
    try {
      await PUT(`/api/content/${itemId}`, updates);
      toast('✅ Saved');
      loadContentSections();
    } catch { toast('Save failed'); }
  };

  // Copy
  document.getElementById('modal-copy-btn').onclick = () => {
    const text = editor.value;
    navigator.clipboard.writeText(text).catch(() => {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    });
    toast('📋 Copied!');
  };
}

// ── Intel tab ──────────────────────────────────────────────────────────────
async function loadIntel() {
  const loadingEl = document.getElementById('intel-loading');
  const emptyEl = document.getElementById('intel-empty');
  const contentEl = document.getElementById('intel-content');

  try {
    const data = await GET('/api/intel');

    if (!data.generated_at && !data.articles.length) {
      loadingEl.style.display = 'none';
      emptyEl.style.display = 'block';
      contentEl.style.display = 'none';
      return;
    }

    if (data.generated_at) {
      const d = new Date(data.generated_at);
      document.getElementById('intel-timestamp').textContent =
        'Updated ' + d.toLocaleDateString('en', { month: 'short', day: 'numeric' }) + ' at ' +
        d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' });
    }
    const badge = document.getElementById('intel-data-badge');
    if (badge) {
      if (data.deep_data) {
        badge.textContent = '⚡ Deep data';
        badge.className = 'intel-data-badge intel-data-badge--deep';
        badge.style.display = 'inline-block';
        badge.title = 'Real post engagement data from Apify (likes, comments, saves, views, outlier detection)';
      } else {
        badge.textContent = '📰 News only';
        badge.className = 'intel-data-badge intel-data-badge--surface';
        badge.style.display = 'inline-block';
        badge.title = 'Surface-level Google News RSS. Add APIFY_TOKEN in Railway for real engagement data.';
      }
    }

    // LinkedIn Intel
    renderIntelCards('intel-linkedin', data.linkedin_intel||data.debates||[], item => {
      const src = item.source_url ? `<a href="${item.source_url}" target="_blank" class="intel-source-link">🔗 ${item.source_name||'Source'}</a>` : '';
      const via = item.found_via ? `<div class="intel-card-via">Found via: ${item.found_via}</div>` : '';
      return `
      <div class="intel-card">
        <div class="intel-card-title">${item.topic}</div>
        <div class="intel-card-sub">${item.why_trending||item.sides||''}</div>
        ${item.hook ? `<div class="intel-card-sub">Hook: "${item.hook}"</div>` : ''}
        ${src}${via}
        <button class="intel-use-btn" data-text="LINKEDIN TRENDING: ${item.topic}. ${item.why_trending||''}. Hook: ${item.hook||''}">✏️ Write on this</button>
      </div>`;
    });

    // Instagram Intel
    renderIntelCards('intel-instagram', data.instagram_intel||data.trending||[], item => {
      const src = item.source_url ? `<a href="${item.source_url}" target="_blank" class="intel-source-link">🔗 ${item.source_name||'Source'}</a>` : '';
      const via = item.found_via ? `<div class="intel-card-via">Found via: ${item.found_via}</div>` : '';
      return `
      <div class="intel-card">
        <div class="intel-card-title">${item.topic}</div>
        <div class="intel-card-sub">${item.why_trending||''}</div>
        ${item.hook ? `<div class="intel-card-sub">Hook: "${item.hook}"</div>` : ''}
        ${src}${via}
        <button class="intel-use-btn" data-text="INSTAGRAM TRENDING: ${item.topic}. ${item.why_trending||''}. Hook: ${item.hook||''}">✏️ Write on this</button>
      </div>`;
    });

    // Gaps
    renderIntelCards('intel-gaps', data.content_gaps||data.gaps||[], item => {
      const src = item.source_url ? `<a href="${item.source_url}" target="_blank" class="intel-source-link">🔗 ${item.source_name||'Source'}</a>` : '';
      return `
      <div class="intel-card">
        <div class="intel-card-title">${item.angle}</div>
        <div class="intel-card-sub">${item.why}</div>
        ${src}
        <button class="intel-use-btn" data-text="GAP OPPORTUNITY: ${item.angle}. ${item.why}">✏️ Write on this</button>
      </div>`;
    });

    // Suggested angles
    const anglesEl = document.getElementById('intel-angles');
    anglesEl.innerHTML = '';
    (data.suggested_angles || []).forEach(angle => {
      // angle can be a string or an object {idea, platform, source_url}
      const idea = (typeof angle === 'object') ? (angle.idea || JSON.stringify(angle)) : angle;
      const platform = (typeof angle === 'object' && angle.platform) ? ` · ${angle.platform}` : '';
      const src = (typeof angle === 'object' && angle.source_url)
        ? `<a href="${angle.source_url}" target="_blank" class="intel-source-link">🔗 Source</a>` : '';
      const card = document.createElement('div');
      card.className = 'intel-card';
      card.innerHTML = `
        <div class="intel-card-title">${idea}</div>
        ${platform ? `<div class="intel-card-via">${platform}</div>` : ''}
        ${src}
        <button class="intel-use-btn" data-text="${idea.replace(/"/g, '&quot;')}">✏️ Write this</button>
      `;
      anglesEl.appendChild(card);
    });

    // Articles
    const articlesEl = document.getElementById('intel-articles');
    articlesEl.innerHTML = '';
    data.articles.slice(0, 20).forEach(article => {
      const el = document.createElement('div');
      el.className = 'intel-article';
      // Detect source type for chip — covers Apify, Instaloader, PRAW, RSS variants
      const src = article.source || '';
      let chip = '';
      if (src.startsWith('Reddit'))       chip = '<span class="src-chip chip-reddit">Reddit</span>';
      else if (src.startsWith('Instagram #') || src.includes('Instagram')) chip = '<span class="src-chip chip-instagram">Instagram</span>';
      else if (src.includes('LinkedIn'))  chip = '<span class="src-chip chip-linkedin">LinkedIn</span>';
      else                                chip = '<span class="src-chip chip-news">Google News</span>';
      const linkEl = article.url ? `<a href="${article.url}" target="_blank" class="intel-source-link">🔗</a>` : '';
      el.innerHTML = `
        <div class="intel-article-header">
          ${chip}
          <span class="intel-article-source-name">${src}</span>
          ${linkEl}
        </div>
        <div class="intel-article-title">${article.title}</div>
        ${article.summary ? `<div class="intel-article-summary">${article.summary.slice(0,150)}…</div>` : ''}
        <button class="intel-article-use" data-text="From '${article.title}' (${src}): ${article.summary||''}">Use as context</button>
      `;
      articlesEl.appendChild(el);
    });

    // Wire up all "Use/Write" buttons
    document.querySelectorAll('.intel-use-btn, .intel-article-use').forEach(btn => {
      btn.addEventListener('click', () => {
        const text = btn.dataset.text;
        state.intelContext = { text, label: text.substring(0, 50) + '…' };
        document.getElementById('intel-context-label').textContent = state.intelContext.label;
        document.getElementById('intel-context-display').style.display = 'flex';
        switchTab('create');
        toast('📡 Intel loaded. Add your thought and generate.');
      });
    });

    loadingEl.style.display = 'none';
    emptyEl.style.display = 'none';
    contentEl.style.display = 'block';

    // Load sources panel after content renders
    loadIntelSources();

  } catch (err) {
    loadingEl.style.display = 'none';
    emptyEl.style.display = 'block';
  }
}

async function loadIntelSources() {
  try {
    const data = await GET('/api/intel/sources');
    const panel = document.getElementById('intel-sources-panel');
    const list = document.getElementById('intel-sources-list');
    if (!data || !data.sources || !data.sources.length) return;

    const typeLabel = {
      'google_news_rss':        '📰 Google News RSS',
      'apify_linkedin':         '🔗 Apify LinkedIn',
      'apify_instagram':        '📸 Apify Instagram',
      'instagram_instaloader':  '📸 Instaloader (free)',
      'instagram_apify':        '📸 Apify Instagram',
      'instagram_none':         '📸 Instagram (unavailable)',
      'reddit_apify':           '💬 Reddit via Apify',
      'reddit_praw':            '💬 Reddit via PRAW OAuth',
      'reddit_rss':             '💬 Reddit RSS fallback',
    };

    list.innerHTML = data.sources.map(s => {
      const ok = s.success && s.articles_found > 0;
      const warn = s.success && s.articles_found === 0;
      const fail = !s.success;
      const icon = ok ? '✅' : warn ? '⚠️' : '❌';
      const cls = ok ? 'source-ok' : warn ? 'source-warn' : 'source-fail';
      const type = typeLabel[s.source_type] || s.source_type;
      const err = s.error_msg ? `<div class="source-error">${s.error_msg}</div>` : '';
      return `<div class="source-row ${cls}">
        <span class="source-icon">${icon}</span>
        <div class="source-info">
          <div class="source-name">${s.source_name}</div>
          <div class="source-meta">${type} · ${s.articles_found} items${err}</div>
        </div>
      </div>`;
    }).join('');

    const runAt = data.run_at ? new Date(data.run_at).toLocaleTimeString('en', {hour:'2-digit',minute:'2-digit'}) : '';
    if (runAt) list.insertAdjacentHTML('afterbegin', `<div class="source-run-time">Last run: ${runAt}</div>`);

    panel.style.display = 'block';
  } catch (e) {
    // silently fail
  }
}

// Wire up sources panel toggle once at startup (not inside loadIntelSources to avoid duplicate listeners)
document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.getElementById('intel-sources-toggle');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const body = document.getElementById('intel-sources-body');
      const arrow = document.getElementById('intel-sources-arrow');
      if (!body) return;
      const isOpen = body.style.display === 'block';
      body.style.display = isOpen ? 'none' : 'block';
      arrow.textContent = isOpen ? '▼' : '▲';
    });
  }
});

function renderIntelCards(containerId, items, templateFn) {
  const el = document.getElementById(containerId);
  el.innerHTML = '';
  if (!items || !items.length) {
    el.innerHTML = '<div class="intel-card-sub" style="opacity:.5">Nothing yet — refresh intel.</div>';
    return;
  }
  items.forEach(item => { el.innerHTML += templateFn(item); });
}

// ── Library tab ────────────────────────────────────────────────────────────
async function loadStories() {
  const list = document.getElementById('stories-list');
  try {
    const stories = await GET('/api/stories');
    list.innerHTML = '';
    if (!stories.length) {
      list.innerHTML = '<div class="empty-state">No stories yet. Add your first one.</div>';
      return;
    }
    stories.forEach(story => {
      const card = document.createElement('div');
      card.className = 'story-card';
      const platformTag = { linkedin: '🔗 LinkedIn', instagram: '📸 Instagram', both: '🌐 Both' }[story.platform] || story.platform;
      card.innerHTML = `
        <div class="story-card-title">${story.title}</div>
        <div class="story-card-snippet">${story.story_snippet}</div>
        <div class="story-card-footer">
          <span class="story-platform-tag">${platformTag}</span>
          <button class="story-use-btn" data-id="${story.id}" data-title="${story.title}">Use in create</button>
        </div>
      `;
      card.querySelector('.story-use-btn').addEventListener('click', () => {
        state.storyContext = { id: story.id, title: story.title };
        document.getElementById('story-context-label').textContent = story.title;
        document.getElementById('story-context-display').style.display = 'flex';
        switchTab('create');
        toast(`📚 Story loaded: ${story.title}`);
      });
      list.appendChild(card);
    });
  } catch {
    list.innerHTML = '<div class="empty-state">Could not load stories.</div>';
  }
}

async function loadRawIdeas() {
  const list = document.getElementById('raw-ideas-list');
  try {
    const ideas = await GET('/api/raw-ideas');
    document.getElementById('raw-ideas-count').textContent = `${ideas.length} ideas`;

    list.innerHTML = '';
    if (!ideas.length) {
      list.innerHTML = '<div class="empty-state">No raw ideas yet. Every voice note saves here automatically.</div>';
      return;
    }

    ideas.forEach(idea => {
      const el = document.createElement('div');
      el.className = 'raw-idea-item';
      const date = new Date(idea.created_at).toLocaleDateString('en', { month: 'short', day: 'numeric' });
      el.innerHTML = `
        <span class="raw-idea-text">${idea.thought}</span>
        <span class="raw-idea-date">${date}</span>
        <div class="raw-idea-actions">
          <button class="raw-idea-use" data-thought="${idea.thought}">Use</button>
          <button class="raw-idea-delete" data-id="${idea.id}">✕</button>
        </div>
      `;
      el.querySelector('.raw-idea-use').addEventListener('click', () => {
        document.getElementById('thought-input').value = idea.thought;
        updateCharCount();
        updateGenerateBtn();
        switchTab('create');
        toast('💡 Idea loaded — edit and generate.');
      });
      el.querySelector('.raw-idea-delete').addEventListener('click', async () => {
        await DEL(`/api/raw-ideas/${idea.id}`);
        await loadRawIdeas();
      });
      list.appendChild(el);
    });
  } catch {
    list.innerHTML = '<div class="empty-state">Could not load raw ideas.</div>';
  }
}

function setupAddStory() {
  document.getElementById('add-story-btn').addEventListener('click', () => {
    document.getElementById('add-story-modal').style.display = 'flex';
  });

  document.getElementById('cancel-story-btn').addEventListener('click', () => {
    document.getElementById('add-story-modal').style.display = 'none';
  });

  document.getElementById('confirm-add-story-btn').addEventListener('click', async () => {
    const title = document.getElementById('story-title-input').value.trim();
    const snippet = document.getElementById('story-body-input').value.trim();
    const platform = document.getElementById('story-platform-select').value;

    if (!title || !snippet) { toast('Fill in both fields.'); return; }

    await POST('/api/stories', { title, story_snippet: snippet, platform });
    document.getElementById('add-story-modal').style.display = 'none';
    document.getElementById('story-title-input').value = '';
    document.getElementById('story-body-input').value = '';
    toast('✅ Story added!');
    loadStories();
  });
}

// ── Queue tab ──────────────────────────────────────────────────────────────
async function loadQueue() {
  const list = document.getElementById('queue-list');
  try {
    const filter = state.queueFilter;
    const url = filter === 'all' ? '/api/content' : `/api/content?status=${filter}`;
    const items = await GET(url);

    list.innerHTML = '';
    if (!items.length) {
      list.innerHTML = '<div class="empty-state">No content yet. Create your first post.</div>';
      return;
    }

    items.forEach(item => {
      const platforms = JSON.parse(item.platforms || '[]');
      const date = new Date(item.created_at).toLocaleDateString('en', { month: 'short', day: 'numeric' });
      const preview = (item.linkedin_content || item.newsletter_content || item.instagram_script || '').substring(0, 100);
      const platIcons = platforms.map(p => ({ linkedin: '🔗', newsletter: '📧', instagram: '📸' }[p] || p)).join(' ');

      const card = document.createElement('div');
      card.className = 'content-card';
      card.innerHTML = `
        <div class="card-meta">
          <span class="card-date">${platIcons} ${date}</span>
          <span class="status-pill ${item.status}">${item.status}</span>
        </div>
        <div class="card-preview">${preview}${preview.length >= 100 ? '…' : ''}</div>
        <div class="rating-row" data-id="${item.id}">
          <button class="rating-btn ${item.rating === 3 ? 'active' : ''}" data-rating="3" title="Loved it">🔥</button>
          <button class="rating-btn ${item.rating === 2 ? 'active' : ''}" data-rating="2" title="Good">👍</button>
          <button class="rating-btn ${item.rating === 1 ? 'active' : ''}" data-rating="1" title="Missed">🔄</button>
        </div>
        <div class="card-actions">
          <button class="card-btn" data-action="expand-queue" data-id="${item.id}" data-platform="${platforms[0] || 'linkedin'}">View</button>
          ${item.status !== 'posted' ? `<button class="card-btn green" data-action="mark-posted-queue" data-id="${item.id}">✅ Mark Posted</button>` : ''}
        </div>
      `;

      // Rating buttons
      card.querySelectorAll('.rating-btn').forEach(rb => {
        rb.addEventListener('click', async () => {
          const rating = parseInt(rb.dataset.rating);
          await PUT(`/api/content/${item.id}`, { rating });
          card.querySelectorAll('.rating-btn').forEach(r => r.classList.remove('active'));
          rb.classList.add('active');
          toast('Rating saved!');
        });
      });

      card.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const action = btn.dataset.action;
          const id = btn.dataset.id;
          const p = btn.dataset.platform;
          if (action === 'expand-queue') {
            state.allContent = await GET('/api/content');
            openContentModal(id, p);
          }
          if (action === 'mark-posted-queue') {
            const result = await POST(`/api/content/${id}/post`, { platforms: JSON.parse(item.platforms || '[]') });
            launchConfetti();
            updateStreak(result.streak || 0);
            toast('🎉 Posted! Streak: ' + result.streak);
            loadQueue();
            loadStats();
          }
        });
      });

      list.appendChild(card);
    });
  } catch (err) {
    list.innerHTML = `<div class="empty-state">Could not load queue. ${err.message}</div>`;
  }
}

function setupQueueFilters() {
  document.querySelectorAll('.filter-pill[data-queue-filter]').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.filter-pill[data-queue-filter]').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      state.queueFilter = pill.dataset.queueFilter;
      loadQueue();
    });
  });
}

// ── Settings tab ───────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const status = await GET('/api/status');
    const indicator = document.getElementById('api-key-status');

    if (status.has_api_key) {
      indicator.className = 'api-status-indicator ok';
      indicator.textContent = '✅ API key is set';
    } else {
      indicator.className = 'api-status-indicator missing';
      indicator.textContent = '⚠️ No API key — add one below';
    }

    document.getElementById('info-stories').textContent = status.stories_count || 0;
    document.getElementById('info-ideas').textContent = status.raw_ideas_count || 0;
    const total = (status.total_linkedin || 0) + (status.total_newsletter || 0) + (status.total_instagram || 0);
    document.getElementById('info-posts').textContent = total;

    // Reddit credential status indicator
    const redditStatus = document.getElementById('reddit-key-status');
    if (redditStatus) {
      if (status.has_reddit) {
        redditStatus.className = 'api-status-indicator ok';
        redditStatus.textContent = '✅ Reddit PRAW connected';
      } else {
        redditStatus.className = 'api-status-indicator missing';
        redditStatus.textContent = '⚠️ No Reddit credentials — using RSS fallback';
      }
    }
  } catch {}

  // Load discard log
  try {
    const logEl = document.getElementById('discard-log-list');
    const data = await GET('/api/kb/export?format=json').catch(() => null);
    if (!data) return;
    const discards = data.discard_log || [];
    if (!discards.length) {
      logEl.innerHTML = '<div class="empty-state" style="padding:12px 0">No discards yet — discard content to teach your taste.</div>';
      return;
    }
    logEl.innerHTML = discards.slice(0, 20).map(d => {
      const preview = (d.raw_thought || d.pattern_notes || 'Discarded item').substring(0, 120);
      const reason = (d.discard_reason || d.pattern_notes)
        ? `<div class="discard-log-reason">"${(d.discard_reason || d.pattern_notes).substring(0, 80)}"</div>` : '';
      const date = d.discarded_at ? `<div class="discard-log-date">${new Date(d.discarded_at).toLocaleDateString('en', {month:'short', day:'numeric'})}</div>` : '';
      return `<div class="discard-log-item">
        <div class="discard-log-preview">${preview}</div>
        ${reason}${date}
      </div>`;
    }).join('');
  } catch {}
}

function setupSettings() {
  document.getElementById('save-api-key-btn').addEventListener('click', async () => {
    const key = document.getElementById('api-key-input').value.trim();
    if (!key) return;
    try {
      await POST('/api/settings/apikey', { api_key: key });
      toast('✅ API key saved!');
      document.getElementById('api-key-input').value = '';
      document.getElementById('setup-banner').style.display = 'none';
      loadSettings();
    } catch (err) {
      toast('❌ ' + err.message);
    }
  });

  document.getElementById('save-reddit-btn').addEventListener('click', async () => {
    const clientId = document.getElementById('reddit-client-id-input').value.trim();
    const clientSecret = document.getElementById('reddit-client-secret-input').value.trim();
    if (!clientId || !clientSecret) {
      toast('Enter both Client ID and Client Secret');
      return;
    }
    try {
      await POST('/api/settings/reddit', { client_id: clientId, client_secret: clientSecret });
      toast('✅ Reddit credentials saved! Refresh Intel to use PRAW.');
      document.getElementById('reddit-client-id-input').value = '';
      document.getElementById('reddit-client-secret-input').value = '';
      loadSettings();
    } catch (err) {
      toast('❌ ' + err.message);
    }
  });

  document.getElementById('view-kb-btn').addEventListener('click', async () => {
    const viewer = document.getElementById('kb-viewer');
    if (viewer.style.display !== 'none') { viewer.style.display = 'none'; return; }

    try {
      const data = await GET('/api/kb');
      viewer.innerHTML = '';
      Object.entries(data.profile).forEach(([key, val]) => {
        const row = document.createElement('div');
        row.className = 'kb-profile-row';
        row.innerHTML = `<div class="kb-profile-key">${key.replace(/_/g, ' ')}</div>
                         <div class="kb-profile-val">${val.substring(0, 200)}${val.length > 200 ? '…' : ''}</div>`;
        viewer.appendChild(row);
      });
      viewer.style.display = 'block';
    } catch { toast('Could not load KB'); }
  });
}

// ── Sheet close handlers ───────────────────────────────────────────────────
function setupSheets() {
  document.getElementById('close-story-sheet').addEventListener('click', () => {
    document.getElementById('story-sheet-overlay').style.display = 'none';
  });
  document.getElementById('story-sheet-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('story-sheet-overlay'))
      document.getElementById('story-sheet-overlay').style.display = 'none';
  });

  document.getElementById('close-intel-sheet').addEventListener('click', () => {
    document.getElementById('intel-sheet-overlay').style.display = 'none';
  });
  document.getElementById('intel-sheet-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('intel-sheet-overlay'))
      document.getElementById('intel-sheet-overlay').style.display = 'none';
  });

  document.getElementById('close-content-modal').addEventListener('click', () => {
    document.getElementById('content-modal').style.display = 'none';
  });
  document.getElementById('content-modal').addEventListener('click', e => {
    if (e.target.id === 'content-modal')
      document.getElementById('content-modal').style.display = 'none';
  });
}

// ── Intel refresh button ───────────────────────────────────────────────────
function setupIntelRefresh() {
  document.getElementById('intel-refresh-btn').addEventListener('click', async () => {
    const btn = document.getElementById('intel-refresh-btn');
    btn.disabled = true;
    btn.textContent = 'Refreshing…';
    document.getElementById('intel-loading').style.display = 'block';
    document.getElementById('intel-content').style.display = 'none';
    document.getElementById('intel-empty').style.display = 'none';

    try {
      await POST('/api/intel/refresh', {});
      toast('📡 Pulling from 12 sources — check back in 30s');

      // Poll for completion
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        const data = await GET('/api/intel').catch(() => null);
        if (data && data.generated_at) {
          clearInterval(poll);
          btn.disabled = false;
          btn.textContent = 'Refresh now';
          loadIntel();
        }
        if (attempts > 10) clearInterval(poll);
      }, 4000);
    } catch {
      btn.disabled = false;
      btn.textContent = 'Refresh now';
    }
  });
}

// ── App startup ────────────────────────────────────────────────────────────
async function init() {
  // Check API key status
  try {
    const status = await GET('/api/status');
    if (!status.has_api_key) {
      document.getElementById('setup-banner').style.display = 'flex';
      document.getElementById('settings-badge').style.display = 'flex';
    }
    updateStreak(status.streak || 0);
  } catch {}

  // Wire up everything
  setupPlatformPills();
  setupVoiceCapture();
  setupPhotoUpload();
  setupContextChips();
  setupPostConfirm();
  setupSectionFilters();
  setupSheets();
  setupAddStory();
  setupQueueFilters();
  setupSettings();
  setupIntelRefresh();
  setupCreatorAdd();

  // Generate button
  document.getElementById('generate-btn').addEventListener('click', generate);

  // Textarea auto-save & char count
  const textarea = document.getElementById('thought-input');
  textarea.addEventListener('input', () => {
    updateCharCount();
    updateGenerateBtn();
    scheduleRawIdeaSave('text');
  });

  // Bottom nav
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Setup banner shortcut
  document.getElementById('setup-banner-btn').addEventListener('click', () => switchTab('settings'));

  // Initial data load
  loadContentSections();
  loadStats();
}

document.addEventListener('DOMContentLoaded', init);

// ── Tracked Creators ────────────────────────────────────────────────────────
async function loadCreators() {
  const list = document.getElementById('creators-list');
  if (!list) return;
  const creators = await GET('/api/creators').catch(() => []);
  list.innerHTML = '';
  if (!creators.length) {
    list.innerHTML = '<div class="intel-card-sub" style="opacity:.5;margin-top:8px">No creators tracked yet.</div>';
    return;
  }
  creators.forEach(c => {
    const icon = c.platform === 'instagram' ? '📸' : '💼';
    const row = document.createElement('div');
    row.className = 'creator-chip';
    row.innerHTML = `
      <span>${icon} <a href="${c.profile_url}" target="_blank">@${c.handle}</a>${c.niche ? ` <span class="creator-niche">${c.niche}</span>` : ''}</span>
      <button class="creator-remove" data-id="${c.id}" title="Remove">✕</button>
    `;
    row.querySelector('.creator-remove').addEventListener('click', async () => {
      await fetch(`/api/creators/${c.id}`, { method: 'DELETE' });
      loadCreators();
      toast('Creator removed');
    });
    list.appendChild(row);
  });
}

function setupCreatorAdd() {
  const btn = document.getElementById('creator-add-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const urlInput = document.getElementById('creator-url-input');
    const nicheInput = document.getElementById('creator-niche-input');
    const url = urlInput.value.trim();
    if (!url) return;
    btn.disabled = true;
    btn.textContent = 'Adding…';
    const result = await POST('/api/creators', { url, niche: nicheInput.value.trim() }).catch(e => ({ error: e.message }));
    btn.disabled = false;
    btn.textContent = 'Add';
    if (result.error) {
      toast('⚠️ ' + result.error);
    } else {
      urlInput.value = '';
      nicheInput.value = '';
      toast(`✅ @${result.handle} added to ${result.platform} tracking`);
      loadCreators();
    }
  });
}
