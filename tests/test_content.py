"""Content templates: German customer text, English translation, no prices, no urgency."""
import pytest

from nba_engine.content import TEMPLATES, TRANSLATIONS_EN, all_templates, lint


def test_every_catalog_offer_has_both_tones_and_a_translation(ds):
    for offer_id in ds.product_catalog["product_id"]:
        assert set(TEMPLATES[offer_id]) == {"du", "sie"}, offer_id
        assert offer_id in TRANSLATIONS_EN, offer_id


def test_all_shipped_templates_are_clean():
    for t in all_templates():
        assert lint(t.subject, t.body, t.tone) == [], t.key


@pytest.mark.parametrize(
    "subject, body, tone, issue",
    [
        ("Angebot", "Guten Tag, nur 9,99 € im Monat für Sie.", "sie", "price"),
        ("Angebot", "Guten Tag, Sie sparen 20 % bei uns.", "sie", "price"),
        ("Angebot", "Guten Tag, nur heute für Sie verfügbar.", "sie", "urgency"),
        ("Letzte Chance", "Guten Tag, sichern Sie sich das Angebot.", "sie", "urgency"),
        ("Angebot", "Guten Tag, hast du schon gehört?", "sie", "Du"),
        ("Angebot", "Hallo, haben Sie Ihre Unterlagen? Ihnen gefällt es.", "du", "Sie"),
    ],
)
def test_lint_catches_forbidden_patterns(subject, body, tone, issue):
    issues = lint(subject, body, tone)
    assert any(issue in i for i in issues), issues
