# Morgan Trading Company — Website (v2)

An upgraded, fully responsive replacement for the Wix site at
[morgantradingcompany.com](https://www.morgantradingcompany.com). Hand-built
static HTML/CSS/JS — fast, mobile/tablet/desktop friendly, and free to host.

## Highlights

- **Responsive** across mobile, tablet, and desktop (fluid type, off-canvas mobile nav).
- **Brand-matched** to MTC: serif "MTC" monogram, black + cream + kraft-gold palette.
- **`/live-display`** route — a live board with precious-metal spot prices, featured
  inventory, an announcements ticker, open/closed status, and a full-screen
  **Signage Mode** for an in-store TV (press **F** or the button; **Esc** to exit).
- **SEO ready** — semantic HTML, meta descriptions, `sitemap.xml`, `robots.txt`,
  and LocalBusiness JSON-LD on the homepage.

## Structure

```
mtc-site/
├── index.html              Home
├── buy.html sell.html trade.html shop.html
├── firearms.html gold-silver-coins.html jewelry.html
│   watches.html luxury-handbags.html general-merchandise.html
├── layaway.html about.html contact.html careers.html disclosures.html
├── 404.html
├── live-display/
│   ├── index.html          The /live-display endpoint
│   ├── live.css            Live-display + signage styles
│   └── data/config.json    Data source + fallback values (edit me)
├── assets/
│   ├── css/styles.css      Design system (single source of truth)
│   ├── js/main.js          Shared header/footer + interactions
│   ├── js/live-display.js  Live board logic
│   └── img/                Brand + category imagery
├── build-pages.js          Regenerates the 6 category pages from a template
├── robots.txt  sitemap.xml  .nojekyll
```

The header and footer are rendered once by `assets/js/main.js` so navigation stays
consistent — edit the `NAV`/`BIZ` objects there to change links or business info.

## Run locally

```bash
cd mtc-site
python3 -m http.server 8099
# open http://localhost:8099
```

## Live Display (self-hosted)

`/live-display/` is a framed website page (header, intro, fullscreen control)
that embeds the **self-hosted** kiosk board at `/live-display/board/`. The board
is a first-party copy of the `mtc-live-display` app — no dependency on the
external GitHub Pages deployment, so it keeps working on this origin after the
domain cutover and the separate GitHub repo can be retired.

- **The board:** `live-display/board/` — live gold & silver spot prices
  (gold-api.com keyless primary), 30-day change, clock, showroom slideshow,
  promotions, and announcements ticker.
- **Point an in-store TV** at `…/live-display/board/` (or use the Fullscreen
  button on `/live-display/`).
- **Customize** via `live-display/board/config.js` (business name, refresh
  interval, price multipliers, theme, kiosk auto-reload).
- **Slideshow images** live in `live-display/board/images/` and are listed in
  `images/manifest.json`. Add a photo + list it there to include it.
- **Announcements** in `live-display/board/ticker.json`; **promotions** in
  `promotions.json`.

## Deploying as a drop-in replacement for the Wix site

The current site is hosted on **Wix**. To replace it:

1. Host this folder (GitHub Pages, Netlify, or Vercel — all serve static files).
   For GitHub Pages, publish the repo and set Pages to serve the site root;
   `.nojekyll` is included so `/live-display/` and asset paths serve as-is.
2. Add the custom domain (`morgantradingcompany.com`) in the host's settings.
3. **Repoint DNS** away from Wix to the new host (A/ALIAS/CNAME records per the
   host's instructions). Once DNS propagates, the new site is live at the domain.

> Note: Wix is a closed platform — there is no way to "drop a file into" it. The
> replacement path is: new static site + DNS switch.

## Image credits

Brand logo and category photography were carried over from the existing MTC site.
Swap any image in `assets/img/` to update the corresponding card.
