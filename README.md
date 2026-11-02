# grok-desk

Private Solana memecoin desk. One always-on Python process watches PumpPortal migrations, audits them, and keeps a **local paper book**. The phone is a static page that polls JSON. Telegram is how you pause, switch mode, and tap Bought or Skip.

The desk does not place orders, does not hold a wallet, and does not ask for a FOMO password.

## Non-negotiables

- FOMO links are built only in `desk/links.py`. Every link keeps the referral parameter from that module. Nothing in the repo shortens or redirects them.
- These stay in the environment, never in git, `web/`, `logs/`, JSON, or GitHub Pages: `TYPESAFE_API_KEY`, `TG_TOKEN`, `VAPID_PRIVATE_KEY`, `APP_TOKEN`, `DATA_PATH`, `RPC_URL`.
- Plan B only. The process does not open a public port. It writes JSON under `docs/d/<DATA_PATH>/`.
- `DATA_PATH` is a secret of 32+ characters. Bare `/d/` and any other path 404. Pages does not list directories.
- Analyst mode is `rules`. Notifier is Telegram. Handle is `chillem122`. Referral discount is `0.10`. RPC comes from `RPC_URL`.

## Layout

```
desk/                 the process
  main.py             asyncio entry
  config.py           env + Step 10 defaults
  fields.py           verified JSON paths
  db.py               SQLite WAL
  bus.py              in-process JSON bus
  links.py            the only FOMO URL builder
  fees.py             FOMO Solana fee bands
  agents/             scanner, auditor, analyst, executor, risk
  telegram.py         long poll
  api.py              Plan B JSON writer
  report.py
web/                  phone UI (source)
docs/                 GitHub Pages root (same UI + published JSON)
tests/
.env.example
```

## Run it

Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Generate the two secrets on the machine that will run the desk. Do not paste them into the repo, a PR, or a chat.

```bash
python -c "import secrets; print(secrets.token_hex(24))"    # DATA_PATH, 48 hex chars
python -c "import secrets; print(secrets.token_urlsafe(32))" # APP_TOKEN, optional
```

Put them in `.env` with `TG_TOKEN` (BotFather) and `RPC_URL` (Helius HTTPS URL). Leave `PAGES_PUSH=0` until this clone is the one Pages serves.

```bash
python -m desk.main
```

That process is the whole desk:

- Scanner subscribes to PumpPortal `subscribeMigration` only.
- Auditor, analyst, executor, and risk follow on the bus.
- Telegram long-polls `getUpdates`.
- The writer rewrites JSON about every 15 seconds.

SQLite lives at `data/desk.sqlite` (WAL). Logs go to `logs/desk.log` as event types, not tokens.

`LOCAL_PREVIEW=1` can serve `docs/` on `127.0.0.1` only. Leave it off on the always-on host. `0.0.0.0` is refused.

## First phone open

After Pages is publishing (below), open this once:

```
https://<pages-host>/<repo>/#d=<DATA_PATH>
```

If you set `APP_TOKEN`, add it in the same fragment:

```
#d=<DATA_PATH>&t=<APP_TOKEN>
```

The page stores those values in `localStorage` and strips the fragment from the address bar. Later visits use the stored path. The token is never written into a JSON file and is never appended to a fetch. The unguessable path is what keeps the files unlisted.

## Telegram

The first private `/start` becomes the owner. Later chats are ignored.

| Command | What it does |
| --- | --- |
| `/buy <mint>` | Size and record a local buy at the last price |
| `/sell <mint>` | Close the local position at the last price |
| `/why <mint>` | Audit layers and the rules verdict |
| `/pause` | Toggle new entries. Open stops keep running |
| `/set <key> <value>` | Change a Step 10 setting |
| `/stack` | Open positions |
| `/status` | Mode, equity, heartbeats |
| `/paper` | Paper book |
| `/live` | Wait for Bought before recording a position |
| `/invite` | Signup and profile links from `links.py` |
| `/report` | Session report |

Paper buy/sell pushes start with `PAPER · `. Buttons are **Buy on FOMO**, **Bought**, and **Skip**. The URL on the button is `links.coin_url`. Bought on a live signal records the fill locally at the signal price. Skip drops it. Neither sends a transaction.

`/set stop_pct` (and trail / timeout) does not loosen a stop already stored on an open position. A tighter timeout can pull a deadline forward. A wider one cannot push it out. The trail stop only moves up.

## What the agents do

**Scanner.** Migration websocket, then DexScreener in batches. PumpPortal `pool` is a type string (`raydium-cpmm`, `pump-amm`, …), not an address. The pool address is the DexScreener `pairAddress`, and only `dexId == pumpswap` continues. Kill order, first hit wins: liquidity under $40k, top 10 holders over 25% (pool vault excluded), 1h change over +80%, copycat. A coin younger than `min_age_sec` waits. The waiting room expires at `waiting_room_max_sec`.

