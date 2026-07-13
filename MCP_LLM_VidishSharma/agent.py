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
