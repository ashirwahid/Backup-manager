import token

from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
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
import base64

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

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
    # GitHub CIS Baseline settings
    github_repo_url: Optional[str] = None  # e.g., "owner/repo"
    github_branch: str = "main"
    github_baseline_path: str = "/"  # Path to baseline JSONs in repo
    github_pat: Optional[str] = None  # Optional for private repos
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
    # GitHub CIS Baseline settings
    github_repo_url: Optional[str] = None
    github_branch: Optional[str] = None
    github_baseline_path: Optional[str] = None
    github_pat: Optional[str] = None

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

# ============== MS Graph API Client ==============

class MSGraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.base_url = "https://graph.microsoft.com/beta"
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
    
    async def get_conditional_access_policies(self) -> List[Dict]:
        """Get conditional access policies"""
        result = await self._make_request("/identity/conditionalAccess/policies")
        return result.get("value", [])

    async def _get_single(self, endpoint: str) -> Optional[Dict]:
        """GET one Graph resource; returns None when the object does not exist."""
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}{endpoint}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self._get_single(endpoint)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            return response.json()

    async def get_user(self, user_id: str) -> Optional[Dict]:
        """Fetch a directory user referenced by a CA policy."""
        select = "id,displayName,userPrincipalName,mail,accountEnabled,userType"
        return await self._get_single(f"/users/{user_id}?$select={select}")

    async def get_group(self, group_id: str) -> Optional[Dict]:
        """Fetch a directory group referenced by a CA policy."""
        select = "id,displayName,mail,mailEnabled,securityEnabled,groupTypes"
        return await self._get_single(f"/groups/{group_id}?$select={select}")
    
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
        # Fields that are read-only and should not be sent when creating
        readonly_fields = [
            "id", "@odata.type", "createdDateTime", "lastModifiedDateTime",
            "version", "createdBy", "lastModifiedBy", "roleScopeTagIds",
            "@odata.context", "settingCount"
        ]
        
        cleaned = {k: v for k, v in policy.items() if k not in readonly_fields}
        return cleaned
    
    # Device Configuration Policies
    async def create_device_configuration(self, policy: Dict) -> Dict:
        """Create a device configuration policy"""
        cleaned = self._clean_policy_for_create(policy, "device_configuration")
        # Need to include @odata.type for creation
        if "@odata.type" in policy:
            cleaned["@odata.type"] = policy["@odata.type"]
        return await self._post_request("/deviceManagement/deviceConfigurations", cleaned)
    
    async def delete_device_configuration(self, policy_id: str) -> bool:
        """Delete a device configuration policy"""
        return await self._delete_request(f"/deviceManagement/deviceConfigurations/{policy_id}")
    
    # Configuration Policies (Settings Catalog)
    async def create_configuration_policy(self, policy: Dict) -> Dict:
        """Create a configuration policy (Settings Catalog)"""
        cleaned = self._clean_policy_for_create(policy, "configuration")
        return await self._post_request("/deviceManagement/configurationPolicies", cleaned)
    
    async def delete_configuration_policy(self, policy_id: str) -> bool:
        """Delete a configuration policy"""
        return await self._delete_request(f"/deviceManagement/configurationPolicies/{policy_id}")
    
    # Conditional Access Policies
    async def create_conditional_access_policy(self, policy: Dict) -> Dict:
        """Create a conditional access policy"""

        if "policy" in policy and isinstance(policy["policy"], dict):
            policy = policy["policy"]
        elif "baseline" in policy and isinstance(policy["baseline"], dict):
            policy = policy["baseline"]

        cleaned = self._clean_policy_for_create(policy, "conditional_access")

        # Convert baseline metadata to Graph field
        if not cleaned.get("displayName"):
            cleaned["displayName"] = (
                cleaned.get("$friendlyName")
                or cleaned.get("name")
            )

        if not cleaned.get("displayName"):
            raise HTTPException(
                status_code=400,
                detail="Conditional Access policy payload is missing 'displayName'. Baseline can provide '$friendlyName'."
            )

        # Remove non-Graph metadata fields
        cleaned.pop("$friendlyName", None)
        cleaned.pop("$description", None)

        if not cleaned.get("state"):
            cleaned["state"] = "disabled"

        logger.info(f"Conditional Access create payload: {json.dumps(cleaned, indent=2)}")
        return await self._post_request("/identity/conditionalAccess/policies", cleaned)
    
    async def delete_conditional_access_policy(self, policy_id: str) -> bool:
        """Delete a conditional access policy"""
        return await self._delete_request(f"/identity/conditionalAccess/policies/{policy_id}")
    
    # Compliance Policies
    async def create_compliance_policy(self, policy: Dict) -> Dict:
        """Create a compliance policy"""
        cleaned = self._clean_policy_for_create(policy, "compliance")
        if "@odata.type" in policy:
            cleaned["@odata.type"] = policy["@odata.type"]
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

    async def read_branch_file_text(self, file_path: str) -> Optional[str]:
        """Return UTF-8 text at path on the configured branch, or None if the file does not exist."""
        headers = self._get_auth_header()
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


