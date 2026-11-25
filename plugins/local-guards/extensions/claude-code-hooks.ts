import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import type { ExtensionAPI, ExtensionContext, ToolCallEvent } from "@earendil-works/pi-coding-agent";

const hooksDir = join(dirname(fileURLToPath(import.meta.url)), "..", "scripts");
const hookTimeout = 120_000;

type HookResult = {
  stdout: string;
  stderr: string;
  code: number;
  timedOut: boolean;
};

export function runHook(name: string, payload: unknown, cwd: string, timeoutMs = hookTimeout): Promise<HookResult> {
  return new Promise((resolve) => {
    const child = spawn("bash", [join(hooksDir, name)], {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
      detached: process.platform !== "win32",
    });
    const kill = (signal: NodeJS.Signals) => {
      child.kill(signal);
      if (process.platform !== "win32" && child.pid) {
        try {
          process.kill(-child.pid, signal);
        } catch (error) {
          stderr += error instanceof Error ? error.message : String(error);
        }
      }
    };
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const timer = setTimeout(() => {
      timedOut = true;
      kill("SIGTERM");
      setTimeout(() => kill("SIGKILL"), 1_000);
      finish(1);
    }, timeoutMs);
    const finish = (code: number) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ stdout, stderr, code, timedOut });
    };
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", (error: Error) => {
      stderr += error.message;
      finish(1);
    });
    child.on("close", (code) => {
      finish(code ?? 1);
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

function denied(result: HookResult): string | undefined {
  if (!result.stdout.trim()) return undefined;
  try {
    const parsed = JSON.parse(result.stdout) as {
      hookSpecificOutput?: { permissionDecision?: string; permissionDecisionReason?: string };
    };
    if (parsed.hookSpecificOutput?.permissionDecision === "deny") {
      return parsed.hookSpecificOutput.permissionDecisionReason || "local-guards denied the command";
    }
  } catch {
    return undefined;
  }
  return undefined;
}

function hookFailure(name: string, result: HookResult): string | undefined {
  if (result.code === 0 && !result.timedOut) return undefined;
  const detail = result.stderr.trim() || (result.timedOut ? "timed out" : `exited ${result.code}`);
  return `${name} failed: ${detail}`;
}

async function runBashGuards(event: ToolCallEvent, ctx: ExtensionContext): Promise<{ block: true; reason: string } | undefined> {
  if (event.toolName !== "bash") return undefined;
  const payload = { cwd: ctx.cwd, tool_input: event.input };
  const denyResult = await runHook("deny-git-push.sh", payload, ctx.cwd);
  const denyReason = denied(denyResult);
  if (denyReason) return { block: true, reason: denyReason };
  const denyFailure = hookFailure("deny-git-push.sh", denyResult);
  if (denyFailure) return { block: true, reason: denyFailure };
  const backupResult = await runHook("backup-ref.sh", payload, ctx.cwd);
  const backupReason = denied(backupResult);
  if (backupReason) return { block: true, reason: backupReason };
  const backupFailure = hookFailure("backup-ref.sh", backupResult);
  if (backupFailure) return { block: true, reason: backupFailure };
  return undefined;
}

export default function registerLocalGuards(pi: ExtensionAPI): void {
  pi.on("tool_call", (event, ctx) => runBashGuards(event, ctx));
}
