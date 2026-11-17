const OFFLINE_CACHE = "mymusic-offline-audio-v1";
const bodyEl = document.body;
const currentUser = {
    id: Number(bodyEl.dataset.userId),
    username: bodyEl.dataset.username,
    role: bodyEl.dataset.role,
};
const canEdit = currentUser.role === "admin" || currentUser.role === "trusted";

const state = {
    tracks: [],
    playlists: [],
    queue: [],
    queueIndex: -1,
    shuffle: false,
    repeat: false,
    currentView: "home",
    activePlaylistId: null,
    offlineTrackIds: new Set(),
    pickTrackId: null,
    importTimer: null,
};

const audio = document.getElementById("audio-player");
const seekBar = document.getElementById("seek-bar");
const volumeBar = document.getElementById("volume-bar");

// =========================
// HELPERS & API
// =========================
async function api(url, options = {}) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
}

function fmtTime(sec) {
    if (!sec || !isFinite(sec)) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? "0" : ""}${s}`;
}

function toast(msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 2800);
}

function esc(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
}

function coverUrl(track) {
    return track?.cover_path ? `/covers/${encodeURIComponent(track.cover_path)}` : null;
}

// =========================
// PWA & OFFLINE CACHE
// =========================
if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
}

async function refreshOfflineIds() {
    if (!("caches" in window)) return;
    const cache = await caches.open(OFFLINE_CACHE);
    const keys = await cache.keys();
    const ids = new Set();
    keys.forEach((req) => {
        const m = new URL(req.url).pathname.match(/^\/stream\/(\d+)$/);
        if (m) ids.add(Number(m[1]));
    });
    state.offlineTrackIds = ids;
    const statEl = document.getElementById("stat-offline");
    if (statEl) statEl.textContent = ids.size;
}

async function toggleOfflineTrack(track) {
    if (!("caches" in window)) {
        toast("Offline caching is not supported in this browser.");
        return;
    }
    const cache = await caches.open(OFFLINE_CACHE);
    const streamUrl = `/stream/${track.id}`;
    if (state.offlineTrackIds.has(track.id)) {
        await cache.delete(streamUrl);
        if (track.cover_path) await cache.delete(coverUrl(track));
        toast(`Removed "${track.title}" from offline storage.`);
    } else {
        toast(`Saving "${track.title}" for offline playback...`);
        await cache.add(streamUrl);
        if (track.cover_path) await cache.add(coverUrl(track)).catch(() => {});
        toast(`Saved "${track.title}" offline!`);
    }
    await refreshOfflineIds();
    updateNowPlayingModal();
    if (state.currentView === "offline") renderCurrentView();
}

// =========================
// AUDIO PLAYER & MEDIA SESSION
// =========================
function currentTrack() {
    return state.queue[state.queueIndex] || null;
}

function playTrack(track, queueList = null) {
    if (queueList) {
        state.queue = [...queueList];
        state.queueIndex = state.queue.findIndex((t) => t.id === track.id);
    } else {
        const idx = state.queue.findIndex((t) => t.id === track.id);
        if (idx >= 0) {
            state.queueIndex = idx;
        } else {
            state.queue.push(track);
            state.queueIndex = state.queue.length - 1;
        }
    }

    audio.src = `/stream/${track.id}`;
    audio.play().catch(() => {});
    api(`/api/tracks/${track.id}/play`, { method: "POST" }).catch(() => {});

    updatePlayerUI();
    updateMediaSession(track);
}

function updatePlayerUI() {
    const t = currentTrack();
    const playBtn = document.getElementById("btn-play-pause");
    playBtn.textContent = audio.paused ? "▶" : "⏸";

    if (!t) return;
    document.getElementById("bar-title").textContent = t.title;
    document.getElementById("bar-artist").textContent = `${t.artist} • ${t.album}`;
    document.getElementById("btn-bar-like").textContent = t.liked ? "♥" : "♡";
    document.getElementById("btn-bar-like").classList.toggle("active", Boolean(t.liked));

    const cUrl = coverUrl(t);
    const barImg = document.getElementById("bar-cover");
    const barFallback = document.getElementById("bar-cover-fallback");
    if (cUrl) {
        barImg.src = cUrl;
        barImg.classList.remove("hidden");
        barFallback.classList.add("hidden");
    } else {
        barImg.classList.add("hidden");
        barFallback.classList.remove("hidden");
    }

    document.querySelectorAll(".track-row").forEach((row) => {
        row.classList.toggle("playing", Number(row.dataset.trackId) === t.id);
    });
    updateNowPlayingModal();
}

function updateNowPlayingModal() {
    const t = currentTrack();
    if (!t) return;
    document.getElementById("np-title").textContent = t.title;
    document.getElementById("np-artist").textContent = t.artist;
    document.getElementById("np-album").textContent = t.album;
    document.getElementById("np-lyrics-box").textContent = t.lyrics?.trim() || "No lyrics saved for this track yet.";

    const cUrl = coverUrl(t);
    const img = document.getElementById("np-cover");
    const fb = document.getElementById("np-cover-fallback");
    if (cUrl) {
        img.src = cUrl;
        img.classList.remove("hidden");
        fb.classList.add("hidden");
    } else {
        img.classList.add("hidden");
        fb.classList.remove("hidden");
    }

    const offBtn = document.getElementById("np-btn-offline");
    const isOff = state.offlineTrackIds.has(t.id);
    offBtn.textContent = isOff ? "✓ Saved Offline" : "⬇ Save Offline";
    offBtn.classList.toggle("active", isOff);

    document.getElementById("queue-count").textContent = state.queue.length;
    const qBox = document.getElementById("np-queue-box");
    qBox.innerHTML = state.queue.map((q, i) => `
        <div class="track-row ${i === state.queueIndex ? "playing" : ""}" data-q-idx="${i}">
            <div class="row-cover">♫</div>
            <div class="row-info">
                <div class="row-title">${esc(q.title)}</div>
                <div class="row-meta">${esc(q.artist)}</div>
            </div>
            <span class="row-duration">${fmtTime(q.duration)}</span>
            <div></div>
        </div>
    `).join("");
    qBox.querySelectorAll("[data-q-idx]").forEach((el) => {
        el.onclick = () => {
            state.queueIndex = Number(el.dataset.qIdx);
            playTrack(state.queue[state.queueIndex]);
        };
    });
}

function updateMediaSession(track) {
    if (!("mediaSession" in navigator)) return;
    const art = coverUrl(track);
    navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        album: track.album,
        artwork: art ? [{ src: art, sizes: "512x512" }] : [],
    });
    navigator.mediaSession.setActionHandler("play", () => audio.play());
    navigator.mediaSession.setActionHandler("pause", () => audio.pause());
    navigator.mediaSession.setActionHandler("previoustrack", playPrev);
    navigator.mediaSession.setActionHandler("nexttrack", playNext);
}

function playNext() {
    if (!state.queue.length) return;
    if (state.repeat) {
        audio.currentTime = 0;
        audio.play();
        return;
    }
    if (state.shuffle && state.queue.length > 1) {
        let nextIdx = state.queueIndex;
        while (nextIdx === state.queueIndex) nextIdx = Math.floor(Math.random() * state.queue.length);
        state.queueIndex = nextIdx;
    } else {
        state.queueIndex = (state.queueIndex + 1) % state.queue.length;
    }
    playTrack(state.queue[state.queueIndex]);
}

function playPrev() {
    if (!state.queue.length) return;
    if (audio.currentTime > 3) {
        audio.currentTime = 0;
        return;
    }
    state.queueIndex = (state.queueIndex - 1 + state.queue.length) % state.queue.length;
    playTrack(state.queue[state.queueIndex]);
}

audio.addEventListener("play", updatePlayerUI);
audio.addEventListener("pause", updatePlayerUI);
audio.addEventListener("ended", playNext);
audio.addEventListener("timeupdate", () => {
    document.getElementById("time-current").textContent = fmtTime(audio.currentTime);
    document.getElementById("time-duration").textContent = fmtTime(audio.duration);
    if (audio.duration) seekBar.value = (audio.currentTime / audio.duration) * 100;
});

seekBar.addEventListener("input", () => {
    if (audio.duration) audio.currentTime = (seekBar.value / 100) * audio.duration;
});

volumeBar.value = localStorage.getItem("mymusic_vol") || "0.9";
audio.volume = Number(volumeBar.value);
volumeBar.addEventListener("input", () => {
    audio.volume = Number(volumeBar.value);
    localStorage.setItem("mymusic_vol", volumeBar.value);
});

document.getElementById("btn-play-pause").onclick = () => {
    if (!currentTrack() && state.tracks.length) return playTrack(state.tracks[0], state.tracks);
    audio.paused ? audio.play() : audio.pause();
};
document.getElementById("btn-next").onclick = playNext;
document.getElementById("btn-prev").onclick = playPrev;
document.getElementById("btn-shuffle").onclick = (e) => {
    state.shuffle = !state.shuffle;
    e.currentTarget.classList.toggle("active", state.shuffle);
};
document.getElementById("btn-repeat").onclick = (e) => {
    state.repeat = !state.repeat;
    e.currentTarget.classList.toggle("active", state.repeat);
};

// =========================
// RENDERING TRACKS & VIEWS
// =========================
function renderCardGrid(container, list) {
    if (!list.length) {
        container.innerHTML = `<p class="muted">Nothing here yet.</p>`;
        return;
    }
    container.innerHTML = list.slice(0, 8).map((t) => {
        const cUrl = coverUrl(t);
        return `
            <div class="music-card" data-play-id="${t.id}">
                <div class="card-cover">${cUrl ? `<img src="${cUrl}" alt="">` : "♫"}</div>
                <div class="card-title">${esc(t.title)}</div>
                <div class="card-sub">${esc(t.artist)}</div>
            </div>
        `;
    }).join("");
    container.querySelectorAll("[data-play-id]").forEach((el) => {
        el.onclick = () => {
            const tr = list.find((x) => x.id === Number(el.dataset.playId));
            if (tr) playTrack(tr, list);
        };
    });
}

function renderTrackList(container, list, options = {}) {
    if (!list.length) {
        container.innerHTML = `<p class="muted">No tracks found.</p>`;
        return;
    }
    const curId = currentTrack()?.id;
    container.innerHTML = list.map((t) => {
        const cUrl = coverUrl(t);
        const isOff = state.offlineTrackIds.has(t.id);
        const canDel = currentUser.role === "admin" || t.added_by === currentUser.id;
        return `
            <div class="track-row ${t.id === curId ? "playing" : ""}" data-track-id="${t.id}">
                <div class="row-cover">${cUrl ? `<img src="${cUrl}" alt="">` : "♫"}</div>
                <div class="row-info">
                    <div class="row-title">${esc(t.title)}</div>
                    <div class="row-meta">${esc(t.artist)} • ${esc(t.album)}</div>
                </div>
                <span class="row-duration">${fmtTime(t.duration)}</span>
                <div class="row-actions">
                    <button class="icon-btn ${t.liked ? "active" : ""}" data-action="like" title="Like">${t.liked ? "♥" : "♡"}</button>
                    <button class="icon-btn ${isOff ? "active" : ""}" data-action="offline" title="Save Offline">${isOff ? "✓" : "⬇"}</button>
                    <button class="icon-btn" data-action="playlist" title="Add to Playlist">＋</button>
                    <button class="icon-btn" data-action="share" title="Copy Share Link">⎘</button>
                    ${canEdit ? `<button class="icon-btn" data-action="edit" title="Edit Tags & Lyrics">✎</button>` : ""}
                    ${options.playlistId ? `<button class="icon-btn" data-action="remove-pl" title="Remove from playlist">✕</button>` : ""}
                    ${canDel && !options.playlistId ? `<button class="icon-btn" data-action="delete" title="Delete Track">🗑</button>` : ""}
                </div>
            </div>
        `;
    }).join("");

    container.querySelectorAll(".track-row").forEach((row) => {
        const id = Number(row.dataset.trackId);
        const track = list.find((x) => x.id === id);
        row.querySelector(".row-cover").onclick = () => playTrack(track, list);
        row.querySelector(".row-info").onclick = () => playTrack(track, list);
        row.querySelectorAll("[data-action]").forEach((btn) => {
            btn.onclick = (e) => {
                e.stopPropagation();
                handleTrackAction(btn.dataset.action, track, options);
            };
        });
    });
}

async function handleTrackAction(action, track, options = {}) {
    if (action === "like") {
        const res = await api(`/api/likes/${track.id}`, { method: "POST" });
        track.liked = res.liked ? 1 : 0;
        const mainT = state.tracks.find((x) => x.id === track.id);
        if (mainT) mainT.liked = track.liked;
        renderCurrentView();
        updatePlayerUI();
    } else if (action === "offline") {
        await toggleOfflineTrack(track);
    } else if (action === "playlist") {
        openPlaylistPicker(track.id);
    } else if (action === "share") {
        const url = `${location.origin}/share/track/${track.id}`;
        navigator.clipboard?.writeText(url).catch(() => {});
        toast("Copied shareable track link!");
    } else if (action === "edit") {
        openEditModal(track);
    } else if (action === "remove-pl") {
        await api(`/api/playlists/${options.playlistId}/tracks`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ track_id: track.id }),
        });
        openPlaylistDetail(options.playlistId);
    } else if (action === "delete") {
        if (!confirm(`Delete "${track.title}" from the server?`)) return;
        await api(`/api/tracks/${track.id}`, { method: "DELETE" });
        toast("Track deleted.");
        await loadTracks();
        renderCurrentView();
    }
}

async function loadTracks(query = "") {
    const data = await api(`/api/tracks?q=${encodeURIComponent(query)}`);
    if (!query) {
        state.tracks = data.tracks;
        document.getElementById("stat-tracks").textContent = state.tracks.length;
    }
    return data.tracks;
}

async function switchView(view) {
    state.currentView = view;
    document.querySelectorAll(".nav-link, .mobile-nav-btn").forEach((el) => {
        el.classList.toggle("active", el.dataset.view === view);
    });
    document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));

    if (view === "home") {
        document.getElementById("view-home").classList.add("active");
    } else if (["search", "library", "liked", "offline"].includes(view)) {
        document.getElementById("view-tracks").classList.add("active");
    } else {
        document.getElementById(`view-${view}`).classList.add("active");
    }
    await renderCurrentView();
}

async function renderCurrentView() {
    const view = state.currentView;
    if (view === "home") {
        const hist = await api("/api/history");
        document.getElementById("stat-plays").textContent = hist.total_plays;
        document.getElementById("recent-count").textContent = `${hist.recent.length} TRACKS`;
        renderCardGrid(document.getElementById("recent-grid"), hist.recent);
        renderTrackList(document.getElementById("top-list"), hist.top);
    } else if (["search", "library", "liked", "offline"].includes(view)) {
        const q = document.getElementById("global-search").value.trim();
        const rescanBtn = document.getElementById("btn-rescan");
        rescanBtn.style.display = view === "library" && canEdit ? "inline-flex" : "none";

        let list = [];
        let title = "All Tracks";
        if (view === "liked") {
            title = "Liked Songs";
            list = (await api("/api/tracks?liked=1")).tracks;
        } else if (view === "offline") {
            title = "Saved Offline";
            list = state.tracks.filter((t) => state.offlineTrackIds.has(t.id));
        } else if (view === "search" || q) {
            title = q ? `Search: "${q}"` : "Search Library";
            list = await loadTracks(q);
        } else {
            list = await loadTracks();
        }
        document.getElementById("tracks-view-title").textContent = title;
        document.getElementById("tracks-view-count").textContent = `${list.length} TRACKS`;
        renderCardGrid(document.getElementById("library-grid"), view === "library" && !q ? list : []);
        document.getElementById("library-grid").classList.toggle("hidden", !(view === "library" && !q));
        renderTrackList(document.getElementById("library-list"), list);
    } else if (view === "playlists") {
        await loadPlaylistsView();
    } else if (view === "import") {
        await loadImportJobs();
    } else if (view === "admin") {
        await loadAdminPanel();
    }
}

// =========================
// PLAYLISTS
// =========================
async function loadPlaylistsView() {
    document.getElementById("playlist-detail").classList.add("hidden");
    document.getElementById("playlists-grid").classList.remove("hidden");
    const data = await api("/api/playlists");
    state.playlists = data.playlists;
    const grid = document.getElementById("playlists-grid");
    if (!state.playlists.length) {
        grid.innerHTML = `<p class="muted">No playlists yet. Create one above!</p>`;
        return;
    }
    grid.innerHTML = state.playlists.map((p) => `
        <div class="music-card" data-pl-id="${p.id}">
            <div class="card-cover">${p.cover_path ? `<img src="/covers/${encodeURIComponent(p.cover_path)}" alt="">` : "☰"}</div>
            <div class="card-title">${esc(p.name)}</div>
            <div class="card-sub">${p.track_count} track(s)</div>
        </div>
    `).join("");
    grid.querySelectorAll("[data-pl-id]").forEach((el) => {
        el.onclick = () => openPlaylistDetail(Number(el.dataset.plId));
    });
}

async function openPlaylistDetail(playlistId) {
    state.activePlaylistId = playlistId;
    const data = await api(`/api/playlists/${playlistId}`);
    const pl = data.playlist;
    document.getElementById("playlists-grid").classList.add("hidden");
    document.getElementById("playlist-detail").classList.remove("hidden");
    document.getElementById("playlist-detail-title").textContent = `${pl.name} • by ${pl.owner_name}`;
    const isOwner = pl.user_id === currentUser.id;
    document.getElementById("btn-delete-playlist").style.display = isOwner ? "inline-flex" : "none";
    renderTrackList(document.getElementById("playlist-tracks-list"), pl.tracks, { playlistId: isOwner ? pl.id : null });
    document.getElementById("btn-play-playlist").onclick = () => {
        if (pl.tracks.length) playTrack(pl.tracks[0], pl.tracks);
    };
    document.getElementById("btn-share-playlist").onclick = () => {
        navigator.clipboard?.writeText(`${location.origin}/share/playlist/${pl.id}`).catch(() => {});
        toast("Copied shareable playlist link!");
    };
    document.getElementById("btn-delete-playlist").onclick = async () => {
        if (!confirm(`Delete playlist "${pl.name}"?`)) return;
        await api(`/api/playlists/${pl.id}`, { method: "DELETE" });
        toast("Playlist deleted.");
        loadPlaylistsView();
    };
}

async function openPlaylistPicker(trackId) {
    state.pickTrackId = trackId;
    const data = await api("/api/playlists");
    const listEl = document.getElementById("playlist-pick-list");
    listEl.innerHTML = data.playlists.length
        ? data.playlists.map((p) => `<div class="admin-item"><span>${esc(p.name)}</span><button class="btn-secondary" data-pick-pl="${p.id}">Add</button></div>`).join("")
        : `<p class="muted">Create a playlist first in the Playlists tab.</p>`;
    listEl.querySelectorAll("[data-pick-pl]").forEach((btn) => {
        btn.onclick = async () => {
            await api(`/api/playlists/${btn.dataset.pickPl}/tracks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ track_id: state.pickTrackId }),
            });
            document.getElementById("modal-playlist-pick").classList.add("hidden");
            toast("Added to playlist!");
        };
    });
    document.getElementById("modal-playlist-pick").classList.remove("hidden");
}

