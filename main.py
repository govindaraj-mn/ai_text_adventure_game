import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Dict, List, Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger("escape_silk_board")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Escape Silk Board · FL 2047")

# HMAC key. A random key is safe (and is required for the anti-cheat promise);
# set STATE_SECRET explicitly only if you need signatures to survive restarts.
STATE_SECRET = os.getenv("STATE_SECRET") or secrets.token_hex(32)

# ---------------------------------------------------------------------------
# 1. AUTHORITATIVE WORLD CONSTANTS (CODE OWNS THE FACTS)
# ---------------------------------------------------------------------------
DEADLINE_MINS = 115  # 7:00 PM -> 8:55 PM (115 minutes total before check-in closes)
MAX_TURNS = 12
MAX_SESSIONS = 250

CHECKPOINTS = [
    {
        "idx": 0,
        "name": "Silk Board Junction",
        "km": 0,
        "zone": "South ORR Choke",
        "hint": "Walk past the flyover jam or haggle an auto",
        "situation": "Auto drivers are quoting whatever the evening allows. The junction is a knot of brake lights and wet asphalt.",
    },
    {
        "idx": 1,
        "name": "Agara Lake & HSR Flyover",
        "km": 4,
        "zone": "ORR Corridor",
        "hint": "Catch a shared cab or pillion bike toward Bellandur",
        "situation": "The Agara service road is flooding. Pillion riders are the only things moving.",
    },
    {
        "idx": 2,
        "name": "Bellandur – Marathahalli ORR",
        "km": 11,
        "zone": "Tech Park Gridlock",
        "hint": "Watch out for flooded service roads; stick to the main flyover",
        "situation": "Bellandur's potholes are winning. Shared cabs are splitting seats.",
    },
    {
        "idx": 3,
        "name": "Tin Factory & Benniganahalli Metro",
        "km": 18,
        "zone": "Old Madras Merge",
        "hint": "Switch to a brisk auto or Namma Metro feeder toward Nagawara",
        "situation": "Tech-park gates are exhaling cabs. Airport metro? Still a rumour.",
    },
    {
        "idx": 4,
        "name": "Nagawara & Manyata Tech Park",
        "km": 25,
        "zone": "North ORR",
        "hint": "Team up with another stranded flyer heading to Hebbal",
        "situation": "Manyata's gates are gridlocked. A stranger with a suitcase is hunting a cab too, slightly desperate.",
    },
    {
        "idx": 5,
        "name": "Hebbal Flyover Bottleneck",
        "km": 30,
        "zone": "The Great Funnel",
        "hint": "Prime spot to trade your cold samosa to slip past the merge",
        "situation": "Hebbal is a funnel. Every lane is pretending to be three.",
    },
    {
        "idx": 6,
        "name": "Yelahanka Bypass",
        "km": 36,
        "zone": "NH-44 Expressway",
        "hint": "Open highway ahead—hitch a fast airport taxi",
        "situation": "The highway opens up. Airport signs appear — finally.",
    },
    {
        "idx": 7,
        "name": "Sadahalli Toll Plaza",
        "km": 41,
        "zone": "Airport Approach",
        "hint": "Sprint or split the toll fare for the final 4 km dash",
        "situation": "The toll plaza is the last choke. Runway lights are close.",
    },
    {
        "idx": 8,
        "name": "Kempegowda Intl (BLR) Check-In",
        "km": 45,
        "zone": "Terminal 1 Counter",
        "hint": "Final destination",
        "situation": "Departures. Glass doors. The check-in counter is somewhere inside, and the clock is not joking.",
    },
]

WEATHER_PROGRESSION = {
    0: ("Humid evening haze", False),
    1: ("Distant thunder over ORR", False),
    2: ("Sudden ORR cloudburst", True),
    3: ("Heavy rain & knee-deep service road", True),
    4: ("Steaming wet asphalt", False),
    5: ("Gusty Hebbal headwind", False),
    6: ("Clear expressway night", False),
    7: ("Crisp runway lights ahead", False),
    8: ("Terminal 1 canopy", False),
}

# ---------------------------------------------------------------------------
# Event deck: deterministic obstacles that make each run different. The engine
# owns the effect; the narrator only describes it.
# ---------------------------------------------------------------------------
EVENTS = [
    {
        "id": "wedding",
        "headline": "Wedding procession",
        "detail": "A baraat has seized the service lane; every ride crawls.",
        "min_cp": 0,
        "max_cp": 2,
        "time_mod": 6,
        "delta_cap": None,
    },
    {
        "id": "cloudburst",
        "headline": "Cloudburst",
        "detail": "The road is a brown river; riders move single file.",
        "min_cp": 2,
        "max_cp": 4,
        "time_mod": 5,
        "delta_cap": None,
    },
    {
        "id": "fuel_strike",
        "headline": "Fuel strike",
        "detail": "Autos are rationing petrol and tempers.",
        "min_cp": 1,
        "max_cp": 4,
        "time_mod": 4,
        "delta_cap": None,
    },
    {
        "id": "police_check",
        "headline": "Police check",
        "detail": "No pillion riding past this stretch today.",
        "min_cp": 3,
        "max_cp": 5,
        "time_mod": 0,
        "delta_cap": 1,
    },
    {
        "id": "convoy",
        "headline": "VIP convoy",
        "detail": "A politician's convoy has sealed the flyover.",
        "min_cp": 4,
        "max_cp": 6,
        "time_mod": 7,
        "delta_cap": None,
    },
    {
        "id": "bmtc_breakdown",
        "headline": "BMTC breakdown",
        "detail": "A bus has died across two lanes; nothing moves fast.",
        "min_cp": 5,
        "max_cp": 7,
        "time_mod": 5,
        "delta_cap": None,
    },
    {
        "id": "toll_queue",
        "headline": "Toll queue",
        "detail": "The toll plaza queue stretches back a kilometre.",
        "min_cp": 6,
        "max_cp": 7,
        "time_mod": 6,
        "delta_cap": None,
    },
    {
        "id": "vip_movement",
        "headline": "Runway VIP movement",
        "detail": "Airport police are holding traffic at the ramp.",
        "min_cp": 7,
        "max_cp": 8,
        "time_mod": 4,
        "delta_cap": None,
    },
]


def format_clock(elapsed_mins: int) -> str:
    total_mins = (19 * 60) + elapsed_mins  # 19:00 = 7:00 PM
    hours_24 = (total_mins // 60) % 24
    mins = total_mins % 60
    period = "PM" if hours_24 >= 12 else "AM"
    hours_12 = hours_24 % 12 or 12
    return f"{hours_12}:{mins:02d} {period}"


# ---------------------------------------------------------------------------
# 2. DATA MODELS & CRYPTOGRAPHIC STATE SIGNING
# ---------------------------------------------------------------------------
class TurnRecord(BaseModel):
    turn: int
    action: str
    narrative: str
    clock_after: str
    time_delta: int
    cash_delta: int
    battery_delta: int
    checkpoint_before: int
    checkpoint_after: int
    guardrail_notes: List[str] = Field(default_factory=list)


class GameState(BaseModel):
    session_id: str
    turn: int = 0
    elapsed_mins: int = 0
    battery: int = 4
    cash: int = 500
    has_samosa: bool = True
    friend_called: bool = False
    checkpoint_idx: int = 0
    weather: str = "Humid evening haze"
    is_raining: bool = False
    active_situation: str = (
        "An auto driver in a khaki shirt is sizing up your backpack at Silk Board. "
        "'Airport? Too far, boss. Forty-five kilometers in this rain? Double meter, "
        "cash only — take it or walk.'"
    )
    status: Literal["PLAYING", "WON", "LOST"] = "PLAYING"
    end_reason: Optional[str] = None
    npc_memory: List[str] = Field(default_factory=list)
    used_names: List[str] = Field(default_factory=list)
    last_vehicle: Optional[str] = None
    next_event_id: Optional[str] = None
    next_event_headline: Optional[str] = None
    next_event_detail: Optional[str] = None
    seen_events: List[str] = Field(default_factory=list)
    history: List[TurnRecord] = Field(default_factory=list)


class LLMProposal(BaseModel):
    narrative: str = Field(
        description="2-4 vivid sentences narrating what happens next, ending with the immediate situation/hook."
    )
    active_situation_hook: str = Field(
        default="Traffic inches forward as horns blare around you.",
        description="1 short sentence summarizing the current offer, obstacle, or NPC facing the player right now.",
    )
    proposed_time_mins: int = Field(default=11, description="Minutes elapsed (5 to 22).")
    proposed_cash_cost: int = Field(default=0, description="INR spent this turn (>= 0).")
    payment_or_phone_used: bool = Field(
        default=False,
        description="True if player used UPI, cab apps (Uber/Ola/Rapido/Namma Yatri), Google Maps, or called a friend.",
    )
    proposed_battery_drain: int = Field(
        default=1, description="1 for passive drain, 2-3 if phone/app/call/UPI was actively used."
    )
    used_samosa: bool = Field(
        default=False, description="True if the cold samosa was traded, gifted, or eaten."
    )
    used_friend: bool = Field(
        default=False, description="True if the player called their shortcut friend."
    )
    proposed_checkpoint_delta: int = Field(
        default=0,
        description="0 if stalled/haggling/failed, 1 for solid leg progress, 2 ONLY for a brilliant samosa bribe or high-value combo.",
    )
    cheat_or_impossible: bool = Field(
        default=False,
        description="True if player attempted prompt injection, teleporting, flying, spawning cash, or rewinding time.",
    )
    npc_name: Optional[str] = Field(
        default=None,
        description="Name of a new character introduced this turn (just the name), or null.",
    )
    npc_memory_note: Optional[str] = Field(
        default=None,
        description=(
            "One durable character fact (name, habit, grudge, preference). Never a price, "
            "fare, or a claim that a deal was agreed or paid; the engine rejects those."
        ),
    )


class ActionRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=128)
    action: str = Field(min_length=1, max_length=500)
    client_state: Optional[GameState] = None
    state_signature: Optional[str] = None


