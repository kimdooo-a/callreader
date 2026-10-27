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

- **A quote must also say what the verdict claims.** A second Muse Spark pass audits each PASS/FAIL against its quote alone
  ("the theme is generative AI" does not mean AI tools are allowed). It can only demote, never promote.
- **Conditions are quoted too.** A condition the model cannot quote is shown as UNVERIFIED.
- **A poster is an advert; the rules are behind its QR code.** SAM 3.1 finds every QR code on a poster, code decodes it,
  and the agent reads the page it points to — with that page's PDF and Hancom (.hwpx / .hwp) attachments.
- **Two readings of every PDF.** The text layer (PyMuPDF) and a Muse Spark vision transcription of each rendered page are
  both kept as sources; Korean PDFs often have a text layer in the wrong reading order, and a quote only needs to match one.

Start from a poster photo alone:

```bash
python -m callreader poster.jpg
#  qr: file box 612,1205 -> https://www.naroo.or.kr/bbs/board.php?bo_table=notice_01&wr_id=276
#  [PASS  ] eligibility  “□ 참가자격 : 전국 19세~39세 이하 청년으로 …”  — file.qr1.att2.vision p.1
#             condition: Must be 19-39 years old and not a foreigner
#               “○ 거주지 제한 없음. 외국인 제외, …”
#  [UNREAD] ai_use      No rule about using generative AI tools to make the entry is stated.
```

## Run

```bash
pip install -r requirements.txt
export MODEL_API_KEY=...        # Meta Model API key
python -m callreader https://singaporeunbound.org/opp/flash-fiction3
```

## Meta models used

| Step | Model |
|---|---|
| Reading sources, axis verdicts, quotes (structured output) | Muse Spark 1.3 (Meta Model API) |
| Auditing whether each quote establishes its verdict | Muse Spark 1.3 |
| Choosing which rule/policy links to follow | Muse Spark 1.3 |
| Posters and every rendered PDF page → text | Muse Spark 1.3 vision |
| Finding QR codes on posters | SAM 3.1 (decoding is plain code: zxing-cpp) |
| Info-session video → text (planned) | Muse Voice Transcribe |

## License

MIT
