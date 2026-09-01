/* TBR Shelf front-end. Server-rendered page; this file adds the interactions.
 *
 * Sections: layout density · filter bar · API helpers · add/import · per-book actions ·
 * background-work polling · voice librarian · bookshelf view.
 */

'use strict';

const root = document.documentElement;
const notice = document.querySelector('#notice');
const FEATURES = (() => {
  try { return JSON.parse(document.body.dataset.features || '{}'); } catch { return {}; }
})();

function say(text) { notice.textContent = text; }

// ---------- Layout density (mobile / auto / desktop) ----------

(function densitySwitcher() {
  function detect() {
    const mobileUA = /Android|iPhone|iPad|iPod|Mobile|Windows Phone/i.test(navigator.userAgent || '');
    return (mobileUA || window.innerWidth < 760) ? 'mobile' : 'desktop';
  }
  function apply() {
    const override = localStorage.getItem('rt-density');
    const active = (override === 'mobile' || override === 'desktop') ? override : detect();
    root.dataset.density = active;
    document.querySelectorAll('.segmented button[data-v]').forEach((button) => {
      button.classList.toggle('on', button.dataset.v === (override || 'auto'));
    });
    const badge = document.querySelector('.switcher .badge');
    if (badge) badge.textContent = override ? `pinned: ${override}` : `auto-detected: ${active}`;
    applyFilterBar();
  }
  document.querySelectorAll('.segmented button[data-v]').forEach((button) => {
    button.onclick = () => {
      if (button.dataset.v === 'auto') localStorage.removeItem('rt-density');
      else localStorage.setItem('rt-density', button.dataset.v);
      apply();
    };
  });
  window.addEventListener('resize', () => {
    if (!localStorage.getItem('rt-density')) apply();
    else applyFilterBar();
  });
  apply();
})();

// ---------- Filter bar: collapsed on mobile unless a filter is active ----------

function activeFilterCount() {
  const form = document.getElementById('filters');
  if (!form) return 0;
  let count = 0;
  if (form.querySelector('[name=q]').value) count++;
  ['shelf', 'status', 'tag'].forEach((name) => {
    const field = form.querySelector(`[name=${name}]`);
    if (field && field.value && field.value !== 'All') count++;
  });
  const sort = form.querySelector('[name=sort]');
  if (sort && sort.value && sort.value !== 'added') count++;
  return count;
}

function applyFilterBar() {
  const form = document.getElementById('filters');
  const toggle = document.getElementById('filtersToggle');
  if (!form || !toggle) return;
  const mobile = root.dataset.density === 'mobile';
  const count = activeFilterCount();
  toggle.hidden = !mobile;
  toggle.querySelector('.fcount').textContent = count ? `· ${count} active` : '';
  if (!mobile) {
    form.hidden = false;
    toggle.setAttribute('aria-expanded', 'false');
    return;
  }
  const open = toggle.getAttribute('aria-expanded') === 'true' || count > 0;
  form.hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
}

document.addEventListener('click', (event) => {
  const toggle = event.target.closest && event.target.closest('#filtersToggle');
  if (!toggle) return;
  const form = document.getElementById('filters');
  const open = toggle.getAttribute('aria-expanded') !== 'true';
  form.hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
});

// ---------- API helpers ----------

async function api(url, method = 'POST', body) {
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : null,
  });
  const json = await response.json();
  if (!response.ok) {
    const detail = typeof json.detail === 'string' ? json.detail : (json.detail?.message || 'Request failed');
    throw Object.assign(new Error(detail), { status: response.status });
  }
  return json;
}

function reportFailure(error) {
  if (error.status === 409 && confirm('Changed elsewhere. Reload now?')) location.reload();
  else say(error.message);
}

async function pollThenReload(id, field, label) {
  for (let attempt = 0; attempt < 24; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    try {
      const { book } = await api(`/api/books/${id}`, 'GET');
      if (book[field] !== 'queued') { location.reload(); return; }
    } catch { /* keep polling */ }
  }
  say(`${label} is taking a while — refresh manually.`);
}

