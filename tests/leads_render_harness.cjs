// Exercise the real Leads view without a server or browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../npi_integrity/static/app.js'), 'utf8');
const start = source.indexOf('async function viewLeads(');
const end = source.indexOf('\nasync function viewMethodology()', start);
assert(start !== -1 && end !== -1, 'Leads view must exist');

function element() {
  return {
    value: '', innerHTML: '', textContent: '', disabled: false, listeners: {},
    addEventListener(event, callback) { this.listeners[event] = callback; },
    fire(event) { this.listeners[event](); },
  };
}

const items = Array.from({length: 12}, (_, i) => ({
  alert_id: `A-${i}`, score: 600, tier: 'Elevated',
  display_name: i === 11 ? 'SECOND PROVIDER' : `TEST PROVIDER ${i}`,
  npi: String(1000000000 + i), state: i === 11 ? 'CA' : 'TX',
  status: i === 11 ? 'In review' : 'New', top_reason: 'Synthetic test signal',
}));
const app = {view: null, replaceChildren(view) { this.view = view; }};
const context = {
  routeId: 1, app, OPEN: new Set(['New', 'In review']),
  api: async () => items, coveragePanel: () => '',
  fmtN: (n) => String(n), esc: (s) => String(s ?? ''),
  scoreBadge: (score) => String(score), statusTag: (status) => status,
  nextReviewStep: () => 'Start review',
  document: {createElement() {
    const view = element();
    view.nodes = new Map();
    view.querySelector = (selector) => {
      const id = selector.slice(1);
      if (!view.innerHTML.includes(`id="${id}"`)) return null;
      if (!view.nodes.has(id)) view.nodes.set(id, element());
      return view.nodes.get(id);
    };
    return view;
  }},
  $: (selector, view) => view.querySelector(selector),
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

(async () => {
  await vm.runInContext('viewLeads(undefined, 1)', context);
  const nodes = app.view.nodes;
  assert.match(nodes.get('lead-body').innerHTML, /TEST PROVIDER 0/);
  assert.equal(nodes.get('lead-count').textContent, '1–10 of 12');
  nodes.get('lead-next').fire('click');
  assert.equal(nodes.get('lead-count').textContent, '11–12 of 12');
  nodes.get('lead-query').value = 'SECOND';
  nodes.get('lead-query').fire('input');
  assert.equal(nodes.get('lead-count').textContent, '1–1 of 1');
  assert.match(nodes.get('lead-body').innerHTML, /SECOND PROVIDER/);

  let resolveRequest;
  context.api = () => new Promise((resolve) => { resolveRequest = resolve; });
  const oldView = app.view;
  const pending = vm.runInContext('viewLeads(undefined, 1)', context);
  context.routeId = 2; // Navigation changed while the Leads request was pending.
  resolveRequest(items);
  await pending;
  assert.equal(app.view, oldView, 'stale response must not replace the current page');
  oldView.nodes.get('lead-query').fire('input');
  assert.equal(oldView.nodes.get('lead-count').textContent, '1–1 of 1', 'stale view handlers must not render');
  process.stdout.write('Leads rendering, filters, paging and navigation race passed\n');
})().catch((error) => { console.error(error); process.exitCode = 1; });
