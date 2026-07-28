/* Generates the category pages from a shared template so they stay consistent.
   Run:  node build-pages.js
   Output: <slug>.html in the site root. Edit CATEGORIES below to change copy. */
const fs = require("fs");
const path = require("path");

const head = (title, desc, page) => `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title} | Morgan Trading Company</title>
  <meta name="description" content="${desc}">
  <link rel="icon" href="assets/img/mtc-logo.png" type="image/png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="assets/css/styles.css">
</head>
<body data-page="${page}" data-base=".">
  <div id="site-header-mount"></div>
  <main id="main">`;

const foot = `  </main>
  <div id="site-footer-mount"></div>
  <script src="assets/js/main.js"></script>
</body>
</html>`;

const pageHero = (crumb, h1, sub, img) => `
    <section class="page-hero">
      <div class="page-hero__media"><img src="assets/img/${img}" alt=""></div>
      <div class="container"><div class="page-hero__inner">
        <p class="breadcrumb"><a href="shop.html">Shop</a> / ${crumb}</p>
        <h1>${h1}</h1>
        <p>${sub}</p>
      </div></div>
    </section>`;

const intro = (lead, body) => `
    <section class="section">
      <div class="container">
        <div class="split" data-reveal>
          <div>
            <p class="eyebrow">Buy · Sell · Trade</p>
            <p class="lead">${lead}</p>
            ${body}
          </div>
          <div class="split__media"><img src="assets/img/{{IMG}}" alt=""></div>
        </div>
      </div>
    </section>`;

const features = (items) => `
    <section class="section bg-cream-2">
      <div class="container">
        <div class="sec-head center" data-reveal><p class="eyebrow">Why Morgan Trading</p><h2>What Sets Us Apart</h2></div>
        <div class="info-grid" data-reveal>
          ${items.map(f => `<div class="info-card"><h3>${f.t}</h3><p>${f.d}</p></div>`).join("\n          ")}
        </div>
      </div>
    </section>`;

const cta = (line) => `
    <section class="section--tight"><div class="container">
      <div class="band" data-reveal>
        <h2>${line}</h2>
        <p class="lead mx-auto measure">Bring your item in for a free, no-obligation appraisal — or call us for a quick quote.</p>
        <div style="margin-top:1.4rem;display:flex;gap:.8rem;justify-content:center;flex-wrap:wrap">
          <a class="btn btn--gold btn--lg" href="sell.html">Get a Cash Offer</a>
          <a class="btn btn--ghost-light btn--lg" href="tel:+19894482236">(989) 448-2236</a>
        </div>
      </div>
    </div></section>`;

