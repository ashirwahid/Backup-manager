import { useState } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import {
  FileText,
  ShieldCheck,
  Smartphone,
  Download,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Copy,
  Cloud,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";

const JsonViewer = ({ data }) => {
  const syntaxHighlight = (json) => {
    if (typeof json !== "string") {
      json = JSON.stringify(json, null, 2);
    }

    return json.replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
      (match) => {
        let cls = "json-number";
        if (/^"/.test(match)) {
          if (/:$/.test(match)) {
            cls = "json-key";
          } else {
            cls = "json-string";
          }
        } else if (/true|false/.test(match)) {
          cls = "json-boolean";
        } else if (/null/.test(match)) {
          cls = "json-null";
        }
        return `<span class="${cls}">${match}</span>`;
      }
    );
  };

  return (
    <pre
      className="json-viewer whitespace-pre-wrap break-all"
      dangerouslySetInnerHTML={{ __html: syntaxHighlight(data) }}
    />
  );
};

const PolicyCard = ({ title, description, icon: Icon, endpoint, onExport, loading }) => (
  <div className="card-base hover:border-zinc-300 transition-colors">
    <div className="p-5">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-sm bg-zinc-100 flex items-center justify-center">
          <Icon className="w-5 h-5 text-zinc-600" strokeWidth={1.5} />
        </div>
        <div>
          <h3 className="font-heading text-sm font-semibold text-zinc-900">{title}</h3>
          <p className="text-xs text-zinc-500 mt-0.5">{description}</p>
        </div>
      </div>
      <div className="mt-4">
        <Button
          data-testid={`export-${endpoint}-btn`}
          onClick={() => onExport(endpoint)}
          disabled={loading === endpoint}
          className="w-full bg-[#0052CC] hover:bg-[#0043A6] gap-2"
          size="sm"
        >
          {loading === endpoint ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Download className="w-4 h-4" />
          )}
          Export
        </Button>
      </div>
    </div>
  </div>
);

