/* CivicLens portal.
 *
 * Every figure on this page comes from the API. The only thing kept in the
 * browser is the list of case numbers this device has filed or looked up -
 * because the platform deliberately has nowhere to keep it.
 */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // --------------------------------------------------------------------- data
  var CATEGORIES = [
    "Bribery - payment demanded for a routine service",
    "Extortion or threat by a public official",
    "Procurement fraud - tender or contract rigging",
    "Embezzlement of public funds",
    "Nepotism or irregular appointment",
    "Falsification of official records",
    "Other official misconduct"
  ];

  var DEPARTMENTS = [
    "Municipal Revenue & Property Tax",
    "Building Plan Approval",
    "Public Works - Roads",
    "Water Supply & Sewerage",
    "Land Records & Registration",
    "Motor Vehicles Licensing",
    "Health & Sanitation Inspectorate",
    "Public Distribution System"
  ];

  /* Registered address of each office, prefilled when a department is chosen.
   * Every field stays editable - this is a convenience, not a constraint. */
  var OFFICES = {
    "Municipal Revenue & Property Tax": { state: "Delhi", district: "Central Delhi", city: "New Delhi", pin: "110002", local_address: "Zonal Revenue Office, Block C, 2nd Floor, Civic Centre, Minto Road" },
    "Building Plan Approval": { state: "Delhi", district: "Central Delhi", city: "New Delhi", pin: "110006", local_address: "Building Sanction Branch, Room 214, Town Hall Annexe, Chandni Chowk" },
    "Public Works - Roads": { state: "Delhi", district: "Central Delhi", city: "New Delhi", pin: "110055", local_address: "Executive Engineer (Roads), Works Depot, Rani Jhansi Road" },
    "Water Supply & Sewerage": { state: "Delhi", district: "North West Delhi", city: "New Delhi", pin: "110085", local_address: "Consumer Services Centre, Jal Bhawan, Sector 9, Rohini" },
    "Land Records & Registration": { state: "Delhi", district: "North Delhi", city: "New Delhi", pin: "110054", local_address: "Office of the Sub-Registrar VI, Kashmere Gate Complex" },
    "Motor Vehicles Licensing": { state: "Delhi", district: "East Delhi", city: "New Delhi", pin: "110093", local_address: "Regional Transport Office, Licensing Wing, Loni Road" },
    "Health & Sanitation Inspectorate": { state: "Delhi", district: "Central Delhi", city: "New Delhi", pin: "110005", local_address: "Ward Health Office, Municipal Dispensary Building, Karol Bagh" },
    "Public Distribution System": { state: "Delhi", district: "East Delhi", city: "New Delhi", pin: "110032", local_address: "Circle Office FSO-14, Food & Supplies Complex, Shahdara" }
  };

  var ZONES = { N: "North Zone", C: "Central Zone", S: "South Zone", E: "East Zone" };

  var STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman & Nicobar Islands", "Chandigarh", "Dadra & Nagar Haveli and Daman & Diu", "Delhi", "Jammu & Kashmir", "Ladakh", "Lakshadweep", "Puducherry"];

  var STATUS = {
    pending: { label: "Pending intake", cls: "s-pending" },
    assigned: { label: "Investigator assigned", cls: "s-assigned" },
    investigating: { label: "Under investigation", cls: "s-investigating" },
    confirmed: { label: "Confirmed", cls: "s-confirmed" },
    closed: { label: "Closed - no finding", cls: "s-closed" }
  };

  // --------------------------------------------------------------------- helpers
  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var parts = iso.split("-");
    return parseInt(parts[2], 10) + " " + months[parseInt(parts[1], 10) - 1] + " " + parts[0];
  }

  function statusOf(key) { return STATUS[key] || { label: key, cls: "s-pending" }; }

  function banner(target, kind, title, detail, retry) {
    var icon = kind === "error"
      ? '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6.3"/><path d="M8 5v3.6M8 10.8h.01"/></svg>'
      : '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8.5l3.2 3.2L13 5"/></svg>';
    target.innerHTML =
      '<div class="banner ' + kind + '">' + icon +
      '<div><b>' + esc(title) + '</b>' + esc(detail) + '</div>' +
      (retry ? '<button class="btn ghost retry" style="padding:5px 11px;font-size:12.5px" data-retry="1">Try again</button>' : '') +
      '</div>';
    if (retry) $("[data-retry]", target).addEventListener("click", retry);
  }

  function clear(target) { target.innerHTML = ""; }

  function skeletonRows(count) {
    var out = "";
    for (var i = 0; i < count; i++) {
      out += '<article class="case"><div class="sev"></div><div>' +
        '<div class="cid skeleton">CRTP-0000-000000</div>' +
        '<h4 class="skeleton" style="max-width:' + (55 + (i % 3) * 12) + '%">Loading a case from the register</h4>' +
        '<div class="meta"><span class="skeleton">Department</span><span class="skeleton">Zone</span></div>' +
        '</div><div class="right"><span class="chip skeleton">Status</span></div></article>';
    }
    return out;
  }

  // --------------------------------------------------------------------- device memory
  var saved = [];
  var filedHere = [];
  try { saved = JSON.parse(localStorage.getItem("civiclens.saved") || "[]"); } catch (e) { saved = []; }
  try { filedHere = JSON.parse(localStorage.getItem("civiclens.filed") || "[]"); } catch (e) { filedHere = []; }

  function persist() {
    try {
      localStorage.setItem("civiclens.saved", JSON.stringify(saved.slice(0, 50)));
      localStorage.setItem("civiclens.filed", JSON.stringify(filedHere.slice(0, 50)));
    } catch (e) { /* private mode */ }
  }

  function remember(caseNo, wasFiledHere) {
    if (saved.indexOf(caseNo) === -1) saved.unshift(caseNo);
    if (wasFiledHere && filedHere.indexOf(caseNo) === -1) filedHere.unshift(caseNo);
    persist();
  }

  // --------------------------------------------------------------------- navigation
  function go(view) {
    $$(".view").forEach(function (section) { section.classList.toggle("active", section.id === "v-" + view); });
    $$("#drawerNav button").forEach(function (button) {
      if (button.dataset.go === view) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    closeDrawer();
    setMenu(false);
    if (location.hash !== "#" + view) history.replaceState(null, "", "#" + view);
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (LOADERS[view]) LOADERS[view]();
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-go]");
    if (target) { event.preventDefault(); go(target.dataset.go); }
  });

  function openDrawer() {
    $("#drawer").classList.add("open");
    $("#scrim").classList.add("open");
    $("#menuBtn").setAttribute("aria-expanded", "true");
  }
  function closeDrawer() {
    $("#drawer").classList.remove("open");
    $("#scrim").classList.remove("open");
    $("#menuBtn").setAttribute("aria-expanded", "false");
  }
  $("#menuBtn").addEventListener("click", function () {
    $("#drawer").classList.contains("open") ? closeDrawer() : openDrawer();
  });
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { closeDrawer(); setMenu(false); }
  });

  // --------------------------------------------------------------------- theme
  var storedTheme = null;
  try { storedTheme = localStorage.getItem("civiclens.theme"); } catch (e) { /* ignore */ }
  if (storedTheme) document.documentElement.setAttribute("data-theme", storedTheme);

  $("#themeBtn").addEventListener("click", function () {
    var current = document.documentElement.getAttribute("data-theme");
    if (!current) current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    var next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("civiclens.theme", next); } catch (e) { /* ignore */ }
    if (lastStats) drawChart(lastStats);
  });

  // --------------------------------------------------------------------- session chrome
  var session = null;

  function renderSession() {
    var signedIn = !!session;
    $("#signinBtn").hidden = signedIn;
    $("#profileWrap").hidden = !signedIn;
    if (!signedIn) return;
    $("#handleTop").textContent = session.employee_code;
    $("#handleMenu").textContent = session.employee_code;
    $("#roleTop").textContent = session.role === "investigator" ? "Ombudsman investigator" : "Departmental officer";
    $("#roleMenu").textContent = session.department;
    $("#avatarTop").textContent = session.role === "investigator" ? "OMB" : "GOV";
    $("#avatarMenu").textContent = $("#avatarTop").textContent;
  }

  var menuOpen = false;
  function setMenu(open) {
    menuOpen = open;
    $("#profileMenu").classList.toggle("open", open);
    $("#profileBtn").setAttribute("aria-expanded", String(open));
  }
  $("#profileBtn").addEventListener("click", function (event) { event.stopPropagation(); setMenu(!menuOpen); });
  document.addEventListener("click", function (event) {
    if (menuOpen && !event.target.closest(".profile-wrap")) setMenu(false);
  });

  window.addEventListener("civiclens:signed-out", function () {
    session = null;
    renderSession();
    $("#officialDash").hidden = true;
    $("#officialLogin").hidden = false;
  });

  function signOut() {
    API.logout().then(function () {
      session = null;
      renderSession();
      $("#officialDash").hidden = true;
      $("#officialLogin").hidden = false;
      go("official");
    });
  }
  $("#signoutBtn").addEventListener("click", signOut);
  $("#logoutBtn").addEventListener("click", signOut);

  // --------------------------------------------------------------------- case rendering
  function caseRow(row, options) {
    options = options || {};
    var status = statusOf(row.status);
    var severity = row.status === "confirmed" ? "sev-confirmed"
      : row.status === "investigating" ? "sev-investigating"
        : row.status === "closed" ? "sev-closed" : "";
    var title = options.title || row.detail || row.category;
    if (title.length > 104) title = title.slice(0, 104).replace(/\s+\S*$/, "") + "…";

    return '<article class="case ' + severity + '">' +
      '<div class="sev"></div>' +
      '<div>' +
      '<div class="cid">' + esc(row.case_no) + '</div>' +
      '<h4>' + esc(title) + '</h4>' +
      '<div class="meta">' +
      '<span>' + esc(row.department) + '</span>' +
      (row.district ? '<span>' + esc(row.district) + '</span>' : '') +
      (row.zone ? '<span>' + esc(ZONES[row.zone] || row.zone) + '</span>' : '') +
      '<span>Filed <em>' + fmtDate(row.filed_on) + '</em></span>' +
      '<span>Investigator <em>' + (row.investigator_code ? esc(row.investigator_code) : "not yet assigned") + '</em></span>' +
      (row.overdue ? '<span style="color:var(--confirmed)">Past the 14-day limit</span>' : '') +
      '</div>' +
      (options.penalty && row.penalty ? '<p class="penalty">' + esc(row.penalty) + '</p>' : '') +
      '</div>' +
      '<div class="right"><span class="chip ' + status.cls + '">' + status.label + '</span>' +
      '<button class="btn ghost" style="padding:5px 10px;font-size:12px" data-track="' + esc(row.case_no) + '">Open</button>' +
      '</div></article>';
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-track]");
    if (!button) return;
    go("track");
    $("#trackInput").value = button.dataset.track;
    track(button.dataset.track);
  });

  // --------------------------------------------------------------------- home
  var lastStats = null;
  var homePage = 1;
  var homeQuery = "";

  function loadHome() {
    loadStats();
    loadRegister();
    loadLedger();
  }

  function loadStats() {
    return API.stats().then(function (stats) {
      lastStats = stats;
      var by = stats.by_status || {};
      $("#kTotal").textContent = stats.total.toLocaleString();
      $("#kActive").textContent = (by.investigating || 0) + (by.assigned || 0);
      $("#kConfirmed").textContent = by.confirmed || 0;
      $("#kDays").textContent = stats.median_days_to_assign === null ? "—" : stats.median_days_to_assign;
      $$(".stat b").forEach(function (el) { el.classList.remove("skeleton"); });

      var buckets = [
        { label: "Confirmed with penalty", color: "var(--confirmed)", n: by.confirmed || 0 },
        { label: "Under active investigation", color: "var(--investigating)", n: by.investigating || 0 },
        { label: "Investigator assigned", color: "var(--pending)", n: by.assigned || 0 },
        { label: "Closed after inquiry", color: "var(--closed)", n: by.closed || 0 },
        { label: "Awaiting assignment", color: "var(--line-strong)", n: by.pending || 0 }
      ].filter(function (bucket) { return bucket.n > 0; });

      $("#integrityScore").textContent = stats.acted_on_pct + "%";
      $("#integrityMeter").innerHTML = buckets.map(function (bucket) {
        return '<i style="width:' + (bucket.n / Math.max(stats.total, 1) * 100) + '%;background:' + bucket.color + '"></i>';
      }).join("");
      $("#integrityLegend").innerHTML = buckets.map(function (bucket) {
        return '<div><span class="sw" style="background:' + bucket.color + '"></span>' + bucket.label +
          '<b>' + bucket.n.toLocaleString() + '</b></div>';
      }).join("");

      drawChart(stats);
      return stats;
    }).catch(function (error) {
      $("#offlineNote").hidden = false;
      banner($("#integrityLegend"), "error", "The register is unreachable.", error.message, function () {
        $("#offlineNote").hidden = true;
        loadStats();
      });
    });
  }

  function drawChart(stats) {
    var order = ["pending", "assigned", "investigating", "confirmed", "closed"];
    var colors = {
      pending: "var(--line-strong)", assigned: "var(--pending)", investigating: "var(--investigating)",
      confirmed: "var(--confirmed)", closed: "var(--closed)"
    };
    var data = order.map(function (key) {
      return { key: key, label: statusOf(key).label, n: (stats.by_status || {})[key] || 0 };
    });
    var max = Math.max.apply(null, data.map(function (d) { return d.n; }).concat([1]));

    var W = 560, H = 230, padL = 34, padR = 12, padT = 16, padB = 46;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var band = plotW / data.length, barWidth = Math.min(52, band * 0.58);

    // Ticks on a rounded scale, so every label names a value the axis reaches.
    var step = Math.max(1, Math.ceil(max / 4));
    var top = step * 4;
    var svg = '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Cases by status: ' +
      data.map(function (d) { return d.label + " " + d.n; }).join(", ") + '">';

    for (var t = 0; t <= top; t += step) {
      var y = padT + plotH - (t / top) * plotH;
      svg += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y +
        '" stroke="var(--line)" stroke-width="1"/>';
      svg += '<text x="' + (padL - 8) + '" y="' + (y + 3.5) + '" text-anchor="end" fill="var(--muted)" ' +
        'font-family="IBM Plex Mono, monospace" font-size="10">' + t + '</text>';
    }

    data.forEach(function (d, i) {
      var cx = padL + band * i + band / 2;
      var height = (d.n / top) * plotH;
      svg += '<rect x="' + (cx - barWidth / 2) + '" y="' + (padT + plotH - height) + '" width="' + barWidth +
        '" height="' + Math.max(height, d.n > 0 ? 2 : 0) + '" fill="' + colors[d.key] + '" rx="1"/>';
      if (d.n > 0) {
        svg += '<text x="' + cx + '" y="' + (padT + plotH - height - 6) + '" text-anchor="middle" fill="var(--ink)" ' +
          'font-family="IBM Plex Mono, monospace" font-size="10.5" font-weight="600">' + d.n + '</text>';
      }
      var words = d.label.split(" ");
      svg += '<text x="' + cx + '" y="' + (H - 26) + '" text-anchor="middle" fill="var(--muted)" ' +
        'font-family="Public Sans, sans-serif" font-size="10">' + esc(words[0]) + '</text>';
      if (words.length > 1) {
        svg += '<text x="' + cx + '" y="' + (H - 14) + '" text-anchor="middle" fill="var(--muted)" ' +
          'font-family="Public Sans, sans-serif" font-size="10">' + esc(words.slice(1).join(" ")) + '</text>';
      }
    });

    svg += '<line x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (W - padR) + '" y2="' + (padT + plotH) +
      '" stroke="var(--line-strong)" stroke-width="1"/></svg>';
    $("#chartWrap").innerHTML = svg;
  }

  function loadRegister() {
    $("#homeCases").innerHTML = skeletonRows(4);
    return API.register({ q: homeQuery, page: homePage, per_page: 8 }).then(function (page) {
      $("#regCount").textContent = page.total
        ? ((page.page - 1) * page.per_page + 1) + "–" + Math.min(page.page * page.per_page, page.total) + " of " + page.total
        : "";
      if (!page.rows.length) {
        $("#homeCases").innerHTML = '<p class="empty">No entry matches that search. Case numbers take the form CRTP-YYYY-000000.</p>';
        $("#homePager").innerHTML = "";
        return;
      }
      $("#homeCases").innerHTML = page.rows.map(function (row) { return caseRow(row, {}); }).join("");

      var pages = Math.ceil(page.total / page.per_page);
      $("#homePager").innerHTML = pages > 1
        ? '<button class="btn ghost" style="padding:6px 12px;font-size:12.5px" id="prevPage"' + (page.page <= 1 ? " disabled" : "") + '>Previous</button>' +
        '<span class="eyebrow">Page ' + page.page + ' of ' + pages + '</span>' +
        '<button class="btn ghost" style="padding:6px 12px;font-size:12.5px" id="nextPage"' + (page.page >= pages ? " disabled" : "") + '>Next</button>'
        : "";
      if ($("#prevPage")) $("#prevPage").addEventListener("click", function () { homePage--; loadRegister(); });
      if ($("#nextPage")) $("#nextPage").addEventListener("click", function () { homePage++; loadRegister(); });
    }).catch(function (error) {
      banner($("#homeCases"), "error", "Could not load the register.", error.message, loadRegister);
    });
  }

  var searchTimer = null;
  $("#homeSearch").addEventListener("input", function () {
    var value = this.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { homeQuery = value; homePage = 1; loadRegister(); }, 250);
  });
  $("#homeSearchClear").addEventListener("click", function () {
    $("#homeSearch").value = ""; homeQuery = ""; homePage = 1; loadRegister();
  });

  function loadLedger() {
    return API.ledger(12).then(function (entries) {
      $("#ledger").innerHTML = entries.map(function (entry) {
        return '<div><span class="t">' + esc(fmtDate(entry.on)) + '</span>' +
          '<span>' + esc(entry.case_no) + ' · ' + esc(entry.milestone) + '</span>' +
          '<span class="h">#' + esc(entry.entry_hash) + '</span></div>';
      }).join("") || '<p class="empty" style="padding:14px">No entries yet.</p>';
      return API.verifyLedger();
    }).then(function (result) {
      if (!result) return;
      var chip = $("#ledgerState");
      chip.className = "chip " + (result.intact ? "s-closed" : "s-confirmed");
      chip.textContent = result.intact ? "Chain intact · " + result.entries + " entries" : "Chain broken at #" + result.broken_at;
    }).catch(function (error) {
      banner($("#ledger"), "error", "Could not read the ledger.", error.message, loadLedger);
    });
  }

  // --------------------------------------------------------------------- report
  function fillSelect(select, values) {
    select.innerHTML = values.map(function (value) { return '<option>' + esc(value) + '</option>'; }).join("");
  }
  fillSelect($("#rType"), CATEGORIES);
  fillSelect($("#rDept"), DEPARTMENTS);
  fillSelect($("#rState"), STATES);
  $("#rWard").innerHTML = Object.keys(ZONES).map(function (key) {
    return '<option value="' + key + '">' + esc(ZONES[key]) + '</option>';
  }).join("");

  function prefillOffice() {
    var office = OFFICES[$("#rDept").value];
    if (!office) return;
    $("#rState").value = office.state;
    $("#rDistrict").value = office.district;
    $("#rCity").value = office.city;
    $("#rPin").value = office.pin;
    $("#rLocal").value = office.local_address;
  }
  $("#rDept").addEventListener("change", prefillOffice);
  prefillOffice();

  $$('input[name="anon"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      var wantsContact = this.value === "contact";
      $("#contactField").hidden = !wantsContact;
      $("#anonHint").textContent = wantsContact
        ? "Your number is encrypted and stored on a separate database, reachable only by the assigned investigator. It never appears in the departmental portal."
        : "No contact field is stored. Your case number is the only way back to this report - save it.";
    });
  });

  var pendingFile = null;
  $("#dropzone").addEventListener("click", function () { $("#rFile").click(); });
  $("#dropzone").addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("#rFile").click(); }
  });
  $("#rFile").addEventListener("change", function () {
    pendingFile = this.files[0] || null;
    $("#fileList").innerHTML = pendingFile
      ? '<li>▸ ' + esc(pendingFile.name) + ' — ' + Math.round(pendingFile.size / 1024) + ' KB — ' +
      '<span style="color:var(--muted)">metadata stripped on upload</span></li>'
      : "";
  });

  function fieldError(input, message) {
    input.style.borderColor = "var(--confirmed)";
    var existing = input.parentNode.querySelector(".field-error");
    if (existing) existing.remove();
    var note = document.createElement("span");
    note.className = "field-error";
    note.textContent = message;
    input.parentNode.appendChild(note);
    input.focus();
    input.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function clearFieldErrors(form) {
    $$(".field-error", form).forEach(function (node) { node.remove(); });
    $$("input,select,textarea", form).forEach(function (input) { input.style.borderColor = ""; });
  }

  $("#reportForm").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = this;
    clearFieldErrors(form);
    clear($("#reportBanner"));

    var pin = $("#rPin").value.trim();
    if (!/^[1-9][0-9]{5}$/.test(pin)) {
      return fieldError($("#rPin"), "A PIN code is six digits and does not start with 0. It decides which bench takes the file.");
    }
    if (!$("#rDistrict").value.trim()) return fieldError($("#rDistrict"), "Name the district - the file cannot be routed without it.");
    if (!$("#rCity").value.trim()) return fieldError($("#rCity"), "Name the city, town or village where the office sits.");
    if (!$("#rLocal").value.trim()) return fieldError($("#rLocal"), "Give the building, floor and counter or room number if you have it.");
    if ($("#rDetail").value.trim().length < 20) {
      return fieldError($("#rDetail"), "Describe what happened in at least a sentence - an investigator needs something to act on.");
    }

    var accused = {
      name: $("#rName").value.trim() || null,
      designation: $("#rDesig").value.trim() || null,
      employee_code: $("#rEmpCode").value.trim() || null
    };
    var payload = {
      category: $("#rType").value,
      department: $("#rDept").value,
      zone: $("#rWard").value,
      office: {
        state: $("#rState").value,
        district: $("#rDistrict").value.trim(),
        city: $("#rCity").value.trim(),
        pin: pin,
        local_address: $("#rLocal").value.trim()
      },
      accused: (accused.name || accused.designation || accused.employee_code) ? accused : null,
      amount_text: $("#rAmount").value.trim() || null,
      detail: $("#rDetail").value.trim(),
      followup_contact: $("#contactField").hidden ? null : ($("#rContact").value.trim() || null)
    };

    var submit = $("#reportSubmit");
    submit.disabled = true;
    submit.innerHTML = '<span class="spin"></span>Sealing your report';

    API.fileReport(payload).then(function (receipt) {
      remember(receipt.case_no, true);
      if (!pendingFile) return receipt;
      submit.innerHTML = '<span class="spin"></span>Stripping file metadata';
      return API.attachEvidence(receipt.case_no, receipt.upload_token, pendingFile)
        .then(function (evidence) { receipt.evidence = evidence; return receipt; })
        .catch(function (error) { receipt.evidenceError = error.message; return receipt; });
    }).then(function (receipt) {
      showReceipt(receipt);
      form.reset();
      pendingFile = null;
      $("#fileList").innerHTML = "";
      $("#contactField").hidden = true;
      prefillOffice();
      loadStats();
      loadRegister();
    }).catch(function (error) {
      if (error.fields && error.fields.length) {
        banner($("#reportBanner"), "error", "That form could not be accepted.",
          error.fields.map(function (f) { return f.field + ": " + f.problem; }).join("; "));
      } else {
        banner($("#reportBanner"), "error", "Your report was not filed.", error.message);
      }
    }).then(function () {
      submit.disabled = false;
      submit.textContent = "Submit report and issue case number";
    });
  });

  function showReceipt(receipt) {
    $("#receiptSlot").innerHTML =
      '<div class="eyebrow">Report accepted</div>' +
      '<h3 style="font-size:19px;margin:6px 0 10px">Write this number down now</h3>' +
      '<div class="receipt"><div class="eyebrow">Permanent case number</div>' +
      '<div class="cidbig">' + esc(receipt.case_no) + '</div>' +
      '<p style="font-size:12.5px;color:var(--ink-2);max-width:44ch;margin:8px auto 0">' + esc(receipt.message) + '</p>' +
      '<button class="btn" style="margin-top:14px" data-track="' + esc(receipt.case_no) + '">Open the case file</button>' +
      '</div>' +
      (receipt.evidence
        ? '<div class="banner ok" style="margin-top:14px"><svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8.5l3.2 3.2L13 5"/></svg>' +
        '<div><b>Evidence stored</b>' + esc(receipt.evidence.note) + '</div></div>'
        : "") +
      (receipt.evidenceError
        ? '<div class="banner error" style="margin-top:14px"><div><b>The case was filed, but the file was not attached.</b>' +
        esc(receipt.evidenceError) + '</div></div>'
        : "") +
      '<p style="font-size:13px;color:var(--ink-2);margin-top:16px">Your report is on the public register now as ' +
      '<span class="chip s-pending">Pending intake</span> and must be assigned to an investigator by ' +
      esc(fmtDate(receipt.assignment_due_on)) + '.</p>';
    $("#receiptSlot").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // --------------------------------------------------------------------- track
  function timelineFor(record) {
    var steps = [
      {
        title: "Report received and sealed", when: fmtDate(record.filed_on), state: "done",
        note: "Intake metadata discarded. Case number issued."
      },
      {
        title: "Investigator assigned",
        when: record.assigned_on ? fmtDate(record.assigned_on) : (record.overdue ? "Overdue" : "Due by " + fmtDate(record.assignment_due_on)),
        state: record.investigator_code ? "done" : "now",
        note: record.investigator_code
          ? "Routed to " + record.investigator_code + ", posted outside " + record.department + "."
          : (record.overdue
            ? "The statutory 14-day limit has passed. The breach is published on the ledger."
            : "Awaiting allocation from the external investigator pool.")
      },
      {
        title: "Under investigation",
        when: ["investigating", "confirmed", "closed"].indexOf(record.status) > -1 ? "In progress or complete" : "Not started",
        state: record.status === "investigating" ? "now" : (["confirmed", "closed"].indexOf(record.status) > -1 ? "done" : ""),
        note: "Statements recorded, records impounded where required."
      },
      {
        title: record.status === "closed" ? "Closed - allegation not substantiated" : "Finding published",
        when: record.decided_on ? fmtDate(record.decided_on) : "Awaiting finding",
        state: record.decided_on ? "done" : "",
        note: record.penalty || (record.status === "closed"
          ? "Reasons recorded and published in full."
          : "Published within 24 hours of the finding.")
      }
    ];
    return '<ul class="timeline">' + steps.map(function (step) {
      return '<li class="' + step.state + '"><b>' + esc(step.title) + '</b>' +
        '<span class="t">' + esc(step.when) + '</span>' +
        '<p style="font-size:13px;color:var(--ink-2);margin-top:4px;max-width:60ch">' + esc(step.note) + '</p></li>';
    }).join("") + '</ul>';
  }

  function track(caseNo) {
    var out = $("#trackResult");
    out.innerHTML = '<div class="panel">' + skeletonRows(1) + '</div>';

    API.trackCase(caseNo).then(function (record) {
      remember(record.case_no, false);
      renderSaved();
      var status = statusOf(record.status);
      var office = record.office;
      out.innerHTML = '<div class="panel">' +
        '<div class="panel-head"><div>' +
        '<div style="font-family:var(--mono);font-size:13px;color:var(--brass)">' + esc(record.case_no) + '</div>' +
        '<h3 style="font-size:21px;margin-top:4px;max-width:44ch">' + esc(record.category) + '</h3></div>' +
        '<span class="chip ' + status.cls + '">' + status.label + '</span></div>' +
        '<dl class="sanitized" style="margin-top:14px">' +
        '<dt>Department</dt><dd>' + esc(record.department) + '</dd>' +
        '<dt>Office address</dt><dd><address class="addr">' + esc(office.local_address) + '<br>' +
        esc(office.city) + ', ' + esc(office.district) + '<br>' + esc(office.state) +
        ' — <span class="pin">' + esc(office.pin) + '</span></address></dd>' +
        '<dt>Official named</dt><dd>' + (record.accused_name
          ? '<span class="named">' + esc(record.accused_name) + ' <span class="tag">substantiated</span></span>'
          : '<span style="color:var(--muted)">' + (record.accused_designation
            ? esc(record.accused_designation) + ' — name withheld until a finding'
            : 'no individual identified') + '</span>') + '</dd>' +
        (record.amount_text ? '<dt>Amount</dt><dd>' + esc(record.amount_text) + '</dd>' : '') +
        '<dt>Investigator</dt><dd>' + (record.investigator_code
          ? esc(record.investigator_code) + ' <span style="color:var(--muted);font-size:12px">— code public, name withheld</span>'
          : '<span style="color:var(--muted)">not yet assigned</span>') + '</dd>' +
        '<dt>Evidence</dt><dd>' + record.evidence_count + ' file(s) held</dd>' +
        '<dt>Reporter</dt><dd><span class="redact" data-len="████████████"></span> ' +
        '<span style="color:var(--muted);font-size:12px">sealed permanently</span></dd>' +
        '</dl>' +
        '<p style="font-size:14px;color:var(--ink-2);margin-top:16px;max-width:66ch;border-top:1px solid var(--line);padding-top:14px">' +
        esc(record.detail) + '</p>' +
        timelineFor(record) +
        '</div>';
    }).catch(function (error) {
      banner(out, "error", error.status === 404 ? "No case carries that number." : "Could not reach the register.",
        error.message, error.status === 404 ? null : function () { track(caseNo); });
    });
  }

  $("#trackBtn").addEventListener("click", function () { track($("#trackInput").value); });
  $("#trackInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter") { event.preventDefault(); track(this.value); }
  });
  $("#sampleBtn").addEventListener("click", function () {
    API.register({ per_page: 1 }).then(function (page) {
      if (!page.rows.length) return;
      $("#trackInput").value = page.rows[0].case_no;
      track(page.rows[0].case_no);
    });
  });

  function renderSaved() {
    var container = $("#savedList");
    if (!saved.length) {
      container.innerHTML = '<p class="empty">Nothing saved yet. Numbers you file or look up are kept here for convenience.</p>';
      return;
    }
    container.innerHTML = skeletonRows(Math.min(saved.length, 3));
    Promise.all(saved.slice(0, 8).map(function (caseNo) {
      return API.trackCase(caseNo).catch(function () { return null; });
    })).then(function (records) {
      var rows = records.filter(Boolean);
      container.innerHTML = rows.length
        ? rows.map(function (row) { return caseRow(row, {}); }).join("")
        : '<p class="empty">Saved numbers could not be read from the register.</p>';
    });
  }

  // --------------------------------------------------------------------- my dashboard
  function loadMine() {
    var container = $("#meCases");
    if (!filedHere.length) {
      container.innerHTML = '<p class="empty">No reports filed from this device yet. Filing one takes four fields and no name.</p>';
      ["meFiled", "mePending", "meActive", "meConfirmed"].forEach(function (id) { $("#" + id).textContent = "0"; });
      return;
    }
    container.innerHTML = skeletonRows(Math.min(filedHere.length, 3));
    Promise.all(filedHere.map(function (caseNo) {
      return API.trackCase(caseNo).catch(function () { return null; });
    })).then(function (records) {
      var rows = records.filter(Boolean);
      $("#meFiled").textContent = rows.length;
      $("#mePending").textContent = rows.filter(function (r) { return r.status === "pending"; }).length;
      $("#meActive").textContent = rows.filter(function (r) { return r.status === "investigating" || r.status === "assigned"; }).length;
      $("#meConfirmed").textContent = rows.filter(function (r) { return r.status === "confirmed"; }).length;
      container.innerHTML = rows.length
        ? rows.map(function (row) { return caseRow(row, { penalty: true }); }).join("")
        : '<p class="empty">Those case numbers are no longer on the register.</p>';
    });
  }

  $("#restoreForm").addEventListener("submit", function (event) {
    event.preventDefault();
    var caseNo = $("#restoreId").value.trim().toUpperCase();
    API.trackCase(caseNo).then(function (record) {
      remember(record.case_no, true);
      banner($("#restoreBanner"), "ok", record.case_no + " added.", "It now appears under your reports.");
      $("#restoreId").value = "";
      loadMine();
      renderSaved();
    }).catch(function (error) {
      banner($("#restoreBanner"), "error", "That case could not be added.", error.message);
    });
  });

  $("#clearDeviceBtn").addEventListener("click", function () {
    saved = []; filedHere = []; persist();
    loadMine(); renderSaved();
  });

  // --------------------------------------------------------------------- map
  var activeZone = null;

  function loadMap() {
    return API.heatmap().then(function (zones) {
      $("#zones").innerHTML = zones.map(function (zone) {
        var shade = { low: "var(--pending-bg)", moderate: "var(--investigating-bg)", high: "var(--confirmed)" }[zone.band];
        var cells = "";
        // Sixteen wards per zone; filled in proportion to the zone's density so
        // the block reads as a quantity, not as sixteen real measurements.
        var filled = Math.round(Math.min(zone.per_10k / 0.8, 1) * 16);
        for (var i = 0; i < 16; i++) {
          cells += '<i style="background:' + (i < filled ? shade : "var(--surface-2)") + '"></i>';
        }
        return '<button class="zone" data-zone="' + zone.zone + '" aria-pressed="' + (activeZone === zone.zone) + '">' +
          '<div class="cells">' + cells + '</div>' +
          '<div class="zfoot"><b>' + esc(ZONES[zone.zone] || zone.zone) + '</b>' +
          '<span>' + zone.band.charAt(0).toUpperCase() + zone.band.slice(1) + ' · ' + zone.per_10k +
          ' per 10k · ' + zone.reports + ' report(s)</span></div></button>';
      }).join("");

      $$(".zone").forEach(function (button) {
        button.addEventListener("click", function () {
          activeZone = activeZone === button.dataset.zone ? null : button.dataset.zone;
          loadMap();
          loadZoneCases();
        });
      });
      loadZoneCases();
    }).catch(function (error) {
      banner($("#zones"), "error", "Could not load the density map.", error.message, loadMap);
    });
  }

  function loadZoneCases() {
    $("#zoneFilterLabel").textContent = activeZone ? (ZONES[activeZone] || activeZone) : "All zones";
    $("#zoneCases").innerHTML = skeletonRows(3);
    API.register({ zone: activeZone, per_page: 50 }).then(function (page) {
      $("#zoneCases").innerHTML = page.rows.length
        ? page.rows.slice(0, 7).map(function (row) { return caseRow(row, {}); }).join("")
        : '<p class="empty">No reports on record in this zone.</p>';

      var counts = {};
      page.rows.forEach(function (row) { counts[row.department] = (counts[row.department] || 0) + 1; });
      var ranked = Object.keys(counts).map(function (key) { return { department: key, n: counts[key] }; })
        .sort(function (a, b) { return b.n - a.n; }).slice(0, 6);
      var max = ranked.length ? ranked[0].n : 1;
      $("#deptBars").innerHTML = ranked.map(function (row) {
        return '<div><div style="display:flex;justify-content:space-between;font-size:12.5px;gap:10px">' +
          '<span>' + esc(row.department) + '</span><b style="font-family:var(--mono);font-variant-numeric:tabular-nums">' + row.n + '</b></div>' +
          '<div style="height:5px;background:var(--surface-2);border-radius:99px;margin-top:5px">' +
          '<i style="display:block;height:100%;border-radius:99px;width:' + (row.n / max * 100) + '%;background:var(--brass)"></i></div></div>';
      }).join("") || '<p class="empty">No departments in range.</p>';
    });
  }

  $("#clearZone").addEventListener("click", function () { activeZone = null; loadMap(); });

  // --------------------------------------------------------------------- bulletins
  function loadBulletins() {
    $("#bulletins").innerHTML = skeletonRows(3);
    API.register({ status: "confirmed", per_page: 25 }).then(function (page) {
      if (!page.rows.length) {
        $("#bulletins").innerHTML = '<p class="empty">No case has reached a substantiated finding yet. Confirmed cases and their penalties are published here.</p>';
        return;
      }
      $("#bulletins").innerHTML = page.rows.map(function (row) { return caseRow(row, { penalty: true }); }).join("");
    }).catch(function (error) {
      banner($("#bulletins"), "error", "Could not load the bulletins.", error.message, loadBulletins);
    });
  }

  // --------------------------------------------------------------------- official portal
  function loadDemoCredentials() {
    API.demoAccounts().then(function (info) {
      if (!info || !info.accounts || !info.accounts.length) return;
      $("#demoCredentials").innerHTML =
        '<div class="notice" style="margin-top:18px"><b>Demonstration deployment</b> — these accounts exist so you can see ' +
        'the departmental and investigator views. The one-time codes below are generated live by the server.' +
        '<div style="margin-top:10px;display:flex;flex-direction:column;gap:8px">' +
        info.accounts.map(function (account) {
          return '<button class="btn ghost" style="padding:7px 11px;font-size:12.5px;text-align:left" ' +
            'data-demo-code="' + esc(account.employee_code) + '" data-demo-pass="' + esc(account.password) + '" ' +
            'data-demo-otp="' + esc(account.otp) + '">' +
            '<span style="font-family:var(--mono)">' + esc(account.employee_code) + '</span> — ' +
            esc(account.role === "investigator" ? "Ombudsman investigator" : account.department) +
            '<br><span style="color:var(--muted);font-size:11.5px">Tap to fill · code ' + esc(account.otp) + '</span></button>';
        }).join("") + '</div></div>';

      $$("[data-demo-code]").forEach(function (button) {
        button.addEventListener("click", function () {
          $("#lEmp").value = button.dataset.demoCode;
          $("#lPass").value = button.dataset.demoPass;
          $("#lOtp").value = button.dataset.demoOtp;
          $("#lOtp").focus();
        });
      });
    }).catch(function () { /* demo mode off: nothing to show, which is correct */ });
  }

  $("#loginForm").addEventListener("submit", function (event) {
    event.preventDefault();
    clear($("#loginBanner"));
    var submit = $("#loginSubmit");
    submit.disabled = true;
    submit.innerHTML = '<span class="spin"></span>Verifying';

    API.login($("#lEmp").value.trim(), $("#lPass").value, $("#lOtp").value.trim())
      .then(function (body) {
        session = { employee_code: $("#lEmp").value.trim(), role: body.role, department: body.department };
        renderSession();
        $("#lPass").value = ""; $("#lOtp").value = "";
        $("#officialLogin").hidden = true;
        $("#officialDash").hidden = false;
        loadDepartment();
      })
      .catch(function (error) {
        banner($("#loginBanner"), "error", "Not signed in.", error.message);
        loadDemoCredentials();   // refresh the one-time codes, which rotate every 30s
      })
      .then(function () {
        submit.disabled = false;
        submit.textContent = "Verify and enter";
      });
  });

  function loadDepartment() {
    $("#oWho").textContent = "Signed in · " + session.employee_code;
    $("#oDept").textContent = session.department;
    $("#officialCases").innerHTML = skeletonRows(3);

    API.deptSummary().then(function (summary) {
      $("#oTotal").textContent = summary.received;
      $("#oActive").textContent = summary.active_investigations;
      $("#oClosed").textContent = summary.resolved;
      $("#oDue").textContent = summary.overdue_assignments;
    });

    API.deptCases().then(function (cases) {
      if (!cases.length) {
        $("#officialCases").innerHTML = '<p class="empty">No complaints are currently on record against this department.</p>';
        return;
      }
      $("#officialCases").innerHTML = cases.map(function (row) {
        var status = statusOf(row.status);
        var office = row.office;
        return '<article class="case" style="grid-template-columns:auto 1fr">' +
          '<div class="sev" style="background:' + (row.status === "confirmed" ? "var(--confirmed)" : "var(--investigating)") + '"></div>' +
          '<div><div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline">' +
          '<span class="cid">' + esc(row.case_no) + '</span><span class="chip ' + status.cls + '">' + status.label + '</span></div>' +
          '<h4>' + esc(row.category) + '</h4>' +
          '<p style="font-size:13.5px;color:var(--ink-2);max-width:68ch;margin:6px 0 12px">' + esc(row.detail) + '</p>' +
          '<dl class="sanitized" style="font-size:13px">' +
          '<dt>Filed</dt><dd>' + fmtDate(row.filed_on) + ' <span style="color:var(--muted);font-size:12px">(calendar day only)</span></dd>' +
          '<dt>Office cited</dt><dd><address class="addr">' + esc(office.local_address) + '<br>' +
          esc(office.city) + ', ' + esc(office.district) + ' — <span class="pin">' + esc(office.pin) + '</span></address></dd>' +
          '<dt>Official named</dt><dd>' + (row.accused_name
            ? esc(row.accused_name) + '<span class="named"> <span class="tag">alleged</span></span>'
            : '<span style="color:var(--muted)">not named</span>') +
          (row.accused_designation ? '<br><span style="font-size:12.5px;color:var(--ink-2)">' + esc(row.accused_designation) + '</span>' : '') + '</dd>' +
          '<dt>Investigator</dt><dd>' + (row.investigator_code ? esc(row.investigator_code) : "awaiting assignment") + '</dd>' +
          '<dt>Reply due</dt><dd>' + fmtDate(row.reply_due_on) + '</dd>' +
          '<dt>Reporter name</dt><dd><span class="redact" data-len="█████████"></span></dd>' +
          '<dt>Reporter contact</dt><dd><span class="redact" data-len="███████████"></span></dd>' +
          '</dl>' +
          (row.evidence.length
            ? '<div style="margin-top:12px;font-size:12.5px">' + row.evidence.map(function (item) {
              return '<button class="btn ghost" style="padding:5px 10px;font-size:12px;margin-right:6px" ' +
                'data-evidence="' + esc(item.sha256) + '">Open evidence · ' + esc(item.media_type) + '</button>';
            }).join("") + '</div>'
            : '') +
          '<div style="margin-top:14px">' +
          '<button class="btn ghost" style="padding:6px 12px;font-size:12.5px" data-reply="' + esc(row.case_no) + '">Submit departmental reply</button>' +
          '</div>' +
          '<div data-reply-slot="' + esc(row.case_no) + '"></div>' +
          '</div></article>';
      }).join("");
    }).catch(function (error) {
      banner($("#officialCases"), "error", "Could not load your department's cases.", error.message, loadDepartment);
    });
  }

  document.addEventListener("click", function (event) {
    var evidenceButton = event.target.closest("[data-evidence]");
    if (evidenceButton) {
      API.evidenceManifest(evidenceButton.dataset.evidence).then(function (manifest) {
        window.open(manifest.download_url, "_blank", "noopener");
      }).catch(function (error) { alert(error.message); });
      return;
    }

    var replyButton = event.target.closest("[data-reply]");
    if (replyButton) {
      var caseNo = replyButton.dataset.reply;
      var slot = document.querySelector('[data-reply-slot="' + caseNo + '"]');
      if (slot.innerHTML) { slot.innerHTML = ""; return; }
      slot.innerHTML = '<div class="field" style="margin-top:12px">' +
        '<label>Departmental reply — published with the case</label>' +
        '<textarea style="min-height:80px" placeholder="State what the department has done or found."></textarea>' +
        '<button class="btn" style="margin-top:8px;padding:7px 14px;font-size:13px" data-send-reply="' + esc(caseNo) + '">Publish reply</button>' +
        '</div>';
      return;
    }

    var sendButton = event.target.closest("[data-send-reply]");
    if (sendButton) {
      var target = sendButton.dataset.sendReply;
      var slotNode = document.querySelector('[data-reply-slot="' + target + '"]');
      var note = slotNode.querySelector("textarea").value.trim();
      if (note.length < 10) { alert("Write at least a sentence - this is published with the case."); return; }
      sendButton.disabled = true;
      API.deptReply(target, note).then(function () {
        slotNode.innerHTML = '<div class="banner ok"><div><b>Reply published.</b>It now appears on the public case file.</div></div>';
        loadRegister();
      }).catch(function (error) {
        sendButton.disabled = false;
        alert(error.message);
      });
    }
  });

  // --------------------------------------------------------------------- audit
  function loadAudit() {
    var container = $("#auditList");
    container.innerHTML = skeletonRows(3);
    API.auditTrail(60).then(function (rows) {
      if (!rows.length) {
        container.innerHTML = '<p class="empty">No entries yet.</p>';
        return;
      }
      container.innerHTML = '<div class="ledger" style="max-height:none">' + rows.map(function (row) {
        return '<div><span class="t">' + esc(row.at.replace("T", " ").slice(0, 16)) + '</span>' +
          '<span>' + esc(row.actor) + ' · ' + esc(row.action) + (row.case_no ? ' · ' + esc(row.case_no) : "") + '</span>' +
          '<span class="h">' + esc(row.role) + '</span></div>';
      }).join("") + '</div>';
    }).catch(function (error) {
      banner(container, "error", "Could not read the audit trail.", error.message, loadAudit);
    });
  }

  // --------------------------------------------------------------------- service status
  function loadStatus() {
    API.health().then(function (health) {
      $("#serviceStatus").innerHTML =
        '<dt>Status</dt><dd>' + esc(health.status) + '</dd>' +
        '<dt>Profile</dt><dd>' + esc(health.profile) + '</dd>' +
        '<dt>Case register</dt><dd>' + (health.checks.case_db ? "reachable" : "unreachable") + '</dd>' +
        (health.checks.intake_db !== undefined
          ? '<dt>Intake store</dt><dd>' + (health.checks.intake_db ? "reachable" : "unreachable") + '</dd>' : '') +
        '<dt>Names pre-finding</dt><dd>' + (health.publishes_names_before_finding ? "published" : "withheld") + '</dd>';
      $("#offlineNote").hidden = health.status === "ok";
    }).catch(function () {
      $("#serviceStatus").innerHTML = '<dt>Status</dt><dd style="color:var(--confirmed)">unreachable</dd>';
      $("#offlineNote").hidden = false;
    });
  }

  // --------------------------------------------------------------------- deployment shape
  /* The same page is served by every service, but a service only mounts the
   * routes its profile allows. On a departmental or investigator host the
   * citizen APIs do not exist, so the portal hides the views that would call
   * them rather than filling the screen with errors it cannot fix. */
  var CITIZEN_VIEWS = ["home", "report", "track", "me", "map", "news"];

  function applyProfile(profile) {
    var staffOnly = profile === "dept" || profile === "investigator";
    if (!staffOnly) return false;

    document.body.dataset.surface = "staff";
    $$("#drawerNav button").forEach(function (button) {
      if (CITIZEN_VIEWS.indexOf(button.dataset.go) > -1) button.hidden = true;
    });
    $$(".reportbar").forEach(function (bar) { bar.hidden = true; });
    document.body.style.paddingBottom = "0";
    $("#signinBtn").textContent = "Sign in";
    $("#footNote").textContent = profile === "investigator"
      ? "Ombudsman investigator service. Citizens report at the public portal."
      : "Departmental service. Citizens report at the public portal.";
    return true;
  }

  // --------------------------------------------------------------------- boot
  var LOADERS = {
    home: loadHome,
    track: renderSaved,
    me: loadMine,
    map: loadMap,
    news: loadBulletins,
    official: function () { if (session) loadDepartment(); else loadDemoCredentials(); },
    audit: loadAudit,
    about: loadStatus
  };

  // A session may survive a reload; ask the server who we are.
  function restoreSession() {
    if (!API.isSignedIn()) return Promise.resolve();
    return API.me().then(function (me) {
      session = { employee_code: me.employee_code, role: me.role, department: me.department };
      renderSession();
      $("#officialLogin").hidden = true;
      $("#officialDash").hidden = false;
    }).catch(function () { API.setToken(null); });
  }

  API.health()
    .then(function (health) { return health.profile; })
    .catch(function () { return "all"; })       // offline: assume the full portal
    .then(function (profile) {
      var staffOnly = applyProfile(profile);
      return restoreSession().then(function () {
        renderSession();
        if (staffOnly) {
          go("official");
          return;
        }
        loadHome();
        renderSaved();
        loadStatus();
        var initial = (location.hash || "#home").slice(1);
        if (document.getElementById("v-" + initial)) go(initial);
      });
    });
})();
