#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# VonDuke Builds — Day 1 Setup (run once, on the Linux HP)
#
# What it does:
#   1. Creates the vonduke-builds repo on GitHub (public) via gh CLI
#   2. Initializes git in this folder
#   3. Commits everything as Day 1 and pushes
#   4. Installs commit_today.sh for the daily workflow
#
# Prereqs:  sudo apt install git gh   →   gh auth login
#
# Usage:    unzip vonduke-builds.zip && cd vonduke-builds
#           chmod +x setup_365.sh && ./setup_365.sh
# ════════════════════════════════════════════════════════════════
set -e

REPO_NAME="vonduke-builds"
GITHUB_USER="svanlee"

echo "════════════════════════════════════════"
echo "  VonDuke Builds — Day 1 Setup"
echo "════════════════════════════════════════"

command -v git >/dev/null || { echo "❌ git not found. Run: sudo apt install git"; exit 1; }
command -v gh  >/dev/null || { echo "❌ gh CLI not found. Run: sudo apt install gh"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "❌ Not logged in. Run: gh auth login"; exit 1; }

# ── Create the repo on GitHub if it doesn't exist ──────────────
if gh repo view "$GITHUB_USER/$REPO_NAME" >/dev/null 2>&1; then
  echo "ℹ️  Repo already exists on GitHub — skipping creation."
else
  gh repo create "$GITHUB_USER/$REPO_NAME" --public \
    --description "365 days of building in public — robotics, embedded, AI. One real artifact per day."
  echo "✅ Repo created: github.com/$GITHUB_USER/$REPO_NAME"
fi

# ── Init local git ──────────────────────────────────────────────
if [ ! -d .git ]; then
  git init -b main
  git remote add origin "https://github.com/$GITHUB_USER/$REPO_NAME.git"
fi

# ── Daily driver script ─────────────────────────────────────────
cat > commit_today.sh << 'DAILY'
#!/usr/bin/env bash
# Daily commit:  ./commit_today.sh "feat(robocar): thing you built" [file...]
set -e
MSG="${1:?Usage: ./commit_today.sh \"commit message\" [files...]}"
shift || true

# Day counter = commits on main + 1... no — track explicitly:
COUNT_FILE=".day_counter"
DAY_NUM=$(( $(cat "$COUNT_FILE" 2>/dev/null || echo 1) + 1 ))
echo "$DAY_NUM" > "$COUNT_FILE"

# DEVLOG entry
DATE_STR=$(date "+%a %b %d %Y")
ENTRY="### Day $DAY_NUM — $DATE_STR\n**${MSG}**\n\n"
sed -i "s|<!-- New entries go at the TOP -->|<!-- New entries go at the TOP -->\n\n${ENTRY}|" DEVLOG.md

git add -A
git commit -m "day-$DAY_NUM: $MSG"
git push origin main
echo ""
echo "✅ Day $DAY_NUM committed: $MSG"
echo "🔗 https://github.com/svanlee/vonduke-builds/commits/main"
DAILY
chmod +x commit_today.sh
echo "1" > .day_counter

# ── Day 1 commit ────────────────────────────────────────────────
git add -A
git commit -m "day-1: init VonDuke Builds — repo, DEVLOG, RoboCar architecture + IMU publisher + EKF config"
git push --set-upstream origin main

echo ""
echo "✅ Day 1 shipped."
echo "🔗 https://github.com/$GITHUB_USER/$REPO_NAME"
echo ""
echo "Daily workflow from tomorrow:"
echo "  ./commit_today.sh \"feat(aksumael): belief state schema v1\" "
