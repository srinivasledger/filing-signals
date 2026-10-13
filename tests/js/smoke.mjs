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
    removeChild(c) {
      const index = this.children.indexOf(c);
      if (index !== -1) this.children.splice(index, 1);
      return c;
    },
    insertBefore(c, before) {
      const index = this.children.indexOf(before);
      this.children.splice(index === -1 ? this.children.length : index, 0, c);
      return c;
    },
    addEventListener() {}, closest() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return {width: 0, height: 0, top: 0, left: 0}; },
    scrollIntoView() {},
    ...props,
  };
  Object.defineProperty(node, "firstChild", {get: () => node.children[0] || null});
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
  createElementNS: (_, name) => el({tagName: name}),
  createTextNode: (t) => el({textContent: t}),
};
global.window = {redrawActivityChart: undefined, matchMedia: () => ({matches: false})};
global.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};

let failed = 0;
function check(label, cond) {
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${label}`);
  if (!cond) failed = 1;
}

for (const name of ["chart", "filter", "theme", "currency"]) {
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

// --- currency.js: the figure has to be right on the reader's clock ---------
//
// "2 business days behind" over Labor Day, and never once "current", are the
// two answers this has to stop giving. Both come from arithmetic, so both are
// checked here rather than by looking at the page.
{
  const RealDate = Date;
  function withClock(etString, dataset) {
    const node = el({dataset});
    const notice = el();
    const gap = el();
    const lead = el();
    gap.textContent = "current";               // what a build made while current says
    lead.textContent = "This dataset is current.";
    for (const id of Object.keys(byId)) delete byId[id];
    byId.currency = node;
    byId["stale-notice"] = notice;
    byId["stale-gap"] = gap;
    byId["stale-lead"] = lead;
    // Freeze "now". toLocaleString is what currency.js uses to reach New York.
    global.Date = class extends RealDate {
      constructor(...args) {
        if (args.length === 0) super(etString);
        else super(...args);
      }
      toLocaleString() { return etString; }
    };
    try {
      // eslint-disable-next-line no-eval
      (0, eval)(fs.readFileSync("site/static/currency.js", "utf8"));
    } finally {
      global.Date = RealDate;
    }
    return {text: node.textContent, noticeHidden: notice.hidden,
            gap: gap.textContent, lead: lead.textContent};
  }

  // Wed 9 Sep, 10:00 in New York. EDGAR has published through Tue 8 Sep.
  // Data through Fri 4 Sep, and Mon 7 Sep was Labor Day: one day behind.
  let r = withClock("9/9/2026, 10:00:00 AM",
                    {through: "2026-09-04", closed: "2026-09-07"});
  check(`Labor Day is not counted as a day behind (got "${r.text}")`,
        r.text === "1 business day behind");
  check("one day behind is normal, not a stale-data notice", r.noticeHidden);

  // Same clock, but the scan has landed: the site must be able to say so.
  r = withClock("9/9/2026, 10:00:00 AM",
                {through: "2026-09-08", closed: "2026-09-07"});
  check(`the page can say it is current (got "${r.text}")`, r.text === "current");

  // Genuinely behind: Thu 3, Fri 4, Mon 7, Tue 8 - four days, nothing closed.
  r = withClock("9/9/2026, 10:00:00 AM", {through: "2026-09-02", closed: ""});
  check(`a real gap still counts (got "${r.text}")`,
        r.text === "4 business days behind");
  check("a real gap shows the notice", r.noticeHidden === false);
  // The notice was built while the data was current and says so inside its
  // own sentence. Revealed unchanged it would read "not current ... current".
  check(`the revealed notice carries the live gap, not the build's (got "${r.gap}")`,
        r.gap === "4 business days behind");
  check(`and its verdict agrees with its figure (got "${r.lead}")`,
        r.lead === "This dataset is not current.");

  // Past 23:00 ET the day's own index is out, so it counts from then.
  r = withClock("9/9/2026, 11:30:00 PM",
                {through: "2026-09-08", closed: "2026-09-07"});
  check(`after 23:00 ET today counts (got "${r.text}")`,
        r.text === "1 business day behind");

  // --- a pipeline that has stopped has to say so on every page ------------
  //
  // The site keeps serving the last pages it built. Every one of them goes on
  // reporting that its checks passed, because they did, on the day it stopped.
  // Nothing else on the page can notice that the day it holds is now weeks
  // old, so this is the only thing standing between a dead scanner and a site
  // that looks fine indefinitely.
  {
    const RealDate = Date;
    function header(etString, through) {
      const mark = el({dataset: {through, closed: ""}, hidden: true});
      for (const id of Object.keys(byId)) delete byId[id];
      byId.freshness = mark;                    // no #currency: not /status
      global.Date = class extends RealDate {
        constructor(...a) { if (a.length === 0) super(etString); else super(...a); }
        toLocaleString() { return etString; }
      };
      try {
        // eslint-disable-next-line no-eval
        (0, eval)(fs.readFileSync("site/static/currency.js", "utf8"));
      } finally { global.Date = RealDate; }
      return mark;
    }

    // Three weeks of silence, seen from any page.
    let m = header("9/30/2026, 10:00:00 AM", "2026-09-08");
    check(`a stopped pipeline is announced in the masthead (got "${m.textContent}")`,
          m.hidden === false && /Data 1[0-9] business days behind/.test(m.textContent));

    // A normal morning between scans says nothing at all.
    m = header("9/9/2026, 10:00:00 AM", "2026-09-08");
    check("a normal day leaves the masthead quiet", m.hidden === true);

    // Caught up: still quiet.
    m = header("9/9/2026, 10:00:00 AM", "2026-09-09");
    check("a current dataset leaves the masthead quiet", m.hidden === true);
  }

  // No data attribute: leave whatever the build rendered alone.
  {
    const node = el({dataset: {}, textContent: "built-in answer"});
    for (const id of Object.keys(byId)) delete byId[id];
    byId.currency = node;
    // eslint-disable-next-line no-eval
    (0, eval)(fs.readFileSync("site/static/currency.js", "utf8"));
    check("without a date it does not overwrite the page",
          node.textContent === "built-in answer");
  }
}

