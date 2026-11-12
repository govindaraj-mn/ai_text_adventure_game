"""Static content for Escape Silk Board.

Everything here is deliberately authored rather than model-generated. The
narrator may decorate an outcome, but the route, weather and rules are fixed so
the game stays fair and reproducible.
"""

from __future__ import annotations

from typing import TypedDict


class Checkpoint(TypedDict):
    name: str
    km: int
    hook: str
    sting: str
    held: str


CHECKPOINTS: list[Checkpoint] = [
    {
        "name": "Silk Board Junction",
        "km": 0,
        "hook": (
            "The Silk Board auto driver wants ₹650. You have ₹500 cash, "
            "4% battery, and 1 cold samosa."
        ),
        "sting": (
            "Behind you, Silk Board's great knot of lanes continues its "
            "argument without you."
        ),
        "held": "The junction resets its countdown. Nobody moves.",
    },
    {
        "name": "Agara Lake",
        "km": 4,
        "hook": (
            "Agara Lake glows like a bad screensaver and the service road has "
            "become a river with opinions. A pillion rider idles at the corner."
        ),
        "sting": (
            "Agara Lake glows like a bad screensaver; the service road has "
            "become a river with opinions."
        ),
        "held": "The Agara signal turns green. Nobody has told the cars.",
    },
    {
        "name": "Bellandur",
        "km": 8,
        "hook": (
            "Bellandur's potholes have developed a taste for hubcaps. The ORR "
            "is one long red ribbon of brake lights."
        ),
        "sting": (
            "Bellandur smells of wet cement and ambition; the potholes are "
            "refilling with rain."
        ),
        "held": (
            "A pothole swallows a scooter's front wheel. The rider laughs, "
            "then does not."
        ),
    },
    {
        "name": "Kadubeesanahalli",
        "km": 12,
        "hook": (
            "Every tech-park gate is exhaling another battalion of cabs into "
            "one service lane. Everyone is leaving at the same time."
        ),
        "sting": (
            "Every tech-park gate exhales a fresh battalion of cabs, and they "
            "all want your lane."
        ),
        "held": (
            "A cab door opens into your path. Then another. Then a delivery "
            "rider going the wrong way."
        ),
    },
    {
        "name": "Marathahalli Bridge",
        "km": 16,
        "hook": (
            "Marathahalli Bridge is a museum of stalled ambition. Somewhere "
            "below, a metro pillar waits for a train that is still a rumour."
        ),
        "sting": (
            "The bridge is a necklace of red brake lights, and you are one of "
            "its beads."
        ),
        "held": (
            "The flyover remains a museum of stalled ambition. The gift shop "
            "is closed."
        ),
    },
    {
        "name": "Tin Factory (KR Puram)",
        "km": 23,
        "hook": (
            "The old tin sheds are gone, but the traffic has squatter's "
            "rights. A Vayu Vajra airport bus idles near the depot."
        ),
        "sting": (
            "The old tin sheds are gone; the traffic stayed and claimed "
            "squatter's rights."
        ),
        "held": (
            "A truck with SPEED painted on the back blocks all three lanes, "
            "moving at the speed of regret."
        ),
    },
    {
        "name": "Hebbal Flyover",
        "km": 32,
        "hook": (
            "Hebbal Flyover offers a beautiful view of the next jam. The "
            "airport signs have started to appear."
        ),
        "sting": (
            "Hebbal Flyover gives you a panoramic view of people going "
            "nowhere."
        ),
        "held": (
            "Hebbal's flyover delivers a beautiful view of the next jam. You "
            "are in it."
        ),
    },
    {
        "name": "Yelahanka",
        "km": 38,
        "hook": (
            "The road opens up. Your hope becomes suspicious. An airport "
            "shuttle is picking up crew near the shoulder."
        ),
        "sting": "The road opens up, and your hope becomes suspicious.",
        "held": "The airport signs are close enough to hurt. So is the clock.",
    },
    {
        "name": "Kempegowda Intl (BLR)",
        "km": 45,
        "hook": (
            "Departures. Glass doors. A guard who has seen worse. The "
            "check-in counter is somewhere inside."
        ),
        "sting": (
            "The departures ramp glows in the rain, a cathedral of glass and "
            "overpriced coffee."
        ),
        "held": "You are at the airport, but the clock has other plans.",
    },
]


def weather_for(turn: int) -> tuple[str, bool]:
    """Return (label, is_raining) for the given turn (1-based)."""

    if turn <= 2:
        return "Humid haze", False
    if turn <= 5:
        return "Heavy ORR rain", True
    if turn <= 7:
        return "Drizzle & potholes", True
    return "Clearing skies", False
