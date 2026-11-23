/**
 * OriginCast Modern Media Player Engine
 * Powered by Artplayer with Custom Subtitle & Web Audio Suite
 */

window.OriginCastAudioEngine = {
    ctx: null,
    source: null,
    gainNode: null,
    compressorNode: null,
    pannerNode: null,
    isInitialized: false,
    currentBoost: 1.0,
    nightMode: false,
    channelMode: 'stereo',
    externalAudio: null,

    init(videoEl) {
        if (this.isInitialized || !videoEl) return;
        try {
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!AudioCtx) return;
            this.ctx = new AudioCtx();
            this.source = this.ctx.createMediaElementSource(videoEl);
            this.gainNode = this.ctx.createGain();
            this.compressorNode = this.ctx.createDynamicsCompressor();

            // Set speech compressor tuning (night mode / dialogue clarity)
            this.compressorNode.threshold.value = -24;
            this.compressorNode.knee.value = 30;
            this.compressorNode.ratio.value = 12;
            this.compressorNode.attack.value = 0.003;
            this.compressorNode.release.value = 0.25;

            // Default chain: source -> gain -> destination
            this.source.connect(this.gainNode);
            this.gainNode.connect(this.ctx.destination);
            this.isInitialized = true;
        } catch (e) {
            console.warn("[OriginCast] Web Audio API initialization skipped or restricted:", e);
        }
    },

    resume() {
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
    },

    setBoost(factor) {
        this.resume();
        this.currentBoost = factor;
        if (this.gainNode) {
            this.gainNode.gain.setValueAtTime(factor, this.ctx.currentTime);
        }
    },

    toggleNightMode(enable) {
        this.resume();
        if (!this.isInitialized || !this.source) return;
        this.nightMode = (enable !== undefined) ? enable : !this.nightMode;
        try {
            this.source.disconnect();
            if (this.nightMode) {
                this.source.connect(this.compressorNode);
                this.compressorNode.connect(this.gainNode);
            } else {
                this.source.connect(this.gainNode);
            }
        } catch (e) {
            console.warn("[OriginCast] Night mode toggle error:", e);
        }
        return this.nightMode;
    },

    loadExternalAudio(file, videoEl) {
        if (!file || !videoEl) return;
        const audioUrl = URL.createObjectURL(file);
        if (this.externalAudio) {
            this.externalAudio.pause();
            this.externalAudio.src = '';
        }
        const audio = new Audio(audioUrl);
        audio.volume = videoEl.volume;
        audio.currentTime = videoEl.currentTime;

        // Sync playback
        videoEl.addEventListener('play', () => audio.play());
        videoEl.addEventListener('pause', () => audio.pause());
        videoEl.addEventListener('seeking', () => audio.currentTime = videoEl.currentTime);
        videoEl.addEventListener('volumechange', () => audio.volume = videoEl.muted ? 0 : videoEl.volume);

        // Mute video audio so external audio plays
        videoEl.muted = true;
        this.externalAudio = audio;
        if (!videoEl.paused) audio.play();
    }
};

window.OriginCastSubtitleManager = {
    tracks: [],
    activeTrackIndex: -1,
    offset: 0,
    fontSize: '20px',
    color: '#ffffff',
    bg: 'transparent',

    srtToVtt(srtText) {
        let clean = srtText.replace(/^\uFEFF/, '').trim();
        // Replace comma with dot in timestamps 00:00:00,000 --> 00:00:00.000
        clean = clean.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, '$1.$2');
        return 'WEBVTT\n\n' + clean;
    },

    createVttBlobUrl(text) {
        const vtt = text.trim().startsWith('WEBVTT') ? text : this.srtToVtt(text);
        const blob = new Blob([vtt], { type: 'text/vtt;charset=utf-8' });
        return URL.createObjectURL(blob);
    }
};

