export type McpServerStatus = "connected" | "error" | "unknown";

export interface McpToolItem {
  server: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface McpServerItem {
  name: string;
  enabled: boolean;
  status: McpServerStatus;
  error: string | null;
  tool_count: number;
  enabled_count: number;
  tools: McpToolItem[];
}

export interface McpCatalog {
  servers: McpServerItem[];
  server_count: number;
  tool_count: number;
  enabled_count: number;
}