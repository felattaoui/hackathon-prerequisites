"""Helpers shared by the PII notebooks.

Everything here is deliberately small and readable: the notebooks display the source of the
functions that matter (see `show_code`). All sample data is synthetic.

Sections:
  1. Configuration and Azure calls (Azure Language PII, Document Intelligence Read)
  2. Structured rules (JSON fields, "label: value" lines)
  3. Travel detectors (booking reference, hotel confirmation, loyalty number, group name, title)
  4. Merge, mask, evaluate
  5. Display helpers
  6. Synthetic image (boarding pass) and image redaction
"""
from __future__ import annotations

import base64
import html
import inspect
import io
import os
import re
import time
from pathlib import Path

PII_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 1. Configuration and Azure calls
# ---------------------------------------------------------------------------
LANGUAGE_API_GA = "2026-05-01"
LANGUAGE_API_PREVIEW = "2026-05-15-preview"
LANGUAGE_MODEL = "2026-05-01"   # pinned model version: results stay reproducible
DOC_INTEL_API = "2024-11-30"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def load_env() -> dict:
    """Read pii/.env (git-ignored, written by scripts/deploy.sh). Never print its content."""
    env = {}
    path = PII_ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"')
    env.update({k: v for k, v in os.environ.items() if k.startswith("PII_")})
    return env


