# Restructure plan: market-first articles built around Most Searched Questions

**Status:** final, 2026-10-23. Ready to implement. Nothing is built yet, and nothing gets committed until the
user asks.
**Basis:** the Web Scraping Services run (`backend/logs/20261022_211843_run.log`) and a full read of
`prompts.py`, `pipeline.py`, `checks.py` and `main.py`.

## 0. Decisions settled with the user

1. **The market is the subject.** The publisher article is only context. It supplies the opening intro and the
   transition into the market. Later in the article, a short snippet from it comes back if and only if it is
   needed and fits naturally (0-3 in all, zero is fine). A snippet never makes the publisher's topic the
   subject again (see 8).
2. **MSQs (Most Searched Questions) are the spine.** They are general questions a real person types into Google
   or a chatbot about the market.
   - Exactly 2-3 MSQs become article headings.
   - Every other answerable MSQ goes to the FAQ.
   - Overlap with the report page's own FAQ does not matter.
3. **No publisher FAQ.** The FAQ is MSQs only.
4. **Figures:** CMI report first, then sourced evidence, otherwise not stated. No hand-entered figures and no
   unbacked values anywhere. Market figures quoted by the guest article are never used.
5. **The title is about the market only.** It states the article's main market point. No "<publisher topic> and
   the <Market>" joins, and no label tail such as ": Key Trends".
6. **Prompts carry framing, never sample outputs.** Each prompt explains why the output exists, how to reason
   to it, and how to test it. Banned shapes are enforced in code.
7. **Three blocks, in one order, no topic switching.** The article runs:
   - **publisher intro** (context from the guest article);
   - **transition** (from the example to the market);
   - **market block** (the SOP sections, then the conclusion).

   The publisher's topic never returns as a topic once the market block starts. Optional snippets (1) sit inside
   market sections and serve the market point being made.
8. **Hard-capped word shares** of the article body (title and FAQ excluded):
   - opening publisher intro **≤ 20%**, including the sponsor paragraph;
   - transition **5-10%**;
   - market block **the rest (≥ 70%)**.

   The 20% cap covers the opening intro only. Optional snippets later in the article are part of the market
   sections they sit in. These are hard caps, computed and enforced in code (§3.6b).
9. **Standing rules still apply:**
   - disconnect old code behind flags, never delete it;
   - one job per LLM call;
   - no quality retries (problems are logged);
   - root-cause fixes;
   - no commits until asked.

---

## 1. Why drafts go wrong: the ideology the code follows today

### 1.1 "The publisher's subject is the article; the market is attached to it"

Every write prompt is built on this:

- `MARKET_SYSTEM`: *"Every market paragraph says what it means for the publisher's subject."*
- `MARKET_USER`: *"A market paragraph that could be pasted unchanged into any other article about this market
  has failed."*
- Headings must be *"specific to THIS post's subject"*.
- The title's *"original subject must visibly carry the title"*, plus a required CMI term. Together these
  produce "<publisher topic> and the <Market>: Key Trends": two subjects, with a trends label that reads as if it
  covers the publisher's part.
- Sub-segments are picked *"by the post so far"*.

