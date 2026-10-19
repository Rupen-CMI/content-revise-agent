SPONSOR_TOKEN = "[[SPONSOR_PARAGRAPH]]"
CMI_LINK_PATTERN = r"\[\[CMI_LINK:(.+?)\]\]"

# What CMI actually DOES — its own offerings/activities, in its own words, pulled from
# coherentmarketinsights.com (/services, /aboutus). Deliberately NOT industry or market names
# (those live in the report catalog and change per run); this is the fixed vocabulary of the
# work CMI itself performs, used to keep a title/framing phrased as market intelligence CMI
# would actually produce, instead of drifting into the original guest post's own topic or voice.
CMI_KEYWORDS = (
    "market research", "market intelligence", "industry analysis", "market monitoring",
    "competitive intelligence", "consulting and advisory", "consumer insights",
    "procurement intelligence", "supplier and demand analysis", "technology research",
    "ESG recommendations", "cross-border expansion", "market forecasting",
    "market sizing", "market trends", "growth drivers", "competitive landscape",
    "key players analysis", "segmentation analysis", "regional market analysis",
    "demand forecasting", "supply chain insights", "syndicated research reports",
    "custom research", "primary research",
)

SOP = """Mandatory content requirements for the revised blog post (these name what each part of the piece must COVER, \
for your own reference — none of these names are headings; the piece should still read as one connected blog, and its \
actual section headings must be written fresh for this specific article, not copied from this list):

1. Market Trends: three key market trends relevant to the market. Explain each clearly, including its impact on the market.
2. Market Intelligence Report Context: relevant context from the Coherent Market Insights (CMI) report for the market, integrated naturally into the discussion, never as a standalone promotional section.
3. Market Segmentation: the key segments and sub-segments covered in the CMI report, presented so they are relevant to the overall discussion.
4. Sub-Segment Analysis: pick one sub-segment from each of two different major segments and explain each in detail: key factors driving its growth/adoption, major applications and end-use cases, and its relevance and contribution to the overall market.
5. Country Analysis: a U.S. analysis of 100-200 words covering key market factors, growth drivers, adoption trends, and relevant industry developments.
6. Market Participants / Key Players: relevant companies operating in the market; where appropriate, briefly connect their presence, products, technologies, or strategies to the market."""


ORIENT_SYSTEM = """You are a senior editor at Coherent Market Insights (CMI), a market research firm. \
Guest posts on CMI's blog are being rewritten into market-intelligence articles anchored on one CMI market report. \
Your job in this step is to identify which CMI market the article belongs to and plan the web research. Respond with JSON only."""

ORIENT_USER = """CMI's report categories: {categories}

ARTICLE TITLE: {title}

ARTICLE BODY:
{body}

PARAGRAPH THAT MUST STAY IN THE FINAL ARTICLE (contains a paid backlink):
{sponsor}

Return JSON with these keys:
- "topic_summary": one sentence describing what the article is about.
- "scope_keywords": 8-12 short phrases (1-4 words each) that define the MARKET or INDUSTRY this article's subject belongs \
to — not a summary of what the article discusses, and not restatements of its topic. Ask yourself: "a market research \
firm publishes reports on industries; which industry/market does this article's actual subject live inside?" Then give \
keywords naming THAT market and its scope — the way that market's own segmentation, vendors, and category terms would be \
named in an industry taxonomy (the kind of terms that appear in a report's table of contents: segment names, product- \
category names, vendor-category names), not generic adjacent concepts the article happens to mention in passing and \
not vague marketing/business-fluff phrases ("digital transformation", "customer experience", "innovation", "growth \
strategy", "best practices") that name no specific industry at all. Every keyword in this list must belong to the ONE \
market the article's subject actually lives in — not a different, adjacent market that merely shares a workflow, \
customer base, or a few overlapping vendors with it. A tool from a NEIGHBORING market being mentioned, integrated \
with, or compared against in the article does not make that neighboring market the article's own scope: e.g. for an \
article about marketing automation software, a phrase like "CRM integration" or "CRM marketing" must NOT appear here — \
CRM is a different, separate market (Customer Relationship Management), even though marketing automation tools \
commonly integrate with it, and letting a CRM-flavored phrase into this list is exactly what pulls "Customer \
Relationship Management Market" into candidate_markets below, which the next rule explicitly forbids. If a word or \
phrase names a market distinct from the one the article is actually about, leave it out even if it is real, specific, \
and accurately describes something the article mentions. If the article names its own market explicitly \
(e.g. states a market size, CAGR, or says "the X market"), that named market and its direct synonyms/near-synonyms MUST \
dominate this list — do not let a secondary or incidental detail outrank the article's own stated subject. Favor \
precision over breadth: a wrong-market keyword is worse than a missing one. \
Example (format only, not a real market): for an article about "automated invoice processing for accounts payable \
teams," correct keywords are "invoice automation", "accounts payable software", "AP automation" — NOT "accounting", \
"finance teams", "enterprise software", or "digital transformation" (too generic/adjacent or pure fluff), and NOT \
"automation" alone (names no market).
- "candidate_markets": 6-8 market names derived from scope_keywords, most fitting first, written as the bare \
market name a research firm would title a report: "<Noun Phrase> Market", e.g. the style of "Zzz Widget Market" or \
"Zzz Fastener Market" (placeholder format examples only, not real markets). Do NOT append segmentation qualifiers \
such as ", By Deployment Type (...)" or ", By End User" — the report catalog is searched by the bare name, and extra \
words make the exact report fail to match. Use established, broad industry market names that \
a research firm would publish a report on, not niche or invented ones. Include both the closest specific market and one \
or two broader parent markets. List ONLY the article's own market and its direct parents — never an adjacent or sibling \
market, even if the article mentions it or shares customers and data with it (e.g. for an article about marketing \
automation software, customer relationship management is a different market and does not belong on this list). The \
retained paragraph's niche must not pull guesses toward a different market — fitting that paragraph in is a later \
step's job, not this one. Do not reuse any market name that happens to appear elsewhere in this prompt.
- "bridge_angle": one sentence on how the article's topic naturally connects to the top candidate market."""


SEARCH_QUERIES_SYSTEM = """You are a research assistant helping a market-intelligence editor at Coherent Market \
Insights (CMI) find recent, real news coverage for a CONFIRMED market (already chosen by a human editor — not your \
decision). Your only job is to write web search engine queries likely to surface genuine recent news about this \
market: product launches, acquisitions, funding rounds, partnerships, regulations, or other concrete developments \
involving companies in this space. Respond with JSON only."""