DEVOPS_BACKUP_PATH_PREFIX = "/policies/backup"
DEVOPS_DEPENDENCIES_USERS_PREFIX = f"{DEVOPS_BACKUP_PATH_PREFIX}/dependencies/users"
DEVOPS_DEPENDENCIES_GROUPS_PREFIX = f"{DEVOPS_BACKUP_PATH_PREFIX}/dependencies/groups"

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
        policy.get("displayName")
        or policy.get("name")
        or policy.get("$friendlyName")
        or policy.get("id")
        or "Unnamed"
    )

def build_policy_file_path(policy_type: str, policy: Dict[str, Any]) -> str:
    policy_id = policy.get("id", str(uuid.uuid4()))
    display_name = safe_file_name(get_policy_display_name(policy))
    return f"{DEVOPS_BACKUP_PATH_PREFIX}/{policy_type}/{policy_id}__{display_name}.json"

def build_devops_backup_plan(export: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Build stable repo paths and JSON text for this export.
    One file per policy, grouped by policy type.
    """
    rows: List[Tuple[str, str]] = []

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

    def ca_payload(policies: List[Any]) -> List[Dict[str, Any]]:
        return [transform_conditional_access_policy(p) for p in policies]

    if export["policy_type"] == "all":
        for ptype in ["device_configuration", "configuration", "conditional_access", "compliance"]:
            pdata = export["policies"][ptype]["policies"]
            if ptype == "conditional_access":
                pdata = ca_payload(pdata)
            path = f"{DEVOPS_BACKUP_PATH_PREFIX}/{ptype}.json"
            rows.append((path, json.dumps(pdata, indent=2)))
        return rows

    ptype = export["policy_type"]
    payload = export["policies"]
    if ptype == "conditional_access":
        payload = ca_payload(payload)
    path = f"{DEVOPS_BACKUP_PATH_PREFIX}/{ptype}.json"
    rows.append((path, json.dumps(payload, indent=2)))
    return rows


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
    return {
        "attempted": True,
        "committed": True,
        "commit_id": commit_id,
        "message": "Pushed backup to Azure DevOps.",
        "files": file_statuses,
        "dependencies": dependency_summary,
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
        "updated_at": settings.updated_at.isoformat()
    }

@api_router.post("/settings")
async def update_settings(settings_update: SettingsUpdate):
    """Update settings"""
    existing = await db.settings.find_one({})
    
    update_data = {k: v for k, v in settings_update.model_dump().items() if v is not None}
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
        policies = await graph_client.get_configuration_policies()
        
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
        config = await graph_client.get_configuration_policies()
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

# Export History Endpoints
@api_router.get("/exports")
async def get_exports():
    """Get export history"""
    exports = await db.exports.find({}, {"_id": 0, "policies": 0}).sort("exported_at", -1).to_list(100)
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
    
    # Get recent exports
    recent_exports = await db.exports.find({}, {"_id": 0, "policies": 0}).sort("exported_at", -1).to_list(5)
    
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
        "azure_configured": azure_configured,
        "devops_configured": devops_configured,
        "github_configured": github_configured
    }


# ============== GitHub CIS Baseline Client ==============

class GitHubClient:
    def __init__(self, repo_url: str, branch: str = "main", pat: Optional[str] = None):
        self.repo_url = repo_url  # format: "owner/repo"
        self.branch = branch
        self.pat = pat
        self.api_base = "https://api.github.com"
        
    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MS-Policy-Manager"
        }
        if self.pat:
            headers["Authorization"] = f"token {self.pat}"
        return headers
    
    async def get_repo_contents(self, path: str = "") -> List[Dict]:
        """Get contents of a directory in the repo"""
        url = f"{self.api_base}/repos/{self.repo_url}/contents/{path}"
        params = {"ref": self.branch}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            return response.json()
    
    async def get_file_content(self, path: str) -> str:
        """Get content of a specific file"""
        url = f"{self.api_base}/repos/{self.repo_url}/contents/{path}"
        params = {"ref": self.branch}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get("encoding") == "base64":
                import base64
                return base64.b64decode(data["content"]).decode("utf-8")
            return data.get("content", "")
    
    async def get_all_json_files(self, path: str = "") -> List[Dict[str, Any]]:
        """Recursively get all JSON files from a path"""
        all_files = []
        
        try:
            contents = await self.get_repo_contents(path)
            
            if not isinstance(contents, list):
                contents = [contents]
            
            for item in contents:
                if item["type"] == "file" and item["name"].endswith(".json"):
                    try:
                        content = await self.get_file_content(item["path"])
                        json_data = json.loads(content)
                        all_files.append({
                            "path": item["path"],
                            "name": item["name"],
                            "data": json_data
                        })
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning(f"Failed to parse {item['path']}: {e}")
                elif item["type"] == "dir":
                    sub_files = await self.get_all_json_files(item["path"])
                    all_files.extend(sub_files)
                    
        except Exception as e:
            logger.error(f"Error fetching from GitHub: {e}")
            raise
            
        return all_files


async def get_github_client() -> GitHubClient:
    """Get GitHub client from stored settings"""
    settings = await get_settings()
    if not settings or not settings.github_repo_url:
        raise HTTPException(status_code=400, detail="GitHub repository not configured. Please update settings.")
    return GitHubClient(
        settings.github_repo_url,
        settings.github_branch or "main",
        settings.github_pat
    )


def compare_policies(tenant_policies: List[Dict], baseline_policies: List[Dict]) -> Dict:
    """
    Compare tenant policies with CIS baseline.
    Returns 4 categories: tenant_only, baseline_only, conflicting, matching
    """
    
    def get_policy_key(policy: Dict) -> str:
        """Generate a unique key for policy comparison"""
        # Try different common identifiers
        if "displayName" in policy:
            return policy["displayName"]
        if "name" in policy:
            return policy["name"]
        if "id" in policy:
            return policy["id"]
        return json.dumps(policy, sort_keys=True)[:100]
    
    def normalize_policy(policy: Dict) -> Dict:
        """Remove volatile fields for comparison"""
        excluded_keys = {"id", "@odata.type", "createdDateTime", "lastModifiedDateTime", 
                        "version", "createdBy", "lastModifiedBy", "roleScopeTagIds"}
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


@api_router.post("/baseline/compare")
async def compare_with_baseline(policy_type: str = "all"):
    """
    Compare tenant policies with CIS baseline from GitHub.
    Returns 4 categories: tenant_only, baseline_only, conflicting, matching
    """
    try:
        # Get tenant policies
        graph_client = await get_graph_client()
        
        tenant_policies = []
        if policy_type in ["all", "device_configuration"]:
            policies = await graph_client.get_device_configuration_policies()
            tenant_policies.extend(policies)
        if policy_type in ["all", "configuration"]:
            policies = await graph_client.get_configuration_policies()
            tenant_policies.extend(policies)
        if policy_type in ["all", "conditional_access"]:
            policies = await graph_client.get_conditional_access_policies()
            tenant_policies.extend(policies)
        if policy_type in ["all", "compliance"]:
            policies = await graph_client.get_compliance_policies()
            tenant_policies.extend(policies)
        
        # Get baseline policies from GitHub
        github_client = await get_github_client()
        settings = await get_settings()
        path = settings.github_baseline_path.strip("/") if settings.github_baseline_path else ""
        
        baseline_files = await github_client.get_all_json_files(path)
        
        # Extract policies from baseline files
        baseline_policies = []
        for file in baseline_files:
            data = file["data"]
            if isinstance(data, list):
                baseline_policies.extend(data)
            elif isinstance(data, dict):
                # Could be a single policy or a wrapped response
                if "value" in data:
                    baseline_policies.extend(data["value"])
                else:
                    baseline_policies.append(data)
        
        # Compare
        comparison = compare_policies(tenant_policies, baseline_policies)
        comparison["tenant_total"] = len(tenant_policies)
        comparison["baseline_total"] = len(baseline_policies)
        comparison["compared_at"] = datetime.now(timezone.utc).isoformat()
        
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

class DeletePolicyRequest(BaseModel):
    policy_id: str
    policy_type: str

class BulkDeployRequest(BaseModel):
    policies: List[Dict[str, Any]]
    policy_type: str

class BulkDeleteRequest(BaseModel):
    policy_ids: List[str]
    policy_type: str


@api_router.post("/deploy/policy")
async def deploy_policy(request: DeployPolicyRequest):
    """Deploy a single policy from baseline to tenant"""
    try:
        graph_client = await get_graph_client()
        
        policy_type = request.policy_type
        policy = request.policy
        
        result = None
        if policy_type == "device_configuration":
            result = await graph_client.create_device_configuration(policy)
        elif policy_type == "configuration":
            result = await graph_client.create_configuration_policy(policy)
        elif policy_type == "conditional_access":
            result = await graph_client.create_conditional_access_policy(policy)
        elif policy_type == "compliance":
            result = await graph_client.create_compliance_policy(policy)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown policy type: {policy_type}")
        
        # Log deployment
        deployment_record = {
            "id": str(uuid.uuid4()),
            "action": "deploy",
            "policy_type": policy_type,
            "policy_name": policy.get("displayName") or policy.get("name", "Unknown"),
            "created_policy_id": result.get("id"),
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "status": "success"
        }
        await db.deployments.insert_one(deployment_record)
        
        return {
            "success": True,
            "message": f"Policy '{policy.get('displayName', 'Unknown')}' deployed successfully",
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
    """Delete a policy from tenant"""
    try:
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
            "status": "success" if success else "failed"
        }
        await db.deployments.insert_one(deployment_record)
        
        return {
            "success": success,
            "message": f"Policy deleted successfully" if success else "Failed to delete policy"
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
        
        results = []
        for policy in request.policies:
            try:
                result = None
                if request.policy_type == "device_configuration":
                    result = await graph_client.create_device_configuration(policy)
                elif request.policy_type == "configuration":
                    result = await graph_client.create_configuration_policy(policy)
                elif request.policy_type == "conditional_access":
                    result = await graph_client.create_conditional_access_policy(policy)
                elif request.policy_type == "compliance":
                    result = await graph_client.create_compliance_policy(policy)
                
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
    """Delete multiple policies from tenant"""
    try:
        graph_client = await get_graph_client()
        
        results = []
        for policy_id in request.policy_ids:
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
        
        return {
            "success": failed == 0,
            "message": f"Deleted {successful} policies, {failed} failed",
            "results": results
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