Result: invented bridges ("maps closely onto the four layers of a Python stack", "Where the stack meets the
segments"). The publisher's technical vocabulary leaks into the market sections, which is where the jargon comes
from. The looser the fit between article and market, the more gets made up.

### 1.2 "The SOP is a fixed template with exact counts"

Sections are specified by sentence, word and bullet counts that ignore what the sources contain:

- U.S.: 4 bullets of 28-35 words;
- key players: exactly 4 companies × 2 details;
- report context: must state size and CAGR;
- evidence minimums.

Rules saying "never invent proof" contradict these counts, and the countable rule wins. A thin report
therefore produces padding:

- "USD 2024 … a stated rate"
- "dense base of vendors"
- "Phantombuster: named by CMI among the key players"

### 1.3 "Fix each failure with another rule"

`MARKET_USER` renders to about 11k tokens of rules, several of them contradictory:

- **Secondary keywords:** "at most one per paragraph", yet code pins 4 keywords to report_context, which is one
  paragraph. The draft stuffed 3 into it.
- **Keyword budget:** "use 4-6", yet all 12 are listed with a home section. The draft used 10.
- **Voice vs keywords:** "use the everyday word", yet every keyword word must appear literally. Result: "the
  forecast of the web scraping services market puts its CAGR at…".
- **Question headings:** the long question-heading rule leaks. 3 sections were assigned questions; 5 of 7
  market headings came out as questions.
- **Placeholder examples:** prompts quote sample shapes, including "Zzz Widget" placeholders, and the model
  copies their form.

The checks count only countable things, so this draft logged "passed every deterministic write check".

### 1.4 "Questions and FAQ are decorations"

- Questions are bolted onto existing sections.
- The FAQ is written last, from the finished draft plus house-style shapes, so it echoes the draft and the report
  page.
- Nothing in the system represents what people actually search for.

### 1.5 Supporting causes

- **No structured reading of the report.** Nothing in code knows a figure is missing.
- **Evidence is gathered per template slot.** Question-string searches (105 s, 0 kept) and report-claim searches
  (239 s) returned junk; 2018 piracy stats became a "trend".
- **The 3A/3B split puts the transition at a seam** between two calls. That seam is the weakest part of every
  draft.
- **Long-tail intents are commercial** (pricing, vendor comparison), so there is no natural sentence for them.
- **No tertiary keywords exist.**

### 1.6 Verified: the guest article's "CMI" figures

The guest article cites USD 1.56 Bn (2026), USD 4.80 Bn (2033), 17.39% CAGR, 71.5% cloud share, 65% market
research and about 40% North America. Checked:

- **Not in our evidence.** In the 2026-10-22 run these numbers occur only inside the guest article text. No search
  hit, selected item or report text contains them. In older runs they appear only in model output carried over
  from the article.
- **Not on the report page.** Report 3613, the only Web Scraping entry in the catalog, states no market size or
  CAGR at all. Its data is from 2018-2019.
- **CMI's own blog posts conflict.** Both link to 3613:
  - blog 4141 (2026-08-27) states USD 1.56 Bn → 4.80 Bn at 17.39%;
  - blog 2589 (2026-10-14) states USD 561.7 Mn → 867.2 Mn at 6.4%.
- **Possible outside origin, unverified.** A search summary attributes the 1.56 Bn / 17.39% pair to a Mordor
  Intelligence report on the broader web scraping market. This was not confirmed.

Consequence: reports can lack headline figures, so the pipeline must detect the gaps and fill them only from
sourced evidence (§3.2). CMI blog posts are not a figure source.

---

## 2. The new ideology

1. **The market is the subject.** The publisher article is the worked example:
   - it opens the post and carries the sponsor paragraph;
   - a dedicated transition block carries the reader from the example to the market, once;
   - after that the article stays on the market;
   - later, a short snippet from it (one sentence at most) comes back if and only if it is needed and fits
     naturally, to make a market point concrete. That means 0-3 in the whole article, and zero is fine. It is
     never a paragraph about the publisher's topic;
   - no market paragraph is forced to connect back to it.
2. **MSQs are the spine.** One list of general, search-box questions decides:
   - the 2-3 question headings;
   - the whole FAQ;
   - what research must cover.
3. **Facts first, then prose.**
   - A fact sheet (report plus sourced gap-fill) and vetted evidence are fixed before writing.
   - A planner assigns facts to sections.
   - The writer turns the plan into prose and adds no facts.
4. **Sections flex to the material; the SOP stays mandatory.**
   - Every SOP section is written.
   - SOP requirements stay hard: 3 trends, 2 sub-segments, U.S. 100-200 words, 1,200-1,500 words, 1-2 CMI
     opinions, CMI link, sponsor paragraph.
   - Counts invented for style become ranges or go.
   - A thin section is short and plain, never padded.
5. **Code enforces structure; prompts carry judgement.** Section order, question headings, keyword caps, title
   shape and figure grounding are validated and repaired in code. Prompts stay short.
6. **Plain language.** Written for a general internet reader. Technical terms are explained in a few words or
   left out, without changing meaning.
7. **Framing, not examples.** No prompt contains a sample title, heading, question, FAQ entry or sentence, real
   or placeholder. A prompt explains three things:
   - **purpose**: what the reader or searcher needs;
   - **procedure**: how to produce the output from this run's facts;
   - **tests**: what a good output passes.

   Shapes that must never appear are checked in code and not quoted in prompts.

---

## 3. The new pipeline

```
Step 1  ORIENT + CATALOG MATCH                      (slimmed: topic summary + market candidates only)
        human confirms the market
Step 2  FETCH REPORT
        ├─ FACT SHEET ──> SEARCH DEMAND             (sequential pair, a few seconds each)
        │      └─ FIGURE GAP SEARCH                 (only if size/forecast/CAGR is missing)
        ├─ PUBLISHER POINTS                         (parallel)
        └─ SEARCH QUERIES ──> MARKET WEB SEARCH     (parallel; the long pole, ~165 s)
        UI: fact sheet (gaps + source of each figure), MSQs with intent and answerability
Step 3  SELECT  vet news, tag each item with section + MSQ ids; FIGURE BACKING job when a gap exists
        code: mark each MSQ answerable / unanswerable
Step 4  PLAN ──> validate + repair in code ──> WRITE ──> FAQ ANSWERS
        finalize: checks + docx
```

LLM calls per run (one job each): orient, fact sheet, search demand, publisher points, search queries, select,
plan, write, FAQ = 9, the same as today.

Each new stage is timed with `StageTimer` and shown in the Step 2 timing panel and logs.

### 3.1 FACT SHEET — `run_fact_sheet`, prompt `FACT_SHEET_*`

One job: read the report page into structured facts, `null` where the report is silent.

```json
{"size":      {"value": "...", "year": "..."} | null,
 "forecast":  {"value": "...", "year": "..."} | null,
 "cagr":      {"value": "...", "period": "..."} | null,
 "segments":  [{"type": "...", "items": ["..."], "leader": "..." | null, "fastest": "..." | null}],
 "regions":   {"leader": "..." | null, "leader_basis": "...", "fastest": "..." | null},
 "us":        ["..."],
 "drivers":   ["..."], "restraints": ["..."], "opportunities": ["..."],
 "players":   ["..."],
 "dated":     [{"text": "...", "year": "..."}]}
```

Code validation:

- Every figure must appear verbatim in the report text, or it becomes `null`.
- Statements dated outside the freshness window are tagged `historical`. They may be used with their year, never
  as current.
- Players are cleaned with `reports.key_players`.
- Every entry carries `source: "report"`.

### 3.2 Figure sourcing chain: report, then evidence, then nothing

This applies to every number in the article and FAQ.

1. **CMI report first.** A verified fact-sheet figure is used and credited to CMI.
2. **Evidence second, only for a gap.** If size, forecast or CAGR is `null`, the FIGURE GAP SEARCH runs in
   Step 2:
   - Deterministic queries built from the market's quoted core phrase plus size, CAGR or forecast wording.
     Bing web results, DDGS fallback, existing page-text fetch.
   - This search bypasses the rival-research-page filters (`_RIVAL_RESEARCH_RE`, `_MARKET_SIZING_RE`), because
     sizing pages are exactly what it needs.
   - SELECT's FIGURE BACKING job picks at most ONE source:
     - **Same market scope:** a broader or narrower market is rejected, never relabelled.
     - **One source for the set:** size, forecast and CAGR all come from it, never mixed across sources.
     - **Excluded:** CMI's own site (its blogs conflict) and the guest article's domain.
     - **Fresh:** dated, or a base year within 2 years of today.
   - Code verifies every value verbatim in that hit's text (reusing `_verify_selection_figures`); a failure drops
     it.
   - Accepted values enter the fact sheet with `source: "<publisher/site name>"` and the hit id.
3. **Nothing third.** An unbacked figure is never stated. The plan cannot assign it and the writer is never asked
   for it.

**Attribution:** report figures are CMI's. An evidence figure names its source in the same sentence and is never
credited to CMI. That source will often be another research firm, or a publication quoting one, and it is
credited by name.

**Other numbers** (trend facts, company details, U.S. facts) reach the writer only inside facts the plan
assigned, so each traces to a fact-sheet entry or a selected evidence item.

Market sizing stays rejected in the ordinary news evidence.

### 3.3 SEARCH DEMAND — `run_search_demand`, prompt `SEARCH_DEMAND_*`

One job: *what do ordinary people type into Google or a chatbot about this market?* It runs after FACT SHEET
(which it reads for segment names and facts). Output:

- **MSQs:** 12 general, market-level questions, each tagged with one intent:

  | Intent | Answerable from | Heading section |
  |---|---|---|
  | `size` | fact sheet `size` | report_context |
  | `growth` | `forecast` / `cagr` | report_context |
  | `trends` | trends evidence, `drivers` | trends |
  | `drivers` | `drivers` | trends |
  | `challenges` | `restraints` | trends |
  | `technology_impact` | trends evidence tagged to it | trends |
  | `segments` | `segments` | segmentation |
  | `region` | `regions` | country_us |
  | `us` | `us`, U.S. evidence | country_us |
  | `players` | `players` | key_players |
  | `outlook` | `forecast` / `opportunities` | conclusion |
  | `opportunities` | `opportunities` | conclusion |

  At most one MSQ per intent, two only where the market genuinely has two distinct searches.

  Framing given to the model (no sample questions in the prompt):
  - **Purpose:** these are the questions CMI wants to rank for in Google and AI answers, so each must be one many
    people actually ask.
  - **Who asks:** people curious about the market as a whole: students, job seekers, investors, small-business
    owners, analysts starting research. They know the market's name, not its jargon.
  - **How they ask:** short, general, plain words, about the market as a whole. Never about one company, a
    product feature or the guest article's topic.
  - **Procedure:** for each intent, imagine the shortest natural way such a person would ask it about this
    market, then check the market's own name is in it.
  - **Tests:**
    - does it make sense typed into a search box with no other context?
    - is the answer a fact about the market (not a definition or how-to)?
    - is it distinct from every other question?
- **Secondary keywords:** the existing suffix list, kept as a pool with each suffix's natural section as a hint.
  The planner picks 4-6.
- **Tertiary keywords (new):** 6-10 related terms people search alongside the market (the report's segment and
  sub-segment names plus close category terms). Used where natural.
- **Long-tail keywords:** 6-8 informational phrases, each tied to a fact-sheet item. No pricing or vendor
  comparison intents.

Code validation:

- An MSQ must contain the market core.
- Drop definition shapes (`_is_definition_question`).
- Drop near-duplicates (`_faq_tokens` overlap).
- After Step 3, each MSQ is marked **answerable** if its intent's fact-sheet field is non-null, or a selected
  evidence item is tagged with its id. Otherwise it is unanswerable, logged and dropped from both headings and
  FAQ.

The autocomplete harvester (`intent.py`) stays disconnected.

### 3.4 PUBLISHER POINTS — `run_publisher_points`, prompt `PUBLISHER_POINTS_*`

One job, run in Step 2 in parallel (it needs the confirmed market). Output:

- `intro_angle`: one line on how this example opens onto the market;
- 4-6 `points`: short, plain statements of what the article shows or explains, each with an id.

Rules:

- Market figures and market claims attributed to CMI or any research firm are excluded.
- Code also drops any point carrying a market-size-shaped figure.
- The writer sees these points, never the raw article. Each point is already plain, which keeps the guest
  article's jargon out.

### 3.5 Evidence and SELECT

- Keep the market news search (Bing RSS + publisher feeds), with its existing vetting.
- Disconnect, keeping the code behind flags:
  - `run_question_evidence`;
  - `run_report_claims` / `run_report_claim_evidence`;
  - the claim-backing search (already off).
- SELECT keeps its tests. It tags each item with a `section` (trends / us / players / segments / context) and the
  MSQ ids it helps answer, and runs FIGURE BACKING when a gap exists.

### 3.6 PLAN — `run_plan`, prompt `PLAN_*`

**Inputs:** fact sheet, vetted evidence, answerable MSQs, keyword pools, publisher points, sponsor paragraph.

**Fixed order:**

1. publisher intro (unheaded);
2. transition (unheaded, directly after the intro);
3. market block: report_context → trends → segmentation → subsegment → country_us → key_players → conclusion;
4. FAQ.

**Output:**

```json
{"title": "...", "title_basis": ["fs.drivers.0", "W3"],
 "intro": {"points": ["P1", "P2"], "words": 230},
 "transition": {"point": "<why this example belongs to the market, and what the reader will learn>", "words": 100},
 "sections": [
   {"sop": "report_context", "heading": "...", "msq": "Q2" | null,
    "point": "<one plain line: what this section tells the reader>",
    "facts": ["fs.cagr", "W4"], "secondary": "growth" | null, "longtail": "L3" | null,
    "tertiary": ["..."], "snippet": "P4" | null, "length": "short|normal", "words": 120}],
 "faq": ["Q1", "Q3", "..."]}
```

**Planner judgement** (framing, no samples):

- **MSQ headings:**
  - Purpose: win the searches people make, with a section that answers them directly.
  - Procedure: among answerable MSQs, pick the 2-3 whose answers the plan's facts make strongest, each in a
    different eligible section (per the intent table).
  - Test: the section's facts give a direct, specific answer.
- **Other headings:**
  - Purpose: tell a skimming reader what the section covers and show search engines the page structure.
  - Procedure: combine the section's job, the market's name or core phrase, and the section's main point from
    its facts, in plain words.
  - Tests: a statement, not a question; more than the job's bare label; about the market, not the guest
    article; not filler.
- **Points and flow:** each section gets a one-line `point`: what it tells the reader. Read in order, the points
  must form one storyline: the example → why it belongs to this market (transition) → how big the market is and where it is heading → what is changing it → how it splits →
  where it is strongest → who leads it → where it is heading. A point that does not follow from the one before
  needs reordering or a clearer point.
- **Facts:**
  - Assign only facts that exist.
  - Assign a few strong facts per section, not every fact available (about 2-4; one per trend bullet, two where
    it is strong). Research shows through specific, sourced facts. It must not bury the reader, so unused facts
    are fine.
  - Trends draw on dated evidence plus the report's drivers.
  - Sub-segments are chosen by what the report emphasises; the publisher example breaks ties.
  - Key players: companies with a fact get that fact. Companies without one are named together in one plain
    sentence, never given invented details.
- **Snippets (optional, 0-3, zero is fine):** framing, no samples.
  - **Purpose:** make a market point concrete with the example the reader already met in the intro.
  - **Procedure:** only after a section's facts are placed, ask whether one publisher point would make that
    section's market point clearer to a general reader. If not, the section gets none. Never add one to reach a
    count.
  - **Tests:**
    - taking it out would make the section less clear;
    - it is one sentence at most;
    - the paragraph is still about the market;
    - it does not repeat what the intro already said.
- **Keywords:** pick 4-6 secondary keywords, 2-3 long-tails and tertiary terms. Each goes in the section where a
  fact makes it fit.
- **Coverage:** every answerable MSQ goes to a heading or to `faq`.
- **Title:** see §3.6a.

**Code validation and repair** (deterministic, no LLM retry; every repair is logged):

- **Order and presence:** all SOP sections present, in the fixed order.
- **Headings:**
  - exactly 2-3 carry an `msq`, with the heading text set to that MSQ;
  - any other heading ending in "?" is replaced with a plain fallback built from the market name and section
    job;
  - fewer than 2 MSQ headings: promote the best answerable MSQ into an empty eligible section;
  - more than 3: demote the extras to `faq`.
- **Facts:**
  - unknown or `null` facts are dropped;
  - a section left without facts is marked `short`;
  - a U.S. section without U.S. facts is marked `qualitative` (about 100-120 words from regional facts, nothing
    invented).
- **Keywords:**
  - secondary: at most one per section, 4-6 in total;
  - long-tail: 2-3, in different sections;
  - snippets: 0-3, at most one per section, none in the conclusion's analyst opinion sentence. Extras beyond 3
    are dropped.
- **Word budgets:** the `words` fields are overwritten with the code-computed budgets from §3.6b. The planner
  does not choose them.
- **MSQs:** each answerable MSQ appears exactly once (a heading or the FAQ).

### 3.6a Title — decided in PLAN, validated in code

**Framing given to the planner** (the prompt contains no sample titles):

1. **Purpose.** The title is the line a searcher sees in Google or an AI answer before clicking. It tells them
   which market the page is about and what worthwhile thing it says about that market.
2. **Find the strongest market point.** Across the facts assigned to the sections, find the one development,
   force or direction the article shows most convincingly, with the most facts behind it. State it to yourself
   in one plain sentence: what is changing in the market and why, or where it is heading and why.
3. **Match it to search.** Find which MSQ intent that point answers. The title reads as the answer to that kind
   of search, or as the search itself.
4. **Compress.** Put the market's name first or near the start. Keep the words that carry the point (the force
   and the direction) and drop decoration.
