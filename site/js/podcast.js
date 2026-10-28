/* League podcast: published episodes + the weekly NotebookLM episode pack. */

const REPO = 'https://github.com/MidZayAI/league-analytics';

window.Podcast = {
  podcast: null, podSource: '', podSourceOpen: false, podCopied: '',

  async loadPodcast() {
    this.podcast = await fetch('data/podcast.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  },
  // newest week with a pack but no audio yet
  get podNext() { return (this.podcast?.drafts || []).find((d) => !d.has_audio) || null; },
  podUploadUrl() { return `${REPO}/upload/main/site/podcast/episodes`; },
  podEditUrl(d) { return `${REPO}/edit/main/site/${d.source}`; },
  podDuration(sec) { return sec ? `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}` : ''; },
  async podToggleSource(d) {
    this.podSourceOpen = !this.podSourceOpen;
    if (this.podSourceOpen && !this.podSource) {
      this.podSource = await fetch(`${d.source}?v=${d.sha}`, { cache: 'no-cache' }).then((r) => r.text()).catch(() => '');
    }
  },
  async podCopy(what, text) {
    try { await navigator.clipboard.writeText(text); this.podCopied = what; } catch (e) { this.podCopied = ''; }
    setTimeout(() => { if (this.podCopied === what) this.podCopied = ''; }, 2000);
  },
};
