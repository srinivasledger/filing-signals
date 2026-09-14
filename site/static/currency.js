// How far behind the data is, worked out when the page is READ.
//
// The figure used to be baked at build time, so a page built on Saturday went
// on claiming Saturday's freshness on Tuesday. It also counted today as a day
// the scan had missed - but today's index is not published until about 22:00
// ET tonight, so "1 business day behind" was the best score the site could
// ever print and it never once said it was current.
//
// Same rule as the pipeline: the newest day EDGAR has published an index for
// is yesterday in New York, or today once it is past 23:00 there. Weekends and
// the days the scan actually found no index for do not count against it.
(function () {
  // The masthead marker carries the dates on every page; /status also has a
  // figure to correct. Either may be absent, and neither is required.
  var mark = document.getElementById('freshness');
  var el = document.getElementById('currency');
  var src = (mark && mark.dataset.through) ? mark : el;
  if (!src || !src.dataset.through) return;

  var through = src.dataset.through;
  var closed = (src.dataset.closed || '').split(' ').filter(Boolean);

  function iso(d) {
    return d.getFullYear() + '-'
      + String(d.getMonth() + 1).padStart(2, '0') + '-'
      + String(d.getDate()).padStart(2, '0');
  }

  var etNow;
  try {
    etNow = new Date(new Date().toLocaleString('en-US',
      { timeZone: 'America/New_York' }));
    if (isNaN(etNow)) return;                 // no Intl data: leave the build's answer
  } catch (e) { return; }

  var horizon = new Date(etNow.getFullYear(), etNow.getMonth(), etNow.getDate());
  if (etNow.getHours() < 23) horizon.setDate(horizon.getDate() - 1);

  var cur = new Date(through + 'T00:00:00');
  if (isNaN(cur)) return;
  var behind = 0, guard = 0;
  while (cur < horizon && guard++ < 4000) {
    cur.setDate(cur.getDate() + 1);
    var day = cur.getDay();
    if (day !== 0 && day !== 6 && closed.indexOf(iso(cur)) === -1) behind++;
  }

  var words = behind <= 0
    ? 'current'
    : behind + ' business day' + (behind === 1 ? '' : 's') + ' behind';
  if (el) el.textContent = words;

  // One business day behind is the normal state between scans, not a fault.
  // Beyond that something has stopped, and it should be said on whatever page
  // the reader is on rather than only on the one they would have to think to
  // visit.
  var notice = document.getElementById('stale-notice');
  if (notice) notice.hidden = behind <= 1;
  // The notice's own figure, or it is revealed still reading "current".
  var gap = document.getElementById('stale-gap');
  if (gap) gap.textContent = words;
  if (mark) {
    mark.hidden = behind <= 1;
    mark.textContent = 'Data ' + words;
    mark.title = 'The newest filing day held is ' + through
      + '. Scans have not extended it since.';
  }
})();