// =========================
// UPLOAD, IMPORT & ADMIN
// =========================
async function loadImportJobs() {
    const data = await api("/api/import");
    const box = document.getElementById("import-jobs-list");
    box.innerHTML = data.jobs.length
        ? data.jobs.map((j) => `
            <div class="job-item">
                <div>
                    <strong>[${esc(j.source_type.toUpperCase())}]</strong> ${esc(j.source_value)}
                    <div class="muted small">${esc(j.message)}</div>
                </div>
                <span class="badge ${esc(j.status)}">${esc(j.status)}</span>
            </div>
        `).join("")
        : `<p class="muted">No import jobs yet.</p>`;
}

async function loadAdminPanel() {
    const [invData, usrData] = await Promise.all([api("/api/invites"), api("/api/users")]);
    document.getElementById("invites-list").innerHTML = invData.invites.map((i) => `
        <div class="admin-item">
            <div><code>${esc(i.code)}</code> <span class="muted small">(${esc(i.role)})</span></div>
            <span class="badge ${i.used_by_name ? "done" : ""}">${i.used_by_name ? `Used by ${esc(i.used_by_name)}` : "Unused"}</span>
        </div>
    `).join("");

    document.getElementById("users-list").innerHTML = usrData.users.map((u) => `
        <div class="admin-item">
            <div><strong>${esc(u.username)}</strong></div>
            ${u.id === currentUser.id
                ? `<span class="badge">${esc(u.role)} (you)</span>`
                : `<select data-user-role="${u.id}" style="width:auto">
                    ${["listener", "trusted", "admin"].map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${r}</option>`).join("")}
                   </select>`}
        </div>
    `).join("");

    document.querySelectorAll("[data-user-role]").forEach((sel) => {
        sel.onchange = async () => {
            await api(`/api/users/${sel.dataset.userRole}/role`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ role: sel.value }),
            });
            toast("User role updated.");
        };
    });
}

// =========================
// EDIT MODAL & WIRE-UP
// =========================
function openEditModal(track) {
    document.getElementById("edit-track-id").value = track.id;
    document.getElementById("edit-title").value = track.title;
    document.getElementById("edit-artist").value = track.artist;
    document.getElementById("edit-album").value = track.album;
    document.getElementById("edit-lyrics").value = track.lyrics || "";
    document.getElementById("edit-cover").value = "";
    document.getElementById("modal-edit").classList.remove("hidden");
}

document.getElementById("edit-track-form").onsubmit = async (e) => {
    e.preventDefault();
    const id = document.getElementById("edit-track-id").value;
    const fd = new FormData();
    fd.append("title", document.getElementById("edit-title").value);
    fd.append("artist", document.getElementById("edit-artist").value);
    fd.append("album", document.getElementById("edit-album").value);
    fd.append("lyrics", document.getElementById("edit-lyrics").value);
    const cov = document.getElementById("edit-cover").files[0];
    if (cov) fd.append("cover", cov);
    await api(`/api/tracks/${id}`, { method: "PATCH", body: fd });
    document.getElementById("modal-edit").classList.add("hidden");
    toast("Song info updated!");
    await loadTracks();
    renderCurrentView();
    updatePlayerUI();
};

document.querySelectorAll(".nav-link, .mobile-nav-btn").forEach((el) => {
    el.onclick = (e) => {
        e.preventDefault();
        switchView(el.dataset.view);
    };
});

document.getElementById("global-search").addEventListener("input", () => {
    if (state.currentView !== "search") switchView("search");
    else renderCurrentView();
});

document.getElementById("btn-play-all").onclick = () => {
    if (state.tracks.length) playTrack(state.tracks[0], state.tracks);
};
document.getElementById("btn-shuffle-all").onclick = () => {
    if (!state.tracks.length) return;
    state.shuffle = true;
    document.getElementById("btn-shuffle").classList.add("active");
    playTrack(state.tracks[Math.floor(Math.random() * state.tracks.length)], state.tracks);
};

document.getElementById("btn-rescan").onclick = async () => {
    const res = await api("/api/scan", { method: "POST" });
    toast(`Scanned ${res.scanned} track(s).`);
    await loadTracks();
    renderCurrentView();
};

document.getElementById("create-playlist-form").onsubmit = async (e) => {
    e.preventDefault();
    const inp = document.getElementById("new-playlist-name");
    await api("/api/playlists", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: inp.value }),
    });
    inp.value = "";
    toast("Playlist created!");
    loadPlaylistsView();
};
document.getElementById("btn-back-playlists").onclick = loadPlaylistsView;

// Modals & Tabs
document.querySelectorAll(".modal-close").forEach((btn) => {
    btn.onclick = () => document.getElementById(btn.dataset.close).classList.add("hidden");
});
document.getElementById("btn-open-now-playing").onclick = () => document.getElementById("modal-player").classList.remove("hidden");
document.getElementById("btn-bar-lyrics").onclick = () => document.getElementById("modal-player").classList.remove("hidden");
document.getElementById("btn-bar-queue").onclick = () => {
    document.getElementById("modal-player").classList.remove("hidden");
    document.querySelector('[data-np-tab="queue"]').click();
};
document.querySelectorAll("[data-np-tab]").forEach((tab) => {
    tab.onclick = () => {
        document.querySelectorAll("[data-np-tab]").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const isLyrics = tab.dataset.npTab === "lyrics";
        document.getElementById("np-lyrics-box").classList.toggle("hidden", !isLyrics);
        document.getElementById("np-queue-box").classList.toggle("hidden", isLyrics);
    };
});

document.getElementById("btn-bar-like").onclick = () => {
    const t = currentTrack();
    if (t) handleTrackAction("like", t);
};
document.getElementById("np-btn-offline").onclick = () => {
    const t = currentTrack();
    if (t) toggleOfflineTrack(t);
};
document.getElementById("np-btn-download").onclick = () => {
    const t = currentTrack();
    if (t) location.href = `/download/${t.id}`;
};
document.getElementById("np-btn-share").onclick = () => {
    const t = currentTrack();
    if (t) handleTrackAction("share", t);
};
document.getElementById("np-btn-playlist").onclick = () => {
    const t = currentTrack();
    if (t) openPlaylistPicker(t.id);
};
const npEditBtn = document.getElementById("np-btn-edit");
if (npEditBtn) {
    npEditBtn.onclick = () => {
        const t = currentTrack();
        if (t) openEditModal(t);
    };
}

if (canEdit) {
    const upInput = document.getElementById("upload-input");
    const dropZone = document.getElementById("drop-zone");
    upInput.onchange = () => {
        document.getElementById("upload-file-count").textContent = `${upInput.files.length} file(s) selected`;
    };
    dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add("dragover"); };
    dropZone.ondragleave = () => dropZone.classList.remove("dragover");
    dropZone.ondrop = (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");
        upInput.files = e.dataTransfer.files;
        upInput.onchange();
    };
    document.getElementById("upload-form").onsubmit = async (e) => {
        e.preventDefault();
        if (!upInput.files.length) return toast("Select audio files first.");
        const fd = new FormData();
        for (const f of upInput.files) fd.append("files", f);
        toast("Uploading & processing...");
        const res = await api("/api/upload", { method: "POST", body: fd });
        toast(`Uploaded ${res.uploaded} track(s) (${res.compressed} compressed to Opus).`);
        upInput.value = "";
        upInput.onchange();
        await loadTracks();
    };
    document.getElementById("import-form").onsubmit = async (e) => {
        e.preventDefault();
        const source_type = document.getElementById("import-type").value;
        const source_value = document.getElementById("import-value").value.trim();
        await api("/api/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source_type, source_value }),
        });
        document.getElementById("import-value").value = "";
        toast("Import job queued!");
        await loadImportJobs();
    };
    document.getElementById("btn-refresh-jobs").onclick = async () => {
        await loadImportJobs();
        await loadTracks();
    };
}

if (currentUser.role === "admin") {
    document.getElementById("invite-form").onsubmit = async (e) => {
        e.preventDefault();
        const role = document.getElementById("invite-role").value;
        const inv = await api("/api/invites", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role }),
        });
        navigator.clipboard?.writeText(inv.code).catch(() => {});
        toast(`Invite code created & copied: ${inv.code}`);
        loadAdminPanel();
    };
}

// =========================
// INIT
// =========================
(async function init() {
    await refreshOfflineIds();
    const me = await api("/api/me");
    const capEl = document.getElementById("capability-note");
    if (capEl) {
        const missing = [];
        if (!me.capabilities.ffmpeg) missing.push("ffmpeg");
        if (!me.capabilities.aria2c) missing.push("aria2c");
        capEl.textContent = missing.length
            ? `Local test note: ${missing.join(" & ")} not installed on this machine (pre-installed in Docker on your server).`
            : "Server tools ready: ffmpeg & aria2c detected.";
    }
    await loadTracks();
    await renderCurrentView();

    const sType = bodyEl.dataset.shareType;
    const sId = Number(bodyEl.dataset.shareId);
    if (sType === "track" && sId) {
        const data = await api(`/api/tracks/${sId}`).catch(() => null);
        if (data?.track) playTrack(data.track, state.tracks);
    } else if (sType === "playlist" && sId) {
        await switchView("playlists");
        await openPlaylistDetail(sId);
    }
})();
