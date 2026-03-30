# Project 03 — IT Asset Lifecycle Manager

## Pattern
**Function Calling — write-back to a system of record**

The agent manages IT hardware through natural language. Every action that
modifies an asset — check out, check in, flag for repair, retire — calls a
function that writes the change back to `cmdb.json`, a persistent mock CMDB.
The data survives session restarts. This is what separates this pattern from
a read-only function calling agent: the functions have real, lasting side effects.

---

## Architecture

```
User (CLI)
    │
    ▼
agent.py
    │  1. Create 7 FunctionTool definitions
    │  2. Create PromptAgentDefinition
    │  3. Create conversation thread
    │
    │  ── per turn ───────────────────────────────────────────────────
    │  4. User request (natural language)
    │  5. responses.create() → agent emits function_call items
    │
    │  ── function call loop ─────────────────────────────────────────
    │  6. dispatch_function_calls():
    │       ├─ search_assets()         → reads cmdb.json
    │       ├─ get_asset_details()     → reads cmdb.json
    │       ├─ checkout_asset()        ──┐
    │       ├─ checkin_asset()           │ writes cmdb.json
    │       ├─ flag_for_repair()         │ (persistent side effects)
    │       ├─ retire_asset()            │
    │       └─ create_procurement_request() ──┘
    │  7. Post FunctionCallOutput list with previous_response_id
    │  8. Agent confirms what changed
    └─────────────────────────────────────────────────────────────────

cmdb.json  (persists between sessions)
    ├── assets{}       10 pre-seeded assets across 4 categories
    └── procurement_requests[]
```

---

## The seven tools

| Tool | Read / Write | What it does |
|---|---|---|
| `search_assets` | Read | Find assets by keyword or status filter |
| `get_asset_details` | Read | Full record + history for one asset |
| `checkout_asset` | **Write** | Assign available asset to employee |
| `checkin_asset` | **Write** | Return asset to available inventory |
| `flag_for_repair` | **Write** | Mark as in_repair, log fault description |
| `retire_asset` | **Write** | Permanently retire, move to disposal |
| `create_procurement_request` | **Write** | Raise request for new hardware |

---

## Asset statuses

```
available  ──checkout──►  checked_out  ──flag──►  in_repair  ──checkin──►  available
    │                                                                           │
    └──────────────────────────── retire ─────────────────────────────────►  retired
```

---

## Pre-seeded CMDB

| Asset ID | Type | Make / Model | Status |
|---|---|---|---|
| LT-1001 | Laptop | Dell Latitude 5540 | available |
| LT-1002 | Laptop | Dell Latitude 5540 | checked_out (Sarah Mitchell) |
| LT-1003 | Laptop | Apple MacBook Pro 14 | in_repair (keyboard fault) |
| LT-1004 | Laptop | Lenovo ThinkPad X1 | retired |
| MN-2001 | Monitor | LG 27UK850-W 4K | available |
| MN-2002 | Monitor | Dell UltraSharp U2722D | checked_out (Sarah Mitchell) |
| PH-3001 | Mobile Phone | Apple iPhone 15 Pro | available |
| PH-3002 | Mobile Phone | Samsung Galaxy S24 | checked_out (Priya Nair) |
| DK-4001 | Docking Station | Dell WD22TB4 | available |
| DK-4002 | Docking Station | CalDigit TS4 | in_repair (USB-C fault) |

---

## Prerequisites

- Python 3.11+
- An Azure AI Foundry project with a **gpt-4.1** (or gpt-4o) model deployment
- Azure CLI logged in (`az login`)

---

## Setup

```bash
# 1. Create and activate virtual environment
python -m venv venv
source venv/Scripts/activate      # Windows
# source venv/bin/activate        # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env — fill in PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME
```

---

## Running

```bash
python agent.py
```

Changes to `cmdb.json` are written immediately when a tool executes. You can
open `cmdb.json` in an editor and watch it update in real time.

---

## Suggested requests to explore

| Request | Tools called | Side effect |
|---|---|---|
| Show me all available laptops | `search_assets` | None |
| Check out LT-1001 to Alice Chen, EMP-0201 | `get_asset_details` → `checkout_asset` | Status → checked_out |
| Sarah Mitchell is leaving — check in all her assets | `search_assets` → `checkin_asset` ×2 | Status → available ×2 |
| Flag LT-1002 for repair: screen has dead pixels | `get_asset_details` → `flag_for_repair` | Status → in_repair |
| We need 3 laptops for new engineering hires | `create_procurement_request` | PR written to CMDB |
| Retire DK-4002 — it's beyond repair | `get_asset_details` → `checkin_asset` → `retire_asset` | Status → retired |

---

## Key concepts illustrated

### Write-back pattern
Unlike read-only tools, these functions call `_save_cmdb()` after every mutation.
The agent cannot undo a write — if a user says "undo that checkout", the agent
must call `checkin_asset` to reverse it, just like a real system.

### Guard logic inside tools
Each write function validates preconditions before mutating state. For example,
`retire_asset` refuses if the asset is `checked_out`, and `checkout_asset` refuses
if the asset is not `available`. The agent receives a `success: false` result with
an explanation and can then decide how to proceed.

### Multi-step sequences
A request like "retire DK-4002" may trigger the agent to check its current status,
discover it is in_repair, check it in first, and then retire it — all within a
single conversational turn via multiple sequential function calls.

### Audit trail
Every write appends an entry to the asset's `history` array with the action, date,
actor, and note. This provides a full audit trail persisted in `cmdb.json`.

---

## License

[MIT](LICENSE)

---

## File structure

```
03-it-asset-lifecycle-manager/
├── agent.py                        # Agent wiring + function call loop
├── tools.py                        # CMDB functions + FUNCTION_MAP registry
├── cmdb.json                       # Persistent mock CMDB (modified at runtime)
├── prompts/
│   └── system_prompt.txt           # IT manager persona and decision guidelines
├── requirements.txt
├── .env.example                    # Template — copy to .env and fill in values
└── README.md
```
