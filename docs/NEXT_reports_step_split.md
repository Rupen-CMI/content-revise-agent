# Next job: split Step 2 into a Reports step and an Evidence step (ordered 2026-11-12)

Branch: `review/round3-merge` (worktree `C:\Users\rupen\Documents\cra-merge`). Nothing pushed. Test server: port 8010 from
this worktree (the user's own server is port 8000; never touch it).

## What the user asked for
- Step 2 becomes two steps: **Reports** (report choice + fact extraction) then **Evidence** (search driven by the facts).
- Reports step has two tabs at the top, styled like shadcn Tabs (https://ui.shadcn.com/docs/components/base/tabs); the
  frontend is plain HTML/JS (`frontend/index.html`, `frontend/app.js`, `frontend/styles.css`), so recreate the look in CSS.
  - **CMI tab:** CMI report suggestions; a textbox for the editor's own CMI report link (already built in Step 1:
    `#step1-cmi-url`, backend `Step2Request.cmi_report_url`); facts and figures used as-is (no variance); a **Fetch report**
    button that fetches the report and builds the fact sheet.
  - **Competitor tab:** agent-suggested market names; the editor either picks one (the agent finds reports on the internet,
    `/step2/find_reports`, and the editor chooses ONE) or pastes their own report link; **Fetch** reads it and builds the fact
    sheet (3-5% variance, `variance.py`).
  - Each tab has its own **fact sheet** view: every fact and figure fetched, clearly; the editor can add a value where one is
    missing.
- The Evidence step then searches using these facts (see "Evidence layer" below).

## Decisions (answered by the user 2026-11-12)
1. Competitor fact sheet shows the original value and the adjusted (3-5%) value side by side.
2. Values the editor enters are used exactly as entered: no variance, marked "added/edited by editor".
3. An **Edit** button at the top of each fact sheet switches the whole sheet to edit mode: the editor may change EVERY value,
   both the agent's prefilled ones and the missing ones (full independence). An edited value is used as entered and
   replaces the agent's; keep the agent's original for reference (and the competitor original/adjusted pair) so the change
   is visible.

## Agreed agent plan
1. Foundation (Sonnet medium): step split in sidebar/run state; backend endpoints for fetch-report + fact sheet and for
   saving editor values; tab shell; shared fact-sheet panel; Evidence step reads the confirmed fact sheet.
2. Then in separate worktrees: competitor tab (Sonnet medium), CMI tab (GLM, or Haiku if GLM is limited).
3. Review, full offline tests (`cd backend; DEEPSEEK_API_KEY=offline-test python devtools/test_*.py`), preview check on 8010,
   live probe (`devtools/evidence_probe.py "<doc>::<market>"`, always hand-pick the article's real market).

## Evidence layer (the job after this one) — analysis done 2026-11-12
- Today the search uses only report name + players + segment names; drivers, opportunities, leading categories, segment
  reasons and U.S. statements never shape a query (`main._step2_v2` ~1264).
- Competitor path searches twice (`_early_evidence` before reports are read, then a top-up); remove the early search.
- Step 1's claim search (`main.py:1026`) and orient's claims_to_back are unused in the v2 chain: remove.
- Plan: section search plan in code (trends from top drivers/opportunities; the two sub-segments fixed at this point as the
  leading category of two splits; segmentation reasons; U.S. from the report's U.S. statements + us_search; key players);
  one query-writing call returning queries grouped by section; one search pass, each hit carries its section; junk filter +
  dedupe + same-event merge (move `planning.dedupe_events` up) once; SELECT assigns exactly one section per item (drop the
  `context` catch-all); per-section minimums (proposed trends 6, each sub-segment 1, U.S. 2, players 3) with one top-up
  search per thin section; Evidence step UI groups evidence by section with counts and a thin flag.

## Done today on this branch (all offline tests pass)
Round-3 fixes (report matching, US from report facts when thin, segment drivers, evidence reuse check, FAQ 40-60 words,
link checks), dedicated CMI link box, search filter fixes (key player / trusted host need a market signal, player-name
cleaning, short queries, unquoted retry, key-player queries, thin-evidence round), SELECT inside-the-market rule + second
pass, deterministic junk filter (listing hosts/titles, non-event facts, old events).
Prompt-length experiment (`devtools/prompt_scale.py`): deepseek-flash follows 140 checkable rules at ~97-99% and context
up to ~17k words; first decline at ~25k words. Repetition is caused by duplicate events under different ids and vague
rules, not prompt length.
Known leftovers: BP-Chargemaster (2018) passes when the page never states the year; "provides"/"conducts" statements are
not caught as non-events; Agile PM software has little news.
