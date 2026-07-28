/* =========================================================================
   Morgan Trading Company — Live Display
   Renders a spot-price board, featured inventory, announcements ticker,
   and a full-screen "signage" mode for an in-store TV.

   Data flow:
     1. Loads live-display/data/config.json
     2. If config.pricesEndpoint / featuredEndpoint are set, fetches live data
        (expected shape documented in config.json). This is where the
        mtc-live-display data source plugs in.
     3. Falls back to config.spotFallback / featuredFallback so a screen is
        never blank.
   ========================================================================= */
(function () {
  "use strict";

  var CFG = null;
  var fmtUSD = function (n) {
    return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  var lastPrices = {};

  function load(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    });
  }

  function timeStr(d) {
    return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }

  /* ---- Spot prices ---------------------------------------------------- */
  function getSpot() {
    if (CFG.pricesEndpoint) {
      return load(CFG.pricesEndpoint).catch(function () { return CFG.spotFallback; });
    }
    return Promise.resolve(CFG.spotFallback);
  }

  function renderSpot(data) {
    var grid = document.getElementById("spot-grid");
    var sgSpots = document.getElementById("sg-spots");
    if (!data || !data.metals) return;
    var html = data.metals.map(function (m) {
      var prev = lastPrices[m.symbol];
      var flash = (prev !== undefined && prev !== m.price) ? " price-flash" : "";
      lastPrices[m.symbol] = m.price;
      var chg = Number(m.change || 0);
      var cls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
      var arrow = chg > 0 ? "▲" : chg < 0 ? "▼" : "•";
      var chgTxt = (chg > 0 ? "+" : "") + chg.toFixed(2) + "%";
      return '' +
        '<div class="spot-card">' +
          '<div class="spot-card__name">' + m.name + ' <span class="sym">' + m.symbol + '</span></div>' +
          '<div class="spot-card__price' + flash + '">' + fmtUSD(m.price) + '</div>' +
          '<div class="spot-card__meta">' +
            '<span class="spot-card__unit">per troy ' + (m.unit || "oz") + '</span>' +
            '<span class="chg ' + cls + '">' + arrow + ' ' + chgTxt + '</span>' +
          '</div>' +
        '</div>';
    }).join("");
    if (grid) grid.innerHTML = html;
    if (sgSpots) sgSpots.innerHTML = html;

    var asOf = data.asOf ? new Date(data.asOf) : new Date();
    document.querySelectorAll("[data-updated]").forEach(function (el) {
      el.textContent = "Updated " + timeStr(asOf);
    });
  }

  /* ---- Featured inventory -------------------------------------------- */
  var featured = [];
  function getFeatured() {
    if (CFG.featuredEndpoint) {
      return load(CFG.featuredEndpoint).catch(function () { return CFG.featuredFallback; });
    }
    return Promise.resolve(CFG.featuredFallback);
  }
  function renderFeatured(items) {
    featured = items || [];
    var grid = document.getElementById("featured-grid");
    if (!grid) return;
    grid.innerHTML = featured.map(function (f) {
      return '' +
        '<div class="feat-card">' +
          '<div class="feat-card__img">' + (f.tag ? '<span class="feat-card__tag">' + f.tag + '</span>' : "") +
            '<img src="' + f.image + '" alt="' + f.title + '"></div>' +
          '<div class="feat-card__body">' +
            '<div class="feat-card__cat">' + (f.category || "") + '</div>' +
            '<div class="feat-card__title">' + f.title + '</div>' +
            '<div class="feat-card__price">' + (f.price || "") + '</div>' +
          '</div>' +
        '</div>';
    }).join("");
  }

  /* ---- Ticker --------------------------------------------------------- */
  function renderTicker() {
    var items = (CFG.announcements || []).map(function (a) {
      return '<span class="ticker__item"><span class="b">◆</span>' + a + '</span>';
    }).join("");
    document.querySelectorAll("[data-ticker]").forEach(function (t) {
      t.innerHTML = '<div class="ticker__track">' + items + items + '</div>';
    });
  }

  /* ---- Store status --------------------------------------------------- */
  function renderStatus() {
    var st = window.MTC ? window.MTC.storeStatus() : { open: false };
    document.querySelectorAll("[data-ld-status]").forEach(function (el) {
      el.className = "status-badge " + (st.open ? "is-open" : "is-closed") + " ld-status";
      el.innerHTML = '<span class="dot"></span>' + (st.open ? "Open Now" : "Closed");
    });
  }

  /* ---- Signage mode --------------------------------------------------- */
  var sgTimer = null, sgClock = null, sgIndex = 0;
  function enterSignage() {
    document.body.classList.add("signage");
    try { if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen(); } catch (e) {}
    startSignage();
    history.replaceState(null, "", "#signage");
  }
  function exitSignage() {
    document.body.classList.remove("signage");
    try { if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen(); } catch (e) {}
    if (sgTimer) clearInterval(sgTimer);
    if (sgClock) clearInterval(sgClock);
    history.replaceState(null, "", location.pathname);
  }
  function tickClock() {
    var el = document.getElementById("sg-clock");
    if (!el) return;
    var d = new Date();
    el.innerHTML = timeStr(d) + "<small>" + d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }) + "</small>";
  }
  function rotateFeature() {
    var stage = document.getElementById("sg-feature");
    if (!stage || !featured.length) return;
    var f = featured[sgIndex % featured.length];
    sgIndex++;
    stage.classList.remove("show");
    setTimeout(function () {
      stage.innerHTML = '<img src="' + f.image + '" alt=""><div class="cat">' + (f.category || "") +
        '</div><div class="t">' + f.title + '</div><div class="pr">' + (f.price || "") + '</div>';
      stage.classList.add("show");
    }, 250);
  }
  function startSignage() {
    tickClock(); sgClock = setInterval(tickClock, 15000);
    rotateFeature();
    var every = (CFG.signageRotateSeconds || 12) * 1000;
    sgTimer = setInterval(rotateFeature, every);
    renderStatus();
  }

  /* ---- Refresh loop --------------------------------------------------- */
  function refresh() {
    getSpot().then(renderSpot);
    renderStatus();
  }

  /* ---- Init ----------------------------------------------------------- */
  function init() {
    load("data/config.json").then(function (cfg) {
      CFG = cfg;
      renderTicker();
      renderStatus();
      refresh();
      getFeatured().then(function (items) {
        renderFeatured(items);
        if (location.hash === "#signage") enterSignage();
      });
      var every = (CFG.refreshSeconds || 60) * 1000;
      setInterval(refresh, every);
    }).catch(function (e) {
      var err = document.getElementById("ld-error");
      if (err) err.style.display = "block";
      console.error("Live Display config failed to load:", e);
    });

    // Controls
    document.addEventListener("click", function (e) {
      var t = e.target.closest("[data-action]");
      if (!t) return;
      if (t.getAttribute("data-action") === "signage") enterSignage();
      if (t.getAttribute("data-action") === "exit-signage") exitSignage();
    });
    window.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && document.body.classList.contains("signage")) exitSignage();
      if (e.key.toLowerCase() === "f" && !/input|textarea|select/i.test(e.target.tagName) && !document.body.classList.contains("signage")) enterSignage();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
