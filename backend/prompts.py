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
- "bridge_angle": one sentence on how the article's topic naturally connects to the top candidate market.
- "claims_to_back": up to 8 of the article's OWN factual claims that are stated as fact but not backed by \
anything inside the article — no source named, no figure given, no example or event behind them (the claims a \
skeptical reader would challenge: a comparison of two named products, "X suits beginners best", an industry \
practice asserted as universal, a how-it-works explanation with no source). For each claim return:
    "id": "C1", "C2", ... in order of appearance in the article;
    "claim": the claim stated in one sentence. Keep every distinctive word exactly as the article has it — \
    named products/companies, the comparison being made, the superlative (e.g. "<Product>'s biggest strength \
    is its simple interface") — do NOT soften or generalize it here; this string must still name the specific \
    assertion that needs backing.
    "why": one short phrase on why it needs outside backing (e.g. "product comparison, no source", "industry \
    assertion, no data").
    "kind": one of "definition" (what something is), "industry_practice" (how the industry works), \
    "company_attribute" (a claim about one or more named companies or products), "comparison" (X is better \
    than / suits / beats Y), "regulatory" (a rule or legal claim).
    "queries": EXACTLY 2 search-engine queries that could find a page backing, qualifying, or contradicting \
    THIS claim (an explainer, a reference page, a vendor or product page, a regulator or reviewer page). They \
    must attack the claim from two different angles with different wording — query 1: the claim's subject and \
    its key terms as plain keywords; query 2: the kind of page that would settle it, phrased differently from \
    query 1 (e.g. "<subject> benefits", "<product> features", "<subject> vendors comparison"). Written the way \
    you'd type into a search engine: 3-7 words, no "OR", no hardcoded year, built around the claim's own \
    distinctive names — never a vague restatement of its topic. Put double quotes only around ONE multi-word \
    product or company name that must match exactly; never quote several names in one query (a query that must \
    contain every quoted name at once finds almost nothing).
  Skip: claims the article itself already backs with a specific source, figure, or named event; claims stated \
  inside PARAGRAPH THAT MUST STAY IN THE FINAL ARTICLE above (that paragraph is fixed and its text is not \
  rewritten, so its wording needs no backing); claims about the MARKET ITSELF (its size, growth, segments, regions, \
  vendors or trends): CMI's own report and market evidence cover those, so they must not be extracted here; \
  purely subjective opinions the article itself clearly frames as \
  opinion. If the article makes fewer than 8 unbacked claims, return only those; if none, return []. Order \
  most-challengeable first."""


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

CLAIMS FROM THE ORIGINAL ARTICLE (each states something as fact that the article did not back itself; your \
first job here is deciding whether the results in the pool below give each one real outside backing):
{claims_block}

SEARCH RESULTS FOR THOSE CLAIMS (each was retrieved by searching for one claim — the "(for: Cn)" tag says \
which claim a result belongs to; the P-number is its id):
{claim_pool}

Task: from the web results, select up to {max_selected} that pass ALL of these tests — a result that fails any one of them must \
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
than {max_selected}, or none, if few results pass all four tests — do not pad with weak, generic, or off-topic results to reach a \
higher count; a shorter list of results that each genuinely earn their place beats a longer list padded with filler. \
For each selected result write two fields. "fact": one factual sentence stating only what the result says. \
"detail": one or two more sentences, taken only from the result text shown above, giving the specifics a writer needs \
to explain and back the fact (amounts, figures, named people or companies, dates, a stated reason or a quote). If the \
result shows nothing beyond the fact (a bare headline), set "detail" to an empty string. In both fields, do not add, \
infer, or round any number: a figure that does not appear in the result's own text must not appear in your output.

Each result above is tagged DATED (with its publish date, already checked to be recent) or UNDATED (an ordinary web \
page with no verifiable publish date, so nothing about it is known to be current). A fact used for "trends" is a claim \
about what is CURRENTLY happening in this market, so its source must be DATED — an UNDATED result must never include \
"trends" in its use_for, no matter how relevant it reads; any DATED result is eligible for "trends" on date alone. An \
UNDATED result is selected only if it states a concrete, citable fact about THIS market that passes all four tests \
above (never a definition or explanation), and then only with use_for ["context"]. When in doubt about an UNDATED \
result, leave it out.

