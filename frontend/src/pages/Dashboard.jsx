import { useState, useEffect } from "react";
import { API } from "@/App";
import axios from "axios";
import { 
  FileText, 
  ShieldCheck, 
  Smartphone, 
  RefreshCw,
  GitBranch,
  CheckCircle2,
  AlertCircle,
  Clock,
  ArrowRight
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useNavigate } from "react-router-dom";

const Dashboard = () => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  const fetchStats = async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API}/dashboard/stats`);
      setStats(response.data);
      setError(null);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to fetch dashboard stats");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  const statCards = [
    {
      label: "Total Exports",
      value: stats?.total_exports || 0,
      icon: FileText,
      color: "text-zinc-600"
    },
    {
      label: "Synced to DevOps",
      value: stats?.synced_exports || 0,
      icon: GitBranch,
      color: "text-emerald-600"
    },
    {
      label: "Pending Sync",
      value: stats?.pending_sync || 0,
      icon: Clock,
      color: "text-amber-600"
    }
  ];

  const policyTypes = [
    { key: "device_configuration", label: "Device Configuration", icon: Smartphone },
    { key: "configuration", label: "Configuration (Settings Catalog)", icon: FileText },
    { key: "conditional_access", label: "Conditional Access", icon: ShieldCheck },
    { key: "compliance", label: "Compliance", icon: CheckCircle2 }
  ];

  if (loading) {
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
      {/* Header */}
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
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </Button>
      </div>

      {/* Configuration Status */}
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

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {statCards.map((stat) => (
          <div key={stat.label} className="stat-card">
            <div className="flex items-center justify-between">
              <div>
                <p className="label-text">{stat.label}</p>
                <p className="stat-value mt-2">{stat.value}</p>
              </div>
              <div className={`w-10 h-10 rounded-sm bg-zinc-50 flex items-center justify-center ${stat.color}`}>
                <stat.icon className="w-5 h-5" strokeWidth={1.5} />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Policy Type Breakdown */}
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

      {/* Recent Exports */}
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
