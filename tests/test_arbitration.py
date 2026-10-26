"""Arbitration on synthetic scored options."""
from nba_engine.arbitration import arbitrate, make_option
from nba_engine.config import EngineConfig
from nba_engine.eligibility import Offer
from nba_engine.scoring import Score

MOBILE = Offer("P-MO-01", "Mobil", "sales")
INTERNET = Offer("P-IN-01", "Internet", "sales")
SERVICE = Offer("P-SV-01", "Service", "service")


def opt(cid, offer, channel, dclv, cfg):
    s = Score(cid, offer.offer_id, channel, "sie", 0.1, 100.0, 100.0, 100.0, 0.0, dclv, False)
    return make_option(offer, s, cfg)


def test_negative_and_zero_options_are_dropped():
    cfg = EngineConfig(channel_capacity={})
    res = arbitrate({"A": [opt("A", MOBILE, "email", -5, cfg), opt("A", INTERNET, "email", 0.0, cfg)]}, cfg)
    assert res.chosen["A"] is None
    assert res.ranked["A"] == []


def test_highest_dclv_wins_and_ranked_is_top_k_per_offer():
    cfg = EngineConfig(channel_capacity={}, top_k=2)
    options = [
        opt("A", MOBILE, "email", 10, cfg),
        opt("A", MOBILE, "sms", 12, cfg),
        opt("A", INTERNET, "email", 8, cfg),
        opt("A", SERVICE, "portal_popup", 3, cfg),
    ]
    res = arbitrate({"A": options}, cfg)
    assert (res.chosen["A"].offer.offer_id, res.chosen["A"].channel) == ("P-MO-01", "sms")
    assert [o.offer.offer_id for o in res.ranked["A"]] == ["P-MO-01", "P-IN-01"]


def test_priority_reorders_but_never_makes_negative_positive():
    cfg = EngineConfig(channel_capacity={}, offer_priority={"P-IN-01": 2.0, "P-SV-01": 100.0})
    options = [opt("A", MOBILE, "email", 10, cfg), opt("A", INTERNET, "email", 8, cfg), opt("A", SERVICE, "email", -1, cfg)]
    res = arbitrate({"A": options}, cfg)
    assert res.chosen["A"].offer.offer_id == "P-IN-01"
    assert all(o.offer.offer_id != "P-SV-01" for o in res.ranked["A"])


def test_disabled_offer_is_skipped_and_noted():
    cfg = EngineConfig(channel_capacity={}, disabled_offers={"P-MO-01"})
    res = arbitrate({"A": [opt("A", MOBILE, "email", 10, cfg), opt("A", INTERNET, "email", 8, cfg)]}, cfg)
    assert res.chosen["A"].offer.offer_id == "P-IN-01"
    assert "P-MO-01 disabled by marketing" in res.notes["A"]


def test_channel_capacity_goes_to_highest_value_and_others_fall_back():
    cfg = EngineConfig(channel_capacity={"outbound_call": 1})
    options = {
        "A": [opt("A", MOBILE, "outbound_call", 50, cfg), opt("A", MOBILE, "email", 5, cfg)],
        "B": [opt("B", MOBILE, "outbound_call", 40, cfg), opt("B", MOBILE, "email", 4, cfg)],
    }
    res = arbitrate(options, cfg)
    assert res.chosen["A"].channel == "outbound_call"
    assert res.chosen["B"].channel == "email"
    assert "outbound_call capacity reached" in res.notes["B"]


def test_at_most_one_action_per_customer():
    cfg = EngineConfig(channel_capacity={})
    options = {"A": [opt("A", MOBILE, "email", 10, cfg), opt("A", INTERNET, "sms", 9, cfg)]}
    res = arbitrate(options, cfg)
    assert len([c for c in res.chosen.values() if c]) == 1
