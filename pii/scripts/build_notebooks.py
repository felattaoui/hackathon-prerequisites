"""Builds the teaching notebooks from source cells (keeps them output-free and reviewable).

Run: python scripts/build_notebooks.py
"""
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]


def md(text):
    return nbf.v4.new_markdown_cell(text.strip())


def code(text):
    return nbf.v4.new_code_cell(text.strip())


NB00 = [
    md("""
# 00 — Setup and map of the PII touchpoints

**Goal of this series.** An agent runtime (a LangGraph orchestrator receiving requests over A2A and REST,
calling an LLM, sub-agents, REST APIs and MCP tools, with memory and observability) handles personal data
at several places. Each notebook takes **one touchpoint**, shows what Azure offers for it according to the
current documentation, and demonstrates it on **synthetic data only**.

**How to use.** Open the notebooks in VS Code, select the kernel of the repository environment
(`.conda/python.exe`) and run cells from top to bottom. Azure resources are created by
`scripts/deploy.sh` (see `README.md`); nothing in the notebooks creates or deletes resources.
"""),
    md("""
## 1. The runtime and its touchpoints

```
                      (1) inputs                                     (2) LLM call
 user / app ──HTTP──► IN A2A / IN REST(JSON) ──► Orchestrator ──────────► LLM
                                                  (LangGraph)
                                                   │    │    │
                     (3) sub-agents (A2A), REST APIs, MCP tools ── data goes out AND comes back
                                                   │
          (5) short-term memory (Redis)  ·  long-term memory (vector index)
          (4) observability (OpenTelemetry ──► Application Insights)
          (6) knowledge base (RAG index)   ·  (7) audit and analytics
```

| # | Touchpoint | Question | Notebook |
|---|---|---|---|
| 1 | Inputs | Can we detect personal data in prompts, JSON payloads and images, including travel identifiers, in several languages, without false positives? | `01_inputs` |
| 1 | Inputs, with Presidio | Same question with the open-source Presidio framework: default settings, then configured | `01b_presidio` |
| 2 | LLM call | Can the LLM work on masked data, and can we put the real values back afterwards? | `02_llm_call` |
| 3 | Sub-agents, APIs, MCP | Does each agent or tool receive only the personal data its mission needs, in and out? | `03_agents_tools` |
| 4 | Observability | Do logs and traces stay free of personal data, before they reach Application Insights? | `04_observability` |
| 5 | Memory | What do we store in short-term and long-term memory, and where do real values live? | `05_memory` |
| 6 | Knowledge base | Can personal data be kept out of a RAG index at indexing time? | `06_knowledge_db` |
| 7 | Audit | Can we audit transactions while keeping only legitimate personal data? | `07_audit` |
"""),
    md("""
## 2. Six words used in every notebook

| Word | Meaning here |
|---|---|
| **Detection** | Finding where personal data is in a text, a JSON payload or an image, and of which type. |
| **Masking** | Replacing a value with `***` or `[PERSON]`. One-way: the value cannot be recovered. |
| **Pseudonymization** | Replacing a value with a token such as `[PERSON_1]` and keeping the mapping in a protected store, so that it can be reversed. Under the GDPR (Art. 4(5)), pseudonymized data is **still personal data**. |
| **Anonymization** | Irreversible removal of anything that identifies a person. Hard to achieve; not the goal of this series. |
| **Re-identification** | Putting the real value back, only where it is needed (for example the booking tool needs the real name). |
| **Minimization** | Each component receives only the personal data it needs (GDPR Art. 5(1)(c)). |
"""),
    md("""
## 3. Personal data in scope

| Type | Synthetic example | Detected by |
|---|---|---|
| Passenger name, group name, title | `Jane Whitfield`, `Blue Heron Travellers`, `Mrs` | Azure Language (`Person`); group name and title: our rules |
| Passport and national documents | `523941876` | Azure Language (passport and national ID categories) |
| Phone, e-mail, address, date of birth | `+44 20 7946 0958`, `jane.whitfield@example.com` | Azure Language |
| **Booking reference (PNR)** | `K7M2QR` (pattern `^[A-Z2-9]{6}$`) | No Azure category: our rules (notebook 01) |
| **Hotel confirmation number** | `a889Ce6FFF8d0` (pattern `[0-9a-zA-Z]{2,20}`) | No Azure category: our rules |
| **Loyalty number** | `XY 4815162342` | No Azure category: our rules |

**Labels used in the notebooks:** **[Documented]** = stated on Microsoft Learn (link given);
**[Observed]** = what the cell output shows on the day you run it; **[Assumption]** = to be validated.
"""),
    md("## 4. Environment check"),
    code(r"""
import sys
from importlib.metadata import version

print("Python", sys.version.split()[0])
for package in ["azure-identity", "httpx", "pillow", "ipython"]:
    print(f"{package:15} {version(package)}")
"""),
    md("## 5. Azure configuration and sign-in"),
    code(r"""
import pii_lab as L

env = L.load_env()
print("PII_FOUNDRY_ENDPOINT configured:", bool(env.get("PII_FOUNDRY_ENDPOINT")))
token = L._token()   # Entra ID token from `az login`: no API key anywhere
print("Azure CLI sign-in: OK (token acquired)")
"""),
    md("""
## 6. First call: is Azure Language reachable?

One short synthetic sentence. If this cell fails with HTTP 401/403 right after deployment,
wait a few minutes: role assignments take time to propagate.
"""),
    code(r"""
doc = L.language_pii(["Please call Jane Whitfield on +44 20 7946 0958."])[0]
for e in doc["entities"]:
    print(f'{e["category"]:12} {e["text"]:20} confidence={e["confidenceScore"]}')
print("redactedText:", doc["redactedText"])
"""),
    md("Next: **`01_inputs`** — detection on free text, JSON payloads and images."),
]


