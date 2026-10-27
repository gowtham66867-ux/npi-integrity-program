// Reproduce navigation while the saved-directory request is still pending.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../npi_integrity/static/app.js'), 'utf8');
const start = source.indexOf('async function viewRoster(');
const end = source.indexOf('\nasync function viewLeads(', start);
assert(start !== -1 && end !== -1, 'directory view must exist');

function node() {
  return {
    innerHTML: '', listeners: {},
    addEventListener(event, callback) { this.listeners[event] = callback; },
    querySelector(selector) {
      if (!this.innerHTML.includes(`id="${selector.slice(1)}"`)) return null;
      return this.children[selector] ||= node();
    },
    children: {},
  };
}
const app = node();
let resolveRequest;
const context = {
  app, routeId: 1,
  api: () => new Promise((resolve) => { resolveRequest = resolve; }),
  $: (selector, root = app) => root.querySelector(selector),
  esc: (value) => String(value ?? ''), mdy: (value) => value,
  REVIEW_DISCLAIMER: 'This is a prompt to check, not a finding of wrongdoing. You decide.',
  toast: () => {}, confirm: () => false,
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

(async () => {
  const pending = vm.runInContext('viewRoster(undefined, 1)', context);
  app.innerHTML = '<h1>Another page</h1>';
  context.routeId = 2;
  resolveRequest({saved: false});
  await pending;
  assert.equal(app.innerHTML, '<h1>Another page</h1>', 'stale directory result must not replace current page');

  context.api = async () => ({saved: true, as_of: '2026-10-06', last_checked_at: '2026-10-21', note: 'Synthetic only',
    results: [{npi: '1136934696', name: 'Test Provider', label: 'Needs attention', reason: 'Synthetic signal'}]});
  await vm.runInContext('viewRoster(undefined, 2)', context);
  assert.match(app.querySelector('#roster-results').innerHTML, /Saved directory results/);
  assert.match(app.querySelector('#roster-results').innerHTML, /1136934696/);
  process.stdout.write('Directory persistence render and navigation race passed\n');
})().catch((error) => { console.error(error); process.exitCode = 1; });
