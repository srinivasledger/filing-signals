// Executes the site's scripts against a minimal DOM stub.
//
// `node --check` only parses; it cannot see a ReferenceError, which is what
// shipped: filter.js read an undeclared `showRoutine` and died on load, so
// every filter on the live site was dead while the page looked fine. This runs
// the top-level path far enough to catch that.
import fs from "node:fs";

function el(props = {}) {
  const node = {
    dataset: {}, classList: {add(){}, remove(){}, toggle(){}},
    style: {}, hidden: false, textContent: "", innerHTML: "",
    children: [], attributes: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k]; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { return c; },
    addEventListener() {}, closest() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return {width: 0, height: 0, top: 0, left: 0}; },
    scrollIntoView() {},
    ...props,
  };
  Object.defineProperty(node, "firstChild", {get: () => null});
  return node;
}

const cards = [
  el({dataset: {signal: "restatement", size: "mega", routine: "no"}}),
  el({dataset: {signal: "late_filing", size: "small", routine: "yes"}}),
];
const feed = el({querySelectorAll: () => cards});
const chartSvg = el();
const chartData = el({
  textContent: JSON.stringify({
    days: ["2026-08-20", "2026-08-21"],
    order: ["restatement", "late_filing"],
    labels: {restatement: "Restatement", late_filing: "Late filing"},
    vars: {restatement: "--series-1", late_filing: "--series-3"},
    rows: [{d: "2026-08-21", s: "restatement", z: "mega", r: false},
           {d: "2026-08-21", s: "late_filing", z: "small", r: true}],
  }),
});

const byId = {
  feed, q: el({value: ""}), chips: el(), sizechips: el(), routinechips: el(),
  count: el(), noresults: el(), "chart-data": chartData, "chart-scope": el(),
};

global.document = {
  documentElement: el(),
  getElementById: (id) => byId[id] || null,
  querySelector: (sel) => sel === ".site-head nav" ? el({
      scrollLeft: 0, clientWidth: 300,
      getBoundingClientRect: () => ({left: 0, right: 300, width: 300, height: 44, top: 0}),
      querySelector: () => el({offsetLeft: 500, offsetWidth: 80,
        getBoundingClientRect: () => ({left: 500, right: 580, width: 80, height: 44, top: 0})}),
    })
    : sel === ".chart svg" ? chartSvg
    : sel === ".chart-legend" ? el()
    : sel.includes("theme") ? el({dataset: {}}) : null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  createElement: () => el(),
  createElementNS: () => el(),
  createTextNode: (t) => el({textContent: t}),
};
global.window = {redrawActivityChart: undefined, matchMedia: () => ({matches: false})};
global.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};

let failed = 0;
function check(label, cond) {
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${label}`);
  if (!cond) failed = 1;
}

for (const name of ["chart", "filter", "theme"]) {
  try {
    // eslint-disable-next-line no-eval
    (0, eval)(fs.readFileSync(`site/static/${name}.js`, "utf8"));
    console.log(`  ok    ${name}.js executed`);
  } catch (err) {
    console.log(`  FAIL  ${name}.js -> ${err.constructor.name}: ${err.message}`);
    failed = 1;
  }
}
// --- a signal page has a feed and a size filter, and nothing else ---
// The home page supplies every control, so running only that shape would not
// notice filter.js reaching for a search box or chip group that is not there.
{
  const rows = [
    el({dataset: {size: "mega"}}),
    el({dataset: {size: "small"}}),
    el({dataset: {size: "mega"}}),
  ];
  const feed2 = el({querySelectorAll: () => rows});
  const sizechips = el();
  for (const id of Object.keys(byId)) delete byId[id];
  byId.feed = feed2;
  byId.sizechips = sizechips;          // no q, chips, routinechips, count, noresults
  global.window.matchMedia = () => ({matches: false});

  let err = null;
  try {
    (0, eval)(fs.readFileSync("site/static/filter.js", "utf8"));
  } catch (e) { err = e; }
  check(`filter.js runs with only a feed and size chips${err ? ` -> ${err.message}` : ""}`,
        err === null);
  check("every row starts visible", rows.every((r) => !r.hidden));
}

// --- a page with no routine toggle must not hide routine entries ---
{
  const rows = [
    el({dataset: {signal: "late_filing", size: "mega", routine: "yes"}}),
    el({dataset: {signal: "auditor_change", size: "mega", routine: "no"}}),
  ];
  const feed3 = el({querySelectorAll: () => rows});
  feed3.parentNode = el({insertBefore(n) { this.children.push(n); return n; }});
  for (const id of Object.keys(byId)) delete byId[id];
  byId.feed = feed3;                       // no routinechips, no q, no chips
  global.window.matchMedia = () => ({matches: false});
  (0, eval)(fs.readFileSync("site/static/filter.js", "utf8"));
  check("a routine entry is shown when there is no toggle to reveal it",
        rows.every((r) => !r.hidden));
}

process.exit(failed);