// --- the homepage filter must explain hidden entries and recover matches ---
// A company card that is on the page but hidden by a signal filter used to
// appear under "Not on this page". Exercise the click/input path, not just the
// top-level load, so the label and reset action cannot drift apart.
{
  function chip(data, selected = false) {
    const classes = new Set(["chip", ...(selected ? ["is-on"] : [])]);
    return el({
      dataset: data,
      classList: {
        contains: (name) => classes.has(name),
        toggle(name, state) { if (state) classes.add(name); else classes.delete(name); },
      },
    });
  }
  function group(buttons) {
    const listeners = {};
    return el({
      addEventListener(type, cb) { listeners[type] = cb; },
      querySelectorAll: () => buttons,
      querySelector(sel) {
        if (sel === ".chip.is-on") return buttons.find(b => b.classList.contains("is-on"));
        const wanted = /\[data-filter="([^"]+)"\]/.exec(sel);
        return wanted ? buttons.find(b => b.dataset.filter === wanted[1]) : null;
      },
      click(button) { listeners.click({target: {closest: () => button}}); },
    });
  }
  const underArmour = el({textContent: "Under Armour, Inc. SEC comment letter",
    dataset: {cik: "1336917", signal: "comment_letter", size: "mega",
              period: "2026-09", form: "UPLOAD", routine: "no"}});
  const restatement = el({textContent: "Other Co restatement",
    dataset: {cik: "222", signal: "restatement", size: "mega",
              period: "2026-09", form: "8-K", routine: "no"}});
  const routineNotice = el({textContent: "Routine late notice",
    dataset: {cik: "333", signal: "late_filing", size: "small",
              period: "2026-09", form: "NT", routine: "yes"}});
  let suggestion;
  const feed4 = el({querySelectorAll: () => [underArmour, restatement, routineNotice],
    parentNode: {insertBefore(node) { suggestion = node; return node; }}});
  const q = el({value: "", listeners: {},
    addEventListener(type, cb) { this.listeners[type] = cb; },
    input(value) { this.value = value; this.listeners.input(); }});
  const all = chip({filter: "all"}, true);
  const restatementChip = chip({filter: "restatement"});
  const signals = group([all, restatementChip]);
  const routineHide = chip({routine: "hide"}, true);
  const routineShow = chip({routine: "show"});
  const routines = group([routineHide, routineShow]);
  const count = el();
  const refineActive = el();
  for (const id of Object.keys(byId)) delete byId[id];
  Object.assign(byId, {feed: feed4, q, chips: signals, routinechips: routines,
                       count, refineactive: refineActive, noresults: el()});
  global.document.createElement = () => el({listeners: {},
    addEventListener(type, cb) { this.listeners[type] = cb; },
    clickReset() { this.listeners.click({target: {closest: () => ({})}}); }});
  global.location = {pathname: "/filing-signals/index.html"};
  global.fetch = async () => ({ok: true, json: async () => [
    {c: "Under Armour, Inc.", t: "UAA", k: 1336917, n: 1},
  ]});
  (0, eval)(fs.readFileSync("site/static/filter.js", "utf8"));

  check("default routine suppression is named beside Refine",
        refineActive.textContent === "1 routine notice hidden");
  check("selected signal is exposed to assistive technology",
        all.getAttribute("aria-pressed") === "true"
        && restatementChip.getAttribute("aria-pressed") === "false"
        && signals.getAttribute("aria-label") === "Signal type");
  signals.click(restatementChip);
  q.input("Under Armour");
  await new Promise(resolve => setImmediate(resolve));
  check("a filtered on-page match is labelled as hidden, not absent",
        suggestion.innerHTML.includes("On this page, hidden by current filters")
        && !suggestion.innerHTML.includes("Not among the entries on this page"));
  suggestion.clickReset();
  await new Promise(resolve => setImmediate(resolve));
  check("Clear filters reveals the matching card and updates pressed states",
        !underArmour.hidden && count.textContent === "1 of 3 events"
        && all.getAttribute("aria-pressed") === "true"
        && routineShow.getAttribute("aria-pressed") === "true");
}

