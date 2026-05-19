const GUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const CA_RESERVED = new Set([
  "All",
  "None",
  "GuestsOrExternalUsers",
  "ExternalUsers",
]);

export const POLICY_KIND_ORDER = [
  "conditional_access",
  "device_configuration",
  "configuration",
  "compliance",
  "unknown",
];

/** Portal-style breadcrumbs shown on the comparison page folder headers. */
export const POLICY_KIND_LABELS = {
  conditional_access:
    "Entra ID > Security > Conditional Access > Policies",
  device_configuration: "Intune > Devices > Configuration Profiles",
  configuration: "Intune > Apps",
  compliance: "Intune > Devices > Compliance > Policies",
  unknown: "Other",
};

export const POLICY_KIND_FILTER_LABELS = {
  conditional_access: "Entra ID · Security · Conditional Access",
  device_configuration: "Intune · Devices · Configuration Profiles",
  configuration: "Intune · Apps",
  compliance: "Intune · Devices · Compliance",
  unknown: "Other",
};

/** DevOps repo paths under Source/Resources/Content (Simeon Baseline layout). */
export const POLICY_DEVOPS_SCOPES = {
  device_configuration:
    "Source/Resources/Content/MSGraph/DeviceManagement/DeviceConfigurations",
  conditional_access:
    "Source/Resources/Content/MSGraph/Identity/ConditionalAccess/Policies",
  configuration:
    "Source/Resources/Content/MSGraph/DeviceAppManagement/MobileApps",
  compliance:
    "Source/Resources/Content/MSGraph/DeviceManagement/DeviceCompliancePolicies",
};

export const nameFromPolicyObject = (policy) =>
  policy?.displayName || policy?.name || policy?.$friendlyName || null;

export const normalizePolicyCompareName = (name) =>
  (name || "").trim().toLowerCase().replace(/\s+/g, " ");

export const getPolicyCompareKey = (policy) => {
  if (!policy) return null;
  if (policy._backupKind === "assignments") {
    const pid = policy.id;
    return pid ? `assignments:${pid}` : "assignments:unknown";
  }
  const label = nameFromPolicyObject(policy);
  if (label) return `name:${normalizePolicyCompareName(label)}`;
  if (policy.id) return `id:${policy.id}`;
  return null;
};

export const getPolicySourceHint = (policy) => {
  if (!policy || typeof policy !== "object") return null;
  const path =
    policy._cisBaselineSourcePath || policy._devOpsBackupPath || "";
  if (!path) return null;
  const base = path.replace(/\\/g, "/").split("/").pop() || "";
  return base.endsWith(".json") ? base.slice(0, -5) : base || null;
};

export const getPolicySubtitle = (policy, category, item) => {
  const source = getPolicySourceHint(policy);
  const id = policy?.id;
  if (source && id) return `${source}`;
  if (source) return source;
  if (id && typeof id === "string") return id;
  if (item?.name && !String(item.name).startsWith("name:")) return item.name;
  return null;
};

export const getFolderSectionShortLabel = (kind) =>
  POLICY_KIND_FILTER_LABELS[kind] || POLICY_KIND_LABELS[kind] || kind;

export const getDependencyDisplayLines = (doc, objectId, depKind) => {
  const primary =
    doc?.displayName ||
    doc?.userPrincipalName ||
    doc?.mail ||
    doc?.mailNickname ||
    null;
  let secondary = null;
  if (depKind === "user") {
    secondary =
      doc?.userPrincipalName && doc.userPrincipalName !== primary
        ? doc.userPrincipalName
        : doc?.mail && doc.mail !== primary
          ? doc.mail
          : null;
  } else {
    secondary =
      doc?.mail && doc.mail !== primary
        ? doc.mail
        : doc?.description && doc.description !== primary
          ? doc.description
          : null;
  }
  return {
    primary:
      primary ||
      (depKind === "user" ? "User (unresolved)" : "Group (unresolved)"),
    secondary,
    objectId,
  };
};

