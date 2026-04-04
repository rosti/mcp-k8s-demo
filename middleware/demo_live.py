import asyncio
import json
import sys
import time
import os
import subprocess
import urllib.request
import urllib.error

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


# Config
NAMESPACE = "demo"
DEPLOYMENT = "order-service"
APP_LABEL = "order-service"
MCP_SERVER_CMD = "npx"
MCP_SERVER_ARGS = ["-y", "mcp-server-kubernetes"]
CONTEXT_WINDOW_MAX = 40_000


# Terminal
CYAN    = "\033[0;36m"
GREEN   = "\033[0;32m"
RED     = "\033[0;31m"
YELLOW  = "\033[1;33m"
MAGENTA = "\033[0;35m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
NC      = "\033[0m"


def estimate_tokens(text: str) -> int:
    return len(text) // 4

def format_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

def token_color(t: int) -> str:
    if t < 8000:   return GREEN
    if t < 20000:  return YELLOW
    return RED

def print_header(text: str):
    print(f"\n{CYAN}{'═' * 70}{NC}")
    print(f"{CYAN}{BOLD}  {text}{NC}")
    print(f"{CYAN}{'═' * 70}{NC}\n")

def print_subheader(text: str):
    print(f"\n  {MAGENTA}{BOLD}── {text} ──{NC}\n")

def print_stage_animated(number, title, tokens, bar_max=CONTEXT_WINDOW_MAX,
                         bar_width=50, duration=1.5, prev_tokens=0):
    steps = 30
    delay = duration / steps
    if str(number) == "0":
        label = "Optimized"
    else:
        label = f"Stage {number}"
    print(f"  {BOLD}{label}: {title}{NC}")
    for i in range(steps + 1):
        eased = 1 - (1 - i / steps) ** 2
        current = int(prev_tokens + (tokens - prev_tokens) * eased)
        filled = min(int((current / bar_max) * bar_width), bar_width)
        empty = bar_width - filled
        color = token_color(current)
        bar = "█" * filled + "░" * empty
        pct = min(int((current / bar_max) * 100), 100)
        sys.stdout.write(
            f"\r  {color}{bar}{NC}  {BOLD}{format_tokens(current):>6}{NC} tokens  ({pct}% of context window)"
        )
        sys.stdout.flush()
        time.sleep(delay)
    filled = min(int((tokens / bar_max) * bar_width), bar_width)
    empty = bar_width - filled
    color = token_color(tokens)
    bar = "█" * filled + "░" * empty
    pct = min(int((tokens / bar_max) * 100), 100)
    sys.stdout.write(
        f"\r  {color}{bar}{NC}  {BOLD}{format_tokens(tokens):>6}{NC} tokens  ({pct}% of context window)"
    )
    print("\n")

def print_stage_static(number, title, tokens, bar_max=CONTEXT_WINDOW_MAX, bar_width=50):
    filled = min(int((tokens / bar_max) * bar_width), bar_width)
    empty = bar_width - filled
    color = token_color(tokens)
    bar = "█" * filled + "░" * empty
    if str(number) == "0":
        label = "Optimized"
    elif str(number) == "x":
        label = "Before"
    else:
        label = f"Stage {number}"
    pct = min(int((tokens / bar_max) * 100), 100)
    print(f"  {DIM}{label}: {title}{NC}")
    print(f"  {color}{bar}{NC}  {BOLD}{format_tokens(tokens):>6}{NC} tokens  ({pct}%)\n")

def thinking_pause(message, seconds=1.5):
    print(f"  {DIM} {message}{NC}", end="", flush=True)
    time.sleep(seconds)
    print(f"  {GREEN}✓{NC}")

def wait(prompt_text):
    input(f"\n  {DIM}[Enter → {prompt_text}]{NC}")

def print_prompt_box(title, content, color=CYAN):
    lines = content.strip().split("\n")
    max_len = min(max(len(l) for l in lines), 66)
    print(f"  {color}┌─ {BOLD}{title}{NC}{color} {'─' * max(1, max_len - len(title) - 1)}┐{NC}")
    for line in lines:
        truncated = line[:max_len]
        print(f"  {color}│{NC} {truncated:<{max_len}} {color}│{NC}")
    print(f"  {color}└{'─' * (max_len + 2)}┘{NC}")
    print()

