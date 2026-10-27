# CallReader

**An agent that reads open calls — contests, grants, hackathons — and only tells you what it can quote.**

Built from scratch during the Meta Global AI Developer Hackathon (build window Oct 27 – Nov 4, 2026). No code predates the event.

## Why

I have read hundreds of open calls to decide which ones were worth a week of work. The expensive mistakes were never about prize size. They were:

- the AI ban that lived only inside the attached PDF, not on the page;
- a summary that kept the verdict ("AI allowed") and dropped the clause that governed it ("…only for spell-checking");
- "I couldn't read it" silently becoming "there is no rule";
- a download that "worked" but saved a 157-byte error page as `rules.pdf`.

CallReader turns those failures into invariants.

## How it works

```
fetch page + every attachment ──► sniff bytes (a .pdf that is HTML = UNREAD, not skipped)
        │                          posters/scans ──► Muse Spark vision transcription
        ▼
Muse Spark 1.3 reads all sources ──► 7 axes: eligibility · fee · cash · on-site · AI use · authorship · deadline
        │                              each with quote + source + condition clause
        ▼
citation verifier (plain code) ──► quote must exist verbatim in the fetched text, else the verdict is demoted
        ▼
3-state card: PASS / FAIL / UNREAD   — UNREAD is never folded into PASS
```

- **The model proposes, code disposes.** A PASS/FAIL survives only if its quote is found character-for-character (after whitespace/hyphen normalization) in a source we actually fetched.
- **Conditions are first-class.** "Unless / only if / 단, / ただし" clauses are stored next to the verdict.
- **Unread attachments poison PASS.** If any attachment could not be opened, eligibility / fee / AI-use cannot be PASS.

## Run

```bash
pip install -r requirements.txt
export MODEL_API_KEY=...        # Meta Model API key
python -m callreader https://singaporeunbound.org/opp/flash-fiction3
```

## Meta models used

| Step | Model |
|---|---|
| Reading sources, axis verdicts, quotes | Muse Spark 1.3 (Meta Model API) |
| Posters / scanned PDFs → text | Muse Spark 1.3 vision |
| Poster region segmentation (planned) | SAM 3.1 |
| Info-session video → text (planned) | Muse Voice Transcribe |

## License

MIT
