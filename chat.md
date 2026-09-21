Yesterday 4:53 AM
Theme 2_Troubleshooting_Smart Guided Troubleshooting Engine(1).pdf
PDF
whats are the primary challenges while building this?

For this Smart Guided Troubleshooting Engine, the hardest part is not building the UI or calling an LLM. The real challenge is making the system deterministic, safe, fast, and grounded in a finite catalog of verified troubleshooting actions.

The PDF’s core pipeline is:

Query Enrichment → Structure Extraction → Deeplink Mapping & Sequencing → Fast-Path Caching → REST API.

The primary engineering challenges
#	Challenge	Why it's hard
1	Understanding messy user queries	Users won't say “Battery drain troubleshooting.” They'll say “my phone dies so fast after the latest update.” You need to map many phrasings to the same canonical problem.
2	Reliable structured extraction	Turning free text into something like {device, issue, symptoms, intent} is easy with an LLM, but hallucinated entities or incorrect fields can corrupt the entire downstream pipeline.
3	Mapping problems → exact settings screens	This is probably the core differentiator. You need to map a canonical issue to the correct device-setting action/deeplink, not merely recommend a generic support article.
4	Correct action sequencing	Multiple actions may be required. They have to be ordered logically—e.g. safe/reversible settings first, destructive or disruptive actions later.
5	Preventing invalid/unsafe links	The specification explicitly requires deeplinks to come from the provided catalog. You cannot let the model invent URLs or modify catalog URLs.
6	Sub-300 ms fast path	Semantic matching + validation + response generation can easily exceed the target latency. Caching becomes an architectural requirement rather than an optimization.
7	Semantic retrieval at scale	You need to recognize that “battery disappearing overnight”, “phone loses 30% while sleeping”, and “idle drain” may represent the same underlying problem without relying purely on keyword matching.
8	Validation / grounding	Every generated action needs to be checked against the catalog and output schema. The LLM should not be the source of truth.
9	Handling missing catalog coverage	The PDF provides a bixby://dummy_positive placeholder for situations where a valid Settings screen isn't currently indexed. Your system needs a deterministic fallback rather than hallucinating a URL.
10	Query paraphrase coverage	The required query_variations means the retrieval layer needs to handle formal, casual, keyword-only, frustrated, typo-heavy, etc. versions of the same problem.
1. Query understanding is deceptively difficult

Consider:

"battery is dropping crazy fast"

"phone gets hot and loses 20% overnight"

"after the update my battery sucks"

"why does my phone die when I'm barely using it"

These are different strings but potentially the same troubleshooting intent.

So your pipeline should probably normalize them into something like:

{
  "topic": "Troubleshooting",
  "device": "phone",
  "issue": "battery_fast_drain",
  "symptoms": [
    "rapid_battery_loss",
    "idle_drain"
  ]
}

The challenge is deciding what is deterministic business logic versus what requires semantic/LLM reasoning.

2. LLM hallucination is one of the biggest risks

The PDF explicitly calls out the challenge of:

preventing LLM hallucination and strictly adhering to source text / catalog constraints.

Suppose the catalog contains:

Settings → Battery → Battery protection

The LLM must not invent:

bixby://settings/battery/advanced-health

just because it sounds plausible.

The correct architecture is:

LLM
 ↓
canonical intent
 ↓
retrieval
 ↓
catalog
 ↓
validated deeplink
 ↓
response

Not:

User → LLM → invented troubleshooting solution

That distinction is extremely important.

3. Deeplink mapping is probably your hardest technical component

Your engine needs to answer:

"What exact screen should the user open?"

rather than merely:

"What should the user do?"

For example:

User:
"My battery is draining overnight"

        ↓

Canonical problem:
battery_fast_drain

        ↓

Retrieve:
Battery optimization
Background usage limits
Battery usage

        ↓

Rank

        ↓

Validate against catalog

        ↓

Generate ordered action plan

And the PDF specifically warns against parent-menu matching and requires the correct action hierarchy.

That's a much more interesting engineering problem than ordinary RAG.