NB01 = [
    md("""
# 01 — Inputs: detect personal data in prompts, JSON payloads and images

**The request (touchpoint 1).** Detect personal data in everything that enters the runtime through
A2A or REST: free-text prompts, structured payloads (JSON) and images. The list includes
travel-specific identifiers (booking reference, hotel confirmation, loyalty number), users write in
many languages, and false positives must be avoided (an airport named after a person is not a person).

**The approach of this notebook.** Use everything Azure does natively first, and write our own code
only where the documentation shows that Azure has no answer. Each time, the notebook says why.

| Need | Native Azure answer | Our code |
|---|---|---|
| Names, contacts, documents, addresses, dates of birth | **Azure Language — PII detection** (GA `2026-05-01`) | — |
| Several languages, language unknown | Azure Language (~80 languages) + **language detection** | — |
| Airport mistaken for a person | Ask Language to recognise **places**, plus a **confidence threshold** | — |
| Booking reference, hotel confirmation, loyalty number | No category, no custom pattern in the online service. Only the **PII container** accepts custom rules | Our rules, written **in the container's rule format** |
| JSON payloads | No field-aware mode | Rules on field names |
| Images | Language reads text only; Document PII accepts PDF/DOCX/TXT, not PNG/JPG | OCR with **Document Intelligence**, then black boxes |

**Sections.** A. Azure Language on free text · B. The airport trap · C. Travel identifiers ·
D. JSON payloads · E. Images · F. Proof · G. Takeaways
"""),
    code(r"""
import json
import re
from IPython.display import HTML, Image, Markdown, display

import pii_lab as L

display(HTML("Colour of a detection = who found it: " + L.legend()))
"""),
    md("""
## A. Azure Language on free text, in five languages

We send synthetic sentences in English, French, German, Spanish and Hindi. This is the exact request
(one document shown). The model version is **pinned** (`modelVersion`) so that results can be
reproduced: with `latest`, the model can change under your feet
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/concepts/model-lifecycle).
"""),
    code(r"""
sample = L.TEXT_SAMPLES[0]
request = {
    "kind": "PiiEntityRecognition",
    "parameters": {"modelVersion": L.LANGUAGE_MODEL, "stringIndexType": "UnicodeCodePoint"},
    "analysisInput": {"documents": [{"id": "0", "language": sample["language"], "text": sample["text"]}]},
}
print(f"POST {{endpoint}}/language/:analyze-text?api-version={L.LANGUAGE_API_GA}")
print(json.dumps(request, indent=2, ensure_ascii=False))
"""),
    md("""
First run with the **default settings**. Each detection has two labels: a broad **family**
(`category`, e.g. *DateTime*) and a precise **type** (`type`, e.g. *DateOfBirth*). Since 2024 the
precise label is the `type` field
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/concepts/entity-categories):
always read that one.
"""),
    code(r"""
texts = [s["text"] for s in L.TEXT_SAMPLES]
languages = [s["language"] for s in L.TEXT_SAMPLES]
default_docs = L.language_pii(texts, languages)
print("Model version used:", default_docs[0]["modelVersion"])

for sample, doc in zip(L.TEXT_SAMPLES, default_docs):
    display(Markdown(f'**{sample["id"]}**'))
    display(HTML(L.highlight(sample["text"], L.language_spans(doc, keep_places=True))))
    rows = [{"text": e["text"], "family (category)": e["category"], "type": e.get("type"),
             "confidence": e["confidenceScore"]} for e in doc["entities"]]
    display(Markdown(L.md_table(rows)))
"""),
    md("""
**What we observed when writing this notebook (2026-10-26)** [Observed] — compare with your run:

- Names, phone numbers, e-mails and addresses are found in the five languages, Hindi included.
- The date of birth is found: family *DateTime*, type **DateOfBirth**.
- One passport number comes back under **several country-specific passport types** (same format in
  several countries). The documentation says so and leaves the choice to the application
  [Documented](https://learn.microsoft.com/azure/foundry/responsible-ai/language-service/transparency-note-personally-identifiable-information).
  We group them under *PassportNumber*.
- Words such as *son* or *viajero* (traveller) come back as **PersonType**: a role, not an identity.
- **Travel identifiers are not recognised**; a loyalty number comes back as a *phone number*. Section C.

### The settings used from now on (all native)
"""),
    code(r"""
LANGUAGE_SETTINGS = {
    # "Default" = the usual categories; we add places so that a place is recognised as a place
    "piiCategories": ["Default", "Location", "Airport", "City"],
    # role words (son, mother, passenger) are not personal data
    "excludePiiCategories": ["PersonType"],
    # detections the model is unsure about are dropped (can also be set per type and per language)
    "confidenceScoreThreshold": {"default": 0.6},
}
print(json.dumps(LANGUAGE_SETTINGS, indent=2))
print("Types treated as places, never masked:", sorted(L.GEO_TYPES))
"""),
    md("""
Each setting is a documented parameter of the PII API
[Documented](https://learn.microsoft.com/rest/api/language/analyze-text/analyze-text/analyze-text):
`piiCategories`, `excludePiiCategories`, `confidenceScoreThreshold`. Places are detected **only to
avoid confusions**; they are not personal data, so `L.language_spans` leaves them out of masking.

### Unknown input language

Users do not say which language they write in. The API reference mentions `"language": "auto"`, but
the service rejects it [Observed]. The documented alternative is the **language detection** feature of
the same service, called first
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/language-detection/overview).
"""),
    code(r"""
try:
    L.language_pii([L.TEXT_SAMPLES[4]["text"]], "auto")
    print('"auto" accepted on this API version')
except RuntimeError as error:
    print('"auto" rejected:', str(error)[:160], "...")

detected = L.detect_languages(texts)
display(Markdown(L.md_table([{"sample": s["id"], "written in": s["language"], "detected": d}
                             for s, d in zip(L.TEXT_SAMPLES, detected)])))
"""),
    md("""
## B. The airport trap

Sentences with **no personal data**, but with airports named after people, flight numbers, fare codes
and city names in capitals. Compare the default settings with ours.
"""),
    code(r"""
trap_texts = [t["text"] for t in L.TRAP_SAMPLES]
trap_languages = [t["language"] for t in L.TRAP_SAMPLES]
trap_default = L.language_pii(trap_texts, trap_languages)
trap_tuned = L.language_pii(trap_texts, trap_languages, **LANGUAGE_SETTINGS)

found = lambda spans: ", ".join(f'{s["text"]} ({s["category"]} {s["score"]})' for s in spans) or "—"
rows = [{"sentence": t["text"],
         "default settings": found(L.language_spans(d, keep_places=True)),
         "our settings: everything detected": found(L.language_spans(tu, keep_places=True)),
         "our settings: personal data": found(L.language_spans(tu))}
        for t, d, tu in zip(L.TRAP_SAMPLES, trap_default, trap_tuned)]
display(Markdown(L.md_table(rows)))
"""),
    md("""
**Reading the table.** With the default settings, Language can mistake an airport for a person or an
address [Observed]. With our settings, airports come back as **places** (column 3), which are not
personal data (column 4), and the threshold removes low-confidence guesses.

A third documented lever exists: **`valueExclusionPolicy`**, a list of values never reported
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/how-to/adapt-to-domain-pii).
It only works on the **exact** value ("a subset of the specified excluded value isn't excluded") and
it would also hide a passenger who really has that name, so we do not use it here:
"""),
    code(r"""
exclusion = {"valueExclusionPolicy": {"caseSensitive": False, "excludedValues": ["Charles de Gaulle"]}}
for text in ["Rendez-vous devant Charles de Gaulle, porte 6.", "Rendez-vous devant Roissy-Charles de Gaulle, porte 6."]:
    doc = L.language_pii([text], "fr", **exclusion)[0]
    print(f"{text!r:60} -> {found(L.language_spans(doc, keep_places=True))}")
"""),
    md("""
## C. Travel identifiers

**What Azure offers.** The online service has no category for booking references, hotel confirmations
or loyalty numbers, and no parameter to add a pattern; `entitySynonyms` only works for existing types
[Documented](https://learn.microsoft.com/rest/api/language/analyze-text/analyze-text/analyze-text).
The **only** native way to teach a new type is the **Text PII container**: the same engine, run on
your own host, with a rule file (`UserRegexRuleFilePath`)
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/how-to/adapt-to-domain-pii).
A rule = a pattern with a base score (`matchScore`) + context words (`hints`) that add a bonus
(`boostingScore`) when found near the match; when several hints match, the highest bonus is used.

**What we do.** We write our rules **in that exact format** and apply them with a small function that
follows those documented semantics. The same rules could be given to the container later.

First, why context is needed: the patterns we were given, applied alone to our sentences.
"""),
    code(r"""
all_texts = [s["text"] for s in L.TEXT_SAMPLES + L.TRAP_SAMPLES]
naive_booking = [m.group() for t in all_texts for m in re.finditer(L.BOOKING_REF_PATTERN, t)]
naive_hotel = [m.group() for t in all_texts for m in re.finditer(L.HOTEL_CONF_PATTERN, t)]
print(f"Booking-reference pattern alone: {len(naive_booking)} matches -> {naive_booking}")
print(f"Hotel-confirmation pattern alone: {len(naive_hotel)} matches (almost every word), e.g. {naive_hotel[:12]}")
"""),
    md("Our three rules, in the container format (patterns and scores; context words per language):"),
    code(r"""
rows = [{"rule": r["name"], "pattern id": p["id"], "base score": p["matchScore"],
         "context bonus": r["matchContext"]["hints"][0]["boostingScore"]}
        for r in L.TRAVEL_RULES for p in r["regexPatterns"]]
display(Markdown(L.md_table(rows) + f"\n\nA match is reported when its score reaches **{L.ACCEPT_SCORE}**."))
rows = [{"category": c, **{lang: ", ".join(words) for lang, words in by_lang.items()}}
        for c, by_lang in L.CONTEXT.items()]
display(Markdown(L.md_table(rows)))
"""),
    code(r"""
for sample in L.TEXT_SAMPLES + L.TRAP_SAMPLES:
    accepted, rejected = L.apply_rules(sample["text"], language=sample["language"])
    print(sample["id"])
    for s in accepted:
        print(f'   reported  {s["text"]:15} {s["category"]:18} score {s["score"]:.2f}  {s["source"]}')
    # the hotel pattern proposes every word containing a digit: only the other ignored candidates are shown
    for s in (r for r in rejected if r["category"] != "HotelConfirmation"):
        print(f'   ignored   {s["text"]:15} {s["category"]:18} score {s["score"]:.2f}  {s["source"]}')
"""),
    md("""
The function that applies the rules, and the two small helpers that do not fit the pattern model
(a group name and a title are recognised by their position in the sentence, not by their shape):
"""),
    code(r"""
display(Markdown(L.show_code(L.apply_rules, L.find_group_names, L.find_titles)))
"""),
    md("The rule file as the container would receive it (first rule shown):"),
    code(r"""
print(json.dumps(L.TRAVEL_RULES[0], indent=2, ensure_ascii=False)[:1500], "...")
"""),
    md("""
### Other options reviewed, and why they were not used here

Before writing our own rules we checked every documented way to detect a new type of identifier.

| Option | What it does | Why not used here |
|---|---|---|
| **Azure Language, online** (this notebook) | Generic personal data, ~80 languages, positions returned | No way to add a new type or a custom pattern [Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/how-to/adapt-to-domain-pii) |
| **Azure Language, container** | Same engine on your own host; accepts the rule file shown above | Valid choice; needs a host running all the time. We reuse its rule format so the move stays possible [Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/how-to/use-containers) |
| **Azure Language, custom entity recognition** | You label examples, it trains a model that returns *your* type ("BookingReference") with its position | About **50 labelled examples per type** recommended (minimum 10 documents) [Documented](https://learn.microsoft.com/azure/ai-services/language-service/custom-named-entity-recognition/how-to/tag-data); results come back **asynchronously** (submit, then fetch) [Documented](https://learn.microsoft.com/azure/ai-services/language-service/custom-named-entity-recognition/how-to/call-api); the current training version expires on 2028-03-31 and deployments on 2029-03-31, dates that "may be extended" [Documented](https://learn.microsoft.com/azure/ai-services/language-service/concepts/model-lifecycle). A good candidate to test if rules are not enough |
| **Azure Language, conversations** | Has a catch-all `NumericIdentifier` type ("case numbers, member numbers, ticket numbers…") [Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/concepts/conversations-entity-categories) | One label for every kind of number, so we cannot tell a booking reference from a loyalty number (needed in notebook 03); English, French, German and Spanish only, no Hindi [Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/language-support); asynchronous |
| **Microsoft Purview** | Custom types with pattern + nearby keywords + confidence: the same idea | Called from an application it only answers with a policy action (block / restrict), **not where** the data is, so it cannot mask [Documented](https://learn.microsoft.com/graph/api/resources/processcontentresponse) |
| **Foundry guardrail blocklists** | Patterns that block a prompt or a completion | Block the whole message, say which list matched but not where; Azure OpenAI models only [Documented](https://learn.microsoft.com/azure/foundry/openai/how-to/use-blocklists) |
| **Presidio** (open source) | Pattern + context words, can call Azure Language for generic data | See below |

**Presidio in more detail.** Presidio started at Microsoft and is now a community project with no
warranty or SLA [Documented](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md).
It works on the same principle as our rules. Its **default settings** explain the kind of results a
multilingual team can get without tuning [Documented, source code](https://github.com/data-privacy-stack/presidio):

- only **English** is configured; French needs a model to be added, and no Hindi model is shipped;
- patterns ignore upper/lower case, so `[A-Z2-9]{6}` also matches ordinary six-letter words;
- the default spaCy model gives the **same score (0.85) to every name it finds**, so a threshold cannot
  separate a real person from "Charles de Gaulle";
- context words are looked for in the **5 words before** the value, none after;
- nothing is filtered out by default (threshold 0).

All of these can be changed: per-language models and context words, case-sensitive patterns, a
threshold, an allow list for places, or Azure Language as the name detector. Presidio can call Azure
Language for generic data, but through an older client library (`azure-ai-textanalytics` 5.x): it
signs in with a key unless you pass your own client, and none of the settings used in this notebook
(places, thresholds, exclusions) are sent
[Documented, source code](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/predefined_recognizers/third_party/azure_ai_language.py).
"""),
    md("""
## D. Structured payloads (JSON)

**What Azure offers.** No PII API reads JSON field by field: the input is raw text
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/how-to/redact-text-pii).

**What we do.** When a value sits under a known field (`recordLocator`, `passport.number`, `email`…),
the field name says what it is: detection is **deterministic**, no model needed. Fields known to hold
no personal data (`from`, `to`, `flight`…) are left alone: sent alone to a language model, a code such
as `NCE` can come back as something else and be masked for nothing [Observed]. The remaining free-text
fields go through Language and the travel rules.
"""),
    code(r"""
print(json.dumps(L.BOOKING_PAYLOAD, indent=2, ensure_ascii=False))
findings, free_text = L.scan_json(L.BOOKING_PAYLOAD)
display(Markdown("**Found by field rules**\n\n" + L.md_table(findings, ["path", "category", "value"])))
display(Markdown("**Known non-personal fields** (left as is): " + ", ".join(f"`{f}`" for f in sorted(L.NON_PII_FIELDS))))
display(Markdown("**Sent to text detection**: " + ", ".join(f'`{f["path"]}`' for f in free_text)))
"""),
    code(r"""
free_languages = L.detect_languages([f["value"] for f in free_text])
free_docs = L.language_pii([f["value"] for f in free_text], free_languages, **LANGUAGE_SETTINGS)
text_spans = {}
for item, doc, lang in zip(free_text, free_docs, free_languages):
    language = L.language_spans(doc)
    spans = L.merge_spans(L.travel_spans(item["value"], language, lang), language)
    if spans:
        text_spans[item["path"]] = spans

masked_payload = L.mask_json(L.BOOKING_PAYLOAD, text_spans)
print(json.dumps(masked_payload, indent=2, ensure_ascii=False))
print("Expected values still readable:", L.leaks(json.dumps(masked_payload, ensure_ascii=False), L.BOOKING_PAYLOAD_GOLD) or "none")
"""),
    md("""
In a real system both lists (personal and non-personal fields) come from the **API contracts**
(OpenAPI schemas, A2A agent cards), with full JSON paths rather than field names: `name` means a person
under `passenger` but not under `hotel`.
"""),
    md("""
## E. Images

**What Azure offers.** Language reads text only. Its document version (Document PII) can redact scanned
**PDF** files but not PNG or JPG images, and it works through Blob storage with an asynchronous job
[Documented](https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/how-to/redact-document-pii).
For text in an image, Microsoft recommends **Document Intelligence Read** (the Image Analysis OCR
retires on 2028-09-25)
[Documented](https://learn.microsoft.com/azure/ai-services/computer-vision/migration-options).

**What we do.** OCR with Document Intelligence (it returns each word with its position), then the
same detections, then black boxes over the words that overlap a detection.
"""),
    code(r"""
image = L.make_boarding_pass()
display(Image(image))
ocr = L.ocr_read(image)
ocr_text = ocr["content"]
print(ocr_text)
"""),
    code(r"""
ocr_language = L.language_spans(L.language_pii([ocr_text], "en", **LANGUAGE_SETTINGS)[0])
image_spans = L.merge_spans(L.scan_labelled_lines(ocr_text), L.travel_spans(ocr_text, ocr_language, "en"), ocr_language)
display(HTML(L.highlight(ocr_text, image_spans)))
display(Image(L.redact_image(image, ocr, image_spans)))
"""),
    md("""
## F. Proof

The whole chain on every synthetic sample, as it would run on real input: detect the language, call
Language with our settings, add the travel rules, merge, mask. Three checks:
1. **Recall** — each expected value is detected (missed values are listed, not hidden);
2. **No leak** — no expected value is still readable after masking;
3. **No false positive** — the trap sentences come out with no personal data.
"""),
    code(r"""
def detect(text, language_doc, language):
    language_found = L.language_spans(language_doc)   # places already left out
    return L.merge_spans(L.scan_labelled_lines(text), L.travel_spans(text, language_found, language), language_found)

samples = L.TEXT_SAMPLES + L.TRAP_SAMPLES
sample_texts = [s["text"] for s in samples]
sample_languages = L.detect_languages(sample_texts)
proof_docs = L.language_pii(sample_texts, sample_languages, **LANGUAGE_SETTINGS)

summary, all_missed, all_leaks, trap_hits = [], [], [], []
for sample, doc, lang in zip(samples, proof_docs, sample_languages):
    spans = detect(sample["text"], doc, lang)
    if sample["id"].startswith("trap"):
        trap_hits += [(sample["id"], s["text"], s["category"]) for s in spans]
        continue
    result = L.evaluate(sample["text"], spans, sample["gold"])
    leaked = L.leaks(L.mask(sample["text"], spans), sample["gold"])
    all_missed += [(sample["id"], r["value"], r["expected"]) for r in result["missed"]]
    all_leaks += [(sample["id"], v) for v in leaked]
    summary.append({"sample": sample["id"], "expected": len(sample["gold"]),
                    "detected": len(sample["gold"]) - len(result["missed"]), "leaks after masking": len(leaked),
                    "other detections": ", ".join(f'{s["text"]} ({s["category"]})' for s in result["extra"])})
    display(Markdown(f'**{sample["id"]}**'))
    display(Markdown(L.md_table(result["rows"])))

image_leaks = L.leaks(L.mask(ocr_text, image_spans), L.BOARDING_PASS_GOLD)

display(Markdown("### Summary\n\n" + L.md_table(summary)))
print("Missed values:", all_missed or "none")
print("Leaks after masking (text):", all_leaks or "none")
print("Leaks after masking (image text):", image_leaks or "none")
print("False positives on traps:", trap_hits or "none")
"""),
    md("""
## G. Takeaways

- **Azure Language does most of the work natively**: names, contacts, documents, addresses and dates of
  birth in the five languages tested, Hindi included. Three documented settings make it fit here:
  recognise places, ignore role words, drop low-confidence guesses. Read the precise `type`, pin the
  model version, and detect the language first.
- **The airport trap is solved natively**: once places are requested, an airport named after a person
  comes back as a place, which is not personal data.
- **Travel identifiers are the real gap.** The online service cannot learn a new type; only the PII
  container accepts custom rules. We wrote our rules in the container's format, so the logic taught here
  is Microsoft's, and the rules can move to the container if running it is acceptable.
- **JSON and images need a little glue**: field rules for structured payloads, OCR before detection for
  images. Azure provides the detection and the OCR; the glue is ours.
- **This is a small synthetic set, not a benchmark.** No detector reaches 100 % on free text, which is
  why the next notebooks add defence in depth: masking before the LLM (02), minimisation per tool (03),
  clean traces (04).
"""),
]