5. **Test before answering:**
   - **Label test:** cover the market name. If no change, cause or direction remains, it is a label; go back to
     step 2.
   - **Backing test:** every claim in the title points to `title_basis`.
   - **Subject test:** every word describes the market. The guest article's topic may appear only as a force
     acting on the market.
   - **Searcher test:** someone typing the matching MSQ recognises the title as their answer.

**Constraints:**

- **One subject, the market.**
- **CMI terms only where they describe the market.**
- **At most one secondary or long-tail keyword** besides the primary.
- **Plain search wording:** 50-65 characters, no clickbait, no listicle or how-to shape. A question form only if
  it is one of the MSQs.

**Code checks.** A failure gets a plain fallback built from the primary keyword plus the top heading MSQ's topic,
and is logged. These banned shapes live in code only, never in the prompt:

- contains the full primary keyword and no other market name;
- the primary keyword starts within the first 40 characters;
- no "<X> and the <primary keyword>" join;
- no label tail after a colon or dash;
- 40-70 characters;
- `title_basis` ids exist.

### 3.6b Block budgets and hard caps — computed in code

The article body (title and FAQ excluded) has three blocks. Each block's share of the body's words is
hard-capped:

| Block | Contains | Hard cap |
|---|---|---|
| Opening publisher intro | lead-in, sponsor paragraph, any own sentences (later snippets are NOT counted here) | ≤ 20% |
| Transition | the bridge from the example to the market | 5-10% |
| Market block | report_context … conclusion | the rest, ≥ 70% |

