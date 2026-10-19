export type Session = {
  authenticated: boolean;
  user: null | {
    id: number;
    login: string;
    name: string | null;
    avatar_url: string | null;
  };
};

export type DeviceAuthorization = {
  authorization_id: string;
  user_code: string;
  verification_uri: string;
  expires_at: string;
  interval: number;
};

export type DeviceAuthorizationPoll = {
  status: "pending" | "complete";
  retry_after?: number;
  user?: Session["user"];
};

export type Project = {
  project_id: string;
  slug: string;
  name: string;
  release: string;
  generated_filename: string;
  plan_source_branch: string;
  revision: number;
  working_plan_version: number;
  plan_fingerprint?: string | null;
  repository_count?: number;
  step_count?: number;
  updated_at: string;
};

export type PlanChangeProposal = {
  proposal_id: string;
  project_id: string;
  base_working_plan_version: number;
  proposal_version: number;
  status: "awaiting_review" | "applied" | "rejected" | "stale" | "superseded" | "cancelled" | "failed";
  rationale: string;
  proposer_kind: string;
  proposer_id: string;
  client_name: string;
  client_version: string | null;
  draft_plan_fingerprint: string;
  review_digest: string;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_channel: string | null;
  decision_reason: string | null;
  applied_working_plan_version: number | null;
  operations: Array<{
    operation_id: string;
    kind: string;
    [key: string]: unknown;
  }>;
  validation: { valid: boolean; schema_version: number };
  diff: {
    summary: string[];
    affected_step_ids: string[];
    project: Record<string, { before: unknown; after: unknown }>;
    repositories: {
      added: string[];
      removed: string[];
      changed: Array<Record<string, unknown>>;
    };
    steps: {
      added: string[];
      removed: string[];
      changed: Array<Record<string, unknown>>;
    };
  };
  consequences: {
    affected_step_ids: string[];
    context_approvals_invalidated: string[];
    implementation_packages_invalidated: string[];
    local_plan_becomes_unpublished: boolean;
    publication_is_separate: boolean;
  };
  review_markdown: string;
};

export type CloneState = {
  path: string;
  branch: string;
  origin: string;
  dirty: boolean;
  ahead: number;
  behind: number;
};

export type RepositoryCiStatus = {
  branch: string;
  state:
    | "queued"
    | "in_progress"
    | "success"
    | "failure"
    | "cancelled"
    | "skipped"
    | "not_found"
    | "unavailable"
    | "authentication_required";
  workflow_name?: string;
  run_number?: number;
  run_attempt?: number;
  event?: string;
  status?: string;
  conclusion?: string;
  head_sha?: string;
  html_url?: string;
  updated_at?: string;
  completed_at?: string;
  message?: string;
};

export type Repository = {
  repository_id: string;
  project_id: string;
  alias: string;
  github_full_name: string;
  github_default_branch: string | null;
  clone_url: string | null;
  local_path: string | null;
  responsibility: string;
  last_generated_digest: string | null;
  clone_state: CloneState | null;
  clone_error: string | null;
  ci_cd: RepositoryCiStatus;
};

export type GitHubAccount = {
  login: string;
  avatar_url: string | null;
  html_url: string | null;
};

export type GitHubTimelineItem = {
  timeline_id: string;
  event: string;
  actor: GitHubAccount | null;
  body: string | null;
  created_at: string | null;
  html_url: string | null;
  state: string | null;
  commit_sha: string | null;
  commit_message: string | null;
  label: string | null;
  assignee: string | null;
  requested_reviewer: string | null;
  rename_from: string | null;
  rename_to: string | null;
  path: string | null;
  line: number | null;
  diff_hunk: string | null;
};

export type Artifact = {
  artifact_id: string;
  repository_id: string;
  repository_alias: string;
  github_full_name: string;
  github_issue_id: number | null;
  kind: "issue" | "pull_request";
  number: number;
  title: string;
  body: string | null;
  state: "open" | "closed";
  state_reason: "completed" | "not_planned" | null;
  html_url: string;
  author_login: string | null;
  timeline: GitHubTimelineItem[];
  assignees: GitHubAccount[];
  reviewers: GitHubAccount[];
  labels: string[];
  head_branch: string | null;
  base_branch: string | null;
  is_draft: number;
  is_merged: number;
  checks_state: string | null;
  changed_files: Array<{
    filename: string;
    status?: string;
    additions?: number;
    deletions?: number;
    changes?: number;
  }>;
  updated_at: string;
};

export type ContextRole =
  | "must_understand"
  | "expected_to_change"
  | "must_verify"
  | "must_preserve";

export type ContextAuthority =
  | "canonical"
  | "reference"
  | "generated"
  | "compatibility"
  | "deprecated"
  | "historical";

export type ContextSelectorKind =
  | "file"
  | "directory"
  | "glob"
  | "symbol"
  | "protobuf"
  | "json_schema"
  | "markdown_heading"
  | "test_case";

export type ContextObservation = {
  context_id: string;
  observed_commit: string | null;
  blob_hash: string | null;
  selector_status: string;
  status:
    | "current"
    | "changed"
    | "missing"
    | "selector_missing"
    | "repository_unavailable";
  details: Record<string, unknown>;
  observed_at: string;
};

export type ContextResource = {
  context_id: string;
  repository_id: string;
  repository_alias: string;
  github_full_name: string;
  repository_local_path: string | null;
  path: string;
  selector_kind: ContextSelectorKind;
  selector_value: string | null;
  role: ContextRole;
  explanation: string;
  required: boolean;
  authority: ContextAuthority;
  observation: ContextObservation | null;
};

