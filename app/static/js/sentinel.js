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

  renderCharts();
  renderMermaid();

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