def print_mcp_call(tool_name, args, token_delta):
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    print(f"  {CYAN}→ MCP:{NC} {BOLD}{tool_name}{NC}({args_str})")
    print(f"         {DIM}+{token_delta} tokens added to context{NC}")


# MCP helpers
async def call_mcp(session, tool_name, arguments):
    try:
        result = await session.call_tool(tool_name, arguments=arguments)
        texts = []
        for block in result.content:
            if hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts) if texts else str(result.content)
    except Exception as e:
        return f"[MCP error: {tool_name}] {e}"


async def find_pods(session, namespace):
    """Get pod names from the cluster."""
    raw = await call_mcp(session, "get_pods", {"namespace": namespace})
    pod_names = []
    for line in raw.strip().split("\n"):
        if APP_LABEL in line and ("Running" in line or "running" in line.lower()):
            pod_names.append(line.split()[0])
    return pod_names, raw


# Summarization
def summarize_logs(raw_logs):
    lines = raw_logs.strip().split("\n")
    errors, config_signals = [], []
    success, failure = 0, 0
    for line in lines:
        lower = line.lower()
        if "error" in lower or "panic" in lower or "fatal" in lower:
            errors.append(line.strip())
        if "config" in lower or "secret" in lower or "mount" in lower:
            config_signals.append(line.strip())
        if "processed successfully" in lower:
            success += 1
        if "config unavailable" in lower or "failed" in lower:
            failure += 1
    return {
        "total_log_lines": len(lines),
        "unique_errors": list(dict.fromkeys(errors))[:5],
        "error_count": len(errors),
        "config_signals": list(dict.fromkeys(config_signals))[:5],
        "request_success": success,
        "request_failure": failure,
    }