**Budgets (code, before PLAN):**

- Target body length: 1,350 words, the middle of the SOP's 1,200-1,500.
- **Publisher intro:** about 18% of the target, which leaves margin under the 20% cap. The sponsor paragraph's
  real words count inside it (reuses `_intro_budget`), so the model's own intro words = budget − sponsor words.
- **Transition:** about 7.5% of the target.
- **Market block:** the remainder, split across the SOP sections by their SOP minimums (for example, U.S.
  100-200) and by how many facts each section has.
- **Long sponsor paragraph:** if the sponsor paragraph alone leaves fewer than about 30 own words for the intro,
  the intro becomes one lead-in sentence plus the sponsor paragraph. If even that breaks 20% at the target
  length, the run logs it, and the market budget grows to restore the ratio within the 1,500-word ceiling.

**Enforcement (code, after WRITE):**

- **Measure:** each block's words are measured on the real text, with the sponsor paragraph substituted in.
- **Over or under a cap:** the hard check fails visibly in the UI and the log, showing each block's words and
  share. There is no repair call and no retry (user decision): the content team trims by hand.
- **Prevention, not repair:** the caps are met up front. Each block gets its exact code-computed word budget in
  the plan, and the writer writes each block to that budget.
- **No block-level topic switching:** blocks are tagged, so code verifies the order intro → transition → market
  with zero switches back. Optional snippets inside market sections count as part of those sections.