SEARCH_QUERIES_USER = """TODAY'S DATE: {today}
CONFIRMED MARKET: {market_name}
KNOWN COMPANIES IN THIS MARKET (from the confirmed CMI report, if any): {key_players}
MARKET SEGMENTS (from the confirmed CMI report, if any): {segments}

SCOPE: the CONFIRMED MARKET above is the only subject. Write queries for that market as a whole \
— its main product or service categories, the segments listed above, its companies, and the industry it sits in \
— and do not narrow to one product, feature or use case unless that IS the market.

Write search engine queries (plain text, as you'd type into a search engine) to find REAL, RECENT news about this \
market, covering EVERY one of these 8 categories so the evidence pool ends up varied rather than dominated by one \
kind of story — more queries means more chances a genuinely good, citable fact turns up in each category, even \
though most results in any category will get filtered out later and only the best few will ever reach the article:
  HIGHER PRIORITY (write 3 queries for EACH of these 3 categories — 9 queries total):
  1. Market trends — ask what's changing in the market itself, don't presuppose the answer. A query must ANCHOR to \
  the market so the engine can reveal whatever the real trend turns out to be, not already name a specific sub-topic \
  as a bare word on its own — "robo-advisory adoption by younger investors" is wrong because a search engine matches \
  "robo" against ANYTHING sharing that substring (a robotics ETF, a password manager, a browser game), \
  since nothing in the query itself says which industry is meant. Anchor every trends query to the market's own core \
  phrase or the market's actual subject (format only, placeholder not a real market: a query shaped like \
  "\"Zzz Widget\" <a plain verb or noun about change>" is anchored; "<one sub-topic word> adoption" on its own is not) \
  — never a bare technology/segment word alone, and vary the wording from query to query rather than reusing one shape.
  2. Market analysis — real reported coverage of the market's direction, segments, or outlook (a journalist's or \
  trade publication's story), NOT a search for a research firm's own report/analyst-deck page — those pages are \
  banned below the same as they are everywhere else in this prompt, this category does not exempt them. Anchor the \
  same way as trends above — the market's own name or subject must appear in the query, not just an analysis-style \
  word on its own.
  3. Latest market-related news — general recent news in this space, broad enough to catch whatever a company-named \
  query below would miss, but still anchored to the market itself (same rule as above) rather than a bare generic \
  term that could belong to any industry.
  LOWER PRIORITY (write 1 query for EACH of these 5 categories — 5 queries total):
  4. A new product/service launch.
  5. A new innovation (a novel technology or capability entering the market).
  6. Regulatory or rule changes affecting the market.
  7. Law changes affecting the market (legislative, not regulatory-agency, action).
  8. Key players — built around the specific company names listed above (if any are given) paired with a concrete \
  event word (acquisition, funding, partnership, launch, expansion).
  That totals 14 queries. Follow these rules exactly, learned from real search failures:
- If a query names the market by a multi-word phrase, wrap that phrase in double quotes so the engine requires it \
as one unit — an unquoted multi-word market name matches anything sharing just one of those words (e.g. unquoted \
"digital marketing software" also matches a story about a marketing AGENCY that has nothing to do with software). \
A query built around a specific company name usually does not need quotes unless the company name itself is a \
common phrase.
- Never use "OR" in a query — a search engine treats each OR branch as a fully independent search, so "launches OR \
unveils OR service" can return results matching only "service", drifting completely off-topic.
- Do not use the word "Market" as part of a quoted phrase (research firms title their own report PR pages "<X> \
Market ... Trend/Analyst", and including it invites exactly that kind of page back as a "result").
- Do not use explainer/definition words ("trend", "overview", "adoption drivers", "what is") in the query TEXT \
itself — they invite glossary and how-to pages, which are not evidence that anything actually happened; the 8 \
categories above describe what KIND of real news to look for, not words to put in the query.
- Do not hardcode a specific year (no "2025", "2026", etc.) into a query — TODAY'S DATE above tells you what "recent" \
actually means right now, but a search engine treats a literal year in the query as a filter, not a recency hint, so \
it just as often excludes genuinely fresh coverage that happens to not repeat that digit in its own text as it \
includes old coverage that does. Rely on the engine's own relevance/recency ranking instead of typing a year.
- Never search for a research firm's own report, analyst deck, or report-listing page (titles like "<Market> \
Size, Share, Growth, Trends, Forecast [to/by] <year>", "<Market> Analysis [year range]", "Forrester/Gartner report \
on <market>") — those are rival research firms' own marketing pages, not reported news, regardless of which \
category above prompted the query.
- For every category except key players, think about how a journalist covering THIS exact market would actually \
phrase it — a close synonym, an abbreviation, or the way the underlying technology/product is normally named in \
coverage — rather than only ever the literal confirmed market name. Only use the literal market name verbatim in a \
minority of your queries across the whole set; spend the rest on these alternate phrasings and company-led queries.

SOURCE GROUPS — in addition to the queries, choose which curated groups of trade-press and regulator websites cover \
this market's industry. Each group below is a menu entry "id: what it covers". Pick the 1 to 3 groups whose coverage \
best matches the confirmed market (most relevant first); pick fewer rather than a loosely related group. A group is \
about the INDUSTRY the market lives in, not the article's own topic.
{source_group_menu}

Return JSON: {{"queries": ["<query 1>", "<query 2>", ...], "source_groups": ["<group id>", ...]}}"""


SELECT_SYSTEM = """You are a senior market-intelligence editor at Coherent Market Insights (CMI). \
The market/report for this article has already been confirmed by a human editor — your only job here is to select \
reliable supporting evidence from web search results. Respond with JSON only."""

SELECT_USER = """ARTICLE TITLE: {title}
ARTICLE TOPIC: {topic_summary}
INTENDED ANGLE: {bridge_angle}
(INTENDED ANGLE was written in an earlier step, before a human confirmed the market below, and may describe or name a \
different market than CONFIRMED CMI REPORT — if the two disagree, CONFIRMED CMI REPORT is authoritative and the only \
market search results are judged against; treat INTENDED ANGLE only as background on why the article was written, \
never as evidence of which market this is actually about.)

PARAGRAPH THAT MUST STAY IN THE FINAL ARTICLE:
{sponsor}

CONFIRMED CMI REPORT (already chosen by a human editor — not your decision):
{report_name}

WEB SEARCH RESULTS:
{hits}

Task: from the web results, select up to 20 that pass ALL of these tests — a result that fails any one of them must \
be rejected, not merely scored lower:
1. Genuinely about THIS confirmed market specifically, not a result that merely shares a few words with it (a result \
about a different, related, or broader market is a wrong-market result, not a weak match — reject it, do not include \
it as "context"). A single word from the market's name taken on its own does not make a result relevant: for a market named \
"Zzz Widget Platform" (placeholder, not a real market), a page about what a "platform" is, or what a "widget" is, is not \
about that market.
2. Credible: a named publication reporting news, or a named company/analyst making a specific, attributed statement — \
not an anonymous listicle, content-farm roundup, or SEO filler page. Reject any page whose title or framing is itself \
a market-report advertisement — "<Market> Size, Share, Growth, Trends, Forecast [to/by] <year>", "<Market> Analysis \
[2020s year range]", or similar — regardless of which site hosts it: that pattern means the page IS a rival research \
firm's own report listing, a company directory, or an index of other articles, not a reported event, even when the \
host itself is not on a known-blocked list.
3. Concrete, not generic: the result must report something that HAPPENED or a specific figure (a company's product \
launch, an acquisition, a funding round, a named regulation taking effect, a specific adoption statistic) — reject \
vague trend-washing ("the market is growing", "companies are investing in AI") that could apply to almost any market and \
adds nothing a reader doesn't already assume. Reject anything that DEFINES or EXPLAINS a concept instead of reporting \
an event: dictionary or encyclopedia entries, "what is X" / "meaning of X" / "types of X" / glossary / tutorial / \
guide pages, and any page whose first sentence is a definition. Explaining the subject is the article's job, not a \
source's — a source earns a place only by telling the reader what happened.
4. Actually usable in the final draft: ask yourself "would this specific fact, cited by name, strengthen the Market \
Trends, U.S. analysis, or Key Players section of the article a reader is about to receive?" — if a fact is true but \
would not actually improve a single sentence of that article, do not select it just because it is on-topic.
Reject listicles, product promotions, how-to guides, and anything about a different market. It is fine to select fewer \
than 20, or none, if few results pass all four tests — do not pad with weak, generic, or off-topic results to reach a \
higher count; a shorter list of results that each genuinely earn their place beats a longer list padded with filler. \
For each selected result, write one factual sentence stating only what the result says; do not add, infer, or round \
any number it does not contain.

Each result above is tagged DATED (with its publish date, already checked to be recent) or UNDATED (an ordinary web \
page with no verifiable publish date, so nothing about it is known to be current). A fact used for "trends" is a claim \
about what is CURRENTLY happening in this market, so its source must be DATED — an UNDATED result must never include \
"trends" in its use_for, no matter how relevant it reads; any DATED result is eligible for "trends" on date alone. An \
UNDATED result is selected only if it states a concrete, citable fact about THIS market that passes all four tests \
above (never a definition or explanation), and then only with use_for ["context"]. When in doubt about an UNDATED \
result, leave it out.

use_for is a LIST: one fact can support more than one part of the article, so list every section it genuinely supports, \
most important first, and nothing it does not truly earn. For example, a named company's acquisition is a "players" \
fact, and it is ALSO a "trends" fact if it illustrates a direction the market is moving in (consolidation, AI adoption, \
platform partnerships). Use "us" only for a fact specifically about the U.S. market. The article needs three distinct \
market trends, a U.S. paragraph, and a key-players section, so when the results allow it, make sure your selection \
gives each of those a real supply of facts — do not label every fact the same way just because they all name a company.

Return JSON:
{{"evidence": [{{"id": "<W-number>", "fact": "<one factual sentence>", "use_for": ["<one or more of: trends, us, players, context>"]}}]}}"""