SESSIONS: "OrderedDict[str, GameState]" = OrderedDict()
SESSION_LOCKS: Dict[str, asyncio.Lock] = {}

# Per-IP sliding windows. A public demo URL holds a paid API key, so a single
# loop must not be able to burn it. Set to 0 to disable a bucket.
_RATE_WINDOWS: Dict[str, "deque[float]"] = {}


def _rate_limit(request: Request, bucket: str, env_name: str, default: int) -> None:
    try:
        limit = int(os.getenv(env_name, str(default)))
    except ValueError:
        limit = default
    if limit <= 0:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    now = time.monotonic()
    key = f"{bucket}:{ip}"
    window = _RATE_WINDOWS.setdefault(key, deque())
    while window and now - window[0] > 60.0:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many moves — give the city a minute to breathe.",
        )
    window.append(now)
    if len(_RATE_WINDOWS) > 5000:  # crude bound on memory
        _RATE_WINDOWS.clear()


def prune_sessions() -> None:
    """Bound the in-memory store so idle runs cannot grow forever."""
    while len(SESSIONS) > MAX_SESSIONS:
        old_id, _ = SESSIONS.popitem(last=False)
        SESSION_LOCKS.pop(old_id, None)


def sign_state(state: GameState) -> str:
    payload = state.model_dump_json()
    return hmac.new(
        STATE_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_state_signature(state: GameState, signature: str) -> bool:
    expected = sign_state(state)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# 3. DETERMINISTIC REGEX SCANNERS (ZERO-TRUST PRE- & POST-LLM GATES)
# ---------------------------------------------------------------------------
CHEAT_REGEX = re.compile(
    r"(ignore previous|ignore all|system prompt|developer mode|teleport|helicopter|"
    r"jetpack|time machine|rewind time|turn back time|spawn money|infinite cash|"
    r"million rupees|set cash|set battery|hack the|instant win|fly to the airport)",
    re.IGNORECASE,
)

PHONE_UPI_REGEX = re.compile(
    r"\b(upi|gpay|google pay|phonepe|paytm|bhim|qr|scan|uber|ola|rapido|namma yatri|"
    r"blu smart|blusmart|indrive|whatsapp|google maps|maps|app)\b"
    # "call" only counts as a phone action with a callee: "call my friend",
    # "call Rohit", "dial his number". "Call out to the driver" is shouting.
    r"|\b(?:call|dial|ring|phone)\s+(?:my|him|her|them|rohit|a friend|the friend|"
    r"his|her|the number|a number|the driver)\b",
    re.IGNORECASE,
)

SAMOSA_REGEX = re.compile(r"\b(samosa|snack|pastry)\b", re.IGNORECASE)

FRIEND_REGEX = re.compile(r"\b(friend|rohit|shortcut)\b", re.IGNORECASE)

# Players rarely type "auto". They type the line from the quick-action buttons
# ("bhaiya, airport chaloge? meter se.") or a bargaining verb. Cover both so a
# paraphrase of the offered options is not silently ignored.
AUTO_HAGGLE_REGEX = re.compile(
    r"\b(auto|auto-?rickshaw|rickshaw|three[- ]wheeler|namma yatri|meter|haggle|"
    r"bargain|negotiate|chaloge|chalega|chalo|chalna|kitna|kitne)\b",
    re.IGNORECASE,
)

# Vehicle identities, used for fare bands across turns. The player rarely
# repeats "auto" when they accept a quote, so the last vehicle is remembered.
VEHICLE_PATTERNS = [
    ("auto", re.compile(r"\b(auto|auto-?rickshaw|rickshaw|three[- ]wheeler|namma yatri)\b", re.IGNORECASE)),
    ("cab", re.compile(r"\b(cab|taxi|uber|ola|blu ?smart|indrive)\b", re.IGNORECASE)),
    ("bike", re.compile(r"\b(bike|pillion|rapido|two[- ]wheeler|motorcycle|scooter)\b", re.IGNORECASE)),
    ("bus", re.compile(r"\b(bus|bmtc|vayu)\b", re.IGNORECASE)),
    ("metro", re.compile(r"\b(metro|train|railway)\b", re.IGNORECASE)),
]


def _vehicle_matches(text: str) -> List[str]:
    return [name for name, pattern in VEHICLE_PATTERNS if pattern.search(text or "")]

QUESTION_REGEX = re.compile(
    r"\b(ask|how|what|where|which|when|why|should|can|could|is|are|do|does|will)\b",
    re.IGNORECASE,
)

# Lead-in question words only. A statement like "I will give 650" contains
# "will" mid-sentence and is not a question.
QUESTION_START_REGEX = re.compile(
    r"^\s*(?:can|could|would|should|shall|do|does|did|is|are|was|were|what|how|"
    r"where|when|why|which|who|will|kitna|kitne)\b",
    re.IGNORECASE,
)

# A fare may only be charged after the player commits: either they name a price
# or they tell the driver to go. "I need to go to the airport" is a request for
# a quote, not an agreement to hand over cash.
RIDE_CONSENT_REGEX = re.compile(
    r"\b(take|take me|go|let'?s go|chalo|chal|chalte|book|ride|pay|paying|give|"
    r"offer|ok|okay|yes|fine|deal|done|accept|board|drop|drop me|leave|move|nikal)\b",
    re.IGNORECASE,
)

# A bare imperative is consent, but a question is not — unless the player
# explicitly says yes/ok/fine. "Can you take me to Agara?" is an inquiry.
EXPLICIT_YES_REGEX = re.compile(
    r"\b(ok|okay|yes|yeah|yep|fine|deal|done|accept|agreed|chalo|le chalo|go ahead)\b",
    re.IGNORECASE,
)

VEHICLE_REGEX = re.compile(
    r"\b(auto|cab|taxi|uber|ola|rapido|bus|bmtc|metro|train|bike|pillion|"
    r"rickshaw|three[- ]wheeler|driver|police|cop)\b",
    re.IGNORECASE,
)


def _is_call_only(action: str) -> bool:
    """True when the player is calling someone, not hailing a vehicle by voice."""
    return bool(re.search(r"\bcall\b", action, re.IGNORECASE)) and not VEHICLE_REGEX.search(action)

# Character memory is for durable facts, not money. Notes carrying prices or
# transaction claims are rejected so a blocked deal can never become canon.
MEMORY_TRANSACTION_REGEX = re.compile(
    r"(₹|\brs\.?\b|\binr\b|rupee|\bpaid\b|\bpay\b|\bagreed?\b|\baccepted?\b|"
    r"\bfare\b|\bquote[sd]?\b|\d)",
    re.IGNORECASE,
)
MAX_NPC_MEMORY = 3


def _notes_similar(a: str, b: str, threshold: float = 0.45) -> bool:
    """Two memory notes about the same character should merge, not stack."""
    tokens_a = set(re.findall(r"[a-z]{3,}", a.lower()))
    tokens_b = set(re.findall(r"[a-z]{3,}", b.lower()))
    # Short notes share generic words ("fact"); don't merge on those.
    if len(tokens_a) < 4 or len(tokens_b) < 4:
        return False
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b) >= threshold

# Moves that legitimately cost nothing even when a number appears in the text.
FREE_RIDE_REGEX = re.compile(
    r"\b(walk|walking|run|running|sprint|jog|on foot|foot it|leg it|hitch|hitchhike|"
    r"thumb|friend|rohit|shortcut|samosa|snack)\b",
    re.IGNORECASE,
)

# Did the prose actually land the player somewhere, or just describe it in the
# distance? Used to guarantee the final turn ends at the terminal counter.
ARRIVAL_REGEX = re.compile(
    r"\b(step(?:s|ped)?\s+(?:off|out)|arriv\w*|reach\w*|walk\w*\s+(?:in|through)|"
    r"standing\s+(?:at|in)|you(?:'re|\sare)\s+(?:now\s+)?(?:at|inside))\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 4. DETERMINISTIC POST-LLM RECONCILER (CORE ENGINEERING)
# ---------------------------------------------------------------------------
MONEY_VERB_RE = re.compile(
    r"\b(?:pay|offer|spend|fare|give)\s+(?:about\s+)?(?:₹|rs\.?|inr)?\s*(\d{2,5})\b",
    re.IGNORECASE,
)
# Hinglish often puts the amount first: "650 pay madthini", "150 cash kottini".
MONEY_BEFORE_VERB_RE = re.compile(
    r"(\d{2,5})\s*(?:rupees?|rs\.?|inr|cash)?\s*(?:pay|paying|give|giving|offer|offering|spend)\b",
    re.IGNORECASE,
)
MONEY_AFTER_RE = re.compile(
    r"(\d{2,5})\s*(?:rupees?|rs\.?|inr|cash)\b", re.IGNORECASE
)
MONEY_SYMBOL_RE = re.compile(r"(?:₹|rs\.?|inr)\s*(\d{2,5})", re.IGNORECASE)
MONEY_FORWARD_RE = re.compile(
    r"\b(\d{2,5})\s*(?:rupees?|rs\.?|inr)?\s*(?:for|to)\s+(?:the\s+)?"
    r"(?:airport|auto|cab|taxi|bike|ride|trip|driver)\b",
    re.IGNORECASE,
)
MONEY_BACKWARD_RE = re.compile(
    r"\b(?:airport|auto|cab|taxi|bike|ride|trip|driver)\s+(?:for|at)\s+"
    r"(?:₹|rs\.?|inr)?\s*(\d{2,5})\b",
    re.IGNORECASE,
)


def extract_offered_amount(text: str) -> int:
    """Find the fare the player is actually offering, not every number in the text.

    The old implementation took the maximum of every 3-5 digit number, so
    "take the 500D bus and pay ₹30" could be treated as a ₹500 spend.
    """
    for pattern in (
        MONEY_VERB_RE,
        MONEY_BEFORE_VERB_RE,
        MONEY_FORWARD_RE,
        MONEY_BACKWARD_RE,
        MONEY_SYMBOL_RE,
        MONEY_AFTER_RE,
    ):
        matches = pattern.findall(text)
        if matches:
            try:
                return int(matches[-1])
            except (TypeError, ValueError):
                continue
    return 0


def _authoritative_situation(state: GameState, moved: bool) -> str:
    cp = CHECKPOINTS[state.checkpoint_idx]
    prefix = "" if moved else "Still at "
    return f"{prefix}{cp['name']} · {cp['situation']}"


def _checkpoint_aliases(idx: int) -> List[str]:
    """Short names players/narrators actually use for a checkpoint."""
    aliases = [CHECKPOINTS[idx]["name"].split()[0]]
    if idx == len(CHECKPOINTS) - 1:
        aliases.extend(["BLR", "airport"])
    return aliases


def _mentions_checkpoint(text: str, idx: int) -> bool:
    return any(
        re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE)
        for alias in _checkpoint_aliases(idx)
    )