### 3.7 WRITE — `run_write_v2`, prompt `WRITE_*`

One call, replacing `PUBLISHER_*` and `MARKET_*`. Input: the validated plan with every fact, snippet and
keyword expanded to text. The instructions (excluding inputs) aim at no more than a quarter of today's
`MARKET_USER`.

**Voice**

- Plain language for a general reader; short sentences; one idea per sentence.
- Technical terms explained in a few words or dropped.
- No "you", no hype, no filler.

**Publisher intro** (its own word budget)

- Sets the scene with the publisher example (`intro_angle` plus its assigned points), in plain words.
- CMI link in paragraph 1, within the first 200 words, as the SOP requires.
- Sponsor placeholder placed per the existing sponsor rules.
- Does not discuss the market beyond the link mention.

**Transition** (its own word budget, 5-10%)

- One short passage that explains why this example belongs to a bigger market and what the reader will learn
  about that market next.
- It is the only place the article moves from the example to the market.

  Framing, no samples:
  - **Purpose:** the reader should feel the turn as natural, not as a jump.
  - **Procedure:** name the market need the example shows, widen it to the market, then preview the market
    questions the article answers.
  - **Test:** it neither repeats the intro nor starts the market analysis's facts.

**Market block** (each section to its budget)

- Stays on the market from its first sentence to the conclusion.

