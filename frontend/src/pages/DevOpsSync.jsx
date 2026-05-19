import { useState, useEffect } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import { 
  GitBranch, 
  RefreshCw,
  CheckCircle2,
  Clock,
  AlertCircle,
  Loader2,
  Trash2,
  Eye,
  Upload,
  X,
  Copy
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
      className="json-viewer whitespace-pre-wrap break-all"
      dangerouslySetInnerHTML={{ __html: syntaxHighlight(data) }}
    />
  );
};

const DevOpsSync = () => {
  const [exports, setExports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(null);
  const [selectedExport, setSelectedExport] = useState(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [exportToDelete, setExportToDelete] = useState(null);
  const [commitMessage, setCommitMessage] = useState("");
  const [syncDialogOpen, setSyncDialogOpen] = useState(false);
  const [exportToSync, setExportToSync] = useState(null);

  const fetchExports = async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/exports`);
      setExports(response.data.exports);
    } catch (err) {
      toast.error("Failed to fetch exports");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchExports();
  }, []);

  const handleViewExport = async (exportId) => {
    try {
      const response = await axios.get(`${API}/exports/${exportId}`);
      setSelectedExport(response.data);
      setSheetOpen(true);
    } catch (err) {
      toast.error("Failed to fetch export details");
    }
  };

  const handleDeleteExport = async () => {
    if (!exportToDelete) return;
    
    try {
      await axios.delete(`${API}/exports/${exportToDelete}`);
      toast.success("Export deleted successfully");
      fetchExports();
    } catch (err) {
      toast.error("Failed to delete export");
    } finally {
      setDeleteDialogOpen(false);
      setExportToDelete(null);
    }
  };

  const openSyncDialog = (exp) => {
    setExportToSync(exp);
    setCommitMessage(`Export ${exp.policy_type} policies - ${new Date().toISOString().split('T')[0]}`);
    setSyncDialogOpen(true);
  };

  const handleSync = async () => {
    if (!exportToSync) return;
    
    try {
      setSyncing(exportToSync.id);
      setSyncDialogOpen(false);
      
      const response = await axios.post(`${API}/devops/sync`, {
        export_id: exportToSync.id,
        commit_message: commitMessage
      });
      
      if (response.data.success) {
        toast.success(response.data.message);
        fetchExports();
      } else {
        toast.error(response.data.message);
      }
    } catch (err) {
      const errorMsg = err.response?.data?.detail || "Failed to sync to DevOps";
      toast.error(errorMsg);
    } finally {
      setSyncing(null);
      setExportToSync(null);
    }
  };

  const copyToClipboard = (data) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success("Copied to clipboard");
  };

  if (loading) {
    return (
      <div data-testid="devops-loading" className="space-y-6">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-9 w-32" />
        </div>
        <Skeleton className="h-96" />
      </div>
    );
  }

  return (
    <div data-testid="devops-page" className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
            DevOps Sync
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Push exported policies to Azure DevOps. Each push updates JSON under{" "}
            <code className="text-xs bg-zinc-100 px-1 rounded">Source/Resources/Content/MSGraph/</code> and removes policy
            backup files that are no longer in that export (for example after you removed policies and
            re-exported). You can push again after a sync, or run a new export on the Policies page after
            changing your tenant.
          </p>
        </div>
        <Button
          data-testid="refresh-exports-btn"
          onClick={fetchExports}
          variant="outline"
          className="gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </Button>
      </div>

      {/* Exports Table */}
      <div className="card-base">
        <div className="card-header">
          <h2 className="font-heading text-sm font-semibold text-zinc-900">
            Export History
          </h2>
          <span className="text-xs text-zinc-500">
            {exports.length} exports
          </span>
        </div>
        
        {exports.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="table-header">
                  <th className="px-4 py-3 text-left">Export ID</th>
                  <th className="px-4 py-3 text-left">Policy Type</th>
                  <th className="px-4 py-3 text-left">Count</th>
                  <th className="px-4 py-3 text-left">Exported At</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {exports.map((exp) => (
                  <tr key={exp.id} className="table-row">
                    <td className="table-cell">
                      <code className="font-mono text-xs bg-zinc-100 px-2 py-1 rounded">
                        {exp.id.substring(0, 8)}...
                      </code>
                    </td>
                    <td className="table-cell capitalize">
                      {exp.policy_type.replace(/_/g, " ")}
                    </td>
                    <td className="table-cell font-mono">
                      {exp.policy_count}
                    </td>
                    <td className="table-cell text-zinc-500 text-sm">
                      {new Date(exp.exported_at).toLocaleString()}
                    </td>
                    <td className="table-cell">
                      {exp.synced_to_devops ? (
                        <span className="badge-success inline-flex items-center gap-1">
                          <CheckCircle2 className="w-3 h-3" />
                          Synced
                        </span>
                      ) : (
                        <span className="badge-warning inline-flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          Pending
                        </span>
                      )}
                    </td>
                    <td className="table-cell">
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          data-testid={`view-export-${exp.id.substring(0, 8)}-btn`}
                          variant="ghost"
                          size="sm"
                          onClick={() => handleViewExport(exp.id)}
                          className="h-8 w-8 p-0"
                        >
                          <Eye className="w-4 h-4" />
                        </Button>
                        
                        <Button
                          data-testid={`sync-export-${exp.id.substring(0, 8)}-btn`}
                          variant="outline"
                          size="sm"
                          onClick={() => openSyncDialog(exp)}
                          disabled={syncing === exp.id}
                          title={
                            exp.synced_to_devops
                              ? "Re-run DevOps backup for this export (commits only if files differ)"
                              : "Push this export to Azure DevOps"
                          }
                          className="gap-1 text-[#0052CC] border-[#0052CC] hover:bg-[#0052CC]/10"
                        >
                          {syncing === exp.id ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Upload className="w-4 h-4" />
                          )}
                          {exp.synced_to_devops ? "Push again" : "Push"}
                        </Button>
                        
                        {exp.synced_to_devops && exp.devops_commit_id && (
                          <code className="font-mono text-xs text-zinc-500">
                            {exp.devops_commit_id.substring(0, 7)}
                          </code>
                        )}
                        
                        <Button
                          data-testid={`delete-export-${exp.id.substring(0, 8)}-btn`}
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setExportToDelete(exp.id);
                            setDeleteDialogOpen(true);
                          }}
                          className="h-8 w-8 p-0 text-red-600 hover:text-red-700 hover:bg-red-50"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-8 text-center">
            <GitBranch className="w-12 h-12 mx-auto text-zinc-300 mb-3" />
            <p className="text-sm text-zinc-500">No exports yet</p>
            <p className="text-xs text-zinc-400 mt-1">
              Export policies from the Policies page to see them here
            </p>
          </div>
        )}
      </div>

      {/* Export Detail Sheet */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader className="border-b border-zinc-200 pb-4">
            <div className="flex items-center justify-between">
              <SheetTitle className="font-heading">
                Export Details
              </SheetTitle>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (!selectedExport) return;
                    openSyncDialog(selectedExport);
                    setSheetOpen(false);
                  }}
                  disabled={!selectedExport || syncing === selectedExport?.id}
                  className="gap-2 text-[#0052CC] border-[#0052CC] hover:bg-[#0052CC]/10"
                >
                  <Upload className="w-4 h-4" />
                  {selectedExport?.synced_to_devops ? "Push again" : "Push"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => copyToClipboard(selectedExport?.policies)}
                  className="gap-2"
                >
                  <Copy className="w-4 h-4" />
                  Copy JSON
                </Button>
              </div>
            </div>
          </SheetHeader>
          
          {selectedExport && (
            <div className="py-4 space-y-4">
              {/* Export Summary */}
              <div className="grid grid-cols-2 gap-4">
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Export ID</p>
                  <p className="font-mono text-xs mt-1 text-zinc-700">
                    {selectedExport.id}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Policy Type</p>
                  <p className="text-sm mt-1 text-zinc-700 capitalize">
                    {selectedExport.policy_type?.replace(/_/g, " ")}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Policy Count</p>
                  <p className="text-lg font-semibold mt-1 text-zinc-900 font-heading">
                    {selectedExport.policy_count}
                  </p>
                </div>
                <div className="p-3 bg-zinc-50 rounded-sm">
                  <p className="label-text">Sync Status</p>
                  <div className="mt-1">
                    {selectedExport.synced_to_devops ? (
                      <span className="badge-success inline-flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" />
                        Synced
                      </span>
                    ) : (
                      <span className="badge-warning inline-flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        Pending
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* JSON Preview */}
              <div>
                <p className="label-text mb-2">Policies JSON</p>
                <ScrollArea className="h-[calc(100vh-380px)]">
                  <JsonViewer data={selectedExport.policies} />
                </ScrollArea>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>

      {/* Sync Dialog */}
      <AlertDialog open={syncDialogOpen} onOpenChange={setSyncDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="font-heading">
              Push to Azure DevOps
            </AlertDialogTitle>
            <AlertDialogDescription>
              {exportToSync?.synced_to_devops
                ? "This export was already synced. A new commit is created only if your backup files differ from the repo (for example after you ran a fresh export)."
                : "This will push the exported policies to your Azure DevOps repository."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="py-4">
            <label className="label-text block mb-2">Commit Message</label>
            <Input
              data-testid="commit-message-input"
              value={commitMessage}
              onChange={(e) => setCommitMessage(e.target.value)}
              placeholder="Enter commit message..."
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-sync-btn"
              onClick={handleSync}
              className="bg-[#0052CC] hover:bg-[#0043A6]"
            >
              Push to DevOps
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="font-heading">
              Delete Export
            </AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this export? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-delete-btn"
              onClick={handleDeleteExport}
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

export default DevOpsSync;
