// Client-side filtering over the already-rendered feed. Two independent
// dimensions - signal type and company size - combined with AND. No build
// step and no framework: the page is complete and readable without this file.
(function () {
  var feed = document.getElementById('feed');
  if (!feed) return;

  // Cards on the home, letters and sequences pages; list rows on a signal
  // page. Anything carrying a size tier is filterable.
  var cards = Array.prototype.slice.call(
    feed.querySelectorAll('.event, li[data-size]'));
  var q = document.getElementById('q');
  var chips = document.getElementById('chips');
  var sizeChips = document.getElementById('sizechips');
  var formChips = document.getElementById('formchips');
  var count = document.getElementById('count');
  var noresults = document.getElementById('noresults');

  var period = document.getElementById('period');
  var refineActive = document.getElementById('refineactive');

  var activeSignal = 'all';
  var activePeriod = 'all';
  var activeForm = 'all';
  var activeSizes = null;              // null means any size
  // Routine notices are hidden by default. On a deadline week they were 120 of
  // 254 events and buried the restatements and auditor changes.
  //
  // Only where the reader can turn them back on. A company page carries the
  // period control alone, and defaulting to hidden there meant one of that
  // company's three entries simply was not shown, with nothing on the page to
  // say so or to reveal it. No control, nothing hidden.
  var showRoutine = !document.getElementById('routinechips');

  var haystacks = cards.map(function (c) { return c.textContent.toLowerCase(); });
  // A row can span months: a company with entries in May and August, a
  // comment-letter thread open across three. Carrying only the newest month
  // hid the row from every earlier month it genuinely belongs to, while the
  // row itself displayed that earlier date. Space-separated, matched by token.
  var periods = cards.map(function (c) {
    return (c.dataset.period || '').split(' ');
  });
  var sizes = cards.map(function (c) { return c.dataset.size || ''; });
  // A company or a sequence spans several forms, so this is a list too.
  var forms = cards.map(function (c) {
    return (c.dataset.form || '').split(' ');
  });
  var routine = cards.map(function (c) { return c.dataset.routine === 'yes'; });
  var routineCount = routine.filter(Boolean).length;

  function apply() {
    var term = ((q && q.value) || '').trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card, i) {
      var okSignal = activeSignal === 'all' || card.dataset.signal === activeSignal;
      // An untiered company (no float reported) is only hidden when a specific
      // size is being asked for; it is never silently counted as small.
      var okSize = !activeSizes || activeSizes.indexOf(sizes[i]) !== -1;
      var okTerm = !term || haystacks[i].indexOf(term) !== -1;
      var okRoutine = showRoutine || !routine[i];
      // "2026" must match "2026-08"; "2026-08" must match only itself.
      var okPeriod = activePeriod === 'all' || periods[i].some(function (p) {
        return p.indexOf(activePeriod) === 0;
      });
      var okForm = activeForm === 'all' || forms[i].indexOf(activeForm) !== -1;
      var show = okSignal && okSize && okTerm && okRoutine && okPeriod && okForm;
      card.hidden = !show;
      if (show) shown++;
    });
    if (count) {
      // The companies page counts companies, not events. The noun comes from
      // the feed so one filter can serve both without knowing the page.
      var unit = feed.dataset.unit || 'events';
      count.textContent = shown === cards.length
        ? cards.length + ' ' + unit
        : shown + ' of ' + cards.length + ' ' + unit;
    }
    if (noresults) noresults.hidden = shown !== 0;
    syncGroups();
    describeRefinements();
    if (q) showElsewhere(term);

    // Keep the chart showing the same population as the cards. Search text is
    // deliberately excluded: it matches card text, which the chart rows do not
    // carry, so applying it there would silently disagree with the register.
    if (window.redrawActivityChart) {
      window.redrawActivityChart(function (row) {
        var okSignal = activeSignal === 'all' || row.s === activeSignal;
        var okSize = !activeSizes || activeSizes.indexOf(row.z) !== -1;
        var okRoutine = showRoutine || !row.r;
        // Chart rows carry no form, so a form filter is not applied to the
        // chart - the same reasoning as the search box above.
        return okSignal && okSize && okRoutine;
      }, describeScope());
    }
  }

  // Year groups on a signal page. A filter that emptied one left its heading
  // and an empty list on the page - "2025 - 1 entry" with nothing beneath it.
  // The heading also has to stop reporting the unfiltered total.
  // Only a group that actually holds rows is one. Anything else has nothing
  // to sync and must never be hidden on their behalf.
  var groups = Array.prototype.slice.call(
    feed.querySelectorAll('.year-group')
  ).filter(function (g) { return g.querySelectorAll('li').length > 0; });

  function syncGroups() {
    groups.forEach(function (g) {
      var live = g.querySelectorAll('li:not([hidden])').length;
      g.hidden = live === 0;
      var label = g.querySelector('.group-count');
      if (!label) return;
      var total = Number(label.dataset.total);
      label.textContent = live === total
        ? total + (total === 1 ? ' entry' : ' entries')
        : live + ' of ' + total + ' entries';
    });
  }

  // A filter tucked inside a closed panel must announce itself, or the page
  // just looks short for no reason.
  function describeRefinements() {
    if (!refineActive) return;
    var parts = [];
    if (activePeriod !== 'all' && period) {
      parts.push(period.options[period.selectedIndex].textContent.trim());
    }
    if (activeSizes && sizeChips) {
      var sb = sizeChips.querySelector('.chip.is-on');
      if (sb && sb.dataset.size !== 'all') parts.push(sb.textContent.trim());
    }
    if (activeForm !== 'all' && formChips) {
      var fb = formChips.querySelector('.chip.is-on');
      if (fb) parts.push(fb.textContent.trim());
    }
    // The default suppression needs to be visible even while Refine is
    // closed; otherwise "141 of 150" has no explanation beside the feed.
    if (document.getElementById('routinechips')) {
      parts.push(showRoutine ? 'routine notices shown'
        : routineCount + ' routine notice' + (routineCount === 1 ? '' : 's') + ' hidden');
    }
    refineActive.textContent = parts.length ? parts.join(' \u00b7 ') : '';
    refineActive.classList.toggle('is-set', parts.length > 0);
  }

  function describeScope() {
    var parts = [];
    if (activeSignal !== 'all' && chips) {
      var btn = chips.querySelector('[data-filter="' + activeSignal + '"]');
      if (btn) parts.push(btn.textContent.trim());
    }
    if (activeSizes && sizeChips) {
      var sbtn = sizeChips.querySelector('.chip.is-on');
      if (sbtn) parts.push(sbtn.textContent.trim());
    }
    return parts.length ? parts.join(' \u00b7 ') : (data_days() + ' days');
  }

  function data_days() {
    try {
      return JSON.parse(document.getElementById('chart-data').textContent).days.length;
    } catch (e) { return ''; }
  }

  function wire(group, onPick) {
    if (!group) return;
    group.querySelectorAll('.chip').forEach(function (c) {
      c.setAttribute('aria-pressed', String(c === group.querySelector('.chip.is-on')));
    });
    group.addEventListener('click', function (e) {
      var btn = e.target.closest('.chip');
      if (!btn) return;
      group.querySelectorAll('.chip').forEach(function (c) {
        c.classList.toggle('is-on', c === btn);
        c.setAttribute('aria-pressed', String(c === btn));
      });
      onPick(btn);
      apply();
    });
  }

  // --- reaching beyond the page ------------------------------------------
  // The feed is a window, so filtering it can only ever find what is already
  // rendered. A company outside that window returned nothing at all, which
  // reads as "not tracked" rather than "not on this page". Search suggestions
  // distinguish genuinely absent companies from cards suppressed by filters.
  var elsewhere = null;
  var indexPromise = null;
  var searchGeneration = 0;

  function ensureIndex() {
    if (!indexPromise) {
      indexPromise = fetch(rootPath() + 'search-index.json')
        .then(function (r) { return r.ok ? r.json() : []; })
        .catch(function () { return []; });
    }
    return indexPromise;
  }

  function rootPath() {
    // Company and signal pages sit one directory down; everything else is at
    // the root. Derived from the path rather than templated in, because this
    // script is shared by every page.
    return /\/(company|signals)\//.test(location.pathname) ? '../' : '';
  }

  function showElsewhere(term) {
    var generation = ++searchGeneration;
    if (term.length < 2) {
      if (elsewhere) elsewhere.hidden = true;
      return;
    }
    if (!elsewhere) {
      elsewhere = document.createElement('div');
      elsewhere.className = 'elsewhere';
      elsewhere.setAttribute('aria-live', 'polite');
      elsewhere.hidden = true;
      feed.parentNode.insertBefore(elsewhere, feed);
      elsewhere.addEventListener('click', function (e) {
        if (e.target.closest('.reset-filters')) resetFilters();
      });
    }
    ensureIndex().then(function (index) {
      if (generation !== searchGeneration) return;  // newer input or filter
      var onPage = {}, visible = {};
      cards.forEach(function (c) {
        var cik = c.dataset.cik || '';
        if (!cik) return;
        onPage[cik] = true;
        if (!c.hidden) visible[cik] = true;
      });
      var matches = index.filter(function (r) {
        return (r.c || '').toLowerCase().indexOf(term) !== -1
          || (r.t || '').toLowerCase().indexOf(term) === 0;
      });
      var hiddenHere = matches.filter(function (r) {
        return onPage[String(r.k)] && !visible[String(r.k)];
      }).slice(0, 8);
      var outside = matches.filter(function (r) {
        return !onPage[String(r.k)];
      }).slice(0, 8);
      if (!hiddenHere.length && !outside.length) { elsewhere.hidden = true; return; }

      function resultList(rows) {
        return '<ul class="mini">' + rows.map(function (r) {
          // CIKs are SEC numeric identifiers; keep the URL numeric even if a
          // malformed search-index row is ever published.
          var cik = String(r.k).replace(/\D/g, '');
          return '<li><a href="' + rootPath() + 'company/' + cik + '.html">'
            + r.c.replace(/[<>&]/g, '') + '</a>'
            + (r.t ? ' <span class="ticker">' + r.t.replace(/[<>&]/g, '') + '</span>' : '')
            + ' <span class="muted">' + r.n + (r.n === 1 ? ' entry' : ' entries')
            + '</span></li>';
        }).join('') + '</ul>';
      }
      elsewhere.innerHTML = (hiddenHere.length
        ? '<p class="elsewhere-head">On this page, hidden by current filters'
          + ' <button type="button" class="reset-filters">Clear filters</button></p>'
          + resultList(hiddenHere) : '')
        + (outside.length
          ? '<p class="elsewhere-head">Not among the entries on this page &mdash; found in the full record</p>'
          + resultList(outside) : '');
      elsewhere.hidden = false;
    });
  }

  function resetFilters() {
    activeSignal = 'all';
    activePeriod = 'all';
    activeForm = 'all';
    activeSizes = null;
    showRoutine = true;
    if (period) period.value = 'all';
    [[chips, 'filter', 'all'], [sizeChips, 'size', 'all'],
      [formChips, 'form', 'all'], [document.getElementById('routinechips'), 'routine', 'show']]
      .forEach(function (item) {
        var group = item[0], key = item[1], value = item[2];
        if (!group) return;
        group.querySelectorAll('.chip').forEach(function (btn) {
          var selected = btn.dataset[key] === value;
          btn.classList.toggle('is-on', selected);
          btn.setAttribute('aria-pressed', String(selected));
        });
      });
    apply();
  }

  if (q) q.addEventListener('input', function () {
    apply();
  });
  if (period) period.addEventListener('change', function () {
    activePeriod = period.value;
    apply();
  });
  if (chips) {
    chips.setAttribute('role', 'group');
    chips.setAttribute('aria-label', 'Signal type');
  }
  wire(chips, function (btn) { activeSignal = btn.dataset.filter; });
  wire(document.getElementById('routinechips'), function (btn) {
    showRoutine = btn.dataset.routine === 'show';
  });
  wire(sizeChips, function (btn) {
    var v = btn.dataset.size;
    activeSizes = v === 'all' ? null : v.split(',');
  });
  wire(formChips, function (btn) { activeForm = btn.dataset.form; });

  apply();
})();