def _find_event(event_id: Optional[str]) -> Optional[dict]:
    if not event_id:
        return None
    return next((e for e in EVENTS if e["id"] == event_id), None)


def _pick_event(state: GameState) -> Optional[dict]:
    """Deterministic obstacle for the upcoming turn: same run, same events."""
    if os.getenv("DISABLE_EVENTS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    upcoming_turn = state.turn + 1
    if upcoming_turn < 2:
        return None
    digest = hashlib.sha256(
        f"{state.session_id}:{upcoming_turn}:event".encode("utf-8")
    ).hexdigest()
    roll = int(digest[:8], 16)
    if roll % 100 >= 45:  # roughly 45% of turns draw an obstacle
        return None
    eligible = [
        e
        for e in EVENTS
        if e["min_cp"] <= state.checkpoint_idx <= e["max_cp"]
        and e["id"] not in state.seen_events
    ]
    if not eligible:
        return None
    return eligible[roll % len(eligible)]


def _schedule_next_event(state: GameState) -> None:
    event = _pick_event(state)
    if event:
        state.next_event_id = event["id"]
        state.next_event_headline = event["headline"]
        state.next_event_detail = event["detail"]
        if event["id"] not in state.seen_events:
            state.seen_events = (state.seen_events + [event["id"]])[-12:]
    else:
        state.next_event_id = None
        state.next_event_headline = None
        state.next_event_detail = None


def reconcile_turn(
    state: GameState, player_action: str, proposal: LLMProposal
) -> GameState:
    guardrails: List[str] = []
    narrative = (proposal.narrative or "").strip()
    if not narrative:
        narrative = "The city absorbs your move without comment and keeps moving."

    # Remember what kind of vehicle this turn is about. "Okay, 400, drop me"
    # inherits the auto from the previous turn, so the auto fare band applies.
    action_vehicles = _vehicle_matches(player_action)
    narrative_vehicles = _vehicle_matches(narrative)
    if action_vehicles:
        state.last_vehicle = action_vehicles[0]
    elif len(narrative_vehicles) == 1:
        state.last_vehicle = narrative_vehicles[0]
    vehicle = state.last_vehicle

    time_delta = proposal.proposed_time_mins
    cash_cost = proposal.proposed_cash_cost
    battery_drain = proposal.proposed_battery_drain
    cp_delta = proposal.proposed_checkpoint_delta

    # The obstacle the player saw in RIGHT NOW applies to this turn. Only the
    # engine may change its effect; the narrator just describes it.
    event = _find_event(state.next_event_id)
    event_capped = False
    if event:
        guardrails.append(f"EVENT: {event['headline']} — {event['detail']}")
        time_delta += int(event.get("time_mod") or 0)

    # Deterministic input scans (never rely solely on the LLM flag).
    regex_cheat = bool(CHEAT_REGEX.search(player_action))
    regex_phone_used = bool(PHONE_UPI_REGEX.search(player_action))
    regex_samosa_mentioned = bool(SAMOSA_REGEX.search(player_action))
    regex_friend_used = bool(FRIEND_REGEX.search(player_action)) or _is_call_only(player_action)

    # Effects need an authorising action: the LLM cannot spend the samosa,
    # burn Rohit's favour, or claim phone use on a turn where the player never
    # mentioned them.
    phone_used = regex_phone_used or regex_friend_used
    friend_used = regex_friend_used
    samosa_used = bool(proposal.used_samosa and regex_samosa_mentioned)
    blocked = False

    # 1. Anti-Cheat & Impossible Physics Guardrail
    if proposal.cheat_or_impossible or regex_cheat:
        blocked = True
        cp_delta = 0
        cash_cost = 0
        time_delta = 12
        guardrails.append(
            "ANTI-CHEAT GUARDRAIL: Reality cannot be hacked at Silk Board (+12m penalty, 0 progress)."
        )
        if regex_cheat and not proposal.cheat_or_impossible:
            narrative = (
                "You attempt to bend the laws of Bengaluru physics with a prompt exploit. "
                "A traffic warden blows his whistle in your ear while a BMTC bus inches past your toes. Twelve minutes vanish."
            )
        else:
            narrative += " [The city refuses to process the request.]"

    # 2. Negative Cash Exploit Guardrail (player trying to earn/steal money)
    if cash_cost < 0:
        blocked = True
        cp_delta = 0
        guardrails.append(
            f"CASH INTEGRITY GUARDRAIL: Blocked negative cost exploit (₹{cash_cost} -> ₹0)."
        )
        narrative = (
            "You try to talk the driver into paying you, or into magically topping up your wallet. "
            "He stares, rolls up the window, and rejoins the queue. Nothing changes except the clock."
        )
        cash_cost = 0

    # 3. Dead Phone / UPI / Cab App / Call Guardrail
    if phone_used and state.battery <= 0:
        already_blocked = blocked
        blocked = True
        cp_delta = 0
        cash_cost = 0
        time_delta = max(10, time_delta)
        guardrails.append(
            "DEAD PHONE GUARDRAIL (0%): Blocked UPI/App/Call action. Physical cash & street haggling only."
        )
        if already_blocked:
            narrative += " [The phone is also dead at 0%, so nothing digital works.]"
        else:
            # Own the narrative: the proposal may have claimed the payment
            # cleared, which is exactly what the engine just refused.
            narrative = (
                "Your phone is a black brick at 0%: no UPI, no cab app, no call. You stare at your "
                "own reflection in the dead screen while the driver loses interest and pulls away "
                "into the jam. Ten minutes gone for nothing."
            )
    elif phone_used and state.battery > 0:
        battery_drain = max(2, battery_drain)
    elif not phone_used:
        # No app, call, or UPI was used: this is a passive turn, so it may cost
        # at most 1%. (The prompt says so; the engine enforces it anyway.)
        battery_drain = min(1, battery_drain)

    # 4. Willingness Guardrails. These run before the cash check so a driver's
    # counter-offer is presented as a choice instead of hiding behind an
    # overdraft the player never triggered.
    offered_amount = extract_offered_amount(player_action)
    offer_verb_money = bool(
        MONEY_VERB_RE.search(player_action) or MONEY_BEFORE_VERB_RE.search(player_action)
    )

    # A named fare cannot be narrated as paid while the proposal charges
    # nothing — that would hand out a free ride. Only an actual offer counts;
    # mentioning "I have 500 rupees" is not a commitment to spend it.
    if (
        offered_amount
        and offer_verb_money
        and cp_delta > 0
        and cash_cost == 0
        and not FREE_RIDE_REGEX.search(player_action)
    ):
        cash_cost = offered_amount

    # 4a. Consent: a quoted fare is an offer, not a transaction. The player
    # must name the price or tell the driver to go; a driver countering with a
    # different number has likewise not been accepted yet. A question is an
    # inquiry even when it contains a verb like "take".
    is_question = "?" in player_action or bool(QUESTION_START_REGEX.search(player_action))
    explicit_yes = bool(EXPLICIT_YES_REGEX.search(player_action))
    consented = explicit_yes or (
        not is_question
        and (bool(offered_amount) or bool(RIDE_CONSENT_REGEX.search(player_action)))
    )

    consent_problem = None
    if cash_cost > 0:
        if offered_amount > state.cash:
            # The player cannot fund their own offer: this is an overdraft, not
            # a negotiation. Let the cash guard explain it.
            consent_problem = None
        elif offered_amount and cash_cost != offered_amount:
            consent_problem = "counter"
        elif not consented:
            consent_problem = "haggle"

    if consent_problem:
        already_blocked = blocked
        blocked = True
        if consent_problem == "counter":
            guardrails.append(
                f"CONSENT GUARDRAIL: Driver countered ₹{cash_cost} against the "
                f"player's ₹{offered_amount}; waiting for acceptance."
            )
        else:
            guardrails.append(
                f"CONSENT GUARDRAIL: Blocked ₹{cash_cost} fare the player never accepted."
            )
        if already_blocked:
            narrative += " [Nothing is paid: the fare was never accepted.]"
        elif consent_problem == "counter":
            narrative = (
                f"You offered ₹{offered_amount}; the driver answers with ₹{cash_cost} for a shorter "
                "stretch. Your hand stays off your wallet and the auto stays parked while the signal "
                f"cycles twice. (Say 'ok ₹{cash_cost}, chalo' to accept his counter, or walk away.)"
            )
        else:
            narrative = (
                f"The driver names his price: ₹{cash_cost}. You have not agreed to it, so your wallet "
                "stays shut and the auto stays parked. He waits through two signal cycles, shrugs, and "
                f"turns back to the jam. (Say 'ok ₹{cash_cost}, chalo' to accept the fare and board.)"
            )
        cash_cost = 0
        cp_delta = 0

    # 4b. Cash Overdraft Guardrail. Only inspect the actual offered fare, so a
    # passing mention of "₹500 in my pocket" or "the 500D bus" cannot block a walk.
    intended_spend = cash_cost
    if offered_amount and (cash_cost > 0 or MONEY_VERB_RE.search(player_action)):
        intended_spend = max(cash_cost, offered_amount)

    if intended_spend > state.cash:
        # The story must not claim a payment the ledger refuses. If no earlier
        # guardrail already owns the narrative, replace it with what really
        # happens; otherwise just note the extra blocker.
        already_blocked = blocked
        blocked = True
        guardrails.append(
            f"CASH OVERDRAFT GUARDRAIL: Blocked ₹{intended_spend} spend (wallet has ₹{state.cash})."
        )
        if already_blocked:
            narrative += (
                f" [The fare also exceeds what you carry: ₹{state.cash} in the wallet.]"
            )
        else:
            narrative = (
                f"You reach into your pocket for ₹{intended_spend} and come up "
                f"₹{intended_spend - state.cash} short — the wallet holds ₹{state.cash}, not a rupee more. "
                "The driver watches you count it twice, loses interest, and pulls away into the jam. "
                "Ten minutes gone, and the meter never started."
            )
        cash_cost = 0
        cp_delta = 0
        time_delta = max(10, time_delta)

    # 5. Samosa Inventory Guardrail. Consume only after every blocker has cleared.
    if samosa_used and not state.has_samosa:
        blocked = True
        cp_delta = 0
        cash_cost = 0
        guardrails.append(
            "INVENTORY GUARDRAIL: Blocked attempt to use the cold samosa a second time!"
        )
        narrative += (
            " [You reach into your bag for the cold samosa, only to pull out an empty, "
            "grease-stained paper wrapper. You already used it earlier!]"
        )
    elif regex_samosa_mentioned and not state.has_samosa and not samosa_used:
        blocked = True
        cp_delta = 0
        cash_cost = 0
        guardrails.append(
            "INVENTORY GUARDRAIL: The cold samosa has already been used."
        )

    samosa_used_now = bool(samosa_used and state.has_samosa and not blocked)

    # 6. Friend NPC guardrail: a shortcut is a one-time favour.
    if friend_used and state.friend_called:
        blocked = True
        cp_delta = 0
        cash_cost = 0
        time_delta = max(5, time_delta)
        guardrails.append(
            "NPC MEMORY GUARDRAIL: Rohit already gave you his one shortcut. His phone rings out."
        )
        narrative += " [Rohit's phone rings out. He is at a wedding, and the wedding has a DJ.]"
    friend_used_now = bool(friend_used and not state.friend_called and not blocked)

    # 7. Forward-Only Time & Speed Clamping
    if time_delta < 5 or time_delta > 22:
        clamped_time = max(5, min(22, time_delta))
        guardrails.append(
            f"TIME CLAMP: Enforced forward clock bounds ({time_delta}m -> {clamped_time}m)."
        )
        time_delta = clamped_time

    if cp_delta < 0 or cp_delta > 2:
        clamped_cp = max(0, min(2, cp_delta))
        guardrails.append(
            f"PACE GUARDRAIL: Clamped checkpoint delta ({cp_delta} -> {clamped_cp})."
        )
        cp_delta = clamped_cp

    # The cold samosa is documented as a two-leg bribe. If the narrator trades
    # it for a ride but only returns one leg, honour the resource's value.
    samosa_for_ride = bool(
        samosa_used_now
        and not re.search(r"\b(eat|ate|eaten|hungry|bite|devour)\b", player_action, re.IGNORECASE)
    )
    if (
        samosa_for_ride
        and cp_delta == 1
        and re.search(
            r"\b(ride|lift|drop|drive|auto|cab|bike|pillion|lane|jam|traffic|fastest|shortcut)\b",
            player_action,
            re.IGNORECASE,
        )
    ):
        cp_delta = 2
        guardrails.append("SAMOSA VALUE: the cold samosa buys two legs.")

    # A two-checkpoint jump needs a real tactical play: the samosa or ₹120+.
    pace_clamped = False
    if cp_delta == 2 and not samosa_used_now and cash_cost < 120:
        cp_delta = 1
        pace_clamped = True
        guardrails.append(
            "PACE GUARDRAIL: Two legs require a samosa trade or a ₹120+ fare; progress reduced to one leg."
        )

    # Obstacles can also physically stop a long move (no pillion past a check).
    if event and event.get("delta_cap") is not None and cp_delta > event["delta_cap"]:
        cp_delta = event["delta_cap"]
        event_capped = True
        guardrails.append(
            f"EVENT: {event['headline']} limits progress to {cp_delta} leg(s)."
        )

    # 8. Fare sanity band. The narrator may invent surge pricing, but an
    # affordable ride must not silently drain more than the documented range.
    # Autos legitimately settle high (they open at ₹650); cabs, bikes, buses
    # and generic rides are capped at ₹150 per leg, ₹480 for a two-leg stretch.
    if cp_delta == 0:
        fare_ceiling = 150
    elif vehicle == "auto":
        fare_ceiling = 480
    else:
        fare_ceiling = 150 if cp_delta == 1 else 480
    if cash_cost > fare_ceiling:
        leg_text = {0: "a ride that went nowhere", 1: "one leg", 2: "two legs"}.get(
            cp_delta, "the ride"
        )
        guardrails.append(
            f"FARE GUARDRAIL: Clamped ₹{cash_cost} to ₹{fare_ceiling} for {leg_text}."
        )
        narrative = (
            narrative.rstrip()
            + f" [You haggle the fare down from ₹{cash_cost} to ₹{fare_ceiling}.]"
        )
        cash_cost = fare_ceiling

    # 9. Apply Authoritative State Mutations
    cp_before = state.checkpoint_idx
    state.checkpoint_idx = min(len(CHECKPOINTS) - 1, state.checkpoint_idx + cp_delta)
    state.elapsed_mins += time_delta
    state.cash = max(0, min(500, state.cash - cash_cost))

    # Proposals may legitimately drain 0% (waiting, walking, staring at traffic).
    clamped_drain = max(0, min(4, battery_drain))
    actual_batt_drain = min(state.battery, clamped_drain)
    state.battery = max(0, state.battery - actual_batt_drain)
    state.turn += 1

    # The narrator must not kill the phone while the engine says it lives.
    if state.battery > 0 and (
        re.search(
            r"\b(phone|screen|battery)\b[^.]{0,60}\b(dies|died|dead|dark|0%)\b",
            narrative,
            re.IGNORECASE,
        )
        or re.search(r"\b(dead phone|phone is dead|phone'?s dead)\b", narrative, re.IGNORECASE)
    ):
        narrative = narrative.rstrip() + (
            f" The screen gutters but holds at {state.battery}% — not dead, just furious."
        )

    if samosa_used_now:
        state.has_samosa = False
        guardrails.append("INVENTORY: 1x cold samosa traded/consumed.")
    if friend_used_now:
        state.friend_called = True

    # Update weather & rain state deterministically by location, not by turn:
    # the sky follows where you are, so Nagawara can't report runway lights.
    weather_idx = state.checkpoint_idx
    w_label, w_rain = WEATHER_PROGRESSION.get(weather_idx, ("Humid evening haze", False))
    state.weather = w_label
    state.is_raining = w_rain

    # The HUD situation is always derived from the authoritative checkpoint,
    # never from an LLM hook that might name the wrong location.
    state.active_situation = _authoritative_situation(
        state, moved=state.checkpoint_idx > cp_before
    )

    # The narrator sometimes names a stop that does not match where the engine
    # actually left the player: only the first stop of a two-leg move, a stop
    # beyond a pace-clamped drop-off, or a detour that never happened.
    dest_idx = state.checkpoint_idx
    dest_corrected = False
    if dest_idx > cp_before:
        dest = CHECKPOINTS[dest_idx]
        mentions_dest = _mentions_checkpoint(narrative, dest_idx)
        next_idx = min(len(CHECKPOINTS) - 1, cp_before + 1)
        ahead_idx = min(len(CHECKPOINTS) - 1, dest_idx + 1)
        if (
            not mentions_dest
            and dest_idx > next_idx
            and _mentions_checkpoint(narrative, next_idx)
        ):
            dest_corrected = True
            if samosa_for_ride:
                narrative = (
                    narrative.rstrip()
                    + f" The samosa does the rest of the talking — he changes his mind and pushes on"
                    f" to {dest['name']}, {45 - dest['km']} km still between you and the airport."
                )
            else:
                narrative = (
                    narrative.rstrip()
                    + f" He doesn't stop at {CHECKPOINTS[next_idx]['name'].split()[0]} — he pushes on"
                    f" to {dest['name']}, {45 - dest['km']} km still between you and the airport."
                )
        elif (
            not mentions_dest
            and ahead_idx > dest_idx
            and _mentions_checkpoint(narrative, ahead_idx)
        ):
            dest_corrected = True
            narrative = (
                narrative.rstrip()
                + f" The auto stops short, at {dest['name']} — "
                f"{CHECKPOINTS[ahead_idx]['name'].split()[0]} is still ahead."
            )

    # A pace-clamped move ended closer than the narrator may have described;
    # say so plainly so the card matches the HUD.
    if pace_clamped and not dest_corrected:
        dest = CHECKPOINTS[state.checkpoint_idx]
        narrative = (
            narrative.rstrip()
            + f" Two legs became one: the driver stops at {dest['name']}, "
            f"{45 - dest['km']} km from the airport."
        )

    if event_capped and not dest_corrected:
        dest = CHECKPOINTS[state.checkpoint_idx]
        narrative = (
            narrative.rstrip()
            + f" The {event['headline'].lower()} stops you short at {dest['name']}, "
            f"{45 - dest['km']} km from the airport."
        )

    # Keep a registry of introduced names so the narrator stops recycling
    # Prakash/Suresh/Ramesh for every new character.
    candidate_names: List[str] = []
    if proposal.npc_name:
        candidate_names.append(proposal.npc_name)
    candidate_names.extend(re.findall(r"\bnamed\s+([A-Z][a-z]+)\b", narrative))
    for raw in candidate_names:
        name = " ".join(raw.strip().split())[:40]
        if name and name.lower() not in {n.lower() for n in state.used_names}:
            state.used_names = (state.used_names + [name])[-12:]

    # The win card says the player reaches the check-in counter, so the prose
    # has to end at the terminal rather than still gazing at it.
    if state.checkpoint_idx == len(CHECKPOINTS) - 1 and not ARRIVAL_REGEX.search(narrative):
        narrative += (
            " You step out at the Terminal 1 departure curb — the check-in counter is right there."
        )

    # Update NPC Memory: only durable character facts survive. Prices, fare
    # recaps and "player agreed/paid" claims are rejected — the ledger is the
    # only record of money, and a blocked deal must never become canon.
    if proposal.npc_memory_note and not blocked:
        note = " ".join(proposal.npc_memory_note.split())[:140]
        if note and not MEMORY_TRANSACTION_REGEX.search(note):
            # Merge a near-duplicate fact instead of stacking the same NPC twice.
            normalized = note.lower().rstrip(".")
            for i, existing in enumerate(state.npc_memory):
                if normalized == existing.lower().rstrip("."):
                    break  # exact duplicate: keep the original wording
                if _notes_similar(note, existing):
                    state.npc_memory[i] = note
                    break
            else:
                state.npc_memory = (state.npc_memory + [note])[-MAX_NPC_MEMORY:]

    # Record Turn History
    state.history.append(
        TurnRecord(
            turn=state.turn,
            action=player_action,
            narrative=narrative,
            clock_after=format_clock(state.elapsed_mins),
            time_delta=time_delta,
            cash_delta=-cash_cost,
            battery_delta=-actual_batt_drain,
            checkpoint_before=cp_before,
            checkpoint_after=state.checkpoint_idx,
            guardrail_notes=guardrails,
        )
    )

    # 10. Deterministic Win / Loss Evaluation
    if state.checkpoint_idx >= len(CHECKPOINTS) - 1:
        if state.elapsed_mins <= DEADLINE_MINS:
            state.status = "WON"
            margin = DEADLINE_MINS - state.elapsed_mins
            if margin <= 5:
                state.end_reason = (
                    f"You reach the check-in counter at {format_clock(state.elapsed_mins)} as the agent "
                    f"is lowering the shutter. She looks at your wet shoes, then your boarding pass, "
                    f"and says the two best words in Bengaluru: 'Go ahead.'"
                )
            elif margin <= 20:
                state.end_reason = (
                    f"You reach BLR Terminal 1 at {format_clock(state.elapsed_mins)} with {margin} minutes "
                    f"to spare. Enough time for security, a bottle of water, and one overpriced sandwich."
                )
            else:
                state.end_reason = (
                    f"You arrive at BLR Terminal 1 at {format_clock(state.elapsed_mins)}, so early that "
                    f"the check-in agent asks if you're sure you're on the 9:40. You have never been more sure of anything."
                )
        else:
            state.status = "LOST"
            state.end_reason = (
                f"Heartbreak at the counter. You reach BLR Terminal 1 at {format_clock(state.elapsed_mins)}, "
                f"missing the 8:55 PM check-in closure by {state.elapsed_mins - DEADLINE_MINS} minutes. "
                f"Somewhere above the ORR, FL 2047 pushes back without you."
            )
    elif state.elapsed_mins >= DEADLINE_MINS:
        state.status = "LOST"
        loc_name = CHECKPOINTS[state.checkpoint_idx]["name"]
        state.end_reason = (
            f"It is {format_clock(state.elapsed_mins)}. Check-in for Flight FL 2047 closed at 8:55 PM "
            f"while you were still stuck at {loc_name}. The city does not blink."
        )
    elif state.turn >= MAX_TURNS:
        state.status = "LOST"
        loc_name = CHECKPOINTS[state.checkpoint_idx]["name"]
        km_left = max(0, 45 - CHECKPOINTS[state.checkpoint_idx]["km"])
        state.end_reason = (
            f"Twelve moves gone. You are still at {loc_name}, {km_left} km from the airport, "
            f"with ₹{state.cash} and {state.battery}% battery. FL 2047 has stopped waiting."
        )

    if state.status == "WON":
        state.active_situation = "You are airside. FL 2047 is boarding. The ORR is someone else's problem now."
    elif state.status == "LOST":
        state.active_situation = state.end_reason

    # Draw the obstacle for the next move (the player will see it before typing)
    # and retire the one that just happened.
    if state.status == "PLAYING":
        _schedule_next_event(state)
    else:
        state.next_event_id = None
        state.next_event_headline = None
        state.next_event_detail = None

    return state


# ---------------------------------------------------------------------------
# 5. MULTI-TURN AWARE LLM NARRATOR ENGINE
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the narrator of 'Escape Silk Board', a sharp, dryly hilarious, high-tension interactive fiction game set in Bengaluru during peak evening traffic.

YOUR VOICE & TASTE:
- Write 2 to 4 vivid, punchy sentences per turn. Zero generic AI filler. Second person, present tense, dry and warm at once.
- ENGLISH ONLY in the prose: natural Indian-English with a Bengaluru accent, no Kannada words mixed in. Drivers and locals speak plain Indian English too, so every sentence reads cleanly on the first pass.
- This is Bengaluru, not generic India: Silk Board, ORR, Agara, Bellandur, Tin Factory, Hebbal, Airport Road, Namma Metro, BMTC, Vayu Vajra, service roads that flood, pillion riders, and the sacred street value of a cold samosa.
- Driver speech examples: "Airport? Too far, sir — ₹650." / "No meter, sir. Traffic is terrible." / "Double charge, sir — airport is far." / "Only till Agara, not the airport."
- Every turn must carry one concrete pressure beat: the clock, the kilometres left, the rain, the 8:55 cutoff. Escalate as the run goes on.
- One metaphor per turn, maximum. Never reuse a catchphrase or stock detail from the last 3 turns (no repeating the meter joke, the paan detail, or "45 km, permit"); each stop brings a new named person with their own vehicle, quirk, and way of talking.
- Maintain strict continuity with RECENT TURN HISTORY and CURRENT SITUATION HOOK. If a driver demanded a price last turn and the player counter-offers this turn, resolve that exact haggle naturally!
- Reference `npc_memory` callbacks when relevant.
- Every new character needs a FRESH name. Never reuse a name from NAMES ALREADY USED, and never default to the same three names; invent varied Kannada, Muslim, Tamil, Telugu, and Anglo-Indian names.
- `npc_name`: if you name a new character this turn, put just that name here (e.g. "Basavaraj"); otherwise null.
- `npc_memory_note`: store only a durable character fact (name, habit, grudge, preference). Never prices, fares, or claims that money changed hands — the engine rejects those, because the ledger is the only record of money.

UNDERSTANDING THE PLAYER:
- Accept Hinglish and loose phrasing as local intent, never as gibberish: "bhaiya airport chaloge?", "meter se", "haggle", "negotiate" all mean bargaining with the auto driver.
- A bare money offer ("₹450", "offer 450") is a fare negotiation with the nearest driver, so resolve it like one.
- ANSWER ONLY WHAT WAS ASKED. "How much?" is a request for a price, not a destination choice — quote the fare for the trip the player actually stated (normally the airport), or ask "Where to?". Never invent a destination and price it.
- The Next Checkpoint in the engine state is where a successful ride WILL end, not where the player asked to go. Do not name it unless the player is actually travelling there; if they ask about the airport, quote the airport fare first (and refuse the full run in your own words) instead of silently switching them to the next checkpoint.

FAIRNESS & PACING (HARD 12-MOVE CAP):
- The route has 8 legs from Checkpoint 0 (Silk Board) to Checkpoint 8 (BLR Check-In).
- NO single action covers the whole city. The airport is 45 km and 8 legs away, so a ride, bribe, or shortcut moves at most 2 legs. A driver ALWAYS drops the player at the next checkpoint, never at Terminal 1, unless the engine state already shows checkpoint 8. If the player asks for the airport, the driver refuses the full trip (fare, airport permit, traffic) and takes only the next stretch — narrate that partial drop-off honestly instead of implying a direct airport run.
- The engine ends the run after 12 player moves (or 115 in-game minutes). Reward decisive play; never stall the player for free.
- PARTIAL PROGRESS: if the player names a place farther than one move can reach, still advance them as far as this move legitimately can (max 2 legs, or 1 on foot) and say plainly that they only got as far as the next checkpoint. Example: at Silk Board, "I walk to Hebbal" moves the player ONE leg to Agara and narrates that they only covered the first stretch. Refusing a long ride or walk just because the destination is far away is a bug, not realism — always make the partial move.
- `proposed_checkpoint_delta`:
  * `0` if the player asks an auto to go by meter without offering extra, takes a bait shortcut, argues, waits, or tries something impossible.
  * `1` for smart, realistic progress (counter-offering a fair fare, walking past a signal jam to catch a pillion bike/shared cab/bus).
  * `2` ONLY for a major tactical play (e.g., sweetening a deal with the cold samosa, or paying ₹120+ for a fast cab/expressway transfer).
- Set `payment_or_phone_used: true` whenever the player uses UPI, Ola, Uber, Rapido, Namma Yatri, Google Maps, or makes a phone call.
- Set `used_friend: true` only when the player explicitly calls their shortcut friend. That favour works once; the engine rejects a second call.
- Set `used_samosa: true` only when the player explicitly offers, trades, or eats the cold samosa. Never spend it on an unrelated move. A samosa traded for a ride is a two-leg play; eating it is a one-leg meal.
- If `PHONE_BATTERY` is 0%, any digital action (UPI, cab apps, maps, calls) MUST fail in your narrative with `proposed_checkpoint_delta: 0`.
- Never narrate the phone dying or the screen going dark unless this turn's drain leaves it at 0%. At 1-3%, the phone dims, lags, or drops mid-sentence — it does not die.
- Keep single-leg auto/bike/bus/shared-cab fares realistic (₹40 to ₹150 per leg, or ₹350–₹480 if negotiating a multi-leg stretch) so a smart ₹500 budget can barely make it across the city.
- MATCH YOUR OWN DELTA: the drop-off you narrate must be the checkpoint that `proposed_checkpoint_delta` actually reaches — delta 1 ends at the Next Checkpoint, delta 2 ends at the Following Checkpoint. Never name a nearer stop than the delta you return, and never claim the airport before checkpoint 8.
- MONEY MUST MATCH THE LEDGER: the rupee amount a driver quotes, accepts, or pockets in the narrative MUST equal `proposed_cash_cost`. Never invent a bigger number than the cash that actually changes hands (e.g. do not accept ₹50 and then narrate the driver pocketing ₹700), and never charge more than the player actually holds.
- A PRICE QUOTE IS NOT A TRANSACTION: only charge `proposed_cash_cost` when the player names a fare or tells the driver to go ("ok ₹150, chalo", "take me to Agara", "I'll pay 150"). If they are merely asking about the trip, quote the fare and stop: `proposed_cash_cost: 0`, `proposed_checkpoint_delta: 0`.
- If the player names a fare, `proposed_cash_cost` MUST equal that number. When the driver wants a different number (a counter-offer), quote it with `proposed_cash_cost: 0` and `proposed_checkpoint_delta: 0` and wait for the player to accept; never charge a counter the player has not agreed to.
- A question is not consent ("Can you take me to Agara?", "UPI?", "How much?"). Quote the fare and wait unless the player names a price or says yes/ok.

Respond ONLY with valid JSON matching this schema:
{
  "narrative": "2-4 sentences describing what happens and setting up the next beat",
  "active_situation_hook": "1 sentence describing the immediate situation/offer at the new location",
  "proposed_time_mins": 11,
  "proposed_cash_cost": 60,
  "payment_or_phone_used": false,
  "proposed_battery_drain": 1,
  "used_samosa": false,
  "used_friend": false,
  "proposed_checkpoint_delta": 1,
  "cheat_or_impossible": false,
  "npc_name": "optional new character name or null",
  "npc_memory_note": "optional short string or null"
}"""


def build_user_prompt(state: GameState, action: str) -> str:
    cur_cp = CHECKPOINTS[state.checkpoint_idx]
    next_cp = CHECKPOINTS[min(len(CHECKPOINTS) - 1, state.checkpoint_idx + 1)]
    after_next_cp = CHECKPOINTS[min(len(CHECKPOINTS) - 1, state.checkpoint_idx + 2)]
    mins_left = DEADLINE_MINS - state.elapsed_mins

    recent_turns_text = "None (This is Turn 1)."
    if state.history:
        recent_slice = state.history[-3:]
        lines = []
        for t in recent_slice:
            lines.append(
                f"Turn {t.turn} | Player: \"{t.action}\" -> Narrator: \"{t.narrative}\" "
                f"(Time +{t.time_delta}m, Cash {t.cash_delta}, Moved to CP {t.checkpoint_after})"
            )
        recent_turns_text = "\n".join(lines)

    used_names_text = json.dumps(state.used_names) if state.used_names else "none yet"
    event_line = ""
    if state.next_event_headline:
        event_line = (
            f"- CURRENT EVENT (the player can see this; weave it into the scene, do not ignore it): "
            f"{state.next_event_headline} — {state.next_event_detail}\n"
        )
    final_hint = ""
    if state.checkpoint_idx >= len(CHECKPOINTS) - 2:
        final_hint = (
            "\n- FINAL APPROACH: a successful move here can reach BLR Terminal 1. If it does, "
            "END the prose with the player actually stepping out at the Terminal 1 departure "
            "curb / check-in counter, not still approaching it.\n"
        )

    return f"""=== AUTHORITATIVE ENGINE STATE (DO NOT VIOLATE) ===
- Move: {state.turn + 1} of {MAX_TURNS} (hard cap; a run that has not reached the airport by move {MAX_TURNS} is lost)
- Clock: {format_clock(state.elapsed_mins)} ({mins_left} mins left until 8:55 PM check-in cutoff)
- Current Location: [{state.checkpoint_idx}/8] {cur_cp['name']} ({cur_cp['km']} km / 45 km)
- Next Checkpoint (where delta 1 ends): {next_cp['name']} ({next_cp['zone']})
- Following Checkpoint (where delta 2 ends): {after_next_cp['name']} ({after_next_cp['zone']})
- Physical Cash in Pocket: ₹{state.cash} (Hard maximum player can spend)
- Phone Battery: {state.battery}% ({"UPI, CAB APPS & CALLS WORK" if state.battery > 0 else "PHONE IS DEAD (0%) - NO UPI, NO CAB APPS, NO CALLS ALLOWED"})
- Bag Inventory: {"1 cold samosa (available to trade/eat)" if state.has_samosa else "Empty (cold samosa already used)"}
- Friend Favour: {"already used (Rohit will not answer)" if state.friend_called else "available once (Rohit knows a 'shortcut')"}
- Weather: {state.weather}
- Current Situation Hook: {state.active_situation}
- NPC Memory: {json.dumps(state.npc_memory)}
- NAMES ALREADY USED (do not reuse for new characters): {used_names_text}
{event_line}{final_hint}=== RECENT TURN HISTORY (FOR CONVERSATION & HAGGLE CONTINUITY) ===
{recent_turns_text}

=== PLAYER'S NEW ACTION ===
"{action}"

Output strictly valid JSON."""


def llm_mode() -> str:
    """Return 'llm' when a provider is configured, otherwise 'offline'."""
    if os.getenv("DISABLE_LLM", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "offline"
    if any(
        os.getenv(name)
        for name in ("LLM_BASE_URL", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
    ):
        return "llm"
    return "offline"


async def call_llm(state: GameState, action: str) -> LLMProposal:
    if llm_mode() == "offline":
        return deterministic_fallback_narrator(state, action)

    local_base = os.getenv("LLM_BASE_URL")
    groq_key = os.getenv("GROQ_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if local_base:
        url = local_base.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        local_key = os.getenv("LLM_API_KEY", "")
        headers = {"Authorization": f"Bearer {local_key}"} if local_key else {}
        model = os.getenv("LLM_MODEL", "local-model")
    elif groq_key:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {groq_key}"}
        model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    elif gemini_key:
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {"Authorization": f"Bearer {gemini_key}"}
        model = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    elif openai_key:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {openai_key}"}
        model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    else:
        return deterministic_fallback_narrator(state, action)

    try:
        # Gemini 3.x spends part of this budget on internal reasoning, so the
        # ceiling has to leave room for both the thinking and the JSON body.
        max_tokens = max(120, min(4096, int(os.getenv("LLM_MAX_TOKENS", "2000"))))
    except ValueError:
        max_tokens = 2000

    def build_payload(budget: int) -> dict:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(state, action)},
            ],
            "temperature": 0.65,
            "max_tokens": budget,
        }
        if os.getenv("LLM_JSON_MODE", "1").strip() != "0":
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def request_proposal(budget: int) -> LLMProposal:
        async with httpx.AsyncClient(timeout=18.0) as client:
            resp = await client.post(url, headers=headers, json=build_payload(budget))
            resp.raise_for_status()
            raw_content = resp.json()["choices"][0]["message"]["content"].strip()
            if raw_content.startswith("```"):
                raw_content = raw_content.strip("`")
                if raw_content.lower().startswith("json"):
                    raw_content = raw_content[4:]
                raw_content = raw_content.strip()
            return LLMProposal(**json.loads(raw_content))

    # One retry covers both failure modes that were silently degrading turns:
    # a transient 429/5xx, and a JSON body cut off because reasoning ate the
    # token budget (retry with a bigger budget).
    last_exc: Optional[Exception] = None
    for attempt, budget in enumerate((max_tokens, min(4096, max_tokens * 2))):
        try:
            return await request_proposal(budget)
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status = exc.response.status_code
            transient = status == 429 or status >= 500
            logger.warning(
                "LLM HTTP %s on attempt %s%s", status, attempt + 1,
                " (retrying)" if transient and attempt == 0 else "",
            )
            if not transient or attempt:
                break
            await asyncio.sleep(0.8)
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            logger.warning("LLM returned incomplete JSON on attempt %s: %s", attempt + 1, exc)
            if attempt:
                break
        except Exception as exc:
            last_exc = exc
            break

    logger.warning("LLM unavailable, using deterministic narrator: %s", last_exc)
    return deterministic_fallback_narrator(state, action)


def _seeded_roll(*parts: object, threshold: int = 60) -> bool:
    """Stable pseudo-random check so a repeated action is not re-rollable."""
    raw = ":".join(str(part) for part in parts)
    roll = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16) % 100
    return roll < threshold


def _cash_shortfall(state: GameState, fare: int, vehicle: str) -> LLMProposal:
    """Narrate an unaffordable fare without pretending the ride happened."""
    return LLMProposal(
        narrative=(
            f"The {vehicle} wants ₹{fare}. You count the ₹{state.cash} in your pocket twice, "
            f"as if the second count might change the first. It does not. The {vehicle} "
            "pulls away into the traffic without you."
        ),
        proposed_time_mins=9,
        proposed_cash_cost=fare,
        proposed_checkpoint_delta=0,
        proposed_battery_drain=0,
    )


def deterministic_fallback_narrator(state: GameState, action: str) -> LLMProposal:
    """Rules-based Bengaluru narrator used when no LLM is configured.

    The engine recognises the verbs a player actually types (walk, bus, metro,
    auto, cab, bike, samosa, friend, wait, hitch) and returns a fair, bounded
    proposal. The reconciler still owns cash, battery, time, and progress.
    """
    act = action.lower().strip()
    cur_cp = CHECKPOINTS[state.checkpoint_idx]
    next_cp = CHECKPOINTS[min(len(CHECKPOINTS) - 1, state.checkpoint_idx + 1)]
    after_next_cp = CHECKPOINTS[min(len(CHECKPOINTS) - 1, state.checkpoint_idx + 2)]
    offered = extract_offered_amount(act)
    phone_used = bool(PHONE_UPI_REGEX.search(act))

    # ------------------------------------------------------------------
    # Anti-cheat
    # ------------------------------------------------------------------
    if CHEAT_REGEX.search(act):
        return LLMProposal(
            narrative=(
                "You try to summon a cheat code in the middle of Outer Ring Road. "
                "An auto driver honks three times and splashes muddy rainwater onto your sneakers. "
                "Bengaluru does not accept pull requests."
            ),
            active_situation_hook=f"Still stuck at {cur_cp['name']} while traffic inches ahead.",
            proposed_time_mins=12,
            proposed_checkpoint_delta=0,
            cheat_or_impossible=True,
            proposed_battery_drain=0,
        )

    # ------------------------------------------------------------------
    # Samosa: one-time inventory, either eaten for energy or traded for a
    # two-leg lift. A repeated attempt is rejected by the reconciler.
    # ------------------------------------------------------------------
    if SAMOSA_REGEX.search(act):
        if not state.has_samosa:
            return LLMProposal(
                narrative=(
                    "You reach into your bag for the legendary cold samosa and find only an "
                    "empty, grease-stained wrapper. The wrapper has more structural integrity "
                    "than the traffic around you."
                ),
                proposed_time_mins=8,
                proposed_checkpoint_delta=0,
                used_samosa=True,
                npc_memory_note="The empty samosa wrapper is now a local monument.",
            )
        if re.search(r"\b(eat|hungry|bite|finish|devour)\b", act):
            return LLMProposal(
                narrative=(
                    f"You eat the cold samosa on the median at {cur_cp['name']}. It is structurally "
                    f"a potato wrapped in regret, but the calories hit. You jog the next stretch to "
                    f"{next_cp['name']} before the sugar crash."
                ),
                proposed_time_mins=10,
                proposed_checkpoint_delta=1,
                used_samosa=True,
                proposed_battery_drain=0,
            )
        fare = max(0, offered)
        if fare > state.cash:
            return LLMProposal(
                narrative=(
                    f"The driver takes the cold samosa, then counts the ₹{state.cash} in your "
                    f"pocket. He wanted ₹{fare}. He hands the samosa back with the solemnity of "
                    "a man returning a wedding ring, and rolls up his window."
                ),
                proposed_time_mins=9,
                proposed_cash_cost=fare,
                proposed_checkpoint_delta=0,
                proposed_battery_drain=0,
            )
        fare_text = f" and ₹{fare}" if fare else ""
        return LLMProposal(
            narrative=(
                f"The driver at {cur_cp['name']} studies the cold samosa with genuine reverence. "
                f"'Malleshwaram samosa? Sit fast, sir.' For the samosa{fare_text}, he threads a "
                f"four-foot gap between two BMTC buses and blasts past {next_cp['name']} all the "
                f"way to {after_next_cp['name']}."
            ),
            proposed_time_mins=12,
            proposed_cash_cost=fare,
            used_samosa=True,
            proposed_checkpoint_delta=2,
            proposed_battery_drain=0,
            npc_memory_note=f"A driver near {cur_cp['name']} gave you VIP treatment for the cold samosa.",
        )

    # ------------------------------------------------------------------
    # Friend / shortcut: a one-time favour that burns battery. The
    # reconciler enforces both the friend flag and the 0% phone gate.
    # ------------------------------------------------------------------
    friend_intent = bool(re.search(r"\b(friend|rohit|shortcut)\b", act))
    call_only = bool(re.search(r"\bcall\b", act)) and not re.search(
        r"\b(auto|cab|taxi|uber|ola|rapido|bus|metro|bike|rickshaw|pillion|driver|police|cop)\b",
        act,
    )
    if friend_intent or call_only:
        if state.friend_called:
            return LLMProposal(
                narrative=(
                    "You call Rohit again. Somewhere in Whitefield, a phone rings out over a "
                    "wedding DJ. Rohit has already given you his one shortcut, and his one "
                    "shortcut has already given you enough material for a therapy session."
                ),
                proposed_time_mins=6,
                proposed_checkpoint_delta=0,
                used_friend=True,
                payment_or_phone_used=True,
                proposed_battery_drain=0,
            )
        if state.battery <= 0:
            return LLMProposal(
                narrative=(
                    "You try to call Rohit. Your phone is a black mirror at 0%. You mouth his "
                    "name at the screen while a cab driver behind you leans on the horn."
                ),
                proposed_time_mins=8,
                proposed_checkpoint_delta=0,
                used_friend=True,
                payment_or_phone_used=True,
                proposed_battery_drain=0,
            )
        return LLMProposal(
            narrative=(
                "'Bro, take the service road, left at the temple, then right after the—' The call "
                f"drops as your battery gasps. The service road is not a shortcut, but it does "
                f"spit you out at {next_cp['name']} twelve minutes later, which is technically progress."
            ),
            proposed_time_mins=12,
            proposed_checkpoint_delta=1,
            used_friend=True,
            payment_or_phone_used=True,
            proposed_battery_drain=2,
            npc_memory_note="Rohit's one shortcut was a jammed service road, but it moved.",
        )

    # ------------------------------------------------------------------
    # Bus: cheap and honest. One leg for ₹60.
    # ------------------------------------------------------------------
    if re.search(r"\b(bus|bmtc|vayu|vayu vajra|shuttle)\b", act):
        if state.cash < 60:
            return _cash_shortfall(state, 60, "bus")
        return LLMProposal(
            narrative=(
                f"A BMTC bus groans to a stop at {cur_cp['name']}. The conductor takes ₹60, "
                f"sells you a ticket, and spends the next ten minutes shouting at a man who is "
                f"trying to board with a refrigerator. You reach {next_cp['name']} intact."
            ),
            proposed_time_mins=10,
            proposed_cash_cost=60,
            proposed_checkpoint_delta=1,
            proposed_battery_drain=0,
        )

    # ------------------------------------------------------------------
    # Metro: cheap in theory, but the airport line is still a rumour.
    # ------------------------------------------------------------------
    if re.search(r"\b(metro|namma metro|train|rail|railway)\b", act):
        return LLMProposal(
            narrative=(
                f"You follow the purple line signs at {cur_cp['name']} with real hope. "
                "The airport connection is still a PowerPoint slide with a completion date "
                "that moves every election. Eleven minutes vanish before you admit it."
            ),
            proposed_time_mins=11,
            proposed_checkpoint_delta=0,
            proposed_battery_drain=0,
            npc_memory_note="The airport metro remains a rumour with excellent branding.",
        )

    # ------------------------------------------------------------------
    # Bike / pillion: fast through jams, one leg, ₹70 by default.
    # ------------------------------------------------------------------
    if re.search(r"\b(bike|pillion|rapido|two[- ]wheeler|motorcycle|scooter)\b", act):
        if 0 < offered < 40:
            return LLMProposal(
                narrative=(
                    f"You offer ₹{offered}. The pillion rider laughs, points at the rain, and "
                    "rides off with someone who understands surge pricing."
                ),
                proposed_time_mins=6,
                proposed_checkpoint_delta=0,
                proposed_battery_drain=0,
            )
        fare = offered if offered >= 40 else 70
        if fare > state.cash:
            return _cash_shortfall(state, fare, "pillion rider")
        return LLMProposal(
            narrative=(
                f"₹{fare}. A pillion rider hands you a helmet that smells like a decade of "
                f"commutes. You weave through the {cur_cp['zone']} gridlock and hop off at "
                f"{next_cp['name']} before the next wave of honking starts."
            ),
            proposed_time_mins=10,
            proposed_cash_cost=fare,
            payment_or_phone_used=phone_used,
            proposed_battery_drain=2 if phone_used else 0,
            proposed_checkpoint_delta=1,
        )

    # ------------------------------------------------------------------
    # Cab: the expensive, comfortable option. ₹120 buys two legs.
    # ------------------------------------------------------------------
    if re.search(r"\b(cab|taxi|uber|ola|blusmart|blu smart|indrive)\b", act):
        if 0 < offered < 60:
            return LLMProposal(
                narrative=(
                    f"The cab driver hears ₹{offered}, rolls up his window, and rejoins the "
                    "traffic queue. The queue does not move. Neither do you."
                ),
                proposed_time_mins=7,
                proposed_checkpoint_delta=0,
                proposed_battery_drain=0,
            )
        fare = offered if offered >= 60 else 120
        if fare > state.cash:
            return _cash_shortfall(state, fare, "cab driver")
        if fare >= 120:
            destination = after_next_cp['name']
            narrative = (
                f"You slide into a shared cab for ₹{fare}. The driver takes the flyover, "
                f"ignores three lane markings, and deposits you at {destination}. "
                "You have paid for two legs and one near-death experience."
            )
            delta, minutes = 2, 13
        else:
            destination = next_cp['name']
            narrative = (
                f"₹{fare} gets you a short shared-cab hop. The driver drops you at "
                f"{destination} with the radio still playing a debate about potholes."
            )
            delta, minutes = 1, 9
        return LLMProposal(
            narrative=narrative,
            proposed_time_mins=minutes,
            proposed_cash_cost=fare,
            payment_or_phone_used=phone_used,
            proposed_battery_drain=2 if phone_used else 0,
            proposed_checkpoint_delta=delta,
        )

    # ------------------------------------------------------------------
    # Auto: the classic ₹650 opening quote. Real money moves the meter.
    # Also the landing spot for bargaining verbs and bare money offers
    # ("offer ₹450"), which are auto haggles in this game's fiction.
    # ------------------------------------------------------------------
    if AUTO_HAGGLE_REGEX.search(act) or MONEY_VERB_RE.search(act):
        if offered == 0:
            return LLMProposal(
                narrative=(
                    f"'Airport? ₹650 fixed.' The driver at {cur_cp['name']} studies your backpack "
                    "and the traffic with equal disdain. 'Meter? Sir, the meter is also stuck at "
                    "Silk Board.' He taps the roof and waits for a real number."
                ),
                proposed_time_mins=8,
                proposed_checkpoint_delta=0,
                payment_or_phone_used=phone_used,
                proposed_battery_drain=1 if phone_used else 0,
                npc_memory_note=f"An auto driver at {cur_cp['name']} refused the meter and did not blink.",
            )
        if offered < 300:
            return LLMProposal(
                narrative=(
                    f"₹{offered}? The driver laughs so hard he has to wipe his eyes. "
                    "He points at the jam, then at the sky, then drives eleven feet forward "
                    "before the light turns red again."
                ),
                proposed_time_mins=7,
                proposed_checkpoint_delta=0,
                proposed_battery_drain=0,
                npc_memory_note=f"An auto driver at {cur_cp['name']} laughed off a lowball offer.",
            )
        if offered > state.cash:
            return _cash_shortfall(state, offered, "auto driver")
        if offered < 450:
            return LLMProposal(
                narrative=(
                    f"The driver pockets ₹{offered} without ceremony and takes the service lane. "
                    f"Twenty minutes of brake lights later, you are at {next_cp['name']}."
                ),
                proposed_time_mins=11,
                proposed_cash_cost=offered,
                proposed_checkpoint_delta=1,
                proposed_battery_drain=2 if phone_used else 0,
                npc_memory_note=f"An auto driver at {cur_cp['name']} took you one leg and kept the change.",
            )
        return LLMProposal(
            narrative=(
                f"₹{offered} and the driver becomes a different man. He takes the flyover, "
                f"the shoulder, and one shortcut through a petrol station. You reach "
                f"{after_next_cp['name']} with your knees still attached."
            ),
            proposed_time_mins=14,
            proposed_cash_cost=offered,
            proposed_checkpoint_delta=2,
            proposed_battery_drain=2 if phone_used else 0,
            npc_memory_note=f"An auto driver at {cur_cp['name']} drove like the ORR belonged to him.",
        )

    # ------------------------------------------------------------------
    # Walking: free, honest, and slow. Rain makes it slower.
    # ------------------------------------------------------------------
    if re.search(r"\b(walk|walking|on foot|run|running|sprint|jog|foot it|leg it)\b", act):
        walk_minutes = 18 if state.is_raining else 15
        return LLMProposal(
            narrative=(
                f"You abandon wheels and walk. {cur_cp['name']} gives way to a stretch of "
                f"sidewalk, a flooded drain, and one very determined street dog. "
                f"{walk_minutes} minutes later you are at {next_cp['name']}, slightly damper but free."
            ),
            proposed_time_mins=walk_minutes,
            proposed_checkpoint_delta=1,
            proposed_battery_drain=0,
        )

    # ------------------------------------------------------------------
    # Hitchhiking / traffic cop: a 60% deterministic coin flip.
    # ------------------------------------------------------------------
    if re.search(r"\b(hitch|hitchhike|thumb|traffic cop|constable|cop|police)\b", act):
        if _seeded_roll(state.session_id, state.turn, "hitch", threshold=60):
            return LLMProposal(
                narrative=(
                    f"A delivery rider takes pity on you at {cur_cp['name']} and waves you onto "
                    f"the back of his scooter. He drops you at {next_cp['name']} before you can "
                    "properly thank him, which is probably for the best."
                ),
                proposed_time_mins=12,
                proposed_checkpoint_delta=1,
                proposed_battery_drain=0,
            )
        return LLMProposal(
            narrative=(
                "You stick out your thumb. A luxury SUV slows down, considers your existence, "
                "and then accelerates. The traffic cop nearby pretends not to see either of you."
            ),
            proposed_time_mins=9,
            proposed_checkpoint_delta=0,
            proposed_battery_drain=0,
        )

    # ------------------------------------------------------------------
    # Waiting / charging / thinking: time passes, nothing moves.
    # ------------------------------------------------------------------
    if re.search(
        r"\b(wait|waiting|charge|charging|rest|sit|breathe|think|look around|stand|"
        r"check the time|check time)\b",
        act,
    ):
        return LLMProposal(
            narrative=(
                f"You wait at {cur_cp['name']} and watch the city do its evening impression of a "
                "parking lot. Somewhere, a horn honks in a key you did not know existed."
            ),
            proposed_time_mins=7,
            proposed_checkpoint_delta=0,
            proposed_battery_drain=0,
        )

    # ------------------------------------------------------------------
    # Unknown action: no progress, just time. Silence is expensive, but
    # acknowledge the attempt so a paraphrase never feels ignored.
    # ------------------------------------------------------------------
    if "?" in action or QUESTION_REGEX.search(act):
        return LLMProposal(
            narrative=(
                f"You ask around at {cur_cp['name']}. A chai vendor considers the question, "
                "pours you a glass, and says the airport is 'just there, sir' while pointing at "
                "forty-five kilometres of brake lights. Eight minutes join the jam."
            ),
            proposed_time_mins=8,
            proposed_checkpoint_delta=0,
            proposed_battery_drain=0,
        )
    return LLMProposal(
        narrative=(
            f"You try it at {cur_cp['name']}. The city absorbs the idea without comment: a cab "
            "driver adjusts his mirror, a pedestrian steps around you, and eight minutes of your "
            "life join the collective traffic jam."
        ),
        proposed_time_mins=8,
        proposed_checkpoint_delta=0,
        proposed_battery_drain=0,
    )


# ---------------------------------------------------------------------------
# 6. FASTAPI ENDPOINTS (WITH HMAC STATELESS CONTINUITY)
# ---------------------------------------------------------------------------
def _response_payload(state: GameState) -> dict:
    return {
        "state": state,
        "state_signature": sign_state(state),
        "clock": format_clock(state.elapsed_mins),
        "checkpoints": CHECKPOINTS,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm_mode": llm_mode(),
        "max_turns": MAX_TURNS,
        "active_sessions": len(SESSIONS),
    }


@app.post("/api/start")
async def start_game(request: Request):
    _rate_limit(request, "start", "START_RATE_LIMIT", 15)
    session_id = str(uuid.uuid4())
    state = GameState(session_id=session_id)
    state.active_situation = _authoritative_situation(state, moved=False)
    _schedule_next_event(state)
    SESSIONS[session_id] = state
    SESSION_LOCKS[session_id] = asyncio.Lock()
    prune_sessions()
    return _response_payload(state)


@app.post("/api/turn")
async def play_turn(req: ActionRequest, request: Request):
    _rate_limit(request, "turn", "TURN_RATE_LIMIT", 30)
    # Serialise turns per session so a double-click cannot apply two mutations
    # against the same snapshot.
    lock = SESSION_LOCKS.setdefault(req.session_id, asyncio.Lock())
    async with lock:
        state = SESSIONS.get(req.session_id)

        # Serverless cold-start / restart recovery via HMAC-verified client state.
        if state is None and req.client_state and req.state_signature:
            recovered = req.client_state
            if recovered.session_id != req.session_id:
                raise HTTPException(
                    status_code=403,
                    detail="TAMPER GUARDRAIL: client state belongs to another session.",
                )
            if not verify_state_signature(recovered, req.state_signature):
                raise HTTPException(
                    status_code=403,
                    detail="TAMPER GUARDRAIL: client state signature mismatch detected.",
                )
            state = recovered
            SESSIONS[req.session_id] = state
            prune_sessions()

        if not state:
            raise HTTPException(status_code=404, detail="Session expired. Click RESET to start.")

        if state.status != "PLAYING":
            return _response_payload(state)

        action_text = req.action.strip()
        if not action_text:
            raise HTTPException(status_code=400, detail="Type an action first.")

        # Never send obvious prompt injections to a paid LLM endpoint.
        if CHEAT_REGEX.search(action_text):
            proposal = deterministic_fallback_narrator(state, action_text)
        else:
            proposal = await call_llm(state, action_text)

        working_state = state.model_copy(deep=True)
        updated_state = reconcile_turn(working_state, action_text, proposal)
        SESSIONS[req.session_id] = updated_state
        SESSIONS.move_to_end(req.session_id)
        return _response_payload(updated_state)


# The static directory is part of the game; create it so import never crashes
# on a fresh checkout, then mount it by absolute path.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_ui():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=500,
            detail="static/index.html is missing. Restore the game UI file.",
        )
    return FileResponse(index_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )
