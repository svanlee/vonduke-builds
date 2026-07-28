/**
 * script.js — Morgan Trading Company Live TV Display
 * Vanilla ES6. Reads from window.MTC_CONFIG and window.MTCPrices.
 * Responsibilities: clock, spot prices (with offline fallback),
 * slideshow (images + videos + seasonal promo slides), ticker.
 */
(function () {
  "use strict";
  const CFG = window.MTC_CONFIG;

  function applyBranding() {
    const root = document.documentElement.style;
    root.setProperty("--bg", CFG.theme.background);
    root.setProperty("--gold", CFG.theme.gold);
    root.setProperty("--silver", CFG.theme.silver);
    root.setProperty("--white", CFG.theme.white);
    root.setProperty("--divider", CFG.theme.divider);
    root.setProperty("--accent", CFG.theme.accent);

    document.getElementById("business-name").textContent = CFG.business.name;
    document.getElementById("business-tagline").textContent = CFG.business.tagline;
    document.title = `${CFG.business.name} — Live Display`;

    if (CFG.business.logoPath) {
      const h1 = document.getElementById("business-name");
      const img = document.createElement("img");
      img.src = CFG.business.logoPath;
      img.alt = CFG.business.name;
      img.style.maxHeight = "2.4em";
      img.style.display = "block";
      img.style.margin = "0 auto 0.3em";
      h1.parentNode.insertBefore(img, h1);
    }
  }

  // ---------------- CLOCK ----------------
  function startClock() {
    const timeEl = document.getElementById("clock-time");
    const meridiemEl = document.getElementById("clock-meridiem");
    const dateEl = document.getElementById("clock-date");
    const { locale, hour12, showSeconds, dateFormatOptions } = CFG.clock;

    function render() {
      const now = new Date();
      const timeOptions = { hour: "2-digit", minute: "2-digit", hour12 };
      if (showSeconds) timeOptions.second = "2-digit";

      let formatted = new Intl.DateTimeFormat(locale, timeOptions).format(now);
      let meridiem = "";
      if (hour12) {
        const match = formatted.match(/\s?(AM|PM)$/i);
        if (match) {
          meridiem = match[1].toUpperCase();
          formatted = formatted.replace(/\s?(AM|PM)$/i, "");
        }
      }
      timeEl.childNodes[0].nodeValue = formatted + " ";
      meridiemEl.textContent = meridiem;
      dateEl.textContent = new Intl.DateTimeFormat(locale, dateFormatOptions).format(now);
    }
    render();
    setInterval(render, 1000);
  }

  // ---------------- SPOT PRICES ----------------
  function formatUSD(value) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(value);
  }
  function arrowGlyph(dir) { return dir === "up" ? "▲" : dir === "down" ? "▼" : "•"; }

  function renderMetal(name, data) {
    const priceEl = document.getElementById(`${name}-price`);
    const arrowEl = document.getElementById(`${name}-arrow`);
    const changeEl = document.getElementById(`${name}-change`);
    const priceContainer = priceEl.parentElement;

    priceEl.textContent = formatUSD(data.price);
    arrowEl.textContent = arrowGlyph(data.direction);
    arrowEl.className = `metal-arrow ${data.direction}`;

    const sign = data.changePercent > 0 ? "+" : "";
    changeEl.textContent = `${sign}${data.changePercent.toFixed(2)}%`;
    changeEl.className = `metal-change ${data.direction}`;

    priceContainer.classList.remove("flash");
    void priceContainer.offsetWidth;
    priceContainer.classList.add("flash");
  }

  function runSweep() {
    const sweep = document.getElementById("sweep-line");
    sweep.classList.remove("run");
    void sweep.offsetWidth;
    sweep.classList.add("run");
  }

  function setLastUpdatedLabel(date, reason) {
    const el = document.getElementById("last-updated");
    if (!CFG.offline.showLastUpdatedLabel) return;
    if (reason) {
      const time = new Intl.DateTimeFormat(CFG.clock.locale, { hour: "2-digit", minute: "2-digit" }).format(date);
      const prefix = reason === "closed" ? "Market Closed" : "Offline";
      el.textContent = `${prefix} — Last Updated ${time}`;
      el.classList.add("visible");
    } else {
      el.textContent = "";
      el.classList.remove("visible");
    }
  }

  // marketHours is evaluated in US Eastern time (where gold/silver actually
  // trade/close), not the display's local time zone, so "5pm" always means
  // the real market close no matter where the board is physically running.
  function easternHour(date) {
    return parseInt(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", hour12: false }).format(date), 10) % 24;
  }
  function isMarketOpen(date) {
    const { startHour, endHour } = CFG.api.marketHours;
    const hour = easternHour(date);
    return hour >= startHour && hour < endHour;
  }

  function msUntilMarketOpen(date) {
    const { startHour } = CFG.api.marketHours;
    // Find the next moment whose Eastern-time hour is startHour, by walking
    // forward in hour-sized steps (safe across the ET/local DST offset,
    // which can differ from the display's own time zone).
    let next = new Date(date);
    next.setMinutes(0, 0, 0);
    for (let i = 0; i < 48; i++) {
      next = new Date(next.getTime() + 60 * 60 * 1000);
      if (next > date && easternHour(next) === startHour) break;
    }
    return next.getTime() - date.getTime();
  }

  function saveToCache(data) {
    try { localStorage.setItem(CFG.offline.storageKey, JSON.stringify(data)); }
    catch (e) { console.warn("Unable to cache prices:", e); }
  }
  // Returns { data, legacy }. `legacy` marks data saved under an older
  // storage key (from before a pricing-formula change) that already has an
  // old adjustment baked in — don't re-adjust it, just show it as a
  // stopgap until a fresh live fetch succeeds.
  function loadFromCache() {
    try {
      const raw = localStorage.getItem(CFG.offline.storageKey);
      if (raw) return { data: JSON.parse(raw), legacy: false };
      const legacyRaw = localStorage.getItem("mtc_last_prices_v1");
      if (legacyRaw) return { data: JSON.parse(legacyRaw), legacy: true };
      return null;
    } catch (e) { return null; }
  }

  // ---- True rolling 30-day baseline for the change% arrow ----
  // Some providers (gold-api.com) don't report a daily % change, and relying
  // on each provider's own figure would make the board's change% jump around
  // whenever the fallback kicks in. Instead we keep our own daily history —
  // one raw price per calendar day — and always compare today's price
  // against whichever entry sits closest to exactly ROLLING_WINDOW_DAYS ago.
  // That's a real rolling window: every day the comparison point advances
  // by one day too, always "about a month back," not a fixed point that
  // only refreshes periodically.
  const ROLLING_WINDOW_DAYS = 30;
  const HISTORY_RETENTION_DAYS = ROLLING_WINDOW_DAYS + 10; // small buffer for gaps
  const HISTORY_KEY = "mtc_price_history_v1";

  function localDateStr(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }
  function loadHistory() {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      const history = raw ? JSON.parse(raw) : [];
      return Array.isArray(history) ? history : [];
    } catch (e) { return []; }
  }
  function saveHistory(history) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); }
    catch (e) { console.warn("Unable to save price history:", e); }
  }
  // Records today's price (once per day; later calls the same day just
  // update that day's entry with the latest value) and prunes anything
  // older than we'll ever need.
  function recordHistory(now, gold, silver) {
    const today = localDateStr(now);
    let history = loadHistory();
    const idx = history.findIndex((h) => h.date === today);
    if (idx >= 0) history[idx] = { date: today, gold, silver };
    else history.push({ date: today, gold, silver });
    const cutoff = new Date(now);
    cutoff.setDate(cutoff.getDate() - HISTORY_RETENTION_DAYS);
    history = history.filter((h) => new Date(h.date + "T00:00:00") >= cutoff);
    history.sort((a, b) => a.date.localeCompare(b.date));
    saveHistory(history);
    return history;
  }
  // Finds the history entry closest to (now - windowDays). Falls back to
  // the nearest entry available if there's a gap (e.g. the display was off
  // that day), so a missing single day doesn't break the comparison.
  function findBaseline(history, now, windowDays) {
    if (!history.length) return null;
    const target = new Date(now);
    target.setDate(target.getDate() - windowDays);
    let best = null, bestDist = Infinity;
    for (const h of history) {
      const dist = Math.abs(new Date(h.date + "T00:00:00") - target);
      if (dist < bestDist) { bestDist = dist; best = h; }
    }
    return best;
  }
  function changeFromBase(price, basePrice) {
    const changePercent = ((price - basePrice) / basePrice) * 100;
    return { changePercent, direction: changePercent > 0 ? "up" : changePercent < 0 ? "down" : "flat" };
  }
  // Merges the config seed with whatever real history has been recorded so
  // far (real entries win on any date they share with the seed). Always
  // merging — rather than only falling back to the seed when history is
  // completely empty — matters once a handful of real days exist: without
  // the older seed dates still in the mix, a lookup ~30 days back would
  // have nothing to land on but today's own just-recorded price.
  function mergedHistory() {
    const byDate = {};
    for (const h of CFG.api.seedHistory || []) byDate[h.date] = h;
    for (const h of loadHistory()) byDate[h.date] = h;
    return Object.values(byDate);
  }
  // Overrides each metal's changePercent/direction using our own rolling
  // 30-day history instead of whatever (if anything) the provider reported.
  function withDailyChange(rawResult, now) {
    const history = mergedHistory();
    const base = findBaseline(history, now, ROLLING_WINDOW_DAYS);
    if (!base) return rawResult; // no history yet — keep provider's own figure
    const apply = (metal, basePrice) => (metal ? { ...metal, ...changeFromBase(metal.price, basePrice) } : metal);
    return { ...rawResult, gold: apply(rawResult.gold, base.gold), silver: apply(rawResult.silver, base.silver) };
  }

  let priceTimer = null;
  function scheduleNext(seconds) {
    if (priceTimer) clearTimeout(priceTimer);
    priceTimer = setTimeout(refreshPrices, seconds * 1000);
  }

  async function refreshPrices() {
    const now = new Date();
    if (!isMarketOpen(now)) {
      const cached = loadFromCache();
      if (cached) {
        const withChange = cached.legacy ? cached.data : withDailyChange(cached.data, now);
        const shown = cached.legacy ? cached.data : window.MTCPrices.applyPriceAdjustment(withChange);
        renderMetal("gold", shown.gold);
        renderMetal("silver", shown.silver);
        setLastUpdatedLabel(new Date(cached.data.fetchedAt), "closed");
        scheduleNext(msUntilMarketOpen(now) / 1000);
        return;
      }
      // No cache yet (first visit, or an old cache was just invalidated) —
      // do one live fetch anyway so the board isn't left blank until the
      // next market-hours window.
    }
    try {
      const data = await window.MTCPrices.fetchPrices(); // raw spot
      // Compute the change% against whatever history already existed BEFORE
      // recording today's price — otherwise today's own price would already
      // be in history and could get picked as its own baseline (0% change).
      const withChange = withDailyChange(data, now);
      recordHistory(now, data.gold.price, data.silver.price);
      const adjusted = window.MTCPrices.applyPriceAdjustment(withChange);
      renderMetal("gold", adjusted.gold);
      renderMetal("silver", adjusted.silver);
      runSweep();
      setLastUpdatedLabel(data.fetchedAt, null);
      saveToCache({ gold: data.gold, silver: data.silver, fetchedAt: data.fetchedAt }); // cache raw, not adjusted
      scheduleNext(CFG.api.refreshIntervalSeconds);
    } catch (err) {
      console.error("Spot price fetch failed, falling back to cache:", err);
      const cached = loadFromCache();
      if (cached) {
        const withChange = cached.legacy ? cached.data : withDailyChange(cached.data, now);
        const shown = cached.legacy ? cached.data : window.MTCPrices.applyPriceAdjustment(withChange);
        renderMetal("gold", shown.gold);
        renderMetal("silver", shown.silver);
        setLastUpdatedLabel(new Date(cached.data.fetchedAt), "offline");
      } else {
        setLastUpdatedLabel(new Date(), "offline");
      }
      scheduleNext(CFG.api.retryIntervalSeconds);
    }
  }

  // ---------------- SLIDESHOW (images + video + promos) ----------------
  const FALLBACK_IMAGE_LIST = [
    "gold-bars-01.jpg", "silver-bars-01.jpg",
    "american-eagles-01.jpg", "american-eagles-02.jpg", "american-eagles-03.jpg", "morgan-dollars-02.jpg",
    "peace-dollars-01.jpg", "rare-coins-01.jpg", "rare-coins-02.jpg", "rare-coins-03.jpg", "rare-coins-04.jpg", "rare-coins-05.jpg", "jewelry-01.jpg",
    "luxury-watches-01.jpg", "luxury-watches-02.jpg", "luxury-watches-03.jpg", "estate-jewelry-01.jpg",
    "coin-collections-01.jpg", "coin-collections-02.jpg", "coin-collections-03.jpg", "coin-collections-04.jpg", "store-interior-02.jpg", "store-interior-03.jpg", "store-interior-04.jpg", "store-interior-05.jpg", "firearms-01.jpg",
    "antiques-01.jpg", "antiques-02.jpg", "antiques-03.jpg", "antiques-04.jpg", "antiques-05.jpg", "antiques-06.jpg", "antiques-07.jpg",
    "cameras-01.jpg", "cameras-02.jpg", "cameras-03.jpg", "cameras-04.jpg", "cameras-05.jpg", "cameras-06.jpg",
    "purses-01.jpg", "purses-02.jpg", "purses-03.jpg", "purses-04.jpg",
  ];

  function captionFromFilename(filename) {
    return filename.replace(/\.[^/.]+$/, "").replace(/[-_]\d+$/, "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  async function discoverImages() {
    const { manifestPath, imageFolder } = CFG.slideshow;
    try {
      const res = await fetch(manifestPath, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.files;
        if (Array.isArray(list) && list.length) return list.map((f) => imageFolder + f);
      }
    } catch (e) { /* fall through */ }
    return FALLBACK_IMAGE_LIST.map((f) => imageFolder + f);
  }

  async function discoverVideos() {
    const { videoManifestPath, mediaFolder } = CFG.slideshow;
    try {
      const res = await fetch(videoManifestPath, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.files;
        if (Array.isArray(list) && list.length) {
          return list.filter((f) => /\.(mp4|webm)$/i.test(f)).map((f) => mediaFolder + f);
        }
      }
    } catch (e) { /* optional — no videos yet is fine */ }
    return [];
  }

  async function discoverPromotions() {
    const promoCfg = CFG.promotions;
    if (!promoCfg || !promoCfg.enabled) return [];
    try {
      const res = await fetch(promoCfg.dataPath, { cache: "no-store" });
      if (!res.ok) return [];
      const data = await res.json();
      const list = Array.isArray(data.promotions) ? data.promotions : [];
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      return list.filter((promo) => {
        if (promo.startDate && today < new Date(promo.startDate)) return false;
        if (promo.endDate && today > new Date(promo.endDate + "T23:59:59")) return false;
        return true;
      });
    } catch (e) {
      console.warn("Could not load promotions.json:", e);
      return [];
    }
  }

  function buildImageSlide(src) {
    const div = document.createElement("div");
    div.className = "slide slide--image";
    const filename = src.split("/").pop();
    const fillList = (CFG.slideshow && CFG.slideshow.fillScreenImages) || [];
    if (fillList.includes(filename)) div.classList.add("slide--image-fill");
    const positionMap = (CFG.slideshow && CFG.slideshow.imagePosition) || {};
    if (positionMap[filename]) div.style.backgroundPosition = positionMap[filename];
    div.style.backgroundImage = `url("${src}")`;
    const caption = document.createElement("div");
    caption.className = "slide-caption";
    caption.textContent = captionFromFilename(src.split("/").pop());
    const colorMap = (CFG.slideshow && CFG.slideshow.captionColor) || {};
    if (colorMap[filename]) caption.style.color = colorMap[filename];
    div.appendChild(caption);
    return { el: div, kind: "image" };
  }

  function buildVideoSlide(src) {
    const div = document.createElement("div");
    div.className = "slide slide--video";
    const video = document.createElement("video");
    video.src = src;
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    div.appendChild(video);
    const caption = document.createElement("div");
    caption.className = "slide-caption";
    caption.textContent = captionFromFilename(src.split("/").pop());
    div.appendChild(caption);
    return { el: div, kind: "video", videoEl: video };
  }

  function buildPromoSlide(promo) {
    const div = document.createElement("div");
    div.className = "slide slide--promo";
    if (promo.image) {
      div.classList.add("slide--promo-image");
      div.style.backgroundImage = `url("${promo.image}")`;
      if (promo.imagePosition) div.style.backgroundPosition = promo.imagePosition;
    }
    div.innerHTML = `
      <div class="promo-card">
        ${promo.badge ? `<div class="promo-badge">${promo.badge}</div>` : ""}
        <div class="promo-headline">${promo.headline}</div>
        ${promo.subtext ? `<div class="promo-subtext">${promo.subtext}</div>` : ""}
      </div>`;
    return { el: div, kind: "promo" };
  }

  function interleave(imageSlides, videoSlides, promoSlides, videoFrequency, promoFrequency) {
    const result = [];
    let videoPtr = 0, promoPtr = 0;
    imageSlides.forEach((slide, i) => {
      result.push(slide);
      const position = i + 1;
      if (videoSlides.length && videoFrequency > 0 && position % videoFrequency === 0) {
        result.push(videoSlides[videoPtr % videoSlides.length]); videoPtr++;
      }
      if (promoSlides.length && promoFrequency > 0 && position % promoFrequency === 0) {
        result.push(promoSlides[promoPtr % promoSlides.length]); promoPtr++;
      }
    });
    return result;
  }

  async function startSlideshow() {
    const container = document.getElementById("slideshow");
    const emptyState = document.getElementById("slideshow-empty");

    const [images, videos, promos] = await Promise.all([discoverImages(), discoverVideos(), discoverPromotions()]);

    if (!images.length && !videos.length && !promos.length) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;

    document.documentElement.style.setProperty("--crossfade", `${CFG.slideshow.crossfadeSeconds}s`);
    const slideDurationMs = CFG.slideshow.slideDurationSeconds * 1000;

    let imageOrder = images.slice();
    if (CFG.slideshow.shuffle) imageOrder = imageOrder.sort(() => Math.random() - 0.5);

    const imageSlides = imageOrder.map(buildImageSlide);
    const videoSlideDefs = videos.map(buildVideoSlide);
    const promoSlideDefs = promos.map(buildPromoSlide);

    const promoFrequency = (CFG.promotions && CFG.promotions.everyNSlides) || 0;
    const videoFrequency = CFG.slideshow.videoFrequency || 0;

    const playlist = interleave(imageSlides, videoSlideDefs, promoSlideDefs, videoFrequency, promoFrequency);
    if (!playlist.length) { emptyState.hidden = false; return; }

    playlist.forEach((slide, i) => {
      container.appendChild(slide.el);
      if (i === 0) slide.el.classList.add("active");
    });

    let currentIndex = 0;
    let advanceTimer = null;

    function activate(index) {
      const current = playlist[currentIndex];
      const next = playlist[index];
      next.el.classList.add("active");
      current.el.classList.remove("active");
      if (current.kind === "video" && current.videoEl) {
        current.videoEl.pause();
        current.videoEl.currentTime = 0;
      }
      currentIndex = index;
      if (next.kind === "video" && next.videoEl) {
        next.videoEl.play().catch(() => {});
      }
    }

    function scheduleAdvance(durationMs) {
      if (advanceTimer) clearTimeout(advanceTimer);
      advanceTimer = setTimeout(advance, durationMs);
    }

    function advance() {
      const nextIndex = (currentIndex + 1) % playlist.length;
      activate(nextIndex);
      const next = playlist[nextIndex];
      if (next.kind === "video" && next.videoEl) {
        const nativeMs = isFinite(next.videoEl.duration) && next.videoEl.duration > 0 ? next.videoEl.duration * 1000 : slideDurationMs;
        scheduleAdvance(Math.min(nativeMs, slideDurationMs * 3));
      } else {
        scheduleAdvance(slideDurationMs);
      }
    }

    if (playlist[0].kind === "video" && playlist[0].videoEl) playlist[0].videoEl.play().catch(() => {});
    scheduleAdvance(slideDurationMs);
  }

  // ---------------- TICKER ----------------
  async function startTicker() {
    const track = document.getElementById("ticker-track");
    let messages = [];
    try {
      const res = await fetch(CFG.ticker.dataPath, { cache: "no-store" });
      const data = await res.json();
      messages = Array.isArray(data.messages) ? data.messages : [];
    } catch (e) {
      messages = ["BUYING GOLD", "BUYING SILVER", "TOP PRICES PAID", "FREE APPRAISALS"];
    }
    if (!messages.length) return;

    const dot = `<span class="ticker-item">•</span>`;
    function buildItems() {
      return messages.map((msg) => `<span class="ticker-item">${msg}</span>`).join(dot);
    }
    track.innerHTML = buildItems() + dot + buildItems();

    requestAnimationFrame(() => {
      const fullWidth = track.scrollWidth / 2;
      const duration = fullWidth / CFG.ticker.speedPxPerSecond;
      track.style.animationDuration = `${duration}s`;
    });
  }

  // ---------------- BOOT ----------------
  // Reloads the whole page periodically so a deployed fix or config change
  // reaches this tab on its own — otherwise a kiosk tab left open
  // indefinitely keeps running whatever JS was loaded when it was last
  // opened, no matter what gets pushed to the site afterward.
  function scheduleAutoReload() {
    const hours = CFG.kiosk && CFG.kiosk.autoReloadHours;
    if (!hours) return;
    setTimeout(() => location.reload(), hours * 60 * 60 * 1000);
  }

  function boot() {
    applyBranding();
    startClock();
    refreshPrices();
    startSlideshow();
    startTicker();
    scheduleAutoReload();
    window.addEventListener("online", () => refreshPrices());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
