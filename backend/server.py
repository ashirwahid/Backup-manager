from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import contextlib
import os
import re
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional, Tuple, Set
import uuid
from datetime import datetime, timezone
import httpx
import msal
import base64
import asyncio
import json
from urllib.parse import quote

import time
import io
import zipfile
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from baseline_deploy_sanitize import (
    collect_ca_placeholder_refs,
    sanitize_policy_for_graph_deploy,
)
from configuration_settings_deploy import clean_configuration_settings_for_graph_create
from baseline_variables import (
    DEFAULT_VARIABLES_PATH,
    admin_user_id_from_variables,
    expand_variables_deep,
    find_unresolved_placeholders,
    parse_variables_json_text,
    tenant_domain_from_variables,
)
from policy_resource_layout import (
    DEVOPS_RESOURCE_CONTENT_ROOT,
    ONEDRIVE_BACKUP_FOLDER,
    ONEDRIVE_DEVOPS_SCOPE,
    POLICY_TYPE_KEYS,
    onedrive_read_scopes,
    devops_backup_commit_root,
    dependencies_groups_prefix,
    dependencies_users_prefix,
    infer_policy_type_from_resource_path,
    policy_compare_key,
    policy_content_scope,
    policy_read_scopes,
    resolve_policy_display_name,
    ui_breadcrumb_label,
)

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app
app = FastAPI(title="MS Policy Manager API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _suppress_httpx_request_info_logs():
    """Avoid httpx INFO lines for expected DevOps 404s on optional/legacy paths."""
    httpx_log = logging.getLogger("httpx")
    prev = httpx_log.level
    httpx_log.setLevel(logging.WARNING)
    try:
        yield
    finally:
        httpx_log.setLevel(prev)

# ============== Models ==============

class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    azure_tenant_id: Optional[str] = None
    azure_client_id: Optional[str] = None
    azure_client_secret: Optional[str] = None
    devops_org: Optional[str] = None
    devops_project: Optional[str] = None
    devops_repo: Optional[str] = None
    devops_pat: Optional[str] = None
    devops_branch: str = "main"
    # When set, CIS / Remove flows queue this YAML pipeline definition instead of calling Graph delete directly.
    # The pipeline should read variable BackupManagerPolicyRemovePayload (JSON: policy_type, policy_ids).
    # Use an environment with approvals in DevOps so deletes wait for human sign-off.
    devops_remove_policy_pipeline_id: Optional[int] = None
    # When True, removals only queue that pipeline; this app never calls Graph DELETE for policies.
    devops_require_pipeline_for_policy_removal: bool = False
    # GitHub CIS Baseline settings
    github_repo_url: Optional[str] = None  # e.g., "owner/repo"
    github_branch: str = "main"
    github_baseline_path: str = "Source/Resources/Content"  # Simeon-style baseline root
    github_variables_path: str = "Source/Resources/variables.json"
    github_pat: Optional[str] = None  # Optional for private repos
    # Optional inline overrides (merged on top of variables.json from GitHub/DevOps)
    baseline_variables: Optional[Dict[str, str]] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SettingsUpdate(BaseModel):
    azure_tenant_id: Optional[str] = None
    azure_client_id: Optional[str] = None
    azure_client_secret: Optional[str] = None
    devops_org: Optional[str] = None
    devops_project: Optional[str] = None
    devops_repo: Optional[str] = None
    devops_pat: Optional[str] = None
    devops_branch: Optional[str] = None
    devops_remove_policy_pipeline_id: Optional[int] = None
    devops_require_pipeline_for_policy_removal: Optional[bool] = None
    # GitHub CIS Baseline settings
    github_repo_url: Optional[str] = None
    github_branch: Optional[str] = None
    github_baseline_path: Optional[str] = None
    github_variables_path: Optional[str] = None
    github_pat: Optional[str] = None
    baseline_variables: Optional[Dict[str, str]] = None

class ExportRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    policy_type: str
    policy_count: int
    policies: List[Dict[str, Any]]
    exported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    synced_to_devops: bool = False
    devops_commit_id: Optional[str] = None

class DevOpsSyncRequest(BaseModel):
    export_id: str
    commit_message: Optional[str] = None

class DevOpsSyncResult(BaseModel):
    success: bool
    commit_id: Optional[str] = None
    message: str

class PolicyExportResponse(BaseModel):
    export_id: str
    policy_type: str
    policy_count: int
    exported_at: str


class DependencyIdsRequest(BaseModel):
    """User/group object IDs to resolve from DevOps dependency backup JSON files."""

    user_ids: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(default_factory=list)
    tenant_commit_id: Optional[str] = None  # Azure DevOps commit; omit for branch head

# ============== MS Graph API Client ==============

class MSGraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.base_url = "https://graph.microsoft.com/beta"
        self.v1_base_url = "https://graph.microsoft.com/v1.0"
        self._token = None
        self._token_expiry = None
        
    async def get_token(self) -> str:
        """Acquire access token using client credentials flow"""
        if self._token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._token
            
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None, app.acquire_token_for_client, self.scope
        )
        
        if "access_token" in result:
            self._token = result["access_token"]
            expires_in = result.get("expires_in", 3600)
            from datetime import timedelta
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 300)
            return self._token
        else:
            error = result.get("error_description", result.get("error", "Unknown error"))
            raise HTTPException(status_code=401, detail=f"Failed to acquire token: {error}")
    
    async def _make_request(self, endpoint: str) -> Dict:
        """Make authenticated request to Graph API"""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        all_results = []
        url = f"{self.base_url}{endpoint}"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            while url:
                response = await client.get(url, headers=headers)
                
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 30))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                    
                response.raise_for_status()
                data = response.json()
                
                if "value" in data:
                    all_results.extend(data["value"])
                    url = data.get("@odata.nextLink")
                else:
                    return data
                    
        return {"value": all_results}
    
    async def get_device_configuration_policies(self) -> List[Dict]:
        """Get device configuration policies"""
        result = await self._make_request("/deviceManagement/deviceConfigurations")
        return result.get("value", [])
    
    async def get_configuration_policies(self) -> List[Dict]:
        """Get configuration policies (Settings Catalog)"""
        result = await self._make_request("/deviceManagement/configurationPolicies")
        return result.get("value", [])

    async def get_configuration_policy_settings(self, policy_id: str) -> List[Dict]:
        """Settings Catalog policy settings (required to recreate the policy)."""
        data = await self._get_single(
            f"/deviceManagement/configurationPolicies/{policy_id}/settings"
        )
        if not data:
            return []
        if isinstance(data, dict) and "value" in data:
            return data.get("value") or []
        if isinstance(data, list):
            return data
        return []

    async def configuration_policy_exists(self, policy_id: str) -> bool:
        row = await self._get_single(
            f"/deviceManagement/configurationPolicies/{policy_id}"
        )
        return row is not None

    async def enrich_configuration_policies_with_settings(
        self, policies: List[Dict]
    ) -> List[Dict]:
        """Attach settings[] to each policy shell returned by the list API."""
        enriched: List[Dict] = []
        for policy in policies:
            row = dict(policy)
            if row.get("settings"):
                enriched.append(row)
                continue
            pid = row.get("id")
            if not pid:
                enriched.append(row)
                continue
            try:
                row["settings"] = await self.get_configuration_policy_settings(pid)
            except Exception as e:
                logger.warning(
                    "Could not fetch settings for configuration policy %s (%s): %s",
                    row.get("name") or pid,
                    pid,
                    e,
                )
            enriched.append(row)
        return enriched
    
    async def get_conditional_access_policies(self) -> List[Dict]:
        """Get conditional access policies"""
        result = await self._make_request("/identity/conditionalAccess/policies")
        return result.get("value", [])

    async def _get_single(
        self, endpoint: str, *, graph_version: str = "beta"
    ) -> Optional[Dict]:
        """GET one Graph resource; returns None when the object does not exist."""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        base = self.v1_base_url if graph_version == "v1" else self.base_url
        url = f"{base}{endpoint}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self._get_single(endpoint, graph_version=graph_version)

            if response.status_code in (404, 400):
                return None

            response.raise_for_status()
            return response.json()

    async def _make_request_v1(self, endpoint: str) -> Dict:
        """Paginated GET against Graph v1.0."""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        all_results: List[Dict] = []
        url: Optional[str] = f"{self.v1_base_url}{endpoint}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            while url:
                response = await client.get(url, headers=headers)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 30))
                    logger.warning("Rate limited on v1 %s, waiting %ss", endpoint, retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                data = response.json()
                if "value" in data:
                    all_results.extend(data["value"])
                    url = data.get("@odata.nextLink")
                else:
                    return data
        return {"value": all_results}

    async def get_user(self, user_id: str) -> Optional[Dict]:
        """Fetch a directory user referenced by a CA policy."""
        select = "id,displayName,userPrincipalName,mail,accountEnabled,userType"
        return await self._get_single(f"/users/{user_id}?$select={select}")

    async def get_group(self, group_id: str) -> Optional[Dict]:
        """Fetch a directory group referenced by a CA policy."""
        select = "id,displayName,mail,mailEnabled,securityEnabled,groupTypes"
        return await self._get_single(f"/groups/{group_id}?$select={select}")

    async def list_users_for_onedrive_export(self) -> List[Dict]:
        """Enabled directory users (all types) for OneDrive export."""
        select = "id,displayName,userPrincipalName,mail,accountEnabled,userType"
        filter_q = quote("accountEnabled eq true", safe="")
        result = await self._make_request_v1(
            f"/users?$select={select}&$filter={filter_q}&$top=999"
        )
        return result.get("value", [])

    async def get_user_v1(self, user_id: str) -> Optional[Dict]:
        select = "id,displayName,userPrincipalName,mail,accountEnabled,userType"
        return await self._get_single(
            f"/users/{user_id}?$select={select}", graph_version="v1"
        )

    async def list_all_drives_v1(self) -> List[Dict]:
        """All drives visible to the app (OneDrive + libraries). Prefer owner.user."""
        result = await self._make_request_v1("/drives")
        return result.get("value", [])

    async def get_user_drive(self, user_id: str) -> Optional[Dict]:
        """User's default OneDrive; None when not provisioned for app-only access."""
        select = quote(
            "id,driveType,createdDateTime,lastModifiedDateTime,webUrl,"
            "quota,owner",
            safe=",",
        )
        drive = await self._get_single(
            f"/users/{user_id}/drive?$select={select}", graph_version="v1"
        )
        if drive:
            return drive
        return await self._get_single(f"/users/{user_id}/drive?$select={select}")

    @staticmethod
    def _onedrive_snapshot(
        user: Dict[str, Any], drive: Dict[str, Any], source: str
    ) -> Dict[str, Any]:
        return {
            "id": user.get("id"),
            "displayName": user.get("displayName"),
            "userPrincipalName": user.get("userPrincipalName"),
            "mail": user.get("mail"),
            "accountEnabled": user.get("accountEnabled"),
            "userType": user.get("userType"),
            "drive": drive,
            "_exportSource": source,
        }

    @staticmethod
    def _is_user_onedrive_drive(drive: Dict[str, Any]) -> bool:
        owner = drive.get("owner") or {}
        if not (owner.get("user") or {}).get("id"):
            return False
        dtype = (drive.get("driveType") or "").lower()
        if not dtype:
            return True
        return dtype in ("business", "personal")

    async def export_onedrive_snapshots(self) -> Dict[str, Any]:
        """
        Export OneDrive metadata. Uses GET /drives first (works with app-only auth),
        then falls back to per-user /users/{id}/drive.
        """
        stats: Dict[str, Any] = {
            "users_scanned": 0,
            "drives_from_list_api": 0,
            "drives_from_user_api": 0,
            "exported_count": 0,
            "users_without_drive": 0,
        }
        snapshots_by_id: Dict[str, Dict[str, Any]] = {}

        try:
            listed = await self.list_all_drives_v1()
            stats["drives_from_list_api"] = len(listed)
            for drive in listed:
                if not self._is_user_onedrive_drive(drive):
                    continue
                owner_user = (drive.get("owner") or {}).get("user") or {}
                uid = owner_user.get("id")
                if not uid:
                    continue
                uid_key = uid.strip().lower()
                if uid_key in snapshots_by_id:
                    continue
                user = await self.get_user_v1(uid) or {
                    "id": uid,
                    "displayName": owner_user.get("displayName"),
                    "userPrincipalName": owner_user.get("email"),
                    "mail": owner_user.get("email"),
                }
                snapshots_by_id[uid_key] = self._onedrive_snapshot(
                    user, drive, "drives_list"
                )
        except Exception as e:
            logger.warning("OneDrive export: GET /drives failed: %s", e)
            stats["drives_list_error"] = str(e)

        users = await self.list_users_for_onedrive_export()
        stats["users_scanned"] = len(users)
        sem = asyncio.Semaphore(8)

        async def fill_from_user_api(user: Dict[str, Any]) -> None:
            uid = user.get("id")
            if not uid:
                return
            uid_key = uid.strip().lower()
            if uid_key in snapshots_by_id:
                return
            async with sem:
                drive = await self.get_user_drive(uid)
            if not drive:
                return
            stats["drives_from_user_api"] += 1
            snapshots_by_id[uid_key] = self._onedrive_snapshot(
                user, drive, "user_drive"
            )

        await asyncio.gather(*(fill_from_user_api(u) for u in users))

        snapshots = sorted(
            snapshots_by_id.values(),
            key=lambda r: (r.get("userPrincipalName") or r.get("displayName") or "").lower(),
        )
        stats["exported_count"] = len(snapshots)
        stats["users_without_drive"] = max(0, stats["users_scanned"] - len(snapshots))

        return {"snapshots": snapshots, "stats": stats}
    
    async def get_compliance_policies(self) -> List[Dict]:
        """Get device compliance policies"""
        result = await self._make_request("/deviceManagement/deviceCompliancePolicies")
        return result.get("value", [])

    async def get_intune_policy_assignments(
        self, policy_type: str, policy_id: str
    ) -> List[Dict]:
        """List group/user assignment targets for an Intune policy."""
        path_templates = {
            "device_configuration": "/deviceManagement/deviceConfigurations/{id}/assignments",
            "configuration": "/deviceManagement/configurationPolicies/{id}/assignments",
            "compliance": "/deviceManagement/deviceCompliancePolicies/{id}/assignments",
        }
        template = path_templates.get(policy_type)
        if not template:
            return []
        endpoint = template.format(id=policy_id)
        result = await self._make_request(endpoint)
        return result.get("value", [])

    # ============== Deploy/Delete Methods ==============
    
    async def _post_request(self, endpoint: str, data: Dict) -> Dict:
        """Make authenticated POST request to Graph API"""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{endpoint}"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=data)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self._post_request(endpoint, data)
            
            response.raise_for_status()
            return response.json() if response.content else {}
    
    async def _delete_request(self, endpoint: str) -> bool:
        """Make authenticated DELETE request to Graph API"""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{endpoint}"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.delete(url, headers=headers)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self._delete_request(endpoint)
            
            response.raise_for_status()
            return True
    
    async def _patch_request(self, endpoint: str, data: Dict) -> Dict:
        """Make authenticated PATCH request to Graph API"""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{endpoint}"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.patch(url, headers=headers, json=data)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self._patch_request(endpoint, data)
            
            response.raise_for_status()
            return response.json() if response.content else {}
    
    def _clean_policy_for_create(self, policy: Dict, policy_type: str) -> Dict:
        """Remove read-only fields before creating a policy"""
        readonly_fields = [
            "id", "@odata.type", "createdDateTime", "lastModifiedDateTime",
            "version", "createdBy", "lastModifiedBy", "roleScopeTagIds",
            "@odata.context", "settingCount",
        ]
        cleaned = {k: v for k, v in policy.items() if k not in readonly_fields}
        return cleaned

    async def resolve_directory_role_definition_id(self, display_name: str) -> Optional[str]:
        """Map Simeon role display name to tenant directory roleDefinition id."""
        name = (display_name or "").strip()
        if not name:
            return None
        escaped = name.replace("'", "''")
        try:
            data = await self._get_request(
                "/roleManagement/directory/roleDefinitions"
                f"?$filter=displayName eq '{escaped}'&$select=id,displayName"
            )
        except Exception as e:
            logger.warning("Role lookup failed for %s: %s", name, e)
            return None
        for row in data.get("value") or []:
            rid = row.get("id")
            if rid:
                return str(rid)
        return None

    async def resolve_user_id_by_local_part(
        self, local_part: str, *, domain_hint: Optional[str] = None
    ) -> Optional[str]:
        """Resolve break-glass / admin style placeholders to a user object id."""
        local = (local_part or "").strip()
        if not local:
            return None
        if domain_hint:
            uid = await self.resolve_user_id_by_upn(f"{local}@{domain_hint.lstrip('@')}")
            if uid:
                return uid
        escaped = local.replace("'", "''")
        filters = [
            f"startswith(userPrincipalName,'{escaped}@')",
            f"mailNickname eq '{escaped}'",
            f"startswith(mail,'{escaped}@')",
        ]
        for odata_filter in filters:
            try:
                data = await self._get_request(
                    f"/users?$filter={odata_filter}"
                    "&$select=id,userPrincipalName,mail&$top=1"
                )
            except Exception as e:
                logger.warning("User lookup failed for %s (%s): %s", local, odata_filter, e)
                continue
            rows = data.get("value") or []
            if rows and rows[0].get("id"):
                return str(rows[0]["id"])
        return None

    async def resolve_user_id_by_upn(self, upn: str) -> Optional[str]:
        """Resolve admin@tenant.onmicrosoft.com style URN values to a user object id."""
        principal = (upn or "").strip()
        if not principal or "@" not in principal:
            return None
        escaped = principal.replace("'", "''")
        try:
            data = await self._get_request(
                f"/users?$filter=userPrincipalName eq '{escaped}'"
                "&$select=id,userPrincipalName&$top=1"
            )
        except Exception as e:
            logger.warning("User UPN lookup failed for %s: %s", principal, e)
            return None
        rows = data.get("value") or []
        if rows and rows[0].get("id"):
            return str(rows[0]["id"])
        return None

    async def resolve_group_id_by_display_name(self, display_name: str) -> Optional[str]:
        name = (display_name or "").strip()
        if not name:
            return None
        escaped = name.replace("'", "''")
        try:
            data = await self._get_request(
                f"/groups?$filter=displayName eq '{escaped}'&$select=id,displayName&$top=1"
            )
        except Exception as e:
            logger.warning("Group lookup failed for %s: %s", name, e)
            return None
        rows = data.get("value") or []
        if rows and rows[0].get("id"):
            return str(rows[0]["id"])
        return None

    async def resolve_authentication_strength_policy_id(
        self, display_name: str
    ) -> Optional[str]:
        """Map Simeon authentication strength policy name to tenant policy id."""
        name = (display_name or "").strip()
        if not name:
            return None
        escaped = name.replace("'", "''")
        try:
            data = await self._get_request(
                "/identity/conditionalAccess/authenticationStrengthPolicies"
                f"?$filter=displayName eq '{escaped}'&$select=id,displayName&$top=1"
            )
        except Exception as e:
            logger.warning("Authentication strength lookup failed for %s: %s", name, e)
            return None
        rows = data.get("value") or []
        if rows and rows[0].get("id"):
            return str(rows[0]["id"])
        return None

    async def _resolve_ca_baseline_placeholders(
        self,
        policy: Dict,
        baseline_variables: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Dict[str, str]]:
        refs = collect_ca_placeholder_refs(policy)
        vars_map = baseline_variables or {}
        domain = tenant_domain_from_variables(vars_map)

        if domain:
            for local in list(refs.get("users") or set()):
                refs.setdefault("user_upns", set()).add(f"{local}@{domain}")

        resolved_roles: Dict[str, str] = {}
        resolved_users: Dict[str, str] = {}
        resolved_groups: Dict[str, str] = {}
        resolved_auth_strengths: Dict[str, str] = {}

        admin_override = admin_user_id_from_variables(vars_map)
        if admin_override:
            resolved_users["admin"] = admin_override
            if domain:
                resolved_users[f"admin@{domain}"] = admin_override

        for role_name in refs.get("roles") or set():
            rid = await self.resolve_directory_role_definition_id(role_name)
            if rid:
                resolved_roles[role_name] = rid
        for local in refs.get("users") or set():
            uid = await self.resolve_user_id_by_local_part(local, domain_hint=domain)
            if uid:
                resolved_users[local] = uid
        for upn in refs.get("user_upns") or set():
            if upn in resolved_users:
                continue
            uid = await self.resolve_user_id_by_upn(upn)
            if uid:
                resolved_users[upn] = uid
        for gname in refs.get("groups") or set():
            gid = await self.resolve_group_id_by_display_name(gname)
            if gid:
                resolved_groups[gname] = gid
        for strength_name in refs.get("auth_strengths") or set():
            sid = await self.resolve_authentication_strength_policy_id(strength_name)
            if sid:
                resolved_auth_strengths[strength_name] = sid
        return {
            "users": resolved_users,
            "groups": resolved_groups,
            "roles": resolved_roles,
            "auth_strengths": resolved_auth_strengths,
        }

    async def _prepare_policy_for_deploy(
        self,
        policy: Dict,
        policy_type: str,
        *,
        baseline_variables: Optional[Dict[str, str]] = None,
    ) -> Dict:
        payload = self._unwrap_policy_payload(policy)
        vars_map = baseline_variables or {}
        tenant_domain = tenant_domain_from_variables(vars_map)
        payload = expand_variables_deep(payload, vars_map)
        if policy_type == "conditional_access":
            maps = await self._resolve_ca_baseline_placeholders(payload, vars_map)
            prepared = sanitize_policy_for_graph_deploy(
                payload,
                policy_type,
                resolved_users=maps["users"],
                resolved_groups=maps["groups"],
                resolved_roles=maps["roles"],
                resolved_auth_strengths=maps["auth_strengths"],
            )
            exclude_users = (
                ((prepared.get("conditions") or {}).get("users") or {}).get("excludeUsers")
                or []
            )
            if exclude_users:
                logger.info(
                    "Conditional Access excludeUsers resolved for deploy: %s",
                    exclude_users,
                )
            else:
                users_block = (policy.get("conditions") or {}).get("users") or {}
                raw_exclude = users_block.get("excludeUsers") or []
                if raw_exclude:
                    logger.warning(
                        "Conditional Access excludeUsers were not resolved; "
                        "ensure variables.json has ResourceContext:TenantDomainName and "
                        "admin@%s exists in the tenant (or set Baseline:AdminUserId).",
                        tenant_domain or "<tenant-domain>",
                    )
            return prepared
        return sanitize_policy_for_graph_deploy(payload, policy_type)
    
    # Device Configuration Policies
    async def create_device_configuration(
        self,
        policy: Dict,
        *,
        baseline_variables: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """Create a device configuration policy"""
        prepared = await self._prepare_policy_for_deploy(
            policy, "device_configuration", baseline_variables=baseline_variables
        )
        cleaned = self._clean_policy_for_create(prepared, "device_configuration")
        if "@odata.type" in prepared:
            cleaned["@odata.type"] = prepared["@odata.type"]
        return await self._post_request("/deviceManagement/deviceConfigurations", cleaned)
    
    async def delete_device_configuration(self, policy_id: str) -> bool:
        """Delete a device configuration policy"""
        return await self._delete_request(f"/deviceManagement/deviceConfigurations/{policy_id}")
    
    def _unwrap_policy_payload(self, policy: Dict) -> Dict:
        if "policy" in policy and isinstance(policy["policy"], dict):
            return dict(policy["policy"])
        if "baseline" in policy and isinstance(policy["baseline"], dict):
            return dict(policy["baseline"])
        return dict(policy)

    def _clean_configuration_settings_for_create(
        self, settings: List[Dict]
    ) -> List[Dict]:
        return clean_configuration_settings_for_graph_create(settings)

    def _build_configuration_policy_create_body(self, policy: Dict) -> Dict:
        settings = policy.get("settings") or []
        if not settings:
            raise ValueError(
                "Settings Catalog policy is missing 'settings'. "
                "Re-export policies to DevOps or deploy while the source policy still exists in Intune."
            )

        readonly_fields = {
            "id",
            "@odata.type",
            "createdDateTime",
            "lastModifiedDateTime",
            "version",
            "createdBy",
            "lastModifiedBy",
            "settingCount",
            "@odata.context",
            "isAssigned",
            "priorityMetaData",
            "assignments",
            "expand",
            "$description",
            "$friendlyName",
            "$name",
        }
        body: Dict[str, Any] = {
            k: v
            for k, v in policy.items()
            if k not in readonly_fields and k != "settings"
        }
        body["name"] = (
            policy.get("name")
            or policy.get("displayName")
            or "Restored policy"
        )
        body.setdefault("description", policy.get("description") or "")
        if not body.get("platforms"):
            raise ValueError(
                "Settings Catalog policy is missing 'platforms' (required by Graph)."
            )
        if not body.get("technologies"):
            body["technologies"] = "mdm"
        body["settings"] = self._clean_configuration_settings_for_create(settings)
        return body

    async def _prepare_configuration_policy_for_deploy(
        self,
        policy: Dict,
        *,
        devops_backup_path: Optional[str] = None,
        devops_commit_id: Optional[str] = None,
    ) -> Dict:
        prepared = self._unwrap_policy_payload(policy)
        settings = prepared.get("settings")
        if settings:
            return prepared

        backup_path = (devops_backup_path or prepared.get("_devOpsBackupPath") or "").strip()
        if backup_path:
            devops_client = await try_get_devops_client()
            if devops_client:
                commit = _normalize_devops_commit_param(devops_commit_id)
                raw = await devops_client.read_branch_file_text(
                    backup_path, commit_id=commit
                )
                if raw:
                    try:
                        from_file = json.loads(raw)
                        if isinstance(from_file, dict) and from_file.get("settings"):
                            merged = dict(from_file)
                            merged.update(
                                {
                                    k: v
                                    for k, v in prepared.items()
                                    if k not in merged or k == "_devOpsBackupPath"
                                }
                            )
                            return merged
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON in DevOps backup %s", backup_path)

        pid = prepared.get("id")
        if pid:
            file_settings = await self.get_configuration_policy_settings(pid)
            if file_settings:
                prepared["settings"] = file_settings
                return prepared

        name = prepared.get("name") or prepared.get("displayName") or pid or "Unknown"
        if pid and not await self.configuration_policy_exists(pid):
            raise ValueError(
                f"Settings Catalog policy '{name}' is no longer in Intune (id {pid}). "
                "The DevOps backup file for this policy does not include a 'settings' array, "
                "so it cannot be recreated. Export configuration policies again (with the updated "
                "app), push to DevOps, then deploy from a backup commit that contains settings."
            )
        raise ValueError(
            f"Settings Catalog policy '{name}' has no settings in the backup and Graph "
            "could not load them. Re-export configuration policies, push to DevOps, and try again."
        )

    # Configuration Policies (Settings Catalog)
    async def create_configuration_policy(
        self,
        policy: Dict,
        *,
        devops_backup_path: Optional[str] = None,
        devops_commit_id: Optional[str] = None,
        baseline_variables: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """Create a configuration policy (Settings Catalog) with its settings."""
        prepared = await self._prepare_configuration_policy_for_deploy(
            policy,
            devops_backup_path=devops_backup_path,
            devops_commit_id=devops_commit_id,
        )
        if baseline_variables:
            prepared = expand_variables_deep(prepared, baseline_variables)
        prepared = sanitize_policy_for_graph_deploy(prepared, "configuration")
        body = self._build_configuration_policy_create_body(prepared)
        return await self._post_request("/deviceManagement/configurationPolicies", body)
    
    async def delete_configuration_policy(self, policy_id: str) -> bool:
        """Delete a configuration policy"""
        return await self._delete_request(f"/deviceManagement/configurationPolicies/{policy_id}")
    
    # Conditional Access Policies
    async def create_conditional_access_policy(
        self,
        policy: Dict,
        *,
        baseline_variables: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """Create a conditional access policy"""
        prepared = await self._prepare_policy_for_deploy(
            policy, "conditional_access", baseline_variables=baseline_variables
        )
        cleaned = self._clean_policy_for_create(prepared, "conditional_access")

        unresolved = find_unresolved_placeholders(cleaned)
        if unresolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Conditional Access policy still contains unresolved baseline placeholders "
                    f"({', '.join(unresolved[:5])}). Add Source/Resources/variables.json to your "
                    "baseline repo with ResourceContext:TenantDomainName (and related keys), "
                    "or ensure the admin/break-glass user exists in the tenant."
                ),
            )

        if not cleaned.get("displayName"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Conditional Access policy is missing displayName after baseline cleanup."
                ),
            )

        if not cleaned.get("state"):
            cleaned["state"] = "disabled"

        grant = cleaned.get("grantControls")
        session = cleaned.get("sessionControls")
        if not grant and not session:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Conditional Access policy has no valid grant or session controls after "
                    "cleanup. Authentication-strength policies require matching "
                    "Authentication strength policies in the tenant (deploy those first). "
                    "Policies with empty builtInControls need a resolved authenticationStrength id."
                ),
            )
        if isinstance(grant, dict):
            built_in = grant.get("builtInControls") or []
            auth = grant.get("authenticationStrength")
            if not built_in and not (
                isinstance(auth, dict) and auth.get("id")
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Conditional Access grantControls are invalid (Graph error 1032). "
                        "Ensure authentication strength policies exist in the tenant and "
                        "variables.json is configured, or use policies with builtInControls "
                        "such as mfa or block."
                    ),
                )

        logger.info("Conditional Access create payload (sanitized): %s", cleaned.get("displayName"))
        return await self._post_request("/identity/conditionalAccess/policies", cleaned)
    
    async def delete_conditional_access_policy(self, policy_id: str) -> bool:
        """Delete a conditional access policy"""
        return await self._delete_request(f"/identity/conditionalAccess/policies/{policy_id}")
    
    # Compliance Policies
    async def create_compliance_policy(
        self,
        policy: Dict,
        *,
        baseline_variables: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """Create a compliance policy"""
        prepared = await self._prepare_policy_for_deploy(
            policy, "compliance", baseline_variables=baseline_variables
        )
        cleaned = self._clean_policy_for_create(prepared, "compliance")
        if "@odata.type" in prepared:
            cleaned["@odata.type"] = prepared["@odata.type"]
        return await self._post_request("/deviceManagement/deviceCompliancePolicies", cleaned)
    
    async def delete_compliance_policy(self, policy_id: str) -> bool:
        """Delete a compliance policy"""
        return await self._delete_request(f"/deviceManagement/deviceCompliancePolicies/{policy_id}")


# ============== Azure DevOps Client ==============

class AzureDevOpsClient:
    def __init__(self, org: str, project: str, repo: str, pat: str, branch: str = "main"):
        self.org = (org or "").strip()
        self.project = (project or "").strip().strip("/")
        self.repo = (repo or "").strip().strip("/")
        self.pat = (pat or "").strip()
        self.branch = (branch or "main").strip()
        if self.org.startswith("https://dev.azure.com/"):
            self.org = self.org.replace("https://dev.azure.com/", "").strip("/").split("/")[0]
        self.base_url = f"https://dev.azure.com/{self.org}"
        self.repo_url = f"{self.base_url}/{self.project}/_apis/git/repositories/{self.repo}"

    def _get_auth_header(self) -> dict:
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("utf-8")
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        
    async def get_branch_head(self) -> str:
        url = f"{self.repo_url}/refs?filter=heads/{self.branch}&api-version=7.1"

        logger.info(f"DevOps refs URL: {url}")
        logger.info(f"DevOps PAT length: {len(self.pat)}")
        logger.info(f"DevOps PAT prefix: {self.pat[:4] if self.pat else ''}")
        logger.info(f"DevOps org={self.org}, project={self.project}, repo={self.repo}, branch={self.branch}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, auth=("", self.pat))

        logger.info(f"DevOps refs status: {response.status_code}")
        logger.info(f"DevOps refs response: {response.text[:500]}")

        response.raise_for_status()
        data = response.json()

        refs = data.get("value", [])
        if not refs:
            return "0000000000000000000000000000000000000000"

        return refs[0]["objectId"]

    async def read_branch_file_text(self, file_path: str) -> Optional[str]:
        path_q = quote(file_path.strip(), safe="/")
        branch_q = quote(self.branch, safe="")
        url = (
            f"{self.repo_url}/items"
            f"?path={path_q}"
            f"&versionDescriptor.version={branch_q}"
            f"&versionDescriptor.versionType=branch"
            f"&includeContent=true"
            f"&api-version=7.1"
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, auth=("", self.pat))

        if response.status_code == 404:
            return None

        response.raise_for_status()
        ctype = (response.headers.get("content-type") or "").lower()

        if "application/json" in ctype:
            try:
                data = response.json()
            except json.JSONDecodeError:
                return response.text

            if isinstance(data, dict):
                content = data.get("content")
                enc = (data.get("contentEncoding") or "").lower()
                if isinstance(content, str):
                    if "base64" in enc:
                        return base64.b64decode(content).decode("utf-8")
                    return content

            if isinstance(data, str):
                return data

        return response.text

    async def push_many_changes(self, azure_changes: List[Dict[str, Any]], commit_message: str) -> Dict:
        if not azure_changes:
            return {}

        old_object_id = await self.get_branch_head()

        push_data = {
            "refUpdates": [
                {
                    "name": f"refs/heads/{self.branch}",
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": azure_changes,
                }
            ],
        }

        url = f"{self.repo_url}/pushes?api-version=7.1"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, auth=("", self.pat), json=push_data)

        logger.info(f"DevOps push status: {response.status_code}")
        logger.info(f"DevOps push response: {response.text[:500]}")

        response.raise_for_status()
        return response.json()
    
        
    async def push_file(self, file_path: str, content: str, commit_message: str) -> Dict:
        """Push a file to the repository"""
        headers = self._get_auth_header()
        old_object_id = await self.get_branch_head()
        
        # Determine change type
        change_type = "add" if old_object_id == "0000000000000000000000000000000000000000" else "add"
        
        # Check if file exists to determine edit vs add
        try:
            check_url = f"{self.repo_url}/items?path={file_path}&api-version=7.1"
            async with httpx.AsyncClient(timeout=30.0) as client:
                check_response = await client.get(check_url, headers=headers)
                if check_response.status_code == 200:
                    change_type = "edit"
        except Exception:
            pass
        
        push_data = {
            "refUpdates": [
                {
                    "name": f"refs/heads/{self.branch}",
                    "oldObjectId": old_object_id
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": [
                        {
                            "changeType": change_type,
                            "item": {"path": file_path},
                            "newContent": {
                                "content": content,
                                "contentType": "rawtext"
                            }
                        }
                    ]
                }
            ]
        }
        
        url = f"{self.repo_url}/pushes?api-version=7.1"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=push_data)
            response.raise_for_status()
            return response.json()

    def _version_descriptor_query(self, commit_id: Optional[str] = None) -> str:
        if commit_id and commit_id.strip():
            return (
                f"versionDescriptor.version={quote(commit_id.strip(), safe='')}"
                f"&versionDescriptor.versionType=commit"
            )
        branch_q = quote(self.branch, safe="")
        return (
            f"versionDescriptor.version={branch_q}"
            f"&versionDescriptor.versionType=branch"
        )

    async def read_branch_file_text(
        self, file_path: str, *, commit_id: Optional[str] = None
    ) -> Optional[str]:
        """Return UTF-8 text at path on the branch or at a specific commit."""
        headers = self._get_auth_header()
        path_q = quote(file_path.strip(), safe="/")
        ver = self._version_descriptor_query(commit_id)
        url = (
            f"{self.repo_url}/items"
            f"?path={path_q}"
            f"&{ver}"
            f"&includeContent=true"
            f"&api-version=7.1"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        ctype = (response.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            try:
                data = response.json()
            except json.JSONDecodeError:
                return response.text
            if isinstance(data, dict):
                content = data.get("content")
                enc = (data.get("contentEncoding") or "").lower()
                if isinstance(content, str):
                    if "base64" in enc:
                        return base64.b64decode(content).decode("utf-8")
                    return content
            if isinstance(data, str):
                return data
        return response.text

    async def push_many_changes(self, azure_changes: List[Dict[str, Any]], commit_message: str) -> Dict:
        """Create one Git push containing multiple file changes."""
        if not azure_changes:
            return {}
        headers = self._get_auth_header()
        old_object_id = await self.get_branch_head()
        push_data = {
            "refUpdates": [
                {
                    "name": f"refs/heads/{self.branch}",
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": azure_changes,
                }
            ],
        }
        url = f"{self.repo_url}/pushes?api-version=7.1"
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=push_data)
            response.raise_for_status()
            return response.json()

    async def list_branch_json_blobs_under(
        self, scope_path: str, *, commit_id: Optional[str] = None
    ) -> List[str]:
        """
        List all .json blob paths under scope_path (recursive) on the branch or at a commit.
        scope_path should be like /policies/backup/configuration
        """
        headers = self._get_auth_header()
        sp = "/" + scope_path.strip().lstrip("/")
        scope_q = quote(sp, safe="/")
        ver = self._version_descriptor_query(commit_id)
        url = (
            f"{self.repo_url}/items"
            f"?scopePath={scope_q}"
            f"&recursionLevel=Full"
            f"&{ver}"
            f"&api-version=7.1"
        )
        with _suppress_httpx_request_info_logs():
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(url, headers=headers)
        if response.status_code == 404:
            logger.debug("DevOps folder not found (skipped): %s", sp)
            return []
        response.raise_for_status()
        data = response.json()
        out: List[str] = []
        for item in data.get("value", []):
            p = item.get("path") or ""
            if (item.get("gitObjectType") or "").lower() == "blob" and p.lower().endswith(".json"):
                out.append(p)
        return out

    async def list_backup_commits(
        self, item_path: Optional[str] = None, top: int = 40
    ) -> List[Dict[str, Any]]:
        """Recent commits on the configured branch that touched item_path (e.g. policies/backup)."""
        headers = self._get_auth_header()
        sp = item_path or devops_backup_commit_root().lstrip("/")
        path_q = quote("/" + sp.strip().lstrip("/"), safe="/")
        branch_q = quote(self.branch, safe="")
        url = (
            f"{self.repo_url}/commits"
            f"?searchCriteria.itemPath={path_q}"
            f"&searchCriteria.itemVersion.version={branch_q}"
            f"&searchCriteria.itemVersion.versionType=branch"
            f"&$top={int(top)}"
            f"&api-version=7.1"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        out: List[Dict[str, Any]] = []
        for c in response.json().get("value", []):
            cid = c.get("commitId") or ""
            if not cid:
                continue
            author = c.get("author") or {}
            out.append(
                {
                    "commit_id": cid,
                    "short_id": cid[:7] if len(cid) >= 7 else cid,
                    "comment": (c.get("comment") or "").strip(),
                    "committed_at": author.get("date"),
                    "author": author.get("name") or author.get("email"),
                }
            )
        return out

    async def branch_blob_exists(
        self, file_path: str, *, commit_id: Optional[str] = None
    ) -> bool:
        headers = self._get_auth_header()
        path_q = quote(file_path.strip(), safe="/")
        ver = self._version_descriptor_query(commit_id)
        url = (
            f"{self.repo_url}/items"
            f"?path={path_q}"
            f"&{ver}"
            f"&api-version=7.1"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        return response.status_code == 200

    async def push_delete_paths(self, paths: List[str], commit_message: str) -> Dict[str, Any]:
        """Delete repository paths that exist on the branch in a single push."""
        uniq: List[str] = []
        seen: Set[str] = set()
        for raw in paths:
            p = normalize_repo_path(raw)
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        existing: List[str] = []
        for p in uniq:
            if await self.branch_blob_exists(p):
                existing.append(p)
        if not existing:
            return {
                "committed": False,
                "deleted": [],
                "message": "No matching files on branch; nothing to delete.",
                "commit_id": None,
            }
        changes: List[Dict[str, Any]] = [
            {"changeType": "delete", "item": {"path": p}} for p in existing
        ]
        raw = await self.push_many_changes(changes, commit_message)
        commit_id = None
        if isinstance(raw, dict):
            commit_id = (raw.get("commits") or [{}])[0].get("commitId")
        return {
            "committed": True,
            "deleted": existing,
            "message": f"Deleted {len(existing)} file(s) from Azure DevOps.",
            "commit_id": commit_id,
        }

    async def queue_policy_remove_pipeline_run(
        self,
        pipeline_id: int,
        *,
        policy_type: str,
        policy_ids: List[str],
    ) -> Dict[str, Any]:
        """
        Queue a YAML pipeline run. Passes JSON in pipeline variable BackupManagerPolicyRemovePayload
        so the pipeline can delete policies in the tenant (e.g. via Graph after an approval gate).
        """
        proj = quote(self.project, safe="")
        url = (
            f"{self.base_url}/{proj}/_apis/pipelines/{int(pipeline_id)}/runs"
            f"?api-version=7.1-preview.1"
        )
        headers = self._get_auth_header()
        payload_obj = {
            "policy_type": policy_type,
            "policy_ids": policy_ids,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        body: Dict[str, Any] = {
            "previewRun": False,
            "resources": {
                "repositories": {
                    "self": {
                        "refName": f"refs/heads/{self.branch}",
                    }
                }
            },
            "variables": {
                "BackupManagerPolicyRemovePayload": {
                    "value": json.dumps(payload_obj),
                    "isSecret": False,
                },
            },
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            detail = response.text[:2000]
            logger.error("DevOps queue pipeline failed: %s", detail)
            raise HTTPException(
                status_code=response.status_code
                if 400 <= response.status_code < 600
                else 502,
                detail=f"Azure DevOps pipeline queue failed: {detail}",
            )
        data = response.json()
        run_id = data.get("id")
        web_url = None
        links = data.get("_links") or {}
        if isinstance(links.get("web"), dict):
            web_url = links["web"].get("href")
        if not web_url and run_id is not None:
            web_url = (
                f"https://dev.azure.com/{self.org}/{proj}/_build/results?buildId={run_id}"
            )
        return {
            "run_id": run_id,
            "state": data.get("state"),
            "web_url": web_url,
        }


def transform_conditional_access_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": policy.get("id"),
        "displayName": policy.get("displayName"),
        "state": policy.get("state"),
        "conditions": policy.get("conditions", {}),
        "grantControls": policy.get("grantControls"),
        "sessionControls": policy.get("sessionControls"),
        "templateId": policy.get("templateId")
    }


# ============== Helper Functions ==============

async def get_settings() -> Optional[SettingsModel]:
    """Get settings from database"""
    doc = await db.settings.find_one({}, {"_id": 0})
    if doc:
        if isinstance(doc.get('updated_at'), str):
            doc['updated_at'] = datetime.fromisoformat(doc['updated_at'].replace('Z', '+00:00'))
        return SettingsModel(**doc)
    return None

async def get_graph_client() -> MSGraphClient:
    """Get MS Graph client from stored settings"""
    settings = await get_settings()
    if not settings or not all([settings.azure_tenant_id, settings.azure_client_id, settings.azure_client_secret]):
        raise HTTPException(status_code=400, detail="Azure AD credentials not configured. Please update settings.")
    return MSGraphClient(settings.azure_tenant_id, settings.azure_client_id, settings.azure_client_secret)

async def get_devops_client() -> AzureDevOpsClient:
    settings = await get_settings()
    if not settings or not all([settings.devops_org, settings.devops_project, settings.devops_repo, settings.devops_pat]):
        raise HTTPException(status_code=400, detail="Azure DevOps credentials not configured. Please update settings.")

    logger.info(f"DevOps org: {settings.devops_org}")
    logger.info(f"DevOps project: {settings.devops_project}")
    logger.info(f"DevOps repo: {settings.devops_repo}")
    logger.info(f"DevOps branch: {settings.devops_branch}")

    return AzureDevOpsClient(
        settings.devops_org, 
        settings.devops_project, 
        settings.devops_repo, 
        settings.devops_pat,
        settings.devops_branch or "main"
    )


async def load_baseline_variables() -> Dict[str, str]:
    """
    Load Simeon variables.json from GitHub and/or DevOps, merged with optional settings overrides.
    DevOps values override GitHub when both are present.
    """
    settings = await get_settings()
    if not settings:
        return {}

    merged: Dict[str, str] = {}
    variables_path = (
        (settings.github_variables_path or DEFAULT_VARIABLES_PATH).strip().lstrip("/")
    )

    if settings.github_repo_url:
        try:
            github_client = await get_github_client()
            raw = await github_client.get_file_content(variables_path)
            merged.update(parse_variables_json_text(raw))
            logger.info(
                "Loaded %d baseline variable(s) from GitHub %s",
                len(merged),
                variables_path,
            )
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code != 404:
                logger.warning("GitHub variables.json load failed: %s", e)
        except Exception as e:
            logger.warning("GitHub variables.json load failed: %s", e)

    devops_client = await try_get_devops_client()
    if devops_client:
        try:
            raw = await devops_client.read_branch_file_text(variables_path)
            if raw:
                devops_vars = parse_variables_json_text(raw)
                merged.update(devops_vars)
                logger.info(
                    "Merged %d baseline variable(s) from DevOps %s",
                    len(devops_vars),
                    variables_path,
                )
        except Exception as e:
            logger.warning("DevOps variables.json load failed: %s", e)

    inline = getattr(settings, "baseline_variables", None)
    if isinstance(inline, dict):
        for key, val in inline.items():
            if val is not None:
                merged[str(key)] = str(val)

    if settings.azure_tenant_id:
        merged.setdefault("ResourceContext:TenantId", settings.azure_tenant_id)

    return merged


async def enrich_baseline_variables_from_tenant(
    graph_client: MSGraphClient, variables: Dict[str, str]
) -> Dict[str, str]:
    """Fill ResourceContext:TenantDomainName from Graph when variables.json omits it."""
    out = dict(variables or {})
    if tenant_domain_from_variables(out):
        return out
    try:
        data = await graph_client._get_request("/organization?$select=verifiedDomains")
        for domain in data.get("verifiedDomains") or []:
            if domain.get("isDefault") and domain.get("name"):
                name = str(domain["name"]).strip()
                out.setdefault("ResourceContext:TenantDomainName", name)
                out.setdefault("ResourceContext:OnMicrosoftDomainName", name)
                logger.info("Inferred ResourceContext:TenantDomainName from Graph: %s", name)
                break
    except Exception as e:
        logger.warning("Could not infer tenant domain from Graph organization: %s", e)
    return out


DEVOPS_POLICY_BACKUP_SUBFOLDERS = POLICY_TYPE_KEYS
DEVOPS_DEPENDENCIES_USERS_PREFIX = dependencies_users_prefix()
DEVOPS_DEPENDENCIES_GROUPS_PREFIX = dependencies_groups_prefix()

# CA policies use reserved strings instead of object IDs for broad inclusion rules.
CA_USER_GROUP_RESERVED_IDS = frozenset({
    "All",
    "None",
    "GuestsOrExternalUsers",
    "ExternalUsers",
})

GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


async def try_get_graph_client() -> Optional[MSGraphClient]:
    settings = await get_settings()
    if not settings or not all(
        [settings.azure_tenant_id, settings.azure_client_id, settings.azure_client_secret]
    ):
        return None
    return MSGraphClient(
        settings.azure_tenant_id,
        settings.azure_client_id,
        settings.azure_client_secret,
    )


INTUNE_ASSIGNMENT_POLICY_TYPES = frozenset({
    "device_configuration",
    "configuration",
    "compliance",
})


def is_directory_object_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(GUID_PATTERN.match(value.strip()))


def is_ca_object_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if candidate in CA_USER_GROUP_RESERVED_IDS:
        return False
    return is_directory_object_id(candidate)


def extract_ca_user_group_ids(policies: List[Dict[str, Any]]) -> Tuple[Set[str], Set[str]]:
    """Collect user and group object IDs referenced in CA policy conditions."""
    user_ids: Set[str] = set()
    group_ids: Set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in ("includeUsers", "excludeUsers") and isinstance(val, list):
                    for item in val:
                        if is_ca_object_id(item):
                            user_ids.add(item.strip())
                elif key in ("includeGroups", "excludeGroups") and isinstance(val, list):
                    for item in val:
                        if is_ca_object_id(item):
                            group_ids.add(item.strip())
                else:
                    walk(val)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for policy in policies:
        walk(policy)

    return user_ids, group_ids


def collect_ca_policies_from_export(export: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return normalized CA policies included in an export record."""
    ptype = export.get("policy_type")
    policies = export.get("policies")

    if ptype == "conditional_access":
        items = policies if isinstance(policies, list) else [policies]
        return [transform_conditional_access_policy(p) for p in items]

    if ptype == "all" and isinstance(policies, dict):
        ca_block = policies.get("conditional_access") or {}
        raw = ca_block.get("policies") or []
        return [transform_conditional_access_policy(p) for p in raw]

    return []


def collect_intune_policies_from_export(
    export: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Return (policy_type, policy) pairs for Intune policies that use assignments."""
    ptype = export.get("policy_type")
    policies = export.get("policies")
    rows: List[Tuple[str, Dict[str, Any]]] = []

    if ptype in INTUNE_ASSIGNMENT_POLICY_TYPES:
        items = policies if isinstance(policies, list) else [policies]
        return [(ptype, p) for p in items if p.get("id")]

    if ptype == "all" and isinstance(policies, dict):
        for intune_type in INTUNE_ASSIGNMENT_POLICY_TYPES:
            block = policies.get(intune_type) or {}
            for policy in block.get("policies") or []:
                if policy.get("id"):
                    rows.append((intune_type, policy))

    return rows


def extract_group_ids_from_assignments(assignments: List[Dict[str, Any]]) -> Set[str]:
    """Collect Entra group IDs from Intune assignment targets."""
    group_ids: Set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            group_id = obj.get("groupId")
            if is_directory_object_id(group_id):
                group_ids.add(group_id.strip())
            for val in obj.values():
                walk(val)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for assignment in assignments:
        walk(assignment)

    return group_ids


def build_policy_assignments_file_path(policy_type: str, policy: Dict[str, Any]) -> str:
    policy_path = build_policy_file_path(policy_type, policy)
    return policy_path.replace(".json", ".assignments.json")


def companion_assignments_backup_path(policy_json_repo_path: str) -> Optional[str]:
    """Sidecar path next to a policy JSON backup, if applicable."""
    p = (policy_json_repo_path or "").strip()
    if not p.endswith(".json") or p.endswith(".assignments.json"):
        return None
    return p[:-5] + ".assignments.json"


def normalize_repo_path(p: str) -> str:
    return "/" + p.strip().lstrip("/").replace("\\", "/")


def infer_policy_type_from_devops_backup_path(path: Optional[str]) -> Optional[str]:
    """Return policy type from a DevOps resource path (Simeon Content layout or legacy backup)."""
    return infer_policy_type_from_resource_path(path)


def infer_policy_type_from_policy(
    policy: Dict[str, Any],
    *,
    explicit: Optional[str] = None,
    devops_backup_path: Optional[str] = None,
) -> str:
    """
    Resolve deploy/delete policy_type. DevOps folder and CA export shape are more
    reliable than @odata.type (CA backups omit it after transform).
    """
    if explicit and explicit != "all":
        return explicit

    path = (devops_backup_path or policy.get("_devOpsBackupPath") or "").strip()
    from_path = infer_policy_type_from_devops_backup_path(path)
    if from_path:
        return from_path

    odata = (policy.get("@odata.type") or "").replace("_", "").lower()
    if "conditionalaccess" in odata:
        return "conditional_access"
    if "deviceconfiguration" in odata and "compliance" not in odata:
        return "device_configuration"
    if "devicecompliance" in odata or (
        "compliance" in odata and "conditional" not in odata
    ):
        return "compliance"

    # Transformed conditional access export (displayName + conditions, no @odata.type)
    if isinstance(policy.get("conditions"), dict) and (
        "grantControls" in policy or "sessionControls" in policy
    ):
        return "conditional_access"

    # Settings catalog shell
    if policy.get("platforms") is not None and policy.get("technologies") is not None:
        return "configuration"

    if policy.get("displayName") and isinstance(policy.get("conditions"), dict):
        return "conditional_access"

    return "configuration"


def _normalize_paths_by_policy_id(paths_by_policy_id: Optional[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in (paths_by_policy_id or {}).items():
        if not isinstance(k, str) or not str(v).strip():
            continue
        out[k.strip().lower()] = str(v).strip()
    return out


async def find_backup_json_paths_for_policies(
    client: AzureDevOpsClient,
    policy_type: str,
    policy_ids: List[str],
) -> List[str]:
    """Resolve policy JSON paths under Source/Resources/Content (and legacy policies/backup)."""
    wanted = {x.strip().lower() for x in policy_ids if isinstance(x, str) and x.strip()}
    if not wanted or policy_type not in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for scope in policy_read_scopes(policy_type):
        try:
            paths = await client.list_branch_json_blobs_under(scope)
        except Exception as e:
            logger.warning("list_branch_json_blobs_under failed for %s: %s", scope, e)
            continue
        for p in paths:
            pl = p.lower()
            if "/dependencies/" in pl or pl.endswith(".assignments.json"):
                continue
            base = p.rsplit("/", 1)[-1]
            sep = base.find("__")
            pid = base[:sep] if sep > 0 else ""
            if pid.lower() in wanted and p not in seen:
                seen.add(p)
                out.append(p)
    return out


async def find_backup_json_paths_for_policies_any_folder(
    client: AzureDevOpsClient,
    policy_ids: List[str],
) -> List[str]:
    """Search all policy backup folders when policy_type is unknown or wrong."""
    seen: Set[str] = set()
    acc: List[str] = []
    for folder in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
        for p in await find_backup_json_paths_for_policies(client, folder, policy_ids):
            if p not in seen:
                seen.add(p)
                acc.append(p)
    return acc


async def remove_tenant_policies_from_devops_backup(
    policy_type: str,
    policy_ids: List[str],
    *,
    paths_by_policy_id: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Delete per-policy JSON (and Intune .assignments.json sidecar if present) under policies/backup/
    after removal from the tenant (or when a remove pipeline is queued). Non-fatal on failure.
    """
    client = await try_get_devops_client()
    if not client:
        return {
            "attempted": False,
            "committed": False,
            "reason": "devops_not_configured",
            "message": "Azure DevOps not configured; skipped backup file removal.",
        }

    wanted_ids = [x.strip() for x in policy_ids if isinstance(x, str) and x.strip()]
    if not wanted_ids:
        return {"attempted": False, "committed": False, "reason": "no_policy_ids", "paths": []}

    wanted_lower = [w.lower() for w in wanted_ids]
    paths_map = _normalize_paths_by_policy_id(paths_by_policy_id)
    has_explicit = any(paths_map.get(pl) for pl in wanted_lower)
    if not has_explicit and policy_type not in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
        return {
            "attempted": False,
            "committed": False,
            "reason": "unsupported_policy_type",
            "message": f"No per-file backup layout for policy_type={policy_type}",
        }

    msg_pt = policy_type
    for pl in wanted_lower:
        raw = paths_map.get(pl)
        if raw:
            inf = infer_policy_type_from_devops_backup_path(raw)
            if inf:
                msg_pt = inf
                break

    paths: List[str] = []
    for pid, pl in zip(wanted_ids, wanted_lower):
        raw = paths_map.get(pl)
        if raw:
            paths.append(normalize_repo_path(raw))
    missing = [wanted_ids[i] for i, pl in enumerate(wanted_lower) if not paths_map.get(pl)]
    if missing:
        paths.extend(await find_backup_json_paths_for_policies_any_folder(client, missing))

    expanded: List[str] = []
    seen: Set[str] = set()
    for p in paths:
        if not p or p in seen:
            continue
        seen.add(p)
        expanded.append(p)
        side = companion_assignments_backup_path(p)
        if side and side not in seen:
            seen.add(side)
            expanded.append(side)

    if not expanded:
        return {
            "attempted": True,
            "committed": False,
            "message": "No matching backup files found for these policies.",
            "paths": [],
        }

    msg = (
        f"Remove tenant policy backup ({msg_pt}) "
        f"{wanted_ids[0] if len(wanted_ids) == 1 else f'{len(wanted_ids)} policies'}"
    )
    try:
        result = await client.push_delete_paths(expanded, msg)
        return {
            "attempted": True,
            "committed": result.get("committed", True),
            "commit_id": result.get("commit_id"),
            "message": result.get("message", "Removed backup files from Azure DevOps."),
            "paths": result.get("deleted") or expanded,
        }
    except Exception as e:
        logger.warning("DevOps backup delete failed (tenant delete already applied): %s", e)
        return {
            "attempted": True,
            "committed": False,
            "error": str(e),
            "paths": expanded,
        }


async def fetch_intune_assignments_for_export(
    graph_client: MSGraphClient,
    export: Dict[str, Any],
) -> Tuple[List[Tuple[str, str]], Set[str]]:
    """Fetch assignments for Intune policies; return backup rows and referenced group IDs."""
    rows: List[Tuple[str, str]] = []
    group_ids: Set[str] = set()
    intune_policies = collect_intune_policies_from_export(export)

    async def fetch_one(policy_type: str, policy: Dict[str, Any]) -> None:
        policy_id = policy["id"]
        try:
            assignments = await graph_client.get_intune_policy_assignments(
                policy_type, policy_id
            )
        except Exception as e:
            logger.warning(
                f"Failed to fetch assignments for {policy_type}/{policy_id}: {e}"
            )
            return

        if not assignments:
            return

        path = build_policy_assignments_file_path(policy_type, policy)
        rows.append((path, json.dumps(assignments, indent=2)))
        group_ids.update(extract_group_ids_from_assignments(assignments))

    if intune_policies:
        await asyncio.gather(*(fetch_one(pt, p) for pt, p in intune_policies))

    return rows, group_ids


async def fetch_directory_dependencies(
    graph_client: MSGraphClient,
    user_ids: Set[str],
    group_ids: Set[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve user/group GUIDs to directory objects via Graph."""
    users: List[Dict[str, Any]] = []
    groups: List[Dict[str, Any]] = []

    async def fetch_user(uid: str) -> None:
        obj = await graph_client.get_user(uid)
        if obj:
            users.append(obj)
        else:
            logger.warning(f"Dependency user not found: {uid}")

    async def fetch_group(gid: str) -> None:
        obj = await graph_client.get_group(gid)
        if obj:
            groups.append(obj)
        else:
            logger.warning(f"Dependency group not found: {gid}")

    if user_ids:
        await asyncio.gather(*(fetch_user(uid) for uid in user_ids))
    if group_ids:
        await asyncio.gather(*(fetch_group(gid) for gid in group_ids))

    return users, groups


def build_dependency_file_path(kind: str, obj: Dict[str, Any]) -> str:
    obj_id = obj.get("id", str(uuid.uuid4()))
    display_name = safe_file_name(obj.get("displayName") or obj.get("userPrincipalName") or obj_id)
    prefix = (
        DEVOPS_DEPENDENCIES_USERS_PREFIX
        if kind == "user"
        else DEVOPS_DEPENDENCIES_GROUPS_PREFIX
    )
    return f"{prefix}/{obj_id}__{display_name}.json"


def build_directory_dependency_backup_rows(
    users: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for user in users:
        rows.append((build_dependency_file_path("user", user), json.dumps(user, indent=2)))
    for group in groups:
        rows.append((build_dependency_file_path("group", group), json.dumps(group, indent=2)))
    return rows


async def try_get_devops_client() -> Optional[AzureDevOpsClient]:
    settings = await get_settings()
    if not settings or not all(
        [settings.devops_org, settings.devops_project, settings.devops_repo, settings.devops_pat]
    ):
        return None
    return AzureDevOpsClient(
        settings.devops_org,
        settings.devops_project,
        settings.devops_repo,
        settings.devops_pat,
        settings.devops_branch or "main",
    )


def json_text_semantically_equal(existing: Optional[str], new_text: str) -> bool:
    if existing is None:
        return False
    try:
        return json.loads(existing) == json.loads(new_text)
    except (json.JSONDecodeError, TypeError):
        return existing.strip() == new_text.strip()


def safe_file_name(name: Optional[str]) -> str:
    if not name:
        return "Unnamed"
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]+', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('._')
    return name or "Unnamed"

def get_policy_display_name(policy: Dict[str, Any]) -> str:
    return (
        resolve_policy_display_name(policy)
        or policy.get("id")
        or "Unnamed"
    )

def build_policy_file_path(policy_type: str, policy: Dict[str, Any]) -> str:
    policy_id = policy.get("id", str(uuid.uuid4()))
    display_name = safe_file_name(get_policy_display_name(policy))
    scope = policy_content_scope(policy_type)
    return f"{scope}/{policy_id}__{display_name}.json"


def build_onedrive_backup_file_path(record: Dict[str, Any]) -> str:
    user_id = record.get("id") or str(uuid.uuid4())
    label = safe_file_name(
        record.get("userPrincipalName") or record.get("displayName") or user_id
    )
    scope = normalize_repo_path(f"{DEVOPS_RESOURCE_CONTENT_ROOT}/{ONEDRIVE_DEVOPS_SCOPE}")
    return f"{scope}/{user_id}__{label}.json"


def build_devops_backup_plan(export: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Build stable repo paths and JSON text for this export.
    One file per policy, grouped by policy type.
    """
    rows: List[Tuple[str, str]] = []

    if export.get("policy_type") == ONEDRIVE_BACKUP_FOLDER:
        for record in export.get("policies") or []:
            if not isinstance(record, dict) or not record.get("id"):
                continue
            path = build_onedrive_backup_file_path(record)
            rows.append((path, json.dumps(record, indent=2)))
        return rows

    def normalize_policy(policy_type: str, policy: Dict[str, Any]) -> Dict[str, Any]:
        if policy_type == "conditional_access":
            return transform_conditional_access_policy(policy)
        return policy

    def add_policy_rows(policy_type: str, policies: List[Dict[str, Any]]) -> None:
        for policy in policies:
            normalized = normalize_policy(policy_type, policy)
            path = build_policy_file_path(policy_type, normalized)
            rows.append((path, json.dumps(normalized, indent=2)))

    if export["policy_type"] == "all":
        for ptype in ["device_configuration", "configuration", "conditional_access", "compliance"]:
            pdata = export["policies"][ptype]["policies"]
            add_policy_rows(ptype, pdata)
        return rows

    ptype = export["policy_type"]
    payload = export["policies"]

    if isinstance(payload, list):
        add_policy_rows(ptype, payload)
    else:
        normalized = normalize_policy(ptype, payload)
        path = build_policy_file_path(ptype, normalized)
        rows.append((path, json.dumps(normalized, indent=2)))

    return rows


def parse_policy_id_from_policy_backup_basename(filename: str) -> Optional[str]:
    """
    Policy id is the segment before the first '__' in `{id}__{display}.json` or
    `{id}__{display}.assignments.json` (display name may contain additional '__').
    """
    if not filename or not filename.endswith(".json"):
        return None
    if filename.endswith(".assignments.json"):
        stem = filename[: -len(".assignments.json")]
    else:
        stem = filename[: -len(".json")]
    sep = stem.find("__")
    if sep <= 0:
        return None
    return stem[:sep].strip()


def display_name_from_policy_backup_basename(
    filename: str, *, assignments: bool = False
) -> str:
    """Human label from `{id}__{display}.json` or `.assignments.json` backup filename."""
    if not filename:
        return "Assignments" if assignments else "Unknown"
    if filename.endswith(".assignments.json"):
        stem = filename[: -len(".assignments.json")]
        assignments = True
    elif filename.endswith(".json"):
        stem = filename[: -len(".json")]
    else:
        stem = filename
    sep = stem.find("__")
    label = stem[sep + 2 :] if sep >= 0 else stem
    label = label.replace("_", " ").strip() or stem
    if assignments and not label.lower().endswith("(assignments)"):
        label = f"{label} (Assignments)"
    return label


def wrap_devops_assignments_backup(
    fp: str, raw: Any, policy_id: Optional[str]
) -> Dict[str, Any]:
    """Turn an Intune `.assignments.json` sidecar into a comparable backup object."""
    basename = (fp or "").rsplit("/", 1)[-1]
    pid = policy_id or parse_policy_id_from_policy_backup_basename(basename)
    assignments_payload = raw if isinstance(raw, list) else raw
    return {
        "id": pid,
        "_backupKind": "assignments",
        "displayName": display_name_from_policy_backup_basename(
            basename, assignments=True
        ),
        "assignments": assignments_payload,
        "_devOpsBackupPath": fp,
    }


def desired_policy_ids_by_type_from_export(export: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Policy GUIDs (lowercased) in this export, grouped by backup folder type."""
    out: Dict[str, Set[str]] = {t: set() for t in DEVOPS_POLICY_BACKUP_SUBFOLDERS}
    out[ONEDRIVE_BACKUP_FOLDER] = set()

    def add_ids(ptype: str, policies: List[Dict[str, Any]]) -> None:
        for p in policies:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if isinstance(pid, str) and pid.strip():
                out[ptype].add(pid.strip().lower())

    ptype_all = export.get("policy_type")
    policies_root = export.get("policies")

    if ptype_all == "all" and isinstance(policies_root, dict):
        for ptype in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
            block = policies_root.get(ptype) or {}
            add_ids(ptype, block.get("policies") or [])
        return out

    if ptype_all == ONEDRIVE_BACKUP_FOLDER:
        if isinstance(policies_root, list):
            add_ids(ONEDRIVE_BACKUP_FOLDER, policies_root)
        return out

    if ptype_all not in out:
        return out

    if isinstance(policies_root, list):
        add_ids(ptype_all, policies_root)
    elif isinstance(policies_root, dict) and policies_root.get("id"):
        add_ids(ptype_all, [policies_root])
    return out


def policy_backup_types_affected_by_export(export: Dict[str, Any]) -> List[str]:
    if export.get("policy_type") == "all":
        return list(DEVOPS_POLICY_BACKUP_SUBFOLDERS)
    ptype = export.get("policy_type")
    if ptype in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
        return [ptype]
    if ptype == ONEDRIVE_BACKUP_FOLDER:
        return [ONEDRIVE_BACKUP_FOLDER]
    return []


async def collect_orphan_policy_backup_paths_for_export(
    client: AzureDevOpsClient, export: Dict[str, Any]
) -> List[str]:
    """
    Policy JSON (and matching .assignments.json) under policies/backup/<type>/ whose policy id
    is not in this export — e.g. removed from tenant before re-export.
    """
    desired = desired_policy_ids_by_type_from_export(export)
    types_scan = policy_backup_types_affected_by_export(export)
    if not types_scan:
        return []

    to_delete: List[str] = []
    seen: Set[str] = set()

    for ptype in types_scan:
        want = desired.get(ptype) or set()
        remote_paths: List[str] = []
        listed: Set[str] = set()
        if ptype == ONEDRIVE_BACKUP_FOLDER:
            scopes = onedrive_read_scopes()
        elif ptype in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
            scopes = policy_read_scopes(ptype)
        else:
            continue
        for scope in scopes:
            try:
                batch = await client.list_branch_json_blobs_under(scope)
            except Exception as e:
                logger.warning("orphan backup list failed for %s: %s", scope, e)
                continue
            for rp in batch:
                if rp not in listed:
                    listed.add(rp)
                    remote_paths.append(rp)

        for rp in remote_paths:
            pl = rp.lower()
            if "/dependencies/" in pl:
                continue
            base = rp.rsplit("/", 1)[-1]
            pid = parse_policy_id_from_policy_backup_basename(base)
            if not pid or not GUID_PATTERN.match(pid):
                continue
            if pid.lower() in want:
                continue
            norm = normalize_repo_path(rp)
            if norm not in seen and await client.branch_blob_exists(norm):
                seen.add(norm)
                to_delete.append(norm)
            if not base.endswith(".assignments.json"):
                side = companion_assignments_backup_path(norm)
                if (
                    side
                    and side not in seen
                    and await client.branch_blob_exists(side)
                ):
                    seen.add(side)
                    to_delete.append(side)

    return to_delete


async def apply_devops_backup_sync(
    export: Dict[str, Any], commit_message: Optional[str] = None
) -> Dict[str, Any]:
    """Push export to stable paths under /policies/backup; one commit if anything changed."""
    export_id = export["id"]
    client = await try_get_devops_client()
    if not client:
        return {
            "attempted": False,
            "committed": False,
            "reason": "devops_not_configured",
            "message": "Azure DevOps not configured; skipped push.",
            "dependencies": {"users": 0, "groups": 0, "assignments": 0},
        }

    plan = build_devops_backup_plan(export)

    user_ids: Set[str] = set()
    group_ids: Set[str] = set()

    ca_policies = collect_ca_policies_from_export(export)
    if ca_policies:
        ca_users, ca_groups = extract_ca_user_group_ids(ca_policies)
        user_ids.update(ca_users)
        group_ids.update(ca_groups)

    graph_client = await try_get_graph_client()
    dependency_summary: Dict[str, int] = {"users": 0, "groups": 0, "assignments": 0}

    if graph_client and collect_intune_policies_from_export(export):
        assignment_rows, assignment_group_ids = await fetch_intune_assignments_for_export(
            graph_client, export
        )
        plan.extend(assignment_rows)
        group_ids.update(assignment_group_ids)
        dependency_summary["assignments"] = len(assignment_rows)

    if graph_client and (user_ids or group_ids):
        users, groups = await fetch_directory_dependencies(
            graph_client, user_ids, group_ids
        )
        plan.extend(build_directory_dependency_backup_rows(users, groups))
        dependency_summary["users"] = len(users)
        dependency_summary["groups"] = len(groups)
    elif user_ids or group_ids:
        logger.info(
            "Policies reference directory objects but Azure AD is not configured; "
            "skipping user/group dependency backup."
        )

    azure_changes: List[Dict[str, Any]] = []
    file_statuses: List[Dict[str, Any]] = []

    for path, new_text in plan:
        old_text = await client.read_branch_file_text(path)
        if json_text_semantically_equal(old_text, new_text):
            file_statuses.append({"path": path, "committed": False, "reason": "unchanged"})
            continue
        change_type = "add" if old_text is None else "edit"
        azure_changes.append(
            {
                "changeType": change_type,
                "item": {"path": path},
                "newContent": {"content": new_text, "contentType": "rawtext"},
            }
        )
        file_statuses.append({"path": path, "committed": True, "reason": "updated"})

    try:
        orphan_paths = await collect_orphan_policy_backup_paths_for_export(client, export)
    except Exception as e:
        logger.warning("orphan policy backup scan failed: %s", e)
        orphan_paths = []

    for op in orphan_paths:
        azure_changes.append({"changeType": "delete", "item": {"path": op}})
        file_statuses.append({"path": op, "committed": True, "reason": "removed_not_in_export"})

    if not azure_changes:
        await db.exports.update_one(
            {"id": export_id},
            {"$set": {"synced_to_devops": True, "devops_sync_note": "unchanged_no_commit"}},
        )
        return {
            "attempted": True,
            "committed": False,
            "commit_id": None,
            "message": "Remote backup files already match this export; no commit created.",
            "files": file_statuses,
            "dependencies": dependency_summary,
        }

    default_msg = f"Policy backup ({export.get('policy_type')}) {export.get('exported_at', '')}"
    msg = commit_message or default_msg

    try:
        result = await client.push_many_changes(azure_changes, msg)
    except Exception as e:
        logger.error(f"DevOps backup push failed for export {export_id}: {e}")
        await db.exports.update_one(
            {"id": export_id},
            {"$set": {"synced_to_devops": False, "devops_sync_note": f"error: {str(e)[:500]}"}},
        )
        for s in file_statuses:
            if s.get("reason") == "updated":
                s["reason"] = "push_failed"
                s["committed"] = False
        return {
            "attempted": True,
            "committed": False,
            "success": False,
            "error": str(e),
            "files": file_statuses,
            "dependencies": dependency_summary,
        }

    commit_id = (result.get("commits") or [{}])[0].get("commitId")
    await db.exports.update_one(
        {"id": export_id},
        {"$set": {"synced_to_devops": True, "devops_commit_id": commit_id, "devops_sync_note": None}},
    )
    pruned = sum(1 for s in file_statuses if s.get("reason") == "removed_not_in_export")
    msg_done = "Pushed backup to Azure DevOps."
    if pruned:
        msg_done += f" Removed {pruned} file(s) that were not in this export."
    return {
        "attempted": True,
        "committed": True,
        "commit_id": commit_id,
        "message": msg_done,
        "files": file_statuses,
        "dependencies": dependency_summary,
        "orphans_removed": pruned,
    }


async def devops_backup_after_export(export_record: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return await apply_devops_backup_sync(export_record)
    except Exception as e:
        logger.exception("Unexpected error during DevOps backup hook")
        eid = export_record.get("id")
        if eid:
            await db.exports.update_one(
                {"id": eid},
                {"$set": {"devops_sync_note": f"hook_error: {str(e)[:400]}"}},
            )
        return {"attempted": True, "committed": False, "success": False, "error": str(e)}


# ============== API Endpoints ==============

@api_router.get("/")
async def root():
    return {"message": "MS Policy Manager API", "version": "1.0.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

# Settings Endpoints
@api_router.get("/settings")
async def get_settings_endpoint():
    """Get current settings (credentials masked)"""
    settings = await get_settings()
    if not settings:
        return {
            "configured": False,
            "azure_configured": False,
            "devops_configured": False,
            "github_configured": False
        }
    
    return {
        "configured": True,
        "azure_configured": bool(settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret),
        "devops_configured": bool(settings.devops_org and settings.devops_project and settings.devops_repo and settings.devops_pat),
        "github_configured": bool(settings.github_repo_url),
        "azure_tenant_id": settings.azure_tenant_id[:8] + "..." if settings.azure_tenant_id else None,
        "azure_client_id": settings.azure_client_id[:8] + "..." if settings.azure_client_id else None,
        "devops_org": settings.devops_org,
        "devops_project": settings.devops_project,
        "devops_repo": settings.devops_repo,
        "devops_branch": settings.devops_branch,
        "github_repo_url": settings.github_repo_url,
        "github_branch": settings.github_branch,
        "github_baseline_path": settings.github_baseline_path,
        "github_variables_path": getattr(
            settings, "github_variables_path", DEFAULT_VARIABLES_PATH
        ),
        "updated_at": settings.updated_at.isoformat(),
        "devops_remove_policy_pipeline_id": settings.devops_remove_policy_pipeline_id,
        "devops_remove_policy_pipeline_configured": bool(
            settings.devops_remove_policy_pipeline_id
            and settings.devops_remove_policy_pipeline_id > 0
        ),
        "devops_require_pipeline_for_policy_removal": bool(
            getattr(settings, "devops_require_pipeline_for_policy_removal", False)
        ),
    }

@api_router.post("/settings")
async def update_settings(settings_update: SettingsUpdate):
    """Update settings"""
    existing = await db.settings.find_one({})
    dump = settings_update.model_dump(exclude_unset=True)
    update_data: Dict[str, Any] = {}
    for k, v in dump.items():
        if k == "devops_remove_policy_pipeline_id":
            if v is None or (isinstance(v, int) and v <= 0):
                update_data[k] = None
            else:
                update_data[k] = int(v)
            continue
        if k == "devops_require_pipeline_for_policy_removal":
            update_data[k] = bool(v)
            continue
        if v is not None:
            update_data[k] = v
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    if existing:
        await db.settings.update_one({}, {"$set": update_data})
    else:
        update_data["id"] = str(uuid.uuid4())
        await db.settings.insert_one(update_data)

    return {"success": True, "message": "Settings updated successfully"}

@api_router.post("/settings/test-azure")
async def test_azure_connection():
    """Test Azure AD connection"""
    try:
        graph_client = await get_graph_client()
        await graph_client.get_token()
        return {"success": True, "message": "Azure AD connection successful"}
    except HTTPException as e:
        return {"success": False, "message": str(e.detail)}
    except Exception as e:
        return {"success": False, "message": str(e)}

@api_router.post("/settings/test-devops")
async def test_devops_connection():
    """Test Azure DevOps connection"""
    try:
        devops_client = await get_devops_client()
        await devops_client.get_branch_head()
        return {"success": True, "message": "Azure DevOps connection successful"}
    except HTTPException as e:
        return {"success": False, "message": str(e.detail)}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _clean_dependency_id_list(raw: List[str], max_n: int) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for x in raw:
        if not x or not isinstance(x, str):
            continue
        s = x.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out[:max_n]


def _pick_devops_blob_for_id(blob_paths: List[str], object_id: str) -> Optional[str]:
    """Match backup path `{id}__{displayName}.json` (see build_dependency_file_path)."""
    needle = f"/{object_id}__".lower()
    prefix = f"{object_id}__".lower()
    for p in blob_paths:
        if needle in p.lower():
            return p
    for p in blob_paths:
        seg = (p.split("/")[-1] or "").lower()
        if seg.startswith(prefix) and seg.endswith(".json"):
            return p
    return None


async def read_dependency_backups_from_devops(
    client: AzureDevOpsClient,
    user_ids: List[str],
    group_ids: List[str],
    *,
    commit_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]:
    user_by_id: Dict[str, Any] = {}
    group_by_id: Dict[str, Any] = {}
    missing_users: List[str] = []
    missing_groups: List[str] = []

    user_blobs = await client.list_branch_json_blobs_under(
        DEVOPS_DEPENDENCIES_USERS_PREFIX, commit_id=commit_id
    )
    group_blobs = await client.list_branch_json_blobs_under(
        DEVOPS_DEPENDENCIES_GROUPS_PREFIX, commit_id=commit_id
    )

    for uid in user_ids:
        path = _pick_devops_blob_for_id(user_blobs, uid)
        if not path:
            missing_users.append(uid)
            continue
        raw = await client.read_branch_file_text(path, commit_id=commit_id)
        if not raw:
            missing_users.append(uid)
            continue
        try:
            user_by_id[uid] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            missing_users.append(uid)

    for gid in group_ids:
        path = _pick_devops_blob_for_id(group_blobs, gid)
        if not path:
            missing_groups.append(gid)
            continue
        raw = await client.read_branch_file_text(path, commit_id=commit_id)
        if not raw:
            missing_groups.append(gid)
            continue
        try:
            group_by_id[gid] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            missing_groups.append(gid)

    return user_by_id, group_by_id, missing_users, missing_groups


async def load_tenant_policies_from_devops_backup(
    client: AzureDevOpsClient,
    policy_type: str,
    *,
    commit_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Load tenant policy JSON from Azure DevOps under Source/Resources/Content/MSGraph/...
    (same layout as export). Includes `.assignments.json` sidecars. Skips dependencies/.
    """
    if policy_type != "all" and policy_type not in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
        raise HTTPException(status_code=400, detail=f"Unknown policy_type: {policy_type}")

    folders = list(DEVOPS_POLICY_BACKUP_SUBFOLDERS) if policy_type == "all" else [policy_type]
    policy_paths: List[str] = []
    assignment_paths: List[str] = []
    seen_policy_paths: Set[str] = set()
    seen_policy_ids: Set[str] = set()
    seen_assignment_paths: Set[str] = set()
    for folder in folders:
        for scope in policy_read_scopes(folder):
            try:
                paths = await client.list_branch_json_blobs_under(
                    scope, commit_id=commit_id
                )
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response else "?"
                logger.warning("DevOps list HTTP %s for %s", code, scope)
                continue
            except Exception as e:
                logger.warning("DevOps list failed for %s: %s", scope, e)
                continue
            for p in paths:
                pl = p.lower()
                if "/dependencies/" in pl:
                    continue
                if pl.endswith(".assignments.json"):
                    if p in seen_assignment_paths:
                        continue
                    seen_assignment_paths.add(p)
                    assignment_paths.append(p)
                elif pl.endswith(".json"):
                    base = p.rsplit("/", 1)[-1]
                    pid = parse_policy_id_from_policy_backup_basename(base)
                    if pid and pid.lower() in seen_policy_ids:
                        continue
                    if pid:
                        seen_policy_ids.add(pid.lower())
                    if p in seen_policy_paths:
                        continue
                    seen_policy_paths.add(p)
                    policy_paths.append(p)

    async def read_policy_json(fp: str) -> Optional[Dict[str, Any]]:
        raw = await client.read_branch_file_text(fp, commit_id=commit_id)
        if not raw:
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in DevOps path %s", fp)
            return None
        if not isinstance(obj, dict):
            return None
        out = dict(obj)
        if not out.get("id"):
            basename = (fp or "").rsplit("/", 1)[-1]
            from_path = parse_policy_id_from_policy_backup_basename(basename)
            if from_path:
                out["id"] = from_path
        out["_devOpsBackupPath"] = fp
        return out

    async def read_assignments_json(fp: str) -> Optional[Dict[str, Any]]:
        raw = await client.read_branch_file_text(fp, commit_id=commit_id)
        if not raw:
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid assignments JSON in DevOps path %s", fp)
            return None
        basename = (fp or "").rsplit("/", 1)[-1]
        pid = parse_policy_id_from_policy_backup_basename(basename)
        return wrap_devops_assignments_backup(fp, obj, pid)

    if not policy_paths and not assignment_paths:
        return []
    loaded = await asyncio.gather(
        *[read_policy_json(p) for p in policy_paths],
        *[read_assignments_json(p) for p in assignment_paths],
    )
    return [x for x in loaded if x]


async def build_devops_policy_inventory(
    client: AzureDevOpsClient,
    *,
    commit_id: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """List policies in DevOps backup grouped by folder (for comparison UI dropdowns)."""
    inventory: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in DEVOPS_POLICY_BACKUP_SUBFOLDERS
    }
    for folder in DEVOPS_POLICY_BACKUP_SUBFOLDERS:
        policies = await load_tenant_policies_from_devops_backup(
            client, folder, commit_id=commit_id
        )
        for policy in policies:
            label = (
                policy.get("displayName")
                or policy.get("name")
                or policy.get("id")
                or "Unknown"
            )
            entry_id = policy.get("id")
            if policy.get("_backupKind") == "assignments" and entry_id:
                entry_id = f"{entry_id}:assignments"
            inventory[folder].append(
                {
                    "id": entry_id,
                    "label": label,
                    "path": policy.get("_devOpsBackupPath"),
                    "backup_kind": policy.get("_backupKind") or "policy",
                }
            )
        inventory[folder].sort(key=lambda row: (row.get("label") or "").lower())
    return inventory


def collect_directory_refs_from_policies(
    policies: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    user_ids, group_ids = extract_ca_user_group_ids(policies)
    return {
        "user_ids": sorted(user_ids),
        "group_ids": sorted(group_ids),
    }


def collect_directory_refs_from_comparison(comparison: Dict[str, Any]) -> Dict[str, List[str]]:
    """CA user/group IDs referenced across all comparison categories."""
    policies: List[Dict[str, Any]] = []
    for cat in ("tenant_only", "baseline_only"):
        for item in comparison.get(cat) or []:
            if isinstance(item, dict) and isinstance(item.get("policy"), dict):
                policies.append(item["policy"])
    for cat in ("matching", "conflicting"):
        for item in comparison.get(cat) or []:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("tenant"), dict):
                policies.append(item["tenant"])
            if isinstance(item.get("baseline"), dict):
                policies.append(item["baseline"])
    return collect_directory_refs_from_policies(policies)


@api_router.post("/devops/dependencies/read")
async def read_devops_dependency_backups(body: DependencyIdsRequest):
    """
    Load user/group JSON from Azure DevOps under policies/backup/dependencies/
    (same JSON files created when policies are exported to the repo).
    """
    user_ids = _clean_dependency_id_list(body.user_ids, 120)
    group_ids = _clean_dependency_id_list(body.group_ids, 120)
    if not user_ids and not group_ids:
        return {
            "user_by_id": {},
            "group_by_id": {},
            "missing_user_ids": [],
            "missing_group_ids": [],
            "source": "devops",
        }

    try:
        client = await get_devops_client()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    commit_id = (body.tenant_commit_id or "").strip() or None
    user_by_id, group_by_id, missing_u, missing_g = await read_dependency_backups_from_devops(
        client, user_ids, group_ids, commit_id=commit_id
    )
    return {
        "user_by_id": user_by_id,
        "group_by_id": group_by_id,
        "missing_user_ids": missing_u,
        "missing_group_ids": missing_g,
        "source": "devops",
        "tenant_commit_id": commit_id,
    }


# Policy Export Endpoints
@api_router.get("/policies/device-configuration")
async def export_device_configuration():
    """Export device configuration policies"""
    try:
        graph_client = await get_graph_client()
        policies = await graph_client.get_device_configuration_policies()
        
        # Store export record
        export_record = {
            "id": str(uuid.uuid4()),
            "policy_type": "device_configuration",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False
        }
        await db.exports.insert_one(export_record)
        devops = await devops_backup_after_export(export_record)

        return {
            "export_id": export_record["id"],
            "policy_type": "device_configuration",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"],
            "devops": devops,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export device configuration policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/policies/configuration")
async def export_configuration_policies():
    """Export configuration policies (Settings Catalog)"""
    try:
        graph_client = await get_graph_client()
        policies = await graph_client.enrich_configuration_policies_with_settings(
            await graph_client.get_configuration_policies()
        )
        
        export_record = {
            "id": str(uuid.uuid4()),
            "policy_type": "configuration",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False
        }
        await db.exports.insert_one(export_record)
        devops = await devops_backup_after_export(export_record)

        return {
            "export_id": export_record["id"],
            "policy_type": "configuration",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"],
            "devops": devops,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export configuration policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/policies/conditional-access")
async def export_conditional_access():
    """Export conditional access policies"""
    try:
        graph_client = await get_graph_client()
        policies = await graph_client.get_conditional_access_policies()

        transformed_policies = [
            transform_conditional_access_policy(policy) for policy in policies
        ]

        export_record = {
            "id": str(uuid.uuid4()),
            "policy_type": "conditional_access",
            "policy_count": len(transformed_policies),
            "policies": transformed_policies,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False
        }
        await db.exports.insert_one(export_record)
        devops = await devops_backup_after_export(export_record)

        return {
            "export_id": export_record["id"],
            "policy_type": "conditional_access",
            "policy_count": len(transformed_policies),
            "policies": transformed_policies,
            "exported_at": export_record["exported_at"],
            "devops": devops,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export conditional access policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@api_router.get("/policies/compliance")
async def export_compliance_policies():
    """Export device compliance policies"""
    try:
        graph_client = await get_graph_client()
        policies = await graph_client.get_compliance_policies()
        
        export_record = {
            "id": str(uuid.uuid4()),
            "policy_type": "compliance",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False
        }
        await db.exports.insert_one(export_record)
        devops = await devops_backup_after_export(export_record)

        return {
            "export_id": export_record["id"],
            "policy_type": "compliance",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"],
            "devops": devops,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export compliance policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/policies/onedrive")
async def export_onedrive():
    """Export provisioned OneDrive drives for member users to DevOps under policies/backup/onedrive/."""
    try:
        graph_client = await get_graph_client()
        export_result = await graph_client.export_onedrive_snapshots()
        snapshots = export_result.get("snapshots") or []
        stats = export_result.get("stats") or {}

        export_record = {
            "id": str(uuid.uuid4()),
            "policy_type": ONEDRIVE_BACKUP_FOLDER,
            "policy_count": len(snapshots),
            "policies": snapshots,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False,
            "onedrive_export_stats": stats,
        }
        await db.exports.insert_one(export_record)
        devops = await devops_backup_after_export(export_record)

        message = None
        if not snapshots and stats.get("users_scanned", 0) > 0:
            message = (
                "No OneDrive drives found. Common causes: missing Files.Read.All (application), "
                "OneDrive not provisioned yet (user must sign in once), or no license. "
                f"Scanned {stats.get('users_scanned')} users; "
                f"GET /drives returned {stats.get('drives_from_list_api', 0)} drives."
            )

        return {
            "export_id": export_record["id"],
            "policy_type": ONEDRIVE_BACKUP_FOLDER,
            "policy_count": len(snapshots),
            "policies": snapshots,
            "exported_at": export_record["exported_at"],
            "devops": devops,
            "stats": stats,
            "message": message,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to export OneDrive snapshots: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/policies/export-all")
async def export_all_policies():
    """Export all policy types"""
    try:
        graph_client = await get_graph_client()
        
        results = {}
        
        # Device Configuration
        device_config = await graph_client.get_device_configuration_policies()
        results["device_configuration"] = {
            "count": len(device_config),
            "policies": device_config
        }
        
        # Configuration Policies
        config = await graph_client.enrich_configuration_policies_with_settings(
            await graph_client.get_configuration_policies()
        )
        results["configuration"] = {
            "count": len(config),
            "policies": config
        }
        
        # Conditional Access
        ca = await graph_client.get_conditional_access_policies()
        results["conditional_access"] = {
            "count": len(ca),
            "policies": ca
        }
        
        # Compliance
        compliance = await graph_client.get_compliance_policies()
        results["compliance"] = {
            "count": len(compliance),
            "policies": compliance
        }
        
        # Store all exports
        export_id = str(uuid.uuid4())
        export_record = {
            "id": export_id,
            "policy_type": "all",
            "policy_count": sum([r["count"] for r in results.values()]),
            "policies": results,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False
        }
        await db.exports.insert_one(export_record)
        devops = await devops_backup_after_export(export_record)

        return {
            "export_id": export_id,
            "policy_type": "all",
            "total_count": export_record["policy_count"],
            "breakdown": {k: v["count"] for k, v in results.items()},
            "exported_at": export_record["exported_at"],
            "devops": devops,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export all policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _exported_at_sort_ts(value: Any) -> float:
    """Normalize exported_at for sorting (Mongo may store ISO strings or BSON datetimes)."""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0.0
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0
    return 0.0


async def list_export_summaries(limit: int, scan_cap: int = 500) -> List[Dict[str, Any]]:
    """
    Export rows without policies blob. Sort in Python so Mongo does not error when
    exported_at mixes string (isoformat) and datetime across documents.
    """
    rows = await db.exports.find({}, {"_id": 0, "policies": 0}).to_list(scan_cap)
    rows.sort(key=lambda d: _exported_at_sort_ts(d.get("exported_at")), reverse=True)
    return rows[:limit]


# Export History Endpoints
@api_router.get("/exports")
async def get_exports():
    """Get export history"""
    exports = await list_export_summaries(100)
    return {"exports": exports, "count": len(exports)}

@api_router.get("/exports/{export_id}")
async def get_export(export_id: str):
    """Get specific export details"""
    export = await db.exports.find_one({"id": export_id}, {"_id": 0})
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")
    return export

@api_router.delete("/exports/{export_id}")
async def delete_export(export_id: str):
    """Delete an export record"""
    result = await db.exports.delete_one({"id": export_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Export not found")
    return {"success": True, "message": "Export deleted"}

# DevOps Sync Endpoints
@api_router.post("/devops/sync")
async def sync_to_devops(request: DevOpsSyncRequest):
    """Re-run Azure DevOps backup for an existing export (stable paths, commit only if content changed)."""
    export = await db.exports.find_one({"id": request.export_id}, {"_id": 0})
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")

    result = await apply_devops_backup_sync(export, request.commit_message)

    if result.get("reason") == "devops_not_configured":
        raise HTTPException(
            status_code=400,
            detail="Azure DevOps credentials not configured. Please update settings.",
        )
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "success": True,
        "committed": result.get("committed", False),
        "commit_id": result.get("commit_id"),
        "message": result.get("message", ""),
        "files": result.get("files", []),
        "dependencies": result.get(
            "dependencies", {"users": 0, "groups": 0, "assignments": 0}
        ),
    }

# Dashboard Stats
@api_router.get("/dashboard/stats")
async def get_dashboard_stats():
    """Get dashboard statistics"""
    total_exports = await db.exports.count_documents({})
    synced_exports = await db.exports.count_documents({"synced_to_devops": True})
    
    # Get recent exports (avoid Mongo sort on mixed-type exported_at)
    recent_exports = await list_export_summaries(5)

    recent_deployments = (
        await db.deployments.find({"action": "deploy"}, {"_id": 0})
        .sort("deployed_at", -1)
        .limit(10)
        .to_list(10)
    )
    
    # Count by policy type
    pipeline = [
        {"$group": {"_id": "$policy_type", "count": {"$sum": 1}}}
    ]
    type_counts = await db.exports.aggregate(pipeline).to_list(10)
    policy_type_counts = {item["_id"]: item["count"] for item in type_counts}
    
    settings = await get_settings()
    
    # Check if all required Azure AD credentials are configured
    azure_configured = bool(
        settings and 
        settings.azure_tenant_id and 
        settings.azure_client_id and 
        settings.azure_client_secret
    )
    
    # Check if all required DevOps credentials are configured
    devops_configured = bool(
        settings and 
        settings.devops_org and 
        settings.devops_project and 
        settings.devops_repo and 
        settings.devops_pat
    )
    
    # Check if GitHub baseline is configured
    github_configured = bool(
        settings and 
        settings.github_repo_url
    )
    
    return {
        "total_exports": total_exports,
        "synced_exports": synced_exports,
        "pending_sync": total_exports - synced_exports,
        "policy_type_counts": policy_type_counts,
        "recent_exports": recent_exports,
        "recent_deployments": recent_deployments,
        "azure_configured": azure_configured,
        "devops_configured": devops_configured,
        "github_configured": github_configured
    }


# ============== GitHub CIS Baseline Client ==============

GITHUB_RATE_LIMIT_HELP = (
    "GitHub API rate limit exceeded. Under Settings, add a GitHub Personal Access Token with read "
    "access to this repository (classic token: `repo` scope; fine-grained: Contents read). "
    "Without a token, GitHub allows only about 60 API requests per hour per IP."
)


class GitHubClient:
    def __init__(self, repo_url: str, branch: str = "main", pat: Optional[str] = None):
        self.repo_url = repo_url  # format: "owner/repo"
        self.branch = branch
        self.pat = (pat or "").strip() or None
        self.api_base = "https://api.github.com"

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "MS-Policy-Manager/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.pat:
            # Bearer is required for fine-grained PATs; classic PATs also accept it.
            headers["Authorization"] = f"Bearer {self.pat}"
        return headers

    def _is_github_rate_limit(self, response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        try:
            msg = (response.json().get("message") or "").lower()
        except Exception:
            msg = (response.text or "").lower()
        return "rate limit" in msg

    def _github_rate_limit_sleep_seconds(self, response: httpx.Response, attempt: int) -> float:
        reset = response.headers.get("x-ratelimit-reset")
        if reset:
            try:
                target = float(reset) + 2.0
                wait = target - time.time()
                if wait > 0:
                    return min(max(wait, 2.0), 3600.0)
            except ValueError:
                pass
        return min(30.0 * (2**attempt), 300.0)

    async def _github_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        last: Optional[httpx.Response] = None
        for attempt in range(6):
            response = await client.request(
                method,
                url,
                headers=self._get_headers(),
                params=params,
                follow_redirects=True,
            )
            last = response
            if response.status_code in (403, 429) and self._is_github_rate_limit(response):
                if attempt < 5:
                    wait = self._github_rate_limit_sleep_seconds(response, attempt)
                    logger.warning(
                        "GitHub rate limit hit (%s); sleeping %.1fs before retry %s/5",
                        response.status_code,
                        wait,
                        attempt + 1,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise HTTPException(status_code=503, detail=GITHUB_RATE_LIMIT_HELP)
            response.raise_for_status()
            return response
        if last is not None:
            last.raise_for_status()
        raise HTTPException(status_code=502, detail="GitHub request failed after retries.")

    async def get_repo_contents(self, path: str = "") -> List[Dict]:
        """Get contents of a directory in the repo"""
        url = f"{self.api_base}/repos/{self.repo_url}/contents/{path}"
        params = {"ref": self.branch}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await self._github_request(client, "GET", url, params=params)
            return response.json()

    async def _get_file_content_with_client(
        self, client: httpx.AsyncClient, path: str
    ) -> str:
        url = f"{self.api_base}/repos/{self.repo_url}/contents/{path}"
        params = {"ref": self.branch}
        response = await self._github_request(client, "GET", url, params=params)
        data = response.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")
        return data.get("content", "")

    async def get_file_content(self, path: str) -> str:
        """Get content of a specific file"""
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await self._get_file_content_with_client(client, path)

    async def _get_all_json_from_zipball(
        self, client: httpx.AsyncClient, baseline_prefix: str
    ) -> List[Dict[str, Any]]:
        """Fetch repo archive in one API call, then read JSON files under baseline_prefix."""
        ref = quote(self.branch, safe="")
        url = f"{self.api_base}/repos/{self.repo_url}/zipball/{ref}"
        response = await self._github_request(client, "GET", url)
        buf = io.BytesIO(response.content)
        all_files: List[Dict[str, Any]] = []
        with zipfile.ZipFile(buf) as zf:
            names = [n for n in zf.namelist() if n and not n.endswith("/")]
            if not names:
                return []
            nested = [n for n in names if "/" in n]
            root = ""
            if nested:
                head = nested[0].split("/", 1)[0]
                prefix = head + "/"
                if all(n.startswith(prefix) for n in names):
                    root = prefix
            for name in names:
                if not name.lower().endswith(".json"):
                    continue
                inner = name[len(root) :] if root and name.startswith(root) else name
                inner = inner.lstrip("/")
                if baseline_prefix:
                    if inner != baseline_prefix and not inner.startswith(baseline_prefix + "/"):
                        continue
                try:
                    with zf.open(name) as member:
                        raw = member.read()
                    json_data = json.loads(raw.decode("utf-8"))
                    all_files.append(
                        {
                            "path": inner.replace("\\", "/"),
                            "name": Path(inner).name,
                            "data": json_data,
                        }
                    )
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning("Failed to parse %s from zipball: %s", inner, e)
        return all_files

    async def _get_all_json_recursive(
        self, client: httpx.AsyncClient, path: str
    ) -> List[Dict[str, Any]]:
        """Legacy: walk Contents API (many requests; may hit rate limits without a PAT)."""
        all_files: List[Dict[str, Any]] = []
        url = f"{self.api_base}/repos/{self.repo_url}/contents/{path}"
        params = {"ref": self.branch}
        response = await self._github_request(client, "GET", url, params=params)
        contents = response.json()
        if not isinstance(contents, list):
            contents = [contents]
        for item in contents:
            if item["type"] == "file" and item["name"].endswith(".json"):
                try:
                    text = await self._get_file_content_with_client(client, item["path"])
                    json_data = json.loads(text)
                    all_files.append(
                        {"path": item["path"], "name": item["name"], "data": json_data}
                    )
                except (json.JSONDecodeError, Exception) as e:
                    logger.warning("Failed to parse %s: %s", item.get("path"), e)
            elif item["type"] == "dir":
                sub = await self._get_all_json_recursive(client, item["path"])
                all_files.extend(sub)
        return all_files

    async def list_commits(self, path: str = "", per_page: int = 30) -> List[Dict[str, Any]]:
        """Recent commits on the configured branch, optionally filtered by path."""
        url = f"{self.api_base}/repos/{self.repo_url}/commits"
        params: Dict[str, Any] = {"sha": self.branch, "per_page": int(per_page)}
        p = (path or "").strip().strip("/")
        if p:
            params["path"] = p
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await self._github_request(client, "GET", url, params=params)
        return response.json()

    async def get_all_json_files(self, path: str = "") -> List[Dict[str, Any]]:
        """
        Load baseline JSON files. Prefers the repository zipball (one REST call) to avoid
        hundreds of per-file Contents API requests that trigger GitHub rate limits.
        """
        baseline_prefix = (path or "").strip("/")
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            try:
                return await self._get_all_json_from_zipball(client, baseline_prefix)
            except (zipfile.BadZipFile, httpx.HTTPStatusError, OSError) as e:
                logger.warning(
                    "GitHub zipball baseline fetch failed (%s); falling back to recursive Contents API",
                    e,
                )
                try:
                    return await self._get_all_json_recursive(client, baseline_prefix)
                except Exception as inner:
                    logger.error("GitHub recursive baseline fetch failed: %s", inner)
                    raise


async def get_github_client(baseline_ref: Optional[str] = None) -> GitHubClient:
    """Get GitHub client from stored settings; baseline_ref overrides branch (branch name or commit SHA)."""
    settings = await get_settings()
    if not settings or not settings.github_repo_url:
        raise HTTPException(status_code=400, detail="GitHub repository not configured. Please update settings.")
    ref = (baseline_ref or "").strip() or (settings.github_branch or "main")
    return GitHubClient(
        settings.github_repo_url,
        ref,
        settings.github_pat,
    )


def _annotate_baseline_policy(policy: Dict[str, Any], source_path: str) -> Dict[str, Any]:
    """Attach GitHub path and ensure displayName for compare/UI (Simeon uses $friendlyName)."""
    if not isinstance(policy, dict):
        return policy
    out = {**policy, "_cisBaselineSourcePath": source_path}
    label = resolve_policy_display_name(out, source_path=source_path)
    if label:
        if not out.get("displayName"):
            out["displayName"] = label
        if not out.get("name"):
            out["name"] = label
    return out


def _expand_baseline_file_to_policies(file: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn one baseline JSON file into one or more policy objects for comparison."""
    data = file.get("data")
    src_path = file.get("path") or ""
    policies: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                policies.append(_annotate_baseline_policy(entry, src_path))
    elif isinstance(data, dict):
        if "value" in data and isinstance(data["value"], list):
            for entry in data["value"]:
                if isinstance(entry, dict):
                    policies.append(_annotate_baseline_policy(entry, src_path))
        else:
            policies.append(_annotate_baseline_policy(data, src_path))
    return policies


def _filter_baseline_files_by_policy_type(
    files: List[Dict[str, Any]], policy_type: str
) -> List[Dict[str, Any]]:
    if policy_type == "all":
        return files
    out: List[Dict[str, Any]] = []
    for file in files:
        path = file.get("path") or ""
        inferred = infer_policy_type_from_resource_path(path)
        if inferred == policy_type:
            out.append(file)
    return out


async def build_baseline_policy_inventory(
    github_client: GitHubClient,
    path: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """List CIS baseline policies from GitHub grouped by type (for comparison filter dropdown)."""
    inventory: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in DEVOPS_POLICY_BACKUP_SUBFOLDERS
    }
    files = await github_client.get_all_json_files(path)
    seen: Dict[str, Set[str]] = {t: set() for t in DEVOPS_POLICY_BACKUP_SUBFOLDERS}
    for file in files:
        src = file.get("path") or ""
        ptype = infer_policy_type_from_resource_path(src)
        if not ptype or ptype not in inventory:
            continue
        for policy in _expand_baseline_file_to_policies(file):
            label = resolve_policy_display_name(policy, source_path=src) or "Unknown"
            key = policy_compare_key(policy)
            if key in seen[ptype]:
                continue
            seen[ptype].add(key)
            inventory[ptype].append(
                {
                    "id": key,
                    "label": label,
                    "path": src,
                    "source": "baseline",
                }
            )
        inventory[ptype].sort(key=lambda row: (row.get("label") or "").lower())
    return inventory


def _normalize_devops_commit_param(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip()
    if v in ("", "latest"):
        return None
    return v


def compare_policies(
    tenant_policies: List[Dict],
    baseline_policies: List[Dict],
    *,
    match_by_id: bool = False,
) -> Dict:
    """
  Compare two policy sets (tenant vs baseline/CIS, or current tenant vs older tenant snapshot).
  Returns 4 categories: tenant_only, baseline_only, conflicting, matching
    """

    def get_policy_key(policy: Dict) -> str:
        return policy_compare_key(policy, match_by_id=match_by_id)
    
    def normalize_policy(policy: Dict) -> Dict:
        """Remove volatile fields for comparison"""
        excluded_keys = {"id", "@odata.type", "createdDateTime", "lastModifiedDateTime", 
                        "version", "createdBy", "lastModifiedBy", "roleScopeTagIds",
                        "_cisBaselineSourcePath", "_devOpsBackupPath", "_backupKind"}
        return {k: v for k, v in policy.items() if k not in excluded_keys}
    
    # Build lookup dictionaries
    tenant_by_name = {}
    for p in tenant_policies:
        key = get_policy_key(p)
        tenant_by_name[key] = p
    
    baseline_by_name = {}
    for p in baseline_policies:
        key = get_policy_key(p)
        baseline_by_name[key] = p
    
    tenant_keys = set(tenant_by_name.keys())
    baseline_keys = set(baseline_by_name.keys())
    
    # Calculate differences
    tenant_only_keys = tenant_keys - baseline_keys
    baseline_only_keys = baseline_keys - tenant_keys
    common_keys = tenant_keys & baseline_keys
    
    matching = []
    conflicting = []
    
    for key in common_keys:
        tenant_normalized = normalize_policy(tenant_by_name[key])
        baseline_normalized = normalize_policy(baseline_by_name[key])
        
        if tenant_normalized == baseline_normalized:
            matching.append({
                "name": key,
                "tenant": tenant_by_name[key],
                "baseline": baseline_by_name[key]
            })
        else:
            # Find specific differences
            differences = []
            all_keys = set(tenant_normalized.keys()) | set(baseline_normalized.keys())
            for k in all_keys:
                t_val = tenant_normalized.get(k)
                b_val = baseline_normalized.get(k)
                if t_val != b_val:
                    differences.append({
                        "field": k,
                        "tenant_value": t_val,
                        "baseline_value": b_val
                    })
            
            conflicting.append({
                "name": key,
                "tenant": tenant_by_name[key],
                "baseline": baseline_by_name[key],
                "differences": differences
            })
    
    tenant_only = [{"name": k, "policy": tenant_by_name[k]} for k in tenant_only_keys]
    baseline_only = [{"name": k, "policy": baseline_by_name[k]} for k in baseline_only_keys]
    
    return {
        "tenant_only": tenant_only,
        "baseline_only": baseline_only,
        "conflicting": conflicting,
        "matching": matching,
        "summary": {
            "tenant_only_count": len(tenant_only),
            "baseline_only_count": len(baseline_only),
            "conflicting_count": len(conflicting),
            "matching_count": len(matching)
        }
    }


# ============== CIS Baseline Comparison Endpoints ==============

@api_router.get("/baseline/files")
async def get_baseline_files():
    """Get list of baseline files from GitHub"""
    try:
        github_client = await get_github_client()
        settings = await get_settings()
        path = settings.github_baseline_path.strip("/") if settings.github_baseline_path else ""

        files = await github_client.get_all_json_files(path)

        return {
            "files": [{"path": f["path"], "name": f["name"]} for f in files],
            "count": len(files)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch baseline files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/baseline/variables")
async def get_baseline_variables_status():
    """Return loaded baseline variables (for deploy placeholder resolution)."""
    settings = await get_settings()
    variables = await load_baseline_variables()
    variables_path = (
        (settings.github_variables_path if settings else None) or DEFAULT_VARIABLES_PATH
    )
    resource_keys = sorted(
        k for k in variables if k.startswith("ResourceContext:")
    )
    return {
        "variables_path": variables_path.strip().lstrip("/"),
        "count": len(variables),
        "resource_context_keys": resource_keys,
        "resource_context": {k: variables[k] for k in resource_keys},
        "loaded": len(variables) > 0,
    }


@api_router.get("/baseline/comparison-sources")
async def get_comparison_sources(
    tenant_commit_id: Optional[str] = None,
    include_inventory: bool = False,
    include_baseline_snapshots: bool = True,
):
    """
    Tenant snapshots (Azure DevOps commits) and CIS baseline refs (GitHub) for CIS comparison UI.

    By default only commit lists are returned (fast). Set include_inventory=true to scan all
    policy JSON files for the policy filter dropdown. Set include_baseline_snapshots=false when
    only tenant commit history is needed (tenant vs previous commit mode).
    """
    settings = await get_settings()
    tenant_commit = _normalize_devops_commit_param(tenant_commit_id)
    devops_branch = (settings.devops_branch if settings else None) or "main"
    gh_branch = (settings.github_branch if settings else None) or "main"
    gh_repo = (settings.github_repo_url if settings else None) or ""
    gh_path = (settings.github_baseline_path or "/").strip("/") if settings else ""

    tenant_snapshots: List[Dict[str, Any]] = [
        {
            "id": "latest",
            "commit_id": None,
            "short_id": None,
            "label": f"Tenant · latest ({devops_branch})",
            "committed_at": None,
            "comment": "Current branch head in Azure DevOps",
        }
    ]
    devops_client = await try_get_devops_client()
    if devops_client:
        try:
            for c in await devops_client.list_backup_commits():
                cid = c["commit_id"]
                comment = (c.get("comment") or "Policy backup").strip()
                date_s = ""
                if c.get("committed_at"):
                    try:
                        date_s = str(c["committed_at"])[:10]
                    except Exception:
                        pass
                label = f"Tenant · {c.get('short_id') or cid[:7]}"
                if date_s:
                    label += f" · {date_s}"
                label += f" — {comment[:80]}"
                tenant_snapshots.append(
                    {
                        "id": cid,
                        "commit_id": cid,
                        "short_id": c.get("short_id"),
                        "label": label,
                        "committed_at": c.get("committed_at"),
                        "comment": comment,
                        "author": c.get("author"),
                    }
                )
        except Exception as e:
            logger.warning("Failed to list DevOps backup commits: %s", e)

    policy_inventory: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in DEVOPS_POLICY_BACKUP_SUBFOLDERS
    }
    baseline_inventory: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in DEVOPS_POLICY_BACKUP_SUBFOLDERS
    }
    if include_inventory and devops_client:
        try:
            policy_inventory = await build_devops_policy_inventory(
                devops_client, commit_id=tenant_commit
            )
        except Exception as e:
            logger.warning("Failed to build DevOps policy inventory: %s", e)

    if include_inventory and settings and settings.github_repo_url:
        try:
            gh_inv_client = await get_github_client()
            baseline_inventory = await build_baseline_policy_inventory(
                gh_inv_client, gh_path
            )
        except Exception as e:
            logger.warning("Failed to build GitHub baseline inventory: %s", e)

    baseline_options: List[Dict[str, Any]] = [
        {
            "id": "latest",
            "ref": gh_branch,
            "label": f"CIS baseline · latest ({gh_branch})",
            "repo": gh_repo,
            "path": gh_path,
        }
    ]
    if include_baseline_snapshots and settings and settings.github_repo_url:
        try:
            gh_client = await get_github_client()
            for c in await gh_client.list_commits(path=gh_path, per_page=35):
                sha = c.get("sha") or ""
                if not sha:
                    continue
                inner = c.get("commit") or {}
                msg = (inner.get("message") or "").split("\n")[0].strip()
                date_s = (inner.get("author") or {}).get("date") or ""
                if date_s:
                    date_s = date_s[:10]
                label = f"CIS baseline · {sha[:7]}"
                if date_s:
                    label += f" · {date_s}"
                if msg:
                    label += f" — {msg[:80]}"
                baseline_options.append(
                    {
                        "id": sha,
                        "ref": sha,
                        "label": label,
                        "repo": gh_repo,
                        "path": gh_path,
                        "committed_at": (inner.get("author") or {}).get("date"),
                    }
                )
        except Exception as e:
            logger.warning("Failed to list GitHub baseline commits: %s", e)

    return {
        "tenant_snapshots": tenant_snapshots,
        "baseline_options": baseline_options,
        "policy_inventory": policy_inventory,
        "baseline_inventory": baseline_inventory,
        "policy_kind_labels": {
            k: ui_breadcrumb_label(k) for k in DEVOPS_POLICY_BACKUP_SUBFOLDERS
        },
        "resource_content_root": DEVOPS_RESOURCE_CONTENT_ROOT.strip("/"),
        "devops_branch": devops_branch,
        "github_branch": gh_branch,
        "github_repo": gh_repo,
        "github_baseline_path": gh_path,
    }


def _snapshot_label(commit_id: Optional[str], branch: str, role: str) -> str:
    if commit_id is None:
        return f"{role} · latest ({branch})"
    return f"{role} · {commit_id[:7]}"


@api_router.post("/baseline/compare")
async def compare_with_baseline(
    policy_type: str = "all",
    tenant_commit_id: Optional[str] = None,
    baseline_ref: Optional[str] = None,
    compare_mode: str = "cis",
    tenant_reference_commit_id: Optional[str] = None,
):
    """
    Compare policies. Modes:
    - cis (default): tenant DevOps snapshot vs CIS baseline on GitHub
    - tenant: two tenant DevOps snapshots (current vs older commit) for revert planning
    """
    try:
        devops_client = await try_get_devops_client()
        if not devops_client:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Comparison loads tenant snapshots from Azure DevOps under Source/Resources/Content/. "
                    "Configure Azure DevOps in Settings, then export policies."
                ),
            )

        mode = (compare_mode or "cis").strip().lower()
        if mode not in ("cis", "tenant"):
            raise HTTPException(
                status_code=400,
                detail="compare_mode must be 'cis' or 'tenant'",
            )

        tenant_commit = _normalize_devops_commit_param(tenant_commit_id)

        if mode == "tenant":
            reference_commit = _normalize_devops_commit_param(tenant_reference_commit_id)
            if not reference_commit:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Pick an older tenant snapshot to compare against "
                        "(tenant_reference_commit_id)."
                    ),
                )
            if reference_commit == tenant_commit:
                raise HTTPException(
                    status_code=400,
                    detail="Current and previous tenant snapshots must be different commits.",
                )

            current_policies = await load_tenant_policies_from_devops_backup(
                devops_client, policy_type, commit_id=tenant_commit
            )
            reference_policies = await load_tenant_policies_from_devops_backup(
                devops_client, policy_type, commit_id=reference_commit
            )

            def _when(cid: Optional[str]) -> str:
                return f"commit {cid[:7]}" if cid else f"branch {devops_client.branch}"

            if not current_policies:
                raise HTTPException(
                    status_code=400,
                    detail=f"No tenant policies at current snapshot ({_when(tenant_commit)}).",
                )
            if not reference_policies:
                raise HTTPException(
                    status_code=400,
                    detail=f"No tenant policies at previous snapshot ({_when(reference_commit)}).",
                )

            comparison = compare_policies(
                current_policies, reference_policies, match_by_id=True
            )
            current_label = _snapshot_label(
                tenant_commit, devops_client.branch, "Current"
            )
            reference_label = _snapshot_label(
                reference_commit, devops_client.branch, "Previous"
            )
            comparison["comparison_mode"] = "tenant"
            comparison["tenant_total"] = len(current_policies)
            comparison["baseline_total"] = len(reference_policies)
            comparison["compared_at"] = datetime.now(timezone.utc).isoformat()
            comparison["tenant_source"] = "devops"
            comparison["tenant_snapshot"] = {
                "commit_id": tenant_commit,
                "is_latest": tenant_commit is None,
                "branch": devops_client.branch,
                "label": current_label,
                "role": "current",
            }
            comparison["reference_snapshot"] = {
                "commit_id": reference_commit,
                "is_latest": False,
                "branch": devops_client.branch,
                "label": reference_label,
                "role": "previous",
            }
            comparison["is_historical_tenant"] = tenant_commit is not None
            comparison["directory_refs"] = collect_directory_refs_from_comparison(
                comparison
            )
            comparison["column_labels"] = {
                "tenant_only": {
                    "title": "Only in current",
                    "subtitle": current_label,
                    "hint": "Added after the older backup",
                },
                "baseline_only": {
                    "title": "Only in previous",
                    "subtitle": reference_label,
                    "hint": "Removed since then — review to restore",
                },
                "conflicting": {
                    "title": "Changed",
                    "subtitle": f"{current_label} vs {reference_label}",
                    "hint": "Same policy, different settings",
                },
                "matching": {
                    "title": "Unchanged",
                    "subtitle": "Same in both snapshots",
                    "hint": "No difference between versions",
                },
                "diff_current": "Current",
                "diff_previous": "Previous",
            }
            return comparison

        # --- CIS baseline mode ---
        tenant_policies = await load_tenant_policies_from_devops_backup(
            devops_client, policy_type, commit_id=tenant_commit
        )
        if not tenant_policies:
            when = (
                f"commit {tenant_commit[:7]}"
                if tenant_commit
                else f"branch {devops_client.branch}"
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No tenant policy JSON found under Source/Resources/Content/ in Azure DevOps at {when}. "
                    "Run a policy export or pick another snapshot."
                ),
            )

        settings = await get_settings()
        baseline_ref_clean = _normalize_devops_commit_param(baseline_ref)
        effective_baseline_ref = baseline_ref_clean or (
            (settings.github_branch if settings else None) or "main"
        )

        github_client = await get_github_client(baseline_ref_clean)
        path = settings.github_baseline_path.strip("/") if settings and settings.github_baseline_path else ""

        baseline_files = await github_client.get_all_json_files(path)
        baseline_files = _filter_baseline_files_by_policy_type(baseline_files, policy_type)

        baseline_policies: List[Dict[str, Any]] = []
        for file in baseline_files:
            baseline_policies.extend(_expand_baseline_file_to_policies(file))

        comparison = compare_policies(tenant_policies, baseline_policies)
        comparison["directory_refs"] = collect_directory_refs_from_comparison(comparison)
        comparison["comparison_mode"] = "cis"
        comparison["tenant_total"] = len(tenant_policies)
        comparison["baseline_total"] = len(baseline_policies)
        comparison["compared_at"] = datetime.now(timezone.utc).isoformat()
        comparison["tenant_source"] = "devops"
        comparison["tenant_snapshot"] = {
            "commit_id": tenant_commit,
            "is_latest": tenant_commit is None,
            "branch": devops_client.branch,
            "label": _snapshot_label(tenant_commit, devops_client.branch, "Tenant"),
        }
        comparison["baseline_snapshot"] = {
            "ref": effective_baseline_ref,
            "is_latest": baseline_ref_clean is None,
            "repo": settings.github_repo_url if settings else None,
            "path": path,
            "label": (
                f"CIS · latest ({settings.github_branch if settings else 'main'})"
                if baseline_ref_clean is None
                else f"CIS · {effective_baseline_ref[:7]}"
            ),
        }
        comparison["is_historical_tenant"] = tenant_commit is not None
        comparison["column_labels"] = {
            "tenant_only": {
                "title": "Tenant only",
                "subtitle": "In tenant, not in CIS",
                "hint": "Can be removed from tenant",
            },
            "baseline_only": {
                "title": "Baseline only",
                "subtitle": "In CIS, not in tenant",
                "hint": "Can be deployed to tenant",
            },
            "conflicting": {
                "title": "Conflicting",
                "subtitle": "Tenant vs CIS",
                "hint": "Different settings",
            },
            "matching": {
                "title": "Matching",
                "subtitle": "Tenant matches CIS",
                "hint": "In sync",
            },
            "diff_current": "Tenant",
            "diff_previous": "CIS baseline",
        }

        return comparison
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to compare with baseline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/settings/test-github")
async def test_github_connection():
    """Test GitHub connection"""
    try:
        github_client = await get_github_client()
        settings = await get_settings()
        path = settings.github_baseline_path.strip("/") if settings.github_baseline_path else ""
        
        # Try to list contents
        contents = await github_client.get_repo_contents(path)
        file_count = len([c for c in contents if c.get("type") == "file" and c.get("name", "").endswith(".json")])
        
        return {
            "success": True, 
            "message": f"GitHub connection successful. Found {len(contents)} items, {file_count} JSON files in path."
        }
    except HTTPException as e:
        return {"success": False, "message": str(e.detail)}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ============== Deploy/Delete Policy Endpoints ==============

class DeployPolicyRequest(BaseModel):
    policy: Dict[str, Any]
    policy_type: str  # device_configuration, configuration, conditional_access, compliance
    devops_backup_path: Optional[str] = None
    devops_commit_id: Optional[str] = None

class DeletePolicyRequest(BaseModel):
    policy_id: str
    policy_type: str
    devops_backup_path: Optional[str] = None  # e.g. /policies/backup/conditional_access/{id}__name.json


class BulkDeployRequest(BaseModel):
    policies: List[Dict[str, Any]]
    policy_type: str
    devops_commit_id: Optional[str] = None

class BulkDeleteRequest(BaseModel):
    policy_ids: List[str]
    policy_type: str
    # Optional map of policy_id -> repo path from CIS `_devOpsBackupPath` for precise deletes.
    devops_backup_paths_by_id: Optional[Dict[str, str]] = None


async def try_queue_policy_removal_pipeline(
    settings: Optional[SettingsModel],
    policy_type: str,
    policy_ids: List[str],
) -> Optional[Dict[str, Any]]:
    """
    If settings.devops_remove_policy_pipeline_id is set, queue that YAML pipeline with
    variable BackupManagerPolicyRemovePayload instead of deleting via Graph in-process.
    """
    if not settings:
        return None
    pid = settings.devops_remove_policy_pipeline_id
    if not pid or pid <= 0:
        return None
    clean_ids = [x.strip() for x in policy_ids if isinstance(x, str) and x.strip()]
    if not clean_ids:
        raise HTTPException(status_code=400, detail="No policy IDs provided")
    client = await try_get_devops_client()
    if not client:
        raise HTTPException(
            status_code=400,
            detail="Remove pipeline ID is set but Azure DevOps credentials are incomplete.",
        )
    return await client.queue_policy_remove_pipeline_run(
        int(pid),
        policy_type=policy_type,
        policy_ids=clean_ids,
    )


def _removal_pipeline_only_mode(settings: Optional[SettingsModel]) -> bool:
    """When True, the API must not call Graph to delete policies—only queue the remove pipeline."""
    return bool(settings and getattr(settings, "devops_require_pipeline_for_policy_removal", False))


REMOVAL_PIPELINE_ONLY_DETAIL = (
    "Policy removal is set to Azure DevOps pipeline only. Configure a positive "
    '"Policy remove pipeline (definition ID)" in Settings (with DevOps org/project/repo/PAT). '
    "Add an Environment with Approvals on the job in your YAML that performs the Microsoft Graph delete."
)


@api_router.post("/deploy/policy")
async def deploy_policy(request: DeployPolicyRequest):
    """Deploy a single policy from baseline to tenant"""
    try:
        graph_client = await get_graph_client()
        baseline_variables = await enrich_baseline_variables_from_tenant(
            graph_client, await load_baseline_variables()
        )

        policy = request.policy
        policy_type = infer_policy_type_from_policy(
            policy,
            explicit=request.policy_type,
            devops_backup_path=request.devops_backup_path,
        )

        result = None
        if policy_type == "device_configuration":
            result = await graph_client.create_device_configuration(
                policy, baseline_variables=baseline_variables
            )
        elif policy_type == "configuration":
            try:
                result = await graph_client.create_configuration_policy(
                    policy,
                    devops_backup_path=request.devops_backup_path,
                    devops_commit_id=request.devops_commit_id,
                    baseline_variables=baseline_variables,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif policy_type == "conditional_access":
            result = await graph_client.create_conditional_access_policy(
                policy, baseline_variables=baseline_variables
            )
        elif policy_type == "compliance":
            result = await graph_client.create_compliance_policy(
                policy, baseline_variables=baseline_variables
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown policy type: {policy_type}")
        
        # Log deployment
        deployment_record = {
            "id": str(uuid.uuid4()),
            "action": "deploy",
            "policy_type": policy_type,
            "policy_name": policy.get("displayName")
            or policy.get("name")
            or "Unknown",
            "created_policy_id": result.get("id"),
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "status": "success"
        }
        await db.deployments.insert_one(deployment_record)
        
        deployed_label = policy.get("displayName") or policy.get("name") or "Unknown"
        return {
            "success": True,
            "message": f"Policy '{deployed_label}' deployed successfully",
            "created_policy": result
        }
        
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        error_detail = e.response.text if e.response else str(e)
        logger.error(f"Failed to deploy policy: {error_detail}")
        raise HTTPException(status_code=e.response.status_code if e.response else 500, detail=error_detail)
    except Exception as e:
        logger.error(f"Failed to deploy policy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.delete("/deploy/policy")
async def delete_policy(request: DeletePolicyRequest):
    """Delete a policy from tenant, or queue an Azure DevOps pipeline when configured."""
    try:
        settings = await get_settings()
        pipeline_out = await try_queue_policy_removal_pipeline(
            settings, request.policy_type, [request.policy_id]
        )
        if pipeline_out is not None:
            ts = datetime.now(timezone.utc).isoformat()
            deployment_record = {
                "id": str(uuid.uuid4()),
                "action": "delete_pipeline_queued",
                "policy_type": request.policy_type,
                "policy_id": request.policy_id,
                "policy_ids": [request.policy_id],
                "pipeline_run_id": pipeline_out.get("run_id"),
                "pipeline_run_state": pipeline_out.get("state"),
                "pipeline_web_url": pipeline_out.get("web_url"),
                "deployed_at": ts,
                "deleted_at": ts,
                "status": "queued",
            }
            await db.deployments.insert_one(deployment_record)
            devops_backup: Optional[Dict[str, Any]] = None
            try:
                p_strip = (request.devops_backup_path or "").strip()
                devops_backup = await remove_tenant_policies_from_devops_backup(
                    request.policy_type,
                    [request.policy_id.strip()],
                    paths_by_policy_id=(
                        {request.policy_id.strip().lower(): p_strip} if p_strip else None
                    ),
                )
            except Exception as e:
                logger.warning(
                    "DevOps backup cleanup failed after remove pipeline was queued: %s",
                    e,
                )
                devops_backup = {"attempted": True, "error": str(e)}
            return {
                "success": True,
                "mode": "devops_pipeline",
                "message": (
                    "Removal queued in Azure DevOps. If the pipeline uses an environment with "
                    "approvals, approve the run before the policy is deleted in the tenant."
                ),
                "pipeline_run_id": pipeline_out.get("run_id"),
                "pipeline_run_url": pipeline_out.get("web_url"),
                "devops_backup": devops_backup,
            }

        if _removal_pipeline_only_mode(settings):
            raise HTTPException(status_code=400, detail=REMOVAL_PIPELINE_ONLY_DETAIL)

        graph_client = await get_graph_client()

        policy_type = request.policy_type
        policy_id = request.policy_id

        success = False
        if policy_type == "device_configuration":
            success = await graph_client.delete_device_configuration(policy_id)
        elif policy_type == "configuration":
            success = await graph_client.delete_configuration_policy(policy_id)
        elif policy_type == "conditional_access":
            success = await graph_client.delete_conditional_access_policy(policy_id)
        elif policy_type == "compliance":
            success = await graph_client.delete_compliance_policy(policy_id)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown policy type: {policy_type}")

        # Log deletion
        deployment_record = {
            "id": str(uuid.uuid4()),
            "action": "delete",
            "policy_type": policy_type,
            "policy_id": policy_id,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "status": "success" if success else "failed",
        }
        await db.deployments.insert_one(deployment_record)

        devops_backup: Optional[Dict[str, Any]] = None
        if success:
            try:
                p_strip = (request.devops_backup_path or "").strip()
                devops_backup = await remove_tenant_policies_from_devops_backup(
                    policy_type,
                    [policy_id],
                    paths_by_policy_id=(
                        {policy_id.strip().lower(): p_strip} if p_strip else None
                    ),
                )
            except Exception as e:
                logger.warning(
                    "DevOps backup cleanup failed after tenant policy delete (tenant change already applied): %s",
                    e,
                )
                devops_backup = {"attempted": True, "error": str(e)}

        return {
            "success": success,
            "mode": "direct_graph",
            "message": "Policy deleted successfully" if success else "Failed to delete policy",
            "devops_backup": devops_backup,
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        error_detail = e.response.text if e.response else str(e)
        logger.error(f"Failed to delete policy: {error_detail}")
        raise HTTPException(status_code=e.response.status_code if e.response else 500, detail=error_detail)
    except Exception as e:
        logger.error(f"Failed to delete policy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/deploy/bulk")
async def bulk_deploy_policies(request: BulkDeployRequest):
    """Deploy multiple policies from baseline to tenant"""
    try:
        graph_client = await get_graph_client()
        baseline_variables = await enrich_baseline_variables_from_tenant(
            graph_client, await load_baseline_variables()
        )
        
        results = []
        for policy in request.policies:
            try:
                result = None
                if request.policy_type == "device_configuration":
                    result = await graph_client.create_device_configuration(
                        policy, baseline_variables=baseline_variables
                    )
                elif request.policy_type == "configuration":
                    result = await graph_client.create_configuration_policy(
                        policy,
                        devops_backup_path=policy.get("_devOpsBackupPath"),
                        devops_commit_id=request.devops_commit_id,
                        baseline_variables=baseline_variables,
                    )
                elif request.policy_type == "conditional_access":
                    result = await graph_client.create_conditional_access_policy(
                        policy, baseline_variables=baseline_variables
                    )
                elif request.policy_type == "compliance":
                    result = await graph_client.create_compliance_policy(
                        policy, baseline_variables=baseline_variables
                    )
                
                results.append({
                    "policy_name": policy.get("displayName") or policy.get("name", "Unknown"),
                    "success": True,
                    "created_id": result.get("id") if result else None
                })
            except Exception as e:
                results.append({
                    "policy_name": policy.get("displayName") or policy.get("name", "Unknown"),
                    "success": False,
                    "error": str(e)
                })
        
        successful = sum(1 for r in results if r["success"])
        failed = len(results) - successful

        ts = datetime.now(timezone.utc).isoformat()
        for row in results:
            if not row.get("success"):
                continue
            await db.deployments.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "action": "deploy",
                    "policy_type": request.policy_type,
                    "policy_name": row.get("policy_name") or "Unknown",
                    "created_policy_id": row.get("created_id"),
                    "deployed_at": ts,
                    "status": "success",
                }
            )
        
        return {
            "success": failed == 0,
            "message": f"Deployed {successful} policies, {failed} failed",
            "results": results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed bulk deploy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/deploy/bulk-delete")
async def bulk_delete_policies(request: BulkDeleteRequest):
    """Delete multiple policies from tenant, or queue one Azure DevOps pipeline when configured."""
    try:
        settings = await get_settings()
        ids = [x.strip() for x in request.policy_ids if isinstance(x, str) and x.strip()]
        if not ids:
            raise HTTPException(status_code=400, detail="No policy IDs provided")

        pipeline_out = await try_queue_policy_removal_pipeline(
            settings, request.policy_type, ids
        )
        if pipeline_out is not None:
            ts = datetime.now(timezone.utc).isoformat()
            deployment_record = {
                "id": str(uuid.uuid4()),
                "action": "delete_pipeline_queued",
                "policy_type": request.policy_type,
                "policy_ids": ids,
                "pipeline_run_id": pipeline_out.get("run_id"),
                "pipeline_run_state": pipeline_out.get("state"),
                "pipeline_web_url": pipeline_out.get("web_url"),
                "deployed_at": ts,
                "deleted_at": ts,
                "status": "queued",
            }
            await db.deployments.insert_one(deployment_record)
            devops_backup: Optional[Dict[str, Any]] = None
            try:
                devops_backup = await remove_tenant_policies_from_devops_backup(
                    request.policy_type,
                    ids,
                    paths_by_policy_id=request.devops_backup_paths_by_id,
                )
            except Exception as e:
                logger.warning(
                    "DevOps backup cleanup failed after bulk remove pipeline was queued: %s",
                    e,
                )
                devops_backup = {"attempted": True, "error": str(e)}
            return {
                "success": True,
                "mode": "devops_pipeline",
                "message": (
                    f"Removal of {len(ids)} policies queued in Azure DevOps. "
                    "Approve the pipeline run (if required) before the tenant is updated."
                ),
                "pipeline_run_id": pipeline_out.get("run_id"),
                "pipeline_run_url": pipeline_out.get("web_url"),
                "devops_backup": devops_backup,
            }

        if _removal_pipeline_only_mode(settings):
            raise HTTPException(status_code=400, detail=REMOVAL_PIPELINE_ONLY_DETAIL)

        graph_client = await get_graph_client()

        results = []
        for policy_id in ids:
            try:
                success = False
                if request.policy_type == "device_configuration":
                    success = await graph_client.delete_device_configuration(policy_id)
                elif request.policy_type == "configuration":
                    success = await graph_client.delete_configuration_policy(policy_id)
                elif request.policy_type == "conditional_access":
                    success = await graph_client.delete_conditional_access_policy(policy_id)
                elif request.policy_type == "compliance":
                    success = await graph_client.delete_compliance_policy(policy_id)
                
                results.append({
                    "policy_id": policy_id,
                    "success": success
                })
            except Exception as e:
                results.append({
                    "policy_id": policy_id,
                    "success": False,
                    "error": str(e)
                })
        
        successful = sum(1 for r in results if r["success"])
        failed = len(results) - successful

        successful_ids = [r["policy_id"] for r in results if r.get("success")]
        devops_backup: Optional[Dict[str, Any]] = None
        if successful_ids:
            try:
                devops_backup = await remove_tenant_policies_from_devops_backup(
                    request.policy_type,
                    successful_ids,
                    paths_by_policy_id=request.devops_backup_paths_by_id,
                )
            except Exception as e:
                logger.warning(
                    "DevOps backup cleanup failed after tenant bulk delete (tenant changes already applied): %s",
                    e,
                )
                devops_backup = {"attempted": True, "error": str(e)}

        return {
            "success": failed == 0,
            "mode": "direct_graph",
            "message": f"Deleted {successful} policies, {failed} failed",
            "results": results,
            "devops_backup": devops_backup,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed bulk delete: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/deploy/history")
async def get_deployment_history():
    """Get deployment history"""
    deployments = await db.deployments.find({}, {"_id": 0}).sort("deployed_at", -1).to_list(100)
    return {"deployments": deployments, "count": len(deployments)}


# Include the router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