use_for is a LIST: one fact can support more than one part of the article, so list at most TWO sections it genuinely supports, \
most important first (the FIRST tag is the one section the writer will build it into), and nothing it does not truly earn. For example, a named company's acquisition is a "players" \
fact, and it is ALSO a "trends" fact if it illustrates a direction the market is moving in (consolidation, AI adoption, \
platform partnerships). Use "us" only for a fact specifically about the U.S. market. The article needs three distinct \
market trends, a U.S. paragraph, and a key-players section, so when the results allow it, make sure your selection \
gives each of those a real supply of facts — do not label every fact the same way just because they all name a company.

CLAIM BACKING (the second selection job, from the claim-results pool above). For each claim under CLAIMS FROM \
THE ORIGINAL ARTICLE, judge the results tagged "(for: Cn)" and select at most TWO of them — exactly the results \
that give the claim real, outside backing the article itself could never supply. Tag each selected result with \
its stance toward the claim: "supports" (the source confirms the claim as stated), "qualifies" (the source \
confirms part of it, or confirms it only under a condition the reader must know), or "contradicts" (the source \
shows the claim is wrong or wrongly generalized). The selected source must say something beyond what the claim \
already assumes — a definition of a term the claim uses, a rule behind it, a comparative judgement from a named \
reviewer, an industry figure — not just repeat the claim in other words. Hold these to the same credibility test \
as the market results (a named publication, regulator, company, educator, or reviewer; not anonymous \
user-generated pages), and apply the figure rule to each fact too (no number that is not in the source's own \
text). A claim with no result that genuinely earns one gets NO entry — never pad a claim's backing with weak \
results, and never invent a source. It is normal for several claims to end up with none.

Return JSON:
{{"evidence": [{{"id": "<W-number>", "fact": "<one factual sentence>", "detail": "<one or two sentences of specifics from the result text, or an empty string>", "use_for": ["<one or more of: trends, us, players, context>"]}}],
  "claim_backing": [{{"claim_id": "<C-number>", "id": "<P-number>", "stance": "<supports|qualifies|contradicts>", "fact": "<one factual sentence stating only what that source says>"}}]}}"""


PUBLISHER_SYSTEM = """You are a writer at Coherent Market Insights (CMI), writing the opening of a blog post for CMI's own blog. \
The post is about the publisher's subject: the subject of a guest article you are given. The market analysis for that subject is \
written separately afterwards and attached below your text, so your text must not conclude, summarize or wrap the post up: the \
post keeps going after you.
You do not rework the guest article's sentences. You write the opening fresh, as a new article, from the INFORMATION in your \
source material: the guest article's points, steps, reasons and examples, plus outside evidence that backs, qualifies or corrects \
what the article claims. The structure, the sentences and the flow are yours. Every main point of the article must be covered, \
and nothing may be written that is in neither the article nor the evidence.
Voice: plain, everyday English, the way a knowledgeable person explains something to a colleague. Contractions and short \
sentences are fine, and the normal word beats the formal synonym when both mean the same thing. It is a blog post: casual and \
easy to read, but correct and professional, with no slang, no internet-speak, no emoji, no hype, no calls to action and no \
direct address of the reader ("you") in your own added sentences.
Sources: you are given the guest article, ARTICLE EVIDENCE (outside sources that back, qualify or correct the article's own \
claims) and one retained paragraph. Never write a fact, figure, name or claim that is in none of them, and never attribute a \
statement to a source that does not say it. Respond with JSON only."""

PUBLISHER_USER = """Write the opening of a CMI blog post about the guest article's subject: an intro plus exactly three sections.

WHAT YOU ARE DOING
Regenerate the whole piece from its source material. Do NOT edit, paraphrase or follow the guest article sentence by sentence or \
paragraph by paragraph. Read the article for its INFORMATION (what it advises, argues, compares and explains, and with which \
examples), read the evidence for what the outside sources establish, then write one new, coherent opening that covers every main \
point of the article, with the evidence woven in wherever it supports, qualifies or corrects a point. Where the article is thin, \
develop the point from the evidence, never from imagination. The market analysis is NOT your job; it is written afterwards.

ORIGINAL TITLE: {title}
ORIGINAL TOPIC: {topic_summary}

SOURCE MATERIAL: THE GUEST ARTICLE (use its information, not its wording or structure):
----- BEGIN GUEST ARTICLE -----
{original_body}
----- END GUEST ARTICLE -----

RETAINED PARAGRAPH (written by the paying guest author; it contains their backlink; it is the ONLY text that must be reproduced \
exactly, and only through the placeholder):
----- BEGIN RETAINED PARAGRAPH -----
{sponsor}
----- END RETAINED PARAGRAPH -----

CONFIRMED CMI MARKET (used only for the one link in intro paragraph 1; the market itself is the subject of the market block written afterwards): {report_name}

SOURCE MATERIAL: EVIDENCE FOR THE GUEST ARTICLE'S OWN CLAIMS (ARTICLE EVIDENCE items, ids C1, C2, ..., each with its stance and source, followed by the \
CLAIMS FROM THE ORIGINAL ARTICLE list):
{evidence}

THE PLAN (fixed, no exceptions)
A. THE INTRO: the first section, heading "", "sop": [], exactly 3 paragraphs. {intro_budget}
B. THREE PUBLISHER SECTIONS: exactly 3 headed sections, each "sop": [], in the order of ideas of the original article. Each has \
exactly 2 paragraphs (never 1, never 3) of 6 sentences each (100-120 words per paragraph, about 220 words per section, no \
paragraph over 140 words).
WORD TARGETS: this part comes to about 870-950 words including the retained paragraph's real words ({sponsor_words} words). \
If your text comes out shorter because the article is short, develop the points from the ARTICLE EVIDENCE; if it is longer, cut
repetition, not points.

A. THE INTRO (3 paragraphs)
  The retained paragraph was written as a MID-ARTICLE paragraph in the guest post: it may open with a connective phrase, refer \
back to something named earlier ("that structure", "these layers"), or assume context that no longer exists once it is moved \
here. Paragraphs 1 and 3 are built around it so the three read as one continuous, natural passage, never as a sentence \
stitched between two generic ones.
  1. LEAD-IN PARAGRAPH. It introduces the publisher's subject and builds up to whatever context the retained paragraph presumes \
the reader already has. If the retained paragraph opens by naming a specific tool, product, company or comparison, name that \
same thing here, say what category it belongs to and set up the point it is about to make: the retained paragraph must never be \
the first place a name appears. It carries the CMI REPORT LINK (see the link rule below).
  2. THE RETAINED PARAGRAPH'S SLOT. The literal placeholder {sponsor_token} stands for it: never write the retained paragraph's \
actual words, a paraphrase of it or a summary of it. {sponsor_rule}
  The placeholder is replaced programmatically with the real retained text; whatever you write around it ends up beside the \
real text, so writing its content yourself would duplicate it.
  3. PIVOT PARAGRAPH. It moves forward from wherever the retained paragraph leaves off and leads INTO THE FIRST PUBLISHER \
SECTION'S TOPIC, the author's own next point. Do not restate, recap or refer back to the retained paragraph's content as a list, \
point or set of criteria (no "Those three things...", "That approach..."), and do not invent your own list in this paragraph and \
then refer back to it as established. A brief transition sentence is fine; the paragraph's real content is something new.
  The retained paragraph's content must never appear anywhere else in your text, not even reworded. Do not mention the guest \
author's brand or website anywhere.

B. THE THREE PUBLISHER SECTIONS
  - List the original article's main points (its advice, steps, arguments, comparisons) and group them into EXACTLY THREE \
sections, in the original's order. Merge small points; never drop a main point: a reader who came for the original subject must \
find everything it advises or argues, including any comparison of named products or options. If the original has more than three \
headed sections, merge neighbours; if fewer, split by idea; never end up with more or fewer than three.
  - Each section: exactly 2 paragraphs of 6 sentences each. Count the sentences and the words. A paragraph of 3 or 4 sentences is \
too short, and one over 8 sentences is too long: when a point covers many items (for example several named products or options), \
give each item ONE sentence and keep to the two paragraphs.
  - Headings read as blog section headers for this article's subject, specific to what the section discusses, not generic labels.
  - These sections are about the publisher's subject only. They contain no statement about any market's size, growth, segments, \
regions or vendor landscape, and no market figure. If the original article makes such statements, leave them out: the market
block, written afterwards, covers the market from CMI's report.
  - Only the LAST publisher section may end with one short, natural sentence that points toward the wider market this subject \
belongs to; the market analysis picks up from it. Do not conclude, do not summarize the post, do not address the reader with a closing \
thought. The first two sections never mention the market.

CLAIMS AND ARTICLE EVIDENCE (this is how every statement in the original that needs backing is handled)
  The evidence block lists ARTICLE EVIDENCE items (ids C1, C2, ...; each shows its stance and source) and a CLAIMS FROM THE \
ORIGINAL ARTICLE list; each claim is marked BACKED or NO OUTSIDE BACKING FOUND. Handle EVERY listed claim this way, in the \
section where its point belongs:
  1. BACKED, stance supports: write the claim, now with its source in the sentence ("According to <the source's name>, ..."), \
using the item's DETAIL specifics.
  2. BACKED, stance qualifies: write the corrected or conditioned version that the source supports, naming the source; the \
reader still gets the original point, now with its condition.
  3. BACKED, stance contradicts: write what the source establishes instead; do not repeat the original claim.
  4. NO OUTSIDE BACKING FOUND: if it is a general statement that is widely understood and carries no verdict, write it in hedged, \
attributed wording ("is often described as", "many beginners find"). If it is a superlative or a verdict about a named product or \
company ("biggest strength", "weakness", "best for", "the simplest"), drop it entirely. Never state it as established fact.
  5. Never drop a whole main point because its claims are unverified. A comparison of named products stays in the article as a \
section built from what ARTICLE EVIDENCE says about each product (facts and differences taken from named sources, not verdicts). \
If there is no evidence for one product, say only what the article's neutral description supports, or leave that product out; the \
section is still written from the evidence that exists and the article's general advice.
  6. Use at least {article_min} DIFFERENT ARTICLE EVIDENCE items in total, spread over the sections where their points belong. A \
claim about a named product may only state what the evidence states. Use only the source names the items print; never invent a \
source.
  Use each item's DETAIL, not only its headline, and weave the items into your sentences instead of one item per sentence. \
Report every item you use in the top-level "evidence_citations" array: the item's id (C1, C2, ...) and the EXACT paragraph text, \
copied character for character from one of your own "paragraphs" strings. If one paragraph uses several items, list it once per \
id. Report only items you actually placed.

RULES
- FIGURES. A figure about a claim (a percentage, an amount, a count) must come verbatim from ARTICLE EVIDENCE. Never invent, \
estimate, round, infer or carry over a number from general knowledge or from the original article's own text; if a point needs a \
figure you do not have, write it qualitatively. Every sentence that states a dollar figure or percentage says in that same \
sentence what it measures.
- RECENCY. State the month and year of every dated development as the source gives them; describe something as recent, new or \
current only if its source places it within the 12 months before TODAY'S DATE: {today}. Never present an undated claim as current.
- CMI REPORT LINK (mandatory). The FIRST time your text names the market, that mention IS the link, written inline as \
[[CMI_LINK:{report_name}]] inside a sentence of intro paragraph 1, so it lands within the first 100 words of the post and no later \
than word 200 when the retained paragraph is counted in its place. No plain-text mention of that market name may come before it, \
and the anchor is exactly {report_name}, never another market's name. The sentence reads as a natural mention of the market, not an \
advertisement, and the paragraph gives a sentence or two of setup before it (what the subject runs on or comes from) so the mention \
lands as the next natural beat. The link appears exactly ONCE in your text. Never say or imply that the market IS a product, \
software or company: it is an industry category that CMI sizes and tracks, made of many vendors' products.
- NAMES. Spell every company and product name the way the ARTICLE EVIDENCE prints it when it differs from the original article's
spelling (for example unusual capitalization).
- VOICE. Short direct sentences, contractions where natural, the everyday word over the formal synonym, enough context at a \
term's first appearance for an unfamiliar reader. A sentence's subject sits within a few words of its verb: if a reader would have \
to hold a list or clause in memory before reaching the verb, split it into two plain sentences. Because you never address the \
reader as "you", name who is acting in generic terms ("a business", "marketing teams", "companies") the first time a paragraph \
needs an actor, and use "the team", "the company", "the business" or "it" only after an earlier sentence of the same paragraph \
has named them. Every "the X" must point to something this piece has already named.
- FORMAT. Paragraph strings are plain text; no bullet lists here.

OUTPUT CHECKLIST (verify before you answer): "sections" holds exactly 4 objects: (1) the intro with "heading": "" and 3 \
paragraphs; (2)-(4) the three publisher sections, each with a heading and exactly 2 paragraphs. Every "sop" is []. No section is \
a conclusion. "evidence_citations" lists the ARTICLE EVIDENCE items you used.

Return JSON in exactly this shape (4 section objects; every "sop" is an empty list; paragraph text is plain prose):
{{"sections": [
  {{"heading": "", "sop": [], "paragraphs": ["<intro paragraph 1: the lead-in, carrying the CMI link>", "<intro paragraph 2: the retained paragraph's slot, with the placeholder>", "<intro paragraph 3: the pivot into the first section's topic>"]}},
  {{"heading": "<heading for main point 1>", "sop": [], "paragraphs": ["<paragraph 1 of 6 sentences>", "<paragraph 2 of 6 sentences>"]}},
  {{"heading": "<heading for main point 2>", "sop": [], "paragraphs": ["<paragraph 1 of 6 sentences>", "<paragraph 2 of 6 sentences>"]}},
  {{"heading": "<heading for main point 3>", "sop": [], "paragraphs": ["<paragraph 1 of 6 sentences>", "<paragraph 2 of 6 sentences>"]}}
 ],
 "evidence_citations": [{{"id": "<article evidence id, e.g. C2>", "paragraph": "<the exact full paragraph text that used it>"}}]}}"""


MARKET_SYSTEM = """You are a writer at Coherent Market Insights (CMI), a market research firm, writing the rest of a blog post for CMI's \
own blog. The opening of the post is already written and is given to you; it is about the publisher's \
subject. Your part is the market analysis: it follows the opening after ONE explicit bridge and takes about 40% of the post's words. \
Every market paragraph says what it means for the publisher's subject, so the whole post reads as one argument that moves from the \
subject to the market it lives in. The opening ends pointing toward the market; you pick up from there.
Voice: plain, everyday English, the way a knowledgeable person explains something to a colleague. Contractions and short \
sentences are fine, and the normal word beats the formal synonym when both mean the same thing. It is a blog post, not a filed \
research report: casual and easy to read, but correct and professional, with no slang, no internet-speak, no emoji, no hype, no \
rhetorical hook, no calls to action, no sales enthusiasm about the market or the report, and no direct address of the reader \
("you"). State what the evidence shows; do not sell. Avoid generic engagement filler such as "in today's fast-paced world", \
"delve", "navigate the landscape", "game-changer", "unlock", "revolutionize".
Sources: you are given the opening, the confirmed CMI report, and MARKET EVIDENCE (recent news about that market). They must never \
cross-contaminate: a name, figure or claim that belongs to one source must never be written as if it came from another. Before \
naming any company, market or figure, check which source in THIS prompt it came from; if you are not certain, do not use it. \
Respond with JSON only."""

MARKET_USER = """Write the rest of the post (the market block) and the title of the CMI blog post whose opening is given below.

{sop}
(That list names what must be COVERED. It is not an order: follow the PLAN below for the order.)

ORIGINAL TITLE: {title}
ORIGINAL TOPIC: {topic_summary}
ANGLE (background only, written before the market was confirmed): {bridge_angle}
The CONFIRMED CMI REPORT below is authoritative. If ANGLE or ORIGINAL TOPIC name a different, narrower or neighbouring market, \
ignore that and treat CMI REPORT: {report_name} as the only market.

THE POST SO FAR (the opening, already written, about the publisher's subject; do not rewrite or repeat it):
----- BEGIN THE POST SO FAR -----
{part1}
----- END THE POST SO FAR -----
The post so far ends pointing toward the market. Your first section, report_context, is the BRIDGE and must continue from where the post so far stops, in its voice, without repeating what it already said.

TODAY'S DATE: {today}

CMI REPORT: {report_name}
CMI REPORT CONTENT (authoritative source for all market figures, segments, regional data and companies):
----- BEGIN CMI REPORT -----
{report_text}
----- END CMI REPORT -----
COMPANY NAMES TO USE (cleaned from the report's company list; the report text above can carry scraping artifacts such as an \
address word glued onto a name): {key_players}

WHAT CMI PROVIDES (its own activities; use this vocabulary to frame the report_context section as market intelligence CMI \
produces; in the TITLE these words are at most a light supporting touch): {cmi_keywords}

SEO KEYWORDS (for search and answer-engine visibility; see the SEO rule below):
PRIMARY KEYWORD: {primary_keyword}
SECONDARY KEYWORDS (each is tagged with the section it belongs in):
{secondary_keywords}

MARKET EVIDENCE (already vetted as real and on-topic; your job is placement, not re-vetting), grouped by the market section it serves:
{evidence}

THE PLAN (fixed, no exceptions)
Write exactly SEVEN sections, each tagged in "sop", in this order: report_context FIRST (it is the bridge), then trends, \
segmentation, subsegment, country_us and key_players in whichever order reads most naturally after the post so far, then conclusion LAST. \
One section per requirement: never split a requirement across two sections, never add a section. Do NOT write an intro, do NOT \
write publisher sections: the post so far has them.
WORD TARGETS: the market block comes to about 570-650 words; with the post so far ({part1_words} words) the whole post comes to \
1,400-1,700 words (never over 1,750). If the block runs long, tighten existing paragraphs (cut restatement) rather than dropping \
a section.

SECTION SPECIFICATIONS
  - report_context (the BRIDGE): exactly ONE paragraph of 3 sentences (55-65 words). Its FIRST sentence explicitly turns from the \
publisher's subject to the market that subject lives in, picking up from the last paragraph of the post so far (the point of the turn, not a \
fixed wording: this subject sits inside this market, and here is that market's size and direction). The rest integrates the CMI \
report's framing, its overall size/growth story and key dynamics (drivers, demand factors), and explicitly connects the market's \
trajectory back to the publisher's subject and its reader: not a generic restatement of the market's importance, but the \
specific implication of the report's figures and dynamics for the kind of reader, product or decision the post so far is about. The report knows nothing about the post so far, so this connection must be written fresh and grounded in the report's actual figures.
  - trends: three distinct trends. ONE paragraph string of 95-110 words: a single lead-in sentence (about 15 words) saying three \
trends are reshaping the market, then each trend as its own "\\n- " line (a dash, one space, then the trend) inside that SAME \
paragraph string, not three separate paragraphs. Each bullet is 1-2 sentences, 25-32 words: it names the trend, explains its \
impact on the market and rests on at least one MARKET EVIDENCE item (two where space allows). This renders as a real bulleted \
list.
  - segmentation: ONE paragraph of 50-60 words INCLUDING its bullets. A single lead-in sentence that uses the word "segment" or \
"segmentation" itself (not a stand-in word such as "axes" or "breakdown") and states how many segments the report uses, then each \
segmentation line as its own "\\n- " bullet inside the same paragraph string (3 bullets maximum, 10-20 words each, for example \
"- By <segment type>: <its categories>."). Say it directly; the shortest grammatical lead-in beats a longer one padded with \
connector words. Any words left belong in the same paragraph, saying how the sub-segments relate to the market; do not add a \
second paragraph.
  - subsegment: exactly TWO paragraphs of 3 sentences each (45-55 words), each a detailed analysis of one sub-segment from two \
DIFFERENT major segments (for example one from "by type" and one from "by application"), covering its growth or adoption drivers, \
its major applications or end uses and its contribution to the overall market.
  - country_us: ONE paragraph of 5 sentences (100-115 words; the SOP requires at least 100 and at most 200) on U.S. market \
factors, growth drivers, adoption trends and relevant industry developments, using MARKET EVIDENCE for the developments.
  - key_players: exactly TWO paragraphs (two, never one). The first (2 sentences, 22-30 words) introduces the competitive \
landscape generally. The second (4 sentences, one per company, each at most 15 words, 50-60 words in all) names exactly FOUR \
companies from the COMPANY NAMES TO USE list or from MARKET EVIDENCE, and gives each ONE concrete detail (a product, a move, a \
partnership or a date) taken from the report or the evidence. Never write a bare list of names and never name a company without a \
concrete detail. Use each company's name exactly as in COMPANY NAMES TO USE or as the evidence prints it.
  - conclusion: ONE paragraph of 4 sentences (70-80 words), last in the post: (1) the main takeaway for the publisher's subject, \
(2) where the market is heading, in one sentence, (3) what that means for a reader of the publisher's subject. It closes BOTH the \
publisher's subject and the market. Grounded only in points already made in the post so far or above: no new figures, companies or claims, \
no direct address of the reader and no call to action.
  - Every market paragraph is written in terms of the publisher's subject: it says what its trend, segment, region or company \
means for, or how it shows up in, that subject. A market paragraph that could be pasted unchanged into any other article about \
this market has failed.
  - State each market figure in ONE section only; if a later section returns to the point, refer back to it without repeating the \
number. The sop names (trends, report_context, ...) are internal tags and never heading text.

EVIDENCE RULES
- Use at least {min_evidence} DIFFERENT MARKET EVIDENCE items in total: each of the three trends rests on at least one (two where \
space allows), the U.S. paragraph uses at least two and key players uses its four companies' details. Every paragraph that makes a \
claim follows the shape claim, then proof (the named company, publication or regulator, the month and year, and the concrete \
detail from the item's FACT and DETAIL: an amount, a product, a deal, a rule, a figure), then why it matters to the publisher's \
reader. A market claim with nothing behind it is filler: supply the proof from the evidence or the report, or cut the claim.
- Use the DETAIL, not only the headline: every item you use contributes at least one concrete specific, in your own words, never \
lifted word for word. Weave items together (cause and effect, contrast, a pattern across several companies) instead of one item per \
sentence, and never place an item in a section it does not belong in just to use it up. Minimums are capped by how much evidence a \
group really has, and you must never invent proof to meet one.
- Report every evidence item you use in the top-level "evidence_citations" array: the item's own id (for example W3) and the EXACT \
paragraph text, copied character for character from one of your own "paragraphs" strings. If one paragraph uses several items, \
list it once per id. Report only items you actually placed.

RULES THAT APPLY EVERYWHERE
- FRESH WRITING (strict, for the CMI report). Every sentence you write must be composed fresh. Do not reuse a sentence or any long \
run of wording from the CMI REPORT CONTENT: not copied, not lightly edited, not moved to another section, not reassembled from \
clauses. Swapping a few words for synonyms while keeping the clause order and sentence shape is not rephrasing; genuine rephrasing \
changes the structure. Before finalizing, check every paragraph: if about ten or more consecutive words match a sentence in the \
report, rewrite it. Names and figures do not count by themselves, but the words around them do. Never emit a run of five or more \
company or segment names in the order the report lists them: reorder them and break the list apart across sentences or with your \
own commentary. Do not repeat sentences from the post so far either.
- FIGURES. A market figure (size, CAGR, share, year, percentage, segment share) must come verbatim from the CMI REPORT CONTENT or \
MARKET EVIDENCE, attached to the same segment and scope it was given for. Never invent, estimate, round, infer or carry over a \
number from general knowledge, from another market or from the post so far; if a point needs a figure you do not have, write it \
qualitatively. Every sentence that states a dollar figure, percentage or CAGR must say in that same sentence what it measures \
(size, forecast, share, growth rate) in ordinary words ("valued at", "a forecast of", "holding a share of"), also in the \
conclusion.
- REPORT HEADLINE FIGURES. The report states its market-size and CAGR figures in a fixed template ("is estimated to be valued at \
USD A in YEAR1 and is expected to reach USD B by YEAR2, exhibiting a compound annual growth rate (CAGR) of C% from YEAR1 to \
YEAR2"). That clause structure must never appear in your output, even with synonyms swapped. State the same two dollar figures and \
the CAGR in a genuinely different sentence shape (a different clause order, connecting idea or split across sentences); the \
numbers and units stay verbatim. The market name in that sentence is plain text, because the one report link is already in the post so far.
- RECENCY. Describe an event, launch, partnership or acquisition as recent, new, latest or current ONLY if its source (a dated \
report line or a dated evidence item) places it within the 12 months before TODAY'S DATE. State the month and year of every dated \
development as the source gives them; never replace the date with "recently" or "in recent years"; never present an undated claim \
as a current development. Forecast periods, base years and market sizes are projections, not events.
- THE MARKET NAME. The one CMI report link is in the post so far, so in your text the market name is plain text. Never say or imply that \
the market IS a product, software or company: it is an industry category that CMI sizes and tracks, made of many vendors' \
products. Say that a company or product belongs to, competes in or is tracked by the market.
- COMPANY SPELLING. Spell every company and product name the way COMPANY NAMES TO USE or the evidence prints it, and drop any \
scraping artifact glued onto a name.
- HEADINGS. Headings read as blog section headers for THIS post's subject, specific to what the section discusses, not labels \
copied from a market report's table of contents. Never use this prompt's requirement names ("Key Players", "Market \
Segmentation", "U.S. Market Analysis") as headings.
- VOICE. Casual, conversational blog voice: shorter direct sentences, contractions where natural, the everyday word over the \
formal synonym ("show" not "demonstrate", "use" not "utilize", "grow" not "exhibit growth"), enough context at a term's first \
appearance that an unfamiliar reader can follow. This applies equally to the trends, segmentation and key_players sections, where \
jargon is most tempting. A sentence's subject sits within a few words of its verb: if a reader would have to hold a list or \
clause in memory before reaching the verb, split it into two plain sentences. Because you never address the reader as "you", \
name who is acting in generic terms ("a business", "marketing teams", "companies") the first time a paragraph needs an actor, \
and use "the team", "the company", "the business" or "it" only after an earlier sentence of the same paragraph has named them. \
Every "the X" must point to something this piece has already named.
- SEO KEYWORDS. Keywords appear woven into sentences that would be written anyway; never forced in, and never chase a count while \
writing.
  PRIMARY KEYWORD: the full market name exactly as given, including "Market" at the end, never shortened. Use it at least ONCE in every one of your seven sections and never twice in the same paragraph. A section that only says "it" or "the market" has not met this.
  SECONDARY KEYWORDS: use between 4 and 6 of the listed secondary keywords in the whole post, no more. All of these limits are \
hard: (1) at most ONE secondary keyword per paragraph; (2) each keyword at most once; (3) its words may be REORDERED but every \
word of it must appear, never swapped for a synonym ("how big the market is" does not contain "size"); (4) the exact static form \
"<primary-name-without-Market> market <word>" may appear at most TWICE in the entire post, and every other keyword is reordered \
(for example "<word> of the <name> market" or "the <name> market's <word>"); (5) put a keyword only in the section it is tagged \
for, and only if it fits a sentence you would write anyway; if it does not fit, skip it; (6) never put a secondary keyword in the \
title. A reader must never be able to tell the post was written around a keyword list.
- TITLE. 40-75 characters (count them), plain reader-friendly language, doing three jobs at once: (1) says what the ORIGINAL \
article is about, as a descriptive version of ORIGINAL TITLE's own idea (its subject, audience or problem) in your own words; \
(2) contains the full primary keyword exactly as given, including "Market"; (3) includes at least one of these CMI terms as a \
natural supporting phrase: "trends", "growth drivers", "competitive landscape", "market intelligence", "market forecast", \
"analysis", "insights". The primary keyword takes about 30-35 characters, so state the original subject in a few words and keep \
the CMI term short. Check that it literally contains one of those terms. Not a how-to, a listicle or a pure opinion headline. \
SWAP TEST: if you could replace the market name with another market's name and the title would still read as sensible, or it \
would fit a different article on the same market, it is too generic; the original subject must visibly carry the title and the \
CMI term only supports it. Vary the shape from run to run, for example (format only): "Warehouse Safety Planning and the Zzz \
Widget Market: Key Trends".
- FORMAT. A paragraph string may contain real newline characters ("\\n") only to hold a lead-in sentence followed by "- " bullet \
lines, exactly where the trends and segmentation rules call for it, and nowhere else.

OUTPUT CHECKLIST (verify before you answer): "title" is filled. "sections" holds exactly 7 objects in this order: report_context, \
then trends, segmentation, subsegment, country_us and key_players in the order you choose, then conclusion last. Each has a \
heading and exactly one entry in "sop". The subsegment and key_players sections have exactly 2 paragraphs; every other section has \
exactly 1. No intro and no publisher section. "evidence_citations" lists the market evidence items you used.

Return JSON in exactly this shape (a title and 7 section objects; the sections after report_context and before conclusion may \
be in any order you choose):
{{"title": "<the title>",
 "sections": [
  {{"heading": "<heading>", "sop": ["report_context"], "paragraphs": ["<the bridge paragraph>"]}},
  {{"heading": "<heading>", "sop": ["trends"], "paragraphs": ["<lead-in sentence>\\n- <trend 1>\\n- <trend 2>\\n- <trend 3>"]}},
  {{"heading": "<heading>", "sop": ["segmentation"], "paragraphs": ["<lead-in sentence>\\n- <segment line 1>\\n- <segment line 2>\\n- <segment line 3>"]}},
  {{"heading": "<heading>", "sop": ["subsegment"], "paragraphs": ["<sub-segment 1 paragraph>", "<sub-segment 2 paragraph>"]}},
  {{"heading": "<heading>", "sop": ["country_us"], "paragraphs": ["<U.S. paragraph>"]}},
  {{"heading": "<heading>", "sop": ["key_players"], "paragraphs": ["<landscape paragraph>", "<four-company paragraph>"]}},
  {{"heading": "<heading>", "sop": ["conclusion"], "paragraphs": ["<the conclusion paragraph>"]}}
 ],
 "evidence_citations": [{{"id": "<evidence id, e.g. W3>", "paragraph": "<the exact full paragraph text that used it>"}}]}}"""


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
paragraph of the unheaded intro section where it appears in the draft above — whether that is its own paragraph or it \
sits inside one of the sentences there. Never alter, move, duplicate, or remove it.
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
