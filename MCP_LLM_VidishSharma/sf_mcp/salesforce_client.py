import logging
import time

import requests

from .config import settings

logger = logging.getLogger("sf_mcp.salesforce")

API_VERSION = "v60.0"


class SalesforceClient:
    """Thin REST/SOQL client authenticated via the OAuth 2.0 username-password flow."""

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
        # Re-login every 25 minutes to stay ahead of session timeout.
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