4. Sequencing actions safely

Suppose troubleshooting requires:

1. Check battery usage
2. Restrict background activity
3. Disable a setting
4. Restart device
5. Factory reset

You don't want the system randomly returning these based on semantic similarity.

You need an explicit ordering model:

Safe / diagnostic
        ↓
Reversible configuration
        ↓
More disruptive action
        ↓
Manual intervention

The specification also distinguishes categories such as standard, critical, and manual, with critical/disruptive actions ordered last.

This should therefore be implemented as rules/metadata, not left entirely to an LLM.

5. The <300 ms requirement changes the architecture

This is another major challenge.

A naïve implementation:

Request
 ↓
LLM
 ↓
Embedding
 ↓
Vector DB
 ↓
LLM
 ↓
Validation
 ↓
Response

could easily become too slow.

Instead, you want:

                 ┌── Cache HIT ──→ Response
                 │
Request → Normalize
                 │
                 └── Cache MISS
                         ↓
                  Semantic Retrieval
                         ↓
                    Validation
                         ↓
                       Cache
                         ↓
                     Response

The PDF explicitly defines a Fast-Path Caching component for verified plans with a target response time below 300 ms.

So I would treat caching as a first-class component from day one.

The most important architectural principle

I would divide the system into three trust levels:

🟢 Deterministic

Things the system must never hallucinate:

Catalog URLs
Action names
Categories
Schema
URL validity
Action ordering constraints
🟡 Semantic

Things where ML/embeddings are useful:

"battery dying quickly"
        ≈
"battery drains overnight"
        ≈
"phone loses charge while idle"
🔵 Generative

Things where an LLM can help:

Natural-language explanation
Description of why an action may help
Query normalization
Paraphrase generation

The LLM should sit around the deterministic engine, not control it.

If I were building it, I'd focus on these 5 problems first
                    SMART TROUBLESHOOTING ENGINE

                         USER QUERY
                             │
                             ▼
                  ┌─────────────────────┐
                  │ 1. QUERY ENRICHMENT  │
                  │ Normalize + classify │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ 2. STRUCTURE        │
                  │ EXTRACTION           │
                  │ Canonical problem    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ 3. RETRIEVAL +      │
                  │ DEEPLINK MAPPING    │
                  │ Catalog-grounded    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ 4. PLAN VALIDATOR   │
                  │ URL + schema +      │
                  │ ordering + safety   │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ 5. CACHE            │
                  │ Verified plans      │
                  └──────────┬──────────┘
                             │
                             ▼
                         REST API
In terms of difficulty, the real engineering core is:

Semantic understanding → Catalog retrieval → Deeplink validation → Action sequencing → Low-latency caching

The REST API and frontend are comparatively straightforward.

And that's why I would not start by building the UI. I'd first build a working canonical problem → verified action-plan engine, then expose it through FastAPI and put the UI on top.

Theme 2_Troubleshooting_Smart Guided Troubleshooting Engine.pdf
PDF
300ms...

Exactly — the 300 ms requirement is the constraint that should shape the whole design.

The brief specifically says the Fast-Path Caching component must serve cached, verified plans with sub-300 ms response times, and its engineering challenge is high-throughput semantic retrieval without model inference on the critical path.

So your idea of follow-up questions needs to be designed around that.

Don't do this
User: "Volume button not working"
        ↓
LLM
        ↓
Think about possible causes
        ↓
Generate question
        ↓
Response

That will fight your latency target.

Do this
User query
    ↓
Local / precomputed semantic matching
    ↓
Is this a known diagnostic state?
   /                    \
 YES                     NO
  ↓                       ↓
<300ms response       Ask follow-up
                            ↓
                       User answers
                            ↓
                    Local state matching
                            ↓
                     Verified plan

The follow-up itself doesn't need to be <300 ms if it's a new conversational turn. The 300 ms target is much more naturally applied to serving a known, verified troubleshooting plan.

This gives you a very clean architecture

I'd split the engine into two paths:

⚡ Fast path
Query
 ↓
