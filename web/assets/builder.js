let uidn = 0;
const uid = () => 'l' + (++uidn);

function builder() {
  return {
    loading: true, emailedAt: '', mail: { configured: false, recipients: [] }, id: null, number: '', numberSuffix: '', revision: 0, status: 'Draft', busy: '', error: '', showErrors: false,
    customer: { id: null, name: '', department: '', institution: '', address: '', email: '', phone: '' },
    custResults: [], custOpen: false,
    tier: 'Academic', discount: 0, defaultYears: 3, gstMode: 'IGST', gstPct: 18,
    more: { date: '', validUntil: '', paymentTerms: '', preparedBy: '' },
    lines: [], products: [], packages: [], prodQuery: '', prodOpen: false, hi: 0,
    calc: { lines: [], totals: null },
    showPreview: false, previewUrl: '',

    get filtered() {
      const q = this.prodQuery.trim().toLowerCase();
      const list = q ? this.products.filter(p => (p.label + ' ' + p.name + ' ' + p.vendor).toLowerCase().includes(q)) : this.products;
      return list.slice(0, 40);
    },

    async init() {
      try { this.more.preparedBy = localStorage.getItem('preparedBy') || ''; } catch (e) {}
      const [products, packages, settings, mail] = await Promise.all([
        api('GET', '/api/products'), api('GET', '/api/packages'), api('GET', '/api/settings'), api('GET', '/api/mail')]);
      this.mail = mail;
      this.products = products; this.packages = packages.filter(p => p.items.length);
      this.gstPct = Number(settings.values.gst_pct);
      const m = location.pathname.match(/^\/quote\/(\d+)/);
      if (m) await this.load(Number(m[1]));
      this.loading = false;
      let prevYears = this.defaultYears;
      this.$watch('defaultYears', (v) => {            // rows still on the old default follow the new one
        if (!(v >= 1)) return;
        this.lines.forEach(l => { if (l.years === prevYears) l.years = v; }); prevYears = v;
      });
      for (const k of ['tier', 'discount', 'gstMode', 'gstPct']) this.$watch(k, () => this.recalc());
      this.$watch('lines', () => this.recalc(), { deep: true });
      this.recalc();
    },

    async load(id) {
      const q = await api('GET', '/api/quotes/' + id);
      Object.assign(this, { emailedAt: q.emailed_at || '', id: q.id, number: q.number, revision: q.revision, numberSuffix: q.number.replace(/^MS-/, ''), status: q.status, tier: q.tier, discount: q.discount_pct,
        gstMode: q.gst_mode, gstPct: q.gst_pct });
      this.customer = q.customer;
      this.more = { date: q.date, validUntil: q.valid_until, paymentTerms: q.payment_terms, preparedBy: q.prepared_by };
      this.lines = q.lines.map(l => {
        const p = this.products.find(x => x.id === l.product_id);
        const current = p ? this.termPrice(p, q.tier, l.users_label, l.years) : undefined;
        // the line still equals today's catalogue price for its term: leave it blank so it follows the catalogue;
        // otherwise keep the saved price (or typed total) as an override
        const follows = !l.complimentary && current !== undefined && Math.abs(current - l.amount) < 0.005;
        return { key: uid(), product_id: l.product_id, description: l.description || '', label: l.label, deployment: l.deployment,
          users_label: l.users_label, years: l.years, complimentary: l.complimentary,
          amount: !follows && l.amount_pinned ? l.amount : null, unit_price: l.complimentary || follows || l.amount_pinned ? null : l.unit_price };
      });
      if (this.lines.length) this.defaultYears = this.lines[0].years;
    },

    // the catalogue's annual price for this many users (undefined when none is stored)
    priceFor(p, tier, users) {
      const u = p.price_basis === 'per_license_year' ? '1' : String(users).trim();
      return (p.prices[tier] || {})[u];
    },
    // the catalogue's price for the whole term: 1 year = annual price, 3 years = the 3-year price, any other length none
    termPrice(p, tier, users, years) {
      const u = p.price_basis === 'per_license_year' ? '1' : String(users).trim();
      const y = Number(years);
      return y === 1 ? (p.prices[tier] || {})[u] : y === 3 ? ((p.prices3 || {})[tier] || {})[u] : undefined;
    },

    // customer
    findCustomers: debounce(async function () {
      const q = this.customer.name.trim();
      this.custResults = await api('GET', '/api/customers?q=' + encodeURIComponent(q));
      this.custOpen = true;
    }, 200),
    pickCustomer(c) { this.customer = { ...c }; this.tier = c.tier; this.custOpen = false; },

    // lines
    newLine(p, users, years) {
      return { key: uid(), product_id: p.id, description: p.description || '', label: p.label, deployment: p.deployment,
        users_label: users || p.default_users, years: years || this.defaultYears, unit_price: null, amount: null,
        complimentary: p.price_basis === 'complimentary' };
    },
    addProduct(p) { this.lines.push(this.newLine(p)); this.prodQuery = ''; this.prodOpen = false; },
    addPackage(id) {
      const pk = this.packages.find(x => String(x.id) === String(id)); if (!pk) return;
      for (const it of pk.items) { const p = this.products.find(x => x.id === it.product_id); if (p) this.lines.push(this.newLine(p, it.users, it.years)); }
    },
    addCustom() { this.lines.push({ key: uid(), product_id: null, description: '', label: '', deployment: 'Local', users_label: '1',
      years: this.defaultYears, unit_price: 0, amount: null, complimentary: false }); },
    // a typed total wins; the unit price is then worked out from it (and the other way round)
    priceShown(l, i) { return l.unit_price !== null ? l.unit_price : (this.calc.lines[i]?.unit_price ?? ''); },
    amountShown(l, i) { return l.amount !== null ? l.amount : (this.calc.lines[i]?.amount ?? ''); },

    // payload / calc
    linePayload(l) {
      return { product_id: l.product_id, label: l.label, description: l.description || '', deployment: l.deployment, users_label: l.users_label,
        years: Number(l.years) || 1, unit_price: l.complimentary || l.amount !== null ? null : l.unit_price,
        amount: l.complimentary ? null : l.amount, complimentary: l.complimentary };
    },
    payload() {
      return { customer: this.customer, tier: this.tier, date: this.more.date || null, valid_until: this.more.validUntil || null,
        discount_pct: Number(this.discount) || 0, gst_mode: this.gstMode, gst_pct: Number(this.gstPct) || 18,
        payment_terms: this.more.paymentTerms, prepared_by: this.more.preparedBy,
        number: this.tier === 'Commercial' && this.numberSuffix.trim() ? 'MS-' + this.numberSuffix.trim() : null, lines: this.lines.map(l => this.linePayload(l)) };
    },
    recalc: debounce(async function () {
      if (!this.lines.length) { this.calc = { lines: [], totals: null }; return; }
      const b = this.payload();
      try { this.calc = await api('POST', '/api/quotes/calc', { tier: b.tier, discount_pct: b.discount_pct, gst_mode: b.gst_mode, gst_pct: b.gst_pct, lines: b.lines }); }
      catch (e) { this.error = e.message; }
    }, 250),

    valid() {
      this.showErrors = true; this.error = '';
      if (!this.customer.name.trim()) { this.error = 'Enter the customer name.'; return false; }
      if (this.tier === 'Commercial' && !this.numberSuffix.trim()) { this.error = 'Enter the quote number, e.g. MS-CA-26-058.'; return false; }
      if (!this.lines.length) { this.error = 'Add at least one product.'; return false; }
      if (this.lines.some(l => !l.label.trim())) { this.error = 'Every row needs a product name.'; return false; }
      const bad = this.calc.lines.findIndex(l => l && l.error);
      if (bad >= 0) { this.error = `Row ${bad + 1}: ${this.calc.lines[bad].error}`; return false; }
      return true;
    },

    async save() {
      if (this.busy) return; if (!this.valid()) return;
      this.busy = 'save'; const ok = await this.persist(); this.busy = '';
      if (ok) toast('Saved ' + this.number);
    },
    async persist() {
      try {
        try { localStorage.setItem('preparedBy', this.more.preparedBy); } catch (e) {}
        const q = this.id ? await api('PUT', '/api/quotes/' + this.id, this.payload()) : await api('POST', '/api/quotes', this.payload());
        const first = !this.id;
        this.id = q.id; this.number = q.number; this.revision = q.revision; this.numberSuffix = q.number.replace(/^MS-/, ''); this.status = q.status; this.customer = q.customer;
        this.more.date = q.date; this.more.validUntil = q.valid_until;
        if (first) history.replaceState(null, '', '/quote/' + q.id);
        return true;
      } catch (e) { this.error = e.message; toast(e.message, true); return false; }
    },
    async download(fmt) {
      if (this.busy) return; if (!this.valid()) return;
      this.busy = fmt;
      try {
        if (!(await this.persist())) return;
        const r = await api('GET', `/api/quotes/${this.id}/${fmt}`, undefined, { raw: true });
        if (!r.ok) throw new Error((await r.json()).detail.message || 'Download failed');
        saveBlob(await r.blob(), filenameFrom(r, this.number + '.' + fmt));
      } catch (e) { this.error = e.message; toast(e.message, true); }
      finally { this.busy = ''; }
    },
    async emailQuote() {
      if (this.busy) return; if (!this.valid()) return;
      if (!this.mail.configured) { this.error = 'E-mail is not set up on this server yet. Ask the administrator.'; return; }
      if (!this.mail.recipients.length) { this.error = 'No e-mail recipients are saved. An administrator can add them in Settings.'; return; }
      if (!confirm('Save and e-mail ' + (this.number || 'this quotation') + ' (PDF) to:\n\n' + this.mail.recipients.join('\n'))) return;
      this.busy = 'email';
      try {
        if (!(await this.persist())) return;
        const r = await api('POST', `/api/quotes/${this.id}/email`);
        this.emailedAt = r.emailed_at; toast('E-mailed ' + this.number + ' to ' + r.sent_to.length + ' recipient(s)');
      } catch (e) { this.error = e.message; toast(e.message, true); }
      finally { this.busy = ''; }
    },
    async preview() {
      if (this.busy) return; if (!this.valid()) return;
      this.busy = 'preview';
      try {
        const r = await api('POST', '/api/quotes/preview.pdf', this.payload(), { raw: true });
        if (!r.ok) throw new Error((await r.json()).detail.message || 'Preview failed');
        if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
        this.previewUrl = URL.createObjectURL(await r.blob()); this.showPreview = true;
      } catch (e) { this.error = e.message; toast(e.message, true); }
      finally { this.busy = ''; }
    },
    async revise() {
      if (!confirm('Create a new revision of ' + this.number + '? This quote stays as it is.')) return;
      try { const n = await api('POST', `/api/quotes/${this.id}/revise`); location.href = '/quote/' + n.id; }
      catch (e) { toast(e.message, true); }
    },
  };
}
