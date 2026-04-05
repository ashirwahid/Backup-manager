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
  Shield,
  Upload,
  Trash2,
  Check,
  Square,
  CheckSquare
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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
const PolicyCard = ({ item, category, onView, onSelect, isSelected, onDeploy, onDelete, isDeploying }) => {
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

  const policyName = item.name || item.policy?.displayName || item.tenant?.displayName || "Unknown";
  const policyId = item.policy?.id || item.tenant?.id;

  return (
    <div className={`card-base mb-2 ${getCategoryStyles()} ${isSelected ? 'ring-2 ring-blue-500' : ''}`}>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <div className="flex items-center">
          {/* Checkbox for selection */}
          {(category === "tenant_only" || category === "baseline_only") && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSelect(item);
              }}
              className="p-3 hover:bg-zinc-100 transition-colors"
            >
              {isSelected ? (
                <CheckSquare className="w-4 h-4 text-blue-600" />
              ) : (
                <Square className="w-4 h-4 text-zinc-400" />
              )}
            </button>
          )}
          
          <CollapsibleTrigger className="flex-1">
            <div className="flex items-center justify-between p-3 pl-0 hover:bg-zinc-50/50 transition-colors">
              <div className="flex items-center gap-2">
                {isOpen ? (
                  <ChevronDown className="w-4 h-4 text-zinc-400" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-zinc-400" />
                )}
                <span className="text-sm font-medium text-zinc-900 truncate max-w-[200px]">
                  {policyName}
                </span>
              </div>
              {category === "conflicting" && item.differences && (
                <Badge variant="outline" className="text-red-600 border-red-200">
                  {item.differences.length} diff
                </Badge>
              )}
            </div>
          </CollapsibleTrigger>
        </div>
        
        <CollapsibleContent>
          <div className="px-3 pb-3 border-t border-zinc-100">
            {category === "conflicting" && item.differences && (
              <div className="mt-3 space-y-2">
                <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Differences</p>
                <div className="space-y-2 max-h-40 overflow-y-auto">
                  {item.differences.slice(0, 5).map((diff, idx) => (
                    <div key={idx} className="bg-zinc-50 rounded-sm p-2 text-xs">
                      <p className="font-medium text-zinc-700 mb-1">{diff.field}</p>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <p className="text-[10px] text-zinc-400 uppercase">Tenant</p>
                          <code className="font-mono text-blue-600 break-all text-[10px]">
                            {JSON.stringify(diff.tenant_value)?.substring(0, 50)}
                          </code>
                        </div>
                        <div>
                          <p className="text-[10px] text-zinc-400 uppercase">Baseline</p>
                          <code className="font-mono text-amber-600 break-all text-[10px]">
                            {JSON.stringify(diff.baseline_value)?.substring(0, 50)}
                          </code>
                        </div>
                      </div>
                    </div>
                  ))}
                  {item.differences.length > 5 && (
                    <p className="text-xs text-zinc-400">+{item.differences.length - 5} more differences</p>
                  )}
                </div>
              </div>
            )}
            
            <div className="mt-3 flex gap-2 flex-wrap">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onView(item, category)}
                className="gap-1 text-xs"
              >
                <Eye className="w-3 h-3" />
                View JSON
              </Button>
              
              {/* Deploy button for baseline_only items */}
              {category === "baseline_only" && (
                <Button
                  size="sm"
                  onClick={() => onDeploy(item)}
                  disabled={isDeploying}
                  className="gap-1 text-xs bg-emerald-600 hover:bg-emerald-700"
                >
                  {isDeploying ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Upload className="w-3 h-3" />
                  )}
                  Deploy
                </Button>
              )}
              
              {/* Delete button for tenant_only items */}
              {category === "tenant_only" && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => onDelete(item)}
                  disabled={isDeploying}
                  className="gap-1 text-xs"
                >
                  {isDeploying ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Trash2 className="w-3 h-3" />
                  )}
                  Remove
                </Button>
              )}
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
};