Normalize
 ↓
Embedding
 ↓
Vector lookup
 ↓
Catalog lookup
 ↓
Validation
 ↓
Cached verified plan

Target: <300 ms

No LLM.

🧠 Diagnostic path
Unknown / ambiguous query
          ↓
Determine missing information
          ↓
Ask ONE question
          ↓
User answer
          ↓
Update diagnostic state
          ↓
Fast local lookup
          ↓
Verified action

Here, the intelligence is used to decide what information is missing, not to generate the actual troubleshooting solution.

And "volume button not working" is perfect for this

Pre-build a diagnostic tree:

VOLUME_BUTTON
│
├── volume UI appears?
│      │
│      ├── YES
│      │    └── Does slider work?
│      │          ├── YES → button-specific branch
│      │          └── NO  → volume-system branch
│      │
│      └── NO
│           └── button response branch
│
└── physical symptoms?
       ├── stuck/damaged → manual/service
       └── normal → software troubleshooting

The model isn't inventing this tree at runtime.

You define the diagnostic graph.

The AI's job is essentially:

"Which node does the user's message correspond to?"

and:

"Which question moves us to the next node?"

This is actually stronger for PRISM

Your pitch can become:

A low-latency, catalog-grounded troubleshooting engine that progressively diagnoses ambiguous problems instead of blindly matching users to support articles.

And you can demonstrate:

Input

"Volume button isn't working."

Engine

"When you press it, does the volume indicator appear on screen?"

User

"Yes."

Engine

"Can you change the volume using the on-screen slider?"

User

"Yes."

Engine

"The volume system is responding. Let's check the button-specific path."

→ Verified Samsung action/deeplink

The interesting part is that the first query didn't contain enough information to select an action, so your system deliberately doesn't pretend that it does.

One correction to my earlier recommendation

For this particular brief, I wouldn't put DeepSeek on the normal request path at all.

Use DeepSeek during development to generate/query-test variations and potentially as a fallback for genuinely unseen language. But production should be:

local semantic retrieval + deterministic diagnostic graph + verified catalog + cache.

That aligns directly with the brief's requirement that fast-path retrieval happen without model inference on the critical path.

If you want, I can next design the exact <300 ms architecture for a Samsung phone — including what runs on-device, what data structures to use, and where the diagnostic graph fits.

Yeah — you're right. I was overcomplicating it with an LLM. Looking at the actual PRISM brief, the intended solution is much more constrained.

The brief explicitly says the Fast-Path must do semantic retrieval without model inference on the critical path, with cached verified plans under 300 ms. It also gives you queries.json, sis_responses.json, deeplinks.json, and schema.py as the core assets.

So for:

"volume button not working"

I would not build a conversational diagnostic chatbot.

What they actually want

Think of it as a semantic search + deterministic resolution engine:

"volume button not working"
            ↓
      Query Enrichment
            ↓
  normalized semantic representation
            ↓
     semantic + keyword search
            ↓
       matched catalog item
            ↓
      correct deeplink/action
            ↓
       validated JSON

The brief's Query Enrichment specifically says to normalize colloquial phrasing into structured technical queries and generate diverse semantic paraphrases. Structure Extraction then turns unstructured customer text into a structured goal object containing issue, symptoms, and action categories.

So the intelligence is in the retrieval/enrichment pipeline, not "ask the user 5 questions."

And your volume-button example?

This is where the catalog is the source of truth.

If:

"volume button not working"

is sufficiently close to a known troubleshooting entry, return its verified plan.

If it isn't:

volume button not working
        ↓
no sufficiently confident catalog match
        ↓
don't invent a troubleshooting flow
        ↓
return controlled fallback

The brief explicitly prohibits hallucinating or altering deeplinks and requires catalog-matched URLs.

So you don't need to determine whether it's hardware or software yourself unless the supplied troubleshooting catalog contains that distinction.

That's an important scope boundary.

The 300 ms architecture

