# How This Project Was Built — A Complete, Manual, Step-by-Step Guide

This document explains **every command and every file** in this project in
enough detail that you could recreate it from a blank folder without any
outside help — even if you've never built a Python project before.

It's written in the order things actually happened, including the mistakes
and fixes, because the mistakes teach you as much as the working code does.

---

## Table of Contents

1. [What we're building (the big picture)](#1-what-were-building-the-big-picture)
2. [Prerequisites](#2-prerequisites)
3. [Create the project folder and a virtual environment](#3-create-the-project-folder-and-a-virtual-environment)
4. [Declare dependencies and install them](#4-declare-dependencies-and-install-them)
5. [Set up config files (.gitignore, .env)](#5-set-up-config-files-gitignore-env)
6. [Salesforce setup: create an External Client App](#6-salesforce-setup-create-an-external-client-app)
7. [Write `sf_mcp/config.py` — loading settings](#7-write-sf_mcpconfigpy--loading-settings)
8. [Write `sf_mcp/salesforce_client.py` — talking to Salesforce](#8-write-sf_mcpsalesforce_clientpy--talking-to-salesforce)
9. [Write `sf_mcp/server.py` — the MCP server and its tools](#9-write-sf_mcpserverpy--the-mcp-server-and-its-tools)
10. [Write `sf_mcp/seed_data.py` — sample data generator](#10-write-sf_mcpseed_datapy--sample-data-generator)
11. [Write `agent.py` — the LLM client that drives the tools](#11-write-agentpy--the-llm-client-that-drives-the-tools)
12. [Running everything](#12-running-everything)
13. [Troubleshooting log — real errors we hit and how we fixed them](#13-troubleshooting-log--real-errors-we-hit-and-how-we-fixed-them)

---

## 1. What we're building (the big picture)

Three moving parts:

```
 +-------------+        stdio (stdin/stdout)       +--------------------+       HTTPS/REST      +-----------------+
 |  agent.py   |  <----------------------------->  |  sf_mcp/server.py  |  <----------------->  |   Salesforce    |
 | (MCP client |   MCP protocol: list_tools(),      |  (MCP server)      |   OAuth + SOQL query   |   org (Opps,    |
 |  + Groq LLM)|    call_tool(name, args)           |  3 tools defined   |                        |   Accounts...)  |
 +-------------+                                    +--------------------+                        +-----------------+
        |
        | chat completion + "tools" list
        v
 +-------------+
 |  Groq API   |  (hosts an open LLM that supports "tool calling" /
 |  (the LLM)  |   "function calling")
 +-------------+
```

- **MCP** (Model Context Protocol) is just a standard way for a program (a
  "server") to expose a list of callable **tools** — each with a name, a
  description, and a JSON schema for its arguments — to any client that
  speaks the protocol. It's transport-agnostic; here we use the simplest
  transport, **stdio**: the client launches the server as a subprocess and
  talks to it by writing/reading JSON messages over its stdin/stdout pipes.
- **Salesforce** is the data source. We talk to it over its plain REST API
  using SOQL (Salesforce's SQL-like query language) — no special SDK
  required, just HTTP requests.
- **Groq** hosts open-weight LLMs (like `openai/gpt-oss-120b`) behind an API
  that is wire-compatible with OpenAI's chat completions API, including
  "tool calling": you send the model a list of tool definitions, and the
  model can respond by asking to call one, instead of answering directly.

`agent.py` is the glue: it starts the MCP server, asks it "what tools do you
have?", hands that list to the LLM, and then loops: the LLM asks to call a
tool → we actually call it against live Salesforce data → we feed the result
back to the LLM → repeat until the LLM has enough information to write a
real, synthesized answer.

---

## 2. Prerequisites

- **Python 3.10+** installed and on your PATH. Check with:
  ```
  python --version
  ```
- **A Salesforce org** you're allowed to modify (a free Developer Edition
  org is perfect — sign up at developer.salesforce.com if you don't have
  one). Never point this kind of project at a production org with real
  customer data.
- **A Groq API key** — free at console.groq.com.
- A terminal. This guide uses PowerShell syntax for Windows-specific
  commands and plain shell syntax elsewhere; the Python code itself is
  identical on any OS.

---

## 3. Create the project folder and a virtual environment

```powershell
mkdir C:\Users\Admin\sf-opportunity-mcp
cd C:\Users\Admin\sf-opportunity-mcp
mkdir sf_mcp
```

**What is `sf_mcp`?** It's a Python **package** — just a folder that
contains an `__init__.py` file, which tells Python "treat this folder as a
package you can `import`." We'll put all our Salesforce/MCP logic inside
it, and keep the two "entry point" scripts (the MCP server's own runner and
the LLM agent) as separate top-level files.

```powershell
python -m venv .venv
```

**What is a virtual environment?** `.venv` becomes its own private copy of
the Python interpreter and package folder, isolated from the rest of your
system. Anything you `pip install` while it's active only exists inside
`.venv` — it can't collide with other projects' dependencies, and if you
mess it up, you delete the folder and start over instead of breaking your
whole machine's Python.

Activate it (every new terminal session, you must re-activate):

```powershell
.venv\Scripts\Activate.ps1
```

(If PowerShell blocks script execution, you can instead call the venv's
Python binary directly without activating, which is what we did throughout
this build: `.venv\Scripts\python.exe <command>`.)

Upgrade pip (the tool that installs packages) — old pip versions
occasionally fail on newer package metadata:

```powershell
.venv\Scripts\python.exe -m pip install --upgrade pip
```

---

## 4. Declare dependencies and install them

Create `requirements.txt`:

```
mcp>=1.28.0
requests>=2.32.0
python-dotenv>=1.0.1
groq>=0.11.0
```

What each one is for:

| Package | Why we need it |
|---|---|
| `mcp` | The official Model Context Protocol SDK — gives us `FastMCP` (to build the server) and `ClientSession`/`stdio_client` (to build the client). |
| `requests` | A simple HTTP client — we use it to call Salesforce's REST API directly (no Salesforce-specific SDK needed). |
| `python-dotenv` | Reads a `.env` file and loads its `KEY=value` lines into environment variables, so secrets don't have to be hardcoded or passed on the command line. |
| `groq` | Groq's official Python client — same shape as OpenAI's client, used to send chat completion requests including tool definitions. |

Install everything into the venv:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This also pulls in each package's own dependencies automatically (e.g.
`mcp` needs `anyio`, `pydantic`, `httpx`, etc. — you don't manage those by
hand).

---

## 5. Set up config files (.gitignore, .env)

**`.gitignore`** — tells Git which files/folders to never track, so you
don't accidentally commit secrets or bulky generated files:

```
.venv/
__pycache__/
*.pyc
.env
*.log
```

**`.env.example`** — a *template* showing what environment variables the
project needs, with blank/placeholder values. This file is safe to commit
because it holds no real secrets:

```
# Salesforce External Client App (OAuth 2.0 Client Credentials flow)
SF_LOGIN_URL=https://login.salesforce.com
SF_CLIENT_ID=
SF_CLIENT_SECRET=

# Groq (OpenAI-compatible tool-calling API)
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
```

**`.env`** — your actual copy with real secrets filled in. Never commit
this one (that's exactly why `.gitignore` lists it):

```powershell
copy .env.example .env
```

Then open `.env` in a text editor and fill in the four blank values. We'll
get `SF_CLIENT_ID` and `SF_CLIENT_SECRET` from Salesforce in the next
section, and `GROQ_API_KEY` from console.groq.com.

---

## 6. Salesforce setup: create an External Client App

This is the part that has the most "gotchas" — Salesforce's terminology and
UI for this changed relatively recently (orgs created after roughly late
2024 default to a redesigned "External Client Apps" framework instead of
the older "Connected Apps" UI). Follow these exact steps; the
[Troubleshooting Log](#13-troubleshooting-log--real-errors-we-hit-and-how-we-fixed-them)
below explains *why* each one matters, in case you hit an error.

### 6.1 Create the app

1. Log into your org, go to **Setup** (gear icon, top right).
2. In Quick Find (top-left search box), type **External Client App
   Manager** and click it.
3. Click **New External Client App**.
4. Fill in **External Client App Name** (e.g. `AI Assignment1`) and
   **Contact Email**. Click **Create**.

### 6.2 Enable OAuth and pick a scope

1. On the app's page, go to the **Settings** tab.
2. Expand **OAuth Settings**, click the toggle/button to enable OAuth if
   it isn't already.
3. **Callback URL**: enter any valid URL — it's required to save, but our
   flow never uses it. `https://login.salesforce.com/services/oauth2/success`
   is a safe placeholder.
4. Under **OAuth Scopes**, move **"Manage user data via APIs (api)"** from
   *Available* to *Selected* (double-click it, or select and click the
   arrow).

### 6.3 Enable the Client Credentials flow

Still on the **Settings** tab, scroll to **Flow Enablement** and check
**Enable Client Credentials Flow**. Click **Save**.

> Why this flow and not "Username-Password"? See section 13.1 — the short
> version is that this newer app type doesn't offer the old
> username-password flow at all, and Client Credentials is simpler anyway:
> it authenticates as the *app*, not as a specific user typing a password,
> so nothing about your personal login credentials ever touches this
> project's code or `.env` file.

### 6.4 Set the "Run As" user

Client Credentials flow authenticates as the app, but Salesforce still
needs to know *which user's permissions* the resulting API calls should
run under.

1. Go to the **Policies** tab.
2. Find the section titled **OAuth Flows and External Client App
   Enhancements**.
3. You'll see **Enable Client Credentials Flow** checked (read-only here,
   matches what you set in Settings) with a **Run As (Username)** field
   underneath it. Type your own username (e.g.
   `yourname@example.com`).
4. Click **Save**.

### 6.5 Relax IP restrictions

Still on the **Policies** tab, find **IP Relaxation** and set it to
**"Relax IP restrictions"**. Also confirm **Permitted Users** is
**"All users can self-authorize"**. Save.

(Without this, Salesforce may reject API calls from your machine's IP
unless it's on the org's trusted IP list.)

### 6.6 Copy the Consumer Key and Secret

1. Go back to the **Settings** tab → **OAuth Settings** section.
2. Click **Consumer Key and Secret**.
3. You may be asked to verify via a code emailed to you.
4. Copy the **Consumer Key** into `.env` as `SF_CLIENT_ID`, and the
   **Consumer Secret** into `.env` as `SF_CLIENT_SECRET`.

### 6.7 Point `SF_LOGIN_URL` at your org's actual domain

This is the step that's easy to miss. Set:

```
SF_LOGIN_URL=https://<your-domain>.my.salesforce.com
```

Find `<your-domain>` under **Setup → My Domain**, or from the URL you see
in your browser's address bar while logged into Setup. **Do not** leave
this as the generic `https://login.salesforce.com` — the Client
Credentials flow specifically requires hitting your org's own domain (see
section 13.3).

At this point `.env` should look like:

```
SF_LOGIN_URL=https://your-domain.my.salesforce.com
SF_CLIENT_ID=3MVG9...(long string)...
SF_CLIENT_SECRET=(a 64-character hex string)
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-120b
```

---

## 7. Write `sf_mcp/config.py` — loading settings

First, an empty `sf_mcp/__init__.py` (just makes the folder a package —
literally zero bytes needed):

```python
# (empty file)
```

Now `sf_mcp/config.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Settings:
    def __init__(self):
        self.sf_login_url = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com")
        self.sf_client_id = _require("SF_CLIENT_ID")
        self.sf_client_secret = _require("SF_CLIENT_SECRET")
        self.groq_api_key = _require("GROQ_API_KEY")
        self.groq_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


settings = Settings()
```

**Line by line:**

- `load_dotenv()` — reads `.env` in the current directory and copies its
  key/value pairs into `os.environ`, as if you'd typed
  `$env:SF_CLIENT_ID = "..."` yourself before running the script. This must
  run *before* anything tries to read those variables.
- `_require(name)` — a small helper: look up an environment variable, and
  if it's missing or empty, raise an error immediately with a clear
  message, instead of letting the program silently run with `None` and
  fail confusingly three functions later.
- `class Settings` — a single object that, when constructed, reads every
  setting the app needs *once* and holds it as a plain attribute. Doing
  this in one place means every other file just writes
  `from .config import settings` and then `settings.sf_client_id` —
  no repeated `os.environ.get(...)` calls scattered through the codebase.
- `settings = Settings()` at module level — this line runs the moment
  anything imports `sf_mcp.config`, so the "did you forget to set
  `GROQ_API_KEY`" error surfaces immediately at startup, not halfway
  through a demo.

---

## 8. Write `sf_mcp/salesforce_client.py` — talking to Salesforce

This file knows nothing about MCP or LLMs — it's a plain wrapper around
Salesforce's REST API. Keeping it separate means you could reuse it in a
completely different project.

```python
import logging
import time

import requests

from .config import settings

logger = logging.getLogger("sf_mcp.salesforce")

API_VERSION = "v60.0"


class SalesforceClient:
    def __init__(self):
        self._access_token = None
        self._instance_url = None
        self._token_fetched_at = 0
        self._opportunity_fields_cache = None

    def _login(self):
        url = f"{settings.sf_login_url}/services/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": settings.sf_client_id,
            "client_secret": settings.sf_client_secret,
        }
        logger.info("Authenticating to Salesforce via Client Credentials flow")
        resp = requests.post(url, data=payload, timeout=30)
        if not resp.ok:
            raise RuntimeError(f"Salesforce OAuth login failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        self._access_token = data["access_token"]
        self._instance_url = data["instance_url"]
        self._token_fetched_at = time.time()
        logger.info("Authenticated. Instance: %s", self._instance_url)

    def _ensure_auth(self):
        if not self._access_token or (time.time() - self._token_fetched_at) > 25 * 60:
            self._login()

    def _headers(self):
        return {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, retry_on_401=True, **kwargs) -> requests.Response:
        self._ensure_auth()
        url = f"{self._instance_url}{path}"
        resp = requests.request(method, url, headers=self._headers(), timeout=30, **kwargs)
        if resp.status_code == 401 and retry_on_401:
            logger.warning("Salesforce session expired, re-authenticating")
            self._login()
            return self._request(method, path, retry_on_401=False, **kwargs)
        return resp

    def query(self, soql: str) -> list[dict]:
        logger.info("SOQL: %s", soql)
        path = f"/services/data/{API_VERSION}/query"
        resp = self._request("GET", path, params={"q": soql})
        if not resp.ok:
            raise RuntimeError(f"SOQL query failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        records = data.get("records", [])
        while not data.get("done", True):
            next_url = data["nextRecordsUrl"]
            resp = self._request("GET", next_url)
            if not resp.ok:
                raise RuntimeError(f"SOQL pagination failed ({resp.status_code}): {resp.text}")
            data = resp.json()
            records.extend(data.get("records", []))
        logger.info("SOQL returned %d record(s)", len(records))
        return records

    def create(self, sobject: str, fields: dict) -> str:
        path = f"/services/data/{API_VERSION}/sobjects/{sobject}"
        resp = self._request("POST", path, json=fields)
        if resp.status_code != 201:
            raise RuntimeError(f"Create {sobject} failed ({resp.status_code}): {resp.text}")
        return resp.json()["id"]

    def opportunity_field_names(self) -> set[str]:
        if self._opportunity_fields_cache is None:
            path = f"/services/data/{API_VERSION}/sobjects/Opportunity/describe"
            resp = self._request("GET", path)
            if not resp.ok:
                raise RuntimeError(f"Describe Opportunity failed ({resp.status_code}): {resp.text}")
            fields = resp.json().get("fields", [])
            self._opportunity_fields_cache = {f["name"] for f in fields}
        return self._opportunity_fields_cache

    def has_region_field(self) -> bool:
        return "Region__c" in self.opportunity_field_names()


_client: SalesforceClient | None = None


def get_client() -> SalesforceClient:
    global _client
    if _client is None:
        _client = SalesforceClient()
    return _client
```

**What each piece does:**

- **`_login`** — does the actual OAuth handshake: POST to
  `.../services/oauth2/token` with `grant_type=client_credentials` plus
  the client id/secret from `.env`. Salesforce responds with JSON
  containing an `access_token` (a short string you attach to every future
  request) and `instance_url` (the actual server hostname to send API
  calls to, which can differ slightly from your login URL). We store both,
  plus the timestamp we got them.
- **`_ensure_auth`** — called before every API request. If we've never
  logged in, or it's been more than 25 minutes (access tokens expire),
  log in again. This means every other method never has to think about
  authentication — they just call `self._ensure_auth()` and trust it.
- **`_headers`** — every Salesforce REST call needs an `Authorization:
  Bearer <token>` header. Centralized here instead of repeated everywhere.
- **`_request`** — a single choke-point for *every* HTTP call this class
  makes. It ensures we're authenticated, builds the full URL, makes the
  request, and — this is the important bit — if Salesforce responds
  `401 Unauthorized` (token expired early, or was revoked), it
  automatically re-logs-in *once* and retries, instead of just failing.
  The `retry_on_401=True` default with a manual `False` on the retry call
  prevents an infinite loop if the second attempt also fails.
- **`query`** — runs a SOQL query. Salesforce paginates large result sets
  (default 2000 records per "page"); the `while not data.get("done",
  True)` loop follows `nextRecordsUrl` links until there's nothing left,
  so callers of `query()` never have to think about pagination — they
  always get the *complete* result list back.
- **`create`** — inserts a new record of any Salesforce object type
  (`Account`, `Opportunity`, `Task`, ...) by POSTing a JSON body of field
  values to `/sobjects/<Type>`. Returns the new record's Id.
- **`opportunity_field_names` / `has_region_field`** — calls Salesforce's
  "describe" endpoint, which returns the full schema (every field name and
  type) for the `Opportunity` object. We use this once, cache the result,
  and check whether a custom `Region__c` field exists — this lets
  `get_pipeline_summary` (in `server.py`) automatically use a real custom
  field if a Salesforce admin later adds one, without any code changes.
- **`get_client()` / module-level `_client`** — a simple **singleton**
  pattern: the first call creates one `SalesforceClient` instance; every
  later call returns the *same* instance, so we don't re-authenticate or
  lose the cached access token between different tool calls.

---

## 9. Write `sf_mcp/server.py` — the MCP server and its tools

This is the heart of "Task 1" from the assignment — the MCP server exposing
Salesforce Opportunity data as callable tools.

```python
import logging
import re
import sys
from datetime import date
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .salesforce_client import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("mcp_server.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("sf_mcp.server")

mcp = FastMCP("salesforce-opportunities")

LIST_FIELDS = (
    "Id, Name, StageName, Amount, Probability, CloseDate, Account.Name, "
    "Owner.Name, NextStep, LastActivityDate"
)

DETAIL_FIELDS = (
    "Id, Name, StageName, Amount, Probability, CloseDate, AccountId, Account.Name, "
    "OwnerId, Owner.Name, NextStep, LastActivityDate, CreatedDate, IsClosed, IsWon, Description"
)

MAX_LIST_LIMIT = 50

REGION_PATTERN = re.compile(r"region\s*:\s*([A-Za-z ]+)", re.IGNORECASE)


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _validate_date(value: str, field_name: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD), got: {value!r}") from exc
    return value
```

**Setup section explained:**

- `logging.basicConfig(...)` with **two handlers** — a `FileHandler`
  writing to `mcp_server.log`, and a `StreamHandler` writing to `sys.stderr`.
  This is deliberate: MCP's stdio transport uses **stdout** to send actual
  protocol messages between client and server. If our log lines went to
  stdout too, they'd corrupt the protocol stream and everything would
  break. `stderr` is safe — it's a separate channel the client ignores —
  and the log *file* gives you a persistent trace to show during a demo
  (`Get-Content mcp_server.log -Wait -Tail 20` in another window).
- `mcp = FastMCP("salesforce-opportunities")` — creates the server object.
  `FastMCP` is a decorator-based helper from the `mcp` package: you write
  plain Python functions, decorate them with `@mcp.tool()`, and it
  auto-generates the JSON schema clients need from your function's type
  hints and docstring. You never hand-write JSON schema yourself.
- `LIST_FIELDS` vs `DETAIL_FIELDS` — two different SOQL field lists. The
  list version is deliberately narrower (fewer fields) because list
  queries can return many rows, and every extra field multiplies the
  token cost when that data gets serialized and sent to the LLM. The
  detail version (used only when fetching *one* specific record by Id)
  includes everything, including the free-text `Description` field.
- `MAX_LIST_LIMIT = 50` — a hard ceiling. Even if the LLM asks for more
  rows than this, we clamp it. This exists because of a real problem we
  hit during testing — see section 13.4.
- `REGION_PATTERN` — a compiled regular expression that looks for text
  like `Region: West` inside a string and captures the word(s) after the
  colon. Used to derive a "region" grouping from the Opportunity's
  `Description` field when there's no dedicated Salesforce field for it.
- `_soql_escape` — SOQL (like SQL) uses single quotes to wrap string
  literals. If a value contains a literal quote character (e.g. an
  account named `O'Brien Inc`), and we didn't escape it, the query would
  break — or worse, someone could deliberately craft input that changes
  the query's meaning (this is the SOQL equivalent of a SQL injection
  vulnerability). Since the values here come from an LLM's tool-call
  arguments, we treat them as untrusted input and always escape.
- `_validate_date` — instead of escaping date strings, we simply insist
  they parse as valid ISO dates (`date.fromisoformat`) and reject anything
  else before it ever reaches a query string. Dates in SOQL are written
  *unquoted* (`CloseDate >= 2026-01-01`, not `'2026-01-01'`), so quote
  escaping wouldn't even help here — validation is the right tool.

### 9.1 `get_opportunities`

```python
@mcp.tool()
def get_opportunities(
    stage: Optional[str] = None,
    account: Optional[str] = None,
    account_id: Optional[str] = None,
    close_date_from: Optional[str] = None,
    close_date_to: Optional[str] = None,
    owner: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch Opportunities with optional filters.

    Args:
        stage: Exact StageName match (e.g. "Proposal/Price Quote", "Closed Won").
        account: Partial, case-insensitive match on the related Account's Name.
        account_id: Exact Salesforce Account Id.
        close_date_from: Only Opportunities closing on/after this ISO date (YYYY-MM-DD).
        close_date_to: Only Opportunities closing on/before this ISO date (YYYY-MM-DD).
        owner: Partial, case-insensitive match on the Opportunity Owner's Name.
        limit: Max records to return (default 25, capped at 50). For totals or
            breakdowns by stage/region/account, use get_pipeline_summary instead
            of raising this limit — it returns compact aggregates, not raw rows.
    """
    logger.info(
        "TOOL get_opportunities(stage=%r, account=%r, account_id=%r, close_date_from=%r, "
        "close_date_to=%r, owner=%r, limit=%r)",
        stage, account, account_id, close_date_from, close_date_to, owner, limit,
    )
    limit = max(1, min(limit or 25, MAX_LIST_LIMIT))
    where = []
    if stage:
        where.append(f"StageName = '{_soql_escape(stage)}'")
    if account:
        where.append(f"Account.Name LIKE '%{_soql_escape(account)}%'")
    if account_id:
        where.append(f"AccountId = '{_soql_escape(account_id)}'")
    if close_date_from:
        where.append(f"CloseDate >= {_validate_date(close_date_from, 'close_date_from')}")
    if close_date_to:
        where.append(f"CloseDate <= {_validate_date(close_date_to, 'close_date_to')}")
    if owner:
        where.append(f"Owner.Name LIKE '%{_soql_escape(owner)}%'")

    soql = f"SELECT {LIST_FIELDS} FROM Opportunity"
    if where:
        soql += " WHERE " + " AND ".join(where)
    soql += f" ORDER BY CloseDate DESC LIMIT {limit}"

    records = get_client().query(soql)
    results = [_flatten(r) for r in records]
    logger.info("get_opportunities -> %d record(s)", len(results))
    return results
```

**Why it's built this way:**

- The `@mcp.tool()` decorator is doing a lot of invisible work: it reads
  the function's **type hints** (`stage: Optional[str]`, `limit: int`) and
  its **docstring** (both the summary line and the `Args:` section) and
  turns them into a JSON schema describing the tool, which is exactly what
  gets sent to the LLM so it knows this tool exists and what arguments it
  accepts. This is why the docstring isn't just a comment for humans here
  — the LLM literally reads it to decide *when* and *how* to call this
  function. Writing a vague docstring produces a model that calls the tool
  wrong.
- Every filter argument is **optional** (`Optional[str] = None`) — the
  caller (the LLM) only passes what it actually needs, and we build the
  SQL `WHERE` clause by conditionally appending pieces to a list, then
  joining with `AND`. This "build a list of clause strings, join at the
  end" pattern is a clean way to handle "any combination of N optional
  filters" without a giant if/elif chain.
- `LIKE '%...%'` for `account` and `owner` gives partial, case-insensitive
  matching (SOQL's `LIKE` is case-insensitive by default) — so the LLM
  doesn't need the exact, fully-correct account name.
- `ORDER BY CloseDate DESC` — a sensible default ordering (most
  imminent/recent first) so results are useful even with no explicit sort
  requested.
- `_flatten(r)` — see below; converts Salesforce's nested JSON shape into
  a flat dict that's easier for the LLM to read.

### 9.2 `get_opportunity_by_id`

```python
@mcp.tool()
def get_opportunity_by_id(opportunity_id: str) -> dict:
    """Fetch a single Opportunity with full detail, including Description and NextStep.

    Args:
        opportunity_id: The Salesforce Id of the Opportunity (15 or 18 chars).
    """
    logger.info("TOOL get_opportunity_by_id(opportunity_id=%r)", opportunity_id)
    soql = f"SELECT {DETAIL_FIELDS} FROM Opportunity WHERE Id = '{_soql_escape(opportunity_id)}' LIMIT 1"
    records = get_client().query(soql)
    if not records:
        return {"error": f"No Opportunity found with Id {opportunity_id}"}
    result = _flatten(records[0])
    logger.info("get_opportunity_by_id -> %s", result.get("Name"))
    return result
```

Simple by design: one Id in, one full record out (using the wider
`DETAIL_FIELDS` list, since we're only ever returning a single row, token
cost isn't a concern here). Returning a plain `{"error": ...}` dict for the
not-found case (rather than raising an exception) means the LLM sees a
normal tool result it can react to in conversation ("that Id doesn't
exist, let me look it up by name instead") rather than an opaque failure.

### 9.3 `get_pipeline_summary`

```python
@mcp.tool()
def get_pipeline_summary(
    group_by: str = "Stage",
    stage: Optional[str] = None,
    close_date_from: Optional[str] = None,
    close_date_to: Optional[str] = None,
    include_closed: bool = True,
) -> dict:
    """Aggregate open/closed Opportunity Amount grouped by Stage, Region, or Account.

    Region is read from a custom Region__c field if present on the org, otherwise
    parsed from a "Region: <value>" token in the Opportunity Description.

    Args:
        group_by: One of "Stage", "Region", "Account".
        stage: Optional exact StageName filter applied before aggregation.
        close_date_from: Only include Opportunities closing on/after this ISO date.
        close_date_to: Only include Opportunities closing on/before this ISO date.
        include_closed: If False, excludes Closed Won/Closed Lost Opportunities.
    """
    logger.info(
        "TOOL get_pipeline_summary(group_by=%r, stage=%r, close_date_from=%r, close_date_to=%r, include_closed=%r)",
        group_by, stage, close_date_from, close_date_to, include_closed,
    )
    group_by_normalized = (group_by or "Stage").strip().lower()
    if group_by_normalized not in ("stage", "region", "account"):
        raise ValueError('group_by must be one of "Stage", "Region", "Account"')

    client = get_client()
    has_region_field = client.has_region_field()

    fields = "Id, Name, StageName, Amount, Probability, CloseDate, Account.Name, Description"
    if has_region_field:
        fields += ", Region__c"

    where = []
    if stage:
        where.append(f"StageName = '{_soql_escape(stage)}'")
    if close_date_from:
        where.append(f"CloseDate >= {_validate_date(close_date_from, 'close_date_from')}")
    if close_date_to:
        where.append(f"CloseDate <= {_validate_date(close_date_to, 'close_date_to')}")
    if not include_closed:
        where.append("IsClosed = false")

    soql = f"SELECT {fields} FROM Opportunity"
    if where:
        soql += " WHERE " + " AND ".join(where)

    records = client.query(soql)

    groups: dict[str, dict] = {}
    for r in records:
        amount = r.get("Amount") or 0
        probability = r.get("Probability") or 0
        weighted = amount * (probability / 100.0)

        if group_by_normalized == "stage":
            key = r.get("StageName") or "Unspecified"
        elif group_by_normalized == "account":
            key = (r.get("Account") or {}).get("Name") or "No Account"
        else:  # region
            if has_region_field and r.get("Region__c"):
                key = r["Region__c"]
            else:
                match = REGION_PATTERN.search(r.get("Description") or "")
                key = match.group(1).strip() if match else "Unspecified"

        bucket = groups.setdefault(key, {"count": 0, "total_amount": 0.0, "weighted_amount": 0.0})
        bucket["count"] += 1
        bucket["total_amount"] += amount
        bucket["weighted_amount"] += weighted

    summary = [
        {
            "group": key,
            "count": bucket["count"],
            "total_amount": round(bucket["total_amount"], 2),
            "weighted_amount": round(bucket["weighted_amount"], 2),
        }
        for key, bucket in sorted(groups.items(), key=lambda kv: kv[1]["total_amount"], reverse=True)
    ]

    result = {
        "group_by": group_by_normalized.capitalize(),
        "region_source": "Region__c field" if has_region_field else "parsed from Description",
        "opportunity_count": len(records),
        "groups": summary,
    }
    logger.info("get_pipeline_summary -> %d group(s) from %d record(s)", len(summary), len(records))
    return result
```

**The key design decision here:** aggregation happens **in Python, after**
fetching raw rows — SOQL itself *can* do `GROUP BY` and `SUM()`, but doing
it client-side gave us one big advantage: the "Region" grouping doesn't
exist as a real query-able field in a vanilla org, since it's parsed out of
free text. You cannot `GROUP BY` a regex match in SOQL. By fetching rows
and grouping in Python, the exact same code path works whether region data
lives in a proper custom field (`Region__c`, checked first) or is buried in
a description string (the `REGION_PATTERN.search(...)` fallback) — and if
an admin adds the real field later, `has_region_field()` (from
`salesforce_client.py`) picks it up automatically with zero code changes.

Walking through the aggregation loop:

- `weighted = amount * (probability / 100.0)` — this is exactly what "weighted
  pipeline" means in sales terminology: an $100,000 deal that's 60% likely
  to close contributes $60,000 to the weighted total. Summed across many
  deals, it's a more realistic forecast than just adding up raw amounts.
- `groups.setdefault(key, {...})` — a dict-of-dicts accumulator pattern:
  the first time we see a group key (e.g. `"Negotiation/Review"`), create
  a fresh zeroed bucket; every subsequent row with that key adds into the
  existing bucket. This avoids a separate "if key not in groups: groups[key]
  = ..." check before every increment.
- The final list comprehension sorts groups by `total_amount` descending —
  so "which group has the most value" is always the first item, easy for
  both a human and an LLM to spot.

### 9.4 `_flatten`

```python
def _flatten(record: dict) -> dict:
    record = dict(record)
    record.pop("attributes", None)
    account = record.pop("Account", None)
    if account:
        record["AccountName"] = account.get("Name")
    owner = record.pop("Owner", None)
    if owner:
        record["OwnerName"] = owner.get("Name")
    return record
```

Salesforce's REST API returns related-object fields (like `Account.Name`
in our SOQL) as a **nested object**: `{"Account": {"Name": "Acme Inc",
"attributes": {...}}, ...}`, plus a noisy `attributes` key on every record
(internal metadata like the record's own REST URL, which the LLM doesn't
need). This function converts that into a flat, simple shape:
`{"AccountName": "Acme Inc", ...}` — fewer tokens, easier for the model to
read directly without understanding Salesforce's specific JSON
conventions.

### 9.5 Running the server directly

```python
if __name__ == "__main__":
    logger.info("Starting Salesforce Opportunity MCP server")
    mcp.run()
```

`mcp.run()` starts the server's main loop, listening on stdin for incoming
MCP protocol messages and writing responses to stdout. This only runs when
the file is executed directly (`python -m sf_mcp.server`), not when it's
merely imported — which matters because `agent.py` needs to import from
this same package elsewhere without accidentally starting a second server.

---

## 10. Write `sf_mcp/seed_data.py` — sample data generator

The MCP tools are useless to demo against an empty org, so this script
creates realistic sample data once.

```python
"""Seeds a small, realistic Opportunity dataset into the connected Salesforce org.

Idempotent: skips seeding if Opportunities from a prior run (Name LIKE 'Demo -%')
already exist, unless --force is passed.
"""

import argparse
import logging
import sys
from datetime import date, timedelta

from .salesforce_client import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sf_mcp.seed")

TODAY = date(2026, 7, 6)

ACCOUNTS = [
    {"Name": "Demo - Acme Robotics", "Industry": "Manufacturing", "BillingState": "CA"},
    {"Name": "Demo - Northwind Health", "Industry": "Healthcare", "BillingState": "NY"},
    {"Name": "Demo - Globex Logistics", "Industry": "Transportation", "BillingState": "TX"},
    {"Name": "Demo - Initech Software", "Industry": "Technology", "BillingState": "WA"},
    {"Name": "Demo - Umbrella Retail", "Industry": "Retail", "BillingState": "IL"},
]

# (account_index, name, stage, amount, probability, close_offset_days, region,
#  next_step, days_since_activity or None for "no recent activity")
OPPORTUNITIES = [
    (0, "Robotics Arm Upgrade - Phase 2", "Negotiation/Review", 185000, 80, 12, "West", "Send final contract redlines", 3),
    (0, "Warehouse Automation Pilot", "Proposal/Price Quote", 92000, 60, 25, "West", "Follow up on pricing questions", 45),
    (1, "EHR Integration Renewal", "Negotiation/Review", 240000, 75, 8, "East", "Legal review of MSA", 5),
    (1, "Telehealth Platform Expansion", "Qualification", 65000, 30, 40, "East", "Schedule discovery call with CMO", 60),
    (1, "Patient Portal Add-on", "Prospecting", 30000, 10, 55, "East", "Send intro deck", None),
    (2, "Fleet Tracking Contract", "Proposal/Price Quote", 150000, 55, 20, "Central", "Present ROI analysis", 10),
    (2, "Cold Chain Monitoring", "Closed Won", 110000, 100, -10, "Central", "", 2),
    (2, "Cross-Dock Optimization", "Closed Lost", 45000, 0, -20, "Central", "", None),
    (3, "DevOps Tooling License", "Negotiation/Review", 98000, 70, 15, "West", "Confirm seat count with IT", 4),
    (3, "Security Audit Engagement", "Qualification", 40000, 25, 35, "West", "Get budget confirmation", 50),
    (3, "Data Platform Migration", "Proposal/Price Quote", 275000, 65, 18, "West", "Deliver revised SOW", 33),
    (3, "API Gateway Add-on", "Prospecting", 22000, 10, 60, "West", "Identify technical champion", None),
    (4, "POS Modernization", "Negotiation/Review", 130000, 85, 10, "Central", "Finalize rollout schedule", 6),
    (4, "Loyalty Program Platform", "Qualification", 58000, 20, 45, "Central", "Align on success metrics", 38),
    (4, "Inventory Forecasting AI", "Proposal/Price Quote", 210000, 50, 22, "EMEA", "Address data privacy questions", 41),
    (4, "Store Analytics Dashboard", "Closed Won", 75000, 100, -5, "EMEA", "", 1),
    (0, "Predictive Maintenance Suite", "Prospecting", 48000, 15, 50, "EMEA", "Book technical deep dive", None),
    (1, "Claims Automation Bot", "Closed Lost", 60000, 0, -15, "EMEA", "", None),
]


def _already_seeded(client) -> bool:
    records = client.query("SELECT Id FROM Opportunity WHERE Name LIKE 'Demo -%' LIMIT 1")
    return len(records) > 0


def seed(force: bool = False):
    client = get_client()

    if not force and _already_seeded(client):
        logger.info("Demo data already present (Opportunities named 'Demo - %%'). Use --force to add more.")
        return

    account_ids = []
    for acct in ACCOUNTS:
        acct_id = client.create("Account", acct)
        logger.info("Created Account %s (%s)", acct["Name"], acct_id)
        account_ids.append(acct_id)

    for (acct_idx, name, stage, amount, probability, close_offset, region, next_step, activity_days_ago) in OPPORTUNITIES:
        close_date = TODAY + timedelta(days=close_offset)
        is_closed_stage = stage in ("Closed Won", "Closed Lost")
        fields = {
            "Name": f"Demo - {name}",
            "AccountId": account_ids[acct_idx],
            "StageName": stage,
            "Amount": amount,
            "Probability": probability,
            "CloseDate": close_date.isoformat(),
            "Description": f"Region: {region}. Seeded demo opportunity for MCP/LLM pipeline analysis.",
            "NextStep": next_step,
        }
        opp_id = client.create("Opportunity", fields)
        logger.info("Created Opportunity '%s' [%s] -> %s", fields["Name"], stage, opp_id)

        if activity_days_ago is not None:
            activity_date = TODAY - timedelta(days=activity_days_ago)
            task_fields = {
                "WhatId": opp_id,
                "Subject": "Call" if not is_closed_stage else "Wrap-up call",
                "Status": "Completed",
                "ActivityDate": activity_date.isoformat(),
                "Description": "Seeded activity for demo purposes.",
            }
            client.create("Task", task_fields)

    logger.info("Seeding complete: %d accounts, %d opportunities.", len(account_ids), len(OPPORTUNITIES))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Seed even if demo data already exists")
    args = parser.parse_args(sys.argv[1:])
    seed(force=args.force)
```

**Design choices explained:**

- **Data as a list of tuples, not a list of dicts** — for a small, static,
  hand-written table of sample rows, a tuple per row (`(account_index,
  name, stage, amount, ...)`) with a comment documenting the column order
  right above it is quicker to scan and edit than repeating dictionary key
  names 18 times. This is a reasonable shortcut for throwaway seed data;
  it would be the wrong choice for anything read from an external file or
  API, where you'd want named fields for safety.
- **Every demo record's Name starts with `"Demo - "`** — this is the
  entire idempotency mechanism: `_already_seeded` just checks "does any
  Opportunity named like that already exist?" If you run the script twice
  without `--force`, it detects the leftover demo data from the first run
  and does nothing, instead of silently doubling every record.
- **`activity_days_ago`** varies deliberately per row (some `3`, some
  `45`, `50`, `60`, some `None`) — this isn't random; it's the data the
  demo needs to exist for the assignment's example question *"Which
  opportunities haven't had activity in the last 30 days?"* to have a real,
  non-trivial answer. A `Task` record with `Status="Completed"` and an
  `ActivityDate` linked via `WhatId` to the Opportunity is what Salesforce
  uses to compute the Opportunity's automatic `LastActivityDate` field —
  we're not setting that field directly (you can't), we're creating the
  underlying activity record that causes Salesforce to compute it. Rows
  with `None` get no Task at all, leaving `LastActivityDate` blank — those
  are the "activity is stale/missing entirely" cases.
- **Region embedded in `Description`, not a real field** — deliberately
  mirrors the real assignment's phrasing ("Region parsed from Description,
  *or* a custom field if they add one"). It exercises the fallback path in
  `get_pipeline_summary` described above.

---

## 11. Write `agent.py` — the LLM client that drives the tools

This is Task 2 and 3 of the assignment: connect an LLM to the MCP server
and let it answer real questions by actually calling the tools.

```python
"""LLM-driven client for the Salesforce Opportunity MCP server.

Spawns the MCP server as a subprocess, hands its tools to a Groq-hosted
tool-calling model, and lets the model decide which tools to call to answer
analytical questions against live Salesforce data.

Usage:
    python agent.py                          interactive REPL
    python agent.py "your question here"     single-shot
"""

import asyncio
import json
import sys

from dotenv import load_dotenv
from groq import Groq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

from sf_mcp.config import settings  # noqa: E402

SYSTEM_PROMPT = """You are a sales operations analyst with live access to Salesforce Opportunity \
data through tools. When asked an analytical question:
1. Call the tools needed to gather the real data (never guess or fabricate numbers).
2. ALWAYS use get_pipeline_summary for any question about totals, sums, weighted pipeline, or \
breakdowns by stage/region/account. It returns compact pre-aggregated numbers. Never call \
get_opportunities with a large limit to compute a sum yourself — that wastes tokens and will \
get rate-limited.
3. Use get_opportunities only to inspect or list individual records: filtering by stage, \
account, owner, close date range, or checking activity recency (LastActivityDate). Keep limit \
as small as the question allows (e.g. 10-25); it is capped at 50.
4. Use get_opportunity_by_id when you need full detail (Description, NextStep) on one \
already-identified record.
5. Weighted pipeline = Amount * Probability / 100, summed (get_pipeline_summary computes this \
for you as weighted_amount per group).
6. "No recent activity" means LastActivityDate is null or older than the requested window; \
compare against today's date, which is 2026-07-06.
Do not just dump a raw table back to the user. Synthesize a real answer: name the specific \
numbers, call out what's notable or risky, and explain your reasoning briefly."""

MAX_TOOL_ROUNDS = 8


def _tool_result_to_text(result) -> str:
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        else:
            parts.append(str(block))
    return "\n".join(parts)
```

**Setup and the system prompt:**

- We connect over **stdio**, meaning `agent.py` will literally spawn
  `python -m sf_mcp.server` as a child process and talk to it through pipes
  — no network port, no separate terminal window needed for the server.
- The `SYSTEM_PROMPT` is doing real engineering work, not just
  scene-setting. Point 2 in particular exists because, in testing, the
  model's *first instinct* for "what's our total weighted pipeline" was to
  call `get_opportunities` with a big limit and add the numbers up itself
  — which works, but wastes a huge number of tokens fetching raw rows when
  a purpose-built aggregation tool already exists. Explicitly telling the
  model *which* tool to prefer, and *why* (token cost, rate limits), fixed
  this. This is a normal part of building an LLM+tools system: you write
  the tools, test with real prompts, see what the model actually chooses
  to do, and adjust the system prompt (or the tools themselves) based on
  observed behavior — you can't fully predict it up front.
- `_tool_result_to_text` — when we call an MCP tool, the result comes back
  as a `CallToolResult` object holding a list of "content blocks" (MCP
  supports multiple content types — text, images, etc.). Our tools only
  ever return text (FastMCP automatically JSON-serializes whatever Python
  value we return into one text block), so this just extracts and
  concatenates the `.text` of each block into a plain string we can hand
  to the LLM as a tool result.

```python
async def run_question(question: str):
    server_params = StdioServerParameters(command=sys.executable, args=["-m", "sf_mcp.server"])
    groq_client = Groq(api_key=settings.groq_api_key)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema,
                    },
                }
                for t in tools_result.tools
            ]
            print(f"[connected to MCP server; {len(tools)} tool(s) available: "
                  f"{', '.join(t['function']['name'] for t in tools)}]\n")

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]
```

**Connecting and discovering tools:**

- `StdioServerParameters(command=sys.executable, args=["-m", "sf_mcp.server"])`
  — describes *how* to launch the server: run the exact same Python
  interpreter this script is using (`sys.executable` — important so it
  uses the venv's Python, not some other Python that might be on the
  system PATH), with arguments `-m sf_mcp.server` (Python's "run this
  module as a script" flag, which works because of that `if __name__ ==
  "__main__":` block back in `server.py`).
- `async with stdio_client(...) as (read, write):` then `async with
  ClientSession(read, write) as session:` — two nested context managers.
  The outer one starts the subprocess and gives us raw read/write streams;
  the inner one wraps those streams in the actual MCP protocol logic
  (handling request IDs, JSON-RPC framing, etc.) so we can call clean
  methods like `session.list_tools()` instead of hand-writing JSON.
- `await session.initialize()` — MCP requires a handshake before any real
  requests; this performs it.
- `session.list_tools()` — asks the server "what tools do you have?" and
  gets back a list of tool definitions, each with `.name`, `.description`,
  and `.inputSchema` (the JSON schema `FastMCP` auto-generated from our
  function signatures back in `server.py`).
- The **list comprehension right after** converts MCP's tool format into
  the specific shape Groq's (and OpenAI's) chat completions API expects:
  `{"type": "function", "function": {"name": ..., "description": ...,
  "parameters": <json-schema>}}`. This translation step is the actual
  "bridge" between the MCP world and the LLM-provider world — MCP doesn't
  know about Groq, and Groq doesn't know about MCP; this dict comprehension
  is where the two protocols meet.
- `messages` — a plain list of `{"role": ..., "content": ...}` dicts. This
  *is* the conversation history, in exactly the format every OpenAI-style
  chat API expects. It starts with our system prompt and the user's
  question, and grows every round.

```python
            for round_num in range(1, MAX_TOOL_ROUNDS + 1):
                response = groq_client.chat.completions.create(
                    model=settings.groq_model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
                msg = response.choices[0].message

                if not msg.tool_calls:
                    print(msg.content)
                    return

                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    print(f"  -> calling tool: {tc.function.name}({args})")
                    result = await session.call_tool(tc.function.name, args)
                    result_text = _tool_result_to_text(result)
                    preview = result_text if len(result_text) < 300 else result_text[:300] + "...(truncated)"
                    print(f"  <- result: {preview}\n")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": result_text,
                    })

            print("[stopped: exceeded max tool-call rounds]")
```

**This loop is the whole "agent" behavior — read it carefully:**

1. Send the full conversation so far, plus the list of available tools,
   to the model.
2. The model's response (`msg`) is *either* a normal text answer, *or* a
   request to call one or more tools (`msg.tool_calls`).
3. **If there are no tool calls**, the model decided it has enough
   information to answer — print `msg.content` (its final synthesized
   answer) and stop. This is the success path.
4. **If there are tool calls**, we first append the assistant's own
   message (including its tool-call requests) to `messages` — this is
   required by the API: the conversation history must show *what the
   model asked for* before it can see the *results* of those requests, or
   the next request will be malformed.
5. Then, for **each** requested tool call: parse its JSON string arguments
   (`tc.function.arguments` arrives as a string, so `json.loads` turns it
   back into a real dict), print a visible `-> calling tool: ...` line
   (this is exactly what you'd point at during the live demo to show the
   tool call actually firing), actually invoke it through the MCP
   session (`await session.call_tool(...)` — this is the call that
   crosses from "the LLM's world" into "the real Salesforce data" via the
   whole chain we built: MCP → `server.py` tool function → `SalesforceClient`
   → Salesforce REST API), then append the result back into `messages`
   with `"role": "tool"` — the specific message role the API uses to feed
   tool output back to the model.
6. The `for round_num in range(...)` loop then repeats: send the
   *updated* `messages` (now including the tool result) back to the
   model. It might ask for another tool call (e.g. "now that I know the
   stage breakdown, let me also check for stale opportunities"), or it
   might now answer in plain text.
7. `MAX_TOOL_ROUNDS = 8` is just a safety valve — without it, a model stuck
   in a bad loop (repeatedly calling a tool without ever answering) would
   run forever and rack up API costs. Eight rounds is generous for
   anything this project's tools are meant to answer.

This request → tool call → tool result → request-again cycle **is** what
"the LLM calls the tools, pulls real data, and generates insight" means in
practice — there's no separate "orchestration" logic beyond this loop; the
model itself decides which tools to call and when it's done, based purely
on what's in `messages` at each step.

```python
async def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        await run_question(question)
        return

    print("Salesforce Opportunity Insights agent. Type a question, or 'exit' to quit.\n")
    while True:
        try:
            question = input("> ").strip()
        except EOFError:
            break
        if not question or question.lower() in ("exit", "quit"):
            break
        await run_question(question)
        print()


if __name__ == "__main__":
    asyncio.run(main())
```

Two modes: if you passed command-line arguments (`python agent.py "some
question"`), treat them as one question and exit after answering — good
for scripting or a quick single demo prompt. Otherwise, drop into an
interactive loop reading questions from the keyboard until you type `exit`
or `quit`. Each call to `run_question` starts a **fresh** MCP server
subprocess and a **fresh** conversation — simple, if slightly less
efficient than keeping one server alive across questions; for a
demo-sized project, the extra second of startup per question is a fine
trade for simplicity.

---

## 12. Running everything

Every command below assumes you're in the project folder and using the
venv's Python directly (adjust if you activated the venv instead).

**1. Verify Salesforce auth works before anything else:**

```powershell
.venv\Scripts\python.exe -c "from sf_mcp.salesforce_client import get_client; c = get_client(); print(c.query('SELECT Id, Name FROM Account LIMIT 3'))"
```

**2. Seed sample data (idempotent — safe to re-run):**

```powershell
.venv\Scripts\python.exe -m sf_mcp.seed_data
```

**3. Ask a single question:**

```powershell
.venv\Scripts\python.exe agent.py "What's our total weighted pipeline and which stage has the most value sitting in it?"
```

**4. Or run interactively:**

```powershell
.venv\Scripts\python.exe agent.py
```

**5. During a live demo**, open a second terminal and tail the log to show
tool calls firing in real time:

```powershell
Get-Content mcp_server.log -Wait -Tail 20
```

---

## 13. Troubleshooting log — real errors we hit and how we fixed them

These are worth understanding, not just skipping past — they're the parts
most likely to trip you up if you rebuild this yourself, especially on a
Salesforce org created recently.

### 13.1 `invalid_grant: authentication failure` with the Username-Password flow

We initially built this using the classic OAuth **username-password**
flow (`grant_type=password`, sending username + password + security
token). It never worked, even after confirming (via a completely separate
SOAP login call) that the username and password were 100% correct. The
root cause: the app had been created as a **new-style "External Client
App"**, and that framework's **Flow Enablement** checklist simply does not
offer "Resource Owner Password Credentials" as an option at all — Salesforce
has been moving away from that flow because sending a raw password over
the wire on every login is considered legacy/insecure by modern OAuth
standards. No amount of fiddling with IP Relaxation, Permitted Users, or
security tokens could have fixed it, because the flow itself wasn't
available for that app type. The fix was switching entirely to **Client
Credentials flow** (client id + secret only — see section 6).

**Lesson:** if an OAuth flow fails with a suspiciously generic error and
you've triple-checked credentials, check whether the flow you're
attempting is even *enabled/available* for that specific app, not just
whether your credentials are right.

### 13.2 `invalid_client: invalid client credentials` vs `invalid_grant: authentication failure`

While debugging the above, we ran a deliberate control test: same request,
but with one character appended to the client secret to make it wrong on
purpose. That produced a *different* error (`invalid_client`) than the
real, correctly-configured secret did (`invalid_grant`). This told us
definitively that Salesforce *was* recognizing our client id/secret pair
as valid — the problem had to be something else about the flow, not a typo
in the credentials. This is a generally useful debugging technique:
**when an error message is vague, deliberately break one variable at a
time and see if the error message changes** — a changed error confirms
that variable was being checked at all; an unchanged error tells you to
look elsewhere.

### 13.3 `invalid_grant: request not supported on this domain`

After switching to Client Credentials flow, login still failed — but with
a new, more specific error. Client Credentials requests must be sent to
the org's **actual My Domain hostname**
(`https://your-domain.my.salesforce.com`), not the generic
`https://login.salesforce.com`. (The username-password flow, by contrast,
tolerates the generic host because it does a redirect/lookup based on the
username — Client Credentials has no username to look up, so it needs the
domain up front.) Fix: set `SF_LOGIN_URL` to the org's real domain (found
under Setup → My Domain).

### 13.4 Groq `413 tokens per minute (TPM) rate_limit_exceeded`

Once auth worked, our very first real question caused the model to call
`get_opportunities` with `limit=200` (trying to fetch every record and sum
them itself) — and the org turned out to already have ~180 pre-existing
Opportunities beyond our 18 seeded demo ones. That's a lot of JSON, and
Groq's free tier caps a single request at 8,000 tokens per minute for the
`gpt-oss-120b` model; the request needed ~35,000. Two fixes, both in
sections 9 and 11 above: (a) hard-cap the tool's `limit` parameter
server-side (`MAX_LIST_LIMIT = 50`) so no single tool call can return an
unbounded amount of data regardless of what the model asks for, and (b)
make the system prompt explicitly steer the model toward
`get_pipeline_summary` (which returns a handful of aggregated numbers, not
hundreds of rows) for any question about totals or breakdowns.

**Lesson:** when you give an LLM a tool that *can* return a lot of data,
assume it eventually will ask for a lot of data, and defend against that
at the tool layer (hard caps) as well as the prompt layer (steering) — a
prompt alone is a suggestion, not a guarantee.

---

That's the entire project. If you rebuild it from this document, you now
also understand *why* each piece exists — not just what to type.