WRITE_SYSTEM = """You are a writer at Coherent Market Insights (CMI), a market research firm, writing a piece for \
CMI's own blog. The original guest article's own subject is the main subject of the piece you write — a reader who came \
for that subject must find it thoroughly covered — and CMI's market analysis is woven into that subject, never stacked \
on top of it or allowed to take it over. The piece must read as a blog a reader follows start to finish — not a market research report's table of \
contents rendered in prose — while still being grounded entirely in the cited report and evidence: a reader should recognize \
this as CMI's own analysis, with claims that are precise and sourced, but never as a disconnected sequence of report-style \
sections glued together. Avoid a dry, rigid analyst-brief register — it still has no rhetorical hook, no calls to action, no \
urgency or sales enthusiasm about the market, and does not address the reader directly ("you"), but within that it should \
read as fluent, connected writing, not a template filled in section by section. Write in plain, everyday English, the way \
a knowledgeable person explains something to a colleague — contractions are fine, shorter sentences are fine, and you do \
not need the most formal synonym available when a normal one says the same thing just as precisely. This is a blog post, \
not a filed research report or an academic paper: casual, conversational, easy-to-read language is the goal, not stiff \
corporate or analyst-report phrasing. Formal still means correct and professional, not sloppy — no slang, no internet- \
speak, no emoji, and no hype filler. State what the data shows without selling \
the market or the report. Avoid generic engagement-writing filler such as "in today's fast-paced world", "delve", "navigate \
the landscape", "game-changer", "unlock", "revolutionize" — but the goal is fluent, grounded analysis, not merely dodging \
specific words. \
Your prompt contains several distinct sources — the original guest article, the one confirmed CMI report, and web \
evidence for that same confirmed market — and they must never cross-contaminate: a name, market, figure, or claim that \
belongs to one source must never be written as if it came from another, even when it is a real fact and even when it \
appears to be about a related topic. Before naming any market, report, or figure in your output, check which source it \
actually came from in THIS prompt; if you are not certain, do not use it. Respond with JSON only."""