export type VerificationRequirement = {
  verification_id: string;
  kind:
    | "test"
    | "command"
    | "schema"
    | "generated"
    | "typecheck"
    | "lint"
    | "integration"
    | "human";
  repository_id: string | null;
  repository_alias: string | null;
  target: string;
  explanation: string;
  required: boolean;
  observation: VerificationObservation | null;
};

export type VerificationObservation = {
  verification_id: string;
  status: "passed" | "failed" | "skipped" | "unavailable";
  evidence: string;
  output_excerpt: string | null;
  exit_code: number | null;
  observed_commit: string | null;
  recorded_by: string;
  observed_at: string;
  stale: boolean;
};

export type ContextReadiness = {
  state:
    | "ready"
    | "needs_context_review"
    | "missing_required_context"
    | "verification_incomplete"
    | "blocked_by_github"
    | "not_ready";
  ready: boolean;
  reasons: string[];
};

export type ImpactAnalysis = {
  pull_request_artifact_id: string;
  pull_request_number: number;
  changed_files: string[];
  expected_changes: string[];
  unexpected_changes: string[];
  untouched_expected: string[];
  verification_resources_changed: string[];
  preserved_resources_changed: string[];
  verification_results: Array<{
    verification_id: string;
    kind: VerificationRequirement["kind"];
    required: boolean;
    status: VerificationObservation["status"] | "not_recorded" | "stale";
    observed_commit: string | null;
  }>;
  checks_state: string | null;
  analysis_hash: string;
};

export type StepState =
  | "planned"
  | "blocked"
  | "ready"
  | "pending"
  | "in_review"
  | "completed"
  | "cancelled";

export type RepositoryStatusMetrics = Record<StepState, number> & {
  repository_id: string;
  alias: string;
  total: number;
};

export type ProjectStatusSnapshot = {
  snapshot_id: number | null;
  plan_revision: number;
  plan_fingerprint: string | null;
  source: "reconciliation" | "current" | string;
  metrics: Record<StepState, number> & {
    actionable: number;
    findings: number;
    active_blockers: number;
    blocked_milestones: number;
  };
  repository_metrics: RepositoryStatusMetrics[];
  recorded_at: string;
};

export type Step = {
  step_id: string;
  repository_id: string;
  repository_alias: string;
  github_full_name: string;
  release: string;
  title: string;
  required_result: string;
  branch_name: string;
  computed_state: StepState;
  observed_state: StepState | null;
  dependencies_complete: boolean;
  blockers_clear: boolean;
  blockers: Blocker[];
  sort_order: number;
  depends_on: string[];
  artifacts: Artifact[];
  context_resources: ContextResource[];
  verification_requirements: VerificationRequirement[];
  context_approval: null | {
    approval_id: string;
    plan_revision: number;
    approved_by: string;
    approved_at: string;
    snapshot: Record<string, { blob_hash: string | null; observed_commit: string | null }>;
  };
  context_readiness: ContextReadiness;
  impact_analysis: ImpactAnalysis | null;
  risk_assessment: {
    level: "green" | "amber" | "red";
    explanation: string;
    factors: Array<{
      severity: "amber" | "red";
      kind: string;
      explanation: string;
    }>;
  };
  regression_history: Array<{
    blocker_id: string;
    issue: string;
    title: string;
    status: "resolved" | "dismissed";
    url: string;
    corrective_changed_files: string[];
    matching_context_paths: string[];
    original_step_ids: string[];
    review_findings: Array<{ author: string | null; body: string; url: string | null }>;
    context_gaps: string[];
    implementation_package: null | {
      package_id: string;
      package_hash: string;
      created_at: string;
    };
  }>;
};

export type Blocker = Artifact & {
  blocker_id: string;
  status: "active" | "resolved" | "dismissed";
  is_active: boolean;
  blocked_step_ids: string[];
  suggested_branch: string;
  artifacts: Artifact[];
};

export type ReconciliationFinding = {
  kind: string;
  severity: "warning" | "error";
  step_id: string;
  message: string;
};

export type Dashboard = {
  project: Project;
  repositories: Repository[];
  steps: Step[];
  blockers: Blocker[];
  metrics: Record<StepState, number> & {
    actionable: number;
    findings: number;
    active_blockers: number;
    blocked_milestones: number;
  };
  repository_metrics: RepositoryStatusMetrics[];
  history: ProjectStatusSnapshot[];
  findings: ReconciliationFinding[];
};

export type Activity = {
  activity_id: number;
  kind: string;
  summary: string;
  details: Record<string, unknown>;
  actor: string | null;
  created_at: string;
};

export type GitHubRepository = {
  id: number;
  full_name: string;
  private: boolean;
  default_branch: string;
  clone_url: string;
  updated_at: string;
  owner_avatar_url: string;
};

export type ProjectImportInspection = {
  project: {
    slug: string;
    name: string;
    release: string;
    generated_filename: string;
    revision: number;
    plan_source_branch: string | null;
  };
  repositories: number;
  steps: number;
  source_repository: string;
  source_path: string;
  fingerprint: string;
};

export type PlanSource = {
  repository_id: string;
  alias: string;
  github_full_name: string;
  path: string | null;
  source_kind: "worktree" | "remote";
  source_branch: string | null;
  revision: number | null;
  fingerprint: string | null;
  status:
    | "unavailable"
    | "missing"
    | "invalid"
    | "current"
    | "newer"
    | "older"
    | "conflict";
  error: string | null;
};

export type PlanSyncStatus = {
  local_revision: number;
  working_fingerprint: string;
  published_fingerprint: string | null;
  has_local_changes: boolean;
  conflict: boolean;
  recommended_repository_id: string | null;
  sources: PlanSource[];
};