NB01B = [
    md("""
# 01b — The same detection with Presidio

**Why this notebook.** Presidio is an open-source framework for detecting and anonymising personal data.
A team that tries it with its default settings on multilingual travel text can get disappointing
results: names missed, airports taken for people, ordinary words taken for booking references. This
notebook reproduces that, explains the cause setting by setting, then shows Presidio configured
properly, with Azure Language plugged in as its detector for generic personal data.

**What Presidio is today.** It started at Microsoft and is now a community project under the *Data
Privacy Stack* organisation, MIT licence, with no warranty or SLA
[Documented](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md).

**What it adds to Azure Language** (it does not replace it):
- a framework to declare **your own types** (pattern + context words) that runs inside the application,
  with no container to host;
- one call that **combines several detectors** (Azure Language, patterns, other models) and can explain
  each decision;
- in its separate *anonymizer* package: masking, hashing, encryption/decryption (not used here).

Its modules for JSON (alpha) and images (beta) are not shown; notebook 01 covers those needs.

**Sections.** A. Default settings · B. Why: five default settings · C. Presidio configured ·
D. A trap found on the way: context words in Hindi · E. Takeaways
"""),
    code(r"""
import logging
import warnings
from importlib.metadata import version
from IPython.display import HTML, Markdown, display

import pii_lab as L
import presidio_lab as P

warnings.filterwarnings("ignore")
logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
print("presidio-analyzer", version("presidio_analyzer"), "| spaCy models:", P.SPACY_MODELS)
display(HTML("Colour of a detection = who found it: " + L.legend(("presidio", "azure-language"))))
samples = L.TEXT_SAMPLES + L.TRAP_SAMPLES
"""),
    md("""
## A. Presidio with its default settings

The engine as created by default (English only, spaCy for names, every predefined recognizer, no
threshold), plus a booking-reference pattern written the obvious way. To keep downloads small we use
the medium English spaCy model; Presidio's default is the large one.
"""),
    code(r"""
default = P.default_engine()
try:
    default.analyze(L.TEXT_SAMPLES[1]["text"], language="fr")
except Exception as error:
    print("Analysing French with the default engine:", type(error).__name__, "-", str(error)[:120])
print("So a team without a French set-up sends every text as English.\n")

default_results = {}
for sample in samples:
    spans = P.to_spans(sample["text"], default.analyze(sample["text"], language="en"))
    default_results[sample["id"]] = spans
    display(Markdown(f'**{sample["id"]}**'))
    display(HTML(L.highlight(sample["text"], spans)))
"""),
    code(r"""
def summarise(results):
    rows, traps = [], []
    for sample in samples:
        spans = results[sample["id"]]
        if sample["id"].startswith("trap"):
            traps += [f'{s["text"]} ({s["category"]})' for s in spans]
            continue
        result = L.evaluate(sample["text"], spans, sample["gold"])
        rows.append({"sample": sample["id"], "expected": len(sample["gold"]),
                     "detected": len(sample["gold"]) - len(result["missed"]),
                     "missed": ", ".join(r["value"] for r in result["missed"]),
                     "false or extra detections": ", ".join(f'{s["text"]} ({s["category"]})' for s in result["extra"])})
    display(Markdown(L.md_table(rows)))
    print(f"Detections in the trap sentences (should be none): {len(traps)}")
    print("  ", "; ".join(traps) or "none")

summarise(default_results)
"""),
    md("""
**Reading the table.** Most expected values count as "detected", but only because the engine flags a lot:
look at the last column and at the trap sentences, which contain no personal data at all. A detector
that flags everything masks everything, and the text passed to the agent loses its meaning.
"""),
    md("""
## B. Why: five default settings

Each cause below is visible in Presidio's source code
([repository](https://github.com/data-privacy-stack/presidio)) and in the run above.

1. **English only.** The default configuration loads one English model
   ([conf/default.yaml](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/conf/default.yaml)).
   Other languages need a model each, and their own context words.
2. **The same score for every name.** spaCy does not give a confidence for the names it finds, so
   Presidio gives them all **0.85**
   ([ner_model_configuration.py](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/nlp_engine/ner_model_configuration.py)).
   A threshold cannot separate a real passenger from "Charles de Gaulle".
3. **Patterns ignore upper and lower case.** `[A-Z2-9]{6}` then also matches "Please", "change",
   "Claire"
   ([pattern_recognizer.py](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/pattern_recognizer.py)).
4. **Context is read only before the value**: 5 words before, 0 after
   ([lemma_context_aware_enhancer.py](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/context_aware_enhancers/lemma_context_aware_enhancer.py)).
5. **No threshold**: everything is returned, down to a score of 0.01
   ([conf/default_analyzer.yaml](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/conf/default_analyzer.yaml)).

Evidence from the run above:
"""),
    code(r"""
names = [s for spans in default_results.values() for s in spans if s["category"] == "PERSON"]
print("Scores given to the names found by spaCy:", sorted({s["score"] for s in names}))
print("Some 'names':", [s["text"] for s in names][:10])
ordinary = [s["text"] for spans in default_results.values() for s in spans
            if s["category"] == "BookingReference" and not any(c.isdigit() for c in s["text"]) and not s["text"].isupper()]
print("Ordinary words taken for booking references:", ordinary)
print("Lowest score returned:", min(s["score"] for spans in default_results.values() for s in spans))
"""),
    md("""
## C. Presidio configured

What changes, each a documented Presidio option:

| Default | Configured |
|---|---|
| English model only | One spaCy model per language: en, fr, de, es; for Hindi, spaCy has no trained pipeline, so the small multilingual model |
| spaCy finds the names (flat 0.85) | **Azure Language** finds generic personal data, with the settings of notebook 01 (places recognised and left out, role words excluded, threshold), through a small custom recognizer signed in with Entra ID. Presidio's built-in Azure recognizer uses an older client library without these settings |
| Every predefined recognizer | Only Azure Language + our travel patterns + a title pattern |
| Case-insensitive patterns | Case-sensitive, same patterns and base scores as the rules of notebook 01 |
| Context words in English, before the value only | Context words per language, 5 words before and 3 after |
| Threshold 0 | Threshold 0.5 |
"""),
    code(r"""
display(Markdown(L.show_code(P.AzureLanguageRecognizer, P.configured_engine)))
"""),
    code(r"""
LANGUAGE_SETTINGS = {"piiCategories": ["Default", "Location", "Airport", "City"],
                     "excludePiiCategories": ["PersonType"],
                     "confidenceScoreThreshold": {"default": 0.6}}
configured = P.configured_engine(LANGUAGE_SETTINGS)
languages = L.detect_languages([s["text"] for s in samples])

configured_results = {}
for sample, lang in zip(samples, languages):
    spans = P.to_spans(sample["text"], configured.analyze(sample["text"], language=lang))
    configured_results[sample["id"]] = spans
    display(Markdown(f'**{sample["id"]}** ({lang})'))
    display(HTML(L.highlight(sample["text"], spans)))
"""),
    code(r"""
summarise(configured_results)
leaked = [(s["id"], v) for s in L.TEXT_SAMPLES
          for v in L.leaks(L.mask(s["text"], configured_results[s["id"]]), s["gold"])]
print("Expected values still readable after masking:", leaked or "none")
"""),
    md("Presidio can explain each decision. Here, why a booking reference was kept:"),
    code(r"""
text = "Mein Buchungscode lautet RVKTPN."
for r in configured.analyze(text, language="de", return_decision_process=True):
    e = r.analysis_explanation
    print(f'{text[r.start:r.end]}: {r.entity_type}, pattern score {e.original_score}, '
          f'context word "{e.supportive_context_word}" -> final score {round(r.score, 2)}')
"""),
    md("""
## D. A trap found on the way: context words in Hindi

Presidio matches context words against the **base form** (lemma) of the neighbouring words, computed by
the spaCy model of the language
([lemma_context_aware_enhancer.py](https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/context_aware_enhancers/lemma_context_aware_enhancer.py)).
The multilingual model used for Hindi has no lemmatizer [Observed]: every base form is empty, so no
context word can ever match. A booking reference made only of letters is then missed in Hindi, while
it is found in English:
"""),
    code(r"""
for text, lang in [("My PNR is RVKTPN", "en"), ("मेरा पीएनआर RVKTPN है", "hi")]:
    found = [(text[r.start:r.end], r.entity_type, round(r.score, 2)) for r in configured.analyze(text, language=lang)]
    print(f"{lang}: {text!r:28} -> {found or 'nothing found'}")
print("Base forms computed for the Hindi sentence:", configured.nlp_engine.process_text("मेरा पीएनआर RVKTPN है", "hi").lemmas)
"""),
    md("""
The rules of notebook 01 look for context words in the raw text, so they do not depend on a
lemmatizer. With Presidio, the fix would be a Hindi pipeline that computes base forms, or a custom
context enhancer [Assumption: not tested here].
"""),
    md("""
## E. Takeaways

- **With its default settings, Presidio produces exactly the kind of errors a multilingual team can
  meet**: airports and greetings taken for people, ordinary words taken for booking references, French
  analysed as English. These come from five default settings, not from the principle.
- **Configured, Presidio gives the same results as notebook 01** on these sentences. The detection of
  generic data comes from Azure Language in both cases; Presidio replaces our own glue code (patterns,
  context, merging) by a framework, and adds explanations.
- **Choosing between the two** is a question of ownership: our ~100 lines follow Microsoft's container
  rule format and have no extra dependency; Presidio is a ready framework with more features (anonymizer
  operators, JSON and image modules), but it needs a spaCy model per language, its Azure connector is
  outdated, its context words need a lemmatizer, and it is a community project without SLA.
- **Neither removes the need for the next layers**: reversible pseudonymisation around the LLM (02),
  minimisation per tool (03). Presidio provides a sample for reversible placeholders, not a ready
  feature [Documented](https://presidio.dataprivacystack.org/samples/python/pseudonymization/).
"""),
]


def write(name, cells):
    nb = nbf.v4.new_notebook()
    nb.cells = cells
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    path = ROOT / "notebooks" / name
    nbf.write(nb, path)
    print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    write("00_setup.ipynb", NB00)
    write("01_inputs.ipynb", NB01)
    write("01b_presidio.ipynb", NB01B)
