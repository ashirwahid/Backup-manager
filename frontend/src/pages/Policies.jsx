import { useState } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import { 
  FileText, 
  ShieldCheck, 
  Smartphone, 
  RefreshCw,
  Download,
  Eye,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Copy,
  X
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

// JSON Syntax Highlighter Component
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

const PolicyCard = ({ title, description, icon: Icon, endpoint, onExport, loading }) => {
  return (
    <div className="card-base hover:border-zinc-300 transition-colors">
      <div className="p-5">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-sm bg-zinc-100 flex items-center justify-center">
              <Icon className="w-5 h-5 text-zinc-600" strokeWidth={1.5} />
            </div>
            <div>
              <h3 className="font-heading text-sm font-semibold text-zinc-900">
                {title}
              </h3>
              <p className="text-xs text-zinc-500 mt-0.5">{description}</p>
            </div>
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <Button
            data-testid={`export-${endpoint}-btn`}
            onClick={() => onExport(endpoint)}
            disabled={loading === endpoint}
            className="flex-1 bg-[#0052CC] hover:bg-[#0043A6] gap-2"
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
};

const Policies = () => {
  const [loading, setLoading] = useState(null);
  const [exportResult, setExportResult] = useState(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const policyTypes = [
    {
      title: "Device Configuration",
      description: "Export device configuration policies from Intune",
      icon: Smartphone,
      endpoint: "device-configuration"
    },
    {
      title: "Configuration Policies",
      description: "Export Settings Catalog configuration policies",
      icon: FileText,
      endpoint: "configuration"
    },
    {
      title: "Conditional Access",
      description: "Export Azure AD conditional access policies",
      icon: ShieldCheck,
      endpoint: "conditional-access"
    },
    {
      title: "Compliance Policies",
      description: "Export device compliance policies",
      icon: CheckCircle2,
      endpoint: "compliance"
    }
  ];

  const handleExport = async (endpoint) => {
    try {
      setLoading(endpoint);
      const response = await axios.get(`${API}/policies/${endpoint}`);
      setExportResult(response.data);
      setSheetOpen(true);
      toast.success(`Successfully exported ${response.data.policy_count} policies`);
    } catch (err) {
      const errorMsg = err.response?.data?.detail || "Failed to export policies";
      toast.error(errorMsg);
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
        policies: response.data.breakdown
      });
      setSheetOpen(true);
      toast.success(`Successfully exported ${response.data.total_count} policies`);
    } catch (err) {
      const errorMsg = err.response?.data?.detail || "Failed to export all policies";
      toast.error(errorMsg);
    } finally {
      setLoading(null);
    }
  };

  const copyToClipboard = (data) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success("Copied to clipboard");
  };

  return (
    <div data-testid="policies-page" className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
            Policies
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Export policies from Microsoft Intune via Graph API
          </p>
        </div>
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

      {/* Policy Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {policyTypes.map((policy) => (
          <PolicyCard
            key={policy.endpoint}
            {...policy}
            onExport={handleExport}
            loading={loading}
          />
        ))}
      </div>

      {/* Info Card */}
      <div className="card-base bg-zinc-50/50">
        <div className="p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-zinc-500 mt-0.5" />
            <div>
              <h3 className="text-sm font-medium text-zinc-700">
                About Policy Export
              </h3>
              <p className="text-xs text-zinc-500 mt-1 leading-relaxed">
                This tool uses the Microsoft Graph API to export policies from your Microsoft 365 tenant. 
                Ensure you have configured the Azure AD credentials in Settings with the required permissions:
              </p>
              <ul className="text-xs text-zinc-500 mt-2 space-y-1 list-disc list-inside">
                <li><code className="font-mono bg-zinc-200 px-1 rounded">DeviceManagementConfiguration.Read.All</code></li>
                <li><code className="font-mono bg-zinc-200 px-1 rounded">Policy.Read.All</code></li>
                <li><code className="font-mono bg-zinc-200 px-1 rounded">DeviceManagementManagedDevices.Read.All</code></li>
              </ul>
            </div>
          </div>
        </div>
      </div>

      {/* Export Result Sheet */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader className="border-b border-zinc-200 pb-4">
            <div className="flex items-center justify-between">
              <SheetTitle className="font-heading">
                Export Result
              </SheetTitle>
              <div className="flex items-center gap-2">
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
            </div>
          </SheetHeader>
          
          {exportResult && (
            <div className="py-4 space-y-4">
              {/* Export Summary */}
              <div className="grid grid-cols-3 gap-4">
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Export ID</p>
                  <p className="font-mono text-xs mt-1 text-zinc-700 truncate">
                    {exportResult.export_id}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Policy Type</p>
                  <p className="text-sm mt-1 text-zinc-700 capitalize">
                    {exportResult.policy_type?.replace(/_/g, " ")}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Count</p>
                  <p className="text-lg font-semibold mt-1 text-zinc-900 font-heading">
                    {exportResult.policy_count || exportResult.total_count}
                  </p>
                </div>
              </div>

              {/* JSON Preview */}
              <div>
                <p className="label-text mb-2">Exported Policies</p>
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
