import { useState, useEffect } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import { 
  Settings as SettingsIcon, 
  Cloud,
  GitBranch,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Save,
  TestTube,
  Github
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";

const SettingsPage = () => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingAzure, setTestingAzure] = useState(false);
  const [testingDevOps, setTestingDevOps] = useState(false);
  const [testingGithub, setTestingGithub] = useState(false);
  const [showSecrets, setShowSecrets] = useState({});
  
  const [settings, setSettings] = useState({
    azure_tenant_id: "",
    azure_client_id: "",
    azure_client_secret: "",
    devops_org: "",
    devops_project: "",
    devops_repo: "",
    devops_pat: "",
    devops_branch: "main",
    devops_remove_policy_pipeline_id: "",
    devops_require_pipeline_for_policy_removal: false,
    github_repo_url: "",
    github_branch: "main",
    github_baseline_path: "Source/Resources/Content",
    github_variables_path: "Source/Resources/variables.json",
    github_pat: ""
  });

  const [configStatus, setConfigStatus] = useState({
    azure_configured: false,
    devops_configured: false,
    github_configured: false
  });

  const [lastFetchedRemovePipelineId, setLastFetchedRemovePipelineId] = useState(null);

  const fetchSettings = async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/settings`);
      setConfigStatus({
        azure_configured: response.data.azure_configured,
        devops_configured: response.data.devops_configured,
        github_configured: response.data.github_configured
      });
      
      // Only set non-sensitive data
      setSettings(prev => ({
        ...prev,
        devops_org: response.data.devops_org || "",
        devops_project: response.data.devops_project || "",
        devops_repo: response.data.devops_repo || "",
        devops_branch: response.data.devops_branch || "main",
        github_repo_url: response.data.github_repo_url || "",
        github_branch: response.data.github_branch || "main",
        github_baseline_path:
          response.data.github_baseline_path || "Source/Resources/Content",
        github_variables_path:
          response.data.github_variables_path || "Source/Resources/variables.json",
        devops_remove_policy_pipeline_id:
          response.data.devops_remove_policy_pipeline_id != null
            ? String(response.data.devops_remove_policy_pipeline_id)
            : "",
        devops_require_pipeline_for_policy_removal: !!response.data.devops_require_pipeline_for_policy_removal,
      }));
      setLastFetchedRemovePipelineId(
        response.data.devops_remove_policy_pipeline_id != null
          ? response.data.devops_remove_policy_pipeline_id
          : null
      );
    } catch (err) {
      toast.error("Failed to fetch settings");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleChange = (field, value) => {
    setSettings(prev => ({ ...prev, [field]: value }));
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      
      const dataToSend = {};
      Object.keys(settings).forEach((key) => {
        if (key === "devops_remove_policy_pipeline_id") return;
        if (key === "devops_require_pipeline_for_policy_removal") return;
        if (settings[key] && String(settings[key]).trim()) {
          dataToSend[key] = String(settings[key]).trim();
        }
      });

      const rawPid = String(settings.devops_remove_policy_pipeline_id ?? "").trim();
      let normalizedPipelineId = null;
      if (rawPid === "") {
        normalizedPipelineId = null;
      } else {
        const n = parseInt(rawPid, 10);
        if (Number.isNaN(n) || n < 0) {
          toast.error("Remove pipeline ID must be a non-negative integer or empty");
          setSaving(false);
          return;
        }
        normalizedPipelineId = n === 0 ? null : n;
      }
      if (normalizedPipelineId !== lastFetchedRemovePipelineId) {
        dataToSend.devops_remove_policy_pipeline_id = normalizedPipelineId;
      }

      dataToSend.devops_require_pipeline_for_policy_removal =
        !!settings.devops_require_pipeline_for_policy_removal;

      await axios.post(`${API}/settings`, dataToSend);
      toast.success("Settings saved successfully");
      fetchSettings();
    } catch (err) {
      toast.error("Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const testAzureConnection = async () => {
    try {
      setTestingAzure(true);
      const response = await axios.post(`${API}/settings/test-azure`);
      
      if (response.data.success) {
        toast.success("Azure AD connection successful!");
      } else {
        toast.error(response.data.message);
      }
    } catch (err) {
      toast.error("Failed to test Azure connection");
    } finally {
      setTestingAzure(false);
    }
  };

  const testDevOpsConnection = async () => {
    try {
      setTestingDevOps(true);
      const response = await axios.post(`${API}/settings/test-devops`);
      
      if (response.data.success) {
        toast.success("Azure DevOps connection successful!");
      } else {
        toast.error(response.data.message);
      }
    } catch (err) {
      toast.error("Failed to test DevOps connection");
    } finally {
      setTestingDevOps(false);
    }
  };

  const testGithubConnection = async () => {
    try {
      setTestingGithub(true);
      const response = await axios.post(`${API}/settings/test-github`);
      
      if (response.data.success) {
        toast.success(response.data.message);
      } else {
        toast.error(response.data.message);
      }
    } catch (err) {
      toast.error("Failed to test GitHub connection");
    } finally {
      setTestingGithub(false);
    }
  };

  const toggleShowSecret = (field) => {
    setShowSecrets(prev => ({ ...prev, [field]: !prev[field] }));
  };

  if (loading) {
    return (
      <div data-testid="settings-loading" className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div data-testid="settings-page" className="space-y-6 max-w-3xl">
      {/* Header */}
      <div>
        <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
          Settings
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          Configure Azure AD and Azure DevOps credentials
        </p>
      </div>

      {/* Azure AD Settings */}
      <div className="card-base">
        <div className="card-header">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-sm bg-[#0078D4]/10 flex items-center justify-center">
              <Cloud className="w-4 h-4 text-[#0078D4]" strokeWidth={1.5} />
            </div>
            <div>
              <h2 className="font-heading text-sm font-semibold text-zinc-900">
                Azure AD Configuration
              </h2>
              <p className="text-xs text-zinc-500">
                Microsoft Graph API authentication
              </p>
            </div>
          </div>
          {configStatus.azure_configured ? (
            <span className="badge-success inline-flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              Configured
            </span>
          ) : (
            <span className="badge-warning inline-flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              Not Configured
            </span>
          )}
        </div>
        
        <div className="card-body space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="azure_tenant_id" className="label-text">
                Tenant ID
              </Label>
              <Input
                id="azure_tenant_id"
                data-testid="azure-tenant-id-input"
                value={settings.azure_tenant_id}
                onChange={(e) => handleChange("azure_tenant_id", e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                className="font-mono text-sm"
              />
            </div>
            
            <div className="space-y-2">
              <Label htmlFor="azure_client_id" className="label-text">
                Client ID (Application ID)
              </Label>
              <Input
                id="azure_client_id"
                data-testid="azure-client-id-input"
                value={settings.azure_client_id}
                onChange={(e) => handleChange("azure_client_id", e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                className="font-mono text-sm"
              />
            </div>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="azure_client_secret" className="label-text">
              Client Secret
            </Label>
            <div className="relative">
              <Input
                id="azure_client_secret"
                data-testid="azure-client-secret-input"
                type={showSecrets.azure_client_secret ? "text" : "password"}
                value={settings.azure_client_secret}
                onChange={(e) => handleChange("azure_client_secret", e.target.value)}
                placeholder="Enter client secret"
                className="pr-10 font-mono text-sm"
              />
              <button
                type="button"
                onClick={() => toggleShowSecret("azure_client_secret")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
              >
                {showSecrets.azure_client_secret ? (
                  <EyeOff className="w-4 h-4" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
              </button>
            </div>
          </div>

          <div className="flex justify-between items-center pt-2">
            <Button
              data-testid="test-azure-btn"
              variant="outline"
              onClick={testAzureConnection}
              disabled={testingAzure || !configStatus.azure_configured}
              className="gap-2"
            >
              {testingAzure ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <TestTube className="w-4 h-4" />
              )}
              Test Connection
            </Button>
          </div>

          {/* Setup Instructions */}
          <div className="mt-4 p-4 bg-zinc-50 rounded-sm">
            <h3 className="text-xs font-semibold text-zinc-700 uppercase tracking-wider mb-2">
              Setup Instructions
            </h3>
            <ol className="text-xs text-zinc-600 space-y-1 list-decimal list-inside">
              <li>Go to Azure Portal &rarr; Azure Active Directory &rarr; App registrations</li>
              <li>Create a new registration or select existing one</li>
              <li>Copy the Application (client) ID and Directory (tenant) ID</li>
              <li>Go to Certificates & secrets &rarr; Create new client secret</li>
              <li>Add API permissions: <code className="bg-zinc-200 px-1 rounded">DeviceManagementConfiguration.Read.All</code>, <code className="bg-zinc-200 px-1 rounded">Policy.Read.All</code></li>
              <li>Grant admin consent for the permissions</li>
            </ol>
          </div>
        </div>
      </div>

      {/* Azure DevOps Settings */}
      <div className="card-base">
        <div className="card-header">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-sm bg-[#0078D4]/10 flex items-center justify-center">
              <GitBranch className="w-4 h-4 text-[#0078D4]" strokeWidth={1.5} />
            </div>
            <div>
              <h2 className="font-heading text-sm font-semibold text-zinc-900">
                Azure DevOps Configuration
              </h2>
              <p className="text-xs text-zinc-500">
                Repository for storing exported policies
              </p>
            </div>
          </div>
          {configStatus.devops_configured ? (
            <span className="badge-success inline-flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              Configured
            </span>
          ) : (
            <span className="badge-warning inline-flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              Not Configured
            </span>
          )}
        </div>
        
        <div className="card-body space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="devops_org" className="label-text">
                Organization
              </Label>
              <Input
                id="devops_org"
                data-testid="devops-org-input"
                value={settings.devops_org}
                onChange={(e) => handleChange("devops_org", e.target.value)}
                placeholder="your-organization"
              />
            </div>
            
            <div className="space-y-2">
              <Label htmlFor="devops_project" className="label-text">
                Project
              </Label>
              <Input
                id="devops_project"
                data-testid="devops-project-input"
                value={settings.devops_project}
                onChange={(e) => handleChange("devops_project", e.target.value)}
                placeholder="your-project"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="devops_repo" className="label-text">
                Repository Name
              </Label>
              <Input
                id="devops_repo"
                data-testid="devops-repo-input"
                value={settings.devops_repo}
                onChange={(e) => handleChange("devops_repo", e.target.value)}
                placeholder="policy-repository"
              />
            </div>
            
            <div className="space-y-2">
              <Label htmlFor="devops_branch" className="label-text">
                Branch
              </Label>
              <Input
                id="devops_branch"
                data-testid="devops-branch-input"
                value={settings.devops_branch}
                onChange={(e) => handleChange("devops_branch", e.target.value)}
                placeholder="main"
              />
            </div>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="devops_remove_policy_pipeline_id" className="label-text">
              Policy remove pipeline (definition ID)
            </Label>
            <Input
              id="devops_remove_policy_pipeline_id"
              data-testid="devops-remove-pipeline-id-input"
              type="number"
              min={0}
              value={settings.devops_remove_policy_pipeline_id}
              onChange={(e) => handleChange("devops_remove_policy_pipeline_id", e.target.value)}
              placeholder="e.g. 42 — optional"
            />
            <p className="text-[11px] text-zinc-500 leading-snug">
              When set, Remove in CIS comparison queues this YAML pipeline instead of deleting via the app.
              The pipeline receives variable{" "}
              <code className="font-mono bg-zinc-100 px-1 rounded">BackupManagerPolicyRemovePayload</code>{" "}
              (JSON with <code className="font-mono bg-zinc-100 px-1">policy_type</code> and{" "}
              <code className="font-mono bg-zinc-100 px-1">policy_ids</code>). Configure an Azure DevOps
              environment with approvals on the job that performs the Graph delete so removal waits for
              sign-off. PAT needs permission to queue builds. Clear the field and save to disable routing.
            </p>
          </div>

          <div className="flex items-center justify-between gap-4 rounded-md border border-zinc-200 bg-zinc-50/50 px-3 py-3">
            <div className="space-y-0.5 min-w-0">
              <Label htmlFor="devops_require_pipeline_switch" className="label-text">
                Require pipeline for policy removal
              </Label>
              <p className="text-[11px] text-zinc-500 leading-snug">
                When on, this app never calls Microsoft Graph to delete policies from CIS Remove—only queues
                the remove pipeline. Turn on after you set the definition ID above and attach an{" "}
                <strong>Environment with Approvals</strong> to the delete job in YAML.
              </p>
            </div>
            <Switch
              id="devops_require_pipeline_switch"
              checked={!!settings.devops_require_pipeline_for_policy_removal}
              onCheckedChange={(v) => handleChange("devops_require_pipeline_for_policy_removal", v)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="devops_pat" className="label-text">
              Personal Access Token (PAT)
            </Label>
            <div className="relative">
              <Input
                id="devops_pat"
                data-testid="devops-pat-input"
                type={showSecrets.devops_pat ? "text" : "password"}
                value={settings.devops_pat}
                onChange={(e) => handleChange("devops_pat", e.target.value)}
                placeholder="Enter PAT"
                className="pr-10 font-mono text-sm"
              />
              <button
                type="button"
                onClick={() => toggleShowSecret("devops_pat")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
              >
                {showSecrets.devops_pat ? (
                  <EyeOff className="w-4 h-4" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
              </button>
            </div>
          </div>

          <div className="flex justify-between items-center pt-2">
            <Button
              data-testid="test-devops-btn"
              variant="outline"
              onClick={testDevOpsConnection}
              disabled={testingDevOps || !configStatus.devops_configured}
              className="gap-2"
            >
              {testingDevOps ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <TestTube className="w-4 h-4" />
              )}
              Test Connection
            </Button>
          </div>

          {/* Setup Instructions */}
          <div className="mt-4 p-4 bg-zinc-50 rounded-sm">
            <h3 className="text-xs font-semibold text-zinc-700 uppercase tracking-wider mb-2">
              Setup Instructions
            </h3>
            <ol className="text-xs text-zinc-600 space-y-1 list-decimal list-inside">
              <li>Go to Azure DevOps &rarr; User Settings &rarr; Personal Access Tokens</li>
              <li>Create new token with &quot;Code (Read & Write)&quot; and &quot;Build (Queue builds)&quot; if you use the remove pipeline</li>
              <li>Set expiration as needed (recommended: 90-180 days)</li>
              <li>Copy the token immediately (shown only once)</li>
              <li>Create or select a repository to store policies</li>
            </ol>
          </div>
        </div>
      </div>

      {/* GitHub CIS Baseline Settings */}
      <div className="card-base">
        <div className="card-header">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-sm bg-zinc-900 flex items-center justify-center">
              <Github className="w-4 h-4 text-white" strokeWidth={1.5} />
            </div>
            <div>
              <h2 className="font-heading text-sm font-semibold text-zinc-900">
                GitHub CIS Baseline
              </h2>
              <p className="text-xs text-zinc-500">
                Repository containing CIS baseline policies for comparison
              </p>
            </div>
          </div>
          {configStatus.github_configured ? (
            <span className="badge-success inline-flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              Configured
            </span>
          ) : (
            <span className="badge-warning inline-flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              Not Configured
            </span>
          )}
        </div>
        
        <div className="card-body space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="github_repo_url" className="label-text">
                Repository (owner/repo)
              </Label>
              <Input
                id="github_repo_url"
                data-testid="github-repo-url-input"
                value={settings.github_repo_url}
                onChange={(e) => handleChange("github_repo_url", e.target.value)}
                placeholder="microsoft/cis-baseline"
              />
            </div>
            
            <div className="space-y-2">
              <Label htmlFor="github_branch" className="label-text">
                Branch
              </Label>
              <Input
                id="github_branch"
                data-testid="github-branch-input"
                value={settings.github_branch}
                onChange={(e) => handleChange("github_branch", e.target.value)}
                placeholder="main"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="github_baseline_path" className="label-text">
                Baseline Path (folder containing JSONs)
              </Label>
              <Input
                id="github_baseline_path"
                data-testid="github-baseline-path-input"
                value={settings.github_baseline_path}
                onChange={(e) => handleChange("github_baseline_path", e.target.value)}
                placeholder="Source/Resources/Content"
              />
            </div>
            
            <div className="space-y-2">
              <Label htmlFor="github_variables_path" className="label-text">
                Variables Path (Simeon variables.json)
              </Label>
              <Input
                id="github_variables_path"
                data-testid="github-variables-path-input"
                value={settings.github_variables_path}
                onChange={(e) => handleChange("github_variables_path", e.target.value)}
                placeholder="Source/Resources/variables.json"
              />
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            Deploy resolves admin@tenant placeholders using ResourceContext:TenantDomainName from variables.json.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="github_pat" className="label-text">
                Personal Access Token (optional for public repos)
              </Label>
              <div className="relative">
                <Input
                  id="github_pat"
                  data-testid="github-pat-input"
                  type={showSecrets.github_pat ? "text" : "password"}
                  value={settings.github_pat}
                  onChange={(e) => handleChange("github_pat", e.target.value)}
                  placeholder="ghp_..."
                  className="pr-10 font-mono text-sm"
                />
                <button
                  type="button"
                  onClick={() => toggleShowSecret("github_pat")}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
                >
                  {showSecrets.github_pat ? (
                    <EyeOff className="w-4 h-4" />
                  ) : (
                    <Eye className="w-4 h-4" />
                  )}
                </button>
              </div>
            </div>
          </div>

          <div className="flex justify-between items-center pt-2">
            <Button
              data-testid="test-github-btn"
              variant="outline"
              onClick={testGithubConnection}
              disabled={testingGithub || !configStatus.github_configured}
              className="gap-2"
            >
              {testingGithub ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <TestTube className="w-4 h-4" />
              )}
              Test Connection
            </Button>
          </div>

          {/* Setup Instructions */}
          <div className="mt-4 p-4 bg-zinc-50 rounded-sm">
            <h3 className="text-xs font-semibold text-zinc-700 uppercase tracking-wider mb-2">
              Setup Instructions
            </h3>
            <ol className="text-xs text-zinc-600 space-y-1 list-decimal list-inside">
              <li>Create or identify a GitHub repository with your CIS baseline JSONs</li>
              <li>Enter the repository as "owner/repo" (e.g., "microsoft/cis-baseline")</li>
              <li>Specify the branch and path where baseline JSONs are stored</li>
              <li>For private repos, create a PAT with "repo" scope at GitHub Settings &rarr; Developer settings &rarr; Personal access tokens</li>
            </ol>
          </div>
        </div>
      </div>

      {/* Save Button */}
      <div className="flex justify-end">
        <Button
          data-testid="save-settings-btn"
          onClick={handleSave}
          disabled={saving}
          className="gap-2 bg-[#0052CC] hover:bg-[#0043A6]"
        >
          {saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          Save Settings
        </Button>
      </div>
    </div>
  );
};

export default SettingsPage;