**Auditor.** Eight layers, stop at the first red. Mint authority, freeze authority, token program (Token or Token-2022 `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`), market pubkey equals the pair and type is PumpSwap, LP lock, top holders, insider graph, then `getProgramAccounts` on both token programs. The RugCheck graph is read from `links`, not `edges`. `RPC_URL` has to allow `getAccountInfo` and `getProgramAccounts`. If a call fails, that layer is red.

**Analyst.** Rules only: alive (5-minute trades and volume), organic (buys and sells, not one-sided), flush (5-minute drop), chase (1-hour extension). The gate needs all four in the safe direction and a score of at least 60.

**Executor.** Position size is equity × risk% / stop%, capped by `max_position_usd` and cash after the FOMO fee. It rechecks liquidity and the 1h change before a new entry. Paper fills at the signal price.

**Risk.** Every 15 seconds. Stop, then trail, then timeout. A missed price poll does not reuse an old print to trip a stop.

Prices are written by the scanner about every 60s for the waiting room, by risk every 15s for open positions, and by the JSON writer about every 60s for the other coins on the phone.

## Fees

`desk/fees.py` uses the published FOMO ordinary-Solana bands (help center, compiled 2026-09-18):

| Notional | Fee |
| --- | --- |
| under $5 | $0.10 |
| $5 up to $47.50 | 2% |
| $47.50 up to $190 | $0.95 |
| $190 and up | 0.50% |

`fees_ref_discount` (default `0.10`) applies to the top band only, so 0.50% becomes 0.45%. The flat bands stay as published. The paper fill uses the observed market price, so the pool fee is already inside that price. FOMO shows the real fee before a person confirms a swap. This number is only for the local book.

## Step 10 defaults

Paper mode, not paused, rules analyst, Telegram, handle `chillem122`, discount `0.10`.

Scanner: min liquidity $40,000, top 10 cap 25%, 1h cap +80%, min age 180s, waiting room 900s.

Analyst: 8 trades and $1,000 volume in 5 minutes, buy ratio 0.35–0.80, flush at −12% in 5 minutes, chase at +40% in 1 hour, minimum score 60.

Book: $1,000 bankroll, 2% risk, $100 max position, $15 minimum, 3 open positions, 12% stop, 15% trail after +10%, 1 hour timeout.

Change them with `/set`. The Account tab shows the same values and does not edit them.

## Step 11 modes

- **paper** (default). Fill at the signal price on the local book. Pushes are prefixed `PAPER · `.
- **live**. Send the FOMO link and wait. A position opens only after Bought. Still no order and no wallet.

## GitHub Pages

The site is the `/docs` folder on `main`. JSON is not in the PR. The running process publishes it.

1. Push this repo and merge to `main`.
2. On GitHub open **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
4. Set **Branch** to `main` and **Folder** to `/docs`.
5. Save. The Pages URL is shown at the top of that settings page. For this repo it is `https://gunnerbeyer-coder.github.io/grok-desk/` once the repo is allowed to publish.
6. A private repo needs GitHub Pro, Team, or Enterprise for Pages. The published files are still fetched by URL. `DATA_PATH` is the lock. Do not put that path in the repo description, a README, or a commit message.
7. On the always-on clone, set `PAGES_PUSH=1` and `PAGES_PUSH_SEC=60` (or `15` if you want a commit every snapshot). The process `git add -f`s only `docs/d/<DATA_PATH>`, commits that path, and `git push origin HEAD`. It does not commit `.env` or other files. The clone needs a git identity and push access. `docs/d/*` is gitignored so a normal `git add` will not pick the secret path up by accident.
8. Open `https://<pages-host>/grok-desk/#d=<DATA_PATH>` once. Add `&t=<APP_TOKEN>` in the fragment when you use a token.
9. Leave `docs/d/` without an `index.html`. GitHub Pages does not list directories, so `/d/` and a wrong segment 404. `docs/404.html` is the not-found page.

The phone polls `state.json`, `chart.json`, `activity.json`, and `coin/<mint>.json` under that segment, with a cache-busting query. Telegram is the fast channel. Pages can lag by the push interval plus GitHub’s publish time.

`web/` is the UI source. `desk/api.py` copies it into `docs/` on startup (not into `docs/d/`). A test checks the committed copies match.

## Tests

```bash
pytest
```

Covers the FOMO link builder, the kill order and rules gate, stops that cannot loosen, paper fills at the signal price, the fee bands, the eight-layer audit, Plan B 404s, and a scan that refuses committed secrets.

## JSON the phone reads

`state.json` — mode, badge, balance, public settings, agent heartbeats, positions, coins, the latest audit beam.  
`chart.json` — `{t, p}` series for the canvas.  
`activity.json` — the feed.  
`coin/<mint>.json` — layers, verdict, candles, the FOMO url for that mint.

No key, token, RPC URL, or `DATA_PATH` is written into those files. If one would leak, the writer refuses the snapshot.
