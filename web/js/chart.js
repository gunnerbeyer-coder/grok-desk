/* Canvas charts. devicePixelRatio-aware. No glow. */
(function () {
  var UP = "#00d35a";
  var DOWN = "#ff453a";
  var GRID = "rgba(84,84,88,0.55)";
  var FAINT = "rgba(235,235,245,0.3)";
  var FONT = "11px -apple-system, BlinkMacSystemFont, sans-serif";

  function setup(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var width = canvas.clientWidth || 320;
    var height = canvas.clientHeight || 168;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx: ctx, w: width, h: height };
  }

  function candlesOf(points, seconds) {
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

  function drawCandles(canvas, points) {
    if (!canvas) return;
    var view = setup(canvas);
    var ctx = view.ctx;
    var bars = candlesOf(points, 60);
    if (!bars.length) {
      ctx.fillStyle = FAINT;
      ctx.font = "13px -apple-system, sans-serif";
      ctx.fillText("No prices yet", 16, view.h / 2);
      return;
    }
    var hi = Math.max.apply(null, bars.map(function (b) { return b.h; }));
    var lo = Math.min.apply(null, bars.map(function (b) { return b.l; }));
    if (hi === lo) {
      hi += Math.abs(hi) * 0.02 || 0.01;
      lo -= Math.abs(lo) * 0.02 || 0.01;
    }
    var pad = (hi - lo) * 0.08;
    hi += pad;
    lo -= pad;
    var left = 12;
    var right = 12;
    var top = 12;
    var bottom = 16;
    var plotW = view.w - left - right;
    var plotH = view.h - top - bottom;
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1;
    for (var g = 0; g < 4; g++) {
      var gy = top + (plotH * g) / 3;
      ctx.beginPath();
      ctx.moveTo(left, gy);
      ctx.lineTo(view.w - right, gy);
      ctx.stroke();
    }
    var slot = plotW / bars.length;
    var bodyW = Math.max(3, Math.min(12, slot * 0.62));
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
      ctx.fillRect(x - bodyW / 2, Math.min(yO, yC), bodyW, Math.max(6, Math.abs(yC - yO)));
    });
  }

  function drawLine(canvas, points) {
    if (!canvas) return;
    var view = setup(canvas);
    var ctx = view.ctx;
    var pts = (points || []).filter(function (pt) { return pt && typeof pt.p === "number"; });
    if (pts.length < 2) return;
    var hi = Math.max.apply(null, pts.map(function (pt) { return pt.p; }));
    var lo = Math.min.apply(null, pts.map(function (pt) { return pt.p; }));
    var flat = hi === lo || Math.abs(hi - lo) < Math.abs(hi) * 0.004;
    if (flat) {
      var mid = hi;
      var span = Math.abs(mid) * 0.16 || 1;
      hi = mid + span * 0.72;
      lo = mid - span * 0.28;
    } else {
      var pad = (hi - lo) * 0.18;
      hi += pad;
      lo -= pad * 0.65;
    }
    var left = 2;
    var right = 48;
    var top = 8;
    var bottom = 22;
    var plotW = view.w - left - right;
    var plotH = view.h - top - bottom;
    function yOf(v) { return top + ((hi - v) / (hi - lo)) * plotH; }
    function xOf(i) { return left + (i / (pts.length - 1)) * plotW; }

    ctx.font = FONT;
    ctx.textBaseline = "middle";
    ctx.lineWidth = 1;
    for (var g = 0; g < 4; g++) {
      var val = hi - ((hi - lo) * g) / 3;
      var y = yOf(val);
      ctx.strokeStyle = "rgba(84,84,88,0.35)";
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + plotW, y);
      ctx.stroke();
      ctx.fillStyle = FAINT;
      ctx.textAlign = "left";
      ctx.fillText(axisLabel(val, hi - lo), left + plotW + 6, y);
    }

    ctx.beginPath();
    pts.forEach(function (pt, i) {
      var x = xOf(i);
      var y = yOf(pt.p);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    var rising = pts[pts.length - 1].p >= pts[0].p;
    ctx.strokeStyle = rising ? UP : DOWN;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    ctx.fillStyle = FAINT;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    ctx.fillText(clock(pts[0].t), left, view.h - 4);
    ctx.textAlign = "right";
    ctx.fillText(clock(pts[pts.length - 1].t), left + plotW, view.h - 4);
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
    ctx.lineWidth = 1.6;
    ctx.stroke();
  }

  function axisLabel(n, span) {
    var abs = Math.abs(n);
    var sign = n < 0 ? "-" : "";
    if ((span > 0 && span < 1) || (abs > 0 && abs < 0.01)) {
      var basis = span > 0 ? span : abs || 1;
      var digits = Math.min(8, Math.max(2, Math.ceil(-Math.log10(basis)) + 1));
      return sign + "$" + abs.toFixed(digits);
    }
    if (span < 800 && abs < 100000) return sign + "$" + Math.round(abs).toLocaleString();
    return compact(n);
  }

  function compact(n) {
    var sign = n < 0 ? "-" : "";
    var abs = Math.abs(n);
    if (abs >= 10000) return sign + "$" + Math.round(abs / 1000) + "k";
    if (abs >= 1000) return sign + "$" + (abs / 1000).toFixed(1) + "k";
    return sign + "$" + Math.round(abs).toLocaleString();
  }

  function clock(ts) {
    var d = new Date(ts * 1000);
    var h = d.getHours();
    var m = d.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return h + ":" + (m < 10 ? "0" : "") + m + " " + ap;
  }

  window.DeskChart = { drawCandles: drawCandles, drawLine: drawLine, drawSpark: drawSpark };
})();
