"""Offline tests of the rule-based detectors used in notebook 01 (no Azure call)."""
import json
import re

import pii_lab as L


def texts(spans):
    return [s["text"] for s in spans]


def by_category(spans, category):
    return [s["text"] for s in spans if s["category"] == category]


def test_booking_reference_needs_context_or_mixed_characters():
    accepted, rejected = L.apply_rules("Fare basis YLOWFR on flight XY2345 to BERLIN.")
    assert by_category(accepted, "BookingReference") == []
    assert {"YLOWFR", "XY2345", "BERLIN"} <= set(texts(rejected))
    accepted, _ = L.apply_rules("Mein Buchungscode lautet RVKTPN.")
    assert by_category(accepted, "BookingReference") == ["RVKTPN"]
    accepted, _ = L.apply_rules("Can you check K7M2QR please?")
    assert by_category(accepted, "BookingReference") == ["K7M2QR"]


def test_scores_follow_documented_semantics():
    accepted, _ = L.apply_rules("PNR K7M2QR")
    # base score of the best pattern + boost of the best hint (hints are not added together)
    assert accepted[0]["score"] == 0.65 + 0.4


def test_context_does_not_cross_a_line_or_sentence():
    accepted, rejected = L.apply_rules("Flight: XY7234 Seat: 12A\nBooking ref: K7M2QR")
    assert by_category(accepted, "BookingReference") == ["K7M2QR"]
    assert "XY7234" in texts(rejected)
    accepted, _ = L.apply_rules("Gate change for XY7234. Your PNR is ready.")
    assert by_category(accepted, "BookingReference") == []


def test_booking_reference_context_in_hindi():
    accepted, _ = L.apply_rules("मेरा पीएनआर 9KDF4M है", language="hi")
    assert by_category(accepted, "BookingReference") == ["9KDF4M"]


def test_hotel_confirmation_only_with_context():
    accepted, _ = L.apply_rules("The hotel a889Ce6FFF8d0 is nice")
    assert by_category(accepted, "HotelConfirmation") == []
    accepted, _ = L.apply_rules("die Bestätigungsnummer des Hotels ist a889Ce6FFF8d0.")
    assert by_category(accepted, "HotelConfirmation") == ["a889Ce6FFF8d0"]


def test_loyalty_and_group_name():
    accepted, _ = L.apply_rules("ma carte de fidélité XY 4815162342. Merci")
    assert by_category(accepted, "LoyaltyNumber") == ["XY 4815162342"]
    accepted, _ = L.apply_rules("My passport number is 523941876.")
    assert by_category(accepted, "LoyaltyNumber") == []
    assert texts(L.find_group_names("Nombre del grupo: Blue Heron Travellers.")) == ["Blue Heron Travellers"]


def test_title_only_before_a_person():
    text = "Hello Mrs Jane Whitfield and Mr Smith"
    person = [{"text": "Jane Whitfield", "category": "Person", "offset": 10, "length": 14, "source": "azure-language"}]
    assert texts(L.find_titles(text, person)) == ["Mrs"]


def test_rule_detectors_cover_travel_gold_values():
    for sample in L.TEXT_SAMPLES:
        found = " ".join(texts(L.travel_spans(sample["text"], language=sample["language"])))
        for value, category in sample["gold"]:
            if category in {"BookingReference", "HotelConfirmation", "LoyaltyNumber", "GroupName"}:
                assert value in found, (sample["id"], value)


def test_traps_produce_no_rule_detection():
    for trap in L.TRAP_SAMPLES:
        assert L.travel_spans(trap["text"], language=trap["language"]) == [], trap["id"]


def test_language_spans_read_type_group_passports_and_drop_places():
    doc = {"entities": [
        {"text": "14/03/1985", "category": "DateTime", "type": "DateOfBirth", "offset": 0, "length": 10, "confidenceScore": 0.99},
        {"text": "523941876", "category": "USUKPassportNumber", "type": "USUKPassportNumber", "offset": 11, "length": 9, "confidenceScore": 0.75},
        {"text": "Charles de Gaulle", "category": "Location", "type": "Location", "offset": 21, "length": 17, "confidenceScore": 0.99},
    ]}
    assert [s["category"] for s in L.language_spans(doc)] == ["DateOfBirth", "PassportNumber"]
    assert len(L.language_spans(doc, keep_places=True)) == 3


def test_a_place_never_hides_a_booking_reference():
    # Language once returned "PNR K7M2QR" as a Location: places are dropped before the merge
    text = "Please change PNR K7M2QR for me."
    doc = {"entities": [{"text": "PNR K7M2QR", "category": "Location", "type": "Location",
                         "offset": 14, "length": 10, "confidenceScore": 0.7}]}
    spans = L.merge_spans(L.travel_spans(text), L.language_spans(doc))
    assert L.leaks(L.mask(text, spans), [("K7M2QR", "BookingReference")]) == []


def test_json_field_rules_and_masking_leave_no_structured_value():
    findings, free_text = L.scan_json(L.BOOKING_PAYLOAD)
    assert {f["category"] for f in findings} >= {"BookingReference", "PassportNumber", "LoyaltyNumber", "Email"}
    notes = L.BOOKING_PAYLOAD["notes"]
    person = notes.index("Leo Whitfield")
    spans = {"notes": L.merge_spans(L.travel_spans(notes), [
        {"text": "Leo Whitfield", "category": "Person", "offset": person, "length": 13, "source": "azure-language"}])}
    masked = json.dumps(L.mask_json(L.BOOKING_PAYLOAD, spans), ensure_ascii=False)
    assert L.leaks(masked, L.BOOKING_PAYLOAD_GOLD) == []


def test_known_non_pii_fields_skip_text_detection():
    _, free_text = L.scan_json(L.BOOKING_PAYLOAD)
    assert [f["path"] for f in free_text] == ["notes"]


def test_labelled_lines_on_boarding_pass_text():
    text = "\n".join(L.BOARDING_PASS_LINES)
    assert set(texts(L.scan_labelled_lines(text))) == {v for v, _ in L.BOARDING_PASS_GOLD}


def test_labelled_lines_when_ocr_merges_lines():
    merged = "SYNTHETIC DATA Passenger: WHITFIELD/JANE MRS Frequent flyer: XY 4815162342"
    found = {(s["category"], s["text"]) for s in L.scan_labelled_lines(merged)}
    assert found == {("Person", "WHITFIELD/JANE MRS"), ("LoyaltyNumber", "XY 4815162342")}


def test_merge_keeps_longest_overlap():
    a = {"text": "Jane", "category": "Person", "offset": 0, "length": 4, "source": "azure-language"}
    b = {"text": "Jane Whitfield", "category": "Person", "offset": 0, "length": 14, "source": "field-rule"}
    assert L.merge_spans([a], [b]) == [b]


def test_rules_follow_the_documented_container_format():
    for rule in L.TRAVEL_RULES:
        assert re.fullmatch(r"CE_[A-Za-z0-9_]+", rule["name"])
        assert len({r["name"] for r in L.TRAVEL_RULES}) == len(L.TRAVEL_RULES)
        for pattern in rule["regexPatterns"]:
            assert {"id", "pattern", "matchScore", "locales"} <= set(pattern)
        for hint in rule["matchContext"]["hints"]:
            assert {"hintText", "boostingScore", "locales", "contextLimit"} <= set(hint)
    json.dumps(L.TRAVEL_RULES)   # serialisable as a rule file
