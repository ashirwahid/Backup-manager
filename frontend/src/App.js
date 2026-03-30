import { useState } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { 
  LayoutDashboard, 
  FileText, 
  ShieldCheck, 
  Smartphone,
  RefreshCw,
  Settings,
  GitBranch,
  Cloud,
  GitCompare
} from "lucide-react";

// Pages
import Dashboard from "@/pages/Dashboard";
import Policies from "@/pages/Policies";
import DevOpsSync from "@/pages/DevOpsSync";
import SettingsPage from "@/pages/Settings";
import CISComparison from "@/pages/CISComparison";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

// Sidebar Navigation
const Sidebar = () => {
  const navItems = [
    { path: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
    { path: "/policies", icon: FileText, label: "Policies" },
    { path: "/cis-comparison", icon: GitCompare, label: "CIS Comparison" },
    { path: "/devops", icon: GitBranch, label: "DevOps Sync" },
    { path: "/settings", icon: Settings, label: "Settings" },
  ];

  return (
    <aside className="w-64 bg-white border-r border-zinc-200 h-screen fixed left-0 top-0 z-50 flex flex-col">
      {/* Logo */}
      <div className="px-6 py-5 border-b border-zinc-200">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-sm bg-[#0052CC] flex items-center justify-center">
            <Cloud className="w-5 h-5 text-white" strokeWidth={1.5} />
          </div>
          <div>
            <h1 className="font-heading font-semibold text-zinc-900 text-sm tracking-tight">
              Policy Manager
            </h1>
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-medium">
              MS Graph + DevOps
            </p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            data-testid={`nav-${item.label.toLowerCase().replace(' ', '-')}`}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-sm transition-colors ${
                isActive
                  ? "bg-zinc-100 text-zinc-900"
                  : "text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900"
              }`
            }
          >
            <item.icon className="w-[18px] h-[18px]" strokeWidth={1.5} />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-4 border-t border-zinc-200">
        <p className="text-[10px] text-zinc-400 uppercase tracking-widest">
          Intune Policy Export
        </p>
      </div>
    </aside>
  );
};

function App() {
  return (
    <div className="App bg-[#FAFAFA] min-h-screen">
      <BrowserRouter>
        <Sidebar />
        <main className="ml-64 min-h-screen">
          <div className="max-w-7xl mx-auto p-6 md:p-8">
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/policies" element={<Policies />} />
              <Route path="/cis-comparison" element={<CISComparison />} />
              <Route path="/devops" element={<DevOpsSync />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </div>
        </main>
        <Toaster position="top-right" richColors />
      </BrowserRouter>
    </div>
  );
}

export default App;
