#!/bin/bash
# Double-click this in Finder to update the app and rebuild the data.
#
# It runs from wherever it lives, so there is no directory to be in and no
# environment to activate first -- the two things that go wrong when the same
# work is done by hand in Terminal.

set -u
cd "$(dirname "$0")" || exit 1

PYTHON="./.venv/bin/python"
printf '\n=== FQHC Prospect Intelligence ===\n\n'
printf 'Folder: %s\n\n' "$(pwd)"

if [ ! -x "$PYTHON" ]; then
    echo "The Python environment is missing."
    echo "Double-click 'Install FQHC Prospect Intelligence.command' first."
    echo
    echo "Press Return to close this window."
    read -r _
    exit 1
fi

echo "Step 1 of 3: getting the latest version..."
git pull --ff-only 2>&1 | sed 's/^/  /'

echo
echo "Step 2 of 3: checking for new source files in ~/Downloads..."
mkdir -p data/raw/uds data/raw/irs_xml

# Files the user has downloaded but not filed. Copied rather than moved, so a
# mistake here never loses somebody's download.
for FILE in "$HOME"/Downloads/*UDS*.xlsx "$HOME"/Downloads/*UDS*.csv \
            "$HOME"/Downloads/LAL-*.xlsx; do
    [ -e "$FILE" ] || continue
    if [ ! -e "data/raw/uds/$(basename "$FILE")" ]; then
        cp "$FILE" data/raw/uds/ && echo "  Added $(basename "$FILE")"
    fi
done
for FILE in "$HOME"/Downloads/*TEOS_XML*.zip; do
    [ -e "$FILE" ] || continue
    if [ ! -e "data/raw/irs_xml/$(basename "$FILE")" ]; then
        cp "$FILE" data/raw/irs_xml/ && echo "  Added $(basename "$FILE")"
    fi
done

echo
echo "Step 3 of 3: rebuilding the data. This takes a few minutes."
echo
"$PYTHON" -m pipeline.run 2>&1 | tee run.log

echo
echo "=== Done ==="
echo "A full log was saved to run.log next to this file."
echo "Open the app from ~/Applications to see the result."
echo
echo "Press Return to close this window."
read -r _
