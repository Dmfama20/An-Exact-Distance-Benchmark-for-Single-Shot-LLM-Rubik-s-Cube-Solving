#!/usr/bin/env bash
# Build the ANONYMIZED review supplement (double-blind submission).
#
# Steps: WITH_DATA release build -> unpack -> redact the deposit DOI in
# the working-copy plan and deviations record -> re-sync the Module A
# manifest's working-copy-plan hash to the redacted file (the FROZEN
# plan predates the DOI and is unaffected; its hash must stay
# 27cce48b...) -> identity-leak scan -> zip -> sha256.
#
# Usage:  bash scripts/make_review_zip.sh \
#             [dist/rubiks-planning-bench_REVIEW-ANON.zip]

set -eo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO_ROOT/dist/rubiks-planning-bench_REVIEW-ANON.zip}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# DOI and leak patterns are assembled from parts so this script (which
# ships inside the zip) never literally contains them itself.
DOI="10.5281/zenodo.216""70328"
REDACTED="10.XXXX/redacted-for-review"
# identity strings that must never appear (bib author names excepted)
LEAK_PATTERN="216""70328|boch""um|a21d""00323|digi""teach"

printf '[1/5] building WITH_DATA release zip\n'
WITH_DATA=1 bash "$REPO_ROOT/scripts/make_release_zip.sh" \
  "$WORK/base.zip" > /dev/null
unzip -q "$WORK/base.zip" -d "$WORK/tree"

printf '[2/5] redacting deposit DOI\n'
for f in ANALYSIS_PLAN.md ANALYSIS_PLAN_FROZEN.md DEVIATIONS.md; do
  if [[ -f "$WORK/tree/$f" ]]; then
    perl -pi -e "s{\Q$DOI\E}{$REDACTED}g" "$WORK/tree/$f"
  fi
done

printf '[3/5] re-syncing manifest hash to the redacted working copy\n'
python3 - "$WORK/tree" <<'EOF'
import hashlib, json, sys
tree = sys.argv[1]
def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()
frozen = sha(f"{tree}/ANALYSIS_PLAN_FROZEN.md")
assert frozen.startswith("27cce48b"), \
    f"FROZEN plan hash changed by redaction ({frozen[:16]}) — abort"
mf = f"{tree}/results_dstar/manifest.json"
m = json.load(open(mf))
m["analysis_plan"]["sha256"] = sha(f"{tree}/ANALYSIS_PLAN.md")
m["analysis_plan"]["note"] = (
    "hash of the DOI-redacted anonymous-review copy of the working-"
    "copy plan; the immutable protocol is ANALYSIS_PLAN_FROZEN.md "
    "(unaffected by redaction)")
json.dump(m, open(mf, "w"), indent=1)
print("      frozen ok, working-copy hash re-synced")
EOF

printf '[4/5] identity-leak scan\n'
if grep -rilE "$LEAK_PATTERN" "$WORK/tree" | grep -v literatur.bib; then
  printf '[FATAL] identity leak found (see above) — not building zip\n'
  exit 1
fi
printf '      clean\n'

printf '[5/5] zipping\n'
rm -f "$OUT"
(cd "$WORK/tree" && zip -qr "$OUT" .)
printf '\nWrote %s (%s files)\n' "$OUT" \
  "$(unzip -l "$OUT" | tail -1 | awk '{print $2}')"
printf 'sha256: '
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$OUT" | cut -d' ' -f1
else
  sha256sum "$OUT" | cut -d' ' -f1
fi
