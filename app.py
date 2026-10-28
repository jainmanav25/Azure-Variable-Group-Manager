import base64
import hmac
import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

if not APP_PASSWORD or not SESSION_SECRET:
    raise RuntimeError("Set APP_PASSWORD and SESSION_SECRET environment variables before starting.")

app = FastAPI(title="Azure DevOps Variable Group Manager")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="strict",
    https_only=COOKIE_SECURE,
    max_age=60 * 60 * 8,
)
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")


def ado_headers(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def clean_org(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("https://dev.azure.com/"):
        value = value.removeprefix("https://dev.azure.com/").strip("/")
    if not value or "/" in value or "://" in value or any(c.isspace() for c in value):
        raise HTTPException(400, "Organization must be its short name or https://dev.azure.com/ORG.")
    return value


def ado_url(org: str, path: str) -> str:
    return f"https://dev.azure.com/{org}/{path.lstrip('/')}"


async def ado_request(client: httpx.AsyncClient, method: str, url: str, headers: dict, **kwargs):
    try:
        response = await client.request(method, url, headers=headers, timeout=30, **kwargs)
    except httpx.TimeoutException:
        raise HTTPException(504, "Azure DevOps API timed out.")
    except httpx.RequestError:
        raise HTTPException(502, "Could not connect to Azure DevOps. Check network/proxy settings.")
    if response.status_code == 401:
        raise HTTPException(401, "PAT invalid, expired, or not authorized.")
    if response.status_code == 403:
        raise HTTPException(403, "Permission denied. Check PAT scope and Azure DevOps project permissions.")
    if response.status_code >= 400:
        try:
            detail = response.json().get("message", response.text[:500])
        except Exception:
            detail = response.text[:500]
        raise HTTPException(response.status_code, f"Azure DevOps API error: {detail}")
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


def validate_payload(data: Any):
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON root must be an object.")
    if not isinstance(data.get("organization"), str) or not data["organization"].strip():
        raise HTTPException(400, "Missing organization.")
    if not isinstance(data.get("project"), str) or not data["project"].strip():
        raise HTTPException(400, "Missing project.")
    group = data.get("variableGroup")
    if not isinstance(group, dict):
        raise HTTPException(400, "Missing variableGroup object.")
    name = group.get("name")
    variables = group.get("variables")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, "variableGroup.name must be a non-empty string.")
    if not isinstance(variables, dict) or not variables:
        raise HTTPException(400, "variableGroup.variables must contain at least one variable.")
    for key, item in variables.items():
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(400, "Variable names must be non-empty strings.")
        if not isinstance(item, dict):
            raise HTTPException(400, f"Variable '{key}' must be an object.")
        if "value" not in item:
            raise HTTPException(400, f"Variable '{key}' is missing value.")
        if not isinstance(item.get("isSecret", False), bool):
            raise HTTPException(400, f"Variable '{key}'.isSecret must be true or false.")
        if item.get("value") is not None and not isinstance(item.get("value"), (str, int, float, bool)):
            raise HTTPException(400, f"Variable '{key}' value must be a string, number, boolean, or null.")


def normalized_variables(incoming: dict, current: dict | None = None) -> dict:
    # Merge-only: keep variables not present in JSON. Azure masks existing secret values;
    # sending null for an unchanged secret is the API's preserve-secret convention.
    merged = dict(current or {})
    for name, item in incoming.items():
        value = item.get("value")
        is_secret = item.get("isSecret", False)
        if is_secret and value is None:
            raise HTTPException(400, f"Secret '{name}' needs a value when adding/updating it.")
        merged[name] = {"value": str(value) if value is not None else "", "isSecret": is_secret}
    for name, item in merged.items():
        if item.get("isSecret"):
            # Never send back a masked/null secret as a real replacement.
            if name not in incoming:
                merged[name] = {"value": None, "isSecret": True}
            elif incoming[name].get("value") is None:
                raise HTTPException(400, f"Secret '{name}' requires a non-null value.")
    return merged


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("authenticated"):
        return templates.TemplateResponse(request=request, name="login.html", context={"error": ""})
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    valid_user = hmac.compare_digest(username, APP_USERNAME)
    valid_pass = hmac.compare_digest(password, APP_PASSWORD)
    if not (valid_user and valid_pass):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password."},
            status_code=401,
        )
    request.session.clear()
    request.session["authenticated"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.post("/api/validate")
async def validate(request: Request, file: str = Form(...)):
    require_login(request)
    try:
        data = json.loads(file)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON at line {exc.lineno}, column {exc.colno}.")
    validate_payload(data)
    return {"valid": True, "organization": clean_org(data["organization"]), "project": data["project"], "groupName": data["variableGroup"]["name"], "variableCount": len(data["variableGroup"]["variables"])}


@app.post("/api/apply")
async def apply(request: Request, file: str = Form(...), pat: str = Form(...), mode: str = Form("merge")):
    require_login(request)
    if mode not in ("merge", "replace"):
        raise HTTPException(400, "Mode must be merge or replace.")
    if not pat.strip():
        raise HTTPException(400, "Enter an Azure DevOps PAT.")
    try:
        data = json.loads(file)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON at line {exc.lineno}, column {exc.colno}.")
    validate_payload(data)

    org = clean_org(data["organization"])
    project = data["project"].strip()
    group = data["variableGroup"]
    group_name = group["name"].strip()
    headers = ado_headers(pat.strip())
    async with httpx.AsyncClient() as client:
        # Resolve project name to its canonical ID.
        project_result = await ado_request(
            client, "GET",
            ado_url(org, f"_apis/projects/{httpx.QueryParams({'project': project})['project']}?api-version=7.1"),
            headers
        )
        project_id = project_result.get("id")
        if not project_id:
            raise HTTPException(404, "Project ID was not returned by Azure DevOps.")

        groups_result = await ado_request(
            client, "GET",
            ado_url(org, f"_apis/distributedtask/variablegroups?projectIds={project_id}&api-version=7.1"),
            headers
        )
        groups = groups_result.get("value", [])
        existing = next((g for g in groups if g.get("name", "").casefold() == group_name.casefold()), None)

        incoming = {}
        for name, item in group["variables"].items():
            incoming[name] = {
                "value": str(item["value"]) if item["value"] is not None else None,
                "isSecret": item.get("isSecret", False)
            }

        if existing:
            group_id = existing["id"]
            detail = await ado_request(
                client, "GET",
                ado_url(org, f"_apis/distributedtask/variablegroups/{group_id}?api-version=7.1"),
                headers
            )
            current_vars = detail.get("variables", {})
            if mode == "merge":
                variables = normalized_variables(incoming, current_vars)
            else:
                # Replace is intentionally explicit; only incoming variables remain.
                variables = normalized_variables(incoming, {})
            body = {
                "id": group_id,
                "type": detail.get("type", "Vsts"),
                "name": group_name,
                "description": group.get("description", detail.get("description", "")),
                "variables": variables,
                "variableGroupProjectReferences": detail.get("variableGroupProjectReferences") or [{
                    "name": group_name,
                    "description": group.get("description", ""),
                    "projectReference": {"id": project_id, "name": project}
                }]
            }
            result = await ado_request(
                client, "PUT",
                ado_url(org, f"_apis/distributedtask/variablegroups/{group_id}?api-version=7.1"),
                headers, json=body
            )
            action = "updated"
        else:
            variables = normalized_variables(incoming, {})
            body = {
                "type": "Vsts",
                "name": group_name,
                "description": group.get("description", ""),
                "variables": variables,
                "variableGroupProjectReferences": [{
                    "name": group_name,
                    "description": group.get("description", ""),
                    "projectReference": {"id": project_id, "name": project}
                }]
            }
            result = await ado_request(
                client, "POST",
                ado_url(org, "_apis/distributedtask/variablegroups?api-version=7.1"),
                headers, json=body
            )
            action = "created"

    # Don't log or return the PAT or variable values.
    return JSONResponse({
        "success": True,
        "action": action,
        "groupName": result.get("name", group_name),
        "groupId": result.get("id"),
        "project": project,
        "variableCountInPayload": len(incoming),
        "mode": mode,
        "message": f"Variable Group '{group_name}' {action} successfully."
    })


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return HTMLResponse(str(exc.detail), status_code=exc.status_code)
