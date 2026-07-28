#!/usr/bin/env bash
#
# download_morgantradingcompany.sh
#
# Mirror https://www.morgantradingcompany.com and all of its subdirectories
# to a local folder, producing a self-contained, browsable copy of the site.
#
# NOTE: In the Claude Code remote environment this site is blocked by the
# egress proxy (the CONNECT to the host returns HTTP 403 — an organization
# network-policy denial). Run this script from an environment with
# unrestricted outbound HTTPS (e.g. your local machine, or a session whose
# network policy allows arbitrary hosts).
#
# Usage:
#   ./download_morgantradingcompany.sh [OUTPUT_DIR]
#
#   OUTPUT_DIR  Optional. Directory to write the mirror into.
#               Defaults to ./morgantradingcompany-mirror
#
# Requirements: wget (GNU wget). On macOS: `brew install wget`.

set -euo pipefail

SITE_URL="https://www.morgantradingcompany.com/"
DOMAIN="www.morgantradingcompany.com"
OUTPUT_DIR="${1:-morgantradingcompany-mirror}"

if ! command -v wget >/dev/null 2>&1; then
  echo "Error: wget is not installed." >&2
  echo "  macOS:        brew install wget" >&2
  echo "  Debian/Ubuntu: sudo apt-get install wget" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Mirroring $SITE_URL"
echo "  -> $OUTPUT_DIR"
echo

# wget flags explained:
#   --mirror            = -r -N -l inf --no-remove-listing (recursive, timestamped)
#   --convert-links     rewrite links so the copy browses offline
#   --adjust-extension  save server-side pages (e.g. .php) as .html
#   --page-requisites   grab CSS/JS/images needed to render each page
#   --no-parent         never ascend above the start URL's directory
#   --domains           only follow links on this host (no off-site crawling)
#   --span-hosts        allowed alongside --domains so page requisites still
#                       load when hosted on a subdomain of the same site
#   --wait / --random-wait  be polite: pause between requests
#   --execute robots=off    fetch all subdirectories even if robots.txt would
#                           otherwise exclude the crawler (remove if you want
#                           to honor robots.txt)
#   -e use_proxy=... etc.  honor HTTPS_PROXY/HTTP_PROXY from the environment
wget \
  --mirror \
  --convert-links \
  --adjust-extension \
  --page-requisites \
  --no-parent \
  --domains="$DOMAIN" \
  --span-hosts \
  --wait=1 \
  --random-wait \
  --execute robots=off \
  --user-agent="Mozilla/5.0 (compatible; site-mirror/1.0)" \
  --directory-prefix="$OUTPUT_DIR" \
  --no-verbose \
  "$SITE_URL"

echo
echo "Done. Mirror written to: $OUTPUT_DIR/$DOMAIN"
echo "Open $OUTPUT_DIR/$DOMAIN/index.html in a browser to view the offline copy."
