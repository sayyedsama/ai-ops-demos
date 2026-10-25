/* AI Tool Ops Desk: static demo. Reads window.OPSDESK_DATA (sample data) and window.OpsTriage. */
(function () {
  "use strict";
  var D = window.OPSDESK_DATA, T = window.OpsTriage;
  var $ = function (s, el) { return (el || document).querySelector(s); };
  var params = new URLSearchParams(location.search);
  if (params.has("shot")) document.documentElement.classList.add("shot");
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var money = function (n) { return "$" + Math.round(n).toLocaleString("en-US"); };
  var nice = function (c) { return c.replace(/_/g, " "); };
  var queue = D.requests.slice();
  var session = [];
  var counter = 110;

  /* label colors, GitLab-style scoped labels */
  var LC = { type: "#6e49cb", tool: "#0f7f8a", review: "#a3301c", sla: "#b86e00" };
  var PC = { P1: "#dd2b0e", P2: "#e2620e", P3: "#737278" };
  function label(l) {
    var i = l.indexOf("::"), k = l.slice(0, i), v = l.slice(i + 2);
    var c = k === "priority" ? PC[v] : (LC[k] || "#555");
    return '<span class="lbl" style="--lc:' + c + '"><span class="k">' + esc(k) + '</span><span class="v">' + esc(v) + "</span></span>";
  }
  var labels = function (ls) { return '<div class="labels">' + ls.map(label).join("") + "</div>"; };

  /* KPIs */
  function kpis(bump) {
    var p1 = queue.filter(function (t) { return t.priority === "P1"; }).length;
    var rev = queue.filter(function (t) { return t.needs_review; }).length;
    var seats = D.adoption.reduce(function (a, r) { return a + r.seats; }, 0);
    var act = D.adoption.reduce(function (a, r) { return a + r.active; }, 0);
    var badp = D.prompts.filter(function (p) { return p.status !== "ok"; }).length;
    var k = [
      ["Open requests", queue.length, p1 + " at P1", "#6e49cb", "queue"],
      ["Seat use, all tools", Math.round(act / seats * 100) + "%", act + " of " + seats + " seats", "#108548", "seats"],
      ["Savings found", money(D.savings.annual_savings), "per year, at renewal", "#fc6d26", "savings"],
      ["Prompts needing care", badp + " of " + D.prompts.length, "owner, version or review", "#dd2b0e", "prompts"],
      ["Sent to a human", rev, "rules were not sure", "#1f75cb", "review"]
    ];
    $("#kpis").innerHTML = k.map(function (x) {
      return '<div class="kpi' + (bump && (x[4] === "queue" || x[4] === "review") ? " bump" : "") + '" style="--c:' + x[3] + '"><span>' + x[0] + "</span><b>" + x[1] + "</b><em>" + x[2] + "</em></div>";
    }).join("");
  }

  /* queue */
  var filter = "all";
  var FILTERS = [["all", "All"], ["P1", "P1"], ["review", "Needs a human"]].concat(
    Object.keys(D.rules.queues).map(function (c) { return [c, nice(c)]; }));
  function match(t, f) { return f === "all" || (f === "P1" && t.priority === "P1") || (f === "review" && t.needs_review) || t.category === f; }
  function drawFilters() {
    $("#filters").innerHTML = FILTERS.map(function (f) {
      var n = queue.filter(function (t) { return match(t, f[0]); }).length;
      if (!n && f[0] !== "all" && f[0] !== "P1") return "";
      return '<button data-f="' + f[0] + '" aria-pressed="' + (filter === f[0]) + '">' + esc(f[1]) + "<span>" + n + "</span></button>";
    }).join("");
  }
  function sorted() {
    return queue.slice().sort(function (a, b) { return a.priority < b.priority ? -1 : a.priority > b.priority ? 1 : (a.created < b.created ? -1 : a.created > b.created ? 1 : 0); });
  }
  function drawQueue(newId) {
    drawFilters();
    $("#queue-list").innerHTML = sorted().filter(function (t) { return match(t, filter); }).map(function (t) {
      return '<li class="' + (t.id === newId ? "new" : "") + '"><div><div class="qid">' + esc(t.id) + '</div><span class="pri ' + t.priority + '">' + t.priority + "</span></div>" +
        '<div><div class="qtext">' + esc(t.summary) + '</div><div class="qsub">' + esc(t.requester_team) + " · opened " + esc(t.created) + "</div>" + labels(t.labels) + "</div>" +
        '<div class="qroute"><small>Routed to</small><b>' + esc(t.queue) + "</b><small>DRI: " + esc(t.dri) + "</small>" + (t.needs_review ? '<span class="flag">needs a human look</span>' : "") + "</div></li>";
    }).join("") || '<li><div></div><div class="qsub">Nothing here.</div><div></div></li>';
  }
  $("#filters").addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return; filter = b.dataset.f; drawQueue();
  });

  /* inventory + savings */
  function drawInventory() {
    var inv = {}; D.inventory.forEach(function (r) { inv[r.tool] = r; });
    var color = { healthy: "#108548", watch: "#d38b00", underused: "#dd2b0e" };
    $("#inv").innerHTML = "<thead><tr><th>Tool</th><th>Active / seats</th><th>Seat use</th><th class='num hide-s'>Per seat / mo</th><th>Status</th></tr></thead><tbody>" +
      D.adoption.slice().sort(function (a, b) { return b.utilization - a.utilization; }).map(function (a) {
        var r = inv[a.tool];
        return '<tr><td class="tool"><b>' + esc(a.tool) + "</b><small>" + esc(r.capability) + " · " + esc(r.owner) + "</small></td>" +
          '<td class="num" style="text-align:left">' + a.active + " / " + a.seats + "</td>" +
          '<td><div class="meter" style="--mc:' + color[a.flag] + '"><span data-w="' + Math.round(a.utilization * 100) + '"></span></div><span class="pct">' + Math.round(a.utilization * 100) + "%</span></td>" +
          '<td class="num hide-s">' + money(+r.cost_per_seat_month) + '</td><td><span class="tag ' + a.flag + '">' + a.flag + "</span></td></tr>";
      }).join("") + "</tbody>";
    var s = D.savings;
    $("#savings").innerHTML = '<div class="pct">Potential savings, sample pricing</div><div class="big" id="sav-num">' + money(s.annual_savings) + '</div><div class="of">per year, out of ' + money(s.annual_spend) + " annual AI tool spend (" + Math.round(s.annual_savings / s.annual_spend * 100) + "%)</div>" +
      s.moves.map(function (m) {
        return m.kind === "consolidate"
          ? '<div class="move"><div class="h">Consolidate ' + esc(m.capability) + "<span>+" + money(m.annual) + "/yr</span></div><p>" + esc(m.drop) + " and " + esc(m.keep) + " do the same job. Keep " + esc(m.keep) + " (more active users, enough spare seats), move people over at renewal.</p><span class='door'>two-way door</span></div>"
          : '<div class="move"><div class="h">Right-size ' + esc(m.tool) + "<span>+" + money(m.annual) + "/yr</span></div><p>" + m.from + " seats, only " + m.active + " active. Drop to " + m.to + " (25% headroom) and re-check after the next enablement push.</p><span class='door'>two-way door</span></div>";
      }).join("");
  }

  /* adoption chart, hand-rolled SVG */
  var PAL = ["#6e49cb", "#fc6d26", "#108548", "#1f75cb", "#c0399f", "#0f7f8a", "#9a6b00"];
  function drawChart() {
    var tools = Object.keys(D.history), W = 760, H = 320, L = 40, R = 70, Tp = 16, B = 30;
    var weeks = D.history[tools[0]].map(function (p) { return p.week; });
    var max = Math.ceil(Math.max.apply(null, tools.map(function (t) { return Math.max.apply(null, D.history[t].map(function (p) { return p.active; })); })) / 20) * 20;
    var x = function (i) { return L + i * (W - L - R) / (weeks.length - 1); };
    var y = function (v) { return Tp + (H - Tp - B) * (1 - v / max); };
    var g = "";
    for (var v = 0; v <= max; v += 20) g += '<line class="gridl" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '"/><text x="' + (L - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + v + "</text>";
    weeks.forEach(function (w, i) { g += '<text x="' + x(i) + '" y="' + (H - 8) + '" text-anchor="middle">' + w.slice(5).replace("-", "/") + "</text>"; });
    tools.forEach(function (t, k) {
      var pts = D.history[t].map(function (p, i) { return [x(i), y(p.active)]; });
      var len = 0; for (var i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      var c = PAL[k % PAL.length];
      g += '<g data-t="' + esc(t) + '"><path class="ln" style="--len:' + Math.ceil(len) + '" stroke="' + c + '" d="M' + pts.map(function (p) { return p.join(","); }).join("L") + '"/>' +
        pts.map(function (p, i) { return '<circle class="pt" cx="' + p[0] + '" cy="' + p[1] + '" r="3.5" fill="#fff" stroke="' + c + '" stroke-width="2"><title>' + esc(t) + ", week of " + weeks[i] + ": " + D.history[t][i].active + " active</title></circle>"; }).join("") +
        '<text class="end" data-y="' + pts[pts.length - 1][1] + '" x="' + (pts[pts.length - 1][0] + 8) + '" y="' + (pts[pts.length - 1][1] + 4) + '" style="fill:' + c + ';font-weight:600">' + D.history[t][pts.length - 1].active + "</text></g>";
    });
    $("#chart").innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Weekly active users per tool over four weeks">' + g + "</svg>";
    /* keep end-of-line numbers from overlapping */
    var ends = Array.prototype.slice.call($("#chart").querySelectorAll("text.end")).sort(function (a, b) { return a.dataset.y - b.dataset.y; });
    var last = -1e9; ends.forEach(function (el) { var yy = Math.max(+el.dataset.y + 4, last + 12); el.setAttribute("y", yy); last = yy; });
    var trend = {}; D.adoption.forEach(function (a) { trend[a.tool] = a.trend; });
    $("#legend").innerHTML = tools.map(function (t, k) {
      var tr = trend[t];
      return '<button data-t="' + esc(t) + '"><i style="background:' + PAL[k % PAL.length] + '"></i>' + esc(t) + '<em class="' + (tr >= 0 ? "up" : "down") + '">' + (tr >= 0 ? "+" : "") + tr + "</em></button>";
    }).join("");
    var chart = $("#chart"), lock = null;
    function focus(t) {
      chart.classList.toggle("focus", !!t);
      chart.querySelectorAll("g[data-t]").forEach(function (gr) { gr.querySelectorAll("*").forEach(function (el) { el.classList.toggle("on", gr.dataset.t === t); }); });
      document.querySelectorAll("#legend button").forEach(function (b) { b.classList.toggle("on", b.dataset.t === t); });
    }
    $("#legend").addEventListener("mouseover", function (e) { var b = e.target.closest("button"); if (b && !lock) focus(b.dataset.t); });
    $("#legend").addEventListener("mouseleave", function () { focus(lock); });
    $("#legend").addEventListener("click", function (e) { var b = e.target.closest("button"); if (!b) return; lock = lock === b.dataset.t ? null : b.dataset.t; focus(lock); });
  }

  /* prompts */
  function drawPrompts() {
    $("#prompt-cards").innerHTML = D.prompts.map(function (p) {
      var ok = p.status === "ok", age = p.age_days, pct = age == null ? 100 : Math.min(100, age / 90 * 100);
      var col = age == null || age > 90 ? "#dd2b0e" : age > 60 ? "#d38b00" : "#108548";
      return '<div class="panel pc"><span class="st ' + (ok ? "ok" : "bad") + '">' + (ok ? "healthy" : esc(p.status)) + "</span><h4>" + esc(p.name) + '</h4><div class="plat">' + esc(p.platform) + "</div>" +
        "<dl><dt>Owner</dt><dd" + (p.owner ? "" : ' class="miss"') + ">" + (esc(p.owner) || "none") + "</dd><dt>Version</dt><dd" + (p.version ? "" : ' class="miss"') + ">" + (p.version ? "v" + esc(p.version) : "none") + "</dd></dl>" +
        '<div class="fresh"><small><span>Last review</span><span>' + (age == null ? "never" : age + " days ago") + '</span></small><div class="meter" style="--mc:' + col + '"><span data-w="' + Math.round(pct) + '"></span></div></div></div>';
    }).join("");
  }

  /* tiny markdown renderer for the weekly update */
  function inline(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>").replace(/(^|\s)_(.+?)_(?=\s|$)/g, "$1<i>$2</i>"); }
  function md(src) {
    var out = [], lines = src.split("\n"), i = 0;
    while (i < lines.length) {
      var l = lines[i];
      if (/^#{1,3} /.test(l)) { var n = l.match(/^#+/)[0].length; out.push("<h" + n + ">" + inline(l.slice(n + 1)) + "</h" + n + ">"); i++; }
      else if (/^\|/.test(l)) {
        var rows = []; while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
        var cells = function (r) { return r.replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        out.push("<table><thead><tr>" + cells(rows[0]).map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>" +
          rows.slice(2).map(function (r) { return "<tr>" + cells(r).map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table>");
      } else if (/^(- |\d+\. )/.test(l)) {
        var ol = /^\d/.test(l), items = [];
        while (i < lines.length && /^(- |\d+\. )/.test(lines[i])) items.push(lines[i++].replace(/^(- |\d+\. )/, ""));
        out.push((ol ? "<ol>" : "<ul>") + items.map(function (x) { return "<li>" + inline(x) + "</li>"; }).join("") + (ol ? "</ol>" : "</ul>"));
      } else if (/^> /.test(l)) { out.push("<blockquote>" + inline(l.slice(2)) + "</blockquote>"); i++; }
      else if (l.trim()) { out.push("<p>" + inline(l) + "</p>"); i++; }
      else i++;
    }
    return out.join("");
  }
  function updateText() {
    if (!session.length) return D.async_update;
    var add = ["## New this session (live)", ""].concat(session.map(function (t) { return "- **" + t.id + "** " + t.priority + " " + nice(t.category) + ": " + t.summary + " Routed to " + t.queue + ", DRI " + t.dri + "."; }), [""]);
    var lines = D.async_update.split("\n"), at = lines.indexOf("## Decisions needed (async, comment by Friday)");
    lines.splice(at < 0 ? 2 : at, 0, add.join("\n"));
    return lines.join("\n");
  }
  function drawDoc() { $("#doc").innerHTML = md(updateText()); }
  $("#copy").addEventListener("click", function () {
    var b = this, txt = updateText();
    (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(function () { b.textContent = "Copied"; }, function () { b.textContent = "Select and copy below"; });
    setTimeout(function () { b.textContent = "Copy markdown"; }, 1800);
  });

  /* try it */
  var EX = [
    "Glean is down and it's blocking my customer call today",
    "Can I get a Claude seat? I start on the Acme deal Monday.",
    "Is it OK to paste customer data from Gong into Claude for a QBR?",
    "Idea: could we automate renewal briefs with a Relevance agent?",
    "How do I write a good prompt for call summaries? Email me at sam@example.com"
  ];
  var EXL = ["Tool down before a call", "Seat request", "Customer data question", "Agent idea", "How-to, with an email"];
  $("#examples").innerHTML = EXL.map(function (e, i) { return '<button type="button" class="chip" data-i="' + i + '" title="' + esc(EX[i]) + '">' + esc(e) + "</button>"; }).join("");
  $("#examples").addEventListener("click", function (e) { var b = e.target.closest(".chip"); if (!b) return; $("#msg").value = EX[b.dataset.i]; run(); });

  var timer = [];
  function run(e) {
    if (e) e.preventDefault();
    var text = $("#msg").value.trim();
    if (!text) { $("#msg").focus(); return; }
    timer.forEach(clearTimeout); timer = [];
    var id = "REQ-" + (++counter);
    var t = T.triage({ id: id, created: D.today, team: $("#team").value, text: text }, D);
    var r = T.route(t, D); t.queue = r.queue; t.dri = r.dri; t.reply = r.reply;
    var steps = document.querySelectorAll("#pipe li"), fast = params.has("shot") || matchMedia("(prefers-reduced-motion: reduce)").matches;
    steps.forEach(function (s) { s.classList.remove("on"); });
    $("#outcome").className = "outcome"; $("#outcome").innerHTML = "";
    steps.forEach(function (s, i) { timer.push(setTimeout(function () { s.classList.add("on"); }, fast ? 0 : 140 * (i + 1))); });
    timer.push(setTimeout(function () { show(t); }, fast ? 0 : 140 * (steps.length + 1)));
  }
  function show(t) {
    var hits = Object.keys(t.scores).filter(function (c) { return t.scores[c] > 0; }).map(function (c) { return "<code>" + nice(c) + " " + t.scores[c] + "</code>"; }).join(" ");
    var why = "Rule hits: " + (hits || "<code>none</code>") + ". " + (t.urgent ? "Urgency words found. " : "") +
      (t.tools.length ? "Tools spotted: " + t.tools.map(esc).join(", ") + ". " : "No known tool named, so a person double-checks. ") +
      (t.redacted !== t.text ? "Email address redacted before anything is stored. " : "");
    var row = [D.today, t.id, "form", esc(t.requester_team), t.category, t.priority, esc(t.queue), esc(t.summary)].join(" | ");
    $("#outcome").innerHTML = '<div class="issue"><div class="issue-h"><div class="meta">' + esc(t.id) + " · opened just now by " + esc(t.requester_team) + "</div><h3>" + esc(t.summary) + "</h3>" + labels(t.labels) + "</div>" +
      '<div class="issue-b"><div><small>Routed to</small><strong>' + esc(t.queue) + "</strong></div><div><small>DRI</small><strong>" + esc(t.dri) + "</strong></div>" +
      "<div><small>Priority</small><strong>" + t.priority + (t.priority === "P1" ? " · page the owner" : "") + "</strong></div><div><small>Human check</small><strong>" + (t.needs_review ? "Yes, flagged" : "Not needed") + "</strong></div></div>" +
      '<div class="why">' + why + "</div>" +
      '<div class="notif"><div class="av">O</div><p><b>Ops Desk</b><span class="bot">BOT</span><br>' + esc(t.reply) + "</p></div>" +
      '<div class="sheet" title="Row appended to the tracking sheet">sheet.csv +1 row: ' + row + "</div></div>";
    queue.push(t); session.push(t); filter = "all";
    kpis(true); drawQueue(t.id); drawDoc();
  }
  $("#intake").addEventListener("submit", run);

  /* boot */
  kpis(); drawQueue(); drawInventory(); drawChart(); drawPrompts(); drawDoc();
  requestAnimationFrame(function () { setTimeout(function () { document.querySelectorAll(".meter span").forEach(function (s) { s.style.width = s.dataset.w + "%"; }); }, 60); });
  if (params.has("demo")) { $("#msg").value = params.get("demo") || EX[0]; run(); }
})();