This is what I'd build:

                 USER QUERY
                      │
                      ▼
             ┌─────────────────┐
             │ Query Enrichment│
             │ local/determin. │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Semantic Search │
             │ + Keyword      │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Rank Candidates │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Catalog Lookup  │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Validate        │
             │ URL + schema    │
             └────────┬────────┘
                      ▼
                VERIFIED PLAN
                      │
                      ▼
                  CACHE
Critical path:

NO LLM.

That's exactly consistent with the brief's wording: "High-throughput semantic retrieval without model inference" for the fast path.

What I'd use instead

For Samsung mobile:

Query representation

normalization
typo handling
synonym dictionary
keyword extraction

Semantic retrieval

small local embedding model or precomputed embeddings
cosine similarity
BM25/keyword matching

Ranking

Something like:

score =
    0.50 × semantic_similarity
  + 0.30 × keyword_similarity
  + 0.20 × metadata_match

Then:

if score >= threshold:
    return catalog action
else:
    fallback

And cache:

query/paraphrase → verified plan

The brief specifically says Fast-Path caching should handle user query paraphrases through semantic matching.

So I'd change our whole earlier direction:

❌ DeepSeek on device
❌ DeepSeek API
❌ conversational diagnosis
❌ dynamically generated troubleshooting trees

✅ Semantic retrieval
✅ Catalog-grounded matching
✅ Deterministic deeplink mapping
✅ Action sequencing rules
✅ Strict schema validation
✅ Fast-path cache <300 ms
✅ REST API

And importantly, don't try to solve problems outside the supplied catalog. The PRISM challenge is asking you to build the engine around the standardized datasets and strict contract, not build a general Samsung hardware diagnostic AI.

If you want, I can now give you the exact algorithm for the <300 ms semantic matching engine (including whether to use embeddings, BM25, SQLite/FAISS, and how to structure queries.json → deeplinks.json).

You're right. I went back to the actual PS, including page 1, instead of extrapolating from page 2.

Yes, they explicitly want an LLM / parsing phase. The PS says:

[1] Phase 1: Structure Extraction (LLM / Parsing)
Extract Goal, categorized Actions, and discrete UI Steps
Enforce strict length, phrasing, and zero-leak constraints.

And the overall goal is to take raw, vague Galaxy-device complaints and turn them into validated, machine-actionable troubleshooting plans under 300 ms for previously encountered issues.

So what are you actually building?

Not a chatbot.

Not a hardware diagnosis system.

Not an LLM that answers the customer directly.

You're building a LLM-powered troubleshooting-plan generator + semantic cache.

The PS's intended pipeline is essentially:

RAW COMPLAINT
    +
OPTIONAL SIIS RAW KNOWLEDGE TEXT
            │
            ▼
┌──────────────────────────┐
│ 0. QUERY ENRICHMENT      │
│                          │
│ colloquial → canonical   │
│ technical query          │
│                          │
│ + 8–10 paraphrases       │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ 1. STRUCTURE EXTRACTION  │
│        LLM / PARSING     │
│                          │
│ Goal                     │
│ Actions                  │
│ UI Steps                 │
│ Categories               │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ 2. DEEPLINK MAPPING      │
│    + ACTION ORDERING      │
│                          │
│ Match UI screens against │
│ deeplink catalog         │
│                          │
│ safe → critical          │
└────────────┬─────────────┘
             │
             ▼
       VALIDATED PLAN
             │
             ▼
┌──────────────────────────┐
│ 3. SEMANTIC CACHE        │
│                          │
│ query/paraphrase         │
│        →                 │
│ verified plan            │
└──────────────────────────┘

That's exactly the architecture they're describing on page 1 and page 2.

And now the 300 ms part makes sense

This was the part I was getting wrong conceptually.

They are not saying the LLM itself must respond in 300 ms.

They explicitly define:

Cache hit
User query
    ↓
semantic cache
    ↓
previously validated plan
    ↓
< 300 ms

No LLM invocation.

Cache miss
User query
    ↓
Query enrichment
    ↓
LLM / parsing
    ↓
Structure extraction
    ↓
Deeplink mapping
    ↓
