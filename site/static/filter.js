// Client-side filtering over the already-rendered feed. Two independent
// dimensions - signal type and company size - combined with AND. No build
// step and no framework: the page is complete and readable without this file.
(function () {
  var feed = document.getElementById('feed');
  if (!feed) return;

  var cards = Array.prototype.slice.call(feed.querySelectorAll('.event'));
  var q = document.getElementById('q');
  var chips = document.getElementById('chips');
  var sizeChips = document.getElementById('sizechips');
  var count = document.getElementById('count');
  var noresults = document.getElementById('noresults');

  var activeSignal = 'all';
  var activeSizes = null;              // null means any size

  var haystacks = cards.map(function (c) { return c.textContent.toLowerCase(); });
  var sizes = cards.map(function (c) { return c.dataset.size || ''; });

  function apply() {
    var term = (q.value || '').trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card, i) {
      var okSignal = activeSignal === 'all' || card.dataset.signal === activeSignal;
      // An untiered company (no float reported) is only hidden when a specific
      // size is being asked for; it is never silently counted as small.
      var okSize = !activeSizes || activeSizes.indexOf(sizes[i]) !== -1;
      var okTerm = !term || haystacks[i].indexOf(term) !== -1;
      var show = okSignal && okSize && okTerm;
      card.hidden = !show;
      if (show) shown++;
    });
    count.textContent = shown === cards.length
      ? cards.length + ' events'
      : shown + ' of ' + cards.length + ' events';
    noresults.hidden = shown !== 0;

    // Keep the chart showing the same population as the cards. Search text is
    // deliberately excluded: it matches card text, which the chart rows do not
    // carry, so applying it there would silently disagree with the register.
    if (window.redrawActivityChart) {
      window.redrawActivityChart(function (row) {
        var okSignal = activeSignal === 'all' || row.s === activeSignal;
        var okSize = !activeSizes || activeSizes.indexOf(row.z) !== -1;
        return okSignal && okSize;
      }, describeScope());
    }
  }

  function describeScope() {
    var parts = [];
    if (activeSignal !== 'all') {
      var btn = chips.querySelector('[data-filter="' + activeSignal + '"]');
      if (btn) parts.push(btn.textContent.trim());
    }
    if (activeSizes) {
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

  q.addEventListener('input', apply);
  wire(chips, function (btn) { activeSignal = btn.dataset.filter; });
  wire(sizeChips, function (btn) {
    var v = btn.dataset.size;
    activeSizes = v === 'all' ? null : v.split(',');
  });

  apply();
})();
