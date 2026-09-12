/* CivicLens API client.
 *
 * One place that knows how to talk to the server, so every view gets the same
 * error handling. Two rules it enforces:
 *
 *   1. Errors surface the server's own problem+json `detail`. The API writes
 *      messages a citizen can act on; repeating them beats inventing new ones.
 *   2. The officer token lives in sessionStorage, not localStorage - it dies
 *      with the tab, so a shared counter machine does not keep a session alive
 *      for the next person who sits down.
 */
(function (global) {
  "use strict";

  var BASE = (global.CIVICLENS_API_BASE || "").replace(/\/$/, "");
  var TOKEN_KEY = "civiclens.session";

  function token() {
    try { return sessionStorage.getItem(TOKEN_KEY) || null; } catch (e) { return null; }
  }
  function setToken(value) {
    try {
      if (value) sessionStorage.setItem(TOKEN_KEY, value);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* private mode: the session simply does not persist */ }
  }

  function ApiError(message, status, body) {
    this.name = "ApiError";
    this.message = message;
    this.status = status || 0;
    this.body = body || null;
    this.fields = (body && body.errors) || [];
    this.requestId = (body && body.request_id) || null;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function request(method, path, options) {
    options = options || {};
    var headers = {};
    if (options.auth !== false && token()) headers.Authorization = "Bearer " + token();

    var init = { method: method, headers: headers };
    if (options.body instanceof FormData) {
      init.body = options.body;                      // let the browser set the boundary
    } else if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }

    return fetch(BASE + path, init).then(function (response) {
      var isJson = (response.headers.get("content-type") || "").indexOf("json") > -1;
      return (isJson ? response.json() : response.text()).then(function (body) {
        if (response.ok) return body;

        if (response.status === 401 && token()) {
          setToken(null);
          global.dispatchEvent(new CustomEvent("civiclens:signed-out"));
        }
        var detail = (body && body.detail) || "That request could not be completed.";
        throw new ApiError(detail, response.status, body);
      });
    }, function () {
      // fetch rejects only on a network-level failure.
      throw new ApiError(
        "Could not reach the register. Check your connection and try again - nothing was sent.",
        0, null
      );
    });
  }

  global.API = {
    ApiError: ApiError,
    token: token,
    setToken: setToken,
    isSignedIn: function () { return !!token(); },

    // ------------------------------------------------------------ public
    health: function () { return request("GET", "/health/ready", { auth: false }); },
    stats: function () { return request("GET", "/api/stats", { auth: false }); },
    heatmap: function () { return request("GET", "/api/heatmap", { auth: false }); },
    ledger: function (limit) { return request("GET", "/api/ledger?limit=" + (limit || 12), { auth: false }); },
    verifyLedger: function () { return request("GET", "/api/ledger/verify", { auth: false }); },

    register: function (params) {
      var query = new URLSearchParams();
      Object.keys(params || {}).forEach(function (key) {
        if (params[key] !== undefined && params[key] !== null && params[key] !== "") {
          query.set(key, params[key]);
        }
      });
      var qs = query.toString();
      return request("GET", "/api/register" + (qs ? "?" + qs : ""), { auth: false });
    },

    trackCase: function (caseNo) {
      return request("GET", "/api/cases/" + encodeURIComponent(caseNo.trim().toUpperCase()), { auth: false });
    },

    fileReport: function (payload) {
      return request("POST", "/api/reports", { body: payload, auth: false });
    },

    attachEvidence: function (caseNo, uploadToken, file) {
      var form = new FormData();
      form.append("file", file);
      return request(
        "POST",
        "/api/reports/" + encodeURIComponent(caseNo) + "/evidence?upload_token=" + encodeURIComponent(uploadToken),
        { body: form, auth: false }
      );
    },

    // ------------------------------------------------------------ officials
    login: function (employeeCode, password, otp) {
      return request("POST", "/api/auth/official/login", {
        auth: false,
        body: { employee_code: employeeCode, password: password, otp: otp }
      }).then(function (body) {
        setToken(body.access_token);
        return body;
      });
    },
    logout: function () {
      return request("POST", "/api/auth/official/logout").catch(function () { /* revoke best effort */ })
        .then(function () { setToken(null); });
    },
    me: function () { return request("GET", "/api/auth/me"); },
    auditTrail: function (limit) { return request("GET", "/api/auth/audit?limit=" + (limit || 40)); },

    deptSummary: function () { return request("GET", "/api/dept/summary"); },
    deptCases: function () { return request("GET", "/api/dept/cases"); },
    deptCase: function (caseNo) { return request("GET", "/api/dept/cases/" + encodeURIComponent(caseNo)); },
    deptReply: function (caseNo, note) {
      return request("POST", "/api/dept/cases/" + encodeURIComponent(caseNo) + "/reply", { body: { note: note } });
    },
    evidenceManifest: function (sha256) { return request("GET", "/api/dept/evidence/" + sha256); },

    // ------------------------------------------------------------ demo affordance
    // Available only while the server runs with DEMO_MODE on; production
    // refuses to start with it enabled.
    demoAccounts: function () { return request("GET", "/api/demo/accounts", { auth: false }); }
  };
})(window);
