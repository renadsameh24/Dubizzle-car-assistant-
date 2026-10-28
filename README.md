# dubizzle car assistant

A local shopping assistant for the sample dubizzle inventory. It answers in plain language, keeps hard limits exact, and does not invent cars.

## Setup

Install [uv](https://docs.astral.sh/uv/), then from this folder:

```bash
uv sync
cp .env.example .env
```

Put your Google AI Studio key in `.env`:

```bash
GEMINI_API_KEY=your_key_here
```

The cleaned inventory is `data/cars.xlsx`. Listing embeddings are already in `data/listing_embeddings.npz`, so a reviewer can rank cars without embedding all 100 ads again. A new question still calls the embedding API once. Rebuild the file only if the spreadsheet changes:

```bash
uv run python -c "from dubizzle_car_assistant.retrieval.semantic import build_embedding_index; build_embedding_index()"
```

## Run

Start the API:

```bash
uv run uvicorn dubizzle_car_assistant.main:app --reload
```

In a second terminal, start the page:

```bash
uv run streamlit run app.py
```

Open the local URL Streamlit prints, usually `http://localhost:8501`.

A new customer types their name. That name is how they come back. The page does not use cookies, and it does not ask anyone to remember a generated id. A returning customer types the same name, then continues a saved chat or starts an empty one. A new chat does not copy old messages. The name, preferences, liked cars, and bookings stay in `data/memory.sqlite` and are still there after the API restarts. A name is a label for this local demonstration, not secure authentication: anyone who types it opens that profile. Two people cannot save the same name. Qualified leads are appended to `leads.csv`. Neither file is committed.

Bookings made before a visitor id was stored have a null `user_id`. They are not attached to a customer. A shopper needs to book again for the booking to follow their name.

## How a question is answered

1. Gemini may call `search_cars`. A stated budget, make, origin, year, or mileage is a hard filter. Those limits stay for the rest of that conversation until the shopper changes or removes one. They are not saved as a permanent preference. Phrases such as "in your inventory", "overall", and "all cars" start a fresh search of the whole catalog. "Only Toyotas" keeps the other limits. A follow-up about one car, such as its price or what the advertisement says, looks up that listing and does not run a new search.
2. SQLite applies those filters. The inventory database is built once per process from the spreadsheet and reused. A missing price does not count as under a budget. A missing mileage does not count as under a mileage cap. `origin` such as `japanese` expands to the makes in the sheet. Known typos such as `toyta` or `40k` are normalized before the filter.
3. Cars that satisfy every limit with a known value are **confirmed matches**. Cars that match the other limits but are missing the price or mileage are **potential matches**. A potential match is not described as under the budget. A strict request for confirmed prices returns only the confirmed list. The page shows a few potential matches and the shopper can ask to see more.
4. The confirmed cars are ranked with cached Gemini embeddings, plus a small local adjustment for the words in this question. If someone says a car should not be expensive but gives no number, the search keeps cars with a listed price of 50,000 AED or less and says that this cutoff was applied. When a budget is in force, the shown confirmed cars are ordered by price.
5. Gemini writes the reply from those rows. A feature is mentioned only when the advertisement states it, and the card labels that sentence as an advertiser claim. The quote is a whole phrase from that listing, with chassis numbers, phone numbers, and dealer boilerplate left out. If the reply names a car that was not retrieved, it is replaced with the confirmed and potential lists. Questions such as how many cars have both a price and a mileage, and comparisons such as the cheapest listed car, are answered from SQL. A busy or failed model call is retried briefly, then the page asks the shopper to try again instead of inventing a car.
6. The page shows the same cars as cards, using each listing's own `photo_url`. An empty search clears the cards for that reply.

Viewing times are checked in code, in Asia/Dubai. Phrases such as "next Saturday at 9 AM" are resolved from the shopper's words, not from a date the model invents. "Next weekend" is treated as ambiguous and the assistant asks which day. A viewing can be booked Monday to Saturday, from 08:00 up to but not including 20:00, and only for a time that has not already passed. Several listing ids in one request are booked separately, and a booking is written to SQLite before the assistant is allowed to say it is booked. A later change such as "make it 2 PM" or "move it to Monday" updates that saved row, and "what did I book?" is read back from it. This is a simulation, not a message to a dubizzle branch.

A budget the shopper already stated stays in force on later searches until they name a new one or clearly drop it. "That car" after a search with no results asks which listing, instead of booking an older one.

A lead is written to `leads.csv` only when the customer's saved name, a maximum price, and a need are all present. The same lead is not written twice.

## Tests

```bash
uv run pytest
```

The default tests do not call Gemini. They check budgets, confirmed and potential matches, missing prices and mileage, Japanese cars from 2019 under 50,000 AED, search limits that carry across messages, viewing hours, stored bookings, leads, and a busy-model reply.

## Design choices

SQL runs before similarity so a budget cannot be overruled by a nearby embedding. The embedding index is a local file because this catalog has about 100 ads. A vector database would not change the result. Dealer descriptions share words such as "comfort", so similarity alone often ranks the same cars. The local adjustment uses the current question to separate an everyday request from a sports-car request without a new embedding model.

Only 13 ads contain a sale price and 39 contain an odometer reading. 12 have both. Monthly payments, service-plan distances, and top speeds are not treated as those values. Unknown stays unknown. The assistant does not estimate a market price.

Short-term memory is the conversation, including its search limits and messages. Long-term memory is the customer name in SQLite: preferences, liked cars, and bookings. The last saved preference is the one the assistant should follow. A search limit from the current chat is not copied into that preference list.

## Limitations

- Most listings have no sale price, so a budget search is short on purpose.
- Market estimates from the web are not mixed into `price_aed`. An outside price is not this listing's asking price.
- The assistant can book a simulated viewing, save a lead, and remember a preference. It cannot cancel a booking or watch the market.
- Web price estimates, Gemini Vision color detection, automated browser recognition of the live site, and LLM-based listing ingestion are future work. They are not part of this prototype.
- Gemini can be slow or return HTTP 503. The page asks the shopper to try again instead of showing a server error.
- Photos come from the URL in the spreadsheet. A broken link is left as unavailable rather than replaced with another car.
