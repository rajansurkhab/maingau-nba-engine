"""Du/Sie detection and channel affinity."""
import pytest

from nba_engine.channel_tone import AffinityModel, ToneDetector, customer_turns, tone_markers
from nba_engine.config import EngineConfig
from nba_engine.state import CustomerState


@pytest.fixture(scope="module")
def tones(ds):
    return ToneDetector(ds, EngineConfig())


@pytest.fixture(scope="module")
def affinity(ds):
    return AffinityModel(ds, EngineConfig())


@pytest.mark.parametrize(
    "text, tone",
    [
        ("Sehr geehrte Damen und Herren, bitte bestätigen Sie den Eingang. Mit freundlichen Grüßen", "sie"),
        ("Guten Tag, bitte teilen Sie mir mit, warum mein Abschlag erhöht wurde.", "sie"),
        ("Hi, echt nervig, ich warte seit zwei Wochen. Könnt ihr euch bitte melden?", "du"),
        ("Hey, hab den Zählerstand in der App eingetragen. Passt das so? LG", "du"),
        ("Super, danke dir, tschüss!", "du"),
    ],
)
def test_tone_markers(text, tone):
    formal, informal = tone_markers(text)
    assert ("du" if informal > formal else "sie") == tone


def test_agent_turns_are_ignored():
    transcript = "Agent: Wie kann ich Ihnen helfen?\nKunde: Hi, danke dir!\nAgent: Gerne, auf Wiederhören."
    assert customer_turns(transcript) == "Hi, danke dir!"
    formal, informal = tone_markers(customer_turns(transcript))
    assert formal == 0 and informal >= 2


def test_default_is_sie_without_texts(tones):
    s = CustomerState("MG-NOTEXT", consents={"profiling_analytics": "granted"})
    result = tones.detect(s)
    assert result.tone == "sie" and not result.used


def test_detects_both_tones_on_real_data(tones, states):
    detected = {tones.detect(s).tone for s in states.values() if s.profiling_allowed}
    assert detected == {"du", "sie"}


def test_tone_falls_back_to_sie_without_profiling(tones, states):
    informal_no_consent = [
        s for s in states.values()
        if not s.profiling_allowed
        and tones.detect(CustomerState(s.customer_id, consents={"profiling_analytics": "granted"})).tone == "du"
    ]
    assert informal_no_consent, "data should contain informal writers without profiling consent"
    for s in informal_no_consent:
        result = tones.detect(s)
        assert result.tone == "sie" and not result.used


def test_mpp_opens_do_not_count_as_engagement(ds, affinity, states):
    nl = ds.newsletter_performance
    mpp_only = (
        nl.groupby("customer_id")
        .apply(lambda g: g["open_client_class"].eq("apple_mpp_prefetch").any() and not g["human_open"].any(),
               include_groups=False)
    )
    cid = next(c for c in mpp_only[mpp_only].index if states[c].profiling_allowed)
    aff = affinity.affinity(states[cid])
    assert aff.evidence["newsletter_email"].startswith("0/")
    assert aff.factor("newsletter_email") < 1


def test_affinity_neutral_without_profiling(affinity, states):
    s = next(s for s in states.values() if not s.profiling_allowed and s.portal_account)
    aff = affinity.affinity(s)
    assert not aff.used
    assert all(aff.factor(ch) == 1.0 for ch in ("newsletter_email", "portal_popup", "app_push", "sms"))


def test_affinity_within_bounds(affinity, states):
    lo, hi = EngineConfig().affinity_bounds
    for s in states.values():
        for f in affinity.affinity(s).factors.values():
            assert lo <= f <= hi