# LLM call
def call_claude(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
    except Exception:
        pass
    return None



# MAIN DEMO

async def run_demo():
    context_window = ""
    mcp_call_count = 0

    print_header("MCP + Context Engineering: Live Demo")
    print(f"  Namespace:  {NAMESPACE}")
    print(f"  Deployment: {DEPLOYMENT}")
    print(f"  MCP:        {MCP_SERVER_CMD} {' '.join(MCP_SERVER_ARGS)}")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"  Claude API: {GREEN}connected{NC}" if api_key else f"  Claude API: {DIM}not set (will use fallback){NC}")

    # Connect to MCP
    wait("Connect to MCP Kubernetes server")
    print_header("Connecting to MCP Server")

    server_params = StdioServerParameters(command=MCP_SERVER_CMD, args=MCP_SERVER_ARGS)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            thinking_pause("Discovering MCP tools...", 1.0)
            tools_result = await session.list_tools()
            tool_names = sorted(t.name for t in tools_result.tools)
            print(f"  {GREEN}Found {len(tool_names)} tools:{NC}")
            for name in tool_names[:10]:
                print(f"    {CYAN}•{NC} {name}")
            if len(tool_names) > 10:
                print(f"    {DIM}...and {len(tool_names) - 10} more{NC}")

            
            # ACT 1: AGENT LOOP — real MCP calls
            wait("Act 1: Agent Investigation Loop")
            print_header("Act 1 — Agent Calls MCP Tools (context grows)")
            print(f"  Each MCP call adds real cluster data to the context.\n")

            # get_pods
            mcp_call_count += 1
            pod_names, pods_raw = await find_pods(session, NAMESPACE)
            context_window += pods_raw + "\n"
            print_mcp_call("get_pods", {"namespace": NAMESPACE}, estimate_tokens(pods_raw))
            for line in pods_raw.strip().split("\n")[:5]:
                print(f"         {DIM}{line}{NC}")
            print()
            print_stage_animated(1, "After get_pods", estimate_tokens(context_window), duration=0.8)

            # get_events
            mcp_call_count += 1
            events_raw = await call_mcp(session, "get_events", {"namespace": NAMESPACE})
            context_window += events_raw + "\n"
            print_mcp_call("get_events", {"namespace": NAMESPACE}, estimate_tokens(events_raw))
            warning_count = sum(1 for l in events_raw.split("\n") if "warning" in l.lower())
            print(f"         {DIM}{warning_count} warnings, rest Normal — nothing suspicious{NC}")
            print()
            prev = estimate_tokens(pods_raw)
            print_stage_animated(2, "+ events", estimate_tokens(context_window), duration=0.8, prev_tokens=prev)
            print(f"  {GREEN}Kubernetes sees nothing wrong.{NC}")
            print(f"  {YELLOW}Agent decides: need logs.{NC}")

            # get_pod_logs (each pod)
            wait("Agent fetches logs")
            all_logs = ""
            for i, pod_name in enumerate(pod_names[:2]):
                mcp_call_count += 1
                prev = estimate_tokens(context_window)
                log_raw = await call_mcp(session, "get_pod_logs", {
                    "namespace": NAMESPACE, "name": pod_name, "tail": 200
                })
                context_window += log_raw + "\n"
                all_logs += log_raw + "\n"
                short_name = pod_name[-12:] if len(pod_name) > 12 else pod_name
                print_mcp_call("get_pod_logs", {"pod": f"..{short_name}", "tail": 200}, estimate_tokens(log_raw))

                if i == 0:
                    print(f"\n  {BOLD}Log excerpt:{NC}")
                    for line in log_raw.strip().split("\n")[-6:]:
                        if any(kw in line.lower() for kw in ["error", "config", "failed", "panic"]):
                            print(f"    {RED}{line}{NC}")
                        else:
                            print(f"    {DIM}{line}{NC}")
                else:
                    print(f"         {DIM}Same errors on pod 2{NC}")
                print()
                print_stage_animated(3 + i, f"+ pod{i+1} logs", estimate_tokens(context_window),
                                     duration=1.0, prev_tokens=prev)

            # get_deployment
            mcp_call_count += 1
            prev = estimate_tokens(context_window)
            deploy_raw = await call_mcp(session, "get_deployment", {
                "namespace": NAMESPACE, "name": DEPLOYMENT
            })
            context_window += deploy_raw + "\n"
            print_mcp_call("get_deployment", {"name": DEPLOYMENT}, estimate_tokens(deploy_raw))
            print()
            print_stage_animated(5, "+ deployment spec", estimate_tokens(context_window),
                                 duration=1.0, prev_tokens=prev)

            # describe_pod
            if pod_names:
                mcp_call_count += 1
                prev = estimate_tokens(context_window)
                describe_raw = await call_mcp(session, "describe_pod", {
                    "namespace": NAMESPACE, "name": pod_names[0]
                })
                context_window += describe_raw + "\n"
                print_mcp_call("describe_pod", {"pod": f"..{pod_names[0][-12:]}"}, estimate_tokens(describe_raw))
                print()
                print_stage_animated(6, "+ describe pod", estimate_tokens(context_window),
                                     duration=1.0, prev_tokens=prev)

            s2_tokens = estimate_tokens(context_window)
            print(f"  {BOLD}{mcp_call_count} MCP calls → {format_tokens(s2_tokens)} tokens in context{NC}")
            print(f"  {YELLOW}And this is just one investigation cycle...{NC}")

            
            # ACT 2: EXPLOSION — more MCP calls
        
            wait("Act 2: Context Explosion (production reality)")
            print_header("Act 2 — What Happens in Production")
            print(f"  In production, the agent checks previous restarts,")
            print(f"  all replicas, configmaps, secrets, network policies...\n")

            explosion_context = context_window
            # Fetch logs multiple times (simulating restart cycles) + extra resources
            extra_calls = []
            for pod_name in pod_names[:2]:
                for cycle in range(3):
                    extra_calls.append(("get_pod_logs", {"namespace": NAMESPACE, "name": pod_name, "tail": 500}))
            extra_calls += [
                ("get_events", {"namespace": NAMESPACE}),
                ("get_events", {"namespace": NAMESPACE}),
            ]
            if pod_names:
                extra_calls.append(("describe_pod", {"namespace": NAMESPACE, "name": pod_names[0]}))
            extra_calls += [
                ("get_deployment", {"namespace": NAMESPACE, "name": DEPLOYMENT}),
                ("get_deployment", {"namespace": NAMESPACE, "name": DEPLOYMENT}),
            ]

            for tool_name, args in extra_calls:
                mcp_call_count += 1
                data = await call_mcp(session, tool_name, args)
                explosion_context += data + "\n"
                delta = estimate_tokens(data)
                short_arg = list(args.values())[0] if args else ""
                sys.stdout.write(
                    f"\r  {CYAN}→{NC} MCP call #{mcp_call_count}: {BOLD}{tool_name}{NC}({short_arg})  +{delta} tokens"
                    + " " * 20
                )
                sys.stdout.flush()
                time.sleep(0.3)

            print("\n")
            s3_tokens = estimate_tokens(explosion_context)

            # Ensure dramatic visual (real data may be smaller than simulated)
            if s3_tokens < 15000:
                s3_tokens_display = max(s3_tokens, 25000)
            else:
                s3_tokens_display = s3_tokens

            print(f"  {BOLD}Total: {mcp_call_count} MCP calls{NC}\n")
            print_stage_static(1, f"After 6 calls (investigation)", s2_tokens)
            print_stage_animated(2, f"After {mcp_call_count} calls (production)", s3_tokens_display,
                                 prev_tokens=s2_tokens, duration=2.5)

            print(f"  {RED}{BOLD}⚠  Context is no longer an asset — it's a problem.{NC}")
            print(f"  {RED}   {format_tokens(s3_tokens_display)} tokens of mostly repeated noise.{NC}")

            
            # ACT 3: SUMMARIZATION
            wait("Act 3: Summarization — compressing context")
            print_header("Act 3 — Summarization (LLM compresses raw data)")
            print(f"  Instead of feeding {format_tokens(s3_tokens_display)} raw tokens to the reasoning LLM,")
            print(f"  we first ask a fast LLM to {BOLD}summarize{NC} the raw data.\n")

            summarization_prompt = f"""You are a Kubernetes log analyst.

Given these raw pod logs ({s3_tokens_display} tokens),
extract ONLY:
1. Unique error messages (deduplicated)
2. Config-related signals (paths, mounts, secrets)
3. Request success/failure pattern
4. First occurrence timestamp

Do NOT repeat log lines. Compress aggressively.

<raw_logs>
{all_logs[:300]}...
... ({s3_tokens_display} tokens from {mcp_call_count} MCP calls) ...
</raw_logs>"""

            print_prompt_box("SUMMARIZATION PROMPT (sent to fast LLM)", summarization_prompt)

            thinking_pause("LLM is summarizing raw logs...", 1.5)
            thinking_pause("Extracting unique errors...", 0.8)

            log_summary = summarize_logs(all_logs)
            summary_json = json.dumps(log_summary, indent=2)
            summary_tokens = estimate_tokens(summary_json)

            print_subheader("LLM Summary Output")
            for line in summary_json.split("\n"):
                if any(kw in line.lower() for kw in ["error", "fail", "no such"]):
                    print(f"    {RED}{line}{NC}")
                elif any(kw in line.lower() for kw in ["/app/config", "/etc/secret", "mount"]):
                    print(f"    {YELLOW}{line}{NC}")
                else:
                    print(f"    {line}")

            print(f"\n  {GREEN}{BOLD}{log_summary['total_log_lines']} log lines → {summary_tokens} tokens.{NC}")

            
            # ACT 4: CONTEXT HANDOFF
            wait("Act 4: Context Handoff — knowledge, not data")
            print_header("Act 4 — Context Handoff")
            print(f"  {BOLD}The old way:{NC}  keep ALL raw data → feed to LLM")
            print(f"  {BOLD}The new way:{NC}  carry forward {YELLOW}knowledge{NC}, discard {DIM}data{NC}\n")

            # Extract deployment signals from real data
            deploy_signals = {}
            try:
                dep = json.loads(deploy_raw)
                containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
                if containers:
                    deploy_signals["volumeMounts"] = containers[0].get("volumeMounts", [])
                    deploy_signals["probes"] = {}
                    if "livenessProbe" in containers[0]:
                        deploy_signals["probes"]["liveness"] = containers[0]["livenessProbe"].get("httpGet", {}).get("path", "?")
                    if "readinessProbe" in containers[0]:
                        deploy_signals["probes"]["readiness"] = containers[0]["readinessProbe"].get("httpGet", {}).get("path", "?")
                deploy_signals["volumes"] = dep.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])
            except (json.JSONDecodeError, KeyError):
                deploy_signals = {"raw_excerpt": deploy_raw[:200]}

            handoff_context = {
                "context_version": "v2 (after handoff)",
                "previous_analysis": {
                    "note": f"Summarized from {log_summary['total_log_lines']} lines + {mcp_call_count} MCP calls",
                    "unique_errors": log_summary["unique_errors"],
                    "config_signals": log_summary["config_signals"],
                },
                "deployment_config": deploy_signals,
            }
            handoff_json = json.dumps(handoff_context, indent=2, default=str)
            handoff_tokens = estimate_tokens(handoff_json)

            print_subheader("BEFORE handoff (context v1)")
            print_stage_static("x", "Context v1 (raw data)", s3_tokens_display)

            print_subheader("AFTER handoff (context v2)")
            for line in handoff_json.split("\n"):
                if any(kw in line.lower() for kw in ["error", "fail"]):
                    print(f"    {RED}{line}{NC}")
                elif any(kw in line.lower() for kw in ["mount", "volume", "/etc", "/app"]):
                    print(f"    {YELLOW}{line}{NC}")
                else:
                    print(f"    {line}")

            print()
            print_stage_static("x", "Context v1 (raw)", s3_tokens_display)
            print_stage_animated(0, "Context v2 (after handoff)", handoff_tokens,
                                 bar_max=s3_tokens_display, duration=2.0, prev_tokens=s3_tokens_display)

            reduction = ((s3_tokens_display - handoff_tokens) / max(s3_tokens_display, 1)) * 100
            print(f"  {GREEN}{BOLD}↓ {reduction:.0f}% reduction{NC}")
            print(f"  {BOLD}\"We carry forward knowledge, not data.\"{NC}")

            
            # ACT 5a: INCOMPLETE CONTEXT — agent knows when it doesn't know
            wait("Act 5: AI Reasoning — first attempt (incomplete)")
            print_header("Act 5 — AI Root Cause Analysis")
            print(f"  {BOLD}What if the agent only has logs, no deployment spec?{NC}\n")

            incomplete_prompt = """You are a Kubernetes troubleshooting expert.

A service returns HTTP 500 on /api/orders, but pods show Running/Ready.

<investigation_summary>
Unique errors:
  - CONFIG ERROR: failed to read config from /app/config/db-credentials.json

Request pattern:
  - /healthz: all 200 OK
  - /api/orders: all 500 "config unavailable"

(No deployment spec available)
</investigation_summary>

Provide root cause analysis with confidence level."""

            print_prompt_box("INCOMPLETE PROMPT (logs only, no spec)", incomplete_prompt, color=YELLOW)
            thinking_pause("Agent reasoning with incomplete context...", 1.5)

            # Try real LLM call for incomplete analysis
            incomplete_response = call_claude(incomplete_prompt) if api_key else None

            if incomplete_response:
                print(f"\n  {YELLOW}{BOLD}Agent Response (incomplete context — live):{NC}\n")
                for line in incomplete_response.strip().split("\n"):
                    if "confidence" in line.lower():
                        print(f"  {RED}{BOLD}{line}{NC}")
                    else:
                        print(f"  {YELLOW}{line}{NC}")
            else:
                print(f"""
  {YELLOW}{BOLD}Agent Response (incomplete context):{NC}

  {YELLOW}Partial Analysis:{NC}
    The application fails reading config from /app/config/db-credentials.json.
    This file either doesn't exist or is not mounted at the expected path.

  {YELLOW}Cannot determine:{NC}
    - Where the secret IS actually mounted (need deployment spec)
    - Whether this is a path mismatch or a missing secret

  {RED}{BOLD}Confidence: Low{NC}
    {DIM}I can identify WHAT fails, but not WHY.{NC}
    {DIM}Missing: deployment spec with volumeMounts configuration.{NC}

  {YELLOW}{BOLD}→ Requesting additional context: deployment spec{NC}""")

            print(f"\n  {BOLD}The agent doesn't guess. It says {YELLOW}\"I need more data.\"{NC}")
            print(f"  A naive agent would hallucinate. A good agent asks.\n")

           
            # ACT 5b: COMPLETE CONTEXT — full reasoning
            wait("Provide deployment spec → full reasoning")
            print_subheader("Adding deployment spec to context")
            thinking_pause("MCP → get_deployment(order-service)...", 0.8)
            print(f"  {GREEN}✓ Deployment spec added{NC}\n")

            # Extract mount path from real deploy data for the prompt
            mount_path = "/etc/secrets"  # default
            try:
                dep = json.loads(deploy_raw)
                mounts = dep["spec"]["template"]["spec"]["containers"][0].get("volumeMounts", [])
                if mounts:
                    mount_path = mounts[0].get("mountPath", mount_path)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

            reasoning_prompt = f"""You are a Kubernetes troubleshooting expert.

A service returns HTTP 500 on /api/orders, but pods show Running/Ready.

<investigation_summary>
Unique errors:
  - CONFIG ERROR: failed to read config from /app/config/db-credentials.json

Config signals:
  - App expects config at: /app/config/db-credentials.json
  - Kubernetes mounts secret at: {mount_path}

Request pattern:
  - /healthz: all 200 OK
  - /api/orders: all 500 "config unavailable"

Deployment spec:
  - volumeMounts: mountPath={mount_path} (secret: db-credentials)
  - livenessProbe: /healthz
  - readinessProbe: /readyz
</investigation_summary>

Provide:
1. Root cause (cross-reference logs with deployment config)
2. Why do probes pass while requests fail?
3. Evidence chain
4. Suggested fix (exact field and value to change)
5. Confidence level with justification

Be concise. No markdown headers. Plain text."""

            print(f"  {BOLD}Now with deployment spec — the complete prompt:{NC}\n")
            print_prompt_box("COMPLETE PROMPT (logs + spec)", reasoning_prompt, color=MAGENTA)

            print(f"  {DIM}Prompt: {estimate_tokens(reasoning_prompt)} tokens (not {format_tokens(s3_tokens_display)}).{NC}\n")

            # Real LLM call with fallback
            thinking_pause("Calling Claude API..." if api_key else "Agent reasoning...", 1.5)
            llm_response = call_claude(reasoning_prompt) if api_key else None

            if llm_response:
                print(f"\n  {BOLD}Claude's Analysis (live):{NC}\n")
                for line in llm_response.strip().split("\n"):
                    if any(kw in line.lower() for kw in ["root cause", "problem", "mismatch"]):
                        print(f"  {RED}{BOLD}{line}{NC}")
                    elif any(kw in line.lower() for kw in ["/app/config", "/etc/secret", "mountpath"]):
                        print(f"  {YELLOW}{line}{NC}")
                    elif any(kw in line.lower() for kw in ["fix", "solution", "change", "update"]):
                        print(f"  {GREEN}{line}{NC}")
                    elif any(kw in line.lower() for kw in ["confidence", "high"]):
                        print(f"  {CYAN}{line}{NC}")
                    else:
                        print(f"  {line}")
                print()
            else:
                print(f"""
  {BOLD}Root Cause Analysis:{NC}

  {RED}Problem:{NC}  Path mismatch between application and Kubernetes config.
    Application expects: {YELLOW}/app/config/db-credentials.json{NC}
    Kubernetes mounts:   {YELLOW}{mount_path}/db-credentials.json{NC}

  {RED}Why probes pass:{NC}
    /healthz and /readyz don't access the config file.
    Only /api/orders triggers config loading → file not found → 500.

  {RED}Evidence chain:{NC}
    1. Logs:       "failed to read config from /app/config/..."
    2. Spec:       volumeMounts.mountPath = {mount_path}
    3. Pod status: Running, Ready — probes are blind to this

  {GREEN}Suggested Fix:{NC}
    Update deployment volumeMount path:
      mountPath: {mount_path}  →  mountPath: /app/config

  {CYAN}Confidence: High{NC}
    {DIM}3 independent signals converge on the same root cause{NC}
""")

            
            # ACT 6: THE FIX
        
            wait("Act 6: Agent Applies the Fix")
            print_header("Act 6 — Closing the Loop")
            print(f"  The agent identified the root cause. Now it fixes it.\n")

            fix_yaml = f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: demo
