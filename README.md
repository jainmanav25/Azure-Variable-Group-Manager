# Azure DevOps Variable Group Manager

Internal web UI for creating a new Azure DevOps Variable Group from JSON or updating an existing group.

## Features

- JSON file upload or paste/edit.
- Basic application login.
- PAT is supplied per operation and is not persisted by the app.
- Create group if missing; update if same name exists in the selected project.
- Merge mode (default): adds/updates variables in JSON while keeping variables not mentioned.
- Replace mode: explicitly replaces the variable set; review carefully before use.
- Does not log or return variable values or PAT.
- Uses Azure DevOps REST API 7.1.

## Requirements

- Python 3.10+
- Azure DevOps Services organization at `https://dev.azure.com/ORG`
- PAT scoped to the target organization with the minimum permissions needed to read and manage variable groups and read project metadata. Exact scope availability may vary by organization policy.
- The PAT owner must have permission to manage variable groups in the target project.

## Windows PowerShell setup

```powershell
cd azure-variable-group-manager
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

$env:APP_USERNAME = "admin"
$env:APP_PASSWORD = "set-a-long-unique-password-here"
$env:SESSION_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(48))")
$env:COOKIE_SECURE = "false"
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Login with the username/password above. Keep it bound to localhost for local use.

## Linux setup

```bash
cd azure-variable-group-manager
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export APP_USERNAME='admin'
export APP_PASSWORD='use-a-long-unique-password'
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export COOKIE_SECURE='false'
uvicorn app:app --host 127.0.0.1 --port 8000
```

## JSON schema

```json
{
  "organization": "YOUR_ORG",
  "project": "YOUR_PROJECT",
  "variableGroup": {
    "name": "APP-PROD-CONFIG",
    "description": "Managed from JSON",
    "variables": {
      "APP_ENV": {"value": "production", "isSecret": false},
      "DB_PASSWORD": {"value": "use-a-secret-source", "isSecret": true}
    }
  }
}
```

`organization` can be the short organization name or the URL `https://dev.azure.com/YOUR_ORG`.
`project` is the project name, not its GUID.
`isSecret: true` marks a variable as secret in Azure DevOps. The JSON still contains the value, so protect the file and do not commit it.

## PAT guidance

Create an organization-scoped PAT with minimum required permissions and the shortest practical lifetime. Do not use a full-access PAT. The app asks for the PAT per operation and does not save it. Avoid running this as a publicly exposed service. For a shared/production deployment, use HTTPS, restrict network access, configure strong app credentials, and preferably migrate to Microsoft Entra ID authentication.

## Important update behaviour

- Merge mode is the default: variables included in JSON are added/updated; variables absent from JSON are retained.
- Replace mode removes variables absent from JSON. Use only after review.
- Azure DevOps masks secret values on reads. For existing secret variables not included in the JSON, the app sends a null value with `isSecret: true` in the update payload to preserve the existing secret. Test this behaviour in a non-production project before using it in production.
- Existing secret variables are updated only when a new value is supplied in JSON.
- This app does not link the group to a pipeline. In Classic Editor, open Variables > Variable groups > Link variable group to use it in a pipeline.

## Troubleshooting

- 401: PAT invalid/expired or wrong organization.
- 403: PAT scope or user's project permissions insufficient.
- 404: organization/project incorrect or inaccessible.
- 400: check JSON schema and API response details.
- Network errors: check proxy, firewall, and TLS inspection configuration.

## Security checklist before shared deployment

- Bind to localhost unless reverse proxy/network access is deliberately configured.
- Use HTTPS; set `COOKIE_SECURE=true` when HTTPS is enabled.
- Store app password and session secret in a secret manager/environment, not source code.
- Restrict access with network ACLs / VPN and approved users.
- Never log request bodies, PATs, or variable values.
- Keep JSON files with secrets out of Git, tickets, email, and shared folders.
- Rotate/revoke PAT immediately if exposed.
- Review Microsoft guidance on PATs; Microsoft recommends Entra authentication for new production integrations.
