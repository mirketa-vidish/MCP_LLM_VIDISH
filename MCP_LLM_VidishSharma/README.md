# Salesforce Opportunity Insights — MCP + LLM

An MCP server that exposes Salesforce Opportunity data as tools, plus an LLM
agent (Groq-hosted, tool-calling) that answers real analytical questions by
calling those tools against live data.

## Architecture

```
agent.py  --spawns-->  sf_mcp/server.py (MCP server, stdio transport)
   |                        |
   | Groq tool-calling      | SOQL over REST (OAuth password flow)
   v                        v
Groq API                Salesforce org
```

- `sf_mcp/salesforce_client.py` — OAuth login + SOQL/REST wrapper.
- `sf_mcp/server.py` — MCP server exposing three tools:
  - `get_opportunities(stage, account, account_id, close_date_from, close_date_to, owner, limit)`
  - `get_opportunity_by_id(opportunity_id)`
  - `get_pipeline_summary(group_by, stage, close_date_from, close_date_to, include_closed)`
- `sf_mcp/seed_data.py` — seeds sample Accounts/Opportunities/Tasks for the demo.
- `agent.py` — connects an LLM (Groq) to the MCP server and runs the tool-call loop.

Every tool call is logged to stderr and to `mcp_server.log` in this directory
— that's the trace to show in the live demo.

## 1. One-time Salesforce setup: External Client App

Newer orgs (Winter '25+) use "External Client Apps" instead of classic
Connected Apps. This project authenticates via the OAuth 2.0 **Client
Credentials** flow — client id/secret only, no username/password stored
anywhere.

1. Setup → Quick Find → **External Client App Manager** → **New External
   Client App**.
2. Fill in name and Contact Email, click **Create**.
3. Under **OAuth Settings**, enable OAuth, set any Callback URL (e.g.
   `https://login.salesforce.com/services/oauth2/success` — unused by this
   flow but required to save), and add scope **Manage user data via APIs (api)**.
4. Under **Flow Enablement**, check **Enable Client Credentials Flow**. Save.
5. Go to the **Policies** tab → **OAuth Flows and External Client App
   Enhancements** → set **Run As (Username)** to the Salesforce user whose
   permissions the integration should use (e.g. yourself). Save.
6. Under **Settings** → **OAuth Settings** → **Consumer Key and Secret**,
   copy the **Consumer Key** and **Consumer Secret**.
7. Also confirm on the **Policies** tab: **IP Relaxation** = "Relax IP
   restrictions", **Permitted Users** = "All users can self-authorize".

## 2. Configure environment

```
cp .env.example .env
```

Fill in `.env`:

```
SF_LOGIN_URL=https://login.salesforce.com
SF_CLIENT_ID=<Consumer Key>
SF_CLIENT_SECRET=<Consumer Secret>

GROQ_API_KEY=<from console.groq.com>
GROQ_MODEL=openai/gpt-oss-120b
```

`.env` is gitignored — never commit it.

## 3. Install dependencies

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Seed sample data

```
python -m sf_mcp.seed_data
```

Creates 5 demo Accounts and 18 demo Opportunities across stages
(Prospecting → Closed Won/Lost), spread across four regions embedded in the
Description field (`Region: West` etc.), with varying last-activity recency
(some very recent, some 40-60 days stale) so risk-style questions have
something real to surface. Re-running is a no-op unless you pass `--force`.

## 5. Run the agent

Single question:
```
python agent.py "What's our total weighted pipeline and which stage has the most value sitting in it?"
```

Interactive:
```
python agent.py
```

Watch `mcp_server.log` (or the terminal, since it's also streamed to stderr)
in a second window during the demo to show tool calls firing in real time:
```
Get-Content mcp_server.log -Wait -Tail 20
```

## Example questions to demo

- "What's our total weighted pipeline and which stage has the most value sitting in it?"
- "Which opportunities haven't had activity in the last 30 days and look at risk?"
- "Summarize this quarter's pipeline by account and call out anything unusual."
- "Break down the pipeline by region and tell me where we're most exposed if the top deal falls through."

## Notes

- Region data comes from parsing `Region: <value>` out of the Opportunity
  Description field. If the org later adds a real `Region__c` picklist field,
  `get_pipeline_summary` automatically prefers it (checked via a describe
  call) with no code changes needed.
- Session tokens are re-fetched automatically on expiry/401 and every 25
  minutes as a precaution.