def foundry_endpoint() -> str:
    endpoint = load_env().get("PII_FOUNDRY_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("PII_FOUNDRY_ENDPOINT missing: run scripts/deploy.sh (see README).")
    return endpoint.rstrip("/")


_credential = None


def _token() -> str:
    """Entra ID token from the Azure CLI login (no API keys: local auth is disabled on the resource)."""
    global _credential
    if _credential is None:
        from azure.identity import AzureCliCredential
        _credential = AzureCliCredential()
    return _credential.get_token(COGNITIVE_SCOPE).token


def _post(url: str, body: dict, params: dict) -> "httpx.Response":
    import httpx
    return httpx.post(url, params=params, json=body, timeout=60,
                      headers={"Authorization": f"Bearer {_token()}"})


def language_pii(texts: list[str], languages: list[str] | str = "en", api_version: str = LANGUAGE_API_GA,
                 **parameters) -> list[dict]:
    """Call Azure Language PII (analyze-text, synchronous) and return one result document per text.

    `parameters` go straight into the request "parameters" object (piiCategories, redactionPolicies,
    confidenceScoreThreshold, valueExclusionPolicy, ...). The service accepts 5 documents per
    synchronous request, so texts are sent in batches of 5.
    """
    if isinstance(languages, str):
        languages = [languages] * len(texts)
    params = {"modelVersion": LANGUAGE_MODEL, "stringIndexType": "UnicodeCodePoint", **parameters}
    results: list[dict] = []
    for start in range(0, len(texts), 5):
        batch = [{"id": str(i), "language": languages[i], "text": texts[i]}
                 for i in range(start, min(start + 5, len(texts)))]
        body = {"kind": "PiiEntityRecognition", "parameters": params, "analysisInput": {"documents": batch}}
        response = _post(f"{foundry_endpoint()}/language/:analyze-text", body, {"api-version": api_version})
        if response.status_code != 200:
            raise RuntimeError(f"Language PII HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()["results"]
        if payload.get("errors"):
            raise RuntimeError(f"Language PII document errors: {payload['errors']}")
        for doc in sorted(payload["documents"], key=lambda d: int(d["id"])):
            doc["modelVersion"] = payload.get("modelVersion")
            results.append(doc)
    return results


def detect_languages(texts: list[str]) -> list[str]:
    """Azure Language detection (ISO 639-1 codes). Needed because the GA PII API rejects language "auto"."""
    codes: list[str] = []
    for start in range(0, len(texts), 5):
        batch = [{"id": str(i), "text": texts[i]} for i in range(start, min(start + 5, len(texts)))]
        body = {"kind": "LanguageDetection", "parameters": {"modelVersion": "latest"},
                "analysisInput": {"documents": batch}}
        response = _post(f"{foundry_endpoint()}/language/:analyze-text", body, {"api-version": LANGUAGE_API_GA})
        if response.status_code != 200:
            raise RuntimeError(f"Language detection HTTP {response.status_code}: {response.text[:500]}")
        docs = sorted(response.json()["results"]["documents"], key=lambda d: int(d["id"]))
        codes += [d["detectedLanguage"]["iso6391Name"] for d in docs]
    return codes


# Places are not personal data. We ask Language to recognise them (piiCategories) so that an
# airport named after a person comes back as a place, then we keep them out of masking.
GEO_TYPES = {"Location", "Airport", "City", "GPE", "State"}


def entity_type(entity: dict) -> str:
    """The precise label of an entity. Language returns a broad `category` (e.g. DateTime) and a precise
    `type` (e.g. DateOfBirth); country-specific passport types are grouped under PassportNumber."""
    kind = entity.get("type") or entity["category"]
    return "PassportNumber" if "PassportNumber" in kind else kind


def language_spans(doc: dict, keep_places: bool = False) -> list[dict]:
    """Convert Language entities to the common span format used by every detector in these notebooks."""
    spans = [{"text": e["text"], "category": entity_type(e), "family": e["category"], "offset": e["offset"],
              "length": e["length"], "score": e.get("confidenceScore"), "source": "azure-language"}
             for e in doc["entities"]]
    return spans if keep_places else [s for s in spans if s["category"] not in GEO_TYPES]


def ocr_read(image_bytes: bytes, poll_seconds: float = 1.0) -> dict:
    """Document Intelligence Read (prebuilt-read): text, words, word polygons and word offsets."""
    import httpx
    url = f"{foundry_endpoint()}/documentintelligence/documentModels/prebuilt-read:analyze"
    response = _post(url, {"base64Source": base64.b64encode(image_bytes).decode()},
                     {"api-version": DOC_INTEL_API, "stringIndexType": "unicodeCodePoint"})
    if response.status_code != 202:
        raise RuntimeError(f"Document Intelligence HTTP {response.status_code}: {response.text[:500]}")
    operation = response.headers["Operation-Location"]
    for _ in range(60):
        time.sleep(poll_seconds)
        status = httpx.get(operation, headers={"Authorization": f"Bearer {_token()}"}, timeout=60).json()
        if status["status"] == "succeeded":
            return status["analyzeResult"]
        if status["status"] == "failed":
            raise RuntimeError(f"Document Intelligence failed: {status.get('error')}")
    raise TimeoutError("Document Intelligence did not finish in time")


# ---------------------------------------------------------------------------
# 2. Structured rules: the field name tells us what the value is
# ---------------------------------------------------------------------------
def _norm(key: str) -> str:
    return re.sub(r"[\s_\-]", "", key).lower()


# Keys are normalised field names; "parent.child" entries win over the bare child name.
FIELD_RULES = {
    "pnr": "BookingReference", "recordlocator": "BookingReference", "bookingreference": "BookingReference",
    "bookingref": "BookingReference",
    "hotelconfirmationnumber": "HotelConfirmation", "hotel.confirmationnumber": "HotelConfirmation",
    "loyaltynumber": "LoyaltyNumber", "frequentflyernumber": "LoyaltyNumber", "frequentflyer": "LoyaltyNumber",
    "loyalty.number": "LoyaltyNumber",
    "firstname": "Person", "lastname": "Person", "givenname": "Person", "surname": "Person",
    "fullname": "Person", "passengername": "Person", "passenger": "Person",
    "title": "PersonTitle", "groupname": "GroupName",
    "dateofbirth": "DateOfBirth", "birthdate": "DateOfBirth", "dob": "DateOfBirth",
    "passportnumber": "PassportNumber", "passport.number": "PassportNumber",
    "nationalid": "NationalId", "documentnumber": "IdDocumentNumber",
    "email": "Email", "emailaddress": "Email", "phone": "PhoneNumber", "phonenumber": "PhoneNumber",
    "mobile": "PhoneNumber", "address": "Address", "street": "Address",
}


# Fields known NOT to hold personal data: skipped by text detection (a lone "NCE" sent to a
# general-purpose model can come back as an organisation and be masked for nothing).
NON_PII_FIELDS = {"from", "to", "flight", "date", "country", "program", "hotelname", "currency", "seat"}


def field_category(path: list[str]) -> str | None:
    keys = [_norm(k) for k in path if not k.isdigit()]
    if len(keys) >= 2 and f"{keys[-2]}.{keys[-1]}" in FIELD_RULES:
        return FIELD_RULES[f"{keys[-2]}.{keys[-1]}"]
    return FIELD_RULES.get(keys[-1]) if keys else None


def scan_json(obj, path: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Walk a JSON payload. Returns (findings from field rules, other strings to analyse as free text)."""
    path = path or []
    findings: list[dict] = []
    free_text: list[dict] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            f, t = scan_json(value, path + [str(key)])
            findings += f
            free_text += t
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            f, t = scan_json(value, path + [str(i)])
            findings += f
            free_text += t
    elif isinstance(obj, (str, int)) and path:
        category = field_category(path)
        if category:
            findings.append({"path": ".".join(path), "category": category, "value": str(obj), "source": "field-rule"})
        elif isinstance(obj, str) and re.search(r"[^\W\d_]", obj) and _norm(path[-1]) not in NON_PII_FIELDS:
            free_text.append({"path": ".".join(path), "value": obj})
    return findings, free_text


LABEL_RULES = {**FIELD_RULES, "name": "Person", "bookingrefpnr": "BookingReference"}


_LABEL = re.compile(r"((?:[^\W\d_]+[ /.]?){1,3}?)[ \t]*:[ \t]*")


def scan_labelled_lines(text: str) -> list[dict]:
    """Same rules applied to "Label: value" pairs (forms, OCR output, e-mails). A label is the last
    1 to 3 words before a colon, so it also works when OCR merges two lines; the value runs to the
    next label or the end of the line."""
    labels = []
    for m in _LABEL.finditer(text):
        words = list(re.finditer(r"[^\W\d_]+", m.group(1)))
        category, n = next(((LABEL_RULES[k], n) for n in (3, 2, 1) if len(words) >= n
                            for k in [_norm(" ".join(w.group() for w in words[-n:]))] if k in LABEL_RULES),
                           (None, 1))
        labels.append({"start": m.start(1) + words[-n].start(), "value_start": m.end(), "category": category})
    spans = []
    for i, label in enumerate(labels):
        if not label["category"]:
            continue
        end = text.find("\n", label["value_start"])
        end = len(text) if end < 0 else end
        if i + 1 < len(labels) and labels[i + 1]["start"] < end:
            end = labels[i + 1]["start"]
        value = text[label["value_start"]:end].rstrip()
        if value:
            spans.append({"text": value, "category": label["category"], "offset": label["value_start"],
                          "length": len(value), "score": 1.0, "source": "field-rule"})
    return spans


# ---------------------------------------------------------------------------
# 3. Travel detectors, written in the rule-file format of the Azure Language PII container
# ---------------------------------------------------------------------------
# The cloud API cannot learn new entity types. The Text PII *container* can, with a rule file
# (UserRegexRuleFilePath): https://learn.microsoft.com/azure/ai-services/language-service/
# personally-identifiable-information/how-to/adapt-to-domain-pii
# We write our rules in that exact format and apply them with `apply_rules`, which follows the
# documented semantics: a pattern gives a base score (matchScore); a context hint found near the
# match adds its boostingScore; when several hints match, the highest one is used.

# Patterns provided for the hackathon (too broad on their own for free text):
BOOKING_REF_PATTERN = r"(?<![A-Za-z0-9])[A-Z2-9]{6}(?![A-Za-z0-9])"   # from ^[A-Z2-9]{6}$
HOTEL_CONF_PATTERN = r"[0-9a-zA-Z]{2,20}"                              # matches almost any word

LOCALES = ["en", "fr", "de", "es", "hi"]
ACCEPT_SCORE = 0.6   # a match is reported when its score reaches this value

CONTEXT = {
    "BookingReference": {
        "en": ["pnr", "booking ref(?:erence)?", "booking code", "record locator", "reservation code",
               "airline reference"],
        "fr": ["pnr", "référence de réservation", "référence de dossier", "numéro de réservation",
               "code de réservation", "dossier de réservation"],
        "de": ["pnr", "buchungscode", "buchungsnummer", "buchungsreferenz", "reservierungscode"],
        "es": ["pnr", "localizador", "código de reserva", "referencia de reserva"],
        "hi": ["pnr", "पीएनआर", "बुकिंग संदर्भ", "बुकिंग कोड"],
    },
    "HotelConfirmation": {
        "en": ["hotel confirmation(?: number)?", "confirmation (?:number|no\\.?|#)"],
        "fr": ["numéro de confirmation", "n° de confirmation", "confirmation (?:de l')?hôtel"],
        "de": ["bestätigungsnummer", "buchungsbestätigung"],
        "es": ["número de confirmación", "confirmación del hotel"],
        "hi": ["पुष्टि संख्या", "कन्फर्मेशन नंबर"],
    },
    "LoyaltyNumber": {
        "en": ["frequent flyer(?: number)?", "loyalty (?:number|card|account)", "membership number"],
        "fr": ["carte de fidélité", "numéro de fidélité", "programme de fidélité", "grand voyageur"],
        "de": ["vielfliegernummer", "vielflieger", "bonusprogramm"],
        "es": ["viajero frecuente", "número de socio", "programa de fidelidad"],
        "hi": ["लॉयल्टी नंबर", "फ़्रीक्वेंट फ़्लायर"],
    },
    "GroupName": {
        "en": ["group name"], "fr": ["nom du groupe"], "de": ["gruppenname"],
        "es": ["nombre del grupo"], "hi": ["समूह का नाम"],
    },
}


def _hints(category: str, boost: float, limit: int = 40) -> list[dict]:
    return [{"hintText": word, "boostingScore": boost, "locales": [lang], "contextLimit": limit}
            for lang, words in CONTEXT[category].items() for word in words]


TRAVEL_RULES = [
    {"name": "CE_BookingReference",
     "description": "Booking reference (PNR): 6 characters, letters A-Z and digits 2-9",
     "regexPatterns": [
         # letters AND digits, and not shaped like a flight number (2 letters + 4 digits)
         {"id": "MixedLettersDigits", "matchScore": 0.65, "locales": LOCALES,
          "pattern": r"(?<![A-Za-z0-9])(?=[A-Z2-9]{0,5}[2-9])(?=[A-Z2-9]{0,5}[A-Z])"
                     r"(?![A-Z]{2}[2-9]{4}(?![A-Za-z0-9]))[A-Z2-9]{6}(?![A-Za-z0-9])"},
         # anything else of that shape (BERLIN, YLOWFR, XY2345): needs a context word
         {"id": "AnySixCharacters", "matchScore": 0.3, "locales": LOCALES, "pattern": BOOKING_REF_PATTERN},
     ],
     "matchContext": {"hints": _hints("BookingReference", 0.4)}},
    {"name": "CE_HotelConfirmation",
     "description": "Hotel confirmation number: 2 to 20 letters or digits, at least one digit",
     "regexPatterns": [
         {"id": "AlphanumericWithDigit", "matchScore": 0.2, "locales": LOCALES,
          "pattern": r"(?<![A-Za-z0-9])(?=[A-Za-z]{0,19}\d)[0-9a-zA-Z]{2,20}(?![A-Za-z0-9])"},
     ],
     "matchContext": {"hints": _hints("HotelConfirmation", 0.5)}},
    {"name": "CE_LoyaltyNumber",
     "description": "Loyalty number: optional 2-character programme code, then 6 to 16 digits",
     "regexPatterns": [
         {"id": "ProgrammeAndDigits", "matchScore": 0.3, "locales": LOCALES,
          "pattern": r"(?<![A-Za-z0-9])(?:[A-Z0-9]{2} )?\d{6,16}(?![A-Za-z0-9])"},
     ],
     "matchContext": {"hints": _hints("LoyaltyNumber", 0.4)}},
]


def apply_rules(text: str, rules: list[dict] = TRAVEL_RULES, language: str | None = None,
                accept: float = ACCEPT_SCORE) -> tuple[list[dict], list[dict]]:
    """Apply container-format rules. Returns (accepted spans, rejected candidates with their score).

    One choice of ours, not documented for the container: a hint only counts when it is on the same
    line and in the same sentence as the match (a keyword on the next line says nothing about it)."""
    same_sentence = lambda a, b: not re.search(r"[\n.!?।]", text[a:b])
    accepted, rejected = [], []
    for rule in rules:
        category = rule["name"].removeprefix("CE_")
        candidates: dict[tuple[int, int], tuple[float, str]] = {}
        for pattern in rule["regexPatterns"]:
            if language and language not in pattern["locales"]:
                continue
            for m in re.finditer(pattern["pattern"], text):
                if pattern["matchScore"] > candidates.get(m.span(), (0.0, ""))[0]:
                    candidates[m.span()] = (pattern["matchScore"], pattern["id"])
        for (start, end), (base, pattern_id) in sorted(candidates.items()):
            boost, hint_word = 0.0, None
            for hint in rule["matchContext"]["hints"]:
                if language and language not in hint["locales"]:
                    continue
                for h in re.finditer(hint["hintText"], text, re.IGNORECASE):
                    near_before = 0 <= start - h.end() <= hint["contextLimit"] and same_sentence(h.end(), start)
                    near_after = 0 <= h.start() - end <= hint["contextLimit"] and same_sentence(end, h.start())
                    if (near_before or near_after) and hint["boostingScore"] > boost:
                        boost, hint_word = hint["boostingScore"], h.group()
            score = round(base + boost, 2)
            why = f"{pattern_id} {base}" + (f" + context '{hint_word}' {boost}" if hint_word else ", no context")
            item = {"text": text[start:end], "category": category, "offset": start, "length": end - start,
                    "score": score, "source": f"travel-rule ({why})"}
            (accepted if score >= accept else rejected).append(item)
    return accepted, rejected


# Two cues that are about sentence structure rather than the shape of a code, so they do not fit
# the pattern + hint model: they stay small helpers.
def find_group_names(text: str, window: int = 40) -> list[dict]:
    """Group name: a quoted string or 1 to 5 capitalised words right after a context keyword."""
    keywords = re.compile("|".join(f"(?:{w})" for ws in CONTEXT["GroupName"].values() for w in ws), re.IGNORECASE)
    value = re.compile(r"[\s:]*(\"[^\"]{2,60}\"|[A-Z][\w'-]+(?: [A-Z][\w'-]+){0,4})")
    spans = []
    for k in keywords.finditer(text):
        m = value.match(text, k.end(), k.end() + window)
        if m:
            spans.append({"text": m.group(1), "category": "GroupName", "offset": m.start(1),
                          "length": len(m.group(1)), "score": 0.9, "source": "travel-rule (after 'group name')"})
    return spans


TITLES = ["Mr", "Mrs", "Ms", "Miss", "Dr", "Prof", "M.", "Mme", "Mlle", "Herr", "Frau", "Sr.", "Sra.",
          "Srta.", "श्री", "श्रीमती", "सुश्री"]


def find_titles(text: str, person_spans: list[dict]) -> list[dict]:
    """Person title (Mr, Mme, Herr, Sra., श्रीमती ...) directly followed by a detected person name."""
    starts = {p["offset"] for p in person_spans if p["category"] == "Person"}
    spans = []
    for title in sorted(TITLES, key=len, reverse=True):
        pattern = re.escape(title) + (r"\.?" if not title.endswith(".") else "")
        for m in re.finditer(rf"(?<!\S){pattern}(?=\s)", text):
            nxt = re.match(r"\s+", text[m.end():])
            if nxt and m.end() + nxt.end() in starts and not any(s["offset"] == m.start() for s in spans):
                spans.append({"text": m.group(), "category": "PersonTitle", "offset": m.start(),
                              "length": len(m.group()), "score": 0.9, "source": "travel-rule (title before name)"})
    return spans


def travel_spans(text: str, person_spans: list[dict] | None = None, language: str | None = None) -> list[dict]:
    accepted, _ = apply_rules(text, language=language)
    return accepted + find_group_names(text) + find_titles(text, person_spans or [])


# ---------------------------------------------------------------------------
# 4. Merge, mask, evaluate
# ---------------------------------------------------------------------------
SOURCE_PRIORITY = {"field-rule": 0, "travel-rule": 1, "azure-language": 2}


def _priority(span: dict) -> int:
    return SOURCE_PRIORITY.get(span["source"].split(" ")[0], 9)


def merge_spans(*groups: list[dict]) -> list[dict]:
    """Union of detector outputs; when two spans overlap keep the longer one, then the more specific source."""
    spans = sorted((s for g in groups for s in g), key=lambda s: (-s["length"], _priority(s)))
    kept: list[dict] = []
    for s in spans:
        if all(s["offset"] + s["length"] <= k["offset"] or s["offset"] >= k["offset"] + k["length"] for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s["offset"])


def mask(text: str, spans: list[dict]) -> str:
    """Replace each span by [CATEGORY]. One-way: notebook 02 introduces reversible pseudonymization."""
    out, pos = [], 0
    for s in sorted(spans, key=lambda s: s["offset"]):
        out.append(text[pos:s["offset"]])
        out.append(f"[{s['category'].upper()}]")
        pos = s["offset"] + s["length"]
    return "".join(out) + text[pos:]


def evaluate(text: str, spans: list[dict], gold: list[tuple[str, str]]) -> dict:
    """Compare detections with the expected PII (value, category) of a synthetic sample."""
    rows, covered = [], set()
    for value, category in gold:
        start = text.find(value)
        hit = [s for s in spans if start >= 0 and s["offset"] < start + len(value) and start < s["offset"] + s["length"]]
        covered.update(id(s) for s in hit)
        rows.append({"value": value, "expected": category,
                     "detected": ", ".join(sorted({f"{s['category']} ({s['source'].split(' ')[0]})" for s in hit})) or "MISSED"})
    extra = [s for s in spans if id(s) not in covered]
    return {"rows": rows, "missed": [r for r in rows if r["detected"] == "MISSED"], "extra": extra}


def mask_json(obj, text_spans: dict[str, list[dict]], path: list[str] | None = None):
    """Masked copy of a JSON payload: field-rule values become [CATEGORY]; free-text values are masked
    with the spans found for their path (`text_spans` maps "a.b.0.c" to spans)."""
    path = path or []
    if isinstance(obj, dict):
        return {k: mask_json(v, text_spans, path + [str(k)]) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_json(v, text_spans, path + [str(i)]) for i, v in enumerate(obj)]
    category = field_category(path) if path else None
    if category:
        return f"[{category.upper()}]"
    if isinstance(obj, str) and ".".join(path) in text_spans:
        return mask(obj, text_spans[".".join(path)])
    return obj


def leaks(masked_text: str, gold: list[tuple[str, str]]) -> list[str]:
    """Expected PII values still readable after masking (must be empty)."""
    return [value for value, _ in gold if value in masked_text]


# ---------------------------------------------------------------------------
# 5. Display helpers
# ---------------------------------------------------------------------------
COLORS = {"azure-language": "#bbdefb", "travel-rule": "#ffe0b2", "field-rule": "#c8e6c9", "presidio": "#e1bee7"}


def highlight(text: str, spans: list[dict]) -> str:
    """HTML rendering: each detected span is coloured by source and tagged with its category."""
    out, pos = [], 0
    for s in sorted(spans, key=lambda s: s["offset"]):
        color = COLORS.get(s["source"].split(" ")[0], "#eeeeee")
        out.append(html.escape(text[pos:s["offset"]]))
        out.append(f'<span style="background:{color};padding:1px 3px;border-radius:3px" '
                   f'title="{html.escape(s["source"])}">{html.escape(text[s["offset"]:s["offset"] + s["length"]])}'
                   f'<sub style="font-size:70%;color:#555"> {s["category"]}</sub></span>')
        pos = s["offset"] + s["length"]
    out.append(html.escape(text[pos:]))
    return '<div style="line-height:1.9;font-family:sans-serif">' + "".join(out) + "</div>"


def legend(sources: tuple[str, ...] = ("azure-language", "travel-rule", "field-rule")) -> str:
    return " ".join(f'<span style="background:{COLORS[n]};padding:1px 6px;border-radius:3px">{n}</span>'
                    for n in sources)


def md_table(rows: list[dict], columns: list[str] | None = None) -> str:
    if not rows:
        return "_(none)_"
    columns = columns or list(rows[0])
    cell = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(cell(r.get(c, "")) for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def show_code(*objects) -> str:
    return "\n\n".join("```python\n" + inspect.getsource(o).rstrip() + "\n```" for o in objects)


# ---------------------------------------------------------------------------
# 6. Synthetic image and image redaction
# ---------------------------------------------------------------------------
BOARDING_PASS_LINES = [
    "BOARDING PASS - SPECIMEN - SYNTHETIC DATA",
    "Passenger: WHITFIELD/JANE MRS",
    "From: NICE (NCE)    To: PARIS (CDG)",
    "Flight: XY7234    Seat: 12A    Date: 20NOV",
    "Booking ref: K7M2QR",
    "Frequent flyer: XY 4815162342",
]


def make_boarding_pass(lines: list[str] = BOARDING_PASS_LINES) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=30)
    image = Image.new("RGB", (1000, 80 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 989, image.height - 11], outline="navy", width=4)
    for i, line in enumerate(lines):
        draw.text((40, 40 + 60 * i), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def redact_image(image_bytes: bytes, ocr: dict, spans: list[dict]) -> bytes:
    """Draw a black box over every OCR word that overlaps a detected span."""
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for page in ocr["pages"]:
        for word in page["words"]:
            w_start, w_end = word["span"]["offset"], word["span"]["offset"] + word["span"]["length"]
            if any(s["offset"] < w_end and w_start < s["offset"] + s["length"] for s in spans):
                xs, ys = word["polygon"][0::2], word["polygon"][1::2]
                draw.rectangle([min(xs) - 3, min(ys) - 3, max(xs) + 3, max(ys) + 3], fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Synthetic samples (fictitious people and identifiers) with the expected PII ("gold")
# ---------------------------------------------------------------------------
TEXT_SAMPLES = [
    {"id": "en", "language": "en",
     "text": "Hi, I'm Mrs Jane Whitfield. Please change PNR K7M2QR for me and my son Leo Whitfield. "
             "You can reach me on +44 20 7946 0958 or jane.whitfield@example.com. My passport number is 523941876.",
     "gold": [("Mrs", "PersonTitle"), ("Jane Whitfield", "Person"), ("K7M2QR", "BookingReference"),
              ("Leo Whitfield", "Person"), ("+44 20 7946 0958", "PhoneNumber"),
              ("jane.whitfield@example.com", "Email"), ("523941876", "PassportNumber")]},
    {"id": "fr", "language": "fr",
     "text": "Bonjour, je suis Mme Claire Delaunay, née le 14/03/1985. Ma référence de réservation est 7XQ4TB "
             "et ma carte de fidélité XY 4815162342. J'atterris à Charles de Gaulle à 7h40, terminal 2E.",
     "gold": [("Mme", "PersonTitle"), ("Claire Delaunay", "Person"), ("14/03/1985", "DateOfBirth"),
              ("7XQ4TB", "BookingReference"), ("XY 4815162342", "LoyaltyNumber")]},
    {"id": "de", "language": "de",
     "text": "Guten Tag, hier ist Herr Lukas Brenner. Mein Buchungscode lautet RVKTPN, die Bestätigungsnummer "
             "des Hotels ist a889Ce6FFF8d0. Telefon: +49 30 5550 1234.",
     "gold": [("Herr", "PersonTitle"), ("Lukas Brenner", "Person"), ("RVKTPN", "BookingReference"),
              ("a889Ce6FFF8d0", "HotelConfirmation"), ("+49 30 5550 1234", "PhoneNumber")]},
    {"id": "es", "language": "es",
     "text": "Soy el Sr. Diego Navarro, localizador HT5W2N, número de viajero frecuente XY 99887766. "
             "Mi dirección es Calle Mayor 10, 28013 Madrid. Nombre del grupo: Blue Heron Travellers.",
     "gold": [("Sr.", "PersonTitle"), ("Diego Navarro", "Person"), ("HT5W2N", "BookingReference"),
              ("XY 99887766", "LoyaltyNumber"), ("Calle Mayor 10, 28013 Madrid", "Address"),
              ("Blue Heron Travellers", "GroupName")]},
    {"id": "hi", "language": "hi",
     "text": "नमस्ते, मेरा नाम प्रिया शर्मा है। मेरा पीएनआर 9KDF4M है और मेरा फ़ोन नंबर +91 98765 43210 है।",
     "gold": [("प्रिया शर्मा", "Person"), ("9KDF4M", "BookingReference"), ("+91 98765 43210", "PhoneNumber")]},
    {"id": "en-airport", "language": "en",
     "text": "Charles Martin will pick you up at Charles de Gaulle.",
     "gold": [("Charles Martin", "Person")]},
]

# Sentences that contain NO personal data but look like they could (false-positive traps).
TRAP_SAMPLES = [
    {"id": "trap-airport-fr", "language": "fr",
     "text": "Le vol XY7234 part de Charles de Gaulle à 9h05 et arrive à Nice à 10h35."},
    {"id": "trap-airport-en", "language": "en",
     "text": "Shuttles leave Charles de Gaulle Airport every 20 minutes for the city centre."},
    {"id": "trap-codes-en", "language": "en",
     "text": "Fare basis YLOWFR applies to flight XY2345 from BERLIN to MADRID, gate K22."},
    {"id": "trap-amount-de", "language": "de",
     "text": "Der Tarif kostet 489 Euro, Abflug um 7:45 Uhr von Terminal 2."},
    {"id": "trap-airport-de", "language": "de",
     "text": "Ich fliege ab Franz Josef Strauss nach Nizza."},
    {"id": "trap-meeting-fr", "language": "fr",
     "text": "Rendez-vous devant Charles de Gaulle, porte 6."},
]

BOOKING_PAYLOAD = {
    "booking": {"recordLocator": "K7M2QR",
                "hotel": {"hotelName": "Hotel Riviera Azur", "confirmationNumber": "a889Ce6FFF8d0"}},
    "passengers": [{
        "title": "Mrs", "firstName": "Jane", "lastName": "Whitfield", "dateOfBirth": "1985-03-14",
        "passport": {"number": "523941876", "country": "GB"},
        "loyalty": {"program": "XY", "number": "4815162342"},
        "contact": {"email": "jane.whitfield@example.com", "phone": "+44 20 7946 0958"},
    }],
    "itinerary": [{"from": "NCE", "to": "CDG", "flight": "XY7234", "date": "2026-11-20"}],
    "notes": "Passenger Leo Whitfield travels with his mother; nom du groupe: Blue Heron Travellers.",
}

BOOKING_PAYLOAD_GOLD = [
    ("K7M2QR", "BookingReference"), ("a889Ce6FFF8d0", "HotelConfirmation"), ("Mrs", "PersonTitle"),
    ("Jane", "Person"), ("Whitfield", "Person"), ("1985-03-14", "DateOfBirth"), ("523941876", "PassportNumber"),
    ("4815162342", "LoyaltyNumber"), ("jane.whitfield@example.com", "Email"), ("+44 20 7946 0958", "PhoneNumber"),
    ("Leo Whitfield", "Person"), ("Blue Heron Travellers", "GroupName"),
]

BOARDING_PASS_GOLD = [("WHITFIELD/JANE MRS", "Person"), ("K7M2QR", "BookingReference"),
                      ("XY 4815162342", "LoyaltyNumber")]