WRITE_USER = """Rewrite the guest article below into a CMI blog post whose MAIN SUBJECT is still the guest article's own \
subject, with CMI's market analysis blended into it, satisfying every requirement.

ABOVE ALL ELSE, THIS IS A PIECE A PERSON SHOULD BE ABLE TO READ SMOOTHLY, START TO FINISH, WITHOUT TRIPPING: every \
rule below exists in service of that goal, not in competition with it, and if following one of them ever produces an \
awkward, confusing, or unnatural sentence, that sentence has failed regardless of which individual rule it technically \
satisfies. Concretely, that means: no awkward repetition of the same word or phrase shape in nearby sentences, no \
grammatical errors (subject/verb agreement, tense consistency, correct punctuation), no semantic errors (a sentence \
that says something different from what was meant, or that doesn't actually follow from the fact it's citing), and no \
logic errors (a claim that contradicts an earlier sentence, a conclusion that doesn't follow from its premise, a \
transition that implies a connection that isn't really there). Before finalizing, read the piece as a reader would, \
start to finish, not rule by rule — if any sentence makes you stop and re-read it, rewrite it.

{sop}

ORIGINAL TITLE: {title}
ORIGINAL TOPIC: {topic_summary}
ANGLE: {bridge_angle}

FULL ORIGINAL ARTICLE (the substance of the piece's main subject — carry its main points over, in entirely new \
wording; see the hard rule below on reusing its wording):
\"\"\"
{original_body}
\"\"\"

RETAINED PARAGRAPH (written by the paying guest author; it contains their backlink — this is the ONLY part of the \
original article above that is allowed to survive into your output):
\"\"\"{sponsor}\"\"\"

TODAY'S DATE: {today}

CMI REPORT: {report_name}
CMI REPORT CONTENT (authoritative source for all market figures, segments, regional data, and companies):
\"\"\"
{report_text}
\"\"\"

WHAT CMI PROVIDES (its own activities/offerings — use this vocabulary to frame the report_context section as market \
intelligence CMI itself produces, not as a restatement of the original article's own angle; in the TITLE these words \
are at most a light supporting touch, never the title's substance — see the title rule below): \
{cmi_keywords}

SEO KEYWORDS (for search/LLM-answer visibility — see the keyword usage rule below):
PRIMARY KEYWORD: {primary_keyword}
SECONDARY KEYWORDS:
{secondary_keywords}

SUPPORTING WEB EVIDENCE (already vetted as real and on-topic by a prior editorial pass — your job now is placement, \
not re-vetting):
{evidence}

Rules:
- The FIRST section in "sections" must be an unheaded intro (heading: "") of exactly 3 paragraphs. Your job is to make \
  the retained paragraph (quoted above as RETAINED PARAGRAPH) read as if it belongs exactly where it lands — infused \
  into the intro seamlessly and smoothly, never as a sentence that looks dropped in or out of place. The retained \
  paragraph was originally written as a MID-ARTICLE paragraph in the guest post — it may open with a connective phrase, \
  refer back to something named earlier ("that structure", "these layers", "this approach"), or assume prior context \
  that no longer exists once it's moved here. You must construct paragraphs 1 and 3 specifically around it to close \
  that gap: read exactly what it says and how it opens and closes, then write the paragraph before and after it so \
  together the three read as one continuous, naturally flowing passage — not as an unrelated sentence stitched between \
  two generic ones.
  SOME RETAINED PARAGRAPHS ARE NOT PROSE AT ALL — just a short linked phrase or a bare anchor text with no sentence \
  around it (e.g. the entire retained paragraph is only "Customer data management systems" as a hyperlink, nothing \
  else). This still counts as "the retained paragraph" and every rule below still applies to it: it is NOT exempt from \
  needing a lead-in and a pivot just because it has no prose of its own to read the opening/closing words of. \
  HARD REQUIREMENT when this is the case: paragraph 1 (the lead-in) MUST itself contain a real sentence that uses the \
  bare anchor phrase's own key noun(s) — not a vaguer stand-in word ("the data," "the systems," "the tools") that \
  merely gestures at the same idea without actually naming it. If the anchor text is "Customer data management \
  systems," paragraph 1 must itself contain the words "customer data" (or equally specific wording naming that same \
  concept) in a real sentence, BEFORE the placeholder paragraph — a lead-in that only talks around the topic in vaguer \
  terms and never actually names it fails this requirement even if it is otherwise well-written, because the anchor \
  phrase then still lands as the first and only time that specific thing is actually named, which is precisely the \
  "dropped in" problem this exists to prevent. Likewise, paragraph 3 must refer back to that SAME concept by name (or \
  a clear synonym of it), not a generic word like "that foundation" or "the basics" that could mean almost anything — \
  a vague callback does not prove continuity, a specific one does.
  ON TOP OF THAT, naming the concept somewhere in paragraph 1 is not enough by itself — the bare phrase still has to \
  land as a grammatical, readable sentence when the placeholder is substituted in verbatim on its own line, not as an \
  isolated fragment with no sentence of its own. You cannot add words INTO the placeholder paragraph itself (it must \
  stay exactly {sponsor_token} and nothing else — see rule 2 below), so the fix has to come from how paragraph 1 \
  ENDS: write paragraph 1's final sentence as a direct, grammatical lead-up that the bare phrase completes when read \
  immediately after it — e.g. if paragraph 1's last sentence ends "...and it starts with getting a handle on your \
  own:" (or "...which depends on one specific piece of infrastructure:" / "...and the foundation for that is:"), the \
  bare phrase that follows reads as that sentence's direct continuation, a grammatical answer to a setup the reader \
  just received, not a non sequitur. This is MANDATORY whenever the retained paragraph is a bare phrase: paragraph 1's \
  closing sentence must end on exactly this kind of lead-up, pointed at the specific bare phrase (not a generic \
  "here's more:" that could precede anything).
  1. A lead-in paragraph you write — it also carries the CMI report link (see the CMI REPORT LINK rule below: the \
  first mention of the market, as the link, within this paragraph) — introducing the original topic and building up to \
  whatever context the retained \
  paragraph's own opening words presume the reader already has. If the retained paragraph opens by naming a specific \
  tool, product, company, or comparison (e.g. it starts mid-comparison, mid-list, or with "This approach..." / "That \
  structure..." / a colon-led definition), you MUST name that same specific thing in this lead-in too — introduce it, \
  say what category it belongs to, and set up the comparison or point it's about to make. The retained paragraph must \
  never be the first place a name, tool, or comparison appears; by the time a reader reaches it, they already know what \
  it's talking about. You are not paraphrasing or quoting the retained paragraph's sentences — you are writing new \
  sentences that establish the same subject so its opening phrase lands on a clear antecedent instead of a non sequitur. Keep this lead-in to 45-60 words.
  2. The literal placeholder text {sponsor_token} and NOTHING else — do not write out the retained paragraph's actual \
  words here, do not paraphrase it, do not summarize it. Copy only this exact character sequence as the entire paragraph \
  string: {sponsor_token}
  This placeholder will be programmatically replaced with the retained paragraph afterward; whatever you put in its place \
  is what ends up BESIDE the real paragraph in the final article, so writing the real content here would duplicate it.
  3. A paragraph you write that moves the piece forward from wherever the retained paragraph leaves off — not by \
  restating what the retained paragraph itself just said, even in your own words. If the retained paragraph makes a \
  specific point, lists specific items, or names specific criteria, this paragraph must NOT summarize, recap, rename, \
  or refer back to that content as a list/point/set of criteria the reader should now recall (no "Those three \
  things...", "That approach...", "Each of those factors..." pointing at the retained paragraph's own content) — that \
  is restating it, and restating counts as reuse just as much as quoting it would.
  This cuts the other way too: do NOT invent your OWN new list, set of steps, or set of criteria in this paragraph and \
  then refer back to it as something already established ("comes down to a few habits: X, Y, and Z... each of those \
  steps...") — a reader has only just met that list in this same paragraph, so treating it as prior context is just as \
  confusing as misquoting the retained paragraph, and a list like that belongs in its own later section with room to \
  actually cover each item, not compressed into one throwaway transition sentence.
  Instead, pick up the retained paragraph's CLOSING idea only far enough to pivot cleanly away from it, into either \
  more of the original topic or the market this article is anchored on (see ANGLE above) — a brief, natural-sounding \
  transition sentence is fine, but the paragraph's real content must be about something genuinely new, not a reflection \
  on or a restatement of a list (the retained paragraph's or one invented on the spot). Keep this pivot paragraph to 45-60 words.
  The retained paragraph's actual content (quoted above) must never appear anywhere in your output — not in the intro, \
  not elsewhere, not even partially reworded, summarized, or referred back to as a unit. The only place it belongs is \
  the placeholder slot, and the placeholder is the literal string {sponsor_token}, never the paragraph's real words or \
  a recap of its ideas. Do not mention the guest author's brand or website anywhere in the article either.
- Every paragraph except the placeholder must be writing you compose fresh. Do not reuse a sentence, or any long run of \
wording, from either the full ORIGINAL ARTICLE or the CMI REPORT CONTENT shown above — not copied verbatim, not lightly \
edited, not moved to a different section, not reassembled from a few of its own clauses. If the original article makes a \
point you also want to make (e.g. the same list of features or the same advice), or the CMI REPORT CONTENT states a \
trend or fact you want to cite, express that point in your own words and sentence structure; reusing either source's \
actual phrasing is not allowed anywhere outside the one retained paragraph. Swapping a handful of individual words for \
synonyms while keeping the same clause order, sentence boundaries, and overall shape is NOT rephrasing — it is the same \
sentence wearing a disguise, and it is forbidden exactly like an unedited copy; genuine rephrasing changes the \
sentence's structure, not just its word choices. The one exception is the market figure itself (see the next rule \
below): the NUMBER and its UNIT must be copied exactly, but the sentence carrying that number must still be your own \
composition, with its own structure — do not lift the report's own sentence just because it happens to contain the \
figure you need. A reader arriving for the original topic must find its main points covered (that is the main subject \
of the piece) — but every word of that coverage and of everything else beyond the retained paragraph, aside from the \
one exact number and unit per figure, must be new.
  Watch especially for a fact or claim that the FULL ORIGINAL ARTICLE and the CMI REPORT CONTENT already state in \
  SIMILAR language to each other (the original article's author may have paraphrased the same report, or a similar \
  one, when writing it) — avoiding either source in isolation is not enough here, because independently rewording \
  each one can still land on wording close to both at once, simply because they were already close to each other \
  before you started. When you notice this situation, deliberately write a THIRD phrasing that resembles neither \
  source's sentence structure, not merely a rewording of whichever one you read most recently.
- Before you finalize, self-check every paragraph you wrote for copying: ask yourself whether any stretch of about ten \
or more consecutive words matches, nearly word-for-word, any sentence in the ORIGINAL ARTICLE or the CMI REPORT \
CONTENT above. Company names, segment names, and figures do not count by themselves — but all the words around them \
do. Wherever you find such a matching stretch, rewrite that sentence with a different structure before finalizing; do \
not leave it in place and do not assume it is fine because a few words differ.
- Enumerations need restructuring, not just rewording: never emit a bare run of five or more company names, segment \
names, or similar list items in the same order any source lists them in. Reorder the items relative to every source, \
and break the list apart — split it across two sentences, or interleave items with your own commentary on each one — \
so the sequence itself is yours even though the individual names are necessarily identical. A list in new words but the \
old order is still the old list.
- Every market figure (sizes, CAGR, shares, years, percentages, demographic/segment shares) must come verbatim from the \
CMI REPORT CONTENT or the SUPPORTING WEB EVIDENCE shown above for THIS specific run — copy the number and unit exactly \
as given there, attached to the same segment/scope it was given for (a figure stated for one segment or one year is not \
interchangeable with another). Never invent, estimate, round, infer, or carry over a number from general knowledge, from \
a different market, or from the FULL ORIGINAL ARTICLE's own claims — the original article may cite real figures, but \
they are for whatever its own author wrote, and the CMI REPORT CONTENT for THIS run is the only authoritative source \
for figures in your output. If a section would otherwise need a figure that is not in either source, write the point \
qualitatively instead of inventing a number.
- FIGURE CLARITY: every sentence that states a dollar figure, a percentage, or a CAGR must itself make clear, in that \
same sentence, what the figure measures — size, forecast value, share, or growth rate — using an ordinary word for it \
("valued at", "a forecast of", "holding a share of"), not just the bare number. Never write a sentence like "the market \
is on a path from USD A to USD B" with no word anywhere telling the reader those are size figures — a reader who skims \
straight to that one sentence, without having read report_context first, must still be able to tell what the number \
means. This applies everywhere a figure appears, including the conclusion, not only in report_context where the figure \
was first introduced; restating a number later in the piece does not inherit the earlier sentence's context.
- The CMI REPORT CONTENT states its headline market-size and CAGR figures using a fixed template — "is estimated to be \
valued at USD A in YEAR1 and is expected to reach USD B by YEAR2, exhibiting a compound annual growth rate (CAGR) of C% \
from YEAR1 to YEAR2" — because every CMI report uses that same phrasing. That exact clause structure must NEVER appear \
in your output, even with individual words swapped for synonyms ("anticipated" for "expected", "climb to" for "reach") \
— that is light editing, not rephrasing, and is forbidden by the no-copy rule above just as much as an unedited copy. \
State the same two dollar figures and the same CAGR percentage using a genuinely different sentence shape: a different \
clause order, a different connecting idea, or a different split across sentences. For example (illustrative only — do \
not reuse this exact wording either; invent your own structure): "Coherent Market Insights pegs the \
{report_name} at USD A for YEAR1, with the firm projecting a climb to USD B by YEAR2 — a C% compound \
annual growth rate." or "The {report_name} carried a USD A valuation in YEAR1 per Coherent Market \
Insights; by YEAR2 that figure is forecast to reach USD B, a C% annual growth rate." or "USD A in YEAR1, USD B by \
YEAR2 — that is the growth Coherent Market Insights forecasts for the {report_name}, a C% compound annual \
rate." (Here the market name is plain text: the one report link was already placed in the intro, see the CMI REPORT \
LINK rule below.) The two numbers, their units, and the CAGR percentage must still come verbatim from the source per \
the rule above; only the sentence construction carrying them must be new.
- Recency: TODAY'S DATE is given above. Describe an event, launch, partnership, acquisition, or other development as \
recent, new, latest, or current ONLY if its source — a dated line in the CMI REPORT CONTENT or a dated SUPPORTING WEB \
EVIDENCE line — places it within the 12 months before TODAY'S DATE. Whenever you mention a specific dated development, \
state its month and year as the source gives them; never drop the date or replace it with vague wording like "recently" \
or "in recent years". Never present an undated claim as a current development. The report's forecast period, base year, \
and market-size figures are projections and measurements, not events — this rule does not apply to them.
- CMI REPORT LINK — its placement is mandatory and specific: the FIRST time the article names the market, that \
mention IS the link. Write it inline as [[CMI_LINK:{report_name}]] inside a sentence of intro paragraph 1 (the lead-in \
you write), so that it lands within the first 100 words of the article and in any case no later than word 200 when the \
retained paragraph is counted in its place. No plain-text mention of the market's full name may appear anywhere before \
it. The anchor text is the market name given above as CMI REPORT: {report_name}, copied exactly — never the name of a \
different market, and never a market name that happens to appear in the FULL ORIGINAL ARTICLE above (the original \
article may itself reference a different CMI report; that is irrelevant here — CMI REPORT: {report_name} is the only \
report this link may ever name). The sentence must read as a natural mention or citation of the market, not an \
advertisement (for example "...the software behind these apps belongs to the [[CMI_LINK:{report_name}]], the market \
Coherent Market Insights tracks for this industry..." — illustrative only, write your own). Do not drop the market's \
full name into the very first sentence of the article just to get the link in early — give the paragraph a \
sentence or two first to set up WHY the market is about to come up (what the apps/tools/technology the reader just \
read about actually run on, or where they come from), so the market-naming sentence lands as the paragraph's natural \
next beat rather than an abrupt, unmotivated pivot. The 100-200 word budget is generous; use some of it before the \
link, not all of it after. Never state or imply \
that the market itself IS a product, a piece of software, or a company — "{report_name}" names a MARKET (an industry \
category that CMI sizes and tracks, made up of many vendors' products), not a single product/platform/software that \
a reader could go use. A sentence like "that shift is powered by the {report_name} — the software that banks use" \
is wrong: it equates the market with a product. Say instead that a company/product belongs to, competes in, is part \
of, or is tracked by the market — never that the market IS the software/platform/tool. This same distinction holds \
everywhere else the market is named later in the article, not only in this first mention. The link appears exactly \
ONCE in the whole article; every later mention of the market name is plain text. This is a mandatory structural \
requirement — the article is incomplete without it.
- Use every piece of SUPPORTING WEB EVIDENCE somewhere in the article, each in whichever section its own content \
actually fits — not wherever is convenient or wherever you happen to be writing when you remember it exists. Each \
evidence line is tagged with an ID in parentheses (e.g. "(W3)") and the section type it was pre-sorted for ([trends], \
[us], [players], [context]), but treat the section tag as a starting hint, not a rule: read what the fact actually \
says and place it by its real subject matter even when that differs from its tag — a fact pre-tagged for one section \
can belong in a different one if its content is really about that other section's subject (for instance, a fact \
filed under one category may really be describing a specific company's move, a regional development, or a \
segment-level shift, in which case it belongs wherever that subject is actually discussed). Only leave a piece of \
evidence out entirely if using it would require distorting what it actually says or forcing it into a section it has \
no real connection to.
- For every piece of evidence you DO use, report it in the top-level "evidence_citations" array: the evidence's own \
  ID (e.g. "W3") and the EXACT paragraph text (copied character-for-character from one of your own "paragraphs" \
  strings above — not a summary, not a snippet, the complete paragraph as you wrote it) that paragraph ended up in. \
  If one paragraph draws on more than one piece of evidence, list it once per evidence ID it used, each entry \
  repeating that same full paragraph text. Only report an evidence ID you genuinely placed somewhere in the article; \
  do not report one you ultimately left out. This is read by the application to show an editor which evidence shaped \
  which paragraph — it does not change how you write, it only asks you to report what you already did.
- This is a blog a reader follows start to finish — one continuous piece of writing that fuses the ORIGINAL ARTICLE's \
own idea and voice with the CMI report's factual substance, not a guest intro bolted onto a stitched-together set of \
independent report sections glued on after it. Where a later section can genuinely pick up a thread left by the one \
before it — a fact, an implication, a question it raised — open with that connection instead of a flat, disconnected \
topic sentence ("The market is segmented by..."). Do this only where the connection is real and would read naturally; \
forcing a callback into every single section produces the opposite of what this is asking for — repetitive, \
mechanical transitions are as much a tell as having none at all. Some sections opening on their own subject plainly is \
fine and expected; the goal is a piece that overall reads as one train of thought with a few genuine connective \
threads running through it, not a transition sentence bolted onto every section header.
- Headings must read as blog section headers for THIS specific article's subject, not as labels copied from a market \
research report's table of contents. Every market-analysis section below still must satisfy its stated requirement (tag it in "sop" \
exactly as before — the heading text itself is unconstrained and separate from that tag), but phrase the heading the \
way a blog covering this exact topic would title that section: specific to what the section actually discusses here, \
in this article, not a generic category name. Do not simply reuse this prompt's own requirement names ("Key Players", \
"U.S. Market Analysis", "Market Segmentation") as headings verbatim — those name the underlying requirement for your \
reference, not the words a reader should see.
- Write in a casual, conversational blog voice, not a formal analyst-report register: favor shorter, direct sentences \
over long ones stacking multiple clauses and qualifiers, use contractions where they read naturally ("it's", "that's", \
"doesn't"), and reach for the normal everyday word over the more formal synonym when both mean the same thing ("show" \
not "demonstrate", "use" not "utilize", "help" not "facilitate", "grow" not "exhibit growth"). When a technical or \
market-specific term first appears, give enough context that an unfamiliar reader can follow what it means without \
stopping. This applies throughout, including the trends, segmentation, and key_players sections where dense jargon- \
stacking and stiff report phrasing are most tempting — precision and plain, casual language are not in conflict; a \
claim is not more rigorous for being harder to parse, and this is a blog post, not a filed research report. Casual \
does not mean sloppy: no slang, no internet-speak, no emoji, still correct grammar and complete sentences — just \
normal spoken English instead of corporate or report-speak.
  NAMED ANTI-PATTERN — separated subject and verb: never write a sentence whose grammatical subject is a long noun \
  phrase or list, pulled even further from its verb by an embedded clause in the middle, so the reader has to hold the \
  subject in memory across a whole clause before reaching what it actually does. This is the single most common cause \
  of a sentence needing a second read. BAD (illustrative shape, not real content): "The same cloud infrastructure, \
  AI-driven advisory, and mobile-first design that the {report_name} supplies to its customers \
  are what let a small business adopt all three at once." — by the time \
  the reader reaches "are what let," they have forgotten the sentence started with a three-item list. GOOD (same \
  content, said directly): "Customers get their cloud infrastructure, AI-driven advisory, and mobile-first design from \
  the {report_name}. That same technology is what lets a small business adopt all three at once." \
  The fix is almost always to split one tangled sentence into two plain \
  ones, each with its subject right next to its verb, rather than finding synonyms for the same structure. Before \
  finalizing any sentence, check: does its subject sit within a few words of its verb? If a reader would have to hold a \
  list or a clause in memory before reaching the verb, rewrite it as two sentences instead.
- SEO keyword usage (for search and LLM-answer-engine visibility — see SEO KEYWORDS above). These phrases must appear \
woven naturally into sentences that would be written anyway; never force one in where it reads like a label pasted \
into prose, and never chase a count or density number while writing — place each one only where it genuinely fits \
what that sentence is already saying:
  These two keyword types work OPPOSITE ways on purpose, because they are different things — PRIMARY is literally \
  the market's own proper name (a name is never "reworded"), SECONDARY keywords are each a topic/idea (an idea is \
  always expressible many ways). Do not let the PRIMARY rule's "always exact, never alter" bleed into how you treat \
  SECONDARY keywords below — a secondary keyword is not a shorter version of the primary rule, it is governed by the \
  opposite instruction:
  - PRIMARY KEYWORD (a proper NAME — never reworded, same as you'd never respell a person's name): this is the full \
  market name EXACTLY as given above, including the word "Market" at the end (e.g. "Wealth Management Platform \
  Market"). Whenever you name the market itself — as opposed to using a pronoun ("it") or "the market" — always use \
  this full phrase in full, never a shortened or truncated version of it (never drop "Market" off the end, e.g. never \
  "wealth management platform" on its own standing in for the real keyword). MINIMUM TARGET: use this exact full \
  phrase at least ONCE in every section of the article (the unheaded intro counts as one section too) — a section \
  that only ever refers to the market as "it" or "the market" and never once names it by its real full phrase has \
  not met this requirement, regardless of how many sections the finished article ends up with. Within a section that \
  has multiple paragraphs, one use anywhere in it is enough; you do not need one per paragraph. Do NOT stack multiple \
  uses of it into one single paragraph just to use it more; that reads as keyword-stuffed and is exactly what this \
  rule is trying to avoid — spread a section's one (or few) uses across its paragraphs rather than clustering them.
  - SECONDARY KEYWORDS (an IDEA, not a name — vary the SENTENCE every time, same as you'd never build the exact same \
  sentence shape twice to make the same point): each one is a fixed set of words whose ORDER is not frozen, not a \
  phrase you may substitute different words into. REORDER, do not REPHRASE: every one of the keyword's own words must \
  still appear in the sentence somewhere — you are free to place them anywhere the grammar needs them and split them \
  across the sentence with other words in between, but you may not swap any of them out for a synonym or a vaguer \
  stand-in word. Example (placeholder market name, not a real one): both "the size of Market-X" and "Market-X size" \
  are correct — every word of the keyword ("Market-X", "size") is present in each, just in a different order; use \
  whichever order fits the sentence you're writing, and switch between the two forms across the article so you are \
  not repeating the exact same "Market-X <word>" shape every time. "how big Market-X has become" is WRONG even \
  though it expresses the same idea: the actual word "size" is gone, replaced by "how big," which drops the keyword \
  itself rather than reordering it — the point of a secondary keyword is that its own specific words appear (for \
  search and LLM-answer-engine visibility, see SEO KEYWORDS above), and a same-idea-different-words paraphrase does \
  not achieve that no matter how well it reads. The only thing you are varying is which sentence structure carries \
  those same words, and where in the article/section you place that sentence — not the words themselves. Prefer the \
  exact static word ORDER ONLY when it already is the smoothest way to say the sentence; the moment two or more \
  secondary keywords start reading alike because they all keep the same "<market> <word>" shape back to back, that \
  repetition is itself a readability problem — vary the SENTENCE CONSTRUCTION around the same required words every \
  time, the same way a person would naturally vary how they build a sentence without ever dropping the specific \
  thing they're actually talking about. As a category, these are not optional — do make a genuine effort to work a \
  meaningful number of them in somewhere in the article. But that requirement is about the category as a whole, not \
  about any single keyword on the list: do not treat this as a checklist you must clear entry by entry. Each one is \
  tagged above with which section its content naturally belongs in — read that section's own content as you write it \
  and build in a keyword's own words (reordered as needed, per the rule above) only where they fit a sentence you \
  would write anyway, seamlessly and grammatically, with nothing about the sentence's construction revealing that it \
  was built to fit a phrase. If a listed secondary keyword's words don't fit naturally anywhere in its tagged section, \
  leave it out — it is completely fine for a section to use none, one, or several of its tagged keywords depending on \
  what genuinely fits, and it is fine for the article overall to end up using well under half the full list if that's \
  what natural writing produces; what matters is that every keyword actually used reads as an unforced, normal part \
  of the sentence it's in, with its own actual words present, not just the general idea. Do not use a secondary \
  keyword's words outside its tagged section just to use it somewhere.
  HARD LIMIT, not a guideline: at most ONE secondary keyword per paragraph, with no exceptions. Before finalizing \
  each paragraph, count how many secondary keywords appear in it — if a paragraph you've written contains two or \
  more, delete all but the single best-fitting one from that paragraph rather than keeping them all, even if that \
  means one of them ends up unused entirely. A section tagged with several keywords is not an instruction to place \
  several of them in its few paragraphs — it is a MENU to choose from, and choosing just one or two good ones beats \
  trying to fit most of the list in. A paragraph with multiple secondary keywords crammed into it reads as an SEO \
  checklist being worked through, not as writing, and is a failure of this rule regardless of how natural any single \
  keyword in it reads on its own.
  - TERTIARY KEYWORDS are entirely optional and not supplied above — if, while writing, a broader related term \
  (a specific underlying technology, a segment name, a close industry synonym) comes up naturally on its own, that's \
  fine and welcome, but never invent or search for one specifically to include; it is not a requirement.
  A reader must never be able to tell this article was written around a keyword list — if a sentence only makes \
  sense as a vehicle for fitting a phrase in, rewrite it or drop that use.
- STRUCTURE AND BLENDING — the original article's subject stays the MAIN subject, and the market analysis is blended \
into it. Before writing, read the FULL ORIGINAL ARTICLE and note its 3-4 main points or subtopics (its major advice, \
steps, or arguments). Then build "sections" as the unheaded intro followed by headed sections of two kinds:
  - ORIGINAL-TOPIC sections ("sop": []): each covers one of those main points in your own words — 1 paragraph, \
  75-90 words per section — following the order of ideas in the original article. Include 4 of them. At least half \
  of them must contain a sentence that ties the point to the market (a trend, a segment, a type of platform or \
  product, or a company covered in a neighbouring market-analysis section), so the two kinds of section read as one \
  argument; never force a tie where the point has none, and never use the same bridging phrasing twice. These sections \
  draw ONLY on the original article's own subject matter — its advice, steps, arguments, and examples. They never carry \
  over a number, percentage, or market-size, segment, region, or company statement from the original article (the CMI \
  report and the supporting evidence are the only sources for market content; if an original point leans on a \
  statistic, state it qualitatively). Write each one from your understanding of the point, not from the original's \
  sentences: decide what the point is in one sentence, then explain it fresh — different sentence structure, different \
  example order, different wording — so that no run of eight consecutive words matches the original.
  - MARKET-ANALYSIS sections: the six requirements below plus the closing conclusion, each in its own section tagged in \
  "sop". Place each where it connects most naturally to the original-topic section just before it (a sensible relative \
  order is trends, report_context, segmentation, subsegment, country_us, key_players, conclusion, with original-topic \
  sections sitting between them). EVERY paragraph in a market-analysis section must be written in terms of the original \
  article's subject: say what this trend, segment, region, or company means for — or how it shows up in — the original \
  subject, instead of describing the market in the abstract. A market paragraph that could be pasted unchanged into any \
  other article about this market has failed this rule.
  Never place more than two market-analysis sections back to back — put an original-topic section between them. The \
  conclusion section comes last and closes the whole piece, original subject and market together. State each market \
  figure in ONE section only; if a later section returns to the same point, refer back to it without repeating the \
  number. These sop names are \
  internal tags only, never heading text (see the heading rule above). Word budget, so the original subject is not \
  crowded out: the intro's two paragraphs you write plus the original-topic sections come to roughly 420-520 words; \
  all market-analysis sections together come to roughly 750-950 words. Each market-analysis requirement below gives a \
  word RANGE per paragraph — a paragraph over its maximum must be tightened, not left long.
  - trends: three distinct trends. Write ONE paragraph string: a single lead-in sentence introducing that three \
  trends are reshaping the market, then each trend as its own "\n- " line (a dash, one space, then the trend) inside \
  that SAME paragraph string — not three separate paragraph strings. Each bullet line names the trend and explains its \
  impact on the market in 40-55 words, not just describing it. This renders as a real bulleted list, which reads far \
  better than three back-to-back "X is the first/second/third force" paragraphs saying the same kind of thing in a row.
  - report_context: two paragraphs of 50-60 words each (not one) integrating the CMI report's framing of the market naturally into \
  the discussion — the market's overall size/growth story, then its key dynamics (drivers, demand factors), as two \
  distinct paragraphs rather than compressing both into one. One of these two paragraphs must also explicitly connect \
  this market's trajectory back to ANGLE/ORIGINAL TOPIC given above — not a generic restatement of the market's \
  importance, but the specific implication of this report's figures and dynamics for the original article's own subject \
  and reader (e.g. what this market's growth or dynamics concretely mean for the kind of reader, product, or decision the \
  original article was about). This is the one thing a reader cannot get from the CMI report alone — the report has no \
  knowledge of the original article's subject, so this connection must be written fresh, grounded in the report's actual \
  figures/dynamics, not asserted vaguely.
  - segmentation: two paragraphs (not one). The FIRST paragraph names the report's major segments — write it as a \
  single lead-in sentence followed by each segmentation line as its own "\n- " bullet inside that same paragraph string \
  (for example one bullet per segmentation category: "- By advisory mode: human, robo, and hybrid." style, 10-20 words each, 3 bullets maximum) rather than cramming all the lines into one dense sentence — a flat list of category names reads far better as \
  a real bulleted list than as prose. The lead-in sentence itself must use the word "segment"/"segmentation" (not a \
  synonym or stand-in word for it) — this is where the segmentation secondary keyword's own words belong, so \
  swapping in a different word here both breaks that keyword rule and makes this section's own subject less clear. \
  Say it directly: the shortest grammatical sentence that uses "segment"/"segmentation" and states the count is \
  always better than a longer one reaching for an extra connector word to pad it out — apply the same plain, \
  no-filler standard to this sentence as to every other sentence in the piece. The SECOND paragraph (45-55 words, ordinary prose, no bullets) explains how the \
  sub-segments within those relate to each other and to the market as a whole.
  - subsegment: exactly two paragraphs of 65-75 words each, each a detailed analysis of one sub-segment from two \
  DIFFERENT major segments (e.g. one from "by type" and one from "by application" — not two sub-segments of the same \
  segment). Each paragraph must cover: growth/adoption drivers, major applications/end-use cases, and its contribution \
  to the overall market.
  - country_us: exactly one paragraph of 130-150 words, on U.S. market factors, growth drivers, \
  adoption trends, and relevant industry developments.
  - key_players: two paragraphs (not one): one of 40-50 words introducing the competitive landscape generally, and a \
  second of 65-80 words naming at least 5 specific companies and connecting each to its products, technology, or strategy.
  - conclusion: exactly one paragraph of 40-50 words closing the article: synthesize the market's overall trajectory and what it implies \
  going forward, grounded only in points already made above. Do not introduce new figures, companies, or claims here, and \
  do not address the reader directly or add a call to action — it closes the analysis, it does not sell anything.
- New title: 40-75 characters (count them before finalizing and shorten if it runs long), in plain, reader-friendly \
language. It must do THREE jobs at once: (1) say what the ORIGINAL article is about — the title should read as a \
descriptive version of the ORIGINAL TITLE's own idea (its subject, audience, or problem, taken from ORIGINAL TITLE and \
ORIGINAL TOPIC above), in your own words, not copied word for word; (2) contain the full primary keyword exactly as \
given (including the word "Market"); and (3) include at least one of these CMI terms as a natural supporting phrase: "trends", "growth \
drivers", "competitive landscape", "market intelligence", "market forecast", "analysis", "insights" — so it reads as \
CMI market intelligence on that subject. Character budget: the primary keyword alone takes about 30-35 characters, so \
express the original subject in a few words and keep the CMI term short. Before finalizing, check that the title \
literally contains one of those CMI terms; if it does not, rewrite it until it does. A reader who sees only the title must be able to tell the subject, \
the market, and that it is an analysis. Do not write it as a how-to, a listicle, or a pure opinion headline.
  SWAP TEST — apply it before finalizing: if you could replace the market name with a different market's name and the \
title would still read as sensible and complete, or if it would still fit a different article on the same market, the \
title is too generic and must be rewritten. A title made only of the market name plus CMI vocabulary ("<Market> Market \
Intelligence: Growth Outlook") fails: the original subject must visibly carry the title and the CMI terms only \
support it. Vary the shape from run to run — for example (format only, not a real market): "Warehouse Safety Planning \
and the Zzz Widget Market: Key Trends", "What Home Repair Hobbyists Mean for the Zzz Widget Market's Growth Drivers", \
or "Zzz Widget Market Intelligence for Safer Warehouse Planning".
- Total length: 1,400-1,700 words — this is a target range, not just a floor. The retained sponsor paragraph ({sponsor_words} words) already counts toward this total, so your own newly written paragraphs must total no more than {max_own_words} words — count them before finalizing. This requires roughly 15-18 paragraphs \
across the intro, the original-topic sections, and the market-analysis sections at the per-section word ranges stated: \
if your draft is shorter, you have not met those ranges, so go back and lengthen the thin paragraphs rather than \
reaching the target by padding sentences with filler words. But going noticeably OVER 1,700 is also a miss, not a \
sign of thoroughness — count your total words before finalizing, and if you are well past 1,700, the fix is to \
tighten existing paragraphs (cut redundant restatement, combine sentences making the same point twice) rather than \
adding more. Satisfying every other rule above — SEO keywords, section transitions, bespoke headings — should not by \
itself push the draft past this range; none of those rules require extra length, only better placement of what you \
were already going to write.

A paragraph string may contain real newline characters ("\n") to hold a lead-in sentence followed by "- " bulleted \
lines (see the trends and segmentation rules above) — this renders as an actual bulleted list, not as literal \
backslash-n text, so use it exactly where those two rules call for it and nowhere else.

Return JSON:
{{"title": "<new title>",
  "sections": [{{"heading": "<heading, or empty string for the intro>",
                "sop": ["<zero or more of: trends, report_context, segmentation, subsegment, country_us, key_players, conclusion>"],
                "paragraphs": ["<paragraph, or a lead-in sentence plus \\n- bullet lines for trends/segmentation>", "..."]}}],
  "evidence_citations": [{{"id": "<evidence ID, e.g. W3>", "paragraph": "<the exact full paragraph text that used it>"}}]}}"""