// Category Column Component
const CategoryColumn = ({ 
  title, 
  icon: Icon, 
  items, 
  category, 
  color, 
  onViewPolicy,
  selectedItems,
  onSelectItem,
  onSelectAll,
  onDeploy,
  onDelete,
  deployingId
}) => {
  const allSelected = items.length > 0 && selectedItems.length === items.length;
  const someSelected = selectedItems.length > 0 && selectedItems.length < items.length;
  
  return (
    <div className="flex flex-col h-full">
      <div className={`flex items-center gap-2 p-3 border-b ${color.border} ${color.bg}`}>
        <Icon className={`w-4 h-4 ${color.icon}`} strokeWidth={1.5} />
        <h3 className={`text-sm font-semibold ${color.text}`}>{title}</h3>
        <Badge variant="secondary" className="ml-auto">
          {items.length}
        </Badge>
      </div>
      
      {/* Bulk actions for tenant_only and baseline_only */}
      {(category === "tenant_only" || category === "baseline_only") && items.length > 0 && (
        <div className="p-2 border-b border-zinc-100 bg-zinc-50/50 flex items-center gap-2">
          <button
            onClick={() => onSelectAll(category)}
            className="flex items-center gap-1 text-xs text-zinc-600 hover:text-zinc-900"
          >
            {allSelected ? (
              <CheckSquare className="w-3.5 h-3.5 text-blue-600" />
            ) : someSelected ? (
              <CheckSquare className="w-3.5 h-3.5 text-blue-400" />
            ) : (
              <Square className="w-3.5 h-3.5" />
            )}
            {allSelected ? "Deselect All" : "Select All"}
          </button>
          
          {selectedItems.length > 0 && (
            <>
              <span className="text-xs text-zinc-400">|</span>
              <span className="text-xs text-zinc-500">{selectedItems.length} selected</span>
              
              {category === "baseline_only" && (
                <Button
                  size="sm"
                  onClick={() => onDeploy(null, true)}
                  className="ml-auto h-6 text-xs gap-1 bg-emerald-600 hover:bg-emerald-700"
                >
                  <Upload className="w-3 h-3" />
                  Deploy Selected
                </Button>
              )}
              
              {category === "tenant_only" && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => onDelete(null, true)}
                  className="ml-auto h-6 text-xs gap-1"
                >
                  <Trash2 className="w-3 h-3" />
                  Remove Selected
                </Button>
              )}
            </>
          )}
        </div>
      )}
      
      <ScrollArea className="flex-1 p-2">
        {items.length > 0 ? (
          items.map((item, idx) => (
            <PolicyCard 
              key={idx} 
              item={item} 
              category={category}
              onView={onViewPolicy}
              onSelect={onSelectItem}
              isSelected={selectedItems.some(s => 
                (s.name === item.name) || 
                (s.policy?.id === item.policy?.id)
              )}
              onDeploy={onDeploy}
              onDelete={onDelete}
              isDeploying={deployingId === (item.name || item.policy?.id)}
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
  const [deployingId, setDeployingId] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deployDialogOpen, setDeployDialogOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState(null);
  
  // Selection state for each category
  const [selectedTenantOnly, setSelectedTenantOnly] = useState([]);
  const [selectedBaselineOnly, setSelectedBaselineOnly] = useState([]);
  
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
      setSelectedTenantOnly([]);
      setSelectedBaselineOnly([]);
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

  // Selection handlers
  const handleSelectTenantOnly = (item) => {
    setSelectedTenantOnly(prev => {
      const isSelected = prev.some(s => s.name === item.name || s.policy?.id === item.policy?.id);
      if (isSelected) {
        return prev.filter(s => s.name !== item.name && s.policy?.id !== item.policy?.id);
      } else {
        return [...prev, item];
      }
    });
  };

  const handleSelectBaselineOnly = (item) => {
    setSelectedBaselineOnly(prev => {
      const isSelected = prev.some(s => s.name === item.name);
      if (isSelected) {
        return prev.filter(s => s.name !== item.name);
      } else {
        return [...prev, item];
      }
    });
  };

  const handleSelectAll = (category) => {
    if (category === "tenant_only") {
      if (selectedTenantOnly.length === comparison?.tenant_only?.length) {
        setSelectedTenantOnly([]);
      } else {
        setSelectedTenantOnly([...comparison.tenant_only]);
      }
    } else if (category === "baseline_only") {
      if (selectedBaselineOnly.length === comparison?.baseline_only?.length) {
        setSelectedBaselineOnly([]);
      } else {
        setSelectedBaselineOnly([...comparison.baseline_only]);
      }
    }
  };

  // Deploy handler
  const handleDeploy = (item, isBulk = false) => {
    if (isBulk) {
      setPendingAction({ type: "deploy_bulk", items: selectedBaselineOnly });
    } else {
      setPendingAction({ type: "deploy_single", item });
    }
    setDeployDialogOpen(true);
  };

  const confirmDeploy = async () => {
    setDeployDialogOpen(false);
    
    try {
      if (pendingAction.type === "deploy_single") {
        const item = pendingAction.item;
        setDeployingId(item.name);
        
        // Determine policy type from the policy structure
        const policy = item.policy;
        let detectedType = policyType;
        if (policyType === "all") {
          // Try to detect type from @odata.type or other indicators
          if (policy["@odata.type"]?.includes("conditionalAccess")) {
            detectedType = "conditional_access";
          } else if (policy["@odata.type"]?.includes("deviceConfiguration")) {
            detectedType = "device_configuration";
          } else if (policy["@odata.type"]?.includes("compliance")) {
            detectedType = "compliance";
          } else {
            detectedType = "configuration";
          }
        }
        
        const response = await axios.post(`${API}/deploy/policy`, {
          policy: policy,
          policy_type: detectedType
        });
        
        if (response.data.success) {
          toast.success(response.data.message);
          runComparison(); // Refresh comparison
        } else {
          toast.error(response.data.message);
        }
      } else if (pendingAction.type === "deploy_bulk") {
        const items = pendingAction.items;
        setDeployingId("bulk");
        
        let detectedType = policyType;
        if (policyType === "all") {
          detectedType = "configuration"; // Default for bulk
        }
        
        const response = await axios.post(`${API}/deploy/bulk`, {
          policies: items.map(i => i.policy),
          policy_type: detectedType
        });
        
        toast.success(response.data.message);
        setSelectedBaselineOnly([]);
        runComparison();
      }
    } catch (err) {
      const errorMsg = err.response?.data?.detail || "Failed to deploy";
      toast.error(errorMsg);
    } finally {
      setDeployingId(null);
      setPendingAction(null);
    }
  };

  // Delete handler
  const handleDelete = (item, isBulk = false) => {
    if (isBulk) {
      setPendingAction({ type: "delete_bulk", items: selectedTenantOnly });
    } else {
      setPendingAction({ type: "delete_single", item });
    }
    setDeleteDialogOpen(true);
  };

  const confirmDelete = async () => {
    setDeleteDialogOpen(false);
    
    try {
      if (pendingAction.type === "delete_single") {
        const item = pendingAction.item;
        const policyId = item.policy?.id;
        
        if (!policyId) {
          toast.error("Policy ID not found");
          return;
        }
        
        setDeployingId(item.name);
        
        let detectedType = policyType;
        if (policyType === "all") {
          const policy = item.policy;
          if (policy["@odata.type"]?.includes("conditionalAccess")) {
            detectedType = "conditional_access";
          } else if (policy["@odata.type"]?.includes("deviceConfiguration")) {
            detectedType = "device_configuration";
          } else if (policy["@odata.type"]?.includes("compliance")) {
            detectedType = "compliance";
          } else {
            detectedType = "configuration";
          }
        }
        
        const response = await axios.delete(`${API}/deploy/policy`, {
          data: {
            policy_id: policyId,
            policy_type: detectedType
          }
        });
        
        if (response.data.success) {
          toast.success("Policy removed from tenant");
          runComparison();
        } else {
          toast.error(response.data.message);
        }
      } else if (pendingAction.type === "delete_bulk") {
        const items = pendingAction.items;
        setDeployingId("bulk");
        
        let detectedType = policyType;
        if (policyType === "all") {
          detectedType = "configuration";
        }
        
        const response = await axios.post(`${API}/deploy/bulk-delete`, {
          policy_ids: items.map(i => i.policy?.id).filter(Boolean),
          policy_type: detectedType
        });
        
        toast.success(response.data.message);
        setSelectedTenantOnly([]);
        runComparison();
      }
    } catch (err) {
      const errorMsg = err.response?.data?.detail || "Failed to delete";
      toast.error(errorMsg);
    } finally {
      setDeployingId(null);
      setPendingAction(null);
    }
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
            Compare, deploy, and remove policies
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
            <p className="text-xs text-zinc-500 mt-1">Can be removed</p>
          </div>
          <div className="stat-card border-l-4 border-l-amber-500">
            <p className="label-text">Baseline Only</p>
            <p className="stat-value text-amber-600">{comparison.summary.baseline_only_count}</p>
            <p className="text-xs text-zinc-500 mt-1">Can be deployed</p>
          </div>
          <div className="stat-card border-l-4 border-l-red-500">
            <p className="label-text">Conflicting</p>
            <p className="stat-value text-red-600">{comparison.summary.conflicting_count}</p>
            <p className="text-xs text-zinc-500 mt-1">Different settings</p>
          </div>
          <div className="stat-card border-l-4 border-l-emerald-500">
            <p className="label-text">Matching</p>
            <p className="stat-value text-emerald-600">{comparison.summary.matching_count}</p>
            <p className="text-xs text-zinc-500 mt-1">In sync</p>
          </div>
        </div>
      )}

      {/* Comparison Grid */}
      {comparison ? (
        <div className="grid grid-cols-4 gap-4 h-[calc(100vh-380px)]">
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
              selectedItems={selectedTenantOnly}
              onSelectItem={handleSelectTenantOnly}
              onSelectAll={handleSelectAll}
              onDeploy={handleDeploy}
              onDelete={handleDelete}
              deployingId={deployingId}
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
              selectedItems={selectedBaselineOnly}
              onSelectItem={handleSelectBaselineOnly}
              onSelectAll={handleSelectAll}
              onDeploy={handleDeploy}
              onDelete={handleDelete}
              deployingId={deployingId}
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
              selectedItems={[]}
              onSelectItem={() => {}}
              onSelectAll={() => {}}
              onDeploy={() => {}}
              onDelete={() => {}}
              deployingId={deployingId}
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
              selectedItems={[]}
              onSelectItem={() => {}}
              onSelectAll={() => {}}
              onDeploy={() => {}}
              onDelete={() => {}}
              deployingId={deployingId}
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

      {/* Deploy Confirmation Dialog */}
      <AlertDialog open={deployDialogOpen} onOpenChange={setDeployDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="font-heading flex items-center gap-2">
              <Upload className="w-5 h-5 text-emerald-600" />
              Deploy to Tenant
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingAction?.type === "deploy_bulk" 
                ? `This will deploy ${pendingAction.items?.length} policies from the CIS baseline to your tenant.`
                : `This will deploy "${pendingAction?.item?.name}" to your tenant.`
              }
              <br /><br />
              <strong className="text-amber-600">Note:</strong> Conditional Access policies will be created in <strong>disabled</strong> state for safety. You'll need to enable them manually after review.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDeploy}
              className="bg-emerald-600 hover:bg-emerald-700"
            >
              Deploy
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="font-heading flex items-center gap-2 text-red-600">
              <Trash2 className="w-5 h-5" />
              Remove from Tenant
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingAction?.type === "delete_bulk" 
                ? `This will permanently delete ${pendingAction.items?.length} policies from your tenant.`
                : `This will permanently delete "${pendingAction?.item?.name}" from your tenant.`
              }
              <br /><br />
              <strong className="text-red-600">Warning:</strong> This action cannot be undone. Make sure you have a backup before proceeding.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              className="bg-red-600 hover:bg-red-700"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default CISComparison;