// ---------- Add book / import CSV ----------

const addPanel = document.querySelector('#addpanel');
if (addPanel) {
  addPanel.addEventListener('toggle', () => {
    if (addPanel.open) addPanel.querySelector('input[name=title]').focus();
  });
}

document.querySelector('#add').onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api('/api/books', 'POST', Object.fromEntries(new FormData(event.target)));
    location.reload();
  } catch (error) { reportFailure(error); }
};

document.querySelector('#importFile').onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  say('Importing…');
  try {
    const response = await fetch('/api/import/csv', { method: 'POST', body: form });
    const json = await response.json();
    if (!response.ok) {
      throw new Error(typeof json.detail === 'string' ? json.detail : (json.detail?.message || 'Import failed'));
    }
    say(json.message);
    if (json.added > 0) location.reload();
  } catch (error) { say(error.message); }
  event.target.value = '';
};

// ---------- Per-book actions ----------

let speakingButton = null;
let summaryAudio = null;

function stopSpeaking() {
  if (summaryAudio) { summaryAudio.pause(); summaryAudio.src = ''; summaryAudio = null; }
  speakingButton = null;
}

function resetListenButton(button) {
  button.textContent = 'Listen';
  button.classList.remove('speaking');
}

function coverButton(url, title, onPick) {
  const button = document.createElement('button');
  button.className = 'coverpick';
  button.title = title;
  const image = document.createElement('img');
  image.src = url;
  image.loading = 'lazy';
  image.onerror = () => button.remove();
  button.append(image);
  button.onclick = onPick;
  return button;
}

