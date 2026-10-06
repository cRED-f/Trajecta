export type MessageRole =
  | "user"
  | "assistant"
  | "system"
  | "tool";

export type MessageStatus =
  | "pending"
  | "streaming"
  | "complete"
  | "error"
  | "cancelled";

export type AttachmentKind =
  | "image"
  | "pdf"
  | "docx"
  | "text"
  | "document"
  | "other";

export type AttachmentStatus =
  | "processing"
  | "ready"
  | "error";

export interface Conversation {
  id: string;
  thread_id: string;
  title: string | null;
  model: string;
  active_branch_id: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ChatBranch {
  id: string;
  conversation_id: string;
  thread_id: string;
  parent_branch_id: string | null;
  fork_message_id: string | null;
  fork_checkpoint_id: string | null;
  head_checkpoint_id: string | null;
  label: string | null;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  parent_message_id: string | null;
  revision_of: string | null;
  base_checkpoint_id: string | null;
  checkpoint_id: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface Attachment {
  id: string;
  conversation_id: string;
  message_id: string | null;
  filename: string;
  mime_type: string | null;
  kind: AttachmentKind;
  virtual_path: string;
  extracted_virtual_path: string | null;
  size_bytes: number;
  sha256: string;
  status: AttachmentStatus;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ConversationDetail extends Conversation {
  branch: ChatBranch | null;
  messages: ChatMessage[];
  attachments: Attachment[];
}

export interface ModelInfo {
  id: string;
  provider: string | null;
  source: string;
  owned_by: string | null;
  metadata: Record<string, unknown>;
}

export interface ModelCatalog {
  default_model: string;
  models: ModelInfo[];
  gateway_reachable: boolean;
}

export type ApprovalDecisionType =
  | "approve"
  | "edit"
  | "reject"
  | "respond";

export interface ApprovalActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

export interface ApprovalReviewConfig {
  action_name: string;
  allowed_decisions: ApprovalDecisionType[];
  args_schema?: Record<string, unknown>;
}

export interface ApprovalInterruptData {
  action_requests?: ApprovalActionRequest[];
  review_configs?: ApprovalReviewConfig[];
  interrupts?: ApprovalInterruptData[];
}

export interface PendingApproval {
  id: string;
  conversation_id: string;
  branch_id: string;
  thread_id: string;
  checkpoint_id: string;
  user_message_id: string;
  model_name: string;
  interrupt_data: ApprovalInterruptData;
  partial_text: string;
  created_at: string;
  updated_at: string;
}

export interface ApprovalDecision {
  type: ApprovalDecisionType;
  message?: string;

  edited_action?: {
    name: string;
    args: Record<string, unknown>;
  };
}

export interface ChatStreamEvent {
  type: string;
  conversation_id: string;
  run_id: string;
  data: Record<string, unknown>;
}

export interface ToolActivity {
  key: string;
  id?: string;
  name?: string;
  args: string;
  result?: string;
  status?: string;
  source?: string;
}

export interface StreamState {
  running: boolean;
  runId: string | null;
  text: string;
  tools: ToolActivity[];
  steps: string[];
  warnings: string[];
  error: string | null;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface SavedMemory {
  id: string;
  tier: string;
  namespace: string;
  key: string;
  content: string;
  created_at: string;
  updated_at: string;
}