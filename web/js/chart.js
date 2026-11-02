/* Canvas charts. Prices are {t, p} ticks. Candles bucket them. */
(function () {
  var UP = "#00d35a";
  var DOWN = "#ff453a";
  var GRID = "rgba(235,235,245,0.12)";
  var MUTED = "rgba(235,235,245,0.6)";

  function candles(points, seconds) {
    var buckets = [];
    var index = {};
    (points || []).forEach(function (pt) {
      if (!pt || typeof pt.p !== "number") return;
      var t = Math.floor(pt.t / seconds) * seconds;
      var bar = index[t];
      if (!bar) {
        bar = { t: t, o: pt.p, h: pt.p, l: pt.p, c: pt.p };
        index[t] = bar;
        buckets.push(bar);
      } else {
        bar.h = Math.max(bar.h, pt.p);
        bar.l = Math.min(bar.l, pt.p);
        bar.c = pt.p;
      }
    });
    return buckets;
  }

  function setup(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var width = canvas.clientWidth || 320;
    var height = canvas.clientHeight || 220;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx: ctx, w: width, h: height };
  }

  function drawCandles(canvas, points) {
    if (!canvas) return;
    var view = setup(canvas);
    var ctx = view.ctx;
    var bars = candles(points, 60);
    if (!bars.length) {
      ctx.fillStyle = MUTED;
      ctx.font = "13px -apple-system, sans-serif";
      ctx.fillText("No prices yet", 16, view.h / 2);
      return;
    }
    var hi = Math.max.apply(null, bars.map(function (b) { return b.h; }));
    var lo = Math.min.apply(null, bars.map(function (b) { return b.l; }));
    if (hi === lo) {
      hi += hi * 0.01 || 0.01;
      lo -= lo * 0.01 || 0.01;
    }
    var pad = (hi - lo) * 0.08;
    hi += pad;
    lo -= pad;
    var left = 8;
    var right = 8;
    var top = 12;
    var bottom = 18;
    var plotW = view.w - left - right;
    var plotH = view.h - top - bottom;
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1;
    for (var g = 0; g < 4; g++) {
      var y = top + (plotH * g) / 3;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(view.w - right, y);
      ctx.stroke();
    }
    var slot = plotW / bars.length;
    var bodyW = Math.max(2, Math.min(10, slot * 0.62));
    bars.forEach(function (bar, i) {
      var x = left + slot * i + slot / 2;
      var yH = top + ((hi - bar.h) / (hi - lo)) * plotH;
      var yL = top + ((hi - bar.l) / (hi - lo)) * plotH;
      var yO = top + ((hi - bar.o) / (hi - lo)) * plotH;
      var yC = top + ((hi - bar.c) / (hi - lo)) * plotH;
      var up = bar.c >= bar.o;
      ctx.strokeStyle = up ? UP : DOWN;
      ctx.fillStyle = up ? UP : DOWN;
      ctx.beginPath();
      ctx.moveTo(x, yH);
      ctx.lineTo(x, yL);
      ctx.stroke();
      var topBody = Math.min(yO, yC);
      var height = Math.max(1, Math.abs(yC - yO));
      ctx.fillRect(x - bodyW / 2, topBody, bodyW, height);
    });
    var last = bars[bars.length - 1].c;
    ctx.fillStyle = last >= bars[0].o ? UP : DOWN;
    ctx.font = "12px -apple-system, sans-serif";
    ctx.fillText(fmtPx(last), left, view.h - 4);
  }

  function drawSpark(canvas, points) {
    if (!canvas) return;
    var view = setup(canvas);
    var ctx = view.ctx;
    var pts = (points || []).filter(function (pt) { return pt && typeof pt.p === "number"; });
    if (pts.length < 2) return;
    var hi = Math.max.apply(null, pts.map(function (pt) { return pt.p; }));
    var lo = Math.min.apply(null, pts.map(function (pt) { return pt.p; }));
    if (hi === lo) hi += 1;
    ctx.beginPath();
    pts.forEach(function (pt, i) {
      var x = (i / (pts.length - 1)) * view.w;
      var y = ((hi - pt.p) / (hi - lo)) * view.h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = pts[pts.length - 1].p >= pts[0].p ? UP : DOWN;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  function fmtPx(n) {
    if (n >= 1) return "$" + n.toFixed(4);
    return "$" + n.toPrecision(3);
  }

  window.DeskChart = { drawCandles: drawCandles, drawSpark: drawSpark };
})();