const CATEGORIES = [
  {
    slug: "firearms", page: "shop", img: "firearms.png",
    title: "Firearms & Ammunition", crumb: "Firearms & Ammo",
    h1: "Firearms &amp; Ammunition",
    desc: "Licensed FFL dealer in Gaylord, MI. Buy, sell & trade handguns, rifles, shotguns and ammo with fair cash offers and legal transfers.",
    sub: "A fully licensed FFL dealer — buy, sell, trade, and transfer firearms and ammunition the right way.",
    lead: "As a federally licensed firearms dealer, we make buying and selling simple, legal, and fair. Bring in your handgun, rifle, or shotgun for a competitive cash offer, or shop our rotating selection of quality firearms and ammunition.",
    body: `<ul class="prose" style="margin-top:1rem"><li>Handguns, rifles &amp; shotguns</li><li>Ammunition in popular calibers</li><li>Legal FFL transfers &amp; background checks</li><li>Cash for guns — top dollar, paid same day</li></ul>`,
    features: [
      { t: "Licensed & Legal", d: "Every purchase, sale, and transfer is handled to the letter of federal and Michigan law." },
      { t: "Fair Cash Offers", d: "We pay competitively for quality firearms — no lowball appraisals." },
      { t: "Knowledgeable Staff", d: "Talk to people who know firearms and can help you buy or sell with confidence." }
    ],
    ctaLine: "Cash for Guns — Paid Same Day"
  },
  {
    slug: "gold-silver-coins", page: "shop", img: "gold-coins.png",
    title: "Gold, Silver & Coins", crumb: "Gold, Silver & Coins",
    h1: "Gold, Silver &amp; Coins",
    desc: "Precious metal & coin dealer in Northern Michigan. We buy gold, silver, platinum, bullion and collectible coins at competitive live-market prices.",
    sub: "Northern Michigan's precious metal & coin dealer — paying competitively against live spot prices.",
    lead: "Gold, silver, and platinum hold their value — and we pay accordingly. Whether it's bullion, scrap, or a collection of rare coins, our specialists appraise against live market prices so you get a fair, transparent offer.",
    body: `<ul class="prose" style="margin-top:1rem"><li>Gold, silver &amp; platinum — coins, bars &amp; scrap</li><li>Collectible &amp; numismatic coins</li><li>Live spot-price based offers</li><li>Sell with confidence, or invest in metals</li></ul><p style="margin-top:1rem"><a href="live-display/index.html" style="color:var(--gold-deep);font-weight:600;border-bottom:1px solid var(--gold)">See today's live spot prices →</a></p>`,
    features: [
      { t: "Live-Market Pricing", d: "Offers tied to real-time gold, silver, and platinum spot prices — watch them on our Live Display." },
      { t: "Coin Expertise", d: "From bullion to rare numismatics, we know how to value your collection." },
      { t: "Transparent Appraisals", d: "We show our work — you'll understand exactly how your offer is calculated." }
    ],
    ctaLine: "Turn Gold & Silver Into Cash"
  },
  {
    slug: "jewelry", page: "shop", img: "jewelry.png",
    title: "Jewelry & Diamonds", crumb: "Jewelry & Diamonds",
    h1: "Jewelry &amp; Diamonds",
    desc: "Jewelry & diamond dealer in Gaylord, MI. Buy, sell & trade fine jewelry, diamonds, gold and estate pieces at honest prices.",
    sub: "Fine jewelry and diamonds — bought, sold, and traded with an expert eye.",
    lead: "From estate jewelry to loose diamonds and gold pieces, we appraise fine jewelry with care and pay honestly. Looking to buy? Our showcase features quality pieces at prices well below retail.",
    body: `<ul class="prose" style="margin-top:1rem"><li>Diamonds — loose &amp; set</li><li>Gold &amp; fine jewelry</li><li>Estate &amp; vintage pieces</li><li>Sell, trade up, or find something special</li></ul>`,
    features: [
      { t: "Diamond Knowledge", d: "The 4 Cs matter — we grade fairly and price accordingly." },
      { t: "Estate Specialists", d: "Inherited jewelry? We'll help you understand what it's worth." },
      { t: "Below-Retail Showcase", d: "Buy beautiful pieces for a fraction of retail." }
    ],
    ctaLine: "Sell or Trade Your Jewelry"
  },
  {
    slug: "watches", page: "shop", img: "watches.png",
    title: "Luxury Watches", crumb: "Luxury Watches",
    h1: "Luxury Watches",
    desc: "Buy, sell & trade luxury watches in Northern Michigan — Rolex and other fine timepieces appraised and priced by experts.",
    sub: "Fine timepieces — Rolex and beyond — appraised, bought, and sold by people who know watches.",
    lead: "A luxury watch is an investment on your wrist. We buy, sell, and trade fine timepieces — appraising authenticity, condition, and market demand to make you a fair offer or find you the right piece.",
    body: `<ul class="prose" style="margin-top:1rem"><li>Rolex &amp; luxury Swiss brands</li><li>Authenticity &amp; condition appraisal</li><li>Cash offers or trade-up value</li><li>Curated timepieces for sale</li></ul>`,
    features: [
      { t: "Authentication", d: "We verify authenticity and condition so every deal is one you can trust." },
      { t: "Market-Aware Offers", d: "Watch values move — our offers reflect current collector demand." },
      { t: "Trade Up", d: "Put your current watch toward the next one in our showcase." }
    ],
    ctaLine: "Get an Offer on Your Watch"
  },
  {
    slug: "luxury-handbags", page: "shop", img: "handbags.png",
    title: "Luxury Handbags", crumb: "Luxury Handbags",
    h1: "Luxury Handbags",
    desc: "Designer & luxury handbag dealer in Gaylord, MI. Buy, sell & trade Louis Vuitton, Gucci and other authentic designer bags.",
    sub: "Authentic designer handbags — bought, sold, and traded with a trained eye for the real thing.",
    lead: "Designer handbags carry real resale value when they're authentic and cared for. We buy and sell luxury bags from the names people love — appraising condition and authenticity to keep every deal fair.",
    body: `<ul class="prose" style="margin-top:1rem"><li>Louis Vuitton, Gucci &amp; other designers</li><li>Authenticity verification</li><li>Fair cash offers &amp; trade value</li><li>Curated luxury bags for sale</li></ul>`,
    features: [
      { t: "Authenticity First", d: "We know the details that separate genuine luxury from replicas." },
      { t: "Condition-Based Value", d: "Honest appraisals based on real resale demand." },
      { t: "Rotating Selection", d: "Our showcase refreshes often — check back for new arrivals." }
    ],
    ctaLine: "Sell Your Designer Bag"
  },
  {
    slug: "general-merchandise", page: "shop", img: "instruments.png",
    title: "General Merchandise", crumb: "General Merchandise",
    h1: "General Merchandise",
    desc: "Tools, electronics, musical instruments and more — buy, sell & trade general merchandise at Morgan Trading Company in Gaylord, MI.",
    sub: "Tools, electronics, instruments, and the unexpected — there's always something worth discovering.",
    lead: "Beyond the showcase, we deal in a rotating mix of quality general merchandise — tools, electronics, musical instruments, and more. Bring in what you've got, or stop by to see what's new on the floor.",
    body: `<ul class="prose" style="margin-top:1rem"><li>Tools &amp; equipment</li><li>Electronics</li><li>Musical instruments</li><li>Always changing — visit often</li></ul>`,
    features: [
      { t: "Fair Offers", d: "We pay honest prices for quality, working items." },
      { t: "Always Changing", d: "Inventory turns over constantly — every visit is different." },
      { t: "Great Deals", d: "Find quality goods for less than you'd expect." }
    ],
    ctaLine: "Bring In Your Items"
  }
];

CATEGORIES.forEach(c => {
  const introBlock = intro(c.lead, c.body).replace("{{IMG}}", c.img);
  const htmlOut =
    head(c.title, c.desc, c.page) +
    pageHero(c.crumb, c.h1, c.sub, c.img) +
    introBlock +
    features(c.features) +
    cta(c.ctaLine) +
    "\n" + foot;
  fs.writeFileSync(path.join(__dirname, c.slug + ".html"), htmlOut);
  console.log("wrote", c.slug + ".html");
});
