/* Browser port of opsdesk.triage_rules() and opsdesk.route().
   The regex rules are loaded from data.js, which is exported from the Python file,
   so both sides share one rulebook. A test runs this file in Node and compares results. */
(function (root) {
  "use strict";
  var EMAIL = /[\w.+-]+@[\w-]+\.[\w.]+/g;
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function score(text, pats) { return pats.filter(function (p) { return new RegExp(p, "i").test(text); }).length; }
  function redact(text) { return text.replace(EMAIL, "[email]"); }
  function summarize(text, limit) {
    limit = limit || 90;
    var first = text.trim().split(/(?<=[.!?])\s/)[0];
    return first.length <= limit ? first : first.slice(0, limit - 3).replace(/\s+$/, "") + "...";
  }
  function detectTools(text, inventory) {
    var low = text.toLowerCase();
    return inventory.filter(function (row) {
      var names = [row.tool].concat((row.aliases || "").split("|").filter(Boolean));
      return names.some(function (n) { return new RegExp("\\b" + esc(n.toLowerCase()) + "\\b").test(low); });
    }).map(function (r) { return r.tool; });
  }
  function days(a, b) { return Math.round((Date.parse(b) - Date.parse(a)) / 86400000); }

  function triage(req, data) {
    var rules = data.rules, text = req.text, steps = [];
    var t = { id: req.id, created: req.created, requester_team: req.team, text: text,
              category: "other", priority: "P3", tools: [], labels: [], summary: "", needs_review: false, engine: "rules" };
    var best = null, bestScore = 0, scores = {};
    Object.keys(rules.categories).forEach(function (c) {
      var s = score(text, rules.categories[c]); scores[c] = s;
      if (best === null || s > bestScore) { best = c; bestScore = s; }
    });
    t.category = bestScore > 0 ? best : "other";
    t.tools = detectTools(text, data.inventory);
    var urgent = score(text, rules.urgent) > 0;
    var age = days(t.created, data.today);
    if (t.category === "issue" && urgent) t.priority = "P1";
    else if (urgent || (t.category === "access_request" && age >= 2) || t.category === "issue" || t.category === "governance") t.priority = "P2";
    t.labels = ["type::" + t.category, "priority::" + t.priority].concat(t.tools.map(function (x) { return "tool::" + x; }));
    if (score(text, rules.sensitive)) { t.labels.push("review::data-governance"); t.needs_review = true; }
    if (age >= 3) t.labels.push("sla::aging");
    if (t.category === "other" || !t.tools.length) t.needs_review = true;
    t.summary = summarize(redact(text));
    t.redacted = redact(text);
    t.scores = scores;
    t.urgent = urgent;
    return t;
  }

  function route(t, data) {
    var owners = {}, free = {}, q = data.rules.queues;
    data.inventory.forEach(function (r) { owners[r.tool] = r.owner; });
    (data.adoption || []).forEach(function (a) { free[a.tool] = a.seats - a.active; });
    var tool = t.tools[0] || null;
    var dri = tool ? (owners[tool] || "Ops triage") : "Ops triage", queue, reply;
    if (t.category === "governance" || t.labels.indexOf("review::data-governance") >= 0) {
      queue = q.governance; dri = "Privacy + Enterprise AI";
      reply = "Thanks for checking first. Please hold off until data governance replies on this issue.";
    } else if (t.category === "access_request" && tool) {
      queue = q.access_request;
      var spare = free[tool];
      reply = spare > 0 ? "Seat request logged. " + tool + " has " + spare + " unused seats, so this is a quick approval from " + dri + "."
                        : "Seat request logged. " + tool + " is at capacity, so " + dri + " will check renewal options.";
    } else if (t.category === "issue") {
      queue = q.issue;
      reply = "Logged as " + t.priority + ". " + dri + " is on it" + (t.priority === "P1" ? " now." : " and will update this issue.");
    } else if (t.category === "use_case") {
      queue = q.use_case; dri = "Enablement";
      reply = "Great idea. It is in the use-case backlog; add the prompt or template you would start from.";
    } else if (t.category === "how_to") {
      queue = q.how_to; dri = "AI champions";
      reply = "Here is the prompt library entry and the champions channel. Someone will reply with an example.";
    } else if (t.category === "data_hygiene") {
      queue = q.data_hygiene;
      reply = "Thanks. The owner will merge duplicates and mark one source of truth.";
    } else {
      queue = q.other; dri = "Ops triage";
      reply = "Thanks. A person will take a look and ask a follow-up question if needed.";
    }
    return { queue: queue, dri: dri, reply: reply };
  }

  var api = { triage: triage, route: route, redact: redact, summarize: summarize, detectTools: detectTools };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.OpsTriage = api;
})(this);