document.querySelectorAll('.book').forEach((tile) => {
  const id = tile.dataset.id;
  const version = () => Number(tile.dataset.version);

  // Exclusive accordion: <details name="book"> covers modern browsers; this covers the rest and
  // scrolls the opened tile into view (not inside the shelf modal, where it would scroll the rail away).
  tile.addEventListener('toggle', () => {
    if (!tile.open) return;
    document.querySelectorAll('details.book[open]').forEach((other) => { if (other !== tile) other.open = false; });
    requestAnimationFrame(() => {
      if (!tile.closest('#shelf-modal')) tile.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  });

  const editedFields = () => {
    const data = { version: version() };
    tile.querySelectorAll('[data-field]').forEach((field) => {
      data[field.dataset.field] = field.type === 'number' ? (field.value === '' ? null : Number(field.value)) : field.value;
    });
    return data;
  };

  const setCover = async (url) => {
    try { await api(`/api/books/${id}`, 'PATCH', { version: version(), cover: url }); location.reload(); }
    catch (error) { reportFailure(error); }
  };

  const quickStatus = tile.querySelector('[data-quick="status"]');
  if (quickStatus) {
    quickStatus.onchange = async () => {
      try {
        const { book } = await api(`/api/books/${id}`, 'PATCH', { version: version(), status: quickStatus.value });
        tile.dataset.version = book.version;
        const pill = tile.querySelector('.status-pill');
        if (pill) pill.textContent = book.status;
        say('Status saved');
      } catch (error) { reportFailure(error); }
    };
  }

  const actions = {
    async save() {
      const { book } = await api(`/api/books/${id}`, 'PATCH', editedFields());
      tile.dataset.version = book.version;
      say('Saved');
      location.reload();
    },
    async listen(button) {
      if (speakingButton === button) { stopSpeaking(); resetListenButton(button); return; }
      const previous = speakingButton;
      stopSpeaking();
      if (previous) resetListenButton(previous);
      speakingButton = button;
      button.textContent = 'Loading…';
      button.classList.add('speaking');
      const audio = new Audio(`/api/books/${id}/audio`);
      summaryAudio = audio;
      audio.onplaying = () => { if (speakingButton === button) button.textContent = 'Stop'; };
      audio.onended = audio.onerror = () => {
        if (speakingButton === button) { speakingButton = null; resetListenButton(button); }
      };
      try { await audio.play(); }
      catch {
        if (speakingButton === button) { speakingButton = null; resetListenButton(button); }
        say('Playback failed — try again.');
      }
    },
    async delete() {
      if (!confirm('Permanently delete?')) return;
      await api(`/api/books/${id}`, 'DELETE', { version: version() });
      location.reload();
    },
    async archive() { await api(`/api/books/${id}/archive`, 'POST', { version: version() }); location.reload(); },
    async restore() { await api(`/api/books/${id}/restore`, 'POST', { version: version() }); location.reload(); },
    async similar() {
      const { suggestions } = await api(`/api/books/${id}/similar`, 'POST', { version: version() });
      const box = document.createElement('div');
      box.className = 'choices';
      box.innerHTML = '<b style="display:block;margin-bottom:4px">Suggestions — add only if you choose:</b>';
      suggestions.forEach((suggestion) => {
        const button = document.createElement('button');
        button.textContent = `Add ${suggestion.title} — ${suggestion.author} to Wishlist`;
        button.onclick = async () => {
          try {
            await api('/api/books', 'POST', { title: suggestion.title, author: suggestion.author, shelf: 'Wishlist' });
            location.reload();
          } catch (error) { reportFailure(error); }
        };
        box.append(button);
      });
      tile.querySelector('.summary-box').append(box);
    },
    async lookup(button) {
      await withBusyLabel(button, 'Looking…', async () => {
        await api(`/api/books/${id}/lookup`, 'POST', { version: version() });
        say('Looking up metadata…');
        pollThenReload(id, 'lookup_state', 'Lookup');
      });
    },
    async summary(button) {
      await withBusyLabel(button, 'Summarizing…', async () => {
        await api(`/api/books/${id}/summary`, 'POST', { version: version() });
        say('Regenerating summary…');
        pollThenReload(id, 'summary_state', 'Summary');
      });
    },
    async 'spine-pref'(button) {
      await api(`/api/books/${id}`, 'PATCH', { version: version(), spine_pref: button.dataset.pref });
      location.reload();
    },
    async 'spine-regen'() {
      await api(`/api/books/${id}/spine-regen`, 'POST', { version: version() });
      say('Queued for the external spine generator.');
      location.reload();
    },
    async 'confirm-lookup'() {
      await api(`/api/books/${id}/confirm-lookup`, 'POST', { version: version() });
      location.reload();
    },
    async altcovers(button) {
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Fetching covers…';
      try {
        const { covers, current } = await api(`/api/books/${id}/altcovers`, 'GET');
        const host = tile.querySelector('.altcovers-host');
        host.innerHTML = '';
        if (!covers.length) { say('No alternative covers found for this title.'); return; }
        covers.forEach((url) => {
          const button = coverButton(url, url === current ? 'Current cover' : 'Use this cover', () => setCover(url));
          if (url === current) button.classList.add('current');
          host.append(button);
        });
        say(`${covers.length} cover option(s) — click one to apply.`);
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
    },
  };

  async function withBusyLabel(button, label, work) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try { await work(); }
    catch (error) { button.disabled = false; button.textContent = original; throw error; }
  }

  tile.querySelectorAll('[data-a]').forEach((button) => {
    button.onclick = async () => {
      try { await actions[button.dataset.a](button); }
      catch (error) { reportFailure(error); }
    };
  });

  // Lookup candidates: cover strip and "use this match" choices, both rendered from the same JSON.
  const candidateCovers = tile.querySelector('.covers[data-c]');
  if (candidateCovers) {
    try {
      const seen = new Set();
      JSON.parse(candidateCovers.dataset.c).forEach((candidate) => {
        [candidate.audible_cover, candidate.cover].forEach((url) => {
          if (!url || seen.has(url)) return;
          seen.add(url);
          const source = url === candidate.audible_cover ? 'Audible' : candidate.source;
          const editions = candidate.editions ? `, ${candidate.editions} editions` : '';
          const title = `${candidate.title || ''} — ${candidate.author || 'unknown'} (${source}${editions})`;
          candidateCovers.append(coverButton(url, title, () => setCover(url)));
        });
      });
    } catch { /* malformed candidate JSON: leave the strip empty */ }
  }
  const choices = tile.querySelector('.choices[data-c]');
  if (choices) {
    try {
      JSON.parse(choices.dataset.c).slice(0, 8).forEach((candidate) => {
        const button = document.createElement('button');
        const year = candidate.year ? ` (${candidate.year})` : '';
        const editions = candidate.editions ? `, ${candidate.editions} ed.` : '';
        button.textContent = `Use ${candidate.title} — ${candidate.author || 'unknown'}${year} [${candidate.source}${editions}]`;
        button.onclick = async () => {
          try {
            await api(`/api/books/${id}/accept-candidate`, 'POST', { version: version(), candidate });
            location.reload();
          } catch (error) { reportFailure(error); }
        };
        choices.append(button);
      });
    } catch { /* malformed candidate JSON: leave the list empty */ }
  }
});

// ---------- Tiles rendered mid-lookup or mid-summary: poll until one settles, then refresh ----------

(function pollBackgroundWork() {
  const queued = Array.from(document.querySelectorAll('details.book'))
    .filter((tile) => tile.dataset.lookup === 'queued' || tile.dataset.summary === 'queued')
    .map((tile) => tile.dataset.id);
  if (!queued.length) return;
  say(`Preparing ${queued.length} book(s) in the background…`);
  let ticks = 0;
  const timer = setInterval(async () => {
    if (document.hidden) return;
    if (++ticks > 70) { clearInterval(timer); say('Still working — refresh manually.'); return; }
    for (const id of queued) {
      try {
        const { book } = await api(`/api/books/${id}`, 'GET');
        if (book.lookup_state !== 'queued' && book.summary_state !== 'queued') {
          clearInterval(timer);
          location.reload();
          return;
        }
      } catch { /* try again next tick */ }
    }
  }, 3000);
})();

// ---------- Voice librarian: tap to record, tap again to send; the reply is spoken back ----------

(function voiceChat() {
  const fab = document.querySelector('#vc-fab');
  const panel = document.querySelector('#vc-panel');
  if (!fab || !panel) return;
  if (!navigator.mediaDevices || typeof MediaRecorder === 'undefined') { fab.style.display = 'none'; return; }
  const log = panel.querySelector('#vc-log');
  const status = panel.querySelector('#vc-status');

  let sid = localStorage.getItem('vc-sid');
  if (!sid) { sid = crypto.randomUUID(); localStorage.setItem('vc-sid', sid); }
  let recorder = null;
  let chunks = [];
  let replyAudio = null;
  const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
    : (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '');

  const setStatus = (text) => { status.textContent = text; };
  const bubble = (cls, text) => {
    const div = document.createElement('div');
    div.className = cls;
    div.textContent = text;
    log.append(div);
    log.scrollTop = log.scrollHeight;
  };
  const positionPanel = () => {
    const rect = fab.getBoundingClientRect();
    panel.style.top = `${rect.bottom + 8}px`;
    panel.style.right = `${window.innerWidth - rect.right}px`;
  };

  async function send(blob) {
    fab.classList.remove('rec');
    fab.classList.add('busy');
    setStatus('Thinking…');
    try {
      if (blob.size < 1500) { setStatus('Too short — hold a moment longer.'); return; }
      const form = new FormData();
      form.append('sid', sid);
      form.append('audio', blob, blob.type.includes('mp4') ? 'a.mp4' : 'a.webm');
      const response = await fetch('/api/voice/chat', { method: 'POST', body: form });
      const json = await response.json();
      if (!response.ok) throw new Error(typeof json.detail === 'string' ? json.detail : 'Voice chat failed');
      positionPanel();
      panel.classList.add('open');
      bubble('u', json.transcript);
      bubble('a', json.reply);
      setStatus('');
      if (json.audio_b64) {
        // Reuse the element unlocked during the send tap: a fresh Audio here is autoplay-blocked on iOS.
        const audio = replyAudio || new Audio();
        replyAudio = audio;
        audio.src = `data:audio/mpeg;base64,${json.audio_b64}`;
        audio.play().catch(() => setStatus('Autoplay blocked — check the silent switch, then tap the mic again.'));
      }
    } catch (error) { setStatus(error.message); }
    finally { fab.classList.remove('busy'); }
  }

  async function startRecording() {
    if (replyAudio) { replyAudio.pause(); replyAudio = null; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch { setStatus('Mic permission needed.'); return; }
    chunks = [];
    const current = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    recorder = current;
    current.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    current.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      send(new Blob(chunks, { type: current.mimeType || mime || 'audio/webm' }));
    };
    current.start();
    fab.classList.add('rec');
    positionPanel();
    panel.classList.add('open');
    setStatus('Listening — tap again to send.');
  }

  const silentWav = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
  fab.onclick = () => {
    if (fab.classList.contains('busy')) return;
    if (recorder && recorder.state === 'recording') {
      // Unlock audio playback inside this tap so the async reply may auto-play (iOS).
      replyAudio = new Audio(silentWav);
      replyAudio.play().catch(() => {});
      recorder.stop();
      recorder = null;
    } else {
      startRecording();
    }
  };

  panel.querySelector('#vc-clear').onclick = async () => {
    if (replyAudio) { replyAudio.pause(); replyAudio = null; }
    try { await fetch(`/api/voice/chat/${sid}`, { method: 'DELETE' }); } catch { /* best effort */ }
    sid = crypto.randomUUID();
    localStorage.setItem('vc-sid', sid);
    log.innerHTML = '';
    setStatus('New conversation.');
  };
})();

// ---------- Bookshelf view: an alternative rendering of the same .book tiles ----------

(function bookshelf() {
  const shelf = document.querySelector('#shelf');
  const modal = document.querySelector('#shelf-modal');
  if (!shelf || !modal) return;
  const body = modal.querySelector('.sm-body');
  const rail = modal.querySelector('.sm-rail');
  const tiles = Array.from(document.querySelectorAll('main details.book'));
  const ORDER = ['Audible', 'Chirp', 'Wishlist'];
  const ACCENT = { Audible: '#8a5a22', Chirp: '#1f6d63', Wishlist: '#4d3f80' };
  const PAGES_PER_INCH = 300;
  const HOURS_TO_PAGES = 38;
  const FORMAT_HEIGHT_IN = { Hardcover: 9.5, Paperback: 8.3, 'Mass Market': 7.0 };

  let colorCache = {};
  try { colorCache = JSON.parse(localStorage.getItem('rt-spine-colors') || '{}'); } catch { /* fresh cache */ }
  const saveColors = () => { try { localStorage.setItem('rt-spine-colors', JSON.stringify(colorCache)); } catch { /* quota */ } };

  const hash = (text) => { let x = 0; for (let i = 0; i < text.length; i++) x = (x * 31 + text.charCodeAt(i)) >>> 0; return x; };
  const hoursOf = (text) => {
    const h = /(\d+)\s*h/i.exec(text || '');
    const m = /(\d+)\s*m/i.exec(text || '');
    return (h || m) ? Number(h?.[1] || 0) + Number(m?.[1] || 0) / 60 : null;
  };

  // Physical-ish sizing: thickness from page count (audio hours as a fallback), height from print format.
  function spineDims(tile) {
    const key = hash(tile.dataset.id + tile.dataset.title);
    const mobile = root.dataset.density === 'mobile';
    const hours = hoursOf(tile.dataset.len);
    const pages = Number(tile.dataset.pages) || (hours != null ? Math.round(hours * HOURS_TO_PAGES) : 0);
    let known = pages > 0;
    let thickness = known ? Math.max(0.35, Math.min(2.6, pages / PAGES_PER_INCH)) : 0.6 + ((key % 40) / 100);
    let heightIn = FORMAT_HEIGHT_IN[tile.dataset.format] || (8.4 + (((key >> 3) % 80) / 100 - 0.4));
    if (Number(tile.dataset.spineH) > 0 && Number(tile.dataset.spineT) > 0) {
      heightIn = Number(tile.dataset.spineH);
      thickness = Number(tile.dataset.spineT);
      known = true;
    }
    const ppi = mobile ? 17 : 22;
    const heightPx = Math.round(heightIn * ppi);
    // An external asset is a picture of the actual book: size the box to its true proportion so nothing
    // is cropped. The CSS-drawn fallback needs a constant floor to fit its thumbnail and vertical text.
    if (tile.dataset.spineSrc === 'external' && tile.dataset.spineState === 'ready' && thickness > 0 && heightIn > 0) {
      return { w: Math.max(6, Math.round(heightPx * thickness / heightIn)), h: heightPx, known: true };
    }
    return { w: Math.round((mobile ? 22 : 26) + thickness * ppi * (mobile ? 0.85 : 1)), h: heightPx, known };
  }

  const textClass = ([r, g, b]) => ((0.2126 * r + 0.7152 * g + 0.0722 * b) > 150 ? 'light' : 'dark');

  function fallbackColor(tile) {
    const key = hash(tile.dataset.id + tile.dataset.title);
    const base = parseInt((ACCENT[tile.dataset.shelf] || '#555555').slice(1), 16);
    const delta = ((key % 40) - 20) * 1.5;
    return [base >> 16, (base >> 8) & 255, base & 255].map((v) => Math.max(0, Math.min(255, Math.round(v + delta))));
  }

  // Dominant saturated colour of the cached cover, sampled through a 16x16 canvas.
  function sampleCover(id, version, callback) {
    const key = `${id}:${version}`;
    if (colorCache[key]) { callback(colorCache[key]); return; }
    const image = new Image();
    image.onload = () => {
      try {
        const size = 16;
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = size;
        const context = canvas.getContext('2d');
        context.drawImage(image, 0, 0, size, size);
        const data = context.getImageData(0, 0, size, size).data;
        const buckets = {};
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i], g = data[i + 1], b = data[i + 2];
          const max = Math.max(r, g, b), min = Math.min(r, g, b);
          const lightness = (max + min) / 2;
          if (lightness > 235 || lightness < 12) continue; // paper white and print black dominate many covers
          const saturation = max ? (max - min) / max : 0;
          const bucket = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
          const weight = 1 + saturation * 3;
          const entry = (buckets[bucket] = buckets[bucket] || { n: 0, r: 0, g: 0, b: 0 });
          entry.n += weight; entry.r += r * weight; entry.g += g * weight; entry.b += b * weight;
        }
        const best = Object.values(buckets).sort((a, b) => b.n - a.n)[0];
        if (!best) throw new Error('no colour');
        let rgb = [best.r / best.n, best.g / best.n, best.b / best.n].map(Math.round);
        // Clamp lightness into a spine-friendly range so the label stays legible.
        const max = Math.max(...rgb), min = Math.min(...rgb), lightness = (max + min) / 2;
        if (lightness > 170) rgb = rgb.map((v) => Math.round(v * 170 / lightness));
        if (lightness < 40) rgb = rgb.map((v) => Math.round(v + (40 - lightness)));
        colorCache[key] = rgb;
        saveColors();
        callback(rgb);
      } catch { callback(null); } // a cross-origin (uncached) cover taints the canvas: caller falls back
    };
    image.onerror = () => callback(null);
    image.src = `/api/books/${id}/cover?v=${version}`;
  }

  function makeSpine(tile) {
    const { w, h } = spineDims(tile);
    const spine = document.createElement('button');
    spine.type = 'button';
    spine.className = `spine${w < 44 ? ' narrow' : ''}${w >= 56 ? ' wide' : ''}`;
    spine.dataset.id = tile.dataset.id;
    spine.dataset.status = tile.dataset.status;
    spine.dataset.lookup = tile.dataset.lookup;
    spine.style.width = `${w}px`;
    spine.style.height = `${h}px`;
    spine.title = tile.dataset.title + (tile.dataset.author ? ` — ${tile.dataset.author}` : '');
    const fallback = fallbackColor(tile);
    spine.style.setProperty('--spine', `rgb(${fallback})`);
    spine.classList.add(textClass(fallback));
    spine.innerHTML = '<span class="ribbon"></span><span class="thumb"><img alt="" loading="lazy"></span>'
      + '<span class="lbl"><span class="t"></span><span class="a"></span></span><span class="band"></span>';
    const thumb = spine.querySelector('.thumb img');
    thumb.src = `/api/books/${tile.dataset.id}/cover?v=${tile.dataset.version}`;
    thumb.onerror = () => thumb.parentNode.remove();
    spine.querySelector('.t').textContent = tile.dataset.title;
    spine.querySelector('.a').textContent = tile.dataset.author;
    // Prefer the server-rendered spine image; fall back to a colour sampled from the cover.
    const image = new Image();
    image.onload = () => { spine.style.backgroundImage = `url("${image.src}")`; spine.classList.add('img'); };
    image.onerror = () => sampleCover(tile.dataset.id, tile.dataset.version, (rgb) => {
      if (!rgb) return;
      spine.style.setProperty('--spine', `rgb(${rgb})`);
      spine.classList.remove('light', 'dark');
      spine.classList.add(textClass(rgb));
    });
    image.src = `/api/books/${tile.dataset.id}/spine?w=${w}&h=${h}&v=${tile.dataset.version}`
      + `&r=${shelf.dataset.spineRev || 0}&s=${tile.dataset.spineSrc || ''}`;
    spine.onclick = () => pullOut(tile);
    return spine;
  }

  function build() {
    shelf.innerHTML = '';
    if (!tiles.length) { shelf.innerHTML = '<p class="shelf-empty">No books found.</p>'; return; }
    const groups = new Map();
    tiles.forEach((tile) => {
      if (!groups.has(tile.dataset.shelf)) groups.set(tile.dataset.shelf, []);
      groups.get(tile.dataset.shelf).push(tile);
    });
    const keys = ORDER.filter((key) => groups.has(key));
    const probe = document.createElement('div');
    probe.className = 'shelf-case';
    probe.style.visibility = 'hidden';
    shelf.append(probe);
    const innerWidth = probe.clientWidth - (root.dataset.density === 'mobile' ? 16 + 4 : 28 + 12);
    probe.remove();
    keys.forEach((key) => {
      const group = document.createElement('div');
      group.className = 'shelf-group';
      if (keys.length > 1) {
        const plaque = document.createElement('div');
        plaque.className = `shelf-plaque ${key.toLowerCase()}`;
        plaque.innerHTML = `${key} <span class="n"></span>`;
        plaque.querySelector('.n').textContent = `· ${groups.get(key).length}`;
        group.append(plaque);
      }
      const bookcase = document.createElement('div');
      bookcase.className = 'shelf-case';
      let row = null;
      let used = Infinity;
      groups.get(key).forEach((tile) => {   // greedy row packing
        const spine = makeSpine(tile);
        const width = parseInt(spine.style.width, 10) + 3;
        if (used + width > innerWidth) {
          row = document.createElement('div');
          row.className = 'shelf-rowbox';
          bookcase.append(row);
          used = 0;
        }
        row.append(spine);
        used += width;
      });
      group.append(bookcase);
      shelf.append(group);
    });
    const legend = document.createElement('div');
    legend.className = 'shelf-legend';
    legend.innerHTML = '<span><i style="background:var(--amber)"></i>Reading</span>'
      + '<span><i style="background:var(--ok)"></i>Finished</span>'
      + '<span><i style="background:var(--muted)"></i>Paused</span><span>tap a spine to pull it out</span>';
    shelf.append(legend);
  }

  // The rail shows the spine as it appears on the shelf; tapping it opens the file so save/share stay native.
  function fillRail(tile) {
    const { w, h } = spineDims(tile);
    const railHeight = Math.min(560, Math.round(240 * h / w)); // /spine clamps w 24-240, h 80-800
    const railWidth = Math.round(railHeight * w / h);
    const url = `/api/books/${tile.dataset.id}/spine?w=${railWidth}&h=${railHeight}&v=${tile.dataset.version}`
      + `&r=${shelf.dataset.spineRev || 0}&s=${tile.dataset.spineSrc || ''}`;
    rail.textContent = '';
    const figure = document.createElement('figure');
    figure.className = 'sm-spine';
    const link = document.createElement('a');
    link.href = url; link.target = '_blank'; link.rel = 'noopener';
    const image = document.createElement('img');
    image.alt = 'Spine';
    image.src = url;
    image.onerror = () => { figure.hidden = true; };
    link.append(image);
    figure.append(link);
    rail.append(figure);
  }

  // Pull-out: move the real tile into the dialog so every existing action keeps working; put it back on close.
  let placeholder = null;
  let current = null;
  function pullOut(tile) {
    if (current) putBack();
    placeholder = document.createComment('shelf-placeholder');
    tile.parentNode.insertBefore(placeholder, tile);
    body.append(tile);
    current = tile;
    document.querySelectorAll('details.book[open]').forEach((other) => { if (other !== tile) other.open = false; });
    fillRail(tile);
    tile.open = true;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    modal.querySelector('.sm-dialog').scrollTop = 0;
    modal.querySelector('.sm-close').focus();
  }
  function putBack() {
    if (!current) return;
    current.open = false;
    placeholder.parentNode.insertBefore(current, placeholder);
    placeholder.remove();
    const id = current.dataset.id;
    current = null;
    placeholder = null;
    modal.hidden = true;
    rail.textContent = '';
    document.body.style.overflow = '';
    const spine = shelf.querySelector(`.spine[data-id="${id}"]`);
    if (spine) spine.focus();
  }
  modal.querySelector('.sm-close').onclick = putBack;
  modal.querySelector('.sm-backdrop').onclick = putBack;
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) putBack(); });
  // Actions reload the page; remember which book was pulled out so it comes back open.
  window.addEventListener('pagehide', () => {
    if (current) sessionStorage.setItem('rt-reopen', current.dataset.id);
    else sessionStorage.removeItem('rt-reopen');
  });

  let built = false;
  let resizeTimer = null;
  function applyView() {
    const view = localStorage.getItem('rt-view') === 'shelf' ? 'shelf' : 'list';
    root.dataset.view = view;
    document.querySelectorAll('.viewswitch button').forEach((button) => {
      button.classList.toggle('on', button.dataset.view === view);
    });
    if (view === 'shelf') {
      shelf.hidden = false;
      if (!built) { build(); built = true; }
    } else {
      if (current) putBack();
      shelf.hidden = true;
    }
  }
  document.querySelectorAll('.viewswitch button').forEach((button) => {
    button.onclick = () => { localStorage.setItem('rt-view', button.dataset.view); applyView(); };
  });
  window.addEventListener('resize', () => {
    if (root.dataset.view !== 'shelf') return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (current) putBack(); build(); }, 150);
  });
  document.querySelectorAll('.segmented button[data-v]').forEach((button) => {
    button.addEventListener('click', () => { if (built) { if (current) putBack(); build(); } });
  });
  applyView();

  const reopen = sessionStorage.getItem('rt-reopen');
  sessionStorage.removeItem('rt-reopen');
  if (reopen && root.dataset.view === 'shelf') {
    const tile = tiles.find((candidate) => candidate.dataset.id === reopen);
    if (tile) pullOut(tile);
  }
})();
