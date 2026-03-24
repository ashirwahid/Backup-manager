from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime, timezone
import httpx
import msal
import base64
import asyncio
import json

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
    
    async def get_compliance_policies(self) -> List[Dict]:
        """Get device compliance policies"""
        result = await self._make_request("/deviceManagement/deviceCompliancePolicies")
        return result.get("value", [])


# ============== Azure DevOps Client ==============

class AzureDevOpsClient:
    def __init__(self, org: str, project: str, repo: str, pat: str, branch: str = "main"):
        self.org = org
        self.project = project
        self.repo = repo
        self.pat = pat
        self.branch = branch
        self.base_url = f"https://dev.azure.com/{org}"
        self.repo_url = f"{self.base_url}/{project}/_apis/git/repositories/{repo}"
        
    def _get_auth_header(self) -> Dict[str, str]:
        """Create authorization header"""
        credentials = f":{self.pat}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json"
        }
    
    async def get_branch_head(self) -> str:
        """Get the latest commit ID of the branch"""
        headers = self._get_auth_header()
        url = f"{self.repo_url}/refs?filter=heads/{self.branch}&api-version=7.1"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            refs = data.get("value", [])
            if not refs:
                # Branch doesn't exist - return zeros for initial commit
                return "0000000000000000000000000000000000000000"
            
            return refs[0]["objectId"]
    
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
    """Get Azure DevOps client from stored settings"""
    settings = await get_settings()
    if not settings or not all([settings.devops_org, settings.devops_project, settings.devops_repo, settings.devops_pat]):
        raise HTTPException(status_code=400, detail="Azure DevOps credentials not configured. Please update settings.")
    return AzureDevOpsClient(
        settings.devops_org, 
        settings.devops_project, 
        settings.devops_repo, 
        settings.devops_pat,
        settings.devops_branch or "main"
    )


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
            "devops_configured": False
        }
    
    return {
        "configured": True,
        "azure_configured": bool(settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret),
        "devops_configured": bool(settings.devops_org and settings.devops_project and settings.devops_repo and settings.devops_pat),
        "azure_tenant_id": settings.azure_tenant_id[:8] + "..." if settings.azure_tenant_id else None,
        "azure_client_id": settings.azure_client_id[:8] + "..." if settings.azure_client_id else None,
        "devops_org": settings.devops_org,
        "devops_project": settings.devops_project,
        "devops_repo": settings.devops_repo,
        "devops_branch": settings.devops_branch,
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
        
        return {
            "export_id": export_record["id"],
            "policy_type": "device_configuration",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"]
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
        
        return {
            "export_id": export_record["id"],
            "policy_type": "configuration",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"]
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
        
        export_record = {
            "id": str(uuid.uuid4()),
            "policy_type": "conditional_access",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_devops": False
        }
        await db.exports.insert_one(export_record)
        
        return {
            "export_id": export_record["id"],
            "policy_type": "conditional_access",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"]
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
        
        return {
            "export_id": export_record["id"],
            "policy_type": "compliance",
            "policy_count": len(policies),
            "policies": policies,
            "exported_at": export_record["exported_at"]
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
        
        return {
            "export_id": export_id,
            "policy_type": "all",
            "total_count": export_record["policy_count"],
            "breakdown": {k: v["count"] for k, v in results.items()},
            "exported_at": export_record["exported_at"]
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
    """Sync an export to Azure DevOps"""
    try:
        # Get export record
        export = await db.exports.find_one({"id": request.export_id}, {"_id": 0})
        if not export:
            raise HTTPException(status_code=404, detail="Export not found")
        
        devops_client = await get_devops_client()
        
        policy_type = export["policy_type"]
        timestamp = export["exported_at"].replace(":", "-").replace(".", "-")
        
        if policy_type == "all":
            # Push each policy type as separate file
            commit_message = request.commit_message or f"Export all policies - {timestamp}"
            results = []
            
            for ptype, pdata in export["policies"].items():
                file_path = f"/policies/{ptype}/{timestamp}.json"
                content = json.dumps(pdata["policies"], indent=2)
                result = await devops_client.push_file(file_path, content, f"Add {ptype} policies")
                results.append(result)
            
            # Update export record
            commit_id = results[-1].get("commits", [{}])[0].get("commitId", "unknown")
            await db.exports.update_one(
                {"id": request.export_id},
                {"$set": {"synced_to_devops": True, "devops_commit_id": commit_id}}
            )
            
            return {
                "success": True,
                "commit_id": commit_id,
                "message": "Successfully synced all policies to Azure DevOps"
            }
        else:
            # Single policy type
            file_path = f"/policies/{policy_type}/{timestamp}.json"
            content = json.dumps(export["policies"], indent=2)
            commit_message = request.commit_message or f"Export {policy_type} policies - {timestamp}"
            
            result = await devops_client.push_file(file_path, content, commit_message)
            commit_id = result.get("commits", [{}])[0].get("commitId", "unknown")
            
            # Update export record
            await db.exports.update_one(
                {"id": request.export_id},
                {"$set": {"synced_to_devops": True, "devops_commit_id": commit_id}}
            )
            
            return {
                "success": True,
                "commit_id": commit_id,
                "message": f"Successfully synced {policy_type} policies to Azure DevOps"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to sync to DevOps: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    
    return {
        "total_exports": total_exports,
        "synced_exports": synced_exports,
        "pending_sync": total_exports - synced_exports,
        "policy_type_counts": policy_type_counts,
        "recent_exports": recent_exports,
        "azure_configured": azure_configured,
        "devops_configured": devops_configured
    }

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
