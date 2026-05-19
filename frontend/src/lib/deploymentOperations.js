import { API } from "@/App";
import axios from "axios";
import {
  detectPolicyType,
  getPolicyDisplayName,
  stripDeployMetadata,
} from "@/lib/cisComparisonUtils";

const newId = () =>
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function buildPendingOperationsFromDeployAction(
  pendingAction,
  policyType,
  devopsCommitId
) {
  if (!pendingAction) return [];
  const deployedAt = new Date().toISOString();

  if (
    pendingAction.type === "deploy_single" ||
    pendingAction.type === "deploy_conflicting"
  ) {
    const item = pendingAction.item;
    const isConflict = pendingAction.type === "deploy_conflicting";
    const policy = stripDeployMetadata(isConflict ? item.baseline : item.policy);
    const category = isConflict ? "conflicting" : "baseline_only";
    const displayName = getPolicyDisplayName(item, category);
    const detectedType = detectPolicyType(policy, policyType);

    return [
      {
        id: newId(),
        action: "deploy",
        policy_name: displayName,
        policy_type: detectedType,
        status: "loading",
        deployed_at: deployedAt,
        request: {
          kind: "deploy_single",
          policy,
          policy_type: detectedType,
          devops_backup_path:
            policy._devOpsBackupPath || policy._cisBaselineSourcePath || undefined,
          devops_commit_id: devopsCommitId,
        },
      },
    ];
  }

  if (pendingAction.type === "deploy_bulk") {
    const items = pendingAction.items || [];
    const first = items[0]?.policy;
    const detectedType = first ? detectPolicyType(first, policyType) : policyType;
    const groupId = newId();
    const policies = items.map((i) => stripDeployMetadata(i.policy));

    return items.map((item) => ({
      id: newId(),
      groupId,
      action: "deploy",
      policy_name: getPolicyDisplayName(item, "baseline_only"),
      policy_type: detectedType,
      status: "loading",
      deployed_at: deployedAt,
      request: {
        kind: "deploy_bulk",
        groupId,
        policies,
        policy_type: detectedType,
        devops_commit_id: devopsCommitId,
      },
    }));
  }

  return [];
}

export function buildPendingOperationsFromDeleteAction(pendingAction, policyType) {
  if (!pendingAction) return [];
  const deployedAt = new Date().toISOString();

  if (
    pendingAction.type === "delete_single" ||
    pendingAction.type === "delete_matching"
  ) {
    const item = pendingAction.item;
    const isMatching = pendingAction.type === "delete_matching";
    const livePolicy = isMatching ? item.tenant : item.policy;
    const policyId = livePolicy?.id;
    if (!policyId) return [];

    const detectedType = detectPolicyType(livePolicy, policyType);
    const displayName = getPolicyDisplayName(
      item,
      isMatching ? "matching" : "tenant_only"
    );

    return [
      {
        id: newId(),
        action: "delete",
        policy_name: displayName,
        policy_type: detectedType,
        status: "loading",
        deployed_at: deployedAt,
        request: {
          kind: "delete_single",
          policy_id: policyId,
          policy_type: detectedType,
          devops_backup_path: livePolicy?._devOpsBackupPath || undefined,
        },
      },
    ];
  }

  if (pendingAction.type === "delete_bulk") {
    const items = pendingAction.items || [];
    let detectedType = policyType;
    if (policyType === "all") {
      detectedType = "configuration";
    }
    const groupId = newId();
    const policyIds = items.map((i) => i.policy?.id).filter(Boolean);

    return items
      .filter((i) => i.policy?.id)
      .map((item) => ({
        id: newId(),
        groupId,
        action: "delete",
        policy_name: getPolicyDisplayName(item, "tenant_only"),
        policy_type: detectedType,
        status: "loading",
        deployed_at: deployedAt,
        request: {
          kind: "delete_bulk",
          groupId,
          policy_ids: policyIds,
          policy_type: detectedType,
        },
      }));
  }

  return [];
}

async function runDeploySingle(request) {
  const response = await axios.post(`${API}/deploy/policy`, {
    policy: request.policy,
    policy_type: request.policy_type,
    devops_backup_path: request.devops_backup_path,
    devops_commit_id: request.devops_commit_id,
  });
  if (!response.data?.success) {
    throw new Error(response.data?.message || "Deploy failed");
  }
  return {
    status: "success",
    created_policy_id: response.data.created_policy?.id,
    deployed_at: new Date().toISOString(),
  };
}

async function runDeployBulk(request) {
  const response = await axios.post(`${API}/deploy/bulk`, {
    policies: request.policies,
    policy_type: request.policy_type,
    devops_commit_id: request.devops_commit_id,
  });
  return response.data?.results || [];
}

async function runDeleteSingle(request) {
  const response = await axios.delete(`${API}/deploy/policy`, {
    data: {
      policy_id: request.policy_id,
      policy_type: request.policy_type,
      devops_backup_path: request.devops_backup_path,
    },
  });
  if (!response.data?.success) {
    throw new Error(response.data?.message || "Remove failed");
  }
  return {
    status: "success",
    deployed_at: new Date().toISOString(),
  };
}

async function runDeleteBulk(request) {
  const response = await axios.post(`${API}/deploy/bulk-delete`, {
    policy_ids: request.policy_ids,
    policy_type: request.policy_type,
  });
  if (!response.data?.success && !response.data?.results) {
    throw new Error(response.data?.message || "Bulk remove failed");
  }
  return response.data;
}

function errorMessage(err) {
  const detail = err.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail) return JSON.stringify(detail);
  return err.message || "Operation failed";
}

/**
 * Run pending deploy/delete operations; calls onRowUpdate(id, patch) per row.
 */
export async function executePendingOperations(operations, onRowUpdate) {
  const handledDeployBulk = new Set();
  const handledDeleteBulk = new Set();

  for (const op of operations) {
    const { request } = op;
    if (!request) continue;

    try {
      if (request.kind === "deploy_single") {
        const patch = await runDeploySingle(request);
        onRowUpdate(op.id, patch);
        continue;
      }

      if (request.kind === "deploy_bulk") {
        if (handledDeployBulk.has(request.groupId)) continue;
        handledDeployBulk.add(request.groupId);
        const groupOps = operations.filter(
          (r) => r.request?.kind === "deploy_bulk" && r.request.groupId === request.groupId
        );
        const results = await runDeployBulk(request);
        groupOps.forEach((row, idx) => {
          const result = results[idx];
          if (result?.success) {
            onRowUpdate(row.id, {
              status: "success",
              created_policy_id: result.created_id,
              deployed_at: new Date().toISOString(),
            });
          } else {
            onRowUpdate(row.id, {
              status: "error",
              error: result?.error || "Deploy failed",
            });
          }
        });
        continue;
      }

      if (request.kind === "delete_single") {
        const patch = await runDeleteSingle(request);
        onRowUpdate(op.id, patch);
        continue;
      }

      if (request.kind === "delete_bulk") {
        if (handledDeleteBulk.has(request.groupId)) continue;
        handledDeleteBulk.add(request.groupId);
        const groupOps = operations.filter(
          (r) => r.request?.kind === "delete_bulk" && r.request.groupId === request.groupId
        );
        await runDeleteBulk(request);
        groupOps.forEach((row) => {
          onRowUpdate(row.id, {
            status: "success",
            deployed_at: new Date().toISOString(),
          });
        });
      }
    } catch (err) {
      onRowUpdate(op.id, {
        status: "error",
        error: errorMessage(err),
      });
    }
  }
}
