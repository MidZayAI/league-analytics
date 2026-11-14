/* League > Draft & waivers: value above replacement (WAR-style) for draft picks and waiver pickups. */

window.Draft = {
  draftData: null, drTab: 'draft', drOpen: null,

  async loadDraft() {
    if (!this.draftData) {
      this.draftData = await fetch('data/draft.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
  },
  get DR() { return this.draftData?.seasons?.[this.season] || null; },
  drName(tid) { return this.DR?.team_names?.[tid] || ''; },
  drPick(pid) { return (this.DR?.picks || []).find((p) => p.player_id === pid); },
  drAdd(pid, tid) { return (this.DR?.adds || []).find((a) => a.player_id === pid && (tid == null || a.team_id === tid)); },
  // pick board: rounds x teams in real draft order (round 1 order = columns)
  get drBoard() {
    const D = this.DR;
    if (!D) return [];
    const rows = [];
    for (let r = 1; r <= D.rounds; r++) {
      rows.push({ r, cells: D.order.map((tid) => D.picks.find((p) => p.round === r && p.team_id === tid) || null) });
    }
    return rows;
  },
  get drRounds() {
    const D = this.DR;
    if (!D) return [];
    return Array.from({ length: D.rounds }, (_, i) => ({ r: i + 1, picks: D.picks.filter((p) => p.round === i + 1).sort((a, b) => a.spot - b.spot) }));
  },
  // cell color: value vs slot on a diverging blue (steal) / red (bust) ramp, scaled to the season's spread
  drCell(p) {
    void this.themeTick;
    if (!p) return '';
    const vals = this.DR.picks.map((x) => Math.abs(x.vs_slot));
    const top = vals.sort((a, b) => b - a)[Math.floor(vals.length * 0.1)] || 1;
    const score = 0.5 + Math.max(-0.5, Math.min(0.5, p.vs_slot / top / 2));
    const hex = rampColor(RAMPS.div[this.dark ? 'dark' : 'light'], score);
    const strength = Math.round(Math.abs(score - 0.5) * 2 * 60);
    return strength ? `background: color-mix(in srgb, ${hex} ${strength}%, transparent)` : '';
  },
  drStatus(p) {
    if (!p) return '';
    if (p.status === 'traded') return `traded ${fmt.date(p.left)}`;
    if (p.status === 'dropped') return `dropped ${fmt.date(p.left)}`;
    if (p.status === 'never rostered') return 'never rostered';
    return '';
  },
  // last name without suffixes (Jaren Jackson Jr. -> Jackson)
  drLast(name) {
    const parts = String(name || '').split(' ').filter((w) => !/^(jr\.?|sr\.?|ii|iii|iv)$/i.test(w));
    return parts.slice(-1)[0] || name;
  },
  get drTopAdds() { return (this.DR?.adds || []).slice(0, 15); },
};
