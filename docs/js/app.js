/* Plan B desk. #d= and #t= are stored once. JSON is polled from d/{path}/.
   FOMO links are taken from that JSON. This file does not build them. */
(function () {
  var PATH_RE = /^[A-Za-z0-9_-]{32,128}$/;
  var ROSTER = ["scanner", "auditor", "analyst", "executor", "risk", "copier"];
  var statsScope = "today";
  var SOURCE_LABEL = {migration: "Migration", trending: "Trending", copy: "Copy", gt_new: "New pools",
    gt_trending: "Gecko hot", dex_new: "Takeovers", pump_movers: "Pump movers"};
  var SETTING_ROWS = [
    ["mode", "Mode"],
    ["paused", "Paused"],
    ["analyst_mode", "Analyst"],
    ["notifier", "Notifier"],
    ["fomo_handle", "Handle"],
    ["stop_pct", "Stop"],
    ["trail_pct", "Trail"],
    ["timeout_sec", "Timeout"],
    ["bankroll_usd", "Bankroll"],
    ["max_position_usd", "Max position"]
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

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function num(value) {
    var n = Number(value);
    return isNaN(n) ? 0 : n;
  }

  function money(n, digits) {
    if (n == null || isNaN(n)) return "—";
    var sign = n < 0 ? "-" : "";
    var abs = Math.abs(n);
    var d = digits == null ? (abs >= 1 || abs === 0 ? 2 : 4) : digits;
    if (abs >= 1000) return sign + "$" + abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    return sign + "$" + abs.toFixed(d);
  }

  function signed(n) {
    if (n == null || isNaN(n)) return "—";
    if (!n) return "+$0";
    return (n > 0 ? "+" : "-") + money(Math.abs(n)).replace(/^-/, "");
  }

  function px(n) {
    if (n == null || isNaN(Number(n))) return "—";
    var value = Number(n);
    var abs = Math.abs(value);
    if (abs >= 1) return "$" + value.toFixed(4);
    if (abs >= 0.0001) return "$" + value.toFixed(6);
    if (!abs) return "$0";
    return "$" + value.toFixed(8);
  }

  function pct(n) {
    if (n == null || isNaN(n)) return "—";
    var sign = n > 0 ? "+" : "";
    return sign + Number(n).toFixed(1) + "%";
  }

  function cls(n) {
    if (n > 0) return "up";
    if (n < 0) return "down";
    return "flat";
  }

  function clock(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    function z(n) { return (n < 10 ? "0" : "") + n; }
    return z(d.getHours()) + ":" + z(d.getMinutes()) + ":" + z(d.getSeconds());
  }

  function holdLabel(seconds) {
    if (seconds == null || isNaN(seconds)) return "—";
    if (seconds < 90) return Math.max(1, Math.round(seconds)) + "s";
    if (seconds < 3600) return Math.round(seconds / 60) + "m";
    return (seconds / 3600).toFixed(1) + "h";
  }

  function items() {
    return (activity && activity.items) || [];
  }

  function agentMap() {
    var by = {};
    (state && state.agents || []).forEach(function (agent) { by[agent.name] = agent; });
    return by;
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

  function deskLink() {
    if (!state) return "";
    return safeUrl(state.profile_url) || safeUrl(state.signup_url);
  }

  function model() {
    var bal = (state && state.balance) || {};
    var equity = num(bal.equity);
    var starting = num(bal.starting) || equity || 1;
    var pnl = num(bal.pnl);
    var positions = (state && state.positions) || [];
    var coins = (state && state.coins) || [];
    var feed = items();
    var unreal = positions.reduce(function (sum, pos) { return sum + num(pos.pnl_usd); }, 0);
    var realized = pnl - unreal;
    var multiple = starting ? equity / starting : 1;
    var seen = coins.length;
    var passed = coins.filter(function (c) {
      return c.status && c.status !== "killed" && c.status !== "rejected" && c.status !== "skipped";
    }).length;
    var fills = feed.filter(function (item) { return item.kind === "fill"; }).length;
    if (!fills) fills = positions.length;
    var exits = feed.filter(function (item) { return item.kind === "exit"; });
    var wins = 0;
    exits.forEach(function (item) {
      var match = /pnl\s+([+-]?\d+(?:\.\d+)?)/.exec(item.text || "");
      if (match && Number(match[1]) > 0) wins += 1;
    });
    var stops = exits.filter(function (item) { return /stop/i.test(item.text || ""); }).length;
    var scans = feed.filter(function (item) { return item.kind === "kill" || item.kind === "scan"; }).length;
    var inflight = coins.filter(function (c) {
      return c.status === "waiting" || c.status === "audit" || c.status === "signal" || c.status === "awaiting";
    }).length;
    var by = agentMap();
    var online = ROSTER.filter(function (name) {
      var agent = by[name];
      return agent && (agent.status === "ok" || agent.status === "stale");
    }).length;
    var green = positions.filter(function (pos) { return num(pos.pnl_usd) > 0; }).length;
    var avg = null;
    if (positions.length) {
      var now = (state && state.ts) || Date.now() / 1000;
      avg = positions.reduce(function (sum, pos) { return sum + Math.max(0, now - num(pos.opened_at)); }, 0) / positions.length;
    }
    var lossExits = exits.length - wins;
    var drawdown = lossExits ? null : 0;
    var milestones = [2, 4, 6.77];
    var next = milestones[0];
    milestones.forEach(function (mark) { if (multiple >= mark) next = mark; });
    var upcoming = milestones.filter(function (mark) { return multiple < mark; })[0] || milestones[milestones.length - 1];
    var gap = Math.max(0, starting * upcoming - equity);
    var latestOut = null;
    for (var i = feed.length - 1; i >= 0; i--) {
      if (feed[i].kind === "kill" || feed[i].kind === "skip") { latestOut = feed[i]; break; }
    }
    return {
      equity: equity, starting: starting, pnl: pnl, realized: realized, unreal: unreal,
      multiple: multiple, seen: seen, passed: passed, fills: fills, wins: wins,
      exits: exits.length, stops: stops, scans: scans, inflight: inflight, online: online,
      green: green, avg: avg, drawdown: drawdown, upcoming: upcoming, gap: gap,
      positions: positions, coins: coins, latestOut: latestOut, nextLabel: upcoming
    };
  }

  function ghost() {
    return '<svg class="ghost" viewBox="0 0 40 40" aria-hidden="true">' +
      '<circle cx="20" cy="20" r="20" fill="#1c1c1e"/>' +
      '<path fill="#fff" d="M20 8.5c-4.7 0-8.2 3.4-8.2 8.1v10.2c.9-.9 1.8-1.3 2.6-.7.8.6 1.3 1.5 2.2 1.5s1.4-.9 2.2-1.5 1.6-.6 2.4.1 1.4 1.4 2.3 1.4 1.5-1 2.4-1.5 1.7-.7 2.5.2c.6.7 1.3 1.2 2.2.7V16.6c0-4.7-3.5-8.1-8.2-8.1z"/>' +
      '<circle cx="16.8" cy="16.6" r="1.25" fill="#1c1c1e"/>' +
      '<circle cx="23.2" cy="16.6" r="1.25" fill="#1c1c1e"/>' +
      "</svg>";
  }

  function header() {
    var m = state ? model() : null;
    var badge = "";
    if (state && state.paused) badge += '<span class="badge pause">Paused</span>';
    if (state && state.mode === "paper") badge += '<span class="badge paper">PAPER</span>';
    else if (state) badge += '<span class="badge live">LIVE</span>';
    var count = m ? '<div class="agentcount">' + m.online + " agents</div>" : "";
    return '<header class="top"><div class="idblock">' + ghost() +
      '<div><div class="name">Grok Bot</div><div class="tagline">night relay · solana</div></div></div>' +
      '<div class="badges">' + badge + count + "</div></header>";
  }

  function pills(m) {
    var stop = (state.settings && state.settings.stop_pct) || "12";
    var winText = m.exits ? (m.wins + "/" + m.exits) : "0/0";
    return '<div class="pills">' +
      chip("orange", "P", "Passed " + m.passed + "/" + m.seen) +
      chip("pink", "F", "Fill " + m.fills) +
      chip("red", "S", "Stop −" + esc(stop) + "%") +
      chip("green", "W", "Wins " + winText) +
      '<span class="pill"><i class="mark sol">◎</i>SOL</span></div>';
  }

  function chip(color, letter, label) {
    return '<span class="pill"><i class="mark ' + color + '">' + letter + "</i>" + esc(label) + "</span>";
  }

  function equityHtml(m) {
    var abs = Math.abs(m.equity);
    var dollars = Math.floor(abs + 1e-8);
    var cents = Math.round((abs - dollars) * 100);
    if (cents === 100) { dollars += 1; cents = 0; }
    var cent = (cents < 10 ? "0" : "") + cents;
    var sign = m.equity < 0 ? "−" : "";
    var night = m.pnl / (m.starting || 1) * 100;
    return '<div class="balrow"><div class="equity num">' + sign + "$" + dollars.toLocaleString() +
      '<span class="cents">.' + cent + "</span></div>" +
      '<span class="mult">' + m.multiple.toFixed(2) + "×</span></div>" +
      '<div class="tonight ' + cls(m.pnl) + '">' + esc(signed(m.pnl)) + " tonight" +
      '<span class="sep">' + (m.pnl >= 0 ? "▲ " : "▼ ") + esc(pct(night)) + "</span></div>" +
      '<canvas class="equity-chart" id="equity"></canvas>';
  }

  function statsHtml(m) {
    var winRate = m.exits ? ((m.wins / m.exits) * 100).toFixed(0) + "%" : "—";
    var dd = m.drawdown == null ? "—" : m.drawdown.toFixed(1) + "%";
    return '<div class="stats">' +
      stat(winRate, "Win rate") +
      stat(signed(m.realized), "Banked") +
      stat(holdLabel(m.avg), "Avg hold") +
      stat(dd, "Max drawdown") +
      "</div>";
  }

  function perfHtml() {
    var all = state && state.stats;
    if (!all) return "";
    var s = all[statsScope] || {total: {}, by_source: {}};
    var t = s.total || {};
    function wr(x) { return x == null ? "—" : Math.round(x) + "%"; }
    function sg(x) { return x == null ? "—" : signed(x); }
    var tabs = ["today", "all"].map(function (k) {
      return '<button type="button" class="pill' + (statsScope === k ? " on" : "") + '" data-scope="' + k + '">' +
        (k === "today" ? "Today" : "All time") + "</button>";
    }).join("");
    var rows = Object.keys(s.by_source || {}).map(function (k) {
      var r = s.by_source[k];
      return '<div class="srcrow"><span>' + esc(SOURCE_LABEL[k] || k) + '</span><span class="num">' + r.trades +
        '</span><span class="num">' + wr(r.win_rate) + '</span><span class="num ' + cls(r.pnl) + '">' + esc(sg(r.pnl)) +
        '</span><span class="num ' + cls(r.avg) + '">' + esc(sg(r.avg)) + "</span></div>";
    }).join("");
    return '<div class="section"><h2>Performance</h2><span class="scopes">' + tabs + "</span></div>" +
      '<div class="stats">' + stat(wr(t.win_rate), "Win rate") + stat(sg(t.pnl), "Total PnL") +
      stat(sg(t.avg), "Avg trade") + stat(String(t.trades || 0), "Trades") + "</div>" +
      '<div class="card srctable"><div class="srcrow head"><span>Source</span><span>Trades</span><span>Win</span><span>PnL</span><span>Avg</span></div>' +
      (rows || '<p class="empty">No closed trades</p>') + "</div>";
  }

  function stat(value, label) {
    return "<div><b class=\"num\">" + esc(value) + "</b><span>" + esc(label) + "</span></div>";
  }

  function icon(name) {
    var paths = {
      search: '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/>',
      shield: '<path d="M12 3 5 6v6c0 4.2 2.8 7.2 7 9 4.2-1.8 7-4.8 7-9V6z"/>',
      cross: '<circle cx="12" cy="12" r="7"/><path d="M12 8v8M8 12h8"/>',
      pulse: '<path d="M3 12h4l2-5 4 10 2-5h6"/>',
      dollar: '<path d="M12 3v18M16 7.5c0-1.6-1.8-2.5-4-2.5s-4 .9-4 2.5 1.6 2.4 4 2.8 4 1.1 4 2.7-1.8 2.5-4 2.5-4-.9-4-2.5"/>',
      home: '<path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-5v-5H10v5H5a1 1 0 0 1-1-1z"/>',
      trades: '<path d="M8 7h11M8 12h11M8 17h11"/><path d="M5 7h.01M5 12h.01M5 17h.01"/>',
      chat: '<path d="M5 16.5V7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9z"/>',
      agents: '<circle cx="9" cy="9" r="2.2"/><circle cx="16" cy="9" r="2.2"/><circle cx="12.5" cy="15" r="2.2"/>'
    };
    return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
      (paths[name] || "") + "</svg>";
  }

  function stages(m) {
    var outPct = m.seen ? ((m.seen - m.passed) / m.seen) * 100 : 0;
    var by = agentMap();
    function pip(names) {
      var bad = names.some(function (name) {
        var agent = by[name];
        return agent && agent.status !== "ok" && agent.status !== "stale";
      });
      var waiting = names.some(function (name) { return by[name] && by[name].status === "stale"; });
      if (bad) return "down";
      if (waiting) return "wait";
      return "";
    }
    var rows = [
      ["Scout", "search", String(m.seen), "pairs seen", pip(["scanner"])],
      ["Analyst", "shield", m.passed + "/" + m.seen, outPct.toFixed(1) + "% out", pip(["analyst", "auditor"])],
      ["Sniper", "cross", String(m.fills), m.fills === 1 ? "fill" : "fills", pip(["executor"])],
      ["Risk", "pulse", m.positions.length + " open", m.stops + " stopped", m.positions.length ? "wait" : pip(["risk"])],
      ["Banker", "dollar", signed(m.realized), m.green + " green", ""]
    ];
    return '<div class="relay">' + rows.map(function (row) {
      var dot = row[4] ? '<i class="pip ' + row[4] + '"></i>' : '<i class="pip"></i>';
      return '<button class="stage" type="button" data-tab="agents"><div class="bubble">' + icon(row[1]) + dot +
        '</div><div class="nm">' + row[0] + '</div><div class="val">' + esc(row[2]) +
        '</div><div class="dt">' + esc(row[3]) + "</div></button>";
    }).join("") + "</div>";
  }

  function alertHtml(m) {
    var item = m.latestOut;
    if (!item) return "";
    var text = item.text || "";
    var match = /^(\S+)\s+kill:(.+)$/.exec(text);
    var body = match ? "$" + match[1].trim() + " " + match[2].trim() + " · out" : text;
    var who = item.kind === "kill" ? "Scout" : "Desk";
    return '<button class="alert" type="button" data-tab="chat"><i></i><span>' +
      esc(who + " " + body) + "</span></button>";
  }

  function positionButton(pos) {
    var width = Math.max(6, Math.min(100, Math.abs(num(pos.pnl_pct))));
    var tone = num(pos.pnl_usd) < 0 ? "down" : "";
    var symbol = (pos.symbol || "").trim() || (pos.mint || "").slice(0, 4);
    return '<button class="pos" type="button" data-mint="' + esc(pos.mint) + '">' +
      '<div class="grow"><div class="sym">$' + esc(symbol) + '</div><div class="sub">' +
      esc(money(pos.size_usd, 0)) + " @ " + esc(px(pos.entry)) + "</div></div>" +
      '<div class="bar ' + tone + '"><i style="width:' + width + '%"></i></div>' +
      '<div class="right ' + cls(pos.pnl_usd) + '"><div>' + esc(pct(pos.pnl_pct)) +
      "</div><div class=\"sub\">" + esc(signed(pos.pnl_usd)) + "</div></div></button>";
  }

  function feedRow(item) {
    var who = "Desk";
    var kind = "desk";
    var what = item.text || "";
    var match = /^(\S+)\s+kill:(.+)$/.exec(what);
    if (item.kind === "kill" && match) {
      who = "Scout";
      kind = "scout";
      what = "$" + match[1].trim() + " " + match[2].trim() + " · out";
    } else if (item.kind === "scan") {
      who = "Scout"; kind = "scout";
    } else if (item.kind === "audit" || item.kind === "verdict") {
      who = "Analyst"; kind = "analyst";
    } else if (item.kind === "fill" || item.kind === "signal" || item.kind === "skip") {
      who = "Sniper"; kind = "sniper";
    } else if (item.kind === "exit") {
      who = "Risk"; kind = "risk";
    }
    return '<div class="feed"><span class="when">' + esc(clock(item.ts)) +
      '</span><span class="who ' + kind + '">' + who + '</span><span class="what">' +
      esc(what) + "</span></div>";
  }

  function milestone(m) {
    var scale = 6;
    var t = Math.max(0, Math.min(1, (m.multiple - 1) / scale));
    var marks = [2, 4, 6.77].map(function (mark) {
      var left = ((mark - 1) / scale) * 100;
      return '<span style="left:' + left.toFixed(2) + '%">' + mark + "×</span>";
    }).join("");
    return '<section class="mile"><div class="milehead"><b>Milestone: ' + m.nextLabel + '×</b><span class="num">' +
      m.multiple.toFixed(2) + "× " + esc(money(m.gap, 0)) + " to " + m.nextLabel +
      '×</span></div><div class="track"><i class="thumb" style="left:' + (t * 100) +
      '%"></i></div><div class="ticks">' + marks + "</div></section>";
  }

  function home() {
    var m = model();
    var positions = m.positions.length
      ? m.positions.map(positionButton).join("")
      : '<p class="empty">No open positions</p>';
    var feed = items().slice().reverse().slice(0, 4);
    var feedHtml = feed.length ? feed.map(feedRow).join("") : '<p class="empty">No activity yet</p>';
    return equityHtml(m) + statsHtml(m) + perfHtml() +
      '<div class="section"><h2>Relay</h2><span>' + m.online + " agents · " + m.inflight + " in flight</span></div>" +
      stages(m) + alertHtml(m) +
      '<div class="section"><h2>Open positions</h2><span>' + m.positions.length + " open · " +
      esc(signed(m.unreal)) + " unrealized</span></div>" + positions +
      '<div class="section"><h2>Activity</h2><span>' + m.scans + " scans · " + m.fills + " fills</span></div>" +
      feedHtml + milestone(m);
  }

  function trades() {
    var m = model();
    var open = m.positions.length ? m.positions.map(positionButton).join("") : '<p class="empty">No open positions</p>';
    var coins = m.coins.length ? m.coins.map(function (coin) {
      var symbol = (coin.symbol || "").trim() || (coin.mint || "").slice(0, 4);
      return '<button class="pos" type="button" data-mint="' + esc(coin.mint) + '">' +
        '<div class="grow"><div class="sym">$' + esc(symbol) + '</div><div class="sub">' +
        esc(coin.status || "") + (coin.reason ? " · " + esc(coin.reason) : "") + "</div></div>" +
        '<div class="right"><div>' + esc(px(coin.price)) + "</div></div></button>";
    }).join("") : '<p class="empty">No coins yet</p>';
    return '<div class="section"><h2>Trades</h2><span>' + m.positions.length + " open</span></div>" + open +
      '<div class="section"><h2>Screen</h2><span>' + m.seen + " seen</span></div>" + coins;
  }

  function chat() {
    var feed = items().slice().reverse();
    var body = feed.length ? feed.map(feedRow).join("") : '<p class="empty">No messages yet</p>';
    return '<div class="section"><h2>Chat</h2></div><p class="note">Replies happen in Telegram. This list is the desk feed.</p>' + body;
  }

  function beam(layers) {
    var by = {};
    (layers || []).forEach(function (layer) { by[layer.layer] = layer; });
    var html = '<div class="beam" aria-hidden="true">';
    for (var i = 1; i <= 8; i++) {
      var layer = by[i];
      var name = !layer ? "" : layer.status === "pass" ? "pass" : "fail";
      html += '<i class="seg ' + name + '"></i>';
    }
    return html + "</div>";
  }

  function agents() {
    var m = model();
    var by = agentMap();
    var rows = ["scanner", "auditor", "analyst", "executor", "risk", "telegram", "writer"].map(function (name) {
      var agent = by[name] || { status: "offline", detail: "" };
      return '<div class="kv"><b>' + esc(name) + "</b><span>" + esc(agent.status) +
        (agent.detail ? " · " + esc(agent.detail) : "") + "</span></div>";
    }).join("");
    var settings = state.settings || {};
    var config = SETTING_ROWS.map(function (pair) {
      var value = settings[pair[0]];
      if (pair[0] === "paused") value = value === "1" ? "Yes" : "No";
      return '<div class="kv"><b>' + esc(pair[1]) + "</b><span>" + esc(value == null ? "—" : value) + "</span></div>";
    }).join("");
    var signup = safeUrl(state.signup_url);
    return '<div class="section"><h2>Agents</h2><span>' + m.online + " online</span></div>" +
      stages(m) + beam(state.last_audit && state.last_audit.layers) +
      '<div class="card">' + rows + "</div>" +
      '<div class="section"><h2>Desk</h2><span>Read only</span></div>' +
      '<div class="card">' + config + "</div>" +
      '<button class="primary" id="invite" type="button"' + (signup ? "" : ' aria-disabled="true"') + ">Invite</button>" +
      '<p class="note">Pause, mode, and size change in Telegram. Invite uses the signup link from the snapshot.</p>';
  }

  function sheet() {
    var doc = coinDoc || {};
    var coin = ((state && state.coins) || []).filter(function (c) { return c.mint === openMint; })[0] || {};
    var pos = doc.position || ((state && state.positions) || []).filter(function (p) { return p.mint === openMint; })[0];
    var symbol = (doc.symbol || coin.symbol || "").trim() || openMint.slice(0, 4);
    var url = safeUrl(doc.url || coin.url || (pos && pos.url));
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
        esc(layer.status) + (layer.detail ? " · " + esc(layer.detail) : "") + "</span></div>";
    }).join("");
    var price = coin.price != null ? coin.price : (pos && pos.price);
    var link = url
      ? '<a class="primary" id="buy" href="' + esc(url) + '" rel="noreferrer noopener">Buy on FOMO</a>'
      : '<span class="primary" aria-disabled="true">Buy on FOMO</span>';
    return '<div class="sheet" id="sheet"><button class="back" id="back" type="button">Back</button>' +
      "<h2>$" + esc(symbol) + "</h2>" +
      '<div class="sub">' + esc(doc.name || coin.name || "") + " · " + esc(doc.status || coin.status || "") + "</div>" +
      '<div class="price-lg">' + esc(px(price)) + "</div>" +
      '<canvas class="candles" id="candles"></canvas>' +
      '<div class="chips">' + chips + "</div>" +
      (pos ? '<div class="card"><div class="kv"><b>Entry</b><span>' + esc(px(pos.entry)) + "</span></div>" +
        '<div class="kv"><b>Stop</b><span>' + esc(px(pos.stop)) + "</span></div>" +
        '<div class="kv"><b>PnL</b><span class="' + cls(pos.pnl_usd) + '">' + esc(signed(pos.pnl_usd)) + "</span></div></div>" : "") +
      (layers ? '<div class="card" style="margin-top:12px">' + layers + "</div>" : '<p class="empty">No audit yet</p>') +
      link + "</div>";
  }

  function tabs() {
    var link = deskLink();
    var names = [["home", "Home", "home"], ["trades", "Trades", "trades"], ["chat", "Chat", "chat"], ["agents", "Agents", "agents"]];
    var html = '<nav class="tabs">';
    html += tabButton(names[0]) + tabButton(names[1]);
    html += link
      ? '<a class="withdraw" id="withdraw" href="' + esc(link) + '" rel="noreferrer noopener">Withdraw</a>'
      : '<span class="withdraw" aria-disabled="true">Withdraw</span>';
    html += tabButton(names[2]) + tabButton(names[3]);
    return html + "</nav>";
  }

  function tabButton(item) {
    return '<button type="button" data-tab="' + item[0] + '" class="' + (tab === item[0] ? "on" : "") + '">' +
      icon(item[2]) + esc(item[1]) + "</button>";
  }

  function skeleton() {
    return '<div class="sk line"></div><div class="sk big"></div><div class="sk chart"></div>' +
      '<div class="sk row"></div><div class="sk row"></div><div class="sk row"></div>';
  }

  function gate(title, body) {
    return '<main class="gate">' + ghost() + "<h1>" + esc(title) + "</h1><p class=\"muted\">" + esc(body) + "</p></main>";
  }

  function bodyForTab() {
    if (tab === "trades") return trades();
    if (tab === "chat") return chat();
    if (tab === "agents") return agents();
    return home();
  }

  function paint() {
    var root = document.getElementById("app");
    var scroller = document.getElementById("scroller");
    var scroll = scroller ? scroller.scrollTop : 0;
    var html;
    if (mode === "need") {
      html = gate("Open the desk link once", "Use the link that ends with #d= . This phone stores it and the address bar is cleared.");
    } else if (mode === "error" && !state) {
      html = gate(
        errorKind === "missing" ? "Nothing at this desk path" : "Can't reach the desk",
        errorKind === "missing" ? "The process has not published this path yet, or the path is wrong." : "Retrying."
      );
    } else if (mode === "loading" && !state) {
      html = '<div class="phone">' + header() + '<div class="main" id="scroller">' + skeleton() + "</div>" + tabs() + "</div>";
    } else if (openMint) {
      html = '<div class="phone">' + sheet() + "</div>";
    } else {
      var lead = tab === "home" ? pills(model()) : "";
      html = '<div class="phone">' + header() + '<div class="main" id="scroller">' + lead + bodyForTab() + "</div>" + tabs() + "</div>";
    }
    root.innerHTML = html;
    var next = document.getElementById("scroller");
    if (next) next.scrollTop = scroll;
    draw();
    bind();
  }

  function equitySeries() {
    var m = model();
    var end = (state && state.ts) || Math.floor(Date.now() / 1000);
    return [
      { t: end - 20 * 60, p: m.equity },
      { t: end - 10 * 60, p: m.equity },
      { t: end, p: m.equity }
    ];
  }

  function seriesFor(mint) {
    if (coinDoc && coinDoc.mint === mint && coinDoc.series) return coinDoc.series;
    if (!chart || !chart.series) return [];
    return chart.series[mint] || [];
  }

  function draw() {
    if (!window.DeskChart || mode !== "ready") return;
    var equity = document.getElementById("equity");
    if (equity && tab === "home" && !openMint) DeskChart.drawLine(equity, equitySeries());
    var candles = document.getElementById("candles");
    if (candles && openMint) {
      var series = seriesFor(openMint);
      if (series.length >= 8) DeskChart.drawCandles(candles, series);
      else DeskChart.drawLine(candles, series);
    }
  }

  function bind() {
    document.querySelectorAll("[data-scope]").forEach(function (button) {
      button.addEventListener("click", function () {
        statsScope = button.getAttribute("data-scope");
        paint();
      });
    });
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (mode !== "ready") return;
        tab = button.getAttribute("data-tab");
        openMint = null;
        paint();
      });
    });
    document.querySelectorAll("[data-mint]").forEach(function (button) {
      button.addEventListener("click", function () {
        openMint = button.getAttribute("data-mint");
        coinDoc = null;
        paint();
        tick();
      });
    });
    var back = document.getElementById("back");
    if (back) back.addEventListener("click", function () { openMint = null; coinDoc = null; paint(); });
    var invite = document.getElementById("invite");
    if (invite) invite.addEventListener("click", shareInvite);
  }

  async function shareInvite() {
    var url = state && safeUrl(state.signup_url);
    if (!url) return;
    if (navigator.share) {
      try { await navigator.share({ url: url }); return; } catch (err) {}
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

  async function getJSON(file) {
    var res = await fetch("d/" + path + "/" + file + "?v=" + Date.now(), { cache: "no-store" });
    if (!res.ok) {
      var err = new Error(res.status === 404 ? "missing" : "network");
      throw err;
    }
    return res.json();
  }

  async function tick() {
    try {
      state = await getJSON("state.json");
      try { activity = await getJSON("activity.json"); } catch (err) {}
      try { chart = await getJSON("chart.json"); } catch (err) {}
      if (openMint) {
        try { coinDoc = await getJSON("coin/" + encodeURIComponent(openMint) + ".json"); } catch (err) {}
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

  function boot() {
    var params = new URLSearchParams(location.hash.replace(/^#/, ""));
    if (params.get("d")) localStorage.setItem("desk_path", params.get("d"));
    if (params.get("t")) localStorage.setItem("desk_token", params.get("t"));
    if (params.has("d") || params.has("t")) history.replaceState(null, "", location.pathname + location.search);
    path = localStorage.getItem("desk_path") || "";
    if (!PATH_RE.test(path)) {
      mode = "need";
      paint();
      return;
    }
    mode = "loading";
    paint();
    tick();
    setInterval(tick, 15000);
  }

  window.addEventListener("resize", function () { if (mode === "ready") draw(); });
  boot();
})();
