"""Opt-in Entra ID collector (--entra). Read-only Microsoft Graph GETs for Conditional
Access, directory roles and on-premises sync status. These need Graph read scopes that
the Azure CLI's own token often lacks, so every call is independent and a refusal is
recorded as a gap rather than stopping the run."""
from .azure import GRAPH, AzureError

V1 = GRAPH + "/v1.0"
SCOPES = {
    "organization": "Organization.Read.All (or Directory.Read.All)",
    "domains": "Domain.Read.All (or Directory.Read.All)",
    "conditional_access": "Policy.Read.All",
    "directory_roles": "RoleManagement.Read.Directory (or Directory.Read.All)",
}


def _pages(client, url):
    out = []
    while url:
        d = client.request("GET", url, audience="graph", retries=3, max_wait=30)
        out.extend(d.get("value", []))
        url = d.get("@odata.nextLink")
    return out


def _policy(p):
    c, g = p.get("conditions") or {}, p.get("grantControls") or {}
    u, a = c.get("users") or {}, c.get("applications") or {}
    return {"name": p.get("displayName"), "state": p.get("state"),
            "allUsers": "All" in (u.get("includeUsers") or []), "includeRoles": len(u.get("includeRoles") or []),
            "includeGroups": len(u.get("includeGroups") or []), "excludeUsers": len(u.get("excludeUsers") or []),
            "excludeGroups": len(u.get("excludeGroups") or []),
            "allApps": "All" in (a.get("includeApplications") or []),
            "apps": a.get("includeApplications") or [], "userActions": a.get("includeUserActions") or [],
            "clientAppTypes": c.get("clientAppTypes") or [],
            "locations": bool(c.get("locations")), "platforms": bool(c.get("platforms")),
            "userRisk": c.get("userRiskLevels") or [], "signInRisk": c.get("signInRiskLevels") or [],
            "grant": g.get("builtInControls") or [], "grantOperator": g.get("operator"),
            "authStrength": ((g.get("authenticationStrength") or {}).get("displayName")),
            "sessionControls": sorted(k for k, v in (p.get("sessionControls") or {}).items() if v and not k.startswith("@"))}


def collect(client, log=print):
    """Returns (data, gaps). Each piece is fetched on its own."""
    data, gaps = {}, {}

    def attempt(name, fn):
        try:
            data[name] = fn()
        except AzureError as e:
            gaps[f"entra:{name}"] = (f"{'no permission (' + SCOPES[name] + ')' if e.status in (401, 403) else str(e)[:200]}")
            log(f"    entra {name:<20} unavailable: {gaps[f'entra:{name}']}")

    attempt("organization", lambda: [
        {k: o.get(k) for k in ("displayName", "id", "onPremisesSyncEnabled", "onPremisesLastSyncDateTime", "tenantType",
                               "createdDateTime")}
        for o in _pages(client, V1 + "/organization")])
    attempt("domains", lambda: [
        {k: d.get(k) for k in ("id", "isDefault", "isVerified", "authenticationType")}
        for d in _pages(client, V1 + "/domains")])
    attempt("conditional_access", lambda: [_policy(p) for p in _pages(client, V1 + "/identity/conditionalAccess/policies")])

    def roles():
        out = []
        for r in _pages(client, V1 + "/directoryRoles"):
            members = _pages(client, f"{V1}/directoryRoles/{r['id']}/members?$select=id,displayName,userPrincipalName")
            out.append({"role": r.get("displayName"), "roleTemplateId": r.get("roleTemplateId"),
                        "members": [{"name": m.get("displayName"), "upn": m.get("userPrincipalName"),
                                     "type": (m.get("@odata.type") or "").split(".")[-1]} for m in members]})
        return out
    attempt("directory_roles", roles)
    return data, gaps