**Sections**

- **MSQ section:** the first 1-2 sentences (about 40-60 words) answer the question directly. This is the
  featured-snippet and AI-answer target. The section then continues.
- **Facts:** each section writes only its assigned facts, with source or date.
  - **Placement:** each fact sits in the sentence that makes the section's point, with a light attribution (the
    source's name and month). No chains of "according to…", and no more than one fact per sentence.
- **Flow:** each market section's first sentence picks up from the previous section's point. The reader should
  never meet a section that seems to start from nowhere, and never return to the publisher's topic.
- **Short sections:** a `short` or `qualitative` section stays short and never fills space.
- **Snippets:** only where the plan assigned one. It is at most one sentence that makes the section's market point
  concrete, and the paragraph stays about the market. No snippet is added on the writer's own initiative.

**Keywords**

- Primary keyword once per section.
- Each assigned secondary or long-tail keyword once, in its section. Its words may be reordered, never swapped
  for synonyms.
- Tertiary terms where natural; unassigned keywords not used.

**SOP**

- 3 trends as bullets, 2 sub-segment paragraphs, U.S. 100-200 words.
- 1-2 labelled CMI analyst opinions, built only from facts in the article.
- 1,200-1,500 words in total.
- The report no-copy rule and the recency rule are kept.

**Title:** used exactly as validated.

