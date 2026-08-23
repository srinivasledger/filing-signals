// Redraws the activity chart when a filter changes.
//
// The server renders the unfiltered chart as SVG, which is what a browser
// without JavaScript keeps. This module takes over only once a filter is
// applied, redrawing from the same rows the cards were built from so the chart
// and the register below it always agree.
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

  function draw(rows) {
    var byDay = {};
    rows.forEach(function (r) {
      (byDay[r.d] = byDay[r.d] || {})[r.s] = ((byDay[r.d] || {})[r.s] || 0) + 1;
    });
    // Keep every day on the axis even when a filter empties it, so the shape
    // of the period stays comparable instead of silently rescaling.
    var days = data.days;
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

    while (holder.firstChild) holder.removeChild(holder.firstChild);

    for (var i = 0; i <= 4; i++) {
      var y = PAD_T + PLOT_H - (PLOT_H * i / 4);
      holder.appendChild(el('line', {x1: PAD_L, y1: y, x2: W - PAD_R, y2: y, "class": 'grid'}));
      holder.appendChild(el('text', {x: PAD_L - 10, y: y + 4, "class": 'axis',
        'text-anchor': 'end'}, String(Math.round(peak * i / 4))));
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

  // Exposed for filter.js; the predicate mirrors the card filters exactly.
  window.redrawActivityChart = function (matches, scopeLabel) {
    draw(data.rows.filter(matches));
    var scope = document.getElementById('chart-scope');
    if (scope && scopeLabel) scope.textContent = scopeLabel;
  };
})();
