import { useState, useEffect, useMemo } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import {
  GitCompare,
  CheckCircle2,
  AlertTriangle,
  Plus,
  Loader2,
  Eye,
  ChevronDown,
  ChevronRight,
  Copy,
  FileText,
  Folder,
  Shield,
  Cloud,
  Upload,
  Trash2,
  Square,
  CheckSquare,
  History,
  Users,
} from "lucide-react";
import {
  POLICY_KIND_FILTER_LABELS,
  POLICY_KIND_LABELS,
  POLICY_KIND_ORDER,
  policyKindLabel,
  buildFolderSectionsWithDependencies,
  detectPolicyType,
  formatDiffValue,
  getDependencyDisplayLines,
  getFolderSectionShortLabel,
  getPolicyCompareKey,
  getPolicyDisplayName,
  getPolicyItemId,
  getPolicySubtitle,
  stripDeployMetadata,
} from "@/lib/cisComparisonUtils";
import {
  buildPendingOperationsFromDeleteAction,
  buildPendingOperationsFromDeployAction,
} from "@/lib/deploymentOperations";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
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
import {
  isRequestAborted,
  useCancellableRequests,
} from "@/lib/useCancellableRequests";

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

const directoryDepKey = (depKind, objectId) => `${depKind}:${objectId}`;

const dependencyDirectoryLabel = (doc, objectId, depKind) =>
  getDependencyDisplayLines(doc, objectId, depKind).primary;

/** Full policy / group name with optional subtitle; wraps instead of truncating. */
const PolicyRowLabel = ({
  title,
  subtitle,
  className = "",
  titleClassName = "text-sm font-medium text-zinc-900",
}) => {
  if (!title) return null;
  const body = (
    <div className={`min-w-0 flex-1 text-left ${className}`}>
      <p className={`${titleClassName} break-words leading-snug`}>{title}</p>
      {subtitle && (
        <p className="mt-0.5 break-all font-mono text-[10px] leading-tight text-zinc-500">
          {subtitle}
        </p>
      )}
    </div>
  );
  if (title.length < 48 && (!subtitle || subtitle.length < 64)) {
    return body;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="min-w-0 flex-1 cursor-default text-left">{body}</div>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        className="max-w-sm bg-zinc-900 text-zinc-50 text-xs leading-relaxed"
      >
        <p className="font-medium">{title}</p>
        {subtitle && <p className="mt-1 font-mono opacity-90">{subtitle}</p>}
      </TooltipContent>
    </Tooltip>
  );
};

const rowPolicyFromItem = (item, category) => {
  if (category === "tenant_only" || category === "baseline_only") return item.policy;
  return item.tenant || item.baseline;
};

const filterComparisonByPolicyId = (comparison, policyId) => {
  if (!comparison || !policyId) return comparison;
  const matches = (item, category) => {
    const row = rowPolicyFromItem(item, category);
    const id = getPolicyItemId(item, category);
    const compareKey = getPolicyCompareKey(row);
    if (policyId.endsWith(":assignments")) {
      const baseId = policyId.slice(0, -":assignments".length);
      return (
        (id === baseId || compareKey === baseId) &&
        row?._backupKind === "assignments"
      );
    }
    return (
      (id === policyId || compareKey === policyId) &&
      row?._backupKind !== "assignments"
    );
  };
  const filterCat = (list, category) =>
    (list || []).filter((item) => matches(item, category));
  return {
    ...comparison,
    tenant_only: filterCat(comparison.tenant_only, "tenant_only"),
    baseline_only: filterCat(comparison.baseline_only, "baseline_only"),
    conflicting: filterCat(comparison.conflicting, "conflicting"),
    matching: filterCat(comparison.matching, "matching"),
    summary: {
      tenant_only_count: filterCat(comparison.tenant_only, "tenant_only").length,
      baseline_only_count: filterCat(comparison.baseline_only, "baseline_only").length,
      conflicting_count: filterCat(comparison.conflicting, "conflicting").length,
      matching_count: filterCat(comparison.matching, "matching").length,
    },
  };
};