Validation
    ↓
Write verified plan to cache
    ↓
Return

The PS literally specifies:

Cache Hit (<= 300 ms): Return validated plan without LLM invocation

and

Cache Miss: Run pipeline [0]-[2], validate schema, write to cache.

So YES, use an LLM.
But NO, don't put it on the fast path.

What does the LLM actually need to make?

This is the most important part.

Given something like:

"my phone gets slow after the latest update"

the LLM isn't supposed to respond with a nice conversational answer.

It needs to produce a strict structured object.

The PS defines fields such as:

goal
title
score
actionName
description
stepGroups
category
actionableDeeplink
query_variations

with strict constraints around lengths, phrasing, categories and URLs.

So you're essentially making:

Natural language → structured troubleshooting plan

For example conceptually:

{
  "goal": "Troubleshooting",
  "title": "Improve device performance",
  "score": 0.92,
  "actions": [
    {
      "actionName": "Clear background apps",
      "description": "...",
      "category": "standard",
      "actionableDeeplink": "bixby://..."
    }
  ]
}

The exact output has to follow their supplied schema.py; don't invent your own schema.

What about "volume button not working"?

This is where we need to distinguish what the PS says from what we were discussing earlier.

The PS says the input can be vague and that the engine should reconstruct the problem into granular, screen-specific actions.

So:

"volume button not working"

goes through the pipeline.

The LLM/parser needs to understand the complaint and, if the supplied SIIS knowledge / catalog supports it, extract the relevant troubleshooting actions.

It is not asking you to build an independent physical-hardware diagnostic model.

If the source material says there are multiple troubleshooting steps, extract them.

If there's no valid Settings deeplink for a step, the specification provides:

bixby://dummy_positive

as the reserved generic placeholder when a valid Settings screen isn't currently indexed.

And manual physical interventions are explicitly categorized as manual and cannot have an actionable deeplink.

So for your team, I'd define the deliverable as 5 things
1. LLM extraction service

Takes:

complaint
+
optional SIIS knowledge

and produces the structured troubleshooting representation.

2. Deeplink resolver

Takes:

"Battery → Background usage limits"

and finds the exact matching entry in deeplinks.json.

No invented URLs.

3. Action sequencing engine

Orders:

standard
   ↓
critical
   ↓
manual

according to the rules in the PS.

4. Semantic cache

Stores:

"battery drains fast"
"battery dying quickly"
"battery drops overnight"
...
            ↓
same validated plan

So future variants can hit the cache without invoking the LLM.

5. REST API

The API exposes the whole thing and reports operational metadata including latency, cache-hit flag and cost.

In one sentence

You're building an LLM-based compiler that converts messy Samsung customer complaints + SIIS knowledge into a strict, validated sequence of one-tap Samsung Settings actions, then caches those validated plans so repeated/semantically similar complaints can be served in <300 ms without calling the LLM.

That's what I now understand the PS to be asking for.

And yes — the LLM is a core part of the requested solution, specifically Phase 1 "Structure Extraction (LLM / Parsing)".

Yes. Let's stop debating the architecture and engineer the thing the PS asks for.

1. What we are building

We're building a Smart Guided Troubleshooting Engine for Samsung devices.

Its job is:

Take a messy customer complaint → understand/structure it → find the correct troubleshooting actions from the supplied Samsung knowledge/catalog → map those actions to exact Samsung Settings deeplinks → validate everything → return a strict troubleshooting plan → cache it for <300 ms future requests.

The PS breaks this into five pipeline components: Query Enrichment, Structure Extraction, Deeplink Mapping & Sequencing, Fast-Path Caching, and REST API.

