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

## Wiring up the Live Display data

`live-display/data/config.json` controls the board. Set `pricesEndpoint` and/or
`featuredEndpoint` to a URL returning JSON in the documented shape; if empty or
unreachable, the board falls back to the values in the same file so a screen is
never blank. This is where the **mtc-live-display** data source plugs in.

Expected prices shape:
```json
{ "asOf": "2026-07-28T15:00:00Z",
  "metals": [ { "symbol":"XAU","name":"Gold","unit":"oz","price":2650.00,"change":0.4 } ] }
```

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
