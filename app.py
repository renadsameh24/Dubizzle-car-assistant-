import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

API = "http://127.0.0.1:8000"
SUGGESTIONS = [
    "Show me one family car under 50000 AED",
    "Any decent Japanese cars from 2019 onwards? Budget's around 50k.",
    "What is my name, and what kind of car did I say I want?",
]
LIMITATION = (
    "Your name is how you come back. It is a label in this demo, not a password. "
    "Anyone who types the same name opens the same saved profile. This page does not use cookies."
)

st.set_page_config(page_title="dubizzle car assistant", page_icon="🚗", layout="centered")

st.markdown(
    """
    <style>
    .brand-mark {
        color: #E00000;
        font-size: 2.4rem;
        font-weight: 800;
        letter-spacing: -0.05em;
        line-height: 1;
    }
    .brand-tag { color: #444; font-size: 1rem; }
    .brand-rule {
        height: 3px;
        width: 72px;
        background: #E00000;
        border-radius: 2px;
        margin: 0.5rem 0 1rem 0;
    }
    .card {
        border: 1px solid #ececec;
        border-radius: 12px;
        padding: 0.75rem 0.9rem 0.2rem 0.9rem;
        margin: 0.45rem 0 0.7rem 0;
        background: #fff;
    }
    footer { visibility: hidden; }
    [data-testid="stToolbar"] { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

for key, value in {
    "stage": "welcome",
    "user_id": "",
    "customer_name": "",
    "session_id": "",
    "messages": [],
    "pending": None,
    "awaiting": None,
    "conversations": [],
    "notice": "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = value


def api_message(body: dict) -> str:
    detail = body.get("detail")
    if isinstance(detail, list):
        return "Enter your name."
    if isinstance(detail, str) and detail:
        return detail
    message = body.get("message")
    if isinstance(message, str) and message:
        return message
    return "That request could not be completed."


def api(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        API + path,
        data=data,
        headers={"content-type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=75) as response:
            body = json.load(response)
            return body if isinstance(body, dict) else {"items": body}
    except HTTPError as error:
        raw = error.read().decode()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": f"The car API returned HTTP {error.code}."}
        if isinstance(parsed, dict):
            parsed["http_status"] = error.code
            return parsed
        return {"detail": f"The car API returned HTTP {error.code}.", "http_status": error.code}
    except TimeoutError:
        return {"detail": "The assistant is taking too long. Please try that question again."}
    except URLError:
        return {"detail": "The car API is not running. Start it with uvicorn, then try again."}


def api_list(path: str) -> dict:
    request = Request(API + path)
    try:
        with urlopen(request, timeout=75) as response:
            return {"items": json.load(response)}
    except HTTPError as error:
        raw = error.read().decode()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": f"The car API returned HTTP {error.code}."}
        parsed["http_status"] = error.code
        return parsed
    except URLError:
        return {"detail": "The car API is not running. Start it with uvicorn, then try again."}


def brand() -> None:
    st.markdown('<div class="brand-mark">dubizzle</div>', unsafe_allow_html=True)
    st.markdown('<div class="brand-tag">Car shopping assistant</div>', unsafe_allow_html=True)
    st.markdown('<div class="brand-rule"></div>', unsafe_allow_html=True)


def enter_chat(session_id: str, messages: list[dict]) -> None:
    st.session_state.session_id = session_id
    st.session_state.messages = messages
    st.session_state.pending = None
    st.session_state.awaiting = None
    st.session_state.stage = "chat"


def money(value: int | None) -> str:
    if value is None:
        return "Price not listed"
    return f"{value:,} AED"


def distance(value: int | None) -> str:
    if value is None:
        return "Mileage not listed"
    return f"{value:,} km"


def render_cards(listings: list[dict]) -> None:
    confirmed = [car for car in listings if car.get("match_kind", "confirmed") == "confirmed"]
    potential = [car for car in listings if car.get("match_kind") == "potential"]
    inventory = [car for car in listings if car.get("match_kind") == "inventory"]
    if confirmed:
        st.markdown("**Confirmed matches**")
        _render_card_list(confirmed)
    if potential:
        st.markdown("**Potential matches — price or mileage needs confirmation**")
        st.caption("These match the other requirements. A missing price or mileage means they are not confirmed for that limit.")
        _render_card_list(potential)
    if inventory:
        st.markdown("**Listings**")
        _render_card_list(inventory)
    if not confirmed and not potential and not inventory:
        _render_card_list(listings)


def _render_card_list(listings: list[dict]) -> None:
    for car in listings:
        photo = car.get("photo_url") or ""
        st.markdown('<div class="card">', unsafe_allow_html=True)
        image, facts = st.columns([1, 2])
        with image:
            if photo.startswith("http"):
                st.image(photo, width="stretch")
            else:
                st.caption("Photo unavailable")
        with facts:
            st.markdown(f"**{car['year']} {car['make'].title()} {car['model'].title()}**")
            st.caption(f"Listing {car['listing_id']} · {(car.get('trim') or '').title()}")
            st.write(f"{money(car.get('price_aed'))} · {distance(car.get('mileage_km'))}")
            if car.get("reason"):
                st.caption(car["reason"])
            if car.get("evidence"):
                st.caption(car["evidence"])
        st.markdown("</div>", unsafe_allow_html=True)


def ask(message: str) -> dict:
    body = api(
        "POST",
        "/chat",
        {
            "message": message,
            "session_id": st.session_state.session_id,
            "user_id": st.session_state.user_id,
        },
    )
    if body.get("reply"):
        return body
    return {"reply": api_message(body), "listings": []}


def welcome() -> None:
    brand()
    st.write("Welcome to dubizzle! Are you a new or returning customer?")
    st.caption(LIMITATION)
    new_customer, returning = st.columns(2)
    with new_customer:
        if st.button("I'm new", width="stretch"):
            st.session_state.stage = "new"
            st.session_state.notice = ""
            st.rerun()
    with returning:
        if st.button("I'm returning", width="stretch"):
            st.session_state.stage = "returning"
            st.session_state.notice = ""
            st.rerun()


def new_customer() -> None:
    brand()
    st.write("What is your name? You will type this same name when you come back.")
    st.caption(LIMITATION)
    name = st.text_input("Your name", key="new_name")
    if st.button("Start", width="stretch"):
        created = api("POST", "/visitors", {"name": name.strip()})
        if not created.get("ok"):
            st.error(api_message(created))
            return
        started = api("POST", "/conversations", {"user_id": created["user_id"]})
        if not started.get("session_id"):
            st.error(api_message(started))
            return
        st.session_state.user_id = created["user_id"]
        st.session_state.customer_name = created["name"]
        st.session_state.notice = f"Welcome, {created['name']}. Come back later by typing this name."
        enter_chat(started["session_id"], [])
        st.rerun()
    if st.button("Back"):
        st.session_state.stage = "welcome"
        st.rerun()


def returning() -> None:
    brand()
    st.write("Welcome back. What name did you use?")
    st.caption(LIMITATION)
    name = st.text_input("Your name", key="returning_name")
    if st.button("Continue", width="stretch"):
        opened = api("POST", "/visitors/open", {"name": name.strip()})
        if not opened.get("ok"):
            st.error(api_message(opened))
        else:
            st.session_state.user_id = opened["user_id"]
            st.session_state.customer_name = opened["name"]
            st.session_state.conversations = opened.get("conversations") or []
            st.session_state.notice = f"Saved bookings for {opened['name']}: {opened.get('booking_count', 0)}."
            st.session_state.stage = "choose"
            st.rerun()
    if st.button("Back"):
        st.session_state.stage = "welcome"
        st.rerun()


def choose() -> None:
    brand()
    st.write(f"Welcome back, {st.session_state.customer_name}")
    if st.session_state.notice:
        st.info(st.session_state.notice)
    st.write("Continue a previous chat, or start a new one. A new chat is empty. Saved preferences, liked cars, and bookings stay on this visitor.")
    if st.button("Start a new chat", width="stretch"):
        started = api(
            "POST",
            "/conversations",
            {"user_id": st.session_state.user_id},
        )
        if not started.get("session_id"):
            st.error(api_message(started))
            return
        st.session_state.notice = "This is a new conversation. Older messages were not copied here."
        enter_chat(started["session_id"], [])
        st.rerun()
    chats = st.session_state.conversations
    if not chats:
        st.caption("No previous chats are saved for this visitor yet.")
    for chat in chats:
        preview = chat.get("preview") or "Empty conversation"
        label = f"{chat.get('updated_at') or 'Saved'} — {preview}"
        if st.button(label, key=chat["session_id"], width="stretch"):
            query = urlencode({"user_id": st.session_state.user_id})
            loaded = api("GET", f"/conversations/{chat['session_id']}?{query}")
            messages = loaded.get("messages")
            if not isinstance(messages, list):
                st.error(api_message(loaded))
                return
            st.session_state.notice = "Continued the saved chat. These messages were loaded from the database."
            enter_chat(
                chat["session_id"],
                [
                    {"role": message["role"], "content": message["content"], "listings": []}
                    for message in messages
                ],
            )
            st.rerun()
    if st.button("Use a different visitor"):
        st.session_state.stage = "welcome"
        st.session_state.user_id = ""
        st.session_state.customer_name = ""
        st.session_state.messages = []
        st.session_state.conversations = []
        st.rerun()


def chat() -> None:
    with st.sidebar:
        st.markdown("### This shopper")
        st.write(st.session_state.customer_name)
        st.caption(LIMITATION)
        st.caption(f"This conversation: {st.session_state.session_id[:8]}")
        if st.button("Start a new chat", width="stretch"):
            started = api("POST", "/conversations", {"user_id": st.session_state.user_id})
            if started.get("session_id"):
                st.session_state.notice = "This is a new conversation. Older messages were not copied here."
                enter_chat(started["session_id"], [])
                st.rerun()
            else:
                st.error(api_message(started))
        if st.button("Saved chats", width="stretch"):
            opened = api("POST", "/visitors/open", {"name": st.session_state.customer_name})
            if opened.get("ok"):
                st.session_state.conversations = opened.get("conversations") or []
                st.session_state.stage = "choose"
                st.rerun()
    brand()
    if st.session_state.notice:
        st.info(st.session_state.notice)
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_cards(message.get("listings") or [])
    if st.session_state.awaiting:
        with st.status("Searching the inventory...", expanded=True) as status:
            st.write("Checking the listings that match this question.")
            body = ask(st.session_state.awaiting)
            status.update(label="Preparing the reply", state="complete")
        reply = str(body.get("reply") or "").strip()
        listings = body.get("listings") or []
        if not reply:
            reply = "Here are the matching cars." if listings else "I couldn't complete that. Please try the question again."
        st.session_state.messages.append(
            {"role": "assistant", "content": reply, "listings": listings}
        )
        st.session_state.awaiting = None
        st.rerun()
    if not st.session_state.messages:
        st.caption("Choose a starting question, or type your own. This conversation does not yet contain older messages.")
        for suggestion in SUGGESTIONS:
            if st.button(suggestion, width="stretch"):
                st.session_state.pending = suggestion
                st.rerun()
    if st.session_state.awaiting is None:
        typed = st.chat_input("Ask about a car, a viewing, or your saved preferences")
        prompt = st.session_state.pending or typed
        if prompt:
            st.session_state.pending = None
            st.session_state.messages.append({"role": "user", "content": prompt})
            st.session_state.awaiting = prompt
            st.rerun()


if st.session_state.stage == "welcome":
    welcome()
elif st.session_state.stage == "new":
    new_customer()
elif st.session_state.stage == "returning":
    returning()
elif st.session_state.stage == "choose":
    choose()
else:
    chat()