2. The system we should build
                    ┌──────────────────┐
                    │   User Complaint │
                    │                  │
                    │ "battery drains  │
                    │  crazy fast"     │
                    └────────┬─────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  SEMANTIC CACHE      │
                  │                      │
                  │ Is this query or a   │
                  │ known paraphrase?    │
                  └───────┬───────┬──────┘
                          │       │
                       HIT│       │MISS
                          │       │
                          ▼       ▼
                    VERIFIED   ┌──────────────┐
                      PLAN     │ 0. Query      │
                          │    │ Enrichment    │
                          │    └──────┬───────┘
                          │           ▼
                          │    ┌──────────────┐
                          │    │ 1. LLM /     │
                          │    │ Structure    │
                          │    │ Extraction   │
                          │    └──────┬───────┘
                          │           ▼
                          │    ┌──────────────┐
                          │    │ 2. Deeplink  │
                          │    │ Mapping +    │
                          │    │ Sequencing   │
                          │    └──────┬───────┘
                          │           ▼
                          │    ┌──────────────┐
                          │    │ VALIDATION   │
                          │    └──────┬───────┘
                          │           ▼
                          │    ┌──────────────┐
                          │    │ WRITE CACHE  │
                          │    └──────┬───────┘
                          │           │
                          └─────┬─────┘
                                ▼
                         REST API RESPONSE

That is our product.

3. First: understand the supplied assets

The PS gives us the raw material:

queries.json
sis_responses.json
deeplinks.json
samples/
schema.py

The document specifically describes these as the standardized datasets/starter assets.

So we should not start by creating our own giant knowledge base.

We should first inspect those files and understand their exact schemas.

Our first engineering task

Build:

loader/
    queries_loader.py
    siis_loader.py
    deeplink_loader.py

models/
    schema.py

pipeline/
    enrichment.py
    extraction.py
    deeplink.py
    sequencing.py
    validation.py
    cache.py

api/
    main.py

tests/
4. Component 0 — Query Enrichment

Input:

"my battery is dying really fast"

Output should become a semantic/canonical representation.

The PS says this component normalizes colloquial phrasing into a structured technical query and generates diverse semantic paraphrases.

For example conceptually:

{
  "canonical_query": "battery fast drain",
  "query_variations": [
    "battery drains quickly",
    "battery losing charge fast",
    "phone battery drains overnight",
    "battery dies quickly"
  ]
}

The important part is that those variations become retrieval keys, not merely decorative text.

5. Component 1 — LLM / Structure Extraction

This is where we actually use the LLM.

The PS explicitly calls this:

Structure Extraction (LLM / Parsing)

The LLM receives the complaint / relevant source material and produces the structured troubleshooting representation.

It needs to identify things such as:

Goal
Issue
Actions
UI steps
Action categories

And it must follow the strict schema.

The LLM should NOT decide arbitrary URLs.

That's critical.

If the LLM says:

Go to Battery Settings

fine.

But it should not invent:

bixby://settings/something-random

The PS says deeplinks must be matched from the supplied catalog, and altering/hallucinating URLs is forbidden.

6. Component 2 — Deeplink Mapping

Now we take the extracted action:

"Battery usage"

and search:

deeplinks.json

for the actual Samsung deeplink.

This is a retrieval problem.

Use:

semantic similarity
+
keyword matching
+
metadata

Then select the catalog entry.

Never:
LLM → URL
Instead:
LLM → action description
              ↓
       catalog retrieval
              ↓
       verified deeplink

That's one of the most important safety/correctness properties of the system.

7. Component 3 — Sequencing

Suppose the system finds:

1. Check battery usage
2. Restrict background usage
3. Restart device
4. Factory reset
5. Visit service center

The engine must produce the correct hierarchy.

The PS defines:

standard — normal configuration/settings actions
critical — disruptive actions such as factory reset/restart/firmware update/safe mode
manual — physical interventions/service-center actions

and specifies that critical actions must be ordered last; manual actions cannot have actionable deeplinks.

So this is our deterministic rules engine.

Not another LLM call.

8. Component 4 — Validator

This is where we become strict.

Before anything reaches the user:

                 GENERATED PLAN
                       │
                       ▼
             ┌───────────────────┐
             │ Schema validation │
             ├───────────────────┤
             │ ✓ title length    │
             │ ✓ score           │
             │ ✓ description     │
             │ ✓ categories      │
             │ ✓ step groups     │
             │ ✓ deeplink exists │
             │ ✓ URL unchanged   │
             │ ✓ manual rules    │
             │ ✓ ordering        │
             └─────────┬─────────┘
                       │
                 PASS / REJECT

This is particularly important because the PS calls out LLM hallucination, strict source adherence and field constraints as core engineering challenges.

9. Component 5 — Fast Path

Now the important 300 ms requirement.

After we successfully generate:

query
     ↓
verified plan

we cache it.

Then someone says:

"battery drains super fast"

and we've already seen a semantically equivalent query.

We do:

query
 ↓
embedding
 ↓
nearest cached query
 ↓
verified plan
 ↓
response

No LLM.

The PS explicitly describes the fast path as serving cached verified plans under 300 ms and handling query paraphrases through semantic matching.

10. What happens with your "volume button" example?

This becomes very clean.

First request
"volume button isn't working"
Pipeline
Query Enrichment
        ↓
LLM Structure Extraction
        ↓
structured troubleshooting goal/actions
        ↓
deeplink catalog matching
        ↓
sequencing
        ↓
validation
        ↓
cache

If the source/catalog provides enough information to construct the plan → return it.

If a physical intervention is required:

category = manual
deeplink = none

because the PS explicitly says manual physical interventions cannot have actionable deeplinks.

We don't invent a hardware diagnosis beyond what the supplied source supports.

11. The really important optimization

We should precompute as much as possible.

Before the API ever receives a user:

queries.json
     ↓
enrichment
     ↓
LLM extraction
     ↓
deeplink resolution
     ↓
validation
     ↓
embeddings
     ↓
cache/index

Then production is mostly:

USER
 ↓
embedding
 ↓
nearest known query
 ↓
verified plan
 ↓
<300ms

For an unseen query, run the full pipeline and add the result to the cache.

That is how the LLM and 300 ms requirement coexist.

12. What our MVP should demonstrate

We don't need 1000 features.

We need one excellent end-to-end pipeline.

I'd target:

Input
"my phone battery is dying really fast"
Output

Strict JSON conforming to schema.py:

Goal
Title
Score
Actions
Descriptions
Step groups
Categories
Verified Samsung deeplinks
Query variations
Then demonstrate:
Request #1
    ↓
LLM + retrieval + validation
    ↓
~X ms
    ↓
CACHE WRITE

Request #2
"battery draining very quickly"
    ↓
semantic cache
    ↓
verified result
    ↓
<300 ms

And show:

cache_hit: true
latency_ms: 42

The PS also calls for the REST service to expose operational metrics including latency, cache-hit status and cost.

13. Our engineering stack

I'd keep it simple:

Python
│
├── FastAPI              → REST API
├── Pydantic             → schema validation
├── LLM                  → structure extraction
├── sentence-transformers
│                         → semantic embeddings
├── FAISS / similar       → retrieval
├── Redis                 → fast cache
└── pytest                → contract tests

For the hackathon prototype, we can even keep Redis/FAISS optional initially and get the whole pipeline working with in-memory structures.

14. Build order

Don't build everything simultaneously.

Phase 1 — Dataset

Load and inspect the actual supplied JSON files.

↓

Phase 2 — Contract

Make schema.py validation pass.

↓

Phase 3 — LLM

Build:

raw complaint
      ↓
structured output

↓

Phase 4 — Deeplink resolver
action
 ↓
exact catalog URL

↓

Phase 5 — Sequencing + validation

↓

Phase 6 — Semantic retrieval

↓

Phase 7 — Cache

↓

Phase 8 — FastAPI

↓

Phase 9 — benchmark
1000 queries
↓
p50
p95
p99
cache hit rate
invalid output rate
Most importantly: we should start with the actual starter assets, not theory.

You've uploaded the PS, but the actual queries.json, sis_responses.json, deeplinks.json, samples/, and schema.py are presumably the files we need next.

If you upload those starter assets, I can take us through the engineering from the actual schemas: first inspect them, define the data model, then implement Query Enrichment → LLM extraction → deeplink matching → validation → cache → FastAPI one component at a time.

