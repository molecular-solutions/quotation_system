function pricesPage() {
  return {
    products: [], maxUsers: 25, filter: '', cur: null, tier: 'Academic', tiers: ['Academic', 'Commercial'],
    edit: { Academic: {}, Commercial: {} }, orig: { Academic: {}, Commercial: {} }, errs: {}, saving: false,
    prev: null, file: null, uploadError: '', importing: false,

    get groups() {
      const f = this.filter.trim().toLowerCase(), by = {};
      for (const p of this.products) if (!f || (p.name + ' ' + p.vendor).toLowerCase().includes(f)) (by[p.vendor] = by[p.vendor] || []).push(p);
      return Object.keys(by).sort((a, b) => (a || '~').localeCompare(b || '~')).map(v => ({ vendor: v, items: by[v] }));
    },
    get userRange() { return this.cur ? Array.from({ length: this.cur.max_users }, (_, i) => i + 1) : []; },
    get anyDirty() { return this.tiers.some(t => this.dirty(t)); },
    dirty(t) { return JSON.stringify(this.edit[t]) !== JSON.stringify(this.orig[t]); },

    // status chip in the product list: how many of the user counts have a price (worst of the two bundles)
    statusText(p) { const m = Math.min(p.counts.Academic, p.counts.Commercial); return m === p.max_users ? '✓' : m === 0 ? '⚠' : m + '/' + p.max_users; },
    statusClass(p) { const m = Math.min(p.counts.Academic, p.counts.Commercial); return m === p.max_users ? 'done' : m === 0 ? 'none' : 'part'; },
    statusTitle(p) { return `Academic ${p.counts.Academic}/${p.max_users} · Commercial ${p.counts.Commercial}/${p.max_users} prices`; },

    async init() {
      window.addEventListener('beforeunload', e => { if (this.anyDirty) { e.preventDefault(); e.returnValue = ''; } });
      await this.loadList();
      if (this.products.length) await this.open(this.products[0].id, true);
    },
    async loadList() { const r = await api('GET', '/api/prices'); this.products = r.products; this.maxUsers = r.max_users; },
    async open(id, force) {
      if (!force && this.anyDirty && !confirm('Discard the unsaved prices for ' + this.cur.name + '?')) return;
      this.show(await api('GET', '/api/prices/' + id));
    },
    show(d) {
      this.cur = d; this.errs = {};
      for (const t of this.tiers) {
        const e = {}; for (let n = 1; n <= d.max_users; n++) e[n] = d.prices[t][n] === undefined ? '' : String(d.prices[t][n]);
        this.edit[t] = e; this.orig[t] = { ...e };
      }
    },
    reset() { for (const t of this.tiers) this.edit[t] = { ...this.orig[t] }; this.errs = {}; },

    num(n) { const v = parseFloat(String(this.edit[this.tier][n]).replace(/[₹,\s]/g, '')); return isNaN(v) ? null : v; },
    perUser(n) { const v = this.num(n); return v === null ? '' : inr(v / n).replace(/\.00$/, ''); },
    // a hint beside a price that doesn't rise with the number of users, or follows an empty cell
    rowWarn(n) {
      const v = this.num(n); if (v === null || n === 1) return '';
      const p = this.num(n - 1);
      if (p === null) return this.cur.max_users > 1 && this.num(n - 1) === null ? 'no price for ' + (n - 1) + ' user' + (n - 1 === 1 ? '' : 's') : '';
      return v <= p ? 'not more than ' + (n - 1) + ' user' + (n - 1 === 1 ? '' : 's') : '';
    },
    paste(ev, n) {                                  // paste a column (or a row) copied from Excel, filling down from this cell
      const cells = (ev.clipboardData.getData('text') || '').split(/[\r\n\t]+/).map(s => s.trim()).filter((s, i, a) => s !== '' || i < a.length - 1);
      cells.forEach((v, i) => { if (n + i <= this.cur.max_users) { this.edit[this.tier][n + i] = v.replace(/[₹,\s]/g, ''); delete this.errs[this.tier + (n + i)]; } });
    },

    async save() {
      this.saving = true; let failed = false;
      for (const t of this.tiers.filter(x => this.dirty(x))) {
        try {
          const d = await api('PUT', `/api/prices/${this.cur.id}/${t}`, { prices: this.edit[t] });
          this.cur.prices[t] = d.prices[t]; this.orig[t] = { ...this.edit[t] };
          for (let n = 1; n <= d.max_users; n++) this.edit[t][n] = d.prices[t][n] === undefined ? '' : String(d.prices[t][n]);
          this.orig[t] = { ...this.edit[t] };
        } catch (e) {
          failed = true; this.tier = t;
          for (const [k, msg] of Object.entries(e.errors || {})) this.errs[t + k] = msg;
          toast(e.message, true);
        }
      }
      this.saving = false;
      if (!failed) { toast('Prices saved'); await this.loadList(); }
    },

    async pick(file) {
      if (!file) return;
      this.uploadError = ''; this.file = file;
      const fd = new FormData(); fd.append('file', file);
      try { this.prev = await api('POST', '/api/prices/upload', fd); } catch (e) { this.uploadError = e.message; }
    },
    async commit() {
      this.importing = true;
      const fd = new FormData(); fd.append('file', this.file);
      try {
        const r = await api('POST', '/api/prices/upload?commit=true', fd);
        toast(`Imported ${r.imported} prices` + (r.skipped ? `, skipped ${r.skipped}` : ''));
        this.prev = null; await this.loadList();
        if (this.cur) this.show(await api('GET', '/api/prices/' + this.cur.id));
      } catch (e) { toast(e.message, true); }
      this.importing = false;
    },
  };
}
