# Illinois Medicaid Cliff Tracker — routine prompt

You are running the Illinois Medicaid Cliff Tracker. Work in the
`il-medicaid-tracker/` folder of this repository.

## What this tracks

County-level Illinois Medicaid managed-care enrollment, and early warning of
coverage losses under the federal work requirements that take effect
**January 1, 2027**, when all ACA adults move to six-month redetermination
cycles. Published estimates of Illinois coverage loss run from 270,000 to over
700,000 people.

## Steps

1. **Ensure a PDF extractor exists.** Try `pdftotext -v`. If missing, run
   `apt-get install -y poppler-utils`, and if that is unavailable,
   `pip install pdfplumber`. The code supports either backend.

2. **Run the tracker:**

   ```bash
   cd il-medicaid-tracker && python run.py
   ```

3. **If the output is exactly `NO_UPDATE`, send no email and stop.** Silence on
   quiet weeks is intended. Do not send a "nothing to report" message.

4. **Otherwise email the result** to `gfuller@allstarpartners.com` using the
   Gmail connector. The first line of output is the subject; everything after
   the blank line is the body. Send it verbatim — do not summarize, reformat,
   or add commentary. The report already carries its own caveats.

5. **Commit and push state:**

   ```bash
   git add il-medicaid-tracker/state.json il-medicaid-tracker/fingerprints.json
   git commit -m "IL Medicaid tracker: $(date +%Y-%m-%d) run"
   git push origin HEAD:main
   ```

   Use `HEAD:main`, not `HEAD`. The routine runs on a detached HEAD and a bare
   `git push origin HEAD` fails with "not a full refname".

## On the first run after any environment change

Report whether `hfs.illinois.gov` and `data.medicaid.gov` were reachable. This
tracker cannot function if the egress proxy blocks them, and a blocked fetch
must be reported loudly rather than producing an empty report.

## Rules

- **Never invent or estimate a figure.** Every number comes from `run.py`.
- **Never suppress a reported problem.** If the output has a DATA PROBLEMS
  section, it goes in the email exactly as written.
- If `run.py` exits non-zero or throws, email the error text to the recipient
  rather than staying silent. Silence is only correct for `NO_UPDATE`.
