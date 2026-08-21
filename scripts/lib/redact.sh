# shellcheck shell=bash
# AVO-lite redaction filter for REPORTS ONLY. Never applied to the ledger/journal
# (the vercel lesson: redacting the source of truth produced false findings).

# redact : stdin -> stdout with common secret shapes masked.
redact() {
  sed -E \
    -e 's/(Bearer )[A-Za-z0-9._-]{8,}/\1***REDACTED***/g' \
    -e 's/(sk-[A-Za-z0-9]{8})[A-Za-z0-9]+/\1***/g' \
    -e 's/(gh[pousr]_[A-Za-z0-9]{6})[A-Za-z0-9]+/\1***/g' \
    -e 's/(xox[baprs]-[A-Za-z0-9-]{6})[A-Za-z0-9-]+/\1***/g' \
    -e 's/(-----BEGIN [A-Z ]*PRIVATE KEY-----).*(-----END)/\1 ***REDACTED*** \2/g' \
    -e 's/([Aa]pi[_-]?[Kk]ey["'"'"' :=]+)[A-Za-z0-9._-]{8,}/\1***/g' \
    -e 's/([Pp]assword["'"'"' :=]+)[^ "'"'"']+/\1***/g'
}
