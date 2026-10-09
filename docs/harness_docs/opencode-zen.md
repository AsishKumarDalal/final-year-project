# OpenCode Zen Setup Guide

OpenCode Zen is OpenCode's own model gateway: one base URL, one API key, a
curated model list. This guide covers calling it as a plain HTTP API and
wiring it into this agent.

---

## 1. Get a key

Sign in at <https://opencode.ai/auth>, add billing, copy your key.

Keys are prefixed `oc_sk_`. Keep them out of git — this repo reads them from
`.env`, which is already gitignored. Never paste a key into a doc, a commit, or
a chat transcript.

---

## 2. Endpoint and auth

```
Base URL: https://opencode.ai/zen/v1
```

Zen speaks **five different wire formats**, and they do not share a header.
Picking the wrong one is the most common failure.

| Wire format | Path | Auth header |
|---|---|---|
| OpenAI Chat Completions | `POST /v1/chat/completions` | `Authorization: Bearer <key>` |
| OpenAI Responses | `POST /v1/responses` | `Authorization: Bearer <key>` |
| Anthropic Messages | `POST /v1/messages` | `x-api-key: <key>` (**not** Bearer) |
| Gemini generateContent | `POST /v1/models/<model>` | `x-goog-api-key: <key>` |
| Jev / SystemOne | `POST /v1/systemone` | `Authorization: Bearer <key>` |

Two things verified on a live key:

- `/v1/messages` rejects `Authorization: Bearer` with `401 Missing API key` and
  only accepts `x-api-key`. The Anthropic SDK sends `x-api-key` natively, so
  use `@ai-sdk/anthropic` as-is.
- `/v1/models/<model>` (Gemini) rejected the same key with `401 Invalid API
  key`, so treat the Gemini route as unverified — see §5.

Also available: `GET /v1/models` (list, 41 models at time of writing) and
`GET /doc`.

---

## 3. Free-tier caveat

Ten models return HTTP 403:

```json
{"type":"error","error":{"type":"FreeTierError",
 "message":"OpenCode's free tier can only be used from within OpenCode"}}
```

That means exactly what it says: those models are for the OpenCode TUI/CLI/Web
only, not for raw API calls from your own scripts.

**`space-bunny-free` is the one free model that works over the raw API.**

Verified (Oct 2026):

| Test | Result |
|---|---|
| `GET /v1/models` with key | `200`, 41 models |
| `space-bunny-free` on `/chat/completions` | `200`, plain completion |
| `space-bunny-free` tool calls on `/chat/completions` | `200`, real `tool_calls` returned |
| `space-bunny-free` on `/messages` | `200` (works on two protocols) |
| `space-bunny-free` on `/responses` | `400 ModelProtocolUnsupported` |
| other `*-free` models | `403 FreeTierError` |
| any paid model, zero balance | `402 Insufficient account funds` |

The `402` is a billing state, not a config error. Zen checks **auth → model
access → balance → protocol**, so on an unfunded account every paid model stops
at `402` and its real wire format cannot be observed.

---

## 4. Model reference (live list)

IDs below are exactly what `GET /v1/models` returned. The endpoint column for
paid models comes from the official docs, not from live calls — those calls
stopped at `402` before reaching the protocol check.

### Works today, free, no billing

| Model ID | Protocol | Notes |
|---|---|---|
| `space-bunny-free` | `/chat/completions`, `/messages` | verified end-to-end, incl. tool calls |

### Free but gated to inside OpenCode (`403`)

`big-pickle`, `fledge-alpha-free`, `ling-3.1-flash-free`,
`longcat-2.5-preview-free`, `mimo-v2.6-flash-free`,
`muse-spark-1.2-contributor-free`, `muse-spark-1.3-contributor-free`,
`nemotron-3.5-lightning-free`, `test`

### Paid — `/chat/completions` (OpenAI SDK compatible)

| Model ID | Input / 1M | Output / 1M |
|---|---|---|
| `deepseek-v4-flash` * | $0.14 | $0.28 |
| `deepseek-v4.1-flash` | $0.30 | $1.20 |
| `deepseek-v4-pro` | $1.74 | $3.48 |
| `glm-5.3-flash` | $0.15 | $0.50 |
| `glm-5.3` | $1.40 | $4.40 |
| `kimi-k2.7-code` | $0.95 | $4.00 |
| `kimi-k3` | $3.00 | $15.00 |
| `minimax-m3` | $0.30 | $1.20 |
| `qwen3.6-plus` | $0.50 | $3.00 |
| `qwen3.8-flash` | $0.15 | $0.47 |

