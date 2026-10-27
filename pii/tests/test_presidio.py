"""Offline tests of the Presidio set-ups of notebook 01b (patterns only, no Azure call)."""
import pytest

pytest.importorskip("presidio_analyzer")
spacy = pytest.importorskip("spacy")
if not all(spacy.util.is_package(m) for m in ("en_core_web_md", "de_core_news_md", "xx_ent_wiki_sm")):
    pytest.skip("spaCy models of notebook 01b not installed", allow_module_level=True)

import presidio_lab as P  # noqa: E402


def found(engine, text, language):
    return {(text[r.start:r.end], r.entity_type) for r in engine.analyze(text, language=language)}


def test_default_settings_take_ordinary_words_for_booking_references():
    hits = found(P.default_engine(), "Please change my seat.", "en")
    assert ("Please", "BookingReference") in hits and ("change", "BookingReference") in hits


def test_configured_patterns_need_context_and_respect_case():
    engine = P.configured_engine({}, languages=("en", "de"), use_azure=False)
    assert found(engine, "Please change my seat. Flight XY2345 to BERLIN.", "en") == set()
    assert ("RVKTPN", "BookingReference") in found(engine, "Mein Buchungscode lautet RVKTPN.", "de")


def test_context_words_do_not_work_without_a_lemmatizer():
    # documents the trap shown in notebook 01b: the Hindi model computes no base forms
    engine = P.configured_engine({}, languages=("en", "hi"), use_azure=False)
    assert ("RVKTPN", "BookingReference") in found(engine, "My PNR is RVKTPN", "en")
    assert found(engine, "मेरा पीएनआर RVKTPN है", "hi") == set()
