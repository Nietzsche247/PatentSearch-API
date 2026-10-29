{% verbatim %}
/* The triage table page (gate S1.8L): sends the form to POST /api/v1/structure/triage/ with the visitor's key, draws the
   answer, keeps the have and lacks marks in this browser only, and writes the CSV and JSON export. Every text node is set
   with textContent, so no patent text is ever read as markup. */
(function () {
  "use strict";
  var API = "/api/v1/structure/triage/";
  var MARKS_STORE = "patentref.triage.marks.v1";
  var POS_TEXT = {every_independent: "Every independent claim", some_independent: "Some independent claims",
                  only_dependent: "Dependent claims only", absent: "Not in the claims"};
  var POS_ORDER = {every_independent: 0, some_independent: 1, only_dependent: 2, absent: 3};
  var STATUS_TEXT = {active: "Active", expired_term: "Expired: term ended", expired_fee_nonpayment: "Expired: fees not paid",
                     withdrawn: "Withdrawn", unknown: "Unknown"};
  var STATUS_ORDER = {expired_fee_nonpayment: 0, expired_term: 1, withdrawn: 2, unknown: 3, active: 4};
  var NOTE_TEXT = {
    not_in_data: "The data in service has no patent with this number.",
    no_claim_text: "The long-text file in service has no claims for this patent (granted after its last grant date).",
    work_cap: "Its claims are over the claim tree's work cap (1,000 claim rows, 1,000,000 characters).",
    offset_check: "Its claim tree failed its offset check, so nothing of it is shown.",
    time_limit: "Not read: the table reached its time limit. Send this patent again."
  };
  var PART_TEXT = {preamble: "Preamble", transition: "Transition", element: "Element", wherein_clause: "Wherein clause"};
  var MARKABLE = {preamble: true, element: true, wherein_clause: true};

  var current = null;
  var openPid = null;
  var order = "given";
  var marks = readMarks();

  function byId(id) { return document.getElementById(id); }

  function node(tag, props, kids) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) { return; }
      if (k === "text") { e.textContent = v; } else if (k === "cls") { e.className = v; } else { e.setAttribute(k, v === true ? "" : String(v)); }
    });
    (kids || []).forEach(function (kid) {
      if (kid !== null && kid !== undefined) { e.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid); }
    });
    return e;
  }

  function empty(e) { while (e.firstChild) { e.removeChild(e.firstChild); } }

  function readMarks() {
    try {
      var v = JSON.parse(window.localStorage.getItem(MARKS_STORE) || "{}");
      return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch (e) { return {}; }
  }

  function keepMarks() {
    try { window.localStorage.setItem(MARKS_STORE, JSON.stringify(marks)); } catch (e) { /* no storage here: the marks last while the page is open */ }
  }

  function say(statusText, alertText) {
    byId("tt-status").textContent = statusText || "";
    byId("tt-alert").textContent = alertText || "";
  }

  /* ---------------------------------------------------------------- the export (the API's columns and its CSV rule) */

  function csvField(v) {
    if (v === null || v === undefined) { return ""; }
    if (v === true) { return "true"; }
    if (v === false) { return "false"; }
    var s = Array.isArray(v) ? v.map(function (x) { return x === null || x === undefined ? "" : String(x); }).join(";") : String(v);
    return /[",\r\n]/.test(s) ? "\"" + s.replace(/"/g, "\"\"") + "\"" : s;
  }

  function exportRows(t, m) {
    var rows = [];
    t.patents.forEach(function (p) {
      var st = p.status || {};
      var base = {patent_id: p.patent_id, title: p.title, status: st.status, status_basis: st.basis,
                  reinstatement_possible: st.reinstatement_possible, as_of: st.as_of, note: p.note};
      rows.push(Object.assign({}, base, {row_kind: "patent"}));
      p.cells.forEach(function (c) {
        var q = c.quote || {};
        rows.push(Object.assign({}, base, {row_kind: "component", component: c.key, position: c.position, match: c.match,
          claim: q.claim, claim_id: q.claim_id, part_id: q.term_id, quote: q.text, start: q.start, end: q.end}));
      });
      p.claim_parts.forEach(function (cl) {
        cl.parts.forEach(function (part) {
          rows.push(Object.assign({}, base, {row_kind: part.part, claim: part.claim, claim_id: part.claim_id, part_id: part.part_id,
            label: part.label, quote: part.text, start: part.start, end: part.end, mark: m[part.part_id]}));
        });
      });
    });
    return rows;
  }

  function toCsv(t, m) {
    var cols = t.export_columns;
    var lines = [cols.map(csvField).join(",")];
    exportRows(t, m).forEach(function (r) { lines.push(cols.map(function (c) { return csvField(r[c]); }).join(",")); });
    return lines.join("\r\n") + "\r\n";
  }

  function marksOf(t, m) {
    var own = {};
    t.patents.forEach(function (p) {
      p.claim_parts.forEach(function (cl) {
        cl.parts.forEach(function (part) { if (m[part.part_id]) { own[part.part_id] = m[part.part_id]; } });
      });
    });
    return own;
  }

  function exportJson(t, m) {
    return {"export": "PatentRef triage table", "exported_at": new Date().toISOString(), "marks": marksOf(t, m), "table": t};
  }

  function save(name, type, text) {
    var url = URL.createObjectURL(new Blob([text], {type: type}));
    var a = node("a", {href: url, download: name});
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
  }

  /* ---------------------------------------------------------------- the table */

  function yesNo(v) { return v === true ? "yes" : (v === false ? "no" : String(v)); }

  function shortList(a) { return a.length > 8 ? a.slice(0, 8).join(", ") + " and " + (a.length - 8) + " more" : a.join(", "); }

  function statusBits(p) {
    var s = p.status;
    if (!s) { return [node("span", {text: p.found ? "Not available" : "Not in the data"})]; }
    var bits = [node("strong", {text: STATUS_TEXT[s.status] || s.status})];
    if (s.basis && s.basis.length) { bits.push(node("span", {cls: "small", text: "Basis: " + (Array.isArray(s.basis) ? s.basis.join(", ") : s.basis)})); }
    if (s.expiry_date_estimated) { bits.push(node("span", {cls: "small", text: "Term end, computed: " + s.expiry_date_estimated})); }
    if (s.lapse_date) { bits.push(node("span", {cls: "small", text: "Fee lapse: " + s.lapse_date})); }
    if (s.reinstated_date) { bits.push(node("span", {cls: "small", text: "Reinstated: " + s.reinstated_date})); }
    bits.push(node("span", {cls: "small", text: "Reinstatement possible: " + yesNo(s.reinstatement_possible)}));
    return bits;
  }

  function cellBits(c) {
    var bits = [node("span", {cls: "pos-label", text: POS_TEXT[c.position] || c.position})];
    var where = [];
    if (c.independent_claims.length) { where.push("independent " + shortList(c.independent_claims)); }
    if (c.dependent_claims.length) { where.push("dependent " + shortList(c.dependent_claims)); }
    if (where.length) { bits.push(node("span", {cls: "small", text: "Claims: " + where.join("; ")})); }
    if (c.match === "partial") { bits.push(node("span", {cls: "small", text: "Partial: " + c.claim_keys.join(", ")})); }
    return bits;
  }

  function markable(p) {
    var n = 0;
    p.claim_parts.forEach(function (cl) { cl.parts.forEach(function (part) { if (MARKABLE[part.part]) { n += 1; } }); });
    return n;
  }

  function markCount(p) {
    var have = 0, lacks = 0;
    p.claim_parts.forEach(function (cl) {
      cl.parts.forEach(function (part) {
        if (marks[part.part_id] === "have") { have += 1; }
        if (marks[part.part_id] === "lacks") { lacks += 1; }
      });
    });
    var n = markable(p);
    return have + lacks ? have + " have, " + lacks + " lacks, of " + n + " parts" : "No marks yet, " + n + " parts";
  }

  function rank(p) { return p.status && STATUS_ORDER[p.status.status] !== undefined ? STATUS_ORDER[p.status.status] : 9; }

  function cpos(p, i) { return p.built && p.cells[i] ? POS_ORDER[p.cells[i].position] : 9; }

  function inOrder(t) {
    var list = t.patents.slice();
    if (order === "status") {
      list.sort(function (a, b) { return rank(a) - rank(b) || a.row - b.row; });
    } else if (order === "date") {
      list.sort(function (a, b) { return (b.patent_date || "").localeCompare(a.patent_date || "") || a.row - b.row; });
    } else if (order.charAt(0) === "c") {
      var i = parseInt(order.slice(1), 10);
      list.sort(function (a, b) { return cpos(a, i) - cpos(b, i) || a.row - b.row; });
    }
    return list;
  }

  function orderOptions(t) {
    var sel = byId("tt-order");
    empty(sel);
    sel.appendChild(node("option", {value: "given", text: "The order given"}));
    sel.appendChild(node("option", {value: "status", text: "Status, expired first"}));
    sel.appendChild(node("option", {value: "date", text: "Grant date, newest first"}));
    t.components.forEach(function (c, i) {
      sel.appendChild(node("option", {value: "c" + i, text: c.key + ": every independent claim first"}));
    });
    order = "given";
    sel.value = order;
  }

  function row(t, p) {
    var tr = node("tr", {id: "tt-row-" + p.patent_id});
    tr.appendChild(node("th", {}, [node("a", {cls: "pid", href: p.permalink, text: "US " + p.patent_id}),
                                   p.title ? node("span", {cls: "small", text: p.title}) : null]));
    tr.appendChild(node("td", {}, statusBits(p)));
    if (p.built) {
      p.cells.forEach(function (c) { tr.appendChild(node("td", {cls: "pos-" + c.position}, cellBits(c))); });
    } else if (t.components.length) {
      tr.appendChild(node("td", {colspan: t.components.length, text: NOTE_TEXT[p.note] || ""}));
    }
    tr.appendChild(node("td", {text: p.claims ? p.claims.total + " claims, " + p.claims.independent.length + " independent" : ""}));
    var last = node("td");
    if (p.built) {
      last.appendChild(node("button", {type: "button", cls: "quiet", "aria-controls": "tt-panel", "aria-expanded": p.patent_id === openPid ? "true" : "false",
                                       "aria-label": "Open the parts of US " + p.patent_id, "data-pid": p.patent_id, text: "Open"}));
      last.appendChild(node("span", {cls: "small", id: "tt-count-" + p.patent_id, text: markCount(p)}));
    } else if (!t.components.length) {
      last.appendChild(node("span", {cls: "small", text: NOTE_TEXT[p.note] || ""}));
    }
    tr.appendChild(last);
    return tr;
  }

  function drawTable(t) {
    var tbl = byId("tt-table");
    empty(tbl);
    tbl.appendChild(node("caption", {id: "tt-caption", text: "The status of " + t.patents.length + " patent" + (t.patents.length === 1 ? "" : "s") +
      " and where their claims mention each component. Scroll sideways for every column."}));
    var head = node("tr", {}, [node("th", {text: "Patent"}), node("th", {text: "Status"})]);
    t.components.forEach(function (c) {
      head.appendChild(node("th", {}, [c.key, node("span", {cls: "small", text: c.from === "table" ? "picked by the table" : "from your list"})]));
    });
    head.appendChild(node("th", {text: "Claims"}));
    head.appendChild(node("th", {text: "Parts"}));
    tbl.appendChild(node("thead", {}, [head]));
    var body = node("tbody");
    inOrder(t).forEach(function (p) { body.appendChild(row(t, p)); });
    tbl.appendChild(body);
  }

  function summary(t) {
    var c = t.counts, s = [], inp = t.input;
    s.push(c.patents + " patent" + (c.patents === 1 ? "" : "s") + " in the table, " + c.built + " with a claim tree.");
    if (c.patents - c.found) { s.push((c.patents - c.found) + " not in the data in service."); }
    if (c.not_built) { s.push(c.not_built + " without a tree; their rows say why."); }
    if (!t.complete) { s.push("The table reached its time limit of " + t.time_limit_seconds + " s: build it again for the rest."); }
    if (inp.kind === "search") {
      s.push("Search: " + inp.found + " patent" + (inp.found === 1 ? "" : "s") + " with a claim that has " +
             inp.terms.map(function (x) { return "“" + x + "”"; }).join(" and ") +
             (inp.more ? "; more patents match, and the table holds the first " + inp.limit + " in the index's order, recent grants first as a rule." : "."));
      if (!inp.complete) { s.push("The search stopped at its time limit, so the list may be short; add a word or a phrase."); }
    }
    if (inp.kind === "list" && inp.unread_count) {
      s.push(inp.unread_count + " piece" + (inp.unread_count === 1 ? "" : "s") + " of the list read as no patent number: " + inp.unread.join(", ") + ".");
    }
    if (inp.unread_components && inp.unread_components.length) { s.push("Read as no component: " + inp.unread_components.join(", ") + "."); }
    if (!t.status_available) { s.push("The status is not available on the server right now."); }
    s.push("Data version " + t.data_version + ", claims from " + t.text_data_version + ", as of " + t.as_of + ".");
    byId("tt-summary").textContent = s.join(" ");
  }

  function find(pid) {
    for (var i = 0; current && i < current.patents.length; i += 1) { if (current.patents[i].patent_id === pid) { return current.patents[i]; } }
    return null;
  }

  function partItem(p, part) {
    var label = PART_TEXT[part.part] + (part.label ? " " + part.label : "");
    var li = node("li", {cls: "part"});
    li.appendChild(node("p", {}, [node("span", {cls: "kind", text: label + ": "}), node("q", {text: part.text}),
      node("span", {cls: "small", text: "Claim " + part.claim + ", characters " + part.start + " to " + part.end})]));
    if (MARKABLE[part.part]) {
      var fs = node("fieldset", {cls: "mark"}, [node("legend", {cls: "vh", text: "Your mark for " + label.toLowerCase() + " of claim " + part.claim})]);
      var name = "m-" + part.part_id;
      [["have", "have"], ["lacks", "lacks"], ["", "no mark"]].forEach(function (o) {
        var id = name + "-" + (o[0] || "none");
        var input = node("input", {type: "radio", name: name, id: id, value: o[0], "data-part": part.part_id, "data-pid": p.patent_id});
        input.checked = (marks[part.part_id] || "") === o[0];
        fs.appendChild(node("span", {cls: "opt"}, [input, node("label", {"for": id, text: o[1]})]));
      });
      li.appendChild(fs);
    }
    return li;
  }

  function openPanel(pid, quiet) {
    var p = find(pid);
    if (!p) { return; }
    openPid = pid;
    Array.prototype.forEach.call(byId("tt-table").querySelectorAll("button[data-pid]"), function (b) {
      b.setAttribute("aria-expanded", b.getAttribute("data-pid") === pid ? "true" : "false");
    });
    var panel = byId("tt-panel");
    empty(panel);
    var h = node("h3", {id: "tt-panel-h", tabindex: "-1", text: "The parts of US " + p.patent_id + (p.title ? ": " + p.title : "")});
    panel.appendChild(h);
    panel.appendChild(node("p", {cls: "small", text: "Mark each part have or lacks as your own note. The marks stay in this browser and go into the export."}));
    panel.appendChild(node("h4", {text: "The terms its claims introduce"}));
    var dl = node("dl", {cls: "own"});
    ["every_independent", "some_independent", "only_dependent"].forEach(function (k) {
      var list = (p.components || {})[k] || [];
      dl.appendChild(node("dt", {text: POS_TEXT[k] + " (" + list.length + ")"}));
      dl.appendChild(node("dd", {text: list.length ? list.join(", ") : "none"}));
    });
    panel.appendChild(dl);
    p.claim_parts.forEach(function (cl) {
      panel.appendChild(node("h4", {text: "Claim " + cl.claim + (cl.category && cl.category !== "other" ? ", " + cl.category : "")}));
      var ol = node("ol", {cls: "parts"});
      cl.parts.forEach(function (part) { ol.appendChild(partItem(p, part)); });
      panel.appendChild(ol);
    });
    panel.appendChild(node("button", {type: "button", cls: "quiet", "data-back": pid, text: "Back to the row of US " + pid}));
    panel.hidden = false;
    if (!quiet) { h.focus(); }
  }

  function show(t, quiet) {
    current = t;
    byId("tt-result").hidden = false;
    summary(t);
    orderOptions(t);
    drawTable(t);
    var panel = byId("tt-panel");
    empty(panel);
    panel.hidden = true;
    openPid = null;
    if (!quiet) { byId("tt-result-h").focus(); }
  }

  /* ---------------------------------------------------------------- the request */

  function seconds(t0) { return ((Date.now() - t0) / 1000).toFixed(1); }

  function problem(code, reason, wait) {
    if (code === 400) { return "The API could not read the request: " + reason; }
    if (code === 403) { return "The API did not take this key. Check it, or get a free key at /api/v1/meta/signup/."; }
    if (code === 429) { return "This key is over its allowance for now. Try again in " + wait + " s."; }
    return "The API could not build the table (status " + code + ")." + (reason ? " " + reason : "") + " Try again in a minute.";
  }

  function send(key, payload, tries) {
    var go = byId("tt-go");
    go.disabled = true;
    say(tries ? "Trying again." : "Building the table. A table of 500 patents can take up to 20 seconds.", "");
    var t0 = Date.now();
    fetch(API, {method: "POST", credentials: "omit", cache: "no-store",
                headers: {"Content-Type": "application/json", "Accept": "application/json", "X-Api-Key": key},
                body: JSON.stringify(payload)})
      .then(function (r) {
        var reason = r.headers.get("X-Status-Reason") || "";
        var wait = parseInt(r.headers.get("Retry-After") || "0", 10) || 2;
        if (r.ok) {
          return r.json().then(function (t) { go.disabled = false; show(t, false); say("The table is ready, in " + seconds(t0) + " s.", ""); });
        }
        if (r.status === 429 && reason && tries < 3) {   /* the server is building other tables: wait for a turn */
          say("The server is building other tables. Trying again in " + wait + " s.", "");
          window.setTimeout(function () { send(key, payload, tries + 1); }, wait * 1000);
          return null;
        }
        go.disabled = false;
        say("", problem(r.status, reason, wait));
        return null;
      })
      .catch(function () { go.disabled = false; say("", "The request did not reach the API. Check the connection and try again."); });
  }

  function by() { return byId("tt-by-search").checked ? "search" : "list"; }

  function fields() {
    var s = by() === "search";
    byId("tt-search-field").hidden = !s;
    byId("tt-list-field").hidden = s;
  }

  /* ---------------------------------------------------------------- wiring */

  byId("tt-by-list").addEventListener("change", fields);
  byId("tt-by-search").addEventListener("change", fields);

  byId("tt-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var key = byId("tt-key").value.trim();
    if (!key) { say("", "Enter your API key first. A free key is at /api/v1/meta/signup/."); byId("tt-key").focus(); return; }
    var payload = {};
    if (by() === "search") {
      var words = byId("tt-search").value.trim();
      if (!words) { say("", "Enter the words to search for."); byId("tt-search").focus(); return; }
      payload.search = words;
      payload.limit = parseInt(byId("tt-limit").value, 10);
    } else {
      var list = byId("tt-list").value;
      if (!list.trim()) { say("", "Enter at least one patent number."); byId("tt-list").focus(); return; }
      payload.patents = list;
    }
    var comps = byId("tt-components").value.trim();
    if (comps) { payload.components = comps; }
    send(key, payload, 0);
  });

  byId("tt-order").addEventListener("change", function (ev) {
    order = ev.target.value;
    if (current) { drawTable(current); }
  });

  byId("tt-table").addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("button[data-pid]") : null;
    if (b) { openPanel(b.getAttribute("data-pid"), false); }
  });

  byId("tt-panel").addEventListener("change", function (ev) {
    var input = ev.target;
    if (!input.getAttribute || !input.getAttribute("data-part")) { return; }
    if (input.value) { marks[input.getAttribute("data-part")] = input.value; } else { delete marks[input.getAttribute("data-part")]; }
    keepMarks();
    var p = find(input.getAttribute("data-pid"));
    var count = p ? byId("tt-count-" + p.patent_id) : null;
    if (count) { count.textContent = markCount(p); }
  });

  byId("tt-panel").addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("button[data-back]") : null;
    if (!b) { return; }
    var opener = byId("tt-table").querySelector("button[data-pid=\"" + b.getAttribute("data-back") + "\"]");
    if (opener) { opener.focus(); }
  });

  byId("tt-csv").addEventListener("click", function () {
    if (current) { save("patentref-triage-" + current.data_version + ".csv", "text/csv;charset=utf-8", toCsv(current, marks)); }
  });

  byId("tt-json").addEventListener("click", function () {
    if (current) { save("patentref-triage-" + current.data_version + ".json", "application/json", JSON.stringify(exportJson(current, marks), null, 1)); }
  });

  byId("tt-unmark").addEventListener("click", function () {
    if (!current || !window.confirm("Remove every mark this browser holds for the patents of this table?")) { return; }
    Object.keys(marksOf(current, marks)).forEach(function (k) { delete marks[k]; });
    keepMarks();
    drawTable(current);
    if (openPid) { openPanel(openPid, true); }
    say("The marks of this table are removed.", "");
  });

  byId("tt-open").addEventListener("change", function (ev) {
    var f = ev.target.files && ev.target.files[0];
    if (!f) { return; }
    var rd = new FileReader();
    rd.onload = function () {
      var obj = null;
      try { obj = JSON.parse(String(rd.result)); } catch (e) { obj = null; }
      if (!obj || !obj.table || !Array.isArray(obj.table.patents) || !Array.isArray(obj.table.export_columns)) {
        say("", "This file is not an export of this page.");
        return;
      }
      Object.keys(obj.marks || {}).forEach(function (k) { if (obj.marks[k] === "have" || obj.marks[k] === "lacks") { marks[k] = obj.marks[k]; } });
      keepMarks();
      show(obj.table, false);
      say("Opened the export of " + (obj.exported_at || "an earlier day") + ": " + obj.table.patents.length + " patents, with its marks.", "");
    };
    rd.readAsText(f);
    ev.target.value = "";
  });

  fields();
  var ex = byId("tt-example");
  if (ex) {
    var t = JSON.parse(ex.textContent);
    byId("tt-list").value = t.patents.map(function (p) { return p.patent_id; }).join("\n");
    byId("tt-components").value = t.components.filter(function (c) { return c.from === "request"; }).map(function (c) { return c.key; }).join(", ");
    show(t, true);
    var first = t.patents.filter(function (p) { return p.built; })[0];
    if (first) { openPanel(first.patent_id, true); }
  }

  window.patentrefTriage = {toCsv: toCsv, exportJson: exportJson, csvField: csvField};
})();
{% endverbatim %}