const Policies = () => {
  const [loading, setLoading] = useState(null);
  const [exportResult, setExportResult] = useState(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const intunePolicyTypes = [
    {
      title: "Intune · Devices · Configuration Profiles",
      description: "Device configuration policies",
      policyType: "device_configuration",
      devopsScope:
        "Source/Resources/Content/MSGraph/DeviceManagement/DeviceConfigurations",
      icon: Smartphone,
      endpoint: "device-configuration",
    },
    {
      title: "Intune · Apps",
      description: "Settings catalog (configuration policies)",
      policyType: "configuration",
      devopsScope:
        "Source/Resources/Content/MSGraph/DeviceAppManagement/MobileApps",
      icon: FileText,
      endpoint: "configuration",
    },
    {
      title: "Entra ID · Conditional Access",
      description: "Conditional access policies",
      policyType: "conditional_access",
      devopsScope:
        "Source/Resources/Content/MSGraph/Identity/ConditionalAccess/Policies",
      icon: ShieldCheck,
      endpoint: "conditional-access",
    },
    {
      title: "Intune · Devices · Compliance",
      description: "Device compliance policies",
      policyType: "compliance",
      devopsScope:
        "Source/Resources/Content/MSGraph/DeviceManagement/DeviceCompliancePolicies",
      icon: CheckCircle2,
      endpoint: "compliance",
    },
  ];

  const handleExport = async (endpoint) => {
    try {
      setLoading(endpoint);
      const response = await axios.get(`${API}/policies/${endpoint}`);
      setExportResult(response.data);
      setSheetOpen(true);
      const count = response.data.policy_count ?? 0;
      const label = endpoint === "onedrive" ? "OneDrive drives" : "policies";
      if (endpoint === "onedrive" && count === 0) {
        const stats = response.data.stats || {};
        toast.warning(
          response.data.message ||
            `No OneDrive drives exported. Scanned ${stats.users_scanned ?? 0} users; ` +
              `listed ${stats.drives_from_list_api ?? 0} drives from Graph.`
        );
      } else {
        toast.success(`Successfully exported ${count} ${label}`);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to export");
    } finally {
      setLoading(null);
    }
  };

  const handleExportAll = async () => {
    try {
      setLoading("all");
      const response = await axios.post(`${API}/policies/export-all`);
      setExportResult({
        ...response.data,
        policies: response.data.breakdown,
      });
      setSheetOpen(true);
      toast.success(`Successfully exported ${response.data.total_count} policies`);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to export all policies");
    } finally {
      setLoading(null);
    }
  };

  const copyToClipboard = (data) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success("Copied to clipboard");
  };

  const devopsPathHint =
    exportResult?.policy_type === "onedrive"
      ? "Source/Resources/Content/MSGraph/Users/OneDriveSnapshots/"
      : intunePolicyTypes.find((t) => t.policyType === exportResult?.policy_type)
          ?.devopsScope || "Source/Resources/Content/MSGraph/...";

  return (
    <div data-testid="policies-page" className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
          Policies
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          Export Intune policies and OneDrive metadata to Azure DevOps
        </p>
      </div>

      <Tabs defaultValue="intune" className="space-y-4">
        <TabsList>
          <TabsTrigger value="intune" data-testid="policies-tab-intune">
            Intune policies
          </TabsTrigger>
          <TabsTrigger value="onedrive" data-testid="policies-tab-onedrive">
            OneDrive
          </TabsTrigger>
        </TabsList>

        <TabsContent value="intune" className="space-y-4">
          <div className="flex justify-end">
            <Button
              data-testid="export-all-policies-btn"
              onClick={handleExportAll}
              disabled={loading === "all"}
              className="gap-2 bg-[#0052CC] hover:bg-[#0043A6]"
            >
              {loading === "all" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Download className="w-4 h-4" />
              )}
              Export All Policies
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {intunePolicyTypes.map((policy) => (
              <PolicyCard
                key={policy.endpoint}
                {...policy}
                onExport={handleExport}
                loading={loading}
              />
            ))}
          </div>

          <div className="card-base bg-zinc-50/50 p-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-zinc-500 mt-0.5 shrink-0" />
              <div>
                <h3 className="text-sm font-medium text-zinc-700">Intune export permissions</h3>
                <p className="text-xs text-zinc-500 mt-1 leading-relaxed">
                  Requires Microsoft Graph application permissions such as{" "}
                  <code className="font-mono bg-zinc-200 px-1 rounded">
                    DeviceManagementConfiguration.Read.All
                  </code>{" "}
                  and{" "}
                  <code className="font-mono bg-zinc-200 px-1 rounded">Policy.Read.All</code>.
                </p>
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="onedrive" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <PolicyCard
              title="OneDrive (all users)"
              description="Export each member user with a provisioned OneDrive to DevOps"
              icon={Cloud}
              endpoint="onedrive"
              onExport={handleExport}
              loading={loading}
            />
          </div>

          <div className="card-base bg-zinc-50/50 p-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-zinc-500 mt-0.5 shrink-0" />
              <div>
                <h3 className="text-sm font-medium text-zinc-700">OneDrive export</h3>
                <p className="text-xs text-zinc-500 mt-1 leading-relaxed">
                  Scans member users and backs up metadata for users with a provisioned drive.
                  Files are stored in Azure DevOps at{" "}
                  <code className="font-mono bg-zinc-200 px-1 rounded">
                    Source/Resources/Content/MSGraph/Users/OneDriveSnapshots/
                  </code>{" "}
                  as <code className="font-mono bg-zinc-200 px-1 rounded">{"{userId}__{upn}.json"}</code>.
                </p>
                <ul className="text-xs text-zinc-500 mt-2 space-y-1 list-disc list-inside">
                  <li>
                    <code className="font-mono bg-zinc-200 px-1 rounded">User.Read.All</code>
                  </li>
                  <li>
                    <code className="font-mono bg-zinc-200 px-1 rounded">Files.Read.All</code>
                  </li>
                  <li>
                    <code className="font-mono bg-zinc-200 px-1 rounded">Sites.Read.All</code>{" "}
                    (recommended)
                  </li>
                </ul>
                <p className="text-xs text-zinc-500 mt-2">
                  A 404 on <code className="font-mono bg-zinc-200 px-1 rounded">/users/…/drive</code>{" "}
                  usually means OneDrive is not provisioned for app access yet. The export also
                  tries <code className="font-mono bg-zinc-200 px-1 rounded">GET /drives</code> to
                  find existing libraries.
                </p>
              </div>
            </div>
          </div>
        </TabsContent>
      </Tabs>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader className="border-b border-zinc-200 pb-4">
            <div className="flex items-center justify-between">
              <SheetTitle className="font-heading">Export Result</SheetTitle>
              <Button
                variant="outline"
                size="sm"
                onClick={() => copyToClipboard(exportResult?.policies)}
                className="gap-2"
              >
                <Copy className="w-4 h-4" />
                Copy JSON
              </Button>
            </div>
          </SheetHeader>

          {exportResult && (
            <div className="py-4 space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Export ID</p>
                  <p className="font-mono text-xs mt-1 text-zinc-700 truncate">
                    {exportResult.export_id}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Type</p>
                  <p className="text-sm mt-1 text-zinc-700 capitalize">
                    {exportResult.policy_type?.replace(/_/g, " ")}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Count</p>
                  <p className="text-lg font-semibold mt-1 text-zinc-900 font-heading">
                    {exportResult.policy_count ?? exportResult.total_count}
                  </p>
                </div>
              </div>

              {exportResult.devops?.committed && (
                <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-sm px-3 py-2">
                  Pushed to Azure DevOps under {devopsPathHint}
                </p>
              )}

              <div>
                <p className="label-text mb-2">Exported data</p>
                <ScrollArea className="h-[calc(100vh-320px)]">
                  <JsonViewer data={exportResult.policies || exportResult.breakdown} />
                </ScrollArea>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default Policies;