// --- the activity chart must follow period and form controls ---------------
// Run the controls against chart.js, not a mocked redraw callback. The graph
// once kept September bars under an October selection and ignored Form even
// though the register responded to both.
{
  function button(data, label, selected = false) {
    const classes = new Set(["chip", ...(selected ? ["is-on"] : [])]);
    return el({dataset: data, textContent: label, classList: {
      contains: (name) => classes.has(name),
      toggle(name, state) { if (state) classes.add(name); else classes.delete(name); },
    }});
  }
  function group(buttons) {
    let onClick;
    return el({
      addEventListener(type, cb) { if (type === "click") onClick = cb; },
      querySelectorAll: () => buttons,
      querySelector(sel) {
        if (sel === ".chip.is-on") return buttons.find(b => b.classList.contains("is-on"));
        const wanted = /\[data-filter="([^"]+)"\]/.exec(sel);
        return wanted ? buttons.find(b => b.dataset.filter === wanted[1]) : null;
      },
      click(btn) { onClick({target: {closest: () => btn}}); },
    });
  }
  const sepCard = el({dataset: {signal: "late_filing", size: "small",
    routine: "yes", period: "2026-09", form: "NT"}});
  const oct8K = el({dataset: {signal: "restatement", size: "mega",
    routine: "no", period: "2026-10", form: "8-K"}});
  const oct10K = el({dataset: {signal: "restatement", size: "mega",
    routine: "no", period: "2026-10", form: "10-K"}});
  const rows = [sepCard, oct8K, oct10K];
  const options = [
    {value: "all", textContent: "All time"},
    {value: "2026-10", textContent: "October 2026"},
    {value: "2026-08", textContent: "August 2026"},
  ];
  const period = el({value: "all", options, selectedIndex: 0,
    addEventListener(type, cb) { if (type === "change") this.onChange = cb; },
    change(value) {
      this.value = value;
      this.selectedIndex = options.findIndex(o => o.value === value);
      this.onChange();
    },
  });
  const anyForm = button({form: "all"}, "Any", true);
  const eightK = button({form: "8-K"}, "8-K");
  const tenK = button({form: "10-K"}, "10-K");
  const forms = group([anyForm, eightK, tenK]);
  const allSignal = button({filter: "all"}, "All", true);
  const restatementSignal = button({filter: "restatement"}, "Restatements");
  const signals = group([allSignal, restatementSignal]);
  const anySize = button({size: "all"}, "Any", true);
  const megaSize = button({size: "mega"}, "$10B+");
  const sizes = group([anySize, megaSize]);
  const routines = group([
    button({routine: "hide"}, "Hidden", true),
    button({routine: "show"}, "Shown"),
  ]);
  const scope = el();
  for (const id of Object.keys(byId)) delete byId[id];
  Object.assign(byId, {
    feed: el({querySelectorAll: () => rows}), period, chips: signals,
    sizechips: sizes, formchips: forms,
    routinechips: routines, "chart-data": chartData, "chart-scope": scope,
  });
  chartSvg.children.length = 0;
  chartData.textContent = JSON.stringify({
    days: ["2026-09-30", "2026-10-01", "2026-10-02"],
    order: ["restatement", "late_filing"],
    labels: {restatement: "Restatement", late_filing: "Late filing"},
    vars: {restatement: "--series-1", late_filing: "--series-muted"},
    rows: [
      {d: "2026-09-30", s: "late_filing", z: "small", r: true, f: "NT"},
      {d: "2026-10-01", s: "restatement", z: "mega", r: false, f: "8-K"},
      {d: "2026-10-02", s: "restatement", z: "mega", r: false, f: "10-K"},
    ],
  });
  (0, eval)(fs.readFileSync("site/static/chart.js", "utf8"));
  (0, eval)(fs.readFileSync("site/static/filter.js", "utf8"));

  const barDates = () => chartSvg.children.filter(n => n.tagName === "path")
    .map(n => n.children[0].textContent.slice(0, 10));
  const axisDates = () => chartSvg.children.filter(n => n.tagName === "text"
    && /^\d\d-\d\d$/.test(n.textContent)).map(n => n.textContent);
  check("default chart scope names its days and hidden routine notices",
        scope.textContent.includes("3 filing days")
        && scope.textContent.includes("routine notices hidden"));

  period.change("2026-10");
  check("October chart excludes September bars and axis dates",
        barDates().join(",") === "2026-10-01,2026-10-02"
        && axisDates().join(",") === "10-01,10-02");
  check("October scope and SVG title name its two filing days",
        scope.textContent.includes("2 filing days · October 2026")
        && chartSvg.getAttribute("aria-label").includes(scope.textContent)
        && chartSvg.firstChild.tagName === "title"
        && chartSvg.firstChild.textContent.includes(scope.textContent));

  forms.click(eightK);
  check("Form filter removes the nonmatching chart bar and names the form",
        barDates().join(",") === "2026-10-01"
        && axisDates().join(",") === "10-01,10-02"
        && scope.textContent.includes("8-K"));
  signals.click(restatementSignal);
  sizes.click(megaSize);
  check("chart scope names active signal, size, form and period together",
        scope.textContent.includes("October 2026")
        && scope.textContent.includes("Restatements")
        && scope.textContent.includes("$10B+")
        && scope.textContent.includes("8-K")
        && scope.textContent.includes("routine notices hidden"));

  period.change("2026-08");
  check("an older period has a clear empty chart and zero-day scope",
        barDates().length === 0 && scope.textContent.includes("0 filing days")
        && chartSvg.children.some(n => n.textContent.includes("No filing days for this period")));
  check("an empty period produces no invalid SVG coordinates",
        chartSvg.children.every(n => Object.values(n.attributes)
          .every(v => !/NaN|Infinity/.test(String(v)))));
}

process.exit(failed);
