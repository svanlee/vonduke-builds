/* =========================================================================
   Morgan Trading Company — shared UI
   Renders the header + footer (single source of truth) and wires up
   the mobile menu, scroll reveals, open/closed status, and active nav.
   Each page sets  <body data-page="home">  to mark the current section.
   Pages inside /live-display/ set  data-base=".."  so links resolve.
   ========================================================================= */
(function () {
  "use strict";

  var BASE = document.body.getAttribute("data-base") || ".";
  var PAGE = document.body.getAttribute("data-page") || "";
  var p = function (path) { return BASE + "/" + path; };

  // Two store locations. Gaylord is the flagship (index 0) used in the header.
  var LOCATIONS = [
    {
      name: "Gaylord",
      tag: "Flagship",
      phone: "(989) 448-2236",
      phoneHref: "tel:+19894482236",
      street: "2484 S. Otsego Ave",
      city: "Gaylord, MI 49734",
      mapHref: "https://maps.google.com/?q=2484+S+Otsego+Ave+Gaylord+MI+49734",
      mapEmbed: "https://www.google.com/maps?q=2484+S+Otsego+Ave+Gaylord+MI+49734&output=embed"
    },
    {
      name: "Alpena",
      tag: "2nd Location",
      phone: "(989) 340-2010",
      phoneHref: "tel:+19893402010",
      street: "486 S. Ripley Blvd",
      city: "Alpena, MI 49707",
      mapHref: "https://maps.google.com/?q=486+S+Ripley+Blvd+Alpena+MI+49707",
      mapEmbed: "https://www.google.com/maps?q=486+S+Ripley+Blvd+Alpena+MI+49707&output=embed"
    }
  ];

  var BIZ = {
    name: "Morgan Trading Company",
    phone: LOCATIONS[0].phone,
    phoneHref: LOCATIONS[0].phoneHref,
    facebook: "https://www.facebook.com/morgantradingcompany",
    email: "info@morgantradingcompany.com",
    locations: LOCATIONS
  };

  // ---- Form delivery ---------------------------------------------------
  // Forms deliver to the shop's inbox via FormSubmit.co — no API key, no
  // backend. The FIRST time a form is submitted, FormSubmit emails
  // `email` a one-time confirmation link; click it once and every
  // submission after that lands in the inbox. If the request ever fails,
  // the form falls back to opening the visitor's email app.
  var FORMS = {
    email: "info@morgantradingcompany.com",
    endpoint: "https://formsubmit.co/ajax/info@morgantradingcompany.com"
  };

  // ---- Analytics -------------------------------------------------------
  // Optional. To turn on Google Analytics 4, paste your Measurement ID
  // (looks like "G-XXXXXXXXXX"). Left blank = no analytics, no cookies.
  var ANALYTICS = { ga4: "" };
  (function () {
    var id = ANALYTICS.ga4;
    if (!id || id.indexOf("G-") !== 0) return;
    var s = document.createElement("script");
    s.async = true; s.src = "https://www.googletagmanager.com/gtag/js?id=" + id;
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    function gtag() { window.dataLayer.push(arguments); }
    gtag("js", new Date()); gtag("config", id);
  })();

  // ---- Live Google reviews (optional) ----------------------------------
  // Turn on the live Google Reviews widget by filling BOTH values below:
  //   placeId — your Google Place ID (looks like "ChIJ...").
  //   apiKey  — a Google Cloud API key with the "Places API (New)" enabled,
  //             RESTRICTED to HTTP referrer = your site's domain so it can't
  //             be abused. (Google ties this to your billing account; the
  //             free monthly credit covers a small business's traffic.)
  // Leave either blank and the page keeps the static verified-rating badge.
  // Responses are cached in the browser for 12h to stay well inside quota.
  var REVIEWS = { placeId: "", apiKey: "", minRating: 4, max: 6 };

  // Primary navigation (label, page-key, href)
  var NAV = [
    { label: "Home",  key: "home",  href: p("index.html") },
    { label: "Buy",   key: "buy",   href: p("buy.html") },
    { label: "Sell",  key: "sell",  href: p("sell.html") },
    { label: "Trade", key: "trade", href: p("trade.html") },
    { label: "Shop",  key: "shop",  href: p("shop.html"), children: [
        { label: "Firearms & Ammo",      href: p("firearms.html") },
        { label: "Gold, Silver & Coins", href: p("gold-silver-coins.html") },
        { label: "Jewelry & Diamonds",   href: p("jewelry.html") },
        { label: "Luxury Watches",       href: p("watches.html") },
        { label: "Luxury Handbags",      href: p("luxury-handbags.html") },
        { label: "General Merchandise",  href: p("general-merchandise.html") }
    ]},
    { label: "Layaway",  key: "layaway",  href: p("layaway.html") },
    { label: "Live Display", key: "live", href: p("live-display/index.html") },
    { label: "About",    key: "about",    href: p("about.html") },
    { label: "Contact",  key: "contact",  href: p("contact.html") }
  ];

  var LOGO = p("assets/img/mtc-logo-full.png") + "?v=3";

  /* ---- SVG icons ------------------------------------------------------ */
  var I = {
    phone: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.94.36 1.85.68 2.72a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.36-1.36a2 2 0 0 1 2.11-.45c.87.32 1.78.55 2.72.68A2 2 0 0 1 22 16.92z"/></svg>',
    pin: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
    clock: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    fb: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M22 12a10 10 0 1 0-11.56 9.88v-6.99H7.9V12h2.54V9.8c0-2.5 1.49-3.89 3.78-3.89 1.09 0 2.24.2 2.24.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56V12h2.78l-.44 2.89h-2.34v6.99A10 10 0 0 0 22 12z"/></svg>',
    check: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    arrow: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    sun: '<svg class="icon-sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg class="icon-moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
  };

  /* ---- Store hours (0=Sun .. 6=Sat) ---------------------------------- */
  var HOURS = [
    { d: "Sunday",    open: null, close: null },
    { d: "Monday",    open: 10,  close: 18 },
    { d: "Tuesday",   open: 10,  close: 18 },
    { d: "Wednesday", open: 10,  close: 18 },
    { d: "Thursday",  open: 10,  close: 18 },
    { d: "Friday",    open: 10,  close: 18 },
    { d: "Saturday",  open: 10,  close: 18 }
  ];
  function storeStatus() {
    var now = new Date();
    var h = HOURS[now.getDay()];
    var hourFrac = now.getHours() + now.getMinutes() / 60;
    var open = h.open !== null && hourFrac >= h.open && hourFrac < h.close;
    return { open: open, hours: h };
  }
  window.MTC = { BIZ: BIZ, HOURS: HOURS, storeStatus: storeStatus, icons: I };

  /* ---- Header --------------------------------------------------------- */
  function renderHeader() {
    var links = NAV.map(function (item) {
      var current = item.key === PAGE ? ' aria-current="page"' : "";
      if (item.children) {
        var sub = item.children.map(function (c) {
          return '<li><a href="' + c.href + '">' + c.label + "</a></li>";
        }).join("");
        return '<li class="has-menu"><a href="' + item.href + '"' + current + ">" + item.label +
               '</a><ul class="submenu">' + sub + "</ul></li>";
      }
      return '<li><a href="' + item.href + '"' + current + ">" + item.label + "</a></li>";
    }).join("");

    return '' +
    '<a class="skip-link" href="#main">Skip to content</a>' +
    '<header class="site-header"><div class="container"><nav class="nav" aria-label="Primary">' +
      '<a class="brand" href="' + p("index.html") + '" aria-label="' + BIZ.name + ' — Buy Sell Trade — home">' +
        '<img class="brand__logo" src="' + LOGO + '" alt="Morgan Trading Company — Buy Sell Trade">' +
      '</a>' +
      '<ul class="nav__links" id="nav-links">' + links + '</ul>' +
      '<div class="nav__cta">' +
        '<button class="theme-toggle" id="theme-toggle" aria-label="Toggle dark mode" title="Toggle dark mode">' + I.sun + I.moon + '</button>' +
        '<a class="nav__phone nav__phone--header" href="' + BIZ.phoneHref + '">' + I.phone + BIZ.phone + '</a>' +
        '<a class="btn btn--gold nav__sell" href="' + p("sell.html") + '">Get an Offer</a>' +
        '<button class="nav__toggle" id="nav-toggle" aria-label="Menu" aria-expanded="false" aria-controls="nav-links"><span></span></button>' +
      '</div>' +
    '</nav></div></header>';
  }

  /* ---- Footer --------------------------------------------------------- */
  function renderFooter() {
    var st = storeStatus();
    var badge = st.open
      ? '<span class="status-badge is-open"><span class="dot"></span>Open now</span>'
      : '<span class="status-badge is-closed"><span class="dot"></span>Closed now</span>';
    return '' +
    '<footer class="site-footer"><div class="container">' +
      '<div class="footer-grid">' +
        '<div>' +
          '<div class="footer-brand">' +
            '<div class="footer-brand__name">Morgan Trading Company</div>' +
            '<div class="footer-brand__tag">Buy · Sell · Trade</div>' +
          '</div>' +
          '<p style="max-width:34ch">Northern Michigan’s trusted buy, sell &amp; trade destination — firearms, precious metals, jewelry, and luxury goods.</p>' +
          badge +
          '<div class="social" style="margin-top:1.2rem">' +
            '<a href="' + BIZ.facebook + '" aria-label="Facebook" target="_blank" rel="noopener">' + I.fb + '</a>' +
          '</div>' +
        '</div>' +
        '<div><h4>Explore</h4><ul class="footer-links">' +
          '<li><a href="' + p("buy.html") + '">Buy</a></li>' +
          '<li><a href="' + p("sell.html") + '">Sell</a></li>' +
          '<li><a href="' + p("trade.html") + '">Trade</a></li>' +
          '<li><a href="' + p("layaway.html") + '">Layaway</a></li>' +
          '<li><a href="' + p("live-display/index.html") + '">Live Display</a></li>' +
          '<li><a href="' + p("faq.html") + '">FAQ</a></li>' +
        '</ul></div>' +
        '<div><h4>Shop</h4><ul class="footer-links">' +
          '<li><a href="' + p("firearms.html") + '">Firearms &amp; Ammo</a></li>' +
          '<li><a href="' + p("gold-silver-coins.html") + '">Gold, Silver &amp; Coins</a></li>' +
          '<li><a href="' + p("jewelry.html") + '">Jewelry &amp; Diamonds</a></li>' +
          '<li><a href="' + p("watches.html") + '">Luxury Watches</a></li>' +
          '<li><a href="' + p("luxury-handbags.html") + '">Luxury Handbags</a></li>' +
        '</ul></div>' +
        '<div><h4>Two Locations</h4><ul class="footer-links">' +
          LOCATIONS.map(function (L) {
            return '<li style="margin-bottom:.6rem">' +
              '<strong style="color:#fff">' + L.name + '</strong><br>' +
              '<a href="' + L.mapHref + '" target="_blank" rel="noopener">' + L.street + '<br>' + L.city + '</a><br>' +
              '<a href="' + L.phoneHref + '">' + L.phone + '</a></li>';
          }).join("") +
          '<li style="color:#9a948a">Mon–Sat: 10AM–6PM · Sun: Closed</li>' +
          '<li><a href="mailto:' + BIZ.email + '">' + BIZ.email + '</a></li>' +
          '<li style="margin-top:.6rem"><a href="' + p("faq.html") + '">FAQ</a> · <a href="' + p("careers.html") + '">Careers</a> · <a href="' + p("disclosures.html") + '">Disclosures</a></li>' +
        '</ul></div>' +
      '</div>' +
      '<div class="footer-bottom">' +
        '<span>&copy; ' + new Date().getFullYear() + ' Morgan Trading Company. All rights reserved.</span>' +
        '<span>FFL Dealer · Licensed Precious Metal &amp; Coin Dealer · Gaylord &amp; Alpena, Michigan</span>' +
      '</div>' +
    '</div></footer>';
  }

  /* ---- Mount + interactions ------------------------------------------ */
  function mount() {
    var head = document.getElementById("site-header-mount");
    var foot = document.getElementById("site-footer-mount");
    if (head) head.outerHTML = renderHeader();
    if (foot) foot.outerHTML = renderFooter();

    // Theme toggle (init script in <head> already set the initial theme)
    var themeBtn = document.getElementById("theme-toggle");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
        var next = cur === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        try { localStorage.setItem("mtc-theme", next); } catch (e) {}
      });
    }

    // Contact / lead forms → Web3Forms (with mailto fallback)
    document.querySelectorAll("form.mtc-form").forEach(function (form) {
      var status = form.querySelector(".form-status");
      var btn = form.querySelector('[type="submit"]');
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (form.reportValidity && !form.reportValidity()) return;
        var data = new FormData(form);
        var subject = form.getAttribute("data-subject") || "New message from the website";
        function show(msg, ok) {
          if (!status) return;
          status.hidden = false;
          status.textContent = msg;
          status.style.color = ok ? "var(--success)" : "var(--danger)";
        }
        function mailtoFallback() {
          var body = [];
          data.forEach(function (v, k) { if (k.charAt(0) !== "_") body.push(k + ": " + v); });
          window.location.href = "mailto:" + FORMS.email +
            "?subject=" + encodeURIComponent(subject) +
            "&body=" + encodeURIComponent(body.join("\n"));
        }
        // FormSubmit control fields
        data.append("_subject", subject);
        data.append("_template", "table");
        data.append("_captcha", "false");
        var orig = btn ? btn.textContent : "";
        if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
        fetch(FORMS.endpoint, { method: "POST", body: data, headers: { "Accept": "application/json" } })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && (j.success === true || j.success === "true")) {
              form.reset();
              show("Thanks! We got your message and will be in touch shortly. For the fastest response, call " + BIZ.phone + ".", true);
            } else { throw new Error("send failed"); }
          })
          .catch(function () {
            mailtoFallback();
            show("Opening your email app to send this to us — or call " + BIZ.phone + ".", true);
          })
          .then(function () { if (btn) { btn.disabled = false; btn.textContent = orig; } });
      });
    });

    // Mobile menu
    var toggle = document.getElementById("nav-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var open = document.body.classList.toggle("menu-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
      document.getElementById("nav-links").addEventListener("click", function (e) {
        if (e.target.tagName === "A" && !e.target.closest(".has-menu > a")) {
          document.body.classList.remove("menu-open");
          toggle.setAttribute("aria-expanded", "false");
        }
      });
      window.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { document.body.classList.remove("menu-open"); toggle.setAttribute("aria-expanded","false"); }
      });
    }

    // Scroll reveal
    var els = document.querySelectorAll("[data-reveal]");
    if ("IntersectionObserver" in window && els.length) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add("is-visible"); io.unobserve(en.target); }
        });
      }, { threshold: 0.12 });
      els.forEach(function (el) { io.observe(el); });
    } else {
      els.forEach(function (el) { el.classList.add("is-visible"); });
    }

    // Fill any [data-store-status] placeholders
    document.querySelectorAll("[data-store-status]").forEach(function (el) {
      var st = storeStatus();
      el.className = "status-badge " + (st.open ? "is-open" : "is-closed");
      el.innerHTML = '<span class="dot"></span>' + (st.open ? "Open now" : "Closed · Opens " +
        (st.hours.open === null ? "Mon 10AM" : st.hours.open + "AM"));
    });

    // Hero slideshow (crossfade + dots + autoplay)
    var heroWrap = document.querySelector("[data-hero-slides]");
    if (heroWrap) {
      var slides = Array.prototype.slice.call(heroWrap.querySelectorAll("img"));
      if (slides.length > 1) {
        var idx = 0, timer = null;
        var INTERVAL = 5500;
        // dots
        var dots = document.createElement("div");
        dots.className = "hero__dots";
        slides.forEach(function (_, i) {
          var b = document.createElement("button");
          b.type = "button";
          b.setAttribute("aria-label", "Show slide " + (i + 1));
          if (i === 0) b.className = "is-active";
          b.addEventListener("click", function () { go(i, true); });
          dots.appendChild(b);
        });
        heroWrap.parentNode.appendChild(dots);
        var dotEls = dots.querySelectorAll("button");
        function go(n, user) {
          slides[idx].classList.remove("is-active");
          dotEls[idx].classList.remove("is-active");
          idx = (n + slides.length) % slides.length;
          slides[idx].classList.add("is-active");
          dotEls[idx].classList.add("is-active");
          if (user) restart();
        }
        function next() { go(idx + 1); }
        function start() {
          var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          if (!reduce) timer = setInterval(next, INTERVAL);
        }
        function restart() { if (timer) clearInterval(timer); start(); }
        start();
        // pause when tab hidden
        document.addEventListener("visibilitychange", function () {
          if (document.hidden) { if (timer) clearInterval(timer); }
          else { restart(); }
        });
      }
    }

    // Live Google reviews (no-op unless configured)
    loadReviews();
  }

  /* ---- Live Google reviews widget ------------------------------------ */
  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function starRow(n) {
    var r = Math.round(n), out = "";
    for (var i = 0; i < 5; i++) out += i < r ? "★" : "☆";
    return out;
  }
  function reviewsMarkup(data) {
    var reviews = (data.reviews || []).filter(function (r) {
      return (r.rating || 0) >= (REVIEWS.minRating || 0);
    }).slice(0, REVIEWS.max || 6);
    if (!reviews.length) return "";
    var mapsUri = data.googleMapsUri || "https://www.google.com/maps/search/?api=1&query=Morgan%20Trading%20Company%20Gaylord%20MI";
    var rating = (data.rating || 0).toFixed(1);
    var count = data.userRatingCount || 0;

    var head =
      '<div class="reviews-live__head">' +
        '<div class="rating-score"><span class="rating-num">' + escHtml(rating) + '</span><span class="rating-outof">out of 5</span></div>' +
        '<div class="stars stars--lg" role="img" aria-label="' + escHtml(rating) + ' out of 5 stars">' + starRow(data.rating || 0) + '</div>' +
        '<p class="rating-meta">Based on <strong>' + escHtml(count) + '</strong> Google reviews</p>' +
      '</div>';

    var cards = reviews.map(function (r) {
      var author = (r.authorAttribution && r.authorAttribution.displayName) || "Google user";
      var uri = (r.authorAttribution && r.authorAttribution.uri) || mapsUri;
      var initial = escHtml(author.trim().charAt(0).toUpperCase() || "G");
      var when = escHtml(r.relativePublishTimeDescription || "");
      var text = (r.text && r.text.text) || r.originalText && r.originalText.text || "";
      return '' +
        '<figure class="review-card">' +
          '<div class="review-card__top">' +
            '<span class="review-card__avatar" aria-hidden="true">' + initial + '</span>' +
            '<div><a class="review-card__name" href="' + escHtml(uri) + '" target="_blank" rel="noopener nofollow">' + escHtml(author) + '</a>' +
            '<div class="review-card__meta"><span class="stars" aria-label="' + (r.rating || 0) + ' star review">' + starRow(r.rating || 0) + '</span>' + (when ? '<span class="review-card__when">' + when + '</span>' : '') + '</div></div>' +
          '</div>' +
          '<blockquote class="review-card__text">' + escHtml(text) + '</blockquote>' +
        '</figure>';
    }).join("");

    return head +
      '<div class="reviews-live__grid">' + cards + '</div>' +
      '<div class="reviews-live__foot"><a class="btn btn--gold" href="' + escHtml(mapsUri) + '" target="_blank" rel="noopener">Read all reviews on Google</a>' +
      '<span class="reviews-live__attrib">Reviews from Google</span></div>';
  }
  function renderReviews(mountEl, data) {
    var html = reviewsMarkup(data);
    if (!html) return false;
    mountEl.innerHTML = html;
    mountEl.hidden = false;
    // Hide the static fallback badge now that live reviews are showing
    var badge = document.querySelector(".rating-showcase");
    if (badge) badge.hidden = true;
    return true;
  }
  function loadReviews() {
    var mountEl = document.getElementById("reviews-live");
    if (!mountEl) return;
    if (!REVIEWS.placeId || !REVIEWS.apiKey) return; // not configured → keep badge

    var CACHE_KEY = "mtc-reviews-" + REVIEWS.placeId;
    var TTL = 12 * 60 * 60 * 1000; // 12h
    try {
      var cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
      if (cached && (Date.now() - cached.t) < TTL && cached.d) {
        if (renderReviews(mountEl, cached.d)) return;
      }
    } catch (e) {}

    var url = "https://places.googleapis.com/v1/places/" + encodeURIComponent(REVIEWS.placeId);
    fetch(url, {
      headers: {
        "X-Goog-Api-Key": REVIEWS.apiKey,
        "X-Goog-FieldMask": "rating,userRatingCount,googleMapsUri,reviews"
      }
    })
    .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
    .then(function (data) {
      if (!data || !data.reviews) throw new Error("no reviews in response");
      try { localStorage.setItem(CACHE_KEY, JSON.stringify({ t: Date.now(), d: data })); } catch (e) {}
      renderReviews(mountEl, data);
    })
    .catch(function (err) {
      // Silent: the static verified-rating badge remains as the fallback.
      if (window.console) console.warn("Live reviews unavailable:", err.message);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else { mount(); }
})();
