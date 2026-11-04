#!/bin/zsh
# Appends the rev-compact confirmation to K1B.md once it exists; the running driver loaded k1b_report.py before the fix.
cd "$(dirname "$0")"
while [[ ! -f K1B.DONE ]]; do
  kill -0 $(cat driver.pid) 2>/dev/null || break
  /bin/sleep 30
done
[[ -f K1B.md ]] && cat K1B-confirmation.txt >> K1B.md
