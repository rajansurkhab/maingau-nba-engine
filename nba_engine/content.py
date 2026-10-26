"""Content (slide 9 step 6): approved message templates per offer and tone.

Customer-facing text must be German (slide 12), so the sendable text is German. Every
template also carries an English translation for reviewers, because the project, the UI
and the pitch are English. Templates carry no prices and no
urgency (slide 12: no personalised prices, no fake urgency); `lint()` enforces this and
checks that the text matches its tone (Du vs. Sie). Marketing must approve a template
before any action using it can be approved (slide 10: approve content).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from nba_engine.channel_tone import tone_markers

# (subject, body) per offer and tone. Deliberately short: one template serves e-mail,
# newsletter teaser, pop-up, push, SMS and the call script opener.
TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "P-EN-01": {
        "sie": ("Ihr Strom von MAINGAU", "Guten Tag, Sie können Ihren Haushaltsstrom jetzt ebenfalls bei MAINGAU beziehen – flexibel und ohne lange Bindung. Alle Details finden Sie in Ihrem Kundenportal."),
        "du": ("Dein Strom von MAINGAU", "Hallo, du kannst deinen Haushaltsstrom jetzt auch bei MAINGAU beziehen – flexibel und ohne lange Bindung. Alle Details findest du in deinem Kundenportal."),
    },
    "P-EN-02": {
        "sie": ("Ihre Preisgarantie verlängern", "Guten Tag, Ihre aktuelle Preisgarantie endet in den nächsten Monaten. Mit StromGarant sichern Sie sich erneut 24 Monate Planungssicherheit. Die Konditionen sehen Sie in Ihrem Kundenportal."),
        "du": ("Deine Preisgarantie verlängern", "Hallo, deine aktuelle Preisgarantie endet in den nächsten Monaten. Mit StromGarant sicherst du dir erneut 24 Monate Planungssicherheit. Die Konditionen siehst du in deinem Kundenportal."),
    },
    "P-EN-03": {
        "sie": ("Strom für Ihre Wärmepumpe", "Guten Tag, mit StromWärme erhalten Sie einen eigenen Tarif für Ihre Wärmepumpe. Gern prüfen wir, ob er zu Ihrem Haushalt passt."),
        "du": ("Strom für deine Wärmepumpe", "Hallo, mit StromWärme bekommst du einen eigenen Tarif für deine Wärmepumpe. Gern prüfen wir, ob er zu deinem Haushalt passt."),
    },
    "P-EN-04": {
        "sie": ("Gas von MAINGAU", "Guten Tag, Sie können auch Ihr Gas bei MAINGAU beziehen und alles über ein Kundenkonto verwalten. Details finden Sie in Ihrem Kundenportal."),
        "du": ("Gas von MAINGAU", "Hallo, du kannst auch dein Gas bei MAINGAU beziehen und alles über ein Kundenkonto verwalten. Details findest du in deinem Kundenportal."),
    },
    "P-EN-05": {
        "sie": ("Dynamischer Stromtarif für Ihr Smart Meter", "Guten Tag, mit Ihrem Smart Meter können Sie den dynamischen Stromtarif nutzen und Verbrauch in günstige Stunden verlegen. Wie das funktioniert, erklären wir in Ihrem Kundenportal."),
        "du": ("Dynamischer Stromtarif für dein Smart Meter", "Hallo, mit deinem Smart Meter kannst du den dynamischen Stromtarif nutzen und Verbrauch in günstige Stunden verlegen. Wie das funktioniert, erklären wir in deinem Kundenportal."),
    },
    "P-MO-01": {
        "sie": ("Mobilfunk von MAINGAU", "Guten Tag, als MAINGAU-Kunde können Sie auch Ihren Mobilfunk bei uns abschließen – mit flexiblen Datentarifen. Die Tarife finden Sie in Ihrem Kundenportal."),
        "du": ("Mobilfunk von MAINGAU", "Hallo, als MAINGAU-Kunde kannst du auch deinen Mobilfunk bei uns abschließen – mit flexiblen Datentarifen. Die Tarife findest du in deinem Kundenportal."),
    },
    "P-MO-02": {
        "sie": ("Mehr Datenvolumen für Sie", "Guten Tag, Ihr Datenvolumen war zuletzt öfter aufgebraucht. Mit einem Upgrade auf den nächsten Tarif haben Sie mehr Spielraum. Sie können jederzeit im Portal wechseln."),
        "du": ("Mehr Datenvolumen für dich", "Hallo, dein Datenvolumen war zuletzt öfter aufgebraucht. Mit einem Upgrade auf den nächsten Tarif hast du mehr Spielraum. Du kannst jederzeit im Portal wechseln."),
    },
    "P-MO-03": {
        "sie": ("Eine Zusatz-SIM für Ihren Haushalt", "Guten Tag, mit einer Zusatz-SIM nutzen weitere Personen in Ihrem Haushalt Ihren Mobilfunkvertrag mit. Die Details finden Sie in Ihrem Kundenportal."),
        "du": ("Eine Zusatz-SIM für deinen Haushalt", "Hallo, mit einer Zusatz-SIM nutzen weitere Personen in deinem Haushalt deinen Mobilfunkvertrag mit. Die Details findest du in deinem Kundenportal."),
    },
    "P-IN-01": {
        "sie": ("Internet von MAINGAU", "Guten Tag, MAINGAU bietet auch Internet per DSL und Glasfaser an. Ob Ihre Adresse versorgt wird, prüfen Sie in Ihrem Kundenportal."),
        "du": ("Internet von MAINGAU", "Hallo, MAINGAU bietet auch Internet per DSL und Glasfaser an. Ob deine Adresse versorgt wird, prüfst du in deinem Kundenportal."),
    },
    "P-SL-01": {
        "sie": ("Ihr Zuhause energieschlau steuern", "Guten Tag, mit dem HEMS Starter-Set steuern Sie Photovoltaik, Wärmepumpe und Ladevorgänge an einem Ort. Wie das funktioniert, zeigen wir Ihnen im Kundenportal."),
        "du": ("Dein Zuhause energieschlau steuern", "Hallo, mit dem HEMS Starter-Set steuerst du Photovoltaik, Wärmepumpe und Ladevorgänge an einem Ort. Wie das funktioniert, zeigen wir dir im Kundenportal."),
    },
    "P-SL-02": {
        "sie": ("Mehr aus Ihrem Speicher holen", "Guten Tag, die Speicher-Optimierung für Ihr HEMS lädt und entlädt Ihren Batteriespeicher automatisch zu passenden Zeiten. Sie aktivieren sie in der App."),
        "du": ("Mehr aus deinem Speicher holen", "Hallo, die Speicher-Optimierung für dein HEMS lädt und entlädt deinen Batteriespeicher automatisch zu passenden Zeiten. Du aktivierst sie in der App."),
    },
    "P-SL-03": {
        "sie": ("Ihre Wallbox smart laden lassen", "Guten Tag, mit der Wallbox-Integration lädt Ihr Fahrzeug bevorzugt mit eigenem Solarstrom. Sie aktivieren sie in der App."),
        "du": ("Deine Wallbox smart laden lassen", "Hallo, mit der Wallbox-Integration lädt dein Fahrzeug bevorzugt mit eigenem Solarstrom. Du aktivierst sie in der App."),
    },
    "P-SH-01": {
        "sie": ("Solarstrom vom eigenen Balkon", "Guten Tag, mit einem Balkonkraftwerk erzeugen Sie einen Teil Ihres Stroms selbst. Alle Infos finden Sie in unserem Shop."),
        "du": ("Solarstrom vom eigenen Balkon", "Hallo, mit einem Balkonkraftwerk erzeugst du einen Teil deines Stroms selbst. Alle Infos findest du in unserem Shop."),
    },
    "P-SH-02": {
        "sie": ("Zu Hause laden mit eigener Wallbox", "Guten Tag, mit einer Wallbox laden Sie Ihr Elektroauto bequem zu Hause. Alle Infos finden Sie in unserem Shop."),
        "du": ("Zu Hause laden mit eigener Wallbox", "Hallo, mit einer Wallbox lädst du dein Elektroauto bequem zu Hause. Alle Infos findest du in unserem Shop."),
    },
    "P-SH-03": {
        "sie": ("Smarte Thermostate für Ihre Heizung", "Guten Tag, smarte Thermostate regeln Ihre Heizung nach Zeitplan und sparen Energie. Alle Infos finden Sie in unserem Shop."),
        "du": ("Smarte Thermostate für deine Heizung", "Hallo, smarte Thermostate regeln deine Heizung nach Zeitplan und sparen Energie. Alle Infos findest du in unserem Shop."),
    },
    "P-SH-04": {
        "sie": ("Stromverbrauch sichtbar machen", "Guten Tag, mit smarten Steckdosen sehen Sie, welches Gerät wie viel Strom verbraucht. Alle Infos finden Sie in unserem Shop."),
        "du": ("Stromverbrauch sichtbar machen", "Hallo, mit smarten Steckdosen siehst du, welches Gerät wie viel Strom verbraucht. Alle Infos findest du in unserem Shop."),
    },
    "P-AS-01": {
        "sie": ("Unterwegs laden mit MAINGAU Autostrom", "Guten Tag, mit MAINGAU Autostrom laden Sie Ihr Elektroauto an vielen öffentlichen Ladepunkten in Europa – per App oder Ladekarte. Details finden Sie in Ihrem Kundenportal."),
        "du": ("Unterwegs laden mit MAINGAU Autostrom", "Hallo, mit MAINGAU Autostrom lädst du dein Elektroauto an vielen öffentlichen Ladepunkten in Europa – per App oder Ladekarte. Details findest du in deinem Kundenportal."),
    },
    "P-SV-01": {
        "sie": ("Wir kümmern uns um Ihr Anliegen", "Guten Tag, wir haben gesehen, dass bei Ihnen zuletzt etwas nicht rund lief. Wir haben uns Ihr Anliegen angesehen und melden uns mit einer Lösung bei Ihnen."),
        "du": ("Wir kümmern uns um dein Anliegen", "Hallo, wir haben gesehen, dass bei dir zuletzt etwas nicht rund lief. Wir haben uns dein Anliegen angesehen und melden uns mit einer Lösung bei dir."),
    },
}

# English translation per offer, for reviewers and the jury (the project language is English).
# Only the German text above is sent to customers; English has no Du/Sie distinction.
TRANSLATIONS_EN: dict[str, tuple[str, str]] = {
    "P-EN-01": ("Your electricity from MAINGAU", "You can now also get your household electricity from MAINGAU – flexible and without a long commitment. All details are in your customer portal."),
    "P-EN-02": ("Extend your price guarantee", "Your current price guarantee ends in the coming months. With StromGarant you secure another 24 months of predictable prices. You can see the terms in your customer portal."),
    "P-EN-03": ("Electricity for your heat pump", "With StromWärme you get a dedicated tariff for your heat pump. We are happy to check whether it suits your household."),
    "P-EN-04": ("Gas from MAINGAU", "You can also get your gas from MAINGAU and manage everything in one customer account. Details are in your customer portal."),
    "P-EN-05": ("Dynamic electricity tariff for your smart meter", "With your smart meter you can use the dynamic electricity tariff and shift consumption to cheaper hours. We explain how it works in your customer portal."),
    "P-MO-01": ("Mobile from MAINGAU", "As a MAINGAU customer you can also get your mobile plan from us – with flexible data plans. The plans are in your customer portal."),
    "P-MO-02": ("More data for you", "Your data volume ran out several times recently. Upgrading to the next plan gives you more room. You can switch any time in the portal."),
    "P-MO-03": ("An extra SIM for your household", "With an extra SIM, other people in your household can share your mobile contract. Details are in your customer portal."),
    "P-IN-01": ("Internet from MAINGAU", "MAINGAU also offers internet via DSL and fibre. You can check in your customer portal whether your address is covered."),
    "P-SL-01": ("Run your home energy-smart", "With the HEMS starter set you control solar, heat pump and charging in one place. We show you how it works in the customer portal."),
    "P-SL-02": ("Get more out of your battery", "Battery optimisation for your HEMS charges and discharges your home battery automatically at the right times. You activate it in the app."),
    "P-SL-03": ("Let your wallbox charge smartly", "With the wallbox integration your car charges preferably with your own solar power. You activate it in the app."),
    "P-SH-01": ("Solar power from your own balcony", "With a balcony solar kit you produce part of your electricity yourself. All information is in our shop."),
    "P-SH-02": ("Charge at home with your own wallbox", "With a wallbox you charge your electric car conveniently at home. All information is in our shop."),
    "P-SH-03": ("Smart thermostats for your heating", "Smart thermostats control your heating on a schedule and save energy. All information is in our shop."),
    "P-SH-04": ("See where your electricity goes", "With smart plugs you see which device uses how much electricity. All information is in our shop."),
    "P-AS-01": ("Charge on the road with MAINGAU Autostrom", "With MAINGAU Autostrom you charge your electric car at many public charge points across Europe – via app or charging card. Details are in your customer portal."),
    "P-SV-01": ("We are taking care of your request", "We noticed that something did not go smoothly for you recently. We have looked into your request and will get back to you with a solution."),
}

_PRICE = re.compile(r"(\d+([.,]\d+)?\s*(€|EUR|Euro|Cent|ct)\b|€|\d+\s*%|rabatt|günstiger als|gratis|kostenlos)", re.IGNORECASE)
_URGENCY = re.compile(
    r"(nur heute|nur noch|sofort|jetzt oder nie|letzte chance|schnell sein|solange der vorrat|"
    r"begrenzt|endet heute|läuft heute ab|nicht verpassen|!!)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Template:
    offer_id: str
    tone: str
    subject: str
    body: str

    @property
    def key(self) -> str:
        return f"{self.offer_id}:{self.tone}"

    @property
    def subject_en(self) -> str:
        return TRANSLATIONS_EN[self.offer_id][0]

    @property
    def body_en(self) -> str:
        return TRANSLATIONS_EN[self.offer_id][1]


def lint(subject: str, body: str, tone: str) -> list[str]:
    """Issues that block approval: prices, urgency, or a tone mismatch (slide 12)."""
    text = f"{subject} {body}"
    issues = []
    if m := _PRICE.search(text):
        issues.append(f"mentions a price or discount: '{m.group(0)}'")
    if m := _URGENCY.search(text):
        issues.append(f"artificial urgency: '{m.group(0)}'")
    formal, informal = tone_markers(text)
    if tone == "sie" and informal:
        issues.append("informal (Du) wording in a Sie template")
    if tone == "du" and formal > 1:  # a single capitalised word at sentence start is tolerated
        issues.append("formal (Sie) wording in a Du template")
    return issues


def all_templates() -> list[Template]:
    return [Template(o, t, s, b) for o, tones in TEMPLATES.items() for t, (s, b) in tones.items()]


def template_for(offer_id: str, tone: str) -> Template:
    subject, body = TEMPLATES[offer_id][tone]
    return Template(offer_id, tone, subject, body)