`qwen3.6-plus` and `qwen3.8-flash` are listed as `/v1/messages` in the official
docs but returned `402` (not `400`) on `/chat/completions`, so they may accept
both. Confirm with credit before relying on it.

### Paid — `/v1/responses` (OpenAI Responses API)

`gpt-5.3-codex`, `gpt-5.3-codex-spark`, `gpt-5.4-mini`, `gpt-5.4-nano`,
`gpt-5.5`, `gpt-5.5-pro`, `gpt-5.6-terra`, `gpt-6-astra`, `gpt-6-luna`,
`gpt-6.1-sol`, `grok-4.7`, `grok-build-0.1`, `muse-spark-1.3`

### Paid — `/v1/messages` (Anthropic API, `x-api-key`)

`claude-fable-5-1`, `claude-haiku-4-5`, `claude-opus-5-5`,
`claude-sonnet-5-5`

### Paid — `/v1/models/<model>` (Gemini API)

`gemini-3.1-pro`, `gemini-3.5-flash-lite`, `gemini-3.8-flash`

### `/v1/systemone` (not a chat API)

`jev-1.13`, `jev-1.13-free`

> **Stale docs warning:** the official endpoint table lists several IDs that
> are *not* in the live `/v1/models` response — e.g. `deepseek-v4-flash` was
> rejected with `403 Model access is disabled`, and `glm-5.1`/`glm-5.2`,
> `kimi-k2.5`/`kimi-k2.6`, `minimax-m2.5`/`m2.7`, `qwen3.8-max`,
> `mimo-v2.5-free`, `nemotron-3-ultra-free`, `claude-sonnet-4.5` are absent.
> Always trust `GET /v1/models` over any published table.

---

## 5. Configure this agent

`agent/llm.py` uses the OpenAI SDK's **chat completions** endpoint, so it can
only reach models in the `/chat/completions` groups above.

Copy `.env.example` to `.env`:

```dotenv
BASE_URL=https://opencode.ai/zen/v1
API_KEY=oc_sk_your_key_here
MODEL=space-bunny-free
AGENT_STREAM=1
```

The client is built from those two vars (`agent/llm.py:19`):

```python
client = OpenAI(
    base_url=os.getenv("BASE_URL", "https://api.openai.com/v1"),
    api_key=os.getenv("API_KEY"),
)
```

Rules:

- `BASE_URL` must include `/v1` and must **not** include a trailing
  `/chat/completions` — the SDK appends that itself.
- `MODEL` must be an exact ID from `GET /v1/models`. Typos surface as
  `Model is not supported`.
- Until you add credit, `MODEL=space-bunny-free` is the only value that works
  end-to-end.

---

## 6. Smoke test

`test_zen.py` in the repo root checks the key, every free model, and tool
calling. Standard library only.

```powershell
$env:OPENCODE_API_KEY = "oc_sk_..."
python test_zen.py
```

Expected on a healthy, unfunded key:

```
[auth] OK - 41 models available
[ ok ] space-bunny-free: 'OK'  tokens={...}
[FAIL] fledge-alpha-free: FreeTierError: ...    <- expected, gated
[tool-call test] space-bunny-free
tool_calls: [{...  "name": "get_weather",  "arguments": "{\"city\":\"Paris\"}"}]
```

Manual check:

```bash
curl https://opencode.ai/zen/v1/chat/completions \
  -H "Authorization: Bearer $OPENCODE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"space-bunny-free","messages":[{"role":"user","content":"Say OK"}]}'
```

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403 FreeTierError` | free model called from outside OpenCode | use `space-bunny-free`, or the TUI |
| `402 Insufficient account funds` | zero balance | add credit, or use a free model |
| `403 Model access is disabled` | model retired or not on your plan | re-read `GET /v1/models` |
| `Model is not supported` | `MODEL` unset or typo | exact ID from `/v1/models` |
| `400 ModelProtocolUnsupported` | model hit on the wrong wire format | use the protocol from §4 |
| `401 Missing API key` on `/messages` | `Authorization: Bearer` used | send `x-api-key` instead |
| `401` on every request incl. `/models` | missing `User-Agent` | some HTTP clients are blocked by default; send a normal UA |
| `404` or provider-shaped error on a GPT/Claude/Gemini model | wrong wire format | those are `/responses`, `/messages`, `/models/<id>` — not `/chat/completions` |

---

## 8. Reference

- Model list: `GET https://opencode.ai/zen/v1/models`
- Official endpoint and pricing table: <https://opencode.ai/docs/zen/>
- Key management: <https://opencode.ai/auth>
- Related: `docs/architecture.md` for how this agent calls the LLM