What about the example of that volume button do they want us to handle this

Not necessarily. Based strictly on the PS, I don't see a requirement saying you must diagnose every broad complaint like “volume button not working” through multiple follow-up questions.

What they explicitly require is:

Take a colloquial customer complaint and normalize it into a technical representation.
Extract the Goal, categorized Actions, and discrete UI Steps using the LLM/parser.
Map those actions to specific device Settings screens using the supplied deeplinks.json.
If a step is a physical intervention such as replacing hardware or visiting a service center, it is a manual action and doesn't get an actionable deeplink.
Do not invent or modify deeplinks.

So for:

“Volume button not working”

your engine should essentially ask:

“What does the supplied SIIS/customer-support knowledge say should be done for this complaint?”

Then extract that prescribed troubleshooting procedure.

If the source says:
Volume button not working
    ↓
Check X
    ↓
Change Y setting
    ↓
If still not working → service center

Your engine should produce those actions.

If the source says:
Volume button not working
    ↓
Physical inspection / service

then you produce a manual action rather than trying to manufacture a Settings deeplink.

What you should NOT do

Don't add your own diagnostic tree like:

Does the volume slider appear?
    ↓
Does the button feel stuck?
    ↓
Was the phone dropped?
    ↓
Is it hardware?

unless that diagnostic logic actually exists in the supplied source data.

The PS is asking you to build a source-grounded troubleshooting-plan extraction and mapping engine, not invent new troubleshooting procedures.

So the answer to your question is:

“Volume button not working” is exactly the kind of vague customer input your engine should be able to process, but the PS does not establish that you need to conduct an interactive hardware-vs-software diagnosis. You should follow whatever troubleshooting actions are represented in the provided SIIS/source assets.

And this is why the SIIS responses + queries + deeplinks datasets are critical. We need to inspect those next to know exactly how the PS expects a case like “volume button not working” to resolve.

As seen in pdf we have schema.py and result.json but I can't find anything else can u tell me where to get this data 

I checked the PS again. The PDF does not contain the actual datasets. It only says that the problem environment provides them.

The PS lists these as starter assets:

queries.json — canonical user queries across Battery, Display, Camera, Performance
siis_responses.json — pre-canned customer-reference text containing troubleshooting instructions, web URLs, and/or images
deeplinks.json — catalog of ~75 masked Samsung Settings deeplinks
samples/ — five complete reference input/output pairs
schema.py — Pydantic models defining the output contract

So if you currently have only:

schema.py
result.json

you are missing the starter dataset package.

Where to get it

I searched the public web for the exact challenge/dataset filenames and couldn't find a public download. So I don't want to make up a GitHub repo or tell you to scrape Samsung support and pretend that's the intended dataset.

The wording in the PS — “The problem environment provides standardized datasets” — strongly suggests these files are supposed to be distributed through the PRISM challenge/problem environment, likely alongside the problem statement or in the participant/starter-code package, rather than being embedded in the PDF.

What I'd do now

Check wherever you received the PS for a package/repository containing something like:

starter/
├── queries.json
├── siis_responses.json
├── deeplinks.json
├── samples/
│   ├── ...
├── schema.py
└── result.json

If schema.py and result.json are literally all you've been given, then ask the hackathon organizers for the “starter assets / problem environment dataset” for Theme 2.

You can send them this exact message:

For Theme 2 – Smart Guided Troubleshooting Engine, the PS references queries.json, siis_responses.json, deeplinks.json, and samples/ under “Data Inputs & Starter Assets.” We currently only have schema.py and result.json. Could you please provide the complete starter dataset/problem environment package?

And don't create those files ourselves yet.

Especially deeplinks.json. The PS says the deeplinks must be matched from the provided catalog and that modifying/hallucinating URLs is strictly forbidden.

So without the actual catalog, we cannot faithfully implement Phase 2.

If you upload the schema.py and result.json you have, I can inspect them and tell you exactly what is missing, what each file is for, and what we can start engineering right now.
