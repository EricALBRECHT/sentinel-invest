(function () {
  if (window.htmx) {
    window.htmx.config.allowEval = false;
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target || !target.matches || !target.matches("[data-auto-submit]")) {
      return;
    }
    if (target.form) {
      target.form.requestSubmit();
    }
  });

  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest
      ? event.target.closest("[data-sentinel-refresh]")
      : null;
    if (!button) {
      return;
    }
    event.preventDefault();
    triggerSentinelRefresh(button);
  });

  document.body.addEventListener("htmx:beforeRequest", function (event) {
    var elt = event.target;
    if (!elt || !elt.classList || !elt.classList.contains("sentinel-live")) {
      return;
    }
    elt.classList.add("is-refreshing");
    setLiveStatus("Actualisation…", false);
  });

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var elt = event.target;
    if (!elt || !elt.classList || !elt.classList.contains("sentinel-live")) {
      return;
    }
    elt.classList.remove("is-refreshing");
    var detail = event.detail || {};
    var xhr = detail.xhr;
    if (detail.successful || (xhr && xhr.status >= 200 && xhr.status < 300)) {
      clearRefreshBusy();
      stampLiveUpdate();
      setLiveStatus("", true);
      return;
    }
    clearRefreshBusy();
    setLiveStatus("Échec actualisation", false);
  });

  document.body.addEventListener("htmx:responseError", function () {
    clearRefreshBusy();
    setLiveStatus("Échec actualisation", false);
  });

  document.body.addEventListener("htmx:sendError", function () {
    clearRefreshBusy();
    setLiveStatus("Échec actualisation", false);
  });

  document.body.addEventListener("htmx:swapError", function () {
    clearRefreshBusy();
    setLiveStatus("Échec actualisation", false);
  });

  renderCharts();
  renderMermaid();

  function triggerSentinelRefresh(button) {
    if (button) {
      button.classList.add("is-busy");
      button.setAttribute("aria-busy", "true");
      if (!button.getAttribute("data-label")) {
        button.setAttribute("data-label", button.textContent || "Actualiser");
      }
      button.textContent = "Actualisation…";
    }
    setLiveStatus("Actualisation…", false);
    document.body.dispatchEvent(new CustomEvent("sentinelRefresh", { bubbles: true }));
  }

  function clearRefreshBusy() {
    var buttons = document.querySelectorAll("[data-sentinel-refresh]");
    for (var i = 0; i < buttons.length; i += 1) {
      var button = buttons[i];
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
      var label = button.getAttribute("data-label");
      if (label) {
        button.textContent = label;
      }
    }
  }

  function stampLiveUpdate() {
    var stamp = document.getElementById("sentinel-last-update");
    var meta = document.getElementById("sentinel-live-meta");
    if (!stamp) {
      return;
    }
    var now = new Date();
    var label =
      String(now.getHours()).padStart(2, "0") +
      ":" +
      String(now.getMinutes()).padStart(2, "0") +
      ":" +
      String(now.getSeconds()).padStart(2, "0");
    stamp.textContent = label;
    if (meta) {
      meta.setAttribute("data-refreshed-at", label);
    }
  }

  function setLiveStatus(message, hide) {
    var node = document.getElementById("sentinel-live-status");
    if (!node) {
      return;
    }
    if (hide || !message) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = message;
  }

  function renderCharts() {
    var node = document.getElementById("chart-payload");
    if (!node || typeof Chart === "undefined") {
      return;
    }
    var data = JSON.parse(node.textContent || "{}");
    var price = document.getElementById("price-chart");
    var volume = document.getElementById("volume-chart");
    var grid = { color: "#2d343d" };
    var dateTicks = {
      color: "#8b97a6",
      maxTicksLimit: 8,
      callback: function (value) {
        var label = this.getLabelForValue(value);
        var match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(label));
        return match ? match[3] + "/" + match[2] + "/" + match[1] : label;
      }
    };
    var numberTicks = {
      color: "#8b97a6",
      callback: function (value) {
        var number = Number(value);
        if (!isFinite(number)) return value;
        return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 }).format(number);
      }
    };
    if (price) {
      new Chart(price, {
        type: "line",
        data: {
          labels: data.labels || [],
          datasets: [
            { label: "Cours", data: data.price || [], borderColor: "#8eb6e0", pointRadius: 0, borderWidth: 1.5 },
            { label: "SMA20", data: data.sma20 || [], borderColor: "#d7a15a", pointRadius: 0, borderWidth: 1 },
            { label: "SMA50", data: data.sma50 || [], borderColor: "#7dcea0", pointRadius: 0, borderWidth: 1 },
            { label: "SMA200", data: data.sma200 || [], borderColor: "#e07a7a", pointRadius: 0, borderWidth: 1 }
          ]
        },
        options: {
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#e7ecf1" } } },
          scales: { x: { ticks: dateTicks, grid: grid }, y: { ticks: numberTicks, grid: grid } }
        }
      });
    }
    if (volume) {
      new Chart(volume, {
        type: "bar",
        data: {
          labels: data.labels || [],
          datasets: [{ label: "Volume", data: data.volume || [], backgroundColor: "#8eb6e0" }]
        },
        options: {
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#e7ecf1" } } },
          scales: { x: { ticks: dateTicks, grid: grid }, y: { ticks: numberTicks, grid: grid, beginAtZero: true } }
        }
      });
    }
  }

  function renderMermaid() {
    if (!window.mermaid) {
      return;
    }
    var nodes = document.querySelectorAll("pre.mermaid");
    if (!nodes.length) {
      return;
    }
    window.mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
    window.mermaid.run({ nodes: nodes });
  }
})();
