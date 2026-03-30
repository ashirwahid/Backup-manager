import { useState, useEffect } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import { 
  GitCompare, 
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Plus,
  Loader2,
  Eye,
  ChevronDown,
  ChevronRight,
  Copy,
  FileText,
  Shield
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { useNavigate } from "react-router-dom";

// JSON Syntax Highlighter
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
      className="json-viewer whitespace-pre-wrap break-all text-xs"
      dangerouslySetInnerHTML={{ __html: syntaxHighlight(data) }}
    />
  );
};

// Policy Card Component for each category
const PolicyCard = ({ item, category, onView }) => {
  const [isOpen, setIsOpen] = useState(false);
  
  const getCategoryStyles = () => {
    switch(category) {
      case "tenant_only":
        return "border-l-4 border-l-blue-500 bg-blue-50/30";
      case "baseline_only":
        return "border-l-4 border-l-amber-500 bg-amber-50/30";
      case "conflicting":
        return "border-l-4 border-l-red-500 bg-red-50/30";
      case "matching":
        return "border-l-4 border-l-emerald-500 bg-emerald-50/30";
      default:
        return "";
    }
  };

  return (
    <div className={`card-base mb-2 ${getCategoryStyles()}`}>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger className="w-full">
          <div className="flex items-center justify-between p-3 hover:bg-zinc-50/50 transition-colors">
            <div className="flex items-center gap-2">
              {isOpen ? (
                <ChevronDown className="w-4 h-4 text-zinc-400" />
              ) : (
                <ChevronRight className="w-4 h-4 text-zinc-400" />
              )}
              <span className="text-sm font-medium text-zinc-900 truncate max-w-[300px]">
                {item.name}
              </span>
            </div>
            {category === "conflicting" && item.differences && (
              <Badge variant="outline" className="text-red-600 border-red-200">
                {item.differences.length} difference{item.differences.length > 1 ? 's' : ''}
              </Badge>
            )}
          </div>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-3 pb-3 border-t border-zinc-100">
            {category === "conflicting" && item.differences && (
              <div className="mt-3 space-y-2">
                <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Differences</p>
                <div className="space-y-2">
                  {item.differences.map((diff, idx) => (
                    <div key={idx} className="bg-zinc-50 rounded-sm p-2 text-xs">
                      <p className="font-medium text-zinc-700 mb-1">{diff.field}</p>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <p className="text-[10px] text-zinc-400 uppercase">Tenant</p>
                          <code className="font-mono text-blue-600 break-all">
                            {JSON.stringify(diff.tenant_value)}
                          </code>
                        </div>
                        <div>
                          <p className="text-[10px] text-zinc-400 uppercase">Baseline</p>
                          <code className="font-mono text-amber-600 break-all">
                            {JSON.stringify(diff.baseline_value)}
                          </code>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-3 flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onView(item, category)}
                className="gap-1 text-xs"
              >
                <Eye className="w-3 h-3" />
                View Full JSON
              </Button>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
};

// Category Column Component
const CategoryColumn = ({ title, icon: Icon, items, category, color, onViewPolicy }) => {
  return (
    <div className="flex flex-col h-full">
      <div className={`flex items-center gap-2 p-3 border-b ${color.border} ${color.bg}`}>
        <Icon className={`w-4 h-4 ${color.icon}`} strokeWidth={1.5} />
        <h3 className={`text-sm font-semibold ${color.text}`}>{title}</h3>
        <Badge variant="secondary" className="ml-auto">
          {items.length}
        </Badge>
      </div>
      <ScrollArea className="flex-1 p-2">
        {items.length > 0 ? (
          items.map((item, idx) => (
            <PolicyCard 
              key={idx} 
              item={item} 
              category={category}
              onView={onViewPolicy}
            />
          ))
        ) : (
          <div className="flex flex-col items-center justify-center h-32 text-zinc-400">
            <Icon className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-xs">No policies</p>
          </div>
        )}
      </ScrollArea>
    </div>
  );
};

const CISComparison = () => {
  const [loading, setLoading] = useState(false);
  const [comparison, setComparison] = useState(null);
  const [policyType, setPolicyType] = useState("all");
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [githubConfigured, setGithubConfigured] = useState(false);
  const [checkingConfig, setCheckingConfig] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    checkConfiguration();
  }, []);

  const checkConfiguration = async () => {
    try {
      setCheckingConfig(true);
      const response = await axios.get(`${API}/settings`);
      setGithubConfigured(response.data.github_configured);
    } catch (err) {
      console.error("Failed to check configuration", err);
    } finally {
      setCheckingConfig(false);
    }
  };

  const runComparison = async () => {
    try {
      setLoading(true);
      const response = await axios.post(`${API}/baseline/compare?policy_type=${policyType}`);
      setComparison(response.data);
      toast.success("Comparison completed successfully");
    } catch (err) {
      const errorMsg = err.response?.data?.detail || "Failed to run comparison";
      toast.error(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  const handleViewPolicy = (item, category) => {
    setSelectedPolicy({ ...item, category });
    setSheetOpen(true);
  };

  const copyToClipboard = (data) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success("Copied to clipboard");
  };

  if (checkingConfig) {
    return (
      <div data-testid="cis-loading" className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  if (!githubConfigured) {
    return (
      <div data-testid="cis-not-configured" className="space-y-6">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
            CIS Baseline Comparison
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Compare your tenant policies with CIS baseline from GitHub
          </p>
        </div>
        
        <div className="card-base p-8 text-center">
          <Shield className="w-16 h-16 mx-auto text-zinc-300 mb-4" />
          <h2 className="text-lg font-semibold text-zinc-700 mb-2">
            GitHub Repository Not Configured
          </h2>
          <p className="text-sm text-zinc-500 mb-4 max-w-md mx-auto">
            To compare your tenant policies with CIS baseline, you need to configure
            the GitHub repository where your baseline JSONs are stored.
          </p>
          <Button
            data-testid="configure-github-btn"
            onClick={() => navigate("/settings")}
            className="bg-[#0052CC] hover:bg-[#0043A6]"
          >
            Configure GitHub Repository
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="cis-comparison-page" className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
            CIS Baseline Comparison
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Compare your tenant policies with CIS baseline from GitHub
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select value={policyType} onValueChange={setPolicyType}>
            <SelectTrigger className="w-[200px]" data-testid="policy-type-select">
              <SelectValue placeholder="Select policy type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Policies</SelectItem>
              <SelectItem value="device_configuration">Device Configuration</SelectItem>
              <SelectItem value="configuration">Configuration (Settings Catalog)</SelectItem>
              <SelectItem value="conditional_access">Conditional Access</SelectItem>
              <SelectItem value="compliance">Compliance</SelectItem>
            </SelectContent>
          </Select>
          <Button
            data-testid="run-comparison-btn"
            onClick={runComparison}
            disabled={loading}
            className="gap-2 bg-[#0052CC] hover:bg-[#0043A6]"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <GitCompare className="w-4 h-4" />
            )}
            Run Comparison
          </Button>
        </div>
      </div>

      {/* Summary Stats */}
      {comparison && (
        <div className="grid grid-cols-4 gap-4">
          <div className="stat-card border-l-4 border-l-blue-500">
            <p className="label-text">Tenant Only</p>
            <p className="stat-value text-blue-600">{comparison.summary.tenant_only_count}</p>
          </div>
          <div className="stat-card border-l-4 border-l-amber-500">
            <p className="label-text">Baseline Only</p>
            <p className="stat-value text-amber-600">{comparison.summary.baseline_only_count}</p>
          </div>
          <div className="stat-card border-l-4 border-l-red-500">
            <p className="label-text">Conflicting</p>
            <p className="stat-value text-red-600">{comparison.summary.conflicting_count}</p>
          </div>
          <div className="stat-card border-l-4 border-l-emerald-500">
            <p className="label-text">Matching</p>
            <p className="stat-value text-emerald-600">{comparison.summary.matching_count}</p>
          </div>
        </div>
      )}

      {/* Comparison Grid */}
      {comparison ? (
        <div className="grid grid-cols-4 gap-4 h-[calc(100vh-340px)]">
          <div className="card-base overflow-hidden flex flex-col">
            <CategoryColumn
              title="Tenant Only"
              icon={Plus}
              items={comparison.tenant_only}
              category="tenant_only"
              color={{
                bg: "bg-blue-50",
                border: "border-blue-200",
                text: "text-blue-700",
                icon: "text-blue-600"
              }}
              onViewPolicy={handleViewPolicy}
            />
          </div>
          
          <div className="card-base overflow-hidden flex flex-col">
            <CategoryColumn
              title="Baseline Only"
              icon={FileText}
              items={comparison.baseline_only}
              category="baseline_only"
              color={{
                bg: "bg-amber-50",
                border: "border-amber-200",
                text: "text-amber-700",
                icon: "text-amber-600"
              }}
              onViewPolicy={handleViewPolicy}
            />
          </div>
          
          <div className="card-base overflow-hidden flex flex-col">
            <CategoryColumn
              title="Conflicting"
              icon={AlertTriangle}
              items={comparison.conflicting}
              category="conflicting"
              color={{
                bg: "bg-red-50",
                border: "border-red-200",
                text: "text-red-700",
                icon: "text-red-600"
              }}
              onViewPolicy={handleViewPolicy}
            />
          </div>
          
          <div className="card-base overflow-hidden flex flex-col">
            <CategoryColumn
              title="Matching"
              icon={CheckCircle2}
              items={comparison.matching}
              category="matching"
              color={{
                bg: "bg-emerald-50",
                border: "border-emerald-200",
                text: "text-emerald-700",
                icon: "text-emerald-600"
              }}
              onViewPolicy={handleViewPolicy}
            />
          </div>
        </div>
      ) : (
        <div className="card-base p-12 text-center">
          <GitCompare className="w-16 h-16 mx-auto text-zinc-300 mb-4" />
          <h2 className="text-lg font-semibold text-zinc-700 mb-2">
            Ready to Compare
          </h2>
          <p className="text-sm text-zinc-500 mb-4 max-w-md mx-auto">
            Select a policy type and click "Run Comparison" to compare your tenant 
            policies with the CIS baseline stored in your GitHub repository.
          </p>
        </div>
      )}

      {/* Policy Detail Sheet */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader className="border-b border-zinc-200 pb-4">
            <div className="flex items-center justify-between">
              <SheetTitle className="font-heading truncate max-w-[400px]">
                {selectedPolicy?.name}
              </SheetTitle>
              <Badge
                variant="outline"
                className={
                  selectedPolicy?.category === "matching" ? "text-emerald-600 border-emerald-200" :
                  selectedPolicy?.category === "conflicting" ? "text-red-600 border-red-200" :
                  selectedPolicy?.category === "tenant_only" ? "text-blue-600 border-blue-200" :
                  "text-amber-600 border-amber-200"
                }
              >
                {selectedPolicy?.category?.replace("_", " ")}
              </Badge>
            </div>
          </SheetHeader>
          
          {selectedPolicy && (
            <div className="py-4 space-y-4">
              {/* Show both tenant and baseline for matching/conflicting */}
              {(selectedPolicy.category === "matching" || selectedPolicy.category === "conflicting") && (
                <div className="space-y-4">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="label-text">Tenant Policy</p>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => copyToClipboard(selectedPolicy.tenant)}
                        className="h-6 text-xs gap-1"
                      >
                        <Copy className="w-3 h-3" />
                        Copy
                      </Button>
                    </div>
                    <ScrollArea className="h-[250px] border border-zinc-200 rounded-sm">
                      <JsonViewer data={selectedPolicy.tenant} />
                    </ScrollArea>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="label-text">Baseline Policy</p>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => copyToClipboard(selectedPolicy.baseline)}
                        className="h-6 text-xs gap-1"
                      >
                        <Copy className="w-3 h-3" />
                        Copy
                      </Button>
                    </div>
                    <ScrollArea className="h-[250px] border border-zinc-200 rounded-sm">
                      <JsonViewer data={selectedPolicy.baseline} />
                    </ScrollArea>
                  </div>
                </div>
              )}
              
              {/* Show single policy for tenant_only or baseline_only */}
              {(selectedPolicy.category === "tenant_only" || selectedPolicy.category === "baseline_only") && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="label-text">
                      {selectedPolicy.category === "tenant_only" ? "Tenant Policy" : "Baseline Policy"}
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => copyToClipboard(selectedPolicy.policy)}
                      className="h-6 text-xs gap-1"
                    >
                      <Copy className="w-3 h-3" />
                      Copy
                    </Button>
                  </div>
                  <ScrollArea className="h-[calc(100vh-280px)] border border-zinc-200 rounded-sm">
                    <JsonViewer data={selectedPolicy.policy} />
                  </ScrollArea>
                </div>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default CISComparison;