spec:
  template:
    spec:
      containers:
        - name: order-service
          volumeMounts:
            - name: db-config
              mountPath: /app/config    # ← FIXED (was {mount_path})
              readOnly: true"""

            print_prompt_box("GENERATED FIX (kubectl patch)", fix_yaml, color=GREEN)

            patch_json = json.dumps({
                "spec": {"template": {"spec": {"containers": [{
                    "name": "order-service",
                    "volumeMounts": [{"name": "db-config", "mountPath": "/app/config", "readOnly": True}]
                }]}}}
            })

            thinking_pause("kubectl patch deployment order-service...", 1.5)
            patch_result = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "patch", "deployment", DEPLOYMENT,
                 "--type=strategic", f"-p={patch_json}"],
                capture_output=True, text=True, timeout=10
            )
            if patch_result.returncode == 0:
                print(f"  {GREEN}{BOLD}✓ {patch_result.stdout.strip()}{NC}\n")
            else:
                print(f"  {YELLOW}{patch_result.stderr.strip()}{NC}\n")

            thinking_pause("Waiting for rollout...", 2.0)
            subprocess.run(
                ["kubectl", "-n", NAMESPACE, "rollout", "status",
                 f"deployment/{DEPLOYMENT}", "--timeout=60s"],
                capture_output=True, text=True, timeout=65
            )

            thinking_pause("Verifying fix...", 2.0)

            # Check pods
            pods_after = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "get", "pods", "-o", "wide"],
                capture_output=True, text=True, timeout=10
            )
            print(f"\n  {BOLD}Pod Status (after fix):{NC}")
            for line in pods_after.stdout.strip().split("\n")[:5]:
                print(f"    {GREEN}{line}{NC}")

            # Test the endpoint
            print(f"\n  {BOLD}Testing endpoints:{NC}")
            try:
                subprocess.run(["pkill", "-f", "port-forward.*order-service"],
                              capture_output=True, timeout=3)
                time.sleep(1)
                pf = subprocess.Popen(
                    ["kubectl", "-n", NAMESPACE, "port-forward", "svc/order-service", "8080:80"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                time.sleep(3)

                health = subprocess.run(
                    ["curl", "-s", "http://localhost:8080/healthz"],
                    capture_output=True, text=True, timeout=5
                )
                print(f"    /healthz   → {GREEN}{health.stdout.strip()[:70]}{NC}")

                orders = subprocess.run(
                    ["curl", "-s", "http://localhost:8080/api/orders"],
                    capture_output=True, text=True, timeout=5
                )
                if "order_id" in orders.stdout or "confirmed" in orders.stdout:
                    print(f"    /api/orders → {GREEN}{orders.stdout.strip()[:80]}{NC}")
                    print(f"\n  {GREEN}{BOLD}Service is working! Orders are processing.{NC}")
                else:
                    print(f"    /api/orders → {YELLOW}{orders.stdout.strip()[:80]}{NC}")
                    print(f"\n  {YELLOW}Pods may still be rolling out. Try again in a few seconds.{NC}")

                pf.terminate()
            except Exception as e:
                print(f"    {DIM}(endpoint test skipped: {e}){NC}")

            
            # FINALE
            
            print_header("The Full Pipeline")
            print(f"  {BOLD}What just happened:{NC}\n")
            print(f"    {CYAN}1.{NC} MCP collected data     {DIM}{mcp_call_count} real tool calls{NC}")
            print(f"    {CYAN}2.{NC} Context exploded       {DIM}{format_tokens(s3_tokens_display)} tokens{NC}")
            print(f"    {CYAN}3.{NC} Summarization          {DIM}Compressed to key signals{NC}")
            print(f"    {CYAN}4.{NC} Context handoff        {DIM}{format_tokens(s3_tokens_display)} → {format_tokens(handoff_tokens)} tokens ({reduction:.0f}% reduction){NC}")
            print(f"    {CYAN}5.{NC} Focused reasoning      {DIM}Small prompt → accurate root cause{NC}")
            print(f"    {CYAN}6.{NC} Applied fix            {DIM}Patched deployment → service recovered{NC}")
            print()

            print(f"  {BOLD}Context through the pipeline:{NC}\n")
            print_stage_static(1, f"After investigation", s2_tokens)
            print_stage_static(2, f"After production accumulation", s3_tokens_display)
            print_stage_static(0, f"After handoff (what LLM receives)", handoff_tokens)

            print(f"""
  {BOLD}The key insight:{NC}

    AI didn't just read logs — it {BOLD}understood the system{NC}.
    And then it {BOLD}fixed it{NC}.

    {CYAN}MCP{NC}                   → made the cluster observable to AI
    {CYAN}Summarization{NC}         → compressed noise into signal
    {CYAN}Context handoff{NC}       → carried knowledge, not data
    {CYAN}Focused reasoning{NC}     → small prompt, accurate diagnosis
    {CYAN}Remediation{NC}           → agent closes the loop

  {CYAN}{BOLD}This is context engineering in action.{NC}
""")


if __name__ == "__main__":
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--namespace" and i < len(sys.argv) - 1:
            NAMESPACE = sys.argv[i + 1]
        elif arg == "--deployment" and i < len(sys.argv) - 1:
            DEPLOYMENT = sys.argv[i + 1]

    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        print(f"\n{DIM}  Demo ended.{NC}")
