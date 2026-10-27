"""Presidio set-ups used by notebook 01b: the default one, and one configured for multilingual travel text.

Presidio is an open-source framework (https://github.com/data-privacy-stack/presidio). Here it is used as
the "glue": Azure Language detects generic personal data, Presidio pattern recognizers detect the
travel identifiers, and Presidio merges the results.
"""
from __future__ import annotations

import logging
import re

from presidio_analyzer import (AnalyzerEngine, EntityRecognizer, Pattern, PatternRecognizer, RecognizerRegistry,
                               RecognizerResult)
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngineProvider

import pii_lab as L

logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)

# spaCy models: medium size to keep downloads small (Presidio's own default is en_core_web_lg).
# No trained Hindi pipeline exists in spaCy: the small multilingual model only tokenises and finds names.
SPACY_MODELS = {"en": "en_core_web_md", "fr": "fr_core_news_md", "de": "de_core_news_md",
                "es": "es_core_news_md", "hi": "xx_ent_wiki_sm"}


def default_engine() -> AnalyzerEngine:
    """What a team gets with the defaults: English only, spaCy names, every predefined recognizer,
    no threshold; plus a booking-reference pattern written the obvious way (default flags, English context)."""
    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": SPACY_MODELS["en"]}]}).create_engine()
    engine = AnalyzerEngine(nlp_engine=nlp)
    engine.registry.add_recognizer(PatternRecognizer(
        supported_entity="BookingReference", patterns=[Pattern("six_characters", r"\b[A-Z2-9]{6}\b", 0.5)],
        context=["pnr", "booking", "reference"]))
    return engine


class AzureLanguageRecognizer(EntityRecognizer):
    """Azure Language PII as a Presidio recognizer, with the settings of notebook 01 (places recognised
    and left out, role words excluded, confidence threshold) and Entra ID sign-in.
    Presidio's built-in Azure recognizer uses an older client library without these settings."""

    ENTITIES = ["Person", "PersonTitle", "PhoneNumber", "Email", "Address", "DateOfBirth", "PassportNumber",
                "Organization", "IPAddress", "CreditCardNumber", "InternationalBankingAccountNumber",
                "DriversLicenseNumber", "NationalId", "GovernmentIssuedId", "DateTime", "Age", "URL"]

    def __init__(self, language: str, settings: dict):
        self.settings = settings
        super().__init__(supported_entities=self.ENTITIES, supported_language=language, name="AzureLanguage")

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        doc = L.language_pii([text], self.supported_language, **self.settings)[0]
        return [RecognizerResult(s["category"], s["offset"], s["offset"] + s["length"], s["score"])
                for s in L.language_spans(doc)]


# Presidio compares each context word with the lemma of ONE surrounding word: no multi-word phrases.
CONTEXT_WORDS = {
    "BookingReference": {"en": ["pnr", "booking", "reference", "locator"],
                         "fr": ["pnr", "réservation", "référence", "dossier"],
                         "de": ["pnr", "buchungscode", "buchungsnummer", "buchungsreferenz"],
                         "es": ["pnr", "localizador", "reserva"],
                         "hi": ["pnr", "पीएनआर"]},
    "HotelConfirmation": {"en": ["confirmation"], "fr": ["confirmation"],
                          "de": ["bestätigungsnummer", "buchungsbestätigung"],
                          "es": ["confirmación"], "hi": ["पुष्टि"]},
    "LoyaltyNumber": {"en": ["flyer", "loyalty", "membership"], "fr": ["fidélité", "voyageur"],
                      "de": ["vielfliegernummer", "vielflieger", "bonusprogramm"],
                      "es": ["frecuente", "socio", "fidelidad"], "hi": ["लॉयल्टी"]},
}

# Same patterns and base scores as the container-format rules of notebook 01
PATTERNS = {rule["name"].removeprefix("CE_"): [Pattern(p["id"], p["pattern"], p["matchScore"])
                                               for p in rule["regexPatterns"]] for rule in L.TRAVEL_RULES}
TITLE_PATTERN = Pattern("title_before_name",
                        r"(?<!\S)(?:Mrs?|Ms|Miss|Dr|Prof|Mme|Mlle|M\.|Herr|Frau|Sr\.|Sra\.|Srta\.)(?=\s+[A-Z])", 0.6)
CASE_SENSITIVE = re.DOTALL | re.MULTILINE   # Presidio adds IGNORECASE by default


def configured_engine(settings: dict, languages: tuple[str, ...] = tuple(SPACY_MODELS),
                      use_azure: bool = True) -> AnalyzerEngine:
    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": lang, "model_name": SPACY_MODELS[lang]} for lang in languages]}).create_engine()
    registry = RecognizerRegistry(supported_languages=list(languages))
    for lang in languages:
        if use_azure:
            registry.add_recognizer(AzureLanguageRecognizer(lang, settings))
        for entity, patterns in PATTERNS.items():
            registry.add_recognizer(PatternRecognizer(
                supported_entity=entity, name=f"{entity}_{lang}", supported_language=lang, patterns=patterns,
                context=CONTEXT_WORDS[entity][lang], global_regex_flags=CASE_SENSITIVE))
        registry.add_recognizer(PatternRecognizer(
            supported_entity="PersonTitle", name=f"PersonTitle_{lang}", supported_language=lang,
            patterns=[TITLE_PATTERN], global_regex_flags=CASE_SENSITIVE))
    enhancer = LemmaContextAwareEnhancer(context_prefix_count=5, context_suffix_count=3)
    return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=list(languages),
                          context_aware_enhancer=enhancer, default_score_threshold=0.5)


def to_spans(text: str, results) -> list[dict]:
    """Presidio results in the span format of pii_lab (to reuse evaluate, mask, highlight)."""
    spans = [{"text": text[r.start:r.end], "category": r.entity_type, "offset": r.start, "length": r.end - r.start,
              "score": round(r.score, 2),
              "source": "azure-language" if r.recognition_metadata.get("recognizer_name") == "AzureLanguage"
              else "presidio"} for r in results]
    return L.merge_spans(spans)