### 3.8 FAQ — `run_faq` rewritten

- **Questions:** every MSQ in the plan's `faq` list, verbatim (up to 9-10).
- **Answers:** from the fact sheet and selected evidence, not the draft.
  - Direct answer in the first words, 1-3 sentences, same figure-sourcing chain.
  - An MSQ that can't be answered from those sources is dropped.
- **Consistency check:** no FAQ figure differs from the draft.
- **Disconnected (code kept):**
  - page-FAQ dedupe (`_drop_page_faq_repeats`, FAQ_USER rule 3b);
  - the `used_questions` rule;
  - house-style shapes;
  - the publisher FAQ group.

### 3.9 Checks — `checks.py`

**New hard checks**

- 2-3 headings are questions, and each matches an MSQ.
- Secondary keywords: at most one per paragraph, 4-6 in total.
- Placeholder figures absent: "USD <year>", "a stated rate", bare "%".
- Every number in the draft and FAQ traces to the fact sheet or a selected evidence item (reusing
  `_unsupported_figures`).
- An evidence-sourced headline figure names its source in the same sentence and is never credited to CMI.
- FAQ questions are MSQs; FAQ figures match the draft.
- Title checks from §3.6a.
- Block shares (§3.6b): publisher intro ≤ 20%, transition 5-10%, market ≥ 70% of the body.
- Block order: intro → transition → market, with zero switches back to the publisher's topic.

**New soft checks**

- Long-tail 2-3.
- Tertiary terms used.
- No sentences over 30 words.
- Flesch ≥ 50.

**Kept:** CMI link, sponsor paragraph, SOP sections present, U.S. length, opinions, report overlap, recency, guest
brand.

**Retired:** "Title names the article's subject", "Title carries CMI vocabulary". "Publisher content is about a
fifth" (soft) and "Topics are grouped" (soft) are replaced by the hard block-share and block-order checks above.

