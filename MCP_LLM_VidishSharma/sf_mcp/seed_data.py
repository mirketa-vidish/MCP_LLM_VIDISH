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
