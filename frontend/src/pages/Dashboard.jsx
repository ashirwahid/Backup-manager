import { useState, useEffect, useRef, useCallback } from "react";
import { API } from "@/App";
import axios from "axios";
import { toast } from "sonner";
import {
  FileText,
  ShieldCheck,
  Smartphone,
  RefreshCw,
  GitBranch,
  CheckCircle2,
  AlertCircle,
  Clock,
  ArrowRight,
  Upload,
  Loader2,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useNavigate, useLocation } from "react-router-dom";
import { executePendingOperations } from "@/lib/deploymentOperations";

const formatPolicyType = (type) => (type || "").replace(/_/g, " ");

const formatActionLabel = (action) =>
  action === "delete" ? "Remove" : "Deploy";

const Dashboard = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const pendingFromNav = location.state?.pendingOperations;

  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(() => !pendingFromNav?.length);
  const [error, setError] = useState(null);
  const operationsStartedRef = useRef(false);
  const [sessionDeployments, setSessionDeployments] = useState(() =>
    pendingFromNav?.length
      ? pendingFromNav.map((row) => ({ ...row, status: row.status || "loading" }))
      : []
  );

  const fetchStats = useCallback(async ({ silent = false } = {}) => {
    try {
      if (!silent) setLoading(true);
      const response = await axios.get(`${API}/dashboard/stats`);
      setStats(response.data);
      setError(null);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to fetch dashboard stats");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats({ silent: Boolean(pendingFromNav?.length) });
  }, [fetchStats, pendingFromNav?.length]);

  useEffect(() => {
    if (!pendingFromNav?.length || operationsStartedRef.current) return;
    operationsStartedRef.current = true;
    window.history.replaceState({}, document.title);

    const initial = sessionDeployments.length
      ? sessionDeployments
      : pendingFromNav.map((row) => ({ ...row, status: row.status || "loading" }));

    const updateRow = (id, patch) => {
      setSessionDeployments((prev) =>
        prev.map((row) => (row.id === id ? { ...row, ...patch } : row))
      );
    };

    executePendingOperations(initial, updateRow).then(() => {
      fetchStats({ silent: true }).finally(() => setLoading(false));
      setSessionDeployments((prev) => {
        const successCount = prev.filter((r) => r.status === "success").length;
        const errorCount = prev.filter((r) => r.status === "error").length;
        if (successCount > 0) {
          toast.success(
            successCount === 1
              ? "Operation completed"
              : `${successCount} operations completed`
          );
        }
        if (errorCount > 0) {
          toast.error(
            errorCount === 1
              ? "An operation failed"
              : `${errorCount} operations failed`
          );
        }
        return prev;
      });
    });
  }, [pendingFromNav, fetchStats]);

  const statCards = [
    {
      label: "Total Exports",
      value: stats?.total_exports || 0,
      icon: FileText,
      color: "text-zinc-600",
    },
    {
      label: "Synced to DevOps",
      value: stats?.synced_exports || 0,
      icon: GitBranch,
      color: "text-emerald-600",
    },
    {
      label: "Pending Sync",
      value: stats?.pending_sync || 0,
      icon: Clock,
      color: "text-amber-600",
    },
  ];

  const policyTypes = [
    { key: "device_configuration", label: "Device Configuration", icon: Smartphone },
    { key: "configuration", label: "Configuration (Settings Catalog)", icon: FileText },
    { key: "conditional_access", label: "Conditional Access", icon: ShieldCheck },
    { key: "compliance", label: "Compliance", icon: CheckCircle2 },
  ];

  const hasActiveSession = sessionDeployments.length > 0;
  const deploymentRows = hasActiveSession
    ? sessionDeployments
    : stats?.recent_deployments || [];

  const showPageSkeleton = loading && !hasActiveSession;

  const renderStatusBadge = (dep) => {
    if (dep.status === "loading") {
      return (
        <span className="badge-warning inline-flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" />
          In progress
        </span>
      );
    }
    if (dep.status === "error") {
      return (
        <span
          className="inline-flex items-center gap-1 rounded-sm bg-red-50 text-red-800 text-xs font-medium px-2 py-0.5"
          title={dep.error}
        >
          <AlertCircle className="w-3 h-3 shrink-0" />
          Failed
        </span>
      );
    }
    if (dep.action === "delete") {
      return (
        <span className="inline-flex items-center gap-1 rounded-sm bg-zinc-100 text-zinc-700 text-xs font-medium px-2 py-0.5">
          <Trash2 className="w-3 h-3" />
          Removed
        </span>
      );
    }
    return (
      <span className="badge-success inline-flex items-center gap-1">
        <CheckCircle2 className="w-3 h-3" />
        {dep.status || "success"}
      </span>
    );
  };

  if (showPageSkeleton) {
    return (
      <div data-testid="dashboard-loading" className="space-y-6">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-9 w-32" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div data-testid="dashboard" className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold text-zinc-900 tracking-tight">
            Dashboard
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Overview of your policy exports and sync status
          </p>
        </div>
        <Button
          data-testid="refresh-stats-btn"
          onClick={fetchStats}
          variant="outline"
          className="gap-2"
          disabled={sessionDeployments.some((r) => r.status === "loading")}
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-sm p-4 text-sm text-red-800">
          {error}
        </div>
      )}

      {hasActiveSession && sessionDeployments.some((r) => r.status === "loading") && (
        <div
          data-testid="deployment-in-progress-banner"
          className="bg-blue-50 border border-blue-200 rounded-sm p-4"
        >
          <div className="flex items-start gap-3">
            <Loader2 className="w-5 h-5 text-blue-600 animate-spin shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-blue-900">
                Deployment in progress
              </p>
              <p className="text-sm text-blue-800 mt-1">
                Track status in the recent deployments table below.
              </p>
            </div>
          </div>
        </div>
      )}

      {(!stats?.azure_configured || !stats?.devops_configured) && (
        <div
          data-testid="config-warning"
          className="bg-amber-50 border border-amber-200 rounded-sm p-4 flex items-start gap-3"
        >
          <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-amber-800">Configuration Required</p>
            <p className="text-sm text-amber-700 mt-1">
              {!stats?.azure_configured && "Azure AD credentials are not configured. "}
              {!stats?.devops_configured && "Azure DevOps credentials are not configured. "}
              <button
                onClick={() => navigate("/settings")}
                className="underline font-medium hover:text-amber-900"
              >
                Go to Settings
              </button>
            </p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {statCards.map((stat) => (
          <div key={stat.label} className="stat-card">
            <div className="flex items-center justify-between">
              <div>
                <p className="label-text">{stat.label}</p>
                <p className="stat-value mt-2">{stat.value}</p>
              </div>
              <div
                className={`w-10 h-10 rounded-sm bg-zinc-50 flex items-center justify-center ${stat.color}`}
              >
                <stat.icon className="w-5 h-5" strokeWidth={1.5} />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="card-base">
        <div className="card-header">
          <h2 className="font-heading text-sm font-semibold text-zinc-900 flex items-center gap-2">
            <Upload className="w-4 h-4 text-emerald-600" />
            Recent deployments
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/cis-comparison")}
            className="text-zinc-600"
          >
            Compare policies
          </Button>
        </div>
        <div className="overflow-x-auto">
          {deploymentRows.length > 0 ? (
            <table className="w-full">
              <thead>
                <tr className="table-header">
                  <th className="px-4 py-3 text-left">Policy name</th>
                  <th className="px-4 py-3 text-left">Action</th>
                  <th className="px-4 py-3 text-left">Type</th>
                  <th className="px-4 py-3 text-left">Policy ID</th>
                  <th className="px-4 py-3 text-left">Time</th>
                  <th className="px-4 py-3 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {deploymentRows.map((dep, idx) => (
                  <tr
                    key={dep.id || dep.created_policy_id || idx}
                    data-testid={
                      dep.status === "loading" ? "deployment-row-loading" : undefined
                    }
                    className={`table-row ${
                      dep.status === "loading"
                        ? "bg-blue-50/40"
                        : dep.status === "success" && hasActiveSession
                          ? "bg-emerald-50/40"
                          : dep.status === "error"
                            ? "bg-red-50/30"
                            : ""
                    }`}
                  >
                    <td className="table-cell font-medium text-zinc-900">
                      {dep.policy_name}
                    </td>
                    <td className="table-cell text-sm text-zinc-600">
                      {formatActionLabel(dep.action || "deploy")}
                    </td>
                    <td className="table-cell capitalize">
                      {formatPolicyType(dep.policy_type)}
                    </td>
                    <td className="table-cell">
                      {dep.created_policy_id ? (
                        <code className="font-mono text-xs bg-zinc-100 px-2 py-1 rounded">
                          {dep.created_policy_id.substring(0, 8)}…
                        </code>
                      ) : (
                        <span className="text-zinc-400">—</span>
                      )}
                    </td>
                    <td className="table-cell text-zinc-500 text-sm">
                      {dep.deployed_at
                        ? new Date(dep.deployed_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="table-cell">{renderStatusBadge(dep)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="p-8 text-center">
              <Upload className="w-12 h-12 mx-auto text-zinc-300 mb-3" />
              <p className="text-sm text-zinc-500">No deployments yet</p>
              <Button
                onClick={() => navigate("/cis-comparison")}
                className="mt-4 bg-[#0052CC] hover:bg-[#0043A6]"
              >
                Run CIS comparison
              </Button>
            </div>
          )}
        </div>
      </div>

      <div className="card-base">
        <div className="card-header">
          <h2 className="font-heading text-sm font-semibold text-zinc-900">
            Export by Policy Type
          </h2>
          <Button
            data-testid="export-all-btn"
            onClick={() => navigate("/policies")}
            size="sm"
            className="gap-2 bg-[#0052CC] hover:bg-[#0043A6]"
          >
            Export Policies
            <ArrowRight className="w-4 h-4" />
          </Button>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {policyTypes.map((type) => (
              <div
                key={type.key}
                className="p-4 border border-zinc-200 rounded-sm hover:border-zinc-300 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-sm bg-zinc-100 flex items-center justify-center">
                    <type.icon className="w-4 h-4 text-zinc-600" strokeWidth={1.5} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-zinc-500 truncate">{type.label}</p>
                    <p className="text-lg font-semibold text-zinc-900 font-heading">
                      {stats?.policy_type_counts?.[type.key] || 0}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card-base">
        <div className="card-header">
          <h2 className="font-heading text-sm font-semibold text-zinc-900">
            Recent Exports
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/devops")}
            className="text-zinc-600"
          >
            View All
          </Button>
        </div>
        <div className="overflow-x-auto">
          {stats?.recent_exports?.length > 0 ? (
            <table className="w-full">
              <thead>
                <tr className="table-header">
                  <th className="px-4 py-3 text-left">Export ID</th>
                  <th className="px-4 py-3 text-left">Policy Type</th>
                  <th className="px-4 py-3 text-left">Count</th>
                  <th className="px-4 py-3 text-left">Exported At</th>
                  <th className="px-4 py-3 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {stats.recent_exports.map((exp) => (
                  <tr key={exp.id} className="table-row">
                    <td className="table-cell">
                      <code className="font-mono text-xs bg-zinc-100 px-2 py-1 rounded">
                        {exp.id.substring(0, 8)}...
                      </code>
                    </td>
                    <td className="table-cell capitalize">
                      {exp.policy_type.replace(/_/g, " ")}
                    </td>
                    <td className="table-cell font-mono">{exp.policy_count}</td>
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
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="p-8 text-center">
              <FileText className="w-12 h-12 mx-auto text-zinc-300 mb-3" />
              <p className="text-sm text-zinc-500">No exports yet</p>
              <Button
                data-testid="first-export-btn"
                onClick={() => navigate("/policies")}
                className="mt-4 bg-[#0052CC] hover:bg-[#0043A6]"
              >
                Create First Export
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
