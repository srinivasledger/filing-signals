// Client-side filtering over the already-rendered feed. No build step, no
// framework: the page is complete and readable with JavaScript disabled.
(function () {
  var feed = document.getElementById('feed');
  if (!feed) return;
  var cards = Array.prototype.slice.call(feed.querySelectorAll('.event'));
  var q = document.getElementById('q');
  var chips = document.getElementById('chips');
  var count = document.getElementById('count');
  var noresults = document.getElementById('noresults');
  var active = 'all';

  var haystacks = cards.map(function (c) { return c.textContent.toLowerCase(); });

  function apply() {
    var term = (q.value || '').trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card, i) {
      var okSignal = active === 'all' || card.dataset.signal === active;
      var okTerm = !term || haystacks[i].indexOf(term) !== -1;
      var show = okSignal && okTerm;
      card.hidden = !show;
      if (show) shown++;
    });
    count.textContent = shown === cards.length
      ? cards.length + ' events'
      : shown + ' of ' + cards.length + ' events';
    noresults.hidden = shown !== 0;
  }

  q.addEventListener('input', apply);
  chips.addEventListener('click', function (e) {
    var btn = e.target.closest('.chip');
    if (!btn) return;
    active = btn.dataset.filter;
    chips.querySelectorAll('.chip').forEach(function (c) {
      c.classList.toggle('is-on', c === btn);
    });
    apply();
  });

  apply();
})();
