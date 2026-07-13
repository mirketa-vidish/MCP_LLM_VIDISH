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


if __name__ == "__main__":
    logger.info("Starting Salesforce Opportunity MCP server")
    mcp.run()