window.OriginCastPlayer = {
    art: null,
    options: null,
    nextCountdownTimer: null,

    init(opts) {
        this.options = opts;
        const container = document.querySelector(opts.container || '#player-container');
        if (!container) return;

        // Check if Artplayer is available
        if (typeof Artplayer === 'undefined') {
            console.error("[OriginCast] Artplayer library not loaded");
            return;
        }

        // Subtitles list
        const initialSubs = opts.subtitles || [];
        OriginCastSubtitleManager.tracks = initialSubs;

        const defaultSub = initialSubs.length > 0 ? initialSubs[0] : null;

        const artOptions = {
            container: container,
            url: opts.url,
            poster: opts.poster || '',
            title: opts.title || 'OriginCast Cinema',
            volume: 0.8,
            isLive: false,
            muted: false,
            autoplay: false,
            pip: true,
            autoSize: false,
            autoMini: false,
            screenshot: true,
            setting: true,
            loop: false,
            flip: true,
            playbackRate: true,
            aspectRatio: true,
            fullscreen: true,
            fullscreenWeb: true,
            subtitleOffset: true,
            miniProgressBar: true,
            theme: '#6366f1',
            icons: {
                loading: '<div class="w-12 h-12 rounded-full border-4 border-indigo-500 border-t-transparent animate-spin"></div>'
            },
            moreVideoAttr: {
                playsinline: true,
                'webkit-playsinline': true,
                crossOrigin: 'anonymous'
            }
        };

        if (defaultSub) {
            artOptions.subtitle = {
                url: defaultSub.url,
                type: defaultSub.type || 'vtt',
                style: {
                    color: '#fff',
                    fontSize: '20px',
                    textShadow: '0 2px 4px rgba(0,0,0,0.9), 0 0 2px #000',
                    fontWeight: '600'
                },
                encoding: 'utf-8',
                escape: false
            };
            OriginCastSubtitleManager.activeTrackIndex = 0;
        }

        const art = new Artplayer(artOptions);
        this.art = art;

        // Setup Player Top Bar Overlay
        this.setupTopBar(art, opts);

        // Setup Controls (Rewind, Fast Forward, Next Ep, Subtitles, Audio Track)
        this.setupControls(art, opts);

        // Setup Settings Menu (Subtitles settings, Audio Boost, Night Mode)
        this.setupSettings(art, opts);

        // Setup Events & Web Audio initialization on interaction
        this.setupEvents(art, opts);

        // Setup Drag & Drop for Subtitles
        this.setupDragAndDrop(art);

        // Setup Global Keyboard Shortcuts
        this.setupKeyboardShortcuts(art, opts);

        return art;
    },

    setupTopBar(art, opts) {
        art.layers.add({
            name: 'top-bar',
            html: `
                <div class="art-top-bar absolute top-0 inset-x-0 p-4 sm:p-6 bg-gradient-to-b from-black/80 via-black/40 to-transparent flex items-center justify-between text-white pointer-events-auto opacity-0 group-hover:opacity-100 transition-opacity duration-300 z-20">
                    <div class="flex items-center space-x-3 truncate">
                        <button onclick="window.history.back()" class="p-2 rounded-xl bg-slate-900/80 hover:bg-slate-800 text-slate-300 hover:text-white border border-slate-700/60 backdrop-blur-md transition-colors" title="Back">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
                        </button>
                        <div class="truncate">
                            <h2 class="text-base sm:text-lg font-bold tracking-tight text-white drop-shadow-md truncate">${opts.title || ''}</h2>
                            ${opts.subtitleText ? `<p class="text-xs text-indigo-300 font-medium drop-shadow">${opts.subtitleText}</p>` : ''}
                        </div>
                    </div>
                    <div class="flex items-center space-x-2 flex-shrink-0">
                        <span class="px-2 py-0.5 rounded text-[11px] font-bold bg-indigo-600/90 text-white backdrop-blur-md shadow-sm">1080p FHD</span>
                        <span class="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-900/80 text-emerald-400 border border-slate-700/60 backdrop-blur-md shadow-sm">LOCAL STREAM</span>
                    </div>
                </div>
            `
        });
    },

    setupControls(art, opts) {
        // 1. Rewind 10s Button
        art.controls.add({
            name: 'rewind10',
            position: 'left',
            index: 10,
            html: `
                <button class="art-icon-custom p-1.5 hover:text-indigo-400 transition-colors" title="Rewind 10s (Left Arrow)">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0019 16V8a1 1 0 00-1.6-.8l-5.334 4zM4.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0011 16V8a1 1 0 00-1.6-.8l-5.334 4z"/>
                    </svg>
                </button>
            `,
            click: function () {
                art.seek = Math.max(0, art.currentTime - 10);
                art.notice.show = 'Rewind 10s';
            }
        });

        // 2. Forward 10s Button
        art.controls.add({
            name: 'forward10',
            position: 'left',
            index: 11,
            html: `
                <button class="art-icon-custom p-1.5 hover:text-indigo-400 transition-colors" title="Forward 10s (Right Arrow)">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.933 12.8a1 1 0 000-1.6L6.6 7.2A1 1 0 005 8v8a1 1 0 001.6.8l5.333-4zM19.933 12.8a1 1 0 000-1.6l-5.333-4A1 1 0 0013 8v8a1 1 0 001.6.8l5.333-4z"/>
                    </svg>
                </button>
            `,
            click: function () {
                art.seek = Math.min(art.duration, art.currentTime + 10);
                art.notice.show = 'Forward 10s';
            }
        });

        // 3. Next Episode Button (TV series only)
        if (opts.hasNextEpisode && opts.onNextEpisode) {
            art.controls.add({
                name: 'next-ep',
                position: 'left',
                index: 12,
                html: `
                    <button class="art-icon-custom p-1.5 text-indigo-400 hover:text-white transition-colors" title="Next Episode (N)">
                        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                            <path d="M4.516 7.548c0-.923 1.02-1.48 1.8-.988l7.108 4.452a1.166 1.166 0 010 1.976L6.316 17.44c-.78.492-1.8-.065-1.8-.988V7.548z"/>
                            <path d="M15.484 7a1 1 0 00-1 1v8a1 1 0 102 0V8a1 1 0 00-1-1z"/>
                        </svg>
                    </button>
                `,
                click: function () {
                    opts.onNextEpisode();
                }
            });
        }

        // 4. Subtitles Dropdown Control (CC)
        art.controls.add({
            name: 'cc-subtitles',
            position: 'right',
            index: 20,
            html: `
                <div class="art-control-cc flex items-center space-x-1 cursor-pointer p-1 hover:text-indigo-400 font-bold text-xs" title="Subtitles (C)">
                    <span class="border border-current px-1 py-0.5 rounded text-[10px] uppercase font-mono tracking-wider">CC</span>
                </div>
            `,
            click: function () {
                // Toggle subtitle visibility
                art.subtitle.show = !art.subtitle.show;
                art.notice.show = art.subtitle.show ? 'Subtitles: Enabled' : 'Subtitles: Disabled';
            }
        });

        // 5. Audio Boost Badge / Quick Indicator
        art.controls.add({
            name: 'audio-boost-indicator',
            position: 'right',
            index: 21,
            html: `
                <div class="art-control-audio flex items-center cursor-pointer p-1 text-slate-300 hover:text-indigo-400" title="Audio Suite & Equalizer">
                    <svg class="w-4 h-4 mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>
                    <span id="audio-boost-label" class="text-[10px] font-mono font-bold bg-slate-800 px-1 py-0.5 rounded">100%</span>
                </div>
            `,
            click: function () {
                // Cycle Audio Boost: 100% -> 150% -> 200% -> 300%
                const boosts = [1.0, 1.5, 2.0, 3.0];
                const labels = ['100%', '150%', '200%', '300%'];
                let currIdx = boosts.indexOf(window.OriginCastAudioEngine.currentBoost);
                if (currIdx === -1) currIdx = 0;
                const nextIdx = (currIdx + 1) % boosts.length;
                const newBoost = boosts[nextIdx];
                window.OriginCastAudioEngine.setBoost(newBoost);
                const el = document.getElementById('audio-boost-label');
                if (el) el.textContent = labels[nextIdx];
                art.notice.show = `Audio Volume Boost: ${labels[nextIdx]}`;
            }
        });
    },

    setupSettings(art, opts) {
        // --- 1. Subtitles Sub-menu in Settings ---
        const subList = OriginCastSubtitleManager.tracks.map((s, idx) => ({
            html: `<span>${s.name} (${s.type.toUpperCase()})</span>`,
            default: idx === 0,
            url: s.url,
            index: idx
        }));

        subList.unshift({
            html: '<span>Disabled (Off)</span>',
            default: subList.length === 0,
            url: null,
            index: -1
        });

        art.setting.add({
            name: 'subtitles-menu',
            width: 260,
            html: 'Subtitles & Captions',
            tooltip: subList.length > 1 ? subList[1].html : 'Off',
            selector: subList,
            onSelect: function (item) {
                if (!item.url) {
                    art.subtitle.show = false;
                    art.notice.show = 'Subtitles: Off';
                } else {
                    art.subtitle.switch(item.url, {
                        name: item.html.replace(/<[^>]*>/g, ''),
                        type: 'vtt'
                    });
                    art.subtitle.show = true;
                    OriginCastSubtitleManager.activeTrackIndex = item.index;
                    art.notice.show = `Loaded: ${item.html.replace(/<[^>]*>/g, '')}`;
                }
                return item.html;
            }
        });

        // Load Custom Subtitle File Picker
        art.setting.add({
            name: 'load-custom-subtitle',
            html: '📂 Load Subtitle File (.srt, .vtt)',
            click: function () {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = '.srt,.vtt,.sub,.ass';
                input.onchange = function (e) {
                    const file = e.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = function (evt) {
                        const blobUrl = OriginCastSubtitleManager.createVttBlobUrl(evt.target.result);
                        art.subtitle.switch(blobUrl, { name: file.name, type: 'vtt' });
                        art.subtitle.show = true;
                        art.notice.show = `Loaded Subtitle: ${file.name}`;
                    };
                    reader.readAsText(file);
                };
                input.click();
            }
        });

        // Subtitle Sync / Delay
        art.setting.add({
            name: 'subtitle-offset',
            width: 250,
            html: 'Subtitle Sync / Delay',
            tooltip: '0.0s',
            selector: [
                { html: '-2.0s Delay', value: -2.0 },
                { html: '-1.0s Delay', value: -1.0 },
                { html: '-0.5s Delay', value: -0.5 },
                { html: '0.0s (Normal)', value: 0.0, default: true },
                { html: '+0.5s Ahead', value: 0.5 },
                { html: '+1.0s Ahead', value: 1.0 },
                { html: '+2.0s Ahead', value: 2.0 },
            ],
            onSelect: function (item) {
                art.subtitleOffset = item.value;
                art.notice.show = `Subtitle Sync: ${item.value > 0 ? '+' : ''}${item.value}s`;
                return item.html;
            }
        });

        // --- 2. Audio Suite in Settings ---
        // Native Audio Track Selection (if detected in HTML5 video)
        art.setting.add({
            name: 'audio-tracks-menu',
            width: 260,
            html: 'Audio Track Stream',
            tooltip: 'Track 1 (Default)',
            selector: [
                { html: 'Track 1: Main Audio (Stereo)', default: true, index: 0 },
                { html: 'Track 2: Alternate Track', index: 1 }
            ],
            onSelect: function (item) {
                const v = art.video;
                if (v && v.audioTracks && v.audioTracks.length > 0) {
                    for (let i = 0; i < v.audioTracks.length; i++) {
                        v.audioTracks[i].enabled = (i === item.index);
                    }
                    art.notice.show = `Audio: ${item.html.replace(/<[^>]*>/g, '')}`;
                } else {
                    art.notice.show = `Selected: ${item.html.replace(/<[^>]*>/g, '')}`;
                }
                return item.html;
            }
        });

        // Volume Boost (100% - 300%)
        art.setting.add({
            name: 'audio-boost-menu',
            width: 220,
            html: 'Audio Boost (Equalizer)',
            tooltip: '100% (Normal)',
            selector: [
                { html: '100% (Normal)', value: 1.0, default: true },
                { html: '150% (Medium Boost)', value: 1.5 },
                { html: '200% (High Boost)', value: 2.0 },
                { html: '300% (Maximum Boost)', value: 3.0 }
            ],
            onSelect: function (item) {
                window.OriginCastAudioEngine.setBoost(item.value);
                const el = document.getElementById('audio-boost-label');
                if (el) el.textContent = `${Math.round(item.value * 100)}%`;
                art.notice.show = `Audio Boost: ${item.html}`;
                return item.html;
            }
        });

        // Night Mode / Speech Clarity (Compressor)
        art.setting.add({
            name: 'audio-night-mode',
            width: 220,
            html: 'Night Mode (Speech Clarity)',
            tooltip: 'Off',
            switch: false,
            onSwitch: function (item) {
                const state = !item.switch;
                window.OriginCastAudioEngine.toggleNightMode(state);
                art.notice.show = state ? 'Night Mode: Dialogue Clarity Active' : 'Night Mode: Off';
                return state;
            }
        });

        // External Audio File Loader
        art.setting.add({
            name: 'load-external-audio',
            html: '🎵 Load External Audio File...',
            click: function () {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'audio/*';
                input.onchange = function (e) {
                    const file = e.target.files[0];
                    if (!file) return;
                    window.OriginCastAudioEngine.loadExternalAudio(file, art.video);
                    art.notice.show = `Loaded External Audio: ${file.name}`;
                };
                input.click();
            }
        });
    },

    setupEvents(art, opts) {
        // Initialize Web Audio graph on first user interaction
        art.on('play', () => {
            window.OriginCastAudioEngine.init(art.video);
            window.OriginCastAudioEngine.resume();
        });

        // Auto-play Next Episode on ended (TV series)
        if (opts.hasNextEpisode && opts.onNextEpisode) {
            art.on('video:ended', () => {
                let secondsLeft = 5;
                art.notice.show = `Up Next: Episode in ${secondsLeft}s...`;
                this.nextCountdownTimer = setInterval(() => {
                    secondsLeft -= 1;
                    if (secondsLeft <= 0) {
                        clearInterval(this.nextCountdownTimer);
                        opts.onNextEpisode();
                    } else {
                        art.notice.show = `Up Next: Episode in ${secondsLeft}s...`;
                    }
                }, 1000);
            });
        }
    },

    setupDragAndDrop(art) {
        const container = art.template.$player;
        if (!container) return;

        ['dragenter', 'dragover'].forEach(name => {
            container.addEventListener(name, (e) => {
                e.preventDefault();
                e.stopPropagation();
                container.classList.add('ring-4', 'ring-indigo-500', 'ring-inset');
            });
        });

        ['dragleave', 'drop'].forEach(name => {
            container.addEventListener(name, (e) => {
                e.preventDefault();
                e.stopPropagation();
                container.classList.remove('ring-4', 'ring-indigo-500', 'ring-inset');
            });
        });

        container.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            if (!dt || !dt.files || dt.files.length === 0) return;
            const file = dt.files[0];
            const name = file.name.toLowerCase();

            if (name.endsWith('.srt') || name.endsWith('.vtt') || name.endsWith('.sub') || name.endsWith('.ass')) {
                const reader = new FileReader();
                reader.onload = (evt) => {
                    const blobUrl = OriginCastSubtitleManager.createVttBlobUrl(evt.target.result);
                    art.subtitle.switch(blobUrl, { name: file.name, type: 'vtt' });
                    art.subtitle.show = true;
                    art.notice.show = `Loaded Subtitle: ${file.name}`;
                };
                reader.readAsText(file);
            } else if (name.endsWith('.mp3') || name.endsWith('.m4a') || name.endsWith('.aac') || name.endsWith('.wav')) {
                window.OriginCastAudioEngine.loadExternalAudio(file, art.video);
                art.notice.show = `Loaded External Audio: ${file.name}`;
            }
        });
    },

    setupKeyboardShortcuts(art, opts) {
        window.addEventListener('keydown', (e) => {
            // Ignore if user is typing in an input or textarea
            if (['input', 'textarea'].includes(document.activeElement.tagName.toLowerCase())) return;

            switch (e.key.toLowerCase()) {
                case 'c':
                    art.subtitle.show = !art.subtitle.show;
                    art.notice.show = art.subtitle.show ? 'Subtitles: On' : 'Subtitles: Off';
                    break;
                case 'b':
                    const el = document.querySelector('.art-control-audio');
                    if (el) el.click();
                    break;
                case 'n':
                    if (opts.hasNextEpisode && opts.onNextEpisode) opts.onNextEpisode();
                    break;
                case '?':
                    this.showShortcutsModal();
                    break;
            }
        });
    },

    showShortcutsModal() {
        let modal = document.getElementById('shortcuts-modal');
        if (modal) {
            modal.classList.toggle('hidden');
            return;
        }
        modal = document.createElement('div');
        modal.id = 'shortcuts-modal';
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in';
        modal.innerHTML = `
            <div class="max-w-md w-full bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl text-white space-y-4">
                <div class="flex items-center justify-between pb-3 border-b border-slate-800">
                    <h3 class="text-base font-bold text-white flex items-center space-x-2">
                        <span>⌨️ Player Keyboard Shortcuts</span>
                    </h3>
                    <button onclick="document.getElementById('shortcuts-modal').classList.add('hidden')" class="text-slate-400 hover:text-white">&times;</button>
                </div>
                <div class="grid grid-cols-2 gap-2.5 text-xs">
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Play / Pause</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">Space</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Fullscreen</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">F</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Seek &plusmn;5s</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">&larr; / &rarr;</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Volume &plusmn;10%</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">&uarr; / &darr;</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Subtitles (CC)</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">C</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Audio Boost</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">B</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Mute Toggle</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">M</kbd></div>
                    <div class="flex justify-between p-2 rounded bg-slate-950 border border-slate-800"><span class="text-slate-400">Shortcuts Help</span><kbd class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-mono">?</kbd></div>
                </div>
                <p class="text-[11px] text-slate-400 text-center pt-2">Tip: You can drag and drop any .srt or .vtt subtitle file directly onto the player!</p>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });
    }
};
