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
  var count = document.getElementById('count');
  var noresults = document.getElementById('noresults');

  var period = document.getElementById('period');
  var refineActive = document.getElementById('refineactive');

  var activeSignal = 'all';
  var activePeriod = 'all';
  var activeSizes = null;              // null means any size
  // Routine notices are hidden by default. On a deadline week they were 120 of
  // 254 events and buried the restatements and auditor changes.
  var showRoutine = false;

  var haystacks = cards.map(function (c) { return c.textContent.toLowerCase(); });
  var periods = cards.map(function (c) { return c.dataset.period || ''; });
  var sizes = cards.map(function (c) { return c.dataset.size || ''; });
  var routine = cards.map(function (c) { return c.dataset.routine === 'yes'; });

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
      var okPeriod = activePeriod === 'all'
        || periods[i].indexOf(activePeriod) === 0;
      var show = okSignal && okSize && okTerm && okRoutine && okPeriod;
      card.hidden = !show;
      if (show) shown++;
    });
    if (count) {
      count.textContent = shown === cards.length
        ? cards.length + ' events'
        : shown + ' of ' + cards.length + ' events';
    }
    if (noresults) noresults.hidden = shown !== 0;
    describeRefinements();

    // Keep the chart showing the same population as the cards. Search text is
    // deliberately excluded: it matches card text, which the chart rows do not
    // carry, so applying it there would silently disagree with the register.
    if (window.redrawActivityChart) {
      window.redrawActivityChart(function (row) {
        var okSignal = activeSignal === 'all' || row.s === activeSignal;
        var okSize = !activeSizes || activeSizes.indexOf(row.z) !== -1;
        var okRoutine = showRoutine || !row.r;
        return okSignal && okSize && okRoutine;
      }, describeScope());
    }
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
    if (showRoutine) parts.push('routine notices shown');
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
    group.addEventListener('click', function (e) {
      var btn = e.target.closest('.chip');
      if (!btn) return;
      group.querySelectorAll('.chip').forEach(function (c) {
        c.classList.toggle('is-on', c === btn);
      });
      onPick(btn);
      apply();
    });
  }

  if (q) q.addEventListener('input', apply);
  if (period) period.addEventListener('change', function () {
    activePeriod = period.value;
    apply();
  });
  wire(chips, function (btn) { activeSignal = btn.dataset.filter; });
  wire(document.getElementById('routinechips'), function (btn) {
    showRoutine = btn.dataset.routine === 'show';
  });
  wire(sizeChips, function (btn) {
    var v = btn.dataset.size;
    activeSizes = v === 'all' ? null : v.split(',');
  });

  apply();
})();
