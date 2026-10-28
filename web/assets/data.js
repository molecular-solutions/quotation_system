function dataPage() {
  let k = 0;
  return {
    tables: [], tab: 'products', rows: [], filter: '', blocks: [], history: [], over: false,
    prev: null, file: null, uploadError: '', importing: false, saving: false,
    colw: { name: 260, description: 240, line_label: 260, vendor: 140, segment: 180, category: 100, deployment: 110, default_users: 120,
            catalog_blocks: 160, department: 200, institution: 200, address: 260, email: 200, package: 180, product: 260 },
    choiceLabel(o) { return ({ per_user_year: 'per user / year', per_license_year: 'per licence / year', complimentary: 'complimentary' })[o] || o; },
    colLabel(k) { return (this.cur?.columns.find(c => c.key === k) || {}).label || k; },
    fmt(v) { return v === null || v === undefined ? '' : v === true ? 'Y' : v === false ? 'N' : v; },

    get dirtyCount() { return this.rows.filter(r => r._dirty).length; },
    get cur() { return this.tables.find(t => t.name === this.tab); },
    get visibleRows() {
      const f = this.filter.trim().toLowerCase();
      return f ? this.rows.filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(f))) : this.rows;
    },

    async init() {
      this.tables = await api('GET', '/api/data/tables');
      this.blocks = await api('GET', '/api/blocks');
      window.addEventListener('beforeunload', e => { if (this.dirtyCount) { e.preventDefault(); e.returnValue = ''; } });
      const h = location.hash.slice(1); if (this.tables.some(t => t.name === h) || h === 'blocks') this.tab = h;
      await this.refresh();
    },
    async switchTab(t) {
      if (this.dirtyCount && !confirm('Discard the unsaved changes in this table?')) return;
      this.tab = t; location.hash = t; this.filter = ''; this.uploadError = ''; if (t !== 'blocks') await this.refresh(); },
    wrap(r) { return { ...r, _k: ++k, _dirty: false, _busy: false, _err: null }; },
    async refresh() {
      if (this.tab === 'blocks') return;
      this.rows = (await api('GET', '/api/data/' + this.tab)).map(r => this.wrap(r));
      this.history = await api('GET', '/api/data/history');
    },
    addRow() {
      const r = this.wrap({}); r._dirty = true;
      for (const c of this.cur.columns) r[c.key] = c.kind === 'bool' ? true : '';
      if (this.tab === 'customers') r.tier = 'Academic';
      this.rows.unshift(r);
    },
    body(r) { const b = {}; for (const c of this.cur.columns) b[c.key] = r[c.key] === null ? '' : r[c.key]; return b; },
    async save(r) {
      if (!r._dirty || r._busy) return;
      r._busy = true; r._err = null;
      try {
        const saved = r.id ? await api('PUT', `/api/data/${this.tab}/${r.id}`, this.body(r)) : await api('POST', '/api/data/' + this.tab, this.body(r));
        Object.assign(r, saved, { _dirty: false });
      } catch (e) { r._err = e.errors || { [this.cur.columns[0].key]: e.message }; if (!this.saving) toast(e.message, true); }
      r._busy = false;
    },
    async saveAll() {
      this.saving = true;
      const todo = this.rows.filter(r => r._dirty);
      for (const r of todo) await this.save(r);
      const failed = todo.filter(r => r._dirty).length;
      this.saving = false;
      if (!failed) toast(todo.length === 1 ? 'Saved' : `Saved ${todo.length} rows`);
      else toast(`${failed} row${failed === 1 ? '' : 's'} could not be saved – see the highlighted cells`, true);
    },
    async remove(r) {
      if (!r.id) { this.rows = this.rows.filter(x => x !== r); return; }
      const what = this.tab === 'products' ? 'Hide ' + r.name + ' from new quotes?' : 'Delete this row?';
      if (!confirm(what)) return;
      try { await api('DELETE', `/api/data/${this.tab}/${r.id}`); await this.refresh(); } catch (e) { toast(e.message, true); }
    },

    async pick(file) {
      if (!file) return;
      this.uploadError = ''; this.file = file;
      const fd = new FormData(); fd.append('file', file);
      try { this.prev = await api('POST', `/api/data/${this.tab}/upload`, fd); }
      catch (e) { this.uploadError = e.message; }
    },
    async commit() {
      this.importing = true;
      const fd = new FormData(); fd.append('file', this.file);
      try {
        const r = await api('POST', `/api/data/${this.tab}/upload?commit=true`, fd);
        toast(`Imported ${r.imported} row${r.imported === 1 ? '' : 's'}` + (r.skipped ? `, skipped ${r.skipped}` : ''));
        this.prev = null; await this.refresh();
      } catch (e) { toast(e.message, true); }
      this.importing = false;
    },
  };
}