REVISE_SYSTEM = """You are a senior editor at Coherent Market Insights (CMI), doing a final editing pass on a \
market-intelligence blog piece that another writer already drafted. The facts, figures, and structure are already \
correct and approved — your job is NOT to re-report or re-verify anything, only to edit the EXISTING prose for two \
things: (1) correct SEO keyword usage (remove stuffing, make sure real keyword coverage is still there) and (2) \
readability — smoother, more natural, easier to follow sentence by sentence. Every fact, figure, company name, and \
claim in the draft must survive your edit completely unchanged in substance; you are polishing how it is said, never \
what is said. Respond with JSON only."""

REVISE_USER = """DRAFT TO EDIT (already approved for facts and structure — your job is prose quality only):

TITLE: {draft_title}

{draft_body}

{sop}

SEO KEYWORDS (for search/LLM-answer visibility):
PRIMARY KEYWORD: {primary_keyword}
SECONDARY KEYWORDS:
{secondary_keywords}

Your task: produce a revised version of this exact draft — same facts, same structure, same section order and \
count, same headings unless a heading itself needs a small wording polish — fixing these three things:

1. SEO KEYWORD STUFFING: an automatic scan of the draft found exactly these problems (a paragraph with TWO OR MORE \
secondary keywords, or the primary keyword twice or more):
{stuffing_report}
Fix ONLY the paragraphs listed above: rewrite each so it reads as natural prose with at most ONE of its listed \
secondary keywords (the one that fits best) and at most ONE use of the primary keyword, rephrasing around the \
removed ones rather than leaving gaps or dangling connectives like "That shows" or "and" with nothing to refer to. \
Every paragraph NOT listed already has acceptable keyword usage: do not REMOVE a secondary or primary keyword's \
own words from it (the article's overall keyword coverage must not shrink beyond the removals the list above calls \
for) — you may still REORDER a keyword's own words within its sentence under the READABILITY rule below, but every \
one of those words must still be there afterward, not replaced with a synonym or a paraphrase of the idea. The \
primary keyword may still appear across the whole article, spread out, just never twice in one paragraph.
2. KEYWORD COVERAGE: an automatic scan also checked whether the draft uses enough of the keywords at all, and found \
these required edits:
{coverage_report}
Make exactly the edits listed above — each is a small rewrite of one sentence in the named paragraph so the \
keyword's OWN WORDS sit in it naturally and grammatically. REORDER those words as needed to fit the sentence; do \
NOT reword or paraphrase them — every one of the keyword's own words must appear somewhere in the sentence or \
paragraph exactly as given (e.g. "size" must appear as the word "size", not replaced by a different word like \
"how big" or "scale"; the same goes for every other keyword's own words, whatever they are). Add no other \
keywords, and never put a second secondary keyword's words in a paragraph that already has one.
3. READABILITY: revise the draft for smoother, more natural, more easily followed prose: shorten sentences that stack \
multiple clauses, replace stiff or report-speak phrasing with plainer language, and make sure each paragraph reads as \
something a person would actually write. Keep the casual-but-correct blog voice (no slang, still full sentences) and \
keep each section flowing into the next the way the draft already does — your edits should make the piece read MORE \
smoothly than before, never choppier.
  Scan every sentence for the SEPARATED SUBJECT AND VERB anti-pattern specifically: a long noun phrase or list \
  standing as the subject, pulled even further from its verb by an embedded relative clause in between. Example of the \
  problem (a real sentence flagged as confusing — read it once, you'll feel yourself losing the thread): "The same \
  cloud infrastructure, AI-driven advisory, and mobile-first design that the Wealth Management Platform Market supplies \
  to banks and brokerages are what let a teenager open an account with $20 and get automated, goal-based guidance." The \
  fix is to split it into two direct sentences, each with its subject right next to its verb: "Banks and brokerages get \
  their cloud infrastructure, AI-driven advisory, and mobile-first design from the Wealth Management Platform Market. \
  That same technology is what lets a teenager open an account with $20 and get automated, goal-based guidance." Apply \
  this test to every sentence in the draft, not only ones flagged elsewhere: if its subject is more than about 4-5 \
  words away from its verb, or a clause is wedged between them, split it into two plain sentences instead of finding \
  synonyms for the same tangled shape.
  Also check every sentence containing a dollar figure, percentage, or CAGR: does THAT sentence itself say what the \
  number measures (size, forecast, share, growth rate), or does it only work if the reader remembers an earlier \
  paragraph? "The market is on a path from USD A to USD B" does not say what A and B measure — add the missing word \
  ("valued at", "a forecast of") rather than leaving the number to speak for itself, especially in the conclusion, \
  which a reader may read without having read report_context first.
  Also check every sentence that names the market: does it ever say or imply that the market itself IS a product, software, platform, or company — something a reader could literally go use or buy? The market name (CMI REPORT: {report_name} above) names a MARKET (an industry category CMI sizes and tracks, made up of many vendors’ own products), never a single product/platform itself. A sentence like "that shift is powered by the Wealth Management Platform Market — the software that banks use" wrongly equates the two; fix it to say a product/company belongs to, competes in, or is tracked by the market instead. This check applies everywhere the market is named, not only its first mention. A secondary keyword's words are not frozen in ORDER, but they ARE the specific words required — wherever a sentence \
carrying one reads stiff, repetitive, or ungrammatical (a subject/verb mismatch such as "one clear <keyword> trends \
is the shift...", a keyword stuck on like a label, or several sentences in a row using the same rigid "<market> <word>" \
shape back to back), REORDER it, do not swap its words for synonyms: add an article or verb, move the words around, \
turn "market size" into "the size of the market" (every one of those words is still present, just reordered) — never \
into a same-idea paraphrase that drops the keyword's own words ("how big the market has become" is wrong: "size" is \
gone). The requirement is that every one of the keyword's own words is still there afterward, just not necessarily in \
the original order. Likewise, if the SAME secondary keyword's wording repeats near-identically more than once \
across the article, vary the SENTENCE around it (not the words themselves) so it doesn't read like a template. A \
paragraph that already \
reads smoothly and is not listed in the scan above can stay exactly as it is. The only keywords you ever remove \
entirely (not just reword) are the ones the stuffing scan above asks you to.

Hard constraints — do not violate these while editing:
- Do not add, remove, or reorder sections. The exact same sections, in the exact same order, with the exact same \
"sop" tags per section, must come back.
- If a paragraph in the draft above contains "\n- " bulleted lines (trends and segmentation are written this way), \
keep it as a lead-in sentence plus that same number of "\n- " bullet lines — never flatten it into one dense prose \
sentence, and never add bullets to a paragraph that didn't already have them.
- Do not change any number, date, percentage, company name, or factual claim — with ONE exception: a figure the \
coverage scan above explicitly lists as unsupported must be removed. You are not fact-checking or re-researching \
anything else — every other fact in the draft is already correct; only the sentences carrying it may change.
- The placeholder string {sponsor_token} must appear EXACTLY ONCE, completely unchanged, in exactly the same \
position (paragraph 2 of the unheaded intro section) that it appears in the draft above. Never alter, move, \
duplicate, or remove it.
- The citation pattern [[CMI_LINK:{report_name}]] must appear EXACTLY ONCE, completely unchanged, in the same \
paragraph of the intro where it appears in the draft above, and it must stay the FIRST mention of the market's full \
name in the article (no plain-text mention of that name before it) and within the first 100 words. Never alter, move, \
duplicate, or remove it.
- Length: the target is 1,400-1,700 words. If the draft you were given is OVER 1,700 words, trim it toward 1,700 by \
tightening wordy sentences (never by dropping a fact). If it is UNDER 1,700 words, your version must not be shorter \
than the draft you were given — tighten wording if you like, but keep every fact and give any room you free up back \
to a clearer explanation of the facts already there.

Return JSON in the exact same shape as the draft was written in:
{{"title": "<the title exactly as given above — unless the coverage scan lists a TITLE edit, in which case apply it; otherwise fix at most a wording flaw, and never change what subject it names or drop the primary keyword>",
  "sections": [{{"heading": "<heading>",
                "sop": ["<same sop tags as the matching section above>"],
                "paragraphs": ["<revised paragraph>", "..."]}}]}}"""
