// Redraws the activity chart when a filter changes.
//
// The server renders the unfiltered chart as SVG, which is what a browser
// without JavaScript keeps. This module takes over only once a filter is
// applied, redrawing from the recent filing-day rows. The record below has a
// separate entry limit, which the page explains beside the graph.
(function () {
  var holder = document.querySelector('.chart svg');
  var raw = document.getElementById('chart-data');
  if (!holder || !raw) return;

  var data;
  try { data = JSON.parse(raw.textContent); } catch (e) { return; }

  var W = 1000, H = 260, PAD_L = 44, PAD_R = 12, PAD_T = 18, PAD_B = 42;
  var PLOT_W = W - PAD_L - PAD_R, PLOT_H = H - PAD_T - PAD_B;
  var GAP = 2, RADIUS = 4, NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function roundedTop(x, y, w, h, r) {
    r = Math.max(0, Math.min(r, h, w / 2));
    return 'M' + x + ',' + (y + h) + ' V' + (y + r) +
           ' Q' + x + ',' + y + ' ' + (x + r) + ',' + y +
           ' H' + (x + w - r) + ' Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) +
           ' V' + (y + h) + ' Z';
  }

  function draw(rows, days) {
    var byDay = {};
    rows.forEach(function (r) {
      (byDay[r.d] = byDay[r.d] || {})[r.s] = ((byDay[r.d] || {})[r.s] || 0) + 1;
    });
    // Keep every filing day in the selected period on the axis even when a
    // signal, size or form filter empties it. Changing the period itself does
    // change the axis, so an October selection never shows September bars.
    while (holder.firstChild) holder.removeChild(holder.firstChild);
    if (!days.length) {
      holder.appendChild(el('text', {x: W / 2, y: H / 2,
        'text-anchor': 'middle', 'font-size': 18, fill: 'var(--muted)'},
      'No filing days for this period in the recent chart window'));
      var emptyLegend = document.querySelector('.chart-legend');
      if (emptyLegend) emptyLegend.textContent = 'No filing days in this period';
      return;
    }

    var peak = 0;
    days.forEach(function (d) {
      var t = 0, c = byDay[d] || {};
      for (var k in c) t += c[k];
      if (t > peak) peak = t;
    });
    peak = peak || 1;

    var present = data.order.filter(function (sig) {
      return days.some(function (d) { return (byDay[d] || {})[sig]; });
    });

    // Never more ticks than whole numbers to label: four steps over a peak of
    // 2 rounds to "2, 2, 1, 1, 0", which reads as a broken axis.
    var steps = Math.max(1, Math.min(4, peak));
    for (var i = 0; i <= steps; i++) {
      var y = PAD_T + PLOT_H - (PLOT_H * i / steps);
      holder.appendChild(el('line', {x1: PAD_L, y1: y, x2: W - PAD_R, y2: y, "class": 'grid'}));
      holder.appendChild(el('text', {x: PAD_L - 10, y: y + 4, "class": 'axis',
        'text-anchor': 'end'}, String(Math.round(peak * i / steps))));
    }

    var slot = PLOT_W / days.length;
    var barW = Math.min(46, slot * 0.62);

    days.forEach(function (day, idx) {
      var counts = byDay[day] || {};
      var cx = PAD_L + slot * idx + slot / 2;
      var x = cx - barW / 2;
      var total = 0;
      for (var k in counts) total += counts[k];

      var stack = present.filter(function (s) { return counts[s]; });
      var cursor = PAD_T + PLOT_H;
      stack.forEach(function (sig, si) {
        var segH = PLOT_H * counts[sig] / peak;
        var drawn = Math.max(1, segH - GAP);
        cursor -= segH;
        var isTop = si === stack.length - 1;
        var d = isTop ? roundedTop(x, cursor, barW, drawn, RADIUS)
                      : 'M' + x + ',' + cursor + ' h' + barW + ' v' + drawn + ' h-' + barW + ' Z';
        var path = el('path', {d: d, fill: 'var(' + data.vars[sig] + ')', "class": 'seg'});
        path.appendChild(el('title', {}, day + ' · ' + data.labels[sig] + ': ' + counts[sig]));
        holder.appendChild(path);
      });

      if (total) {
        holder.appendChild(el('text', {x: cx, y: PAD_T + PLOT_H - PLOT_H * total / peak - 7,
          "class": 'bar-total', 'text-anchor': 'middle'}, String(total)));
      }
      holder.appendChild(el('text', {x: cx, y: H - PAD_B + 20, "class": 'axis',
        'text-anchor': 'middle'}, day.slice(5)));
    });

    var legend = document.querySelector('.chart-legend');
    if (legend) {
      legend.innerHTML = '';
      present.forEach(function (sig) {
        var span = document.createElement('span');
        span.className = 'key';
        var i = document.createElement('i');
        i.style.background = 'var(' + data.vars[sig] + ')';
        span.appendChild(i);
        span.appendChild(document.createTextNode(data.labels[sig]));
        legend.appendChild(span);
      });
      if (!present.length) legend.textContent = 'No events match that filter';
    }
  }

  // Exposed for filter.js. The chart contains only the most recent filing
  // days, so an older period may have no dates in this window at all.
  window.redrawActivityChart = function (matches, scopeLabel, periodKey) {
    var days = data.days.filter(function (day) {
      return !periodKey || periodKey === 'all' || day.indexOf(periodKey) === 0;
    });
    var rows = data.rows.filter(function (row) {
      return (!periodKey || periodKey === 'all' || row.d.indexOf(periodKey) === 0)
        && matches(row);
    });
    draw(rows, days);
    var dayLabel = days.length + (days.length === 1 ? ' filing day' : ' filing days');
    var label = dayLabel + (scopeLabel ? ' \u00b7 ' + scopeLabel : '');
    var scope = document.getElementById('chart-scope');
    if (scope) scope.textContent = label;
    var title = 'Events per filing day by signal type: ' + label;
    holder.setAttribute('aria-label', title);
    holder.insertBefore(el('title', {}, title), holder.firstChild);
  };
})();
