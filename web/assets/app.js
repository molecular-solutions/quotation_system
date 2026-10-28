// Shared helpers: fetch wrapper, toast, formatting, top bar.
window.api = async function (method, url, body, opts = {}) {
  const init = { method, headers: {}, credentials: 'same-origin' };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  if (r.status === 401 && !url.endsWith('/login')) { location.href = '/login'; throw new Error('login required'); }
  if (opts.raw) return r;
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) {
    const d = data && data.detail;
    const e = new Error(typeof d === 'string' ? d : (d && (d.message || (d.errors && 'Please fix the highlighted fields'))) || 'Request failed (' + r.status + ')');
    e.status = r.status; e.errors = d && d.errors; throw e;
  }
  return data;
};

window.toast = function (msg, bad) {
  const el = document.createElement('div');
  el.className = 'toast' + (bad ? ' bad' : ''); el.textContent = msg; document.body.appendChild(el);
  setTimeout(() => el.remove(), bad ? 6000 : 2600);
};

// Indian digit grouping, e.g. 1234567.5 -> 12,34,567.50
window.inr = function (n) {
  n = Number(n || 0); const neg = n < 0; n = Math.abs(n);
  let [w, f] = n.toFixed(2).split('.');
  if (w.length > 3) w = w.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + w.slice(-3);
  return (neg ? '- ' : '') + '₹ ' + w + '.' + f;
};

// keeps `this`, so it works as an Alpine component method
window.debounce = (fn, ms) => { let t; return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); }; };

window.saveBlob = function (blob, filename) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
};
window.filenameFrom = (r, fallback) => ((r.headers.get('content-disposition') || '').match(/filename="([^"]+)"/) || [])[1] || fallback;

window.topbar = async function (active) {
  const el = document.getElementById('top');
  el.innerHTML = '<img src="/assets/logo.png" alt="Molecular Solutions"><nav></nav>';
  let me; try { me = await api('GET', '/api/me'); } catch (e) { return; }
  window.me = me;
  const pages = [['/', 'Quotes']].concat(me.role === 'admin' ? [['/data', 'Data'], ['/prices', 'Prices'], ['/settings', 'Settings'], ['/admin', 'Admin']] : []);
  const links = pages.map(([h, t]) => `<a href="${h}" class="${active === t ? 'on' : ''}">${t}</a>`).join('');
  el.innerHTML = `<img src="/assets/logo.png" alt="Molecular Solutions"><nav>${links}</nav>` +
    `<a class="who" href="/account" title="Account and password"></a>` +
    `<button class="link" onclick="api('POST','/api/logout').then(()=>location.href='/login')">Log out</button>`;
  el.querySelector('.who').textContent = me.name;
};

// Copy text to the clipboard (falls back for plain-http pages, where navigator.clipboard is unavailable).
window.copyText = async function (text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {}
  const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select();
  let ok = false; try { ok = document.execCommand('copy'); } catch (e) {} t.remove(); return ok;
};
