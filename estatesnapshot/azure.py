"""Thin Azure REST client. Authenticates through the Azure CLI (az login), so the
tool needs no secrets and no SDK: only Reader on the target subscriptions."""
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

ARM = "https://management.azure.com"
GRAPH = "https://graph.microsoft.com"
ARG_API = "2022-10-01"
LOGANALYTICS = "https://api.loganalytics.io"
METRICS_AUDIENCE = "https://metrics.monitor.azure.com"
NOT_THERE = (400, 404, 409)    # "no such setting here": a fact about the resource, not a failure


class AzureError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def _az(args):
    exe = shutil.which("az") or shutil.which("az.cmd")
    if not exe:
        raise AzureError("Azure CLI (az) not found on PATH")
    r = subprocess.run([exe] + args, capture_output=True, text=True)
    if r.returncode:
        raise AzureError((r.stderr or "az failed").strip().splitlines()[-1])
    return json.loads(r.stdout)


class Client:
    def __init__(self, tenant=None):
        self.tenant = tenant
        self._tokens = {}
        self._lock = threading.Lock()      # collectors fan out across threads

    def token(self, audience="arm"):
        with self._lock:
            cached = self._tokens.get(audience)
            if cached and cached[1] - time.time() > 300:
                return cached[0]
            args = ["account", "get-access-token", "-o", "json"]
            if audience == "graph":
                args += ["--resource-type", "ms-graph"]
            elif audience == "loganalytics":
                args += ["--resource", LOGANALYTICS]
            elif audience == "metrics":
                args += ["--resource", METRICS_AUDIENCE]
            else:
                args += ["--resource", ARM + "/"]
            if self.tenant:
                args += ["--tenant", self.tenant]
            d = _az(args)
            expires = float(d.get("expires_on") or (time.time() + 3000))
            self._tokens[audience] = (d["accessToken"], expires)
            return d["accessToken"]

    def request(self, method, url, body=None, audience="arm", retries=5, max_wait=60, base_wait=1):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", "Bearer " + self.token(audience))
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504, 529) and attempt < retries:
                    wait = e.headers.get("Retry-After") or e.headers.get("x-ms-ratelimit-microsoft.costmanagement-entity-retry-after")
                    wait = float(wait) if wait else base_wait * 2 ** attempt
                    if wait > max_wait:
                        raise AzureError(f"HTTP {e.code} throttled; service asked to wait {wait:.0f}s, more than the {max_wait}s allowed", e.code)
                    time.sleep(wait)
                    continue
                detail = e.read().decode(errors="replace")[:400]
                raise AzureError(f"HTTP {e.code} {method} {url.split('?')[0]}: {detail}", e.code)
        raise AzureError(f"gave up after retries: {url}")

    # -- helpers -------------------------------------------------------------

    def pages(self, url, **kw):
        """Follow nextLink on an ARM list call and return every item."""
        out = []
        while url:
            d = self.request("GET", url, **kw)
            out.extend(d.get("value", []))
            url = d.get("nextLink")
        return out

    def subscriptions(self):
        d = self.request("GET", f"{ARM}/subscriptions?api-version=2022-12-01")
        subs = d.get("value", [])
        if self.tenant:
            subs = [s for s in subs if s.get("tenantId") == self.tenant]
        return [s for s in subs if s.get("state") == "Enabled"]

    def arg(self, query, subscriptions):
        """Run a Resource Graph query across subscriptions, following skip tokens."""
        out = []
        for i in range(0, len(subscriptions), 1000):
            batch, skip = subscriptions[i:i + 1000], None
            while True:
                opts = {"$top": 1000, "resultFormat": "objectArray"}
                if skip:
                    opts["$skipToken"] = skip
                d = self.request("POST", f"{ARM}/providers/Microsoft.ResourceGraph/resources?api-version={ARG_API}",
                                 {"subscriptions": batch, "query": query, "options": opts})
                out.extend(d.get("data", []))
                skip = d.get("$skipToken")
                if not skip:
                    break
        return out
