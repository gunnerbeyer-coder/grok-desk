/* Plan B desk. The fragment #d= is stored once. Files are polled. No orders. */
(function () {
  var PATH_RE = /^[A-Za-z0-9_-]{32,128}$/;
  var AGENTS = [
    ["scanner", "Migrations, then the kill screen"],
    ["auditor", "Eight layers, stop at the first red"],
    ["analyst", "Rules: alive, organic, flush, chase"],
    ["executor", "Size the paper book, or wait for Bought"],
    ["risk", "Stops only move tighter"]
  ];
  var SETTING_ROWS = [
    ["mode", "Mode"],
    ["paused", "Paused"],
    ["analyst_mode", "Analyst"],
    ["notifier", "Notifier"],
    ["fomo_handle", "Handle"],
    ["fees_ref_discount", "Referral discount"],
    ["min_liq_usd", "Min liquidity"],
    ["max_top10_pct", "Max top 10"],
    ["max_h1_pct", "Max 1h change"],
    ["stop_pct", "Stop"],
    ["trail_pct", "Trail"],
    ["trail_arm_pct", "Trail arms at"],
    ["timeout_sec", "Timeout"],
    ["bankroll_usd", "Bankroll"],
    ["max_position_usd", "Max position"],
    ["max_open_positions", "Max open"]
  ];

  var path = "";
  var state = null;
  var activity = null;
  var chart = null;
  var coinDoc = null;
  var tab = "home";
  var openMint = null;
  var mode = "loading";
  var errorKind = "";
  var timer = null;

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function money(n) {
    if (n == null || isNaN(n)) return "—";
    var sign = n < 0 ? "-" : "";
    var abs = Math.abs(n);
    if (abs >= 1000) return sign + "$" + abs.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (abs >= 1) return sign + "$" + abs.toFixed(2);
    return sign + "$" + abs.toPrecision(3);
  }

  function px(n) {
    if (n == null || isNaN(n)) return "—";
    if (Math.abs(n) >= 1) return "$" + Number(n).toFixed(4);
    return "$" + Number(n).toPrecision(4);
  }

  function pct(n) {
    if (n == null || isNaN(n)) return "—";
    var sign = n > 0 ? "+" : "";
    return sign + Number(n).toFixed(2) + "%";
  }

  function cls(n) {
    if (n > 0) return "up";
    if (n < 0) return "down";
    return "flat";
  }

  function ago(ts) {
    if (!ts) return "";
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return Math.floor(s) + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h";
  }

  function knownUrls() {
    var set = {};
    function add(url) {
      if (typeof url === "string" && url.indexOf("https://") === 0) set[url] = true;
    }
    if (state) {
      add(state.signup_url);
      add(state.profile_url);
      (state.coins || []).forEach(function (c) { add(c.url); });
      (state.positions || []).forEach(function (p) { add(p.url); });
    }
    if (coinDoc) add(coinDoc.url);
    return set;
  }

  function safeUrl(url) {
    return knownUrls()[url] ? url : "";
  }

  async function getJSON(file) {
    var res = await fetch("d/" + path + "/" + file + "?v=" + Date.now(), { cache: "no-store" });
    if (!res.ok) {
      var err = new Error(res.status === 404 ? "missing" : "network");
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  async function tick() {
    try {
      var next = await getJSON("state.json");
      state = next;
      try { activity = await getJSON("activity.json"); } catch (e) {}
      try { chart = await getJSON("chart.json"); } catch (e) {}
      if (openMint) {
        try { coinDoc = await getJSON("coin/" + encodeURIComponent(openMint) + ".json"); } catch (e) {}
      }
      mode = "ready";
    } catch (err) {
      if (!state) {
        mode = "error";
        errorKind = err.message || "network";
      }
    }
    paint();
  }

  function seriesFor(mint) {
    if (!chart || !chart.series) return [];
    return chart.series[mint] || [];
  }

  function beam(layers) {
    var by = {};
    (layers || []).forEach(function (layer) { by[layer.layer] = layer; });
    var html = '<div class="beam" aria-hidden="true">';
    for (var i = 1; i <= 8; i++) {
      var layer = by[i];
      var name = !layer ? "idle" : layer.status === "pass" ? "pass" : "fail";
      html += '<i class="seg ' + name + '"></i>';
    }
    return html + "</div>";
  }

  function statusPill(status) {
    var kind = "pill";
    if (status === "killed" || status === "rejected") kind += " kill";
    else if (status === "open") kind += " open";
    else if (status === "waiting" || status === "watch") kind += " wait";
    else if (status === "audit" || status === "audit_pass" || status === "signal") kind += " audit";
    return '<span class="' + kind + '">' + esc(status || "—") + "</span>";
  }

  function header() {
    var modeName = state && state.mode === "live" ? "live" : "paper";
    var label = modeName === "live" ? "LIVE" : "PAPER";
    var paused = state && state.paused ? '<span class="badge pause">PAUSED</span>' : "";
    return (
      '<header class="top"><h1>Desk</h1><div class="badges">' +
      paused +
      '<span class="badge ' + modeName + '">' + label + "</span></div></header>"
    );
  }

  function home() {
    var bal = state.balance || {};
    var pnl = bal.pnl || 0;
    var stale = state.ts && Date.now() / 1000 - state.ts > 120
      ? '<div class="stale">Snapshot is ' + esc(ago(state.ts)) + " old</div>"
      : "";
    var banner = state.paused ? '<div class="banner">Paused. Open stops still run. Resume from Telegram.</div>' : "";
    var positions = state.positions || [];
    var posHtml = positions.length
      ? positions.map(positionRow).join("")
      : '<p class="empty">No open positions</p>';
    var coins = state.coins || [];
    var coinHtml = coins.length
      ? coins.map(coinRow).join("")
      : '<p class="empty">Waiting for a migration</p>';
    var audit = state.last_audit;
    var beamHtml = audit ? beam(audit.layers) : beam([]);
    return (
      header() +
      '<div class="main" id="scroller">' +
      '<div class="equity">' + esc(money(bal.equity)) + "</div>" +
      '<div class="pnl ' + cls(pnl) + '">' + esc(pct(bal.starting ? (pnl / bal.starting) * 100 : 0)) +
      " · " + esc(money(pnl)) + "</div>" + stale + banner +
      '<div class="section"><h2>Auditor</h2><span>' + esc(audit && audit.symbol ? audit.symbol : "") + "</span></div>" +
      beamHtml +
      '<div class="section"><h2>Positions</h2><span>' + positions.length + "</span></div>" +
      '<div class="card">' + posHtml + "</div>" +
      '<div class="section"><h2>Coins</h2></div>' +
      '<div class="card">' + coinHtml + "</div></div>"
    );
  }

  function positionRow(pos) {
    return (
      '<button class="row" data-mint="' + esc(pos.mint) + '">' +
      '<div class="grow"><div class="sym">' + esc(pos.symbol || "—") + "</div>" +
      '<div class="sub">' + esc(px(pos.entry)) + " → " + esc(px(pos.price)) + "</div></div>" +
      '<canvas class="spark" data-mint="' + esc(pos.mint) + '"></canvas>' +
      '<div class="right ' + cls(pos.pnl_usd) + '"><div>' + esc(money(pos.pnl_usd)) + "</div>" +
      '<div class="sub">' + esc(pct(pos.pnl_pct)) + "</div></div></button>"
    );
  }

  function coinRow(coin) {
    return (
      '<button class="row" data-mint="' + esc(coin.mint) + '">' +
      '<div class="grow"><div class="sym">' + esc(coin.symbol || coin.mint.slice(0, 4)) + "</div>" +
      '<div class="sub">' + esc(coin.name || "") + " " + statusPill(coin.status) + "</div></div>" +
      '<canvas class="spark" data-mint="' + esc(coin.mint) + '"></canvas>' +
      '<div class="right"><div>' + esc(px(coin.price)) + "</div>" +
      '<div class="sub">' + esc(coin.score == null ? "" : Math.round(coin.score)) + "</div></div></button>"
    );
  }

  function agents() {
    var by = {};
    (state.agents || []).forEach(function (agent) { by[agent.name] = agent; });
    var cards = AGENTS.map(function (pair) {
      var agent = by[pair[0]] || { status: "offline", detail: "", ts: null };
      var extra = pair[0] === "auditor" ? beam(state.last_audit && state.last_audit.layers) : "";
      return (
        '<article class="agent"><header><h3><i class="dot ' + esc(agent.status) + '"></i>' +
        esc(pair[0]) + "</h3><span class=\"sub\">" + esc(ago(agent.ts)) + "</span></header>" +
        '<p class="role">' + esc(pair[1]) + "</p>" +
        '<p class="sub">' + esc(agent.detail || agent.status) + "</p>" + extra + "</article>"
      );
    }).join("");
    var rest = ["telegram", "writer"].map(function (name) {
      var agent = by[name] || { status: "offline", detail: "" };
      return '<div class="kv"><b>' + esc(name) + "</b><span>" + esc(agent.status) + " · " + esc(agent.detail || "") + "</span></div>";
    }).join("");
    return header() + '<div class="main" id="scroller">' + cards + '<div class="card" style="margin-top:16px">' + rest + "</div></div>";
  }

  function activityView() {
    var items = (activity && activity.items) || [];
    var body = items.length
      ? '<div class="card">' + items.slice().reverse().map(function (item) {
          return (
            '<div class="row"><div class="grow"><div class="sym">' + esc(item.text || item.kind) + "</div>" +
            '<div class="sub">' + esc(item.kind) + " · " + esc(ago(item.ts)) + "</div></div></div>"
          );
        }).join("") + "</div>"
      : '<p class="empty">No activity yet</p>';
    return header() + '<div class="main" id="scroller"><div class="section"><h2>Activity</h2></div>' + body + "</div>";
  }

  function account() {
    var settings = state.settings || {};
    var rows = SETTING_ROWS.map(function (pair) {
      var value = settings[pair[0]];
      if (pair[0] === "paused") value = value === "1" || value === true ? "Yes" : "No";
      return '<div class="kv"><b>' + esc(pair[1]) + "</b><span>" + esc(value == null ? "—" : value) + "</span></div>";
    }).join("");
    var signup = safeUrl(state.signup_url);
    return (
      header() +
      '<div class="main" id="scroller">' +
      '<p class="muted">Read only. Pause, mode, and settings change in Telegram.</p>' +
      '<div class="card">' + rows + "</div>" +
      '<button class="primary" id="invite" type="button"' + (signup ? "" : ' aria-disabled="true"') + ">Invite</button>" +
      '<p class="sub" style="margin-top:10px">Share the signup link from the desk. The phone does not build it.</p>' +
      "</div>"
    );
  }

  function sheet() {
    var doc = coinDoc || {};
    var coin = (state.coins || []).filter(function (c) { return c.mint === openMint; })[0] || {};
    var symbol = doc.symbol || coin.symbol || openMint.slice(0, 4);
    var url = safeUrl(doc.url || coin.url);
    var verdict = doc.verdict;
    var chips = "";
    if (verdict) {
      ["alive", "organic", "flush", "chase"].forEach(function (key) {
        var on = !!verdict[key];
        var bad = key === "flush" || key === "chase";
        var safe = bad ? !on : on;
        chips += '<span class="chip ' + (safe ? "on" : "off") + '">' + esc(key + " " + (on ? "yes" : "no")) + "</span>";
      });
      chips += '<span class="chip">score ' + esc(verdict.score) + "</span>";
    }
    var layers = (doc.layers || []).map(function (layer) {
      return '<div class="kv"><b>' + esc(layer.layer + " " + layer.name) + "</b><span>" +
        esc(layer.status) + " · " + esc(layer.detail || "") + "</span></div>";
    }).join("");
    var pos = doc.position;
    var posHtml = pos
      ? '<div class="card"><div class="kv"><b>Entry</b><span>' + esc(px(pos.entry)) + "</span></div>" +
        '<div class="kv"><b>Stop</b><span>' + esc(px(pos.stop)) + "</span></div>" +
        '<div class="kv"><b>PnL</b><span class="' + cls(pos.pnl_usd) + '">' + esc(money(pos.pnl_usd)) + "</span></div></div>"
      : "";
    var link = url
      ? '<a class="primary" id="buy" href="' + esc(url) + '" rel="noreferrer">Buy on FOMO</a>'
      : '<span class="primary" aria-disabled="true">Buy on FOMO</span>';
    return (
      '<div class="sheet" id="sheet">' +
      '<button class="back" id="back" type="button">Back</button>' +
      "<h2>" + esc(symbol) + "</h2>" +
      '<div class="sub">' + esc(doc.name || coin.name || "") + " · " + esc(doc.status || coin.status || "") + "</div>" +
      '<div class="price-lg">' + esc(px(coin.price != null ? coin.price : (pos && pos.price))) + "</div>" +
      '<canvas class="candles" id="candles"></canvas>' +
      '<div class="chips">' + chips + "</div>" +
      posHtml +
      (layers ? '<div class="card" style="margin-top:12px">' + layers + "</div>" : '<p class="empty">No audit yet</p>') +
      link +
      "</div>"
    );
  }

  function gate(title, body, spinning) {
    return (
      '<main class="gate">' +
      (spinning ? '<div class="spin"></div>' : "") +
      '<p class="eyebrow">Desk</p><h1>' + esc(title) + "</h1>" +
      '<p class="muted">' + esc(body) + "</p></main>"
    );
  }

  function tabs() {
    var items = [
      ["home", "Home"],
      ["agents", "Agents"],
      ["activity", "Activity"],
      ["account", "Account"]
    ];
    return (
      '<nav class="tabs">' +
      items.map(function (item) {
        return '<button type="button" data-tab="' + item[0] + '" class="' + (tab === item[0] ? "on" : "") + '">' +
          icon(item[0]) + esc(item[1]) + "</button>";
      }).join("") +
      "</nav>"
    );
  }

  function icon(name) {
    var paths = {
      home: '<path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z"/>',
      agents: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
      activity: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
      account: '<circle cx="12" cy="8" r="3"/><path d="M5 19c1.5-3 3.8-4.5 7-4.5S17.5 16 19 19"/>'
    };
    return '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">' +
      (paths[name] || "") + "</svg>";
  }

  function paint() {
    var root = document.getElementById("app");
    var scroller = document.getElementById("scroller");
    var scroll = scroller ? scroller.scrollTop : 0;
    var html;
    if (mode === "loading") {
      html = gate("Loading desk", "Reading the snapshot.", true);
    } else if (mode === "need") {
      html = gate(
        "Open the desk link once",
        "Use the link that ends with #d=… . This phone stores it and the address bar is cleared."
      );
    } else if (mode === "error") {
      html = gate(
        errorKind === "missing" ? "Nothing at this desk path" : "Can't reach the desk",
        errorKind === "missing"
          ? "The process has not published this path yet, or the path is wrong."
          : "Retrying."
      );
    } else if (openMint) {
      html = '<div class="phone">' + sheet() + "</div>";
    } else {
      var body = tab === "agents" ? agents() : tab === "activity" ? activityView() : tab === "account" ? account() : home();
      html = '<div class="phone">' + body + tabs() + "</div>";
    }
    root.innerHTML = html;
    var nextScroll = document.getElementById("scroller");
    if (nextScroll) nextScroll.scrollTop = scroll;
    draw();
    bind();
  }

  function draw() {
    if (!window.DeskChart) return;
    document.querySelectorAll("canvas.spark").forEach(function (canvas) {
      DeskChart.drawSpark(canvas, seriesFor(canvas.getAttribute("data-mint")));
    });
    var candles = document.getElementById("candles");
    if (candles && openMint) {
      var points = (coinDoc && coinDoc.series) || seriesFor(openMint);
      DeskChart.drawCandles(candles, points);
    }
  }

  function bind() {
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        tab = button.getAttribute("data-tab");
        openMint = null;
        paint();
      });
    });
    document.querySelectorAll("button.row[data-mint]").forEach(function (button) {
      button.addEventListener("click", function () {
        openMint = button.getAttribute("data-mint");
        coinDoc = null;
        paint();
        tick();
      });
    });
    var back = document.getElementById("back");
    if (back) {
      back.addEventListener("click", function () {
        openMint = null;
        coinDoc = null;
        paint();
      });
    }
    var invite = document.getElementById("invite");
    if (invite) invite.addEventListener("click", shareInvite);
  }

  async function shareInvite() {
    var url = state && safeUrl(state.signup_url);
    if (!url) return;
    if (navigator.share) {
      try {
        await navigator.share({ url: url });
        return;
      } catch (err) {}
    }
    try {
      await navigator.clipboard.writeText(url);
      toast("Invite link copied");
    } catch (err) {
      toast("Use Telegram /invite");
    }
  }

  function toast(text) {
    var node = document.createElement("div");
    node.className = "toast";
    node.textContent = text;
    document.body.appendChild(node);
    setTimeout(function () { node.remove(); }, 1600);
  }

  function boot() {
    var params = new URLSearchParams(location.hash.replace(/^#/, ""));
    if (params.get("d")) localStorage.setItem("desk_path", params.get("d"));
    if (params.get("t")) localStorage.setItem("desk_token", params.get("t"));
    if (params.has("d") || params.has("t")) {
      history.replaceState(null, "", location.pathname + location.search);
    }
    path = localStorage.getItem("desk_path") || "";
    if (!PATH_RE.test(path)) {
      mode = "need";
      paint();
      return;
    }
    mode = "loading";
    paint();
    tick();
    timer = setInterval(tick, 15000);
  }

  boot();
})();