export const formatDiffValue = (value) => {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

export const getPolicyDisplayName = (item, category) => {
  if (!item) return "this policy";
  let label = null;
  if (category === "tenant_only" || category === "baseline_only") {
    label = nameFromPolicyObject(item.policy);
  } else if (category === "matching" || category === "conflicting") {
    label =
      nameFromPolicyObject(item.tenant) || nameFromPolicyObject(item.baseline);
  }
  if (label) return label;
  if (item.name && !GUID_RE.test(String(item.name))) return item.name;
  return (
    nameFromPolicyObject(item.policy) ||
    nameFromPolicyObject(item.tenant) ||
    nameFromPolicyObject(item.baseline) ||
    item.name ||
    "Unknown"
  );
};

export const getPolicyItemId = (item, category) => {
  if (!item) return undefined;
  if (category === "matching") return item.tenant?.id || item.baseline?.id;
  if (category === "conflicting") return item.tenant?.id || item.baseline?.id;
  return item.policy?.id || item.tenant?.id || item.baseline?.id;
};

export const inferKindFromPath = (path) => {
  const p = (path || "").toLowerCase();
  if (p.includes("/conditionalaccess/") && p.includes("/policies")) {
    return "conditional_access";
  }
  if (p.includes("/deviceconfigurations")) return "device_configuration";
  if (p.includes("/devicecompliancepolicies")) return "compliance";
  if (p.includes("/configurationpolicies") || p.includes("/mobileapps")) {
    return "configuration";
  }
  if (p.includes("/conditional_access/")) return "conditional_access";
  if (p.includes("/device_configuration/")) return "device_configuration";
  if (p.includes("/configuration/")) return "configuration";
  if (p.includes("/compliance/")) return "compliance";
  return null;
};

export const policyKindLabel = (kind, sources) =>
  sources?.policy_kind_labels?.[kind] || POLICY_KIND_LABELS[kind] || kind;

export const detectPolicyType = (policy, selectedType) => {
  if (selectedType && selectedType !== "all") return selectedType;
  const fromPath = inferKindFromPath(
    policy?._devOpsBackupPath || policy?._cisBaselineSourcePath
  );
  if (fromPath) return fromPath;
  const odata = policy?.["@odata.type"] || "";
  if (odata.includes("conditionalAccess")) return "conditional_access";
  if (odata.includes("deviceConfiguration")) return "device_configuration";
  if (odata.includes("compliance")) return "compliance";
  if (
    policy?.conditions &&
    (policy.grantControls !== undefined || policy.sessionControls !== undefined)
  ) {
    return "conditional_access";
  }
  if (policy?.platforms != null && policy?.technologies != null) {
    return "configuration";
  }
  return "configuration";
};

export const inferItemPolicyKind = (item, category, comparisonPolicyType) => {
  if (comparisonPolicyType && comparisonPolicyType !== "all") {
    return comparisonPolicyType;
  }
  const policy = item.policy || item.tenant || item.baseline;
  return detectPolicyType(policy, "all");
};

export const isCaObjectId = (value) => {
  if (typeof value !== "string") return false;
  const v = value.trim();
  if (CA_RESERVED.has(v)) return false;
  return GUID_RE.test(v);
};

export const walkCaUserGroupIds = (obj, users, groups) => {
  if (!obj || typeof obj !== "object") return;
  if (Array.isArray(obj)) {
    obj.forEach((entry) => walkCaUserGroupIds(entry, users, groups));
    return;
  }
  Object.entries(obj).forEach(([key, val]) => {
    if (
      (key === "includeUsers" || key === "excludeUsers") &&
      Array.isArray(val)
    ) {
      val.forEach((id) => {
        if (isCaObjectId(id)) users.add(id.trim());
      });
    } else if (
      (key === "includeGroups" || key === "excludeGroups") &&
      Array.isArray(val)
    ) {
      val.forEach((id) => {
        if (isCaObjectId(id)) groups.add(id.trim());
      });
    } else {
      walkCaUserGroupIds(val, users, groups);
    }
  });
};

export const collectColumnDirectoryRefs = (items, category) => {
  const users = new Set();
  const groups = new Set();
  for (const item of items) {
    if (category === "tenant_only" || category === "baseline_only") {
      walkCaUserGroupIds(item.policy, users, groups);
    } else if (category === "matching" || category === "conflicting") {
      walkCaUserGroupIds(item.tenant, users, groups);
      walkCaUserGroupIds(item.baseline, users, groups);
    }
  }
  return { users: [...users].sort(), groups: [...groups].sort() };
};

export const buildPolicyFolderSections = (items, category, comparisonPolicyType) => {
  const byKind = {};
  for (const item of items) {
    const kind = inferItemPolicyKind(item, category, comparisonPolicyType);
    if (!byKind[kind]) byKind[kind] = [];
    byKind[kind].push(item);
  }
  const sections = [];
  for (const kind of POLICY_KIND_ORDER) {
    const list = byKind[kind];
    if (!list?.length) continue;
    sections.push({
      id: kind,
      label: POLICY_KIND_LABELS[kind] || kind,
      shortLabel: getFolderSectionShortLabel(kind),
      items: list,
    });
  }
  return sections;
};

export const buildFolderSectionsWithDependencies = (
  items,
  category,
  comparisonPolicyType,
  directoryResolution
) => {
  const sections = buildPolicyFolderSections(items, category, comparisonPolicyType);
  const { users, groups } = collectColumnDirectoryRefs(items, category);
  if (users.length) {
    sections.push({
      id: `${category}-deps-users`,
      label: "Dependencies · Users",
      shortLabel: "Users (CA dependencies)",
      dependencyKind: "user",
      dependencyIds: users,
    });
  }
  if (groups.length) {
    sections.push({
      id: `${category}-deps-groups`,
      label: "Dependencies · Groups",
      shortLabel: "Groups (CA dependencies)",
      dependencyKind: "group",
      dependencyIds: groups,
    });
  }
  return { sections, directoryResolution };
};

export const stripDeployMetadata = (policy) => {
  if (!policy || typeof policy !== "object") return policy;
  const out = { ...policy };
  delete out._devOpsBackupPath;
  delete out._cisBaselineSourcePath;
  return out;
};