### 3.10 Disconnected components (code kept, flags off)

| Component | Replaced by |
|---|---|
| `PUBLISHER_*` (3A), `MARKET_*` (3B), `_render_part1`, 3A/3B layout checks | PLAN + WRITE |
| `REVISE_*` | already off |
| `QUESTION_DRAFT_*`, `run_question_evidence`, `_QUESTION_ASSIGNMENT_RE` parsing | SEARCH DEMAND + PLAN |
| `REPORT_CLAIMS_*` + its evidence search | FACT SHEET |
| `LONGTAIL_*`, ORIENT `longtail_candidates`, `long_tail_plan` stubs | SEARCH DEMAND + PLAN |
| ORIENT `claims_to_back` (already ignored) | removed from the prompt text |
| FAQ house shapes, page dedupe, publisher FAQ | FAQ = MSQs |
| `_SECONDARY_KEYWORD_SUFFIXES` section pinning | kept as a pool; PLAN assigns |

### 3.11 UI — `frontend/`

- **Step 2:**
  - the fact sheet, with gaps highlighted and the source of each figure (CMI report or the named evidence
    source);
  - the MSQ list with intent and answerability (marked after Step 3);
  - new stage timings.
- **Final view:**
  - keyword legend with counts for primary / secondary / tertiary / long-tail;
  - MSQ headings marked;
  - the FAQ shown as MSQs.

---

## 4. Build order

Each phase is tested before the next starts. Old code stays wired until phase 8 switches over.

1. **Fixtures.** Freeze raw inputs (article, sponsor, report page text, news hits) for:
   - Web Scraping: no headline figures, loose article fit;
   - Wealth (`fixture_wealth.json`): has figures;
   - one more market.

   Later phases add their outputs to the fixtures.
2. **Fact sheet + figure chain.**
   - Build `FACT_SHEET_*`, `run_fact_sheet` and its validation, the figure gap search, and FIGURE BACKING.
   - Tests:
     - Web Scraping triggers the gap search. A same-scope sourced figure is accepted or the figure stays null;
       a broader-market page and CMI blog pages are rejected.
     - Wealth uses report figures with no gap search.
3. **Search demand.**
   - Build `SEARCH_DEMAND_*`, `run_search_demand`, the filters and answerability marking.
   - Tests: MSQs for 3 markets read as general search queries with distinct intents and no definitions; no
     sample wording leaks.
4. **Publisher points.** Build `PUBLISHER_POINTS_*` and the figure strip. Test: none of the §1.6 figures
   survive.
5. **Plan.** Build `PLAN_*`, `run_plan` and the validator/repair. Unit-test the validator on hand-built bad
   plans:
   - 5 question headings;
   - stuffed keywords;
   - null facts;
   - an MSQ both as a heading and in the FAQ;
   - a bad title (label tail, two-subject join, missing primary keyword);
   - more than 3 snippets, or two in one section.

   Unit-test the §3.6b budget maths, including a long sponsor paragraph.
6. **Write.** Build `WRITE_*` and `run_write_v2`, reusing the sponsor, link and intro helpers, plus the block
   measurement and its hard check. Replay on all fixtures. Every replay must:
   - land within the block caps on the first write (no repair step exists, so a miss means the budgets or the
     framing need fixing);
   - keep the intro → transition → market order.
7. **FAQ** from MSQs, plus the consistency check.
8. **Checks + wiring.**
   - New and retired checks.
   - `main.py` Step 2/3/4 on the new chain.
   - Old calls behind flags.
   - `capture_fixture.py` / `replay_write.py` on the new chain.
9. **UI** changes.
10. **Evaluation.** 3 fixtures × 3 replays each.
    - Every new hard check passes on all 9.
    - Exactly 2-3 MSQ headings, every FAQ entry an MSQ, and no unbacked number.
    - Publisher intro ≤ 20%, transition 5-10%, market ≥ 70%, no topic switching.
    - Then read each draft in full and judge it against the five goals: intuitive, SOP-complete, plain,
      substantiated, SEO/MSQ-optimised.
    - Failures are traced to their framing cause, not patched with a new rule.
