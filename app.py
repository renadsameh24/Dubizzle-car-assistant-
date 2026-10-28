import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import streamlit as st

API_URL = "http://127.0.0.1:8000/chat"

st.set_page_config(page_title="dubizzle car assistant")
st.title("dubizzle car assistant")

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "messages" not in st.session_state:
    st.session_state.messages = []

user_id = st.sidebar.text_input("Visitor id", value="visitor-1").strip()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Ask about a car")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    payload = {"message": prompt, "session_id": st.session_state.session_id}
    if user_id:
        payload["user_id"] = user_id
    request = Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urlopen(request, timeout=90) as response:
            reply = json.load(response)["reply"]
    except HTTPError as error:
        reply = f"The car API returned HTTP {error.code}."
    except URLError:
        reply = "The car API is not running. Start it with uvicorn, then try again."
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()