/**
 * config.js — Morgan Trading Company Live TV Display
 * Everything an owner needs to customize lives here.
 * Loaded as a plain <script> so it sets window.MTC_CONFIG globally.
 */
window.MTC_CONFIG = {
  business: {
    name: "Morgan Trading Company",
    tagline: "Current Spot Prices",
    logoPath: null, // e.g. "assets/icons/logo.svg"
  },

  api: {
    // gold-api.com is primary: it's free, keyless, and has no documented
    // rate limit. goldapi.io (100 req/month free cap) is the backup, tried
    // only if the primary fails — so it should rarely get used at all, and
    // its quota is there as a safety net rather than something we spend
    // down every day.
    provider: "goldapicom", // "demo" | "goldapi" | "goldapicom" | "metalsdev" | "currentgold"
    fallbackProviders: ["goldapi"],
    goldapi: { apiKey: "goldapi-597e29075eb7cb7f0166ce8074455e96-io", baseUrl: "https://www.goldapi.io/api" },
    metalsdev: { apiKey: "YOUR_METALS_DEV_KEY", baseUrl: "https://api.metals.dev/v1" },
    currentgold: { apiKey: "YOUR_CURRENT_GOLD_KEY", baseUrl: "https://api.current.gold/v1" },
    // Primary provider has no quota concern, so we can check often.
    refreshIntervalSeconds: 600,
    // How long to wait before retrying after EVERY provider in the chain
    // fails (a real outage, not just goldapi.io's quota).
    retryIntervalSeconds: 60,
    marketHours: { startHour: 9, endHour: 17 }, // 24h local time; refreshes only fetch live prices in this window
    // Percent of live spot price to display, so the board matches what we
    // actually buy/sell at instead of raw exchange spot. 1.0 = show raw spot.
    priceMultiplier: { gold: 0.999, silver: 0.995 },
    // Rounds the (multiplier-adjusted) price down to the nearest whole dollar.
    roundDownToDollar: { gold: true, silver: true },
    // Seeds the rolling 30-day price history on a device that has never
    // stored any (e.g. first load, or cleared browser data), using real
    // historical daily spot prices (gathered from public market reports,
    // interpolated between confirmed reference points) covering the month
    // before this was set, so today's change% reflects actual price
    // movement instead of starting near 0%. Once a device records its own
    // daily history, this seed is ignored — it only fills the gap before
    // real history exists.
    seedHistory: [
      { date: "2026-06-27", gold: 4013.0, silver: 58.04 },
      { date: "2026-06-28", gold: 4013.52, silver: 58.18 },
      { date: "2026-06-29", gold: 4014.04, silver: 58.31 },
      { date: "2026-06-30", gold: 4014.56, silver: 58.45 },
      { date: "2026-07-01", gold: 4020.0, silver: 58.74 },
      { date: "2026-07-02", gold: 4044.6, silver: 59.39 },
      { date: "2026-07-03", gold: 4069.2, silver: 60.04 },
      { date: "2026-07-04", gold: 4093.8, silver: 60.7 },
      { date: "2026-07-05", gold: 4118.4, silver: 61.35 },
      { date: "2026-07-06", gold: 4143.0, silver: 62.0 },
      { date: "2026-07-07", gold: 4166.0, silver: 61.36 },
      { date: "2026-07-08", gold: 4139.0, silver: 58.8 },
      { date: "2026-07-09", gold: 4112.0, silver: 59.04 },
      { date: "2026-07-10", gold: 4100.5, silver: 58.92 },
      { date: "2026-07-11", gold: 4089.0, silver: 58.8 },
      { date: "2026-07-12", gold: 4077.5, silver: 58.67 },
      { date: "2026-07-13", gold: 4066.0, silver: 58.55 },
      { date: "2026-07-14", gold: 4074.0, silver: 58.04 },
      { date: "2026-07-15", gold: 4033.0, silver: 57.53 },
      { date: "2026-07-16", gold: 3992.0, silver: 57.02 },
      { date: "2026-07-17", gold: 3996.5, silver: 57.0 },
      { date: "2026-07-18", gold: 4001.0, silver: 56.97 },
      { date: "2026-07-19", gold: 4005.5, silver: 56.95 },
      { date: "2026-07-20", gold: 4010.0, silver: 56.92 },
      { date: "2026-07-21", gold: 4054.0, silver: 57.5 },
      { date: "2026-07-22", gold: 4053.5, silver: 58.09 },
      { date: "2026-07-23", gold: 4053.0, silver: 58.67 },
      { date: "2026-07-24", gold: 4055.82, silver: 58.9 },
      { date: "2026-07-25", gold: 4069.21, silver: 59.03 },
      { date: "2026-07-26", gold: 4082.61, silver: 59.17 },
      { date: "2026-07-27", gold: 4096.0, silver: 59.3 },
    ],
  },

  slideshow: {
    imageFolder: "images/",
    mediaFolder: "media/",
    slideDurationSeconds: 10,
    crossfadeSeconds: 1,
    shuffle: false,
    manifestPath: "images/manifest.json",
    videoManifestPath: "media/manifest.json",
    videoFrequency: 4, // 1 video slide every N regular slides (0 = off)
    // Filenames listed here fill the screen edge-to-edge (may crop) instead of
    // the default fit-to-viewport (never crops, may letterbox).
    fillScreenImages: [
      "estate-jewelry-01.jpg", "jewelry-01.jpg", "rare-coins-04.jpg",
      "silver-bars-01.jpg", "coin-collections-01.jpg", "coin-collections-02.jpg", "coin-collections-03.jpg", "coin-collections-04.jpg",
      "firearms-01.jpg", "luxury-watches-02.jpg",
    ],
    // Per-filename background-position override for fill-screen images, when
    // centering crops out the interesting part of the photo.
    imagePosition: {
      "firearms-01.jpg": "center 55%",
    },
    // Per-filename caption text color override, for photos with a light/white
    // background where the default gold caption text is hard to read.
    captionColor: {},
  },

  promotions: {
    enabled: true,
    dataPath: "promotions.json",
    everyNSlides: 5, // 1 promo slide every N regular slides (0 = off)
  },

  ticker: {
    dataPath: "ticker.json",
    speedPxPerSecond: 90,
  },

  clock: {
    locale: "en-US",
    hour12: true,
    showSeconds: true,
    dateFormatOptions: { weekday: "long", year: "numeric", month: "long", day: "numeric" },
  },

  theme: {
    background: "#111111",
    gold: "#D4AF37",
    silver: "#C0C0C0",
    white: "#FFFFFF",
    divider: "#444444",
    accent: "#8B6F2A",
  },

  offline: {
    showLastUpdatedLabel: true,
    storageKey: "mtc_last_prices_v2",
  },

  kiosk: {
    // This runs as a browser tab left open indefinitely, so a code/config
    // update pushed to the site otherwise never reaches it until someone
    // physically reloads the page. Auto-reloading periodically means every
    // fix goes live on its own within a few hours instead of needing a
    // manual refresh each time.
    autoReloadHours: 3,
  },
};