// Policy Card Component for each category
const PolicyCard = ({
  item,
  category,
  onView,
  onSelect,
  isSelected,
  onDeploy,
  onDelete,
  isDeploying,
  diffLabels = { current: "Tenant", previous: "Baseline" },
}) => {
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

  const policyName = getPolicyDisplayName(item, category);
  const policyId = getPolicyItemId(item, category);
  const rowPolicy =
    category === "tenant_only" || category === "baseline_only"
      ? item.policy
      : item.tenant || item.baseline;
  const isAssignmentsRow = rowPolicy?._backupKind === "assignments";
  const policySubtitle = getPolicySubtitle(rowPolicy, category, item);

  return (
    <div className={`card-base mb-2 ${getCategoryStyles()} ${isSelected ? "ring-2 ring-blue-500" : ""}`}>
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
          
          <CollapsibleTrigger className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2 p-3 pl-0 hover:bg-zinc-50/50 transition-colors">
              <div className="flex min-w-0 flex-1 items-start gap-2">
                {isOpen ? (
                  <ChevronDown className="w-4 h-4 text-zinc-400" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-zinc-400" />
                )}
                <PolicyRowLabel title={policyName} subtitle={policySubtitle} />
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                {isAssignmentsRow && (
                  <Badge variant="outline" className="text-violet-700 border-violet-200 text-[10px]">
                    Assignments
                  </Badge>
                )}
                {category === "conflicting" && item.differences && (
                  <Badge variant="outline" className="text-red-600 border-red-200 text-[10px]">
                    {item.differences.length} diff
                  </Badge>
                )}
              </div>
            </div>
          </CollapsibleTrigger>
        </div>
        
        <CollapsibleContent>
          <div className="px-3 pb-3 border-t border-zinc-100">
            {category === "conflicting" && item.differences && (
              <div className="mt-3 space-y-2">
                <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Differences</p>
                <div className="space-y-2 max-h-56 overflow-y-auto">
                  {item.differences.slice(0, 8).map((diff, idx) => (
                    <div key={idx} className="bg-zinc-50 rounded-sm p-2 text-xs">
                      <p className="font-medium text-zinc-700 mb-1">{diff.field}</p>
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <div>
                          <p className="text-[10px] text-zinc-400 uppercase">{diffLabels.current}</p>
                          <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-blue-700">
                            {formatDiffValue(diff.tenant_value)}
                          </pre>
                        </div>
                        <div>
                          <p className="text-[10px] text-zinc-400 uppercase">{diffLabels.previous}</p>
                          <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-amber-700">
                            {formatDiffValue(diff.baseline_value)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  ))}
                  {item.differences.length > 8 && (
                    <p className="text-xs text-zinc-400">+{item.differences.length - 8} more differences</p>
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
              
              {(category === "tenant_only" || category === "matching") && onDelete && (
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

              {category === "conflicting" && onDeploy && (
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
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
};

const DependencyDirectoryCard = ({
  objectId,
  depKind,
  doc,
  category,
  onView,
  directoryResolution,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const lines = getDependencyDisplayLines(doc, objectId, depKind);
  const missing = !doc && !directoryResolution?.loading;

  const getCategoryStyles = () => {
    switch (category) {
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
        <CollapsibleTrigger className="flex w-full items-center justify-between p-3 hover:bg-zinc-50/50">
          <div className="flex min-w-0 items-center gap-2">
            {isOpen ? (
              <ChevronDown className="w-4 h-4 shrink-0 text-zinc-400" />
            ) : (
              <ChevronRight className="w-4 h-4 shrink-0 text-zinc-400" />
            )}
            <Users className="w-3.5 h-3.5 shrink-0 text-violet-600" />
            <PolicyRowLabel
              title={lines.primary}
              subtitle={
                lines.secondary
                  ? `${lines.secondary} · ${lines.objectId}`
                  : lines.objectId
              }
            />
          </div>
          <Badge variant="outline" className="shrink-0 text-[10px] capitalize">
            {depKind}
          </Badge>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border-t border-zinc-100 px-3 pb-3 pt-2">
            {directoryResolution?.loading && (
              <p className="flex items-center gap-2 text-xs text-zinc-500">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading from DevOps…
              </p>
            )}
            {missing && !directoryResolution?.loading && (
              <p className="text-xs text-amber-700">Not found in dependencies backup</p>
            )}
            <div className="mt-2 flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!doc}
                onClick={() =>
                  onView({
                    category: "directory_dependency",
                    depKind,
                    objectId,
                    doc,
                    name: lines.primary,
                  })
                }
                className="gap-1 text-xs"
              >
                <Eye className="w-3 h-3" />
                View JSON
              </Button>
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
  subtitle,
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
  deployingId,
  diffLabels,
  hideTenantActions = false,
  groupByKind = true,
  comparisonPolicyType = "all",
  directoryResolution,
}) => {
  const allSelected = items.length > 0 && selectedItems.length === items.length;
  const someSelected = selectedItems.length > 0 && selectedItems.length < items.length;

  const { sections: folderSections } = buildFolderSectionsWithDependencies(
    items,
    category,
    comparisonPolicyType,
    directoryResolution
  );

  const renderPolicyCard = (item, idx) => (
    <PolicyCard
      key={getPolicyItemId(item, category) || idx}
      item={item}
      category={category}
      onView={onViewPolicy}
      onSelect={onSelectItem}
      isSelected={selectedItems.some(
        (s) =>
          s.name === item.name ||
          s.policy?.id === item.policy?.id ||
          getPolicyItemId(s, category) === getPolicyItemId(item, category)
      )}
      onDeploy={onDeploy}
      onDelete={onDelete}
      isDeploying={
        deployingId ===
        (getPolicyItemId(item, category) || getPolicyDisplayName(item, category))
      }
      diffLabels={diffLabels}
    />
  );

  const renderSectionBody = (section) => {
    if (section.dependencyIds) {
      const byId =
        section.dependencyKind === "user"
          ? directoryResolution?.userById || {}
          : directoryResolution?.groupById || {};
      return section.dependencyIds.map((objectId) => (
        <DependencyDirectoryCard
          key={directoryDepKey(section.dependencyKind, objectId)}
          objectId={objectId}
          depKind={section.dependencyKind}
          doc={byId[objectId]}
          category={category}
          onView={onViewPolicy}
          directoryResolution={directoryResolution}
        />
      ));
    }
    return section.items.map((item, idx) => renderPolicyCard(item, idx));
  };

  return (
    <div className="flex flex-col h-full">
      <div className={`flex items-center gap-2 p-3 border-b ${color.border} ${color.bg}`}>
        <Icon className={`w-4 h-4 ${color.icon}`} strokeWidth={1.5} />
        <div className="min-w-0 flex-1">
          <h3 className={`text-sm font-semibold ${color.text}`}>{title}</h3>
          {subtitle && (
            <p className="text-[10px] text-zinc-500 break-words leading-snug">{subtitle}</p>
          )}
        </div>
        <Badge variant="secondary" className="ml-auto shrink-0">
          {items.length}
        </Badge>
      </div>
      
      {/* Bulk actions for tenant_only and baseline_only */}
      {!hideTenantActions && (category === "tenant_only" || category === "baseline_only") && items.length > 0 && (
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
          groupByKind && folderSections.length > 0 ? (
            <div className="space-y-2">
              {folderSections.map((section) => {
                const count = section.dependencyIds?.length ?? section.items?.length ?? 0;
                return (
                  <Collapsible
                    key={section.id}
                    defaultOpen={
                      section.dependencyIds
                        ? false
                        : section.id === "conditional_access" || comparisonPolicyType !== "all"
                    }
                    className="group rounded-md border border-zinc-200/80 bg-white"
                  >
                    <CollapsibleTrigger className="flex w-full items-center gap-2 px-2 py-2 text-left hover:bg-zinc-50">
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform duration-200 group-data-[state=open]:rotate-90" />
                      <Folder
                        className={`h-4 w-4 shrink-0 ${
                          section.dependencyIds ? "text-violet-600/90" : "text-amber-600/90"
                        }`}
                        strokeWidth={1.5}
                      />
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="min-w-0 flex-1 text-left text-xs font-medium leading-snug text-zinc-800 break-words">
                            {section.shortLabel || getFolderSectionShortLabel(section.id) || section.label}
                          </span>
                        </TooltipTrigger>
                        {section.label && section.label !== (section.shortLabel || section.id) && (
                          <TooltipContent
                            side="right"
                            className="max-w-xs bg-zinc-900 text-zinc-50 text-xs leading-relaxed"
                          >
                            {section.label}
                          </TooltipContent>
                        )}
                      </Tooltip>
                      <Badge variant="secondary" className="shrink-0 text-[10px]">
                        {count}
                      </Badge>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="space-y-1 px-1 pb-2 pt-1">
                      {renderSectionBody(section)}
                    </CollapsibleContent>
                  </Collapsible>
                );
              })}
            </div>
          ) : (
            items.map((item, idx) => renderPolicyCard(item, idx))
          )
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
  const [compareMode, setCompareMode] = useState("cis");
  const [tenantSnapshotId, setTenantSnapshotId] = useState("latest");
  const [tenantReferenceSnapshotId, setTenantReferenceSnapshotId] = useState("");
  const [baselineSnapshotId, setBaselineSnapshotId] = useState("latest");
  const [comparisonSources, setComparisonSources] = useState({
    tenant_snapshots: [
      {
        id: "latest",
        label: "Tenant · latest (default)",
      },
    ],
    baseline_options: [
      {
        id: "latest",
        label: "CIS baseline · latest (default)",
      },
    ],
    policy_inventory: {},
    baseline_inventory: {},
    loading: false,
  });
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [policyFilter, setPolicyFilter] = useState("all");
  const [directoryResolution, setDirectoryResolution] = useState({
    userById: {},
    groupById: {},
    loading: false,
    error: null,
  });
  const [devopsConfigured, setDevopsConfigured] = useState(false);
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
  const { begin: beginRequest, abort: abortRequest } = useCancellableRequests();

  useEffect(() => {
    checkConfiguration();
  }, []);

  const fetchComparisonSources = async (
    commitId,
    {
      includeInventory = false,
      includeBaselineSnapshots = true,
    } = {}
  ) => {
    const signal = beginRequest("comparison-sources");
    try {
      setSourcesLoading(true);
      const params = {
        include_inventory: includeInventory,
        include_baseline_snapshots: includeBaselineSnapshots,
      };
      if (commitId && commitId !== "latest") {
        params.tenant_commit_id = commitId;
      }
      const response = await axios.get(`${API}/baseline/comparison-sources`, {
        params,
        signal,
      });
      setComparisonSources((prev) => ({
        tenant_snapshots: response.data.tenant_snapshots?.length
          ? response.data.tenant_snapshots
          : prev.tenant_snapshots?.length
            ? prev.tenant_snapshots
            : [{ id: "latest", label: "Tenant · latest" }],
        baseline_options: response.data.baseline_options?.length
          ? response.data.baseline_options
          : prev.baseline_options?.length
            ? prev.baseline_options
            : [{ id: "latest", label: "CIS baseline · latest" }],
        policy_inventory: includeInventory
          ? response.data.policy_inventory || {}
          : prev.policy_inventory || {},
        baseline_inventory: includeInventory
          ? response.data.baseline_inventory || {}
          : prev.baseline_inventory || {},
        loading: false,
      }));
    } catch (err) {
      if (isRequestAborted(err)) return;
      console.error("Failed to load comparison sources", err);
      toast.error("Failed to load snapshot list from DevOps/GitHub");
    } finally {
      if (!signal.aborted) {
        setSourcesLoading(false);
      }
    }
  };

  useEffect(() => {
    if (compareMode !== "tenant" || !devopsConfigured) return;
    const onlyDefault = comparisonSources.tenant_snapshots.length <= 1;
    if (onlyDefault && !sourcesLoading) {
      fetchComparisonSources(undefined, {
        includeInventory: false,
        includeBaselineSnapshots: false,
      });
    }
  }, [compareMode, devopsConfigured]);

  useEffect(() => {
    if (compareMode !== "tenant" || comparisonSources.tenant_snapshots.length < 2) return;
    if (tenantReferenceSnapshotId) return;
    const firstPast = comparisonSources.tenant_snapshots.find((s) => s.id !== "latest");
    if (firstPast) setTenantReferenceSnapshotId(firstPast.id);
  }, [compareMode, comparisonSources.tenant_snapshots, tenantReferenceSnapshotId]);

  const selectedTenantSnapshot = comparisonSources.tenant_snapshots.find(
    (s) => s.id === tenantSnapshotId
  );
  const isHistoricalTenant =
    compareMode === "cis" && tenantSnapshotId && tenantSnapshotId !== "latest";
  const isTenantHistoryMode = compareMode === "tenant";

  const columnLabels = comparison?.column_labels || null;
  const diffLabels = columnLabels
    ? { current: columnLabels.diff_current, previous: columnLabels.diff_previous }
    : { current: "Tenant", previous: "Baseline" };
  const hideTenantActions =
    isHistoricalTenant ||
    (isTenantHistoryMode && tenantSnapshotId !== "latest");
  const col = (key, fallbackTitle, fallbackHint) => ({
    title: columnLabels?.[key]?.title ?? fallbackTitle,
    subtitle: columnLabels?.[key]?.subtitle,
    hint: columnLabels?.[key]?.hint ?? fallbackHint,
  });

  const checkConfiguration = async () => {
    const signal = beginRequest("settings");
    try {
      setCheckingConfig(true);
      const response = await axios.get(`${API}/settings`, { signal });
      setGithubConfigured(response.data.github_configured);
      setDevopsConfigured(!!response.data.devops_configured);
    } catch (err) {
      if (isRequestAborted(err)) return;
      console.error("Failed to check configuration", err);
    } finally {
      if (!signal.aborted) {
        setCheckingConfig(false);
      }
    }
  };

  const focusedPolicyId = useMemo(() => {
    if (!policyFilter.includes(":")) return null;
    return policyFilter.split(":").slice(1).join(":");
  }, [policyFilter]);

  const displayComparison = useMemo(() => {
    if (!comparison) return null;
    return filterComparisonByPolicyId(comparison, focusedPolicyId);
  }, [comparison, focusedPolicyId]);

  useEffect(() => {
    const refs = comparison?.directory_refs;
    if (!refs) return;
    const userIds = refs.user_ids || [];
    const groupIds = refs.group_ids || [];
    if (!userIds.length && !groupIds.length) {
      setDirectoryResolution({
        userById: {},
        groupById: {},
        loading: false,
        error: null,
      });
      return;
    }
    const signal = beginRequest("dependencies");
    (async () => {
      setDirectoryResolution((prev) => ({ ...prev, loading: true, error: null }));
      try {
        const commit =
          tenantSnapshotId && tenantSnapshotId !== "latest"
            ? tenantSnapshotId
            : undefined;
        const res = await axios.post(
          `${API}/devops/dependencies/read`,
          {
            user_ids: userIds,
            group_ids: groupIds,
            tenant_commit_id: commit,
          },
          { signal }
        );
        if (signal.aborted) return;
        setDirectoryResolution({
          userById: res.data.user_by_id || {},
          groupById: res.data.group_by_id || {},
          loading: false,
          error: null,
        });
      } catch (err) {
        if (isRequestAborted(err)) return;
        setDirectoryResolution({
          userById: {},
          groupById: {},
          loading: false,
          error: err.response?.data?.detail || "Failed to load dependencies",
        });
      }
    })();
    return () => abortRequest("dependencies");
  }, [comparison, tenantSnapshotId, beginRequest, abortRequest]);

  const handlePolicyFilterChange = (value) => {
    setPolicyFilter(value);
    if (value === "all") {
      setPolicyType("all");
    } else if (value.includes(":")) {
      const [kind] = value.split(":");
      setPolicyType(kind);
    } else {
      setPolicyType(value);
    }
  };

  const deployDevOpsCommitId = () => {
    if (compareMode === "tenant" && tenantReferenceSnapshotId && tenantReferenceSnapshotId !== "latest") {
      return tenantReferenceSnapshotId;
    }
    if (tenantSnapshotId && tenantSnapshotId !== "latest") {
      return tenantSnapshotId;
    }
    return undefined;
  };

  const runComparison = async () => {
    const signal = beginRequest("compare");
    try {
      setLoading(true);
      setSelectedTenantOnly([]);
      setSelectedBaselineOnly([]);
      setDirectoryResolution({
        userById: {},
        groupById: {},
        loading: false,
        error: null,
      });
      const params = new URLSearchParams({
        policy_type: policyType,
        compare_mode: compareMode,
      });
      if (compareMode === "cis" && !githubConfigured) {
        toast.error("Configure GitHub baseline in Settings for CIS comparison");
        setLoading(false);
        return;
      }
      if (compareMode === "tenant") {
        if (!tenantReferenceSnapshotId) {
          toast.error("Select a previous tenant snapshot to compare against");
          setLoading(false);
          return;
        }
        if (tenantSnapshotId && tenantSnapshotId !== "latest") {
          params.set("tenant_commit_id", tenantSnapshotId);
        }
        params.set("tenant_reference_commit_id", tenantReferenceSnapshotId);
      } else {
        if (tenantSnapshotId && tenantSnapshotId !== "latest") {
          params.set("tenant_commit_id", tenantSnapshotId);
        }
        if (baselineSnapshotId && baselineSnapshotId !== "latest") {
          const opt = comparisonSources.baseline_options.find(
            (b) => b.id === baselineSnapshotId
          );
          params.set("baseline_ref", opt?.ref || baselineSnapshotId);
        }
      }
      const response = await axios.post(
        `${API}/baseline/compare?${params.toString()}`,
        null,
        { signal }
      );
      if (signal.aborted) return;
      setComparison(response.data);
      toast.success("Comparison completed successfully");
      fetchComparisonSources(tenantSnapshotId, {
        includeInventory: true,
        includeBaselineSnapshots: compareMode === "cis",
      });
    } catch (err) {
      if (isRequestAborted(err)) return;
      const errorMsg = err.response?.data?.detail || "Failed to run comparison";
      toast.error(errorMsg);
    } finally {
      if (!signal.aborted) {
        setLoading(false);
      }
    }
  };

  const handleSnapshotDropdownOpen = (open) => {
    if (!open || sourcesLoading) return;
    const needsSnapshots = comparisonSources.tenant_snapshots.length <= 1;
    if (devopsConfigured && needsSnapshots) {
      fetchComparisonSources(tenantSnapshotId, {
        includeInventory: false,
        includeBaselineSnapshots: compareMode === "cis",
      });
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
      if (selectedTenantOnly.length === displayComparison?.tenant_only?.length) {
        setSelectedTenantOnly([]);
      } else {
        setSelectedTenantOnly([...(displayComparison?.tenant_only || [])]);
      }
    } else if (category === "baseline_only") {
      if (selectedBaselineOnly.length === displayComparison?.baseline_only?.length) {
        setSelectedBaselineOnly([]);
      } else {
        setSelectedBaselineOnly([...(displayComparison?.baseline_only || [])]);
      }
    }
  };

  // Deploy handler
  const handleDeploy = (item, isBulk = false) => {
    if (isBulk) {
      setPendingAction({ type: "deploy_bulk", items: selectedBaselineOnly });
    } else if (item?.baseline && item?.tenant) {
      setPendingAction({ type: "deploy_conflicting", item });
    } else {
      setPendingAction({ type: "deploy_single", item });
    }
    setDeployDialogOpen(true);
  };

  const goToDashboardWithPending = (pendingOperations) => {
    if (!pendingOperations?.length) return;
    navigate("/dashboard", { state: { pendingOperations } });
  };

  const confirmDeploy = () => {
    setDeployDialogOpen(false);
    const pendingOperations = buildPendingOperationsFromDeployAction(
      pendingAction,
      policyType,
      deployDevOpsCommitId()
    );
    if (!pendingOperations.length) {
      toast.error("Nothing to deploy");
      setPendingAction(null);
      return;
    }
    if (pendingAction?.type === "deploy_bulk") {
      setSelectedBaselineOnly([]);
    }
    setDeployingId(null);
    setPendingAction(null);
    goToDashboardWithPending(pendingOperations);
  };

  // Delete handler
  const handleDelete = (item, isBulk = false) => {
    if (isBulk) {
      setPendingAction({ type: "delete_bulk", items: selectedTenantOnly });
    } else if (item?.tenant?.id && item?.baseline && !item?.policy) {
      setPendingAction({ type: "delete_matching", item });
    } else {
      setPendingAction({ type: "delete_single", item });
    }
    setDeleteDialogOpen(true);
  };

  const confirmDelete = () => {
    setDeleteDialogOpen(false);
    const pendingOperations = buildPendingOperationsFromDeleteAction(
      pendingAction,
      policyType
    );
    if (!pendingOperations.length) {
      toast.error("Policy ID not found");
      setPendingAction(null);
      return;
    }
    if (pendingAction?.type === "delete_bulk") {
      setSelectedTenantOnly([]);
    }
    setDeployingId(null);
    setPendingAction(null);
    goToDashboardWithPending(pendingOperations);
  };

  if (checkingConfig) {
    return (
      <div data-testid="cis-loading" className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  if (false && githubConfigured) {
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

  if (!devopsConfigured) {
    return (
      <div data-testid="cis-devops-not-configured" className="space-y-6">
        <h1 className="font-heading text-2xl font-semibold text-zinc-900">CIS Baseline Comparison</h1>
        <div className="card-base p-8 text-center">
          <Cloud className="w-16 h-16 mx-auto text-zinc-300 mb-4" />
          <p className="text-sm text-zinc-500 mb-4">
            Configure Azure DevOps in Settings to load tenant policy snapshots.
          </p>
          <Button onClick={() => navigate("/settings")}>Configure Azure DevOps</Button>
        </div>
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={300}>
    <div data-testid="cis-comparison-page" className="space-y-6">
      {/* Header */}
      <div className="space-y-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
            CIS Baseline Comparison
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Tenant vs previous commit loads commit history automatically. Open a snapshot
            dropdown in CIS mode to refresh commits. Policy filter options load after the first run.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={compareMode} onValueChange={setCompareMode}>
            <SelectTrigger className="w-[200px]" data-testid="compare-mode-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cis">Tenant vs CIS baseline</SelectItem>
              <SelectItem value="tenant">Tenant vs previous commit</SelectItem>
            </SelectContent>
          </Select>
          <Select value={policyFilter} onValueChange={handlePolicyFilterChange}>
            <SelectTrigger className="w-[260px]" data-testid="policy-type-select">
              <SelectValue placeholder="Policy filter" />
            </SelectTrigger>
            <SelectContent className="max-h-[360px]">
              <SelectItem value="all">All policies</SelectItem>
              {POLICY_KIND_ORDER.map((kind) => {
                const tenantRows = comparisonSources.policy_inventory?.[kind] || [];
                const baselineRows =
                  comparisonSources.baseline_inventory?.[kind] || [];
                if (!tenantRows.length && !baselineRows.length) return null;
                const groupLabel = policyKindLabel(kind, comparisonSources);
                const filterLabel =
                  POLICY_KIND_FILTER_LABELS[kind] || groupLabel;
                return (
                  <SelectGroup key={kind}>
                    <SelectLabel>{filterLabel}</SelectLabel>
                    <SelectItem value={kind}>
                      All {filterLabel}
                    </SelectItem>
                    {tenantRows.map((row) => (
                      <SelectItem
                        key={`tenant:${kind}:${row.id}`}
                        value={`${kind}:${row.id}`}
                      >
                        Tenant · {row.label}
                      </SelectItem>
                    ))}
                    {baselineRows.map((row) => (
                      <SelectItem
                        key={`baseline:${kind}:${row.id}`}
                        value={`${kind}:${row.id}`}
                      >
                        CIS · {row.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                );
              })}
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
        {compareMode === "cis" ? (
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="label-text flex items-center gap-1.5">
                <Cloud className="w-3.5 h-3.5" /> Tenant snapshot (Azure DevOps)
              </label>
              <Select
                value={tenantSnapshotId}
                onValueChange={setTenantSnapshotId}
                onOpenChange={handleSnapshotDropdownOpen}
                disabled={sourcesLoading}
              >
                <SelectTrigger className="w-full" data-testid="tenant-snapshot-select">
                  <SelectValue placeholder="Tenant version" />
                  {sourcesLoading && <Loader2 className="ml-2 h-3 w-3 animate-spin" />}
                </SelectTrigger>
                <SelectContent className="max-h-[320px]">
                  {comparisonSources.tenant_snapshots.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label className="label-text flex items-center gap-1.5">
                <Shield className="w-3.5 h-3.5" /> CIS baseline (GitHub)
              </label>
              <Select
                value={baselineSnapshotId}
                onValueChange={setBaselineSnapshotId}
                onOpenChange={handleSnapshotDropdownOpen}
                disabled={sourcesLoading || !githubConfigured}
              >
                <SelectTrigger className="w-full" data-testid="baseline-snapshot-select">
                  <SelectValue placeholder="Baseline version" />
                </SelectTrigger>
                <SelectContent className="max-h-[320px]">
                  {comparisonSources.baseline_options.map((o) => (
                    <SelectItem key={o.id} value={o.id}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="label-text flex items-center gap-1.5">
                <Cloud className="w-3.5 h-3.5" /> Current tenant (Azure DevOps)
              </label>
              <Select
                value={tenantSnapshotId}
                onValueChange={setTenantSnapshotId}
                onOpenChange={handleSnapshotDropdownOpen}
                disabled={sourcesLoading}
              >
                <SelectTrigger className="w-full" data-testid="tenant-current-select">
                  <SelectValue placeholder="Current snapshot" />
                </SelectTrigger>
                <SelectContent className="max-h-[320px]">
                  {comparisonSources.tenant_snapshots.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label className="label-text flex items-center gap-1.5">
                <History className="w-3.5 h-3.5" /> Previous commit
              </label>
              <Select
                value={tenantReferenceSnapshotId}
                onValueChange={setTenantReferenceSnapshotId}
                onOpenChange={handleSnapshotDropdownOpen}
                disabled={sourcesLoading}
              >
                <SelectTrigger className="w-full" data-testid="tenant-reference-select">
                  <SelectValue placeholder="Older snapshot" />
                </SelectTrigger>
                <SelectContent className="max-h-[320px]">
                  {comparisonSources.tenant_snapshots
                    .filter((s) => s.id !== tenantSnapshotId)
                    .map((s) => (
                      <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}
      </div>

      {compareMode === "cis" && !githubConfigured && (
        <Alert className="border-amber-200 bg-amber-50">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Configure GitHub in Settings for CIS baseline comparison, or switch to Tenant vs previous commit.
          </AlertDescription>
        </Alert>
      )}
      {isTenantHistoryMode && (
        <Alert className="border-violet-200 bg-violet-50">
          <History className="h-4 w-4" />
          <AlertDescription>
            Only in current = added since the older backup. Only in previous = removed since then. Deploy restores policies to the live tenant.
          </AlertDescription>
        </Alert>
      )}

      {isHistoricalTenant && (
        <Alert className="border-blue-200 bg-blue-50 text-blue-950">
          <History className="h-4 w-4 text-blue-600" />
          <AlertTitle>Historical tenant snapshot</AlertTitle>
          <AlertDescription>
            Remove/deploy actions apply to the live tenant. Use Tenant · latest to change production.
          </AlertDescription>
        </Alert>
      )}

      {directoryResolution.error && comparison && (
        <Alert className="border-amber-200 bg-amber-50">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Could not load user/group dependencies from DevOps: {directoryResolution.error}
          </AlertDescription>
        </Alert>
      )}

      {/* Summary Stats */}
      {displayComparison && (
        <div className="grid grid-cols-4 gap-4">
          <div className="stat-card border-l-4 border-l-blue-500">
            <p className="label-text">{col("tenant_only", "Tenant Only", "Can be removed").title}</p>
            <p className="stat-value text-blue-600">{displayComparison.summary.tenant_only_count}</p>
            <p className="text-xs text-zinc-500 mt-1">{col("tenant_only", "Tenant Only", "Can be removed").hint}</p>
          </div>
          <div className="stat-card border-l-4 border-l-amber-500">
            <p className="label-text">{col("baseline_only", "Baseline Only", "Can be deployed").title}</p>
            <p className="stat-value text-amber-600">{displayComparison.summary.baseline_only_count}</p>
            <p className="text-xs text-zinc-500 mt-1">{col("baseline_only", "Baseline Only", "Can be deployed").hint}</p>
          </div>
          <div className="stat-card border-l-4 border-l-red-500">
            <p className="label-text">{col("conflicting", "Conflicting", "Different settings").title}</p>
            <p className="stat-value text-red-600">{displayComparison.summary.conflicting_count}</p>
            <p className="text-xs text-zinc-500 mt-1">{col("conflicting", "Conflicting", "Different settings").hint}</p>
          </div>
          <div className="stat-card border-l-4 border-l-emerald-500">
            <p className="label-text">{col("matching", "Matching", "In sync").title}</p>
            <p className="stat-value text-emerald-600">{displayComparison.summary.matching_count}</p>
            <p className="text-xs text-zinc-500 mt-1">{col("matching", "Matching", "In sync").hint}</p>
          </div>
        </div>
      )}

      {/* Comparison Grid */}
      {displayComparison ? (
        <div className="overflow-x-auto pb-2">
        <div className="grid h-[calc(100vh-380px)] min-w-[1280px] grid-cols-4 gap-4">
          <div className="card-base flex min-w-[300px] flex-col overflow-hidden">
            <CategoryColumn
              title={col("tenant_only", "Tenant Only", "Can be removed").title}
              subtitle={col("tenant_only", "Tenant Only", "Can be removed").subtitle}
              icon={Plus}
              items={displayComparison.tenant_only}
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
              diffLabels={diffLabels}
              hideTenantActions={hideTenantActions}
              groupByKind
              comparisonPolicyType={policyType}
              directoryResolution={directoryResolution}
            />
          </div>

          <div className="card-base flex min-w-[300px] flex-col overflow-hidden">
            <CategoryColumn
              title={col("baseline_only", "Baseline Only", "Can be deployed").title}
              subtitle={col("baseline_only", "Baseline Only", "Can be deployed").subtitle}
              icon={FileText}
              items={displayComparison.baseline_only}
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
              diffLabels={diffLabels}
              hideTenantActions={hideTenantActions}
              groupByKind
              comparisonPolicyType={policyType}
              directoryResolution={directoryResolution}
            />
          </div>

          <div className="card-base flex min-w-[300px] flex-col overflow-hidden">
            <CategoryColumn
              title={col("conflicting", "Conflicting", "Different settings").title}
              subtitle={col("conflicting", "Conflicting", "Different settings").subtitle}
              icon={AlertTriangle}
              items={displayComparison.conflicting}
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
              onDeploy={handleDeploy}
              onDelete={handleDelete}
              deployingId={deployingId}
              diffLabels={diffLabels}
              hideTenantActions
              groupByKind
              comparisonPolicyType={policyType}
              directoryResolution={directoryResolution}
            />
          </div>

          <div className="card-base flex min-w-[300px] flex-col overflow-hidden">
            <CategoryColumn
              title={col("matching", "Matching", "In sync").title}
              subtitle={col("matching", "Matching", "In sync").subtitle}
              icon={CheckCircle2}
              items={displayComparison.matching}
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
              onDeploy={handleDeploy}
              onDelete={handleDelete}
              deployingId={deployingId}
              diffLabels={diffLabels}
              hideTenantActions
              groupByKind
              comparisonPolicyType={policyType}
              directoryResolution={directoryResolution}
            />
          </div>
        </div>
        </div>
      ) : (
        <div className="card-base p-12 text-center">
          <GitCompare className="w-16 h-16 mx-auto text-zinc-300 mb-4" />
          <h2 className="text-lg font-semibold text-zinc-700 mb-2">
            Ready to Compare
          </h2>
          <p className="text-sm text-zinc-500 mb-4 max-w-md mx-auto">
            Choose Tenant vs CIS baseline or Tenant vs previous commit, pick snapshots, then run comparison.
          </p>
        </div>
      )}

      {/* Policy Detail Sheet */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader className="border-b border-zinc-200 pb-4">
            <div className="flex items-center justify-between">
              <SheetTitle className="font-heading break-words pr-8 text-left leading-snug">
                {selectedPolicy
                  ? selectedPolicy.category === "directory_dependency"
                    ? selectedPolicy.name
                    : getPolicyDisplayName(selectedPolicy, selectedPolicy.category)
                  : ""}
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
                      <p className="label-text">{diffLabels.current} Policy</p>
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
                      <p className="label-text">{diffLabels.previous} Policy</p>
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
                      {selectedPolicy.category === "tenant_only"
                        ? `${diffLabels.current} Policy`
                        : `${diffLabels.previous} Policy`}
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
                : pendingAction?.type === "deploy_conflicting"
                  ? `This will deploy the ${diffLabels.previous} version to your live tenant for "${getPolicyDisplayName(pendingAction.item, "conflicting")}".`
                  : `This will deploy "${getPolicyDisplayName(pendingAction?.item, "baseline_only")}" to your tenant.`}
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
                : pendingAction?.type === "delete_matching"
                  ? `This will remove the matching policy "${getPolicyDisplayName(pendingAction.item, "matching")}" from your live tenant.`
                  : `This will permanently delete "${getPolicyDisplayName(pendingAction?.item, "tenant_only")}" from your tenant.`}
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
    </TooltipProvider>
  );
};

export default CISComparison;
