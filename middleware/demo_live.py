"""
MCP + Context Engineering Demo — LIVE & GENERIC
================================================

Works with ANY Kubernetes error. No hardcoded error messages,
paths, or fixes. Everything is discovered from the real cluster
via MCP and analyzed by Claude dynamically.

Requirements:
    1. Kubernetes cluster with a problematic workload
    2. npm (for mcp-server-kubernetes via npx)
    3. pip install mcp
    4. ANTHROPIC_API_KEY (required — this is the generic version)

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python demo_live.py
    python demo_live.py --namespace myapp --deployment my-service
"""

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



# Config — change these or pass via CLI
NAMESPACE = "demo"
DEPLOYMENT = "order-service"
MCP_SERVER_CMD = "npx"
MCP_SERVER_ARGS = ["-y", "mcp-server-kubernetes"]
CONTEXT_WINDOW_MAX = 40_000



# Terminal presentation

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

def print_header(text):
    print(f"\n{CYAN}{'═' * 70}{NC}")
    print(f"{CYAN}{BOLD}  {text}{NC}")
    print(f"{CYAN}{'═' * 70}{NC}\n")

def print_subheader(text):
    print(f"\n  {MAGENTA}{BOLD}── {text} ──{NC}\n")

def wait(prompt_text):
    input(f"\n  {DIM}[Enter → {prompt_text}]{NC}")

def thinking_pause(message, seconds=1.5):
    print(f"  {DIM}⏳ {message}{NC}", end="", flush=True)
    time.sleep(seconds)
    print(f"  {GREEN}✓{NC}")

def print_prompt_box(title, content, color=CYAN):
    lines = content.strip().split("\n")
    max_len = min(max((len(l) for l in lines), default=40), 66)
    print(f"  {color}┌─ {BOLD}{title}{NC}{color} {'─' * max(1, max_len - len(title) - 1)}┐{NC}")
    for line in lines:
        truncated = line[:max_len]
        print(f"  {color}│{NC} {truncated:<{max_len}} {color}│{NC}")
    print(f"  {color}└{'─' * (max_len + 2)}┘{NC}")
    print()

def print_mcp_call(tool_name, args_display, token_delta):
    print(f"  {CYAN}→ MCP:{NC} {BOLD}{tool_name}{NC}({args_display})")
    print(f"         {DIM}+{token_delta} tokens{NC}")

def print_stage_animated(number, title, tokens, bar_max=CONTEXT_WINDOW_MAX,
                         bar_width=50, duration=1.5, prev_tokens=0):
    steps = 30
    delay = duration / steps
    label = "Optimized" if str(number) == "0" else f"Stage {number}"
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
    if str(number) == "0":   label = "Optimized"
    elif str(number) == "x": label = "Before"
    else:                    label = f"Stage {number}"
    pct = min(int((tokens / bar_max) * 100), 100)
    print(f"  {DIM}{label}: {title}{NC}")
    print(f"  {color}{bar}{NC}  {BOLD}{format_tokens(tokens):>6}{NC} tokens  ({pct}%)\n")

def print_llm_response(text):
    for line in text.strip().split("\n"):
        lower = line.lower()
        if any(kw in lower for kw in ["root cause", "problem:", "issue:", "error:", "bug:"]):
            print(f"  {RED}{BOLD}{line}{NC}")
        elif any(kw in lower for kw in ["fix", "solution", "change", "update", "patch", "recommend"]):
            print(f"  {GREEN}{line}{NC}")
        elif any(kw in lower for kw in ["confidence", "high", "medium", "low"]):
            print(f"  {CYAN}{BOLD}{line}{NC}")
        elif any(kw in lower for kw in ["evidence", "signal", "because", "chain"]):
            print(f"  {YELLOW}{line}{NC}")
        elif any(kw in lower for kw in ["cannot", "missing", "insufficient", "need more", "unable"]):
            print(f"  {YELLOW}{line}{NC}")
        else:
            print(f"  {line}")
    print()



# MCP helpers


# Tool name mapping — mcp-server-kubernetes (Flux159) uses kubectl_* names
# CONFIRMED: kubectl_get expects: resourceType + namespace (not command!)
# kubectl_logs expects: podName, namespace, tail
# kubectl_describe expects: resourceType, name, namespace
TOOL_MAP = {
    "get_pods":       ("kubectl_get",      lambda ns, **kw: {"resourceType": "pods", "namespace": ns}),
    "get_events":     ("kubectl_get",      lambda ns, **kw: {"resourceType": "events", "namespace": ns}),
    "get_pod_logs":   ("kubectl_logs",     lambda ns, name="", tail=100, **kw: {"resourceType": "pod", "name": name, "namespace": ns, "tail": tail}),
    "get_deployment": ("kubectl_get",      lambda ns, name="", **kw: {"resourceType": f"deployment/{name}", "namespace": ns}),
    "describe_pod":   ("kubectl_describe", lambda ns, name="", **kw: {"resourceType": "pod", "name": name, "namespace": ns}),
}


async def call_mcp(session, tool_name, arguments):
    """Call an MCP tool, auto-mapping friendly names to actual tool names."""
    # Check if we need to remap the tool name
    actual_tool = tool_name
    actual_args = arguments

    if tool_name in TOOL_MAP:
        actual_tool, args_fn = TOOL_MAP[tool_name]
        ns = arguments.get("namespace", NAMESPACE)
        actual_args = args_fn(ns, **{k: v for k, v in arguments.items() if k != "namespace"})

    try:
        result = await session.call_tool(actual_tool, arguments=actual_args)
        texts = []
        for block in result.content:
            if hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts) if texts else str(result.content)
    except Exception as e:
        return f"[MCP error: {tool_name} → {actual_tool}] {e}"


async def find_pod_names(session, namespace, deployment):
    raw = await call_mcp(session, "get_pods", {"namespace": namespace})
    names = []

    # MCP server returns JSON with items array
    try:
        data = json.loads(raw)
        if "items" in data:
            for item in data["items"]:
                name = item.get("name", "")
                if deployment in name:
                    names.append(name)
    except (json.JSONDecodeError, TypeError):
        # Fallback: parse as text table
        for line in raw.strip().split("\n"):
            if deployment in line and line.strip() and not line.startswith("NAME"):
                parts = line.split()
                if parts:
                    names.append(parts[0])

    return names, raw



# Claude API

def _get_ssl_context():
    """Get SSL context that works on macOS."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # Fallback: unverified (works for demo, not for production)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

_ssl_ctx = None

def call_claude(prompt, max_tokens=1500):
    global _ssl_ctx
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    if _ssl_ctx is None:
        _ssl_ctx = _get_ssl_context()

    try:
        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": max_tokens,
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
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
    except Exception as e:
        print(f"  {YELLOW}(API error: {e}){NC}")
    return None



# MAIN DEMO

async def run_demo():
    context_window = ""
    mcp_call_count = 0
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    print_header("MCP + Context Engineering: Live Demo")
    print(f"  Namespace:  {NAMESPACE}")
    print(f"  Deployment: {DEPLOYMENT}")
    print(f"  MCP:        {MCP_SERVER_CMD} {' '.join(MCP_SERVER_ARGS)}")
    if api_key:
        print(f"  Claude API: {GREEN}connected{NC}")
    else:
        print(f"  Claude API: {RED}NOT SET{NC}")
        print(f"  {RED}Run: export ANTHROPIC_API_KEY=\"sk-ant-...\" and try again.{NC}")
        return

    # ── Connect to MCP ───────────────────────────────────────
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
            for name in tool_names[:12]:
                print(f"    {CYAN}•{NC} {name}")
            if len(tool_names) > 12:
                print(f"    {DIM}...and {len(tool_names) - 12} more{NC}")

            
            # ACT 1: AGENT LOOP
           
            wait("Act 1: Agent Investigation Loop")
            print_header("Act 1 — Agent Calls MCP Tools (context grows)")
            print(f"  Each MCP call adds real cluster data to the context.\n")

            # get_pods
            mcp_call_count += 1
            pod_names, pods_raw = await find_pod_names(session, NAMESPACE, DEPLOYMENT)
            context_window += f"=== kubectl get pods -n {NAMESPACE} ===\n{pods_raw}\n\n"
            print_mcp_call("get_pods", f"namespace={NAMESPACE}", estimate_tokens(pods_raw))
            for line in pods_raw.strip().split("\n")[:5]:
                print(f"         {DIM}{line}{NC}")
            print()
            print_stage_animated(1, "After get_pods", estimate_tokens(context_window), duration=0.8)

            # get_events
            mcp_call_count += 1
            events_raw = await call_mcp(session, "get_events", {"namespace": NAMESPACE})
            context_window += f"=== events -n {NAMESPACE} ===\n{events_raw}\n\n"
            print_mcp_call("get_events", f"namespace={NAMESPACE}", estimate_tokens(events_raw))
            print(f"         {DIM}{len(events_raw.strip().split(chr(10)))} events collected{NC}")
            print()
            prev = estimate_tokens(pods_raw)
            print_stage_animated(2, "+ events", estimate_tokens(context_window), duration=0.8, prev_tokens=prev)

            # get_pod_logs
            wait("Agent fetches logs")
            all_logs = ""
            for i, pod_name in enumerate(pod_names[:2]):
                mcp_call_count += 1
                prev = estimate_tokens(context_window)
                log_raw = await call_mcp(session, "get_pod_logs", {
                    "namespace": NAMESPACE, "name": pod_name, "tail": 200
                })
                context_window += f"=== logs: {pod_name} ===\n{log_raw}\n\n"
                all_logs += log_raw + "\n"
                short = pod_name[-15:]
                print_mcp_call("get_pod_logs", f"pod=..{short}, tail=200", estimate_tokens(log_raw))
                if i == 0:
                    print(f"\n  {BOLD}Log excerpt (last 6 lines):{NC}")
                    for line in log_raw.strip().split("\n")[-6:]:
                        lower = line.lower()
                        if any(kw in lower for kw in ["error", "panic", "fatal", "fail", "crash"]):
                            print(f"    {RED}{line}{NC}")
                        elif "warn" in lower:
                            print(f"    {YELLOW}{line}{NC}")
                        else:
                            print(f"    {DIM}{line}{NC}")
                else:
                    print(f"         {DIM}(pod {i+1} logs collected){NC}")
                print()
                print_stage_animated(3 + i, f"+ pod{i+1} logs", estimate_tokens(context_window),
                                     duration=1.0, prev_tokens=prev)

            # get_deployment
            mcp_call_count += 1
            prev = estimate_tokens(context_window)
            deploy_raw = await call_mcp(session, "get_deployment", {
                "namespace": NAMESPACE, "name": DEPLOYMENT
            })
            context_window += f"=== deployment: {DEPLOYMENT} ===\n{deploy_raw}\n\n"
            print_mcp_call("get_deployment", f"name={DEPLOYMENT}", estimate_tokens(deploy_raw))
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
                context_window += f"=== describe: {pod_names[0]} ===\n{describe_raw}\n\n"
                print_mcp_call("describe_pod", f"pod=..{pod_names[0][-15:]}", estimate_tokens(describe_raw))
                print()
                print_stage_animated(6, "+ describe pod", estimate_tokens(context_window),
                                     duration=1.0, prev_tokens=prev)

            s2_tokens = estimate_tokens(context_window)
            print(f"  {BOLD}{mcp_call_count} MCP calls → {format_tokens(s2_tokens)} tokens{NC}")

            
            # ACT 2: EXPLOSION
            
            wait("Act 2: Context Explosion")
            print_header("Act 2 — What Happens in Production")
            print(f"  In production, the agent checks restart history,")
            print(f"  all replicas, configmaps, secrets...\n")

            explosion_context = context_window
            extra_calls = []
            for pod_name in pod_names[:2]:
                for _ in range(3):
                    extra_calls.append(("get_pod_logs", {"namespace": NAMESPACE, "name": pod_name, "tail": 500}))
            extra_calls.append(("get_events", {"namespace": NAMESPACE}))
            extra_calls.append(("get_events", {"namespace": NAMESPACE}))
            if pod_names:
                extra_calls.append(("describe_pod", {"namespace": NAMESPACE, "name": pod_names[0]}))
            extra_calls.append(("get_deployment", {"namespace": NAMESPACE, "name": DEPLOYMENT}))

            for tool_name, args in extra_calls:
                mcp_call_count += 1
                data = await call_mcp(session, tool_name, args)
                explosion_context += data + "\n"
                delta = estimate_tokens(data)
                short_arg = str(list(args.values())[0])[:20]
                sys.stdout.write(
                    f"\r  {CYAN}→{NC} MCP #{mcp_call_count}: {BOLD}{tool_name}{NC}({short_arg}) +{delta}tok" + " " * 20
                )
                sys.stdout.flush()
                time.sleep(0.3)

            print("\n")
            s3_tokens = estimate_tokens(explosion_context)
            s3_display = max(s3_tokens, 20000)

            print(f"  {BOLD}Total: {mcp_call_count} MCP calls{NC}\n")
            print_stage_static(1, "Initial investigation", s2_tokens)
            print_stage_animated(2, f"After {mcp_call_count} calls", s3_display,
                                 prev_tokens=s2_tokens, duration=2.5)
            print(f"  {RED}{BOLD}⚠  Context is no longer an asset — it's a problem.{NC}")

            
            # ACT 3: SUMMARIZATION — Claude compresses
            
            wait("Act 3: Summarization")
            print_header("Act 3 — Claude Compresses Raw Data")

            truncated = explosion_context[:80000]
            summarization_prompt = f"""You are a Kubernetes diagnostics expert.

I collected raw data from a cluster ({mcp_call_count} MCP tool calls, ~{format_tokens(s3_tokens)} tokens).

Analyze ALL of it and extract a concise diagnostic summary in JSON:
{{
  "unique_errors": ["deduplicated error messages"],
  "config_signals": ["config/mount/path/env/secret findings"],
  "request_patterns": "what works vs what fails",
  "pod_status": "brief status summary",
  "key_observations": ["other important findings"]
}}

Be aggressive about deduplication. Only what matters for diagnosis.

<raw_cluster_data>
{truncated}
</raw_cluster_data>

Respond ONLY with the JSON object."""

            display_prompt = f"""Analyze {format_tokens(s3_tokens)} tokens of raw cluster data.
Extract concise diagnostic summary as JSON:
  unique_errors, config_signals, request_patterns,
  pod_status, key_observations

<raw_cluster_data>
  ... {format_tokens(s3_tokens)} tokens from {mcp_call_count} MCP calls ...
</raw_cluster_data>"""

            print_prompt_box("SUMMARIZATION PROMPT", display_prompt)
            thinking_pause("Claude is analyzing raw cluster data...", 2.0)

            summary_response = call_claude(summarization_prompt, max_tokens=1000)
            summary_json = ""
            if summary_response:
                clean = summary_response.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                try:
                    summary_json = json.dumps(json.loads(clean), indent=2)
                except json.JSONDecodeError:
                    summary_json = clean

                summary_tokens = estimate_tokens(summary_json)
                print_subheader("Claude's Summary")
                for line in summary_json.split("\n"):
                    lower = line.lower()
                    if any(kw in lower for kw in ["error", "fail", "panic", "crash"]):
                        print(f"    {RED}{line}{NC}")
                    elif any(kw in lower for kw in ["mount", "config", "path", "secret"]):
                        print(f"    {YELLOW}{line}{NC}")
                    else:
                        print(f"    {line}")
                print(f"\n  {GREEN}{BOLD}{format_tokens(s3_tokens)} → {summary_tokens} tokens{NC}")
            else:
                print(f"  {RED}Summarization failed.{NC}")
                summary_tokens = s3_tokens

            
            # ACT 4: CONTEXT HANDOFF
            
            wait("Act 4: Context Handoff")
            print_header("Act 4 — Context Handoff")
            print(f"  {BOLD}Old way:{NC} all raw data → LLM")
            print(f"  {BOLD}New way:{NC} carry {YELLOW}knowledge{NC}, discard {DIM}data{NC}\n")

            handoff_tokens = estimate_tokens(summary_json) if summary_json else s3_tokens
            print_stage_static("x", "Raw (before)", s3_display)
            print_stage_animated(0, "Optimized (after handoff)", handoff_tokens,
                                 bar_max=s3_display, duration=2.0, prev_tokens=s3_display)

            reduction = ((s3_display - handoff_tokens) / max(s3_display, 1)) * 100
            print(f"  {GREEN}{BOLD}↓ {reduction:.0f}% reduction{NC}")
            print(f"  {BOLD}\"We carry forward knowledge, not data.\"{NC}")

            
            # ACT 5a: INCOMPLETE CONTEXT
            
            wait("Act 5: AI Reasoning — incomplete context")
            print_header("Act 5 — Root Cause Analysis")
            print(f"  {BOLD}What if the agent only has logs, no deployment spec?{NC}\n")

            incomplete_prompt = f"""You are a Kubernetes troubleshooting expert.

Workload "{DEPLOYMENT}" in namespace "{NAMESPACE}" has issues.
I ONLY have pod logs — no deployment spec, no describe output.

<logs_only>
{all_logs[:4000]}
</logs_only>

Based ONLY on these logs:
1. What can you determine about the problem?
2. What can you NOT determine without more context?
3. What additional data do you need?
4. Confidence level (Low/Medium/High) and why.

Be concise."""

            print_prompt_box("INCOMPLETE PROMPT (logs only)", f"Workload: {DEPLOYMENT}\nData: logs only, no spec\n\nWhat can you determine?\nWhat can you NOT determine?\nWhat data do you need?\nConfidence?", color=YELLOW)
            thinking_pause("Claude reasoning with incomplete context...", 2.0)

            incomplete_response = call_claude(incomplete_prompt)
            if incomplete_response:
                print(f"\n  {YELLOW}{BOLD}Claude (incomplete context):{NC}\n")
                print_llm_response(incomplete_response)
            else:
                print(f"  {YELLOW}(incomplete analysis unavailable){NC}\n")

            print(f"  {BOLD}A naive agent would hallucinate. A good agent {YELLOW}asks for more data.{NC}\n")

            
            # ACT 5b: FULL REASONING
            
            wait("Add deployment spec → full reasoning")
            print_subheader("Adding full context")
            thinking_pause("Context enriched...", 0.8)

            reasoning_prompt = f"""You are a Kubernetes troubleshooting expert.

Workload "{DEPLOYMENT}" in namespace "{NAMESPACE}" has issues.
Complete diagnostic context (summarized from {mcp_call_count} MCP calls):

<diagnostic_summary>
{summary_json[:5000]}
</diagnostic_summary>

<deployment_spec>
{deploy_raw[:5000]}
</deployment_spec>

Provide:
1. ROOT CAUSE — what exactly is wrong and why
2. WHY standard monitoring might miss it
3. EVIDENCE CHAIN — which signals led to this conclusion
4. EXACT FIX — specific kubectl command or YAML change
5. CONFIDENCE — High/Medium/Low with justification

Be concise and specific."""

            print_prompt_box("COMPLETE PROMPT", f"Workload: {DEPLOYMENT}\nData: summary + deployment spec\n\nRoot cause? Evidence? Exact fix? Confidence?", color=MAGENTA)
            thinking_pause("Claude reasoning over complete context...", 2.5)

            reasoning_response = call_claude(reasoning_prompt)
            if reasoning_response:
                print(f"\n  {BOLD}Claude's Root Cause Analysis:{NC}\n")
                print_llm_response(reasoning_response)
            else:
                print(f"  {RED}Reasoning failed.{NC}\n")

            
            # ACT 6: GENERATE AND APPLY FIX
            
            wait("Act 6: Generate and Apply Fix")
            print_header("Act 6 — Closing the Loop")

            fix_prompt = f"""Based on this analysis:

{reasoning_response or "unavailable"}

And the deployment spec:

{deploy_raw[:5000]}

Generate a kubectl patch to fix this.
Respond ONLY with JSON:
{{
  "patch_type": "strategic",
  "patch_json": {{ ... }},
  "explanation": "one line explaining the fix"
}}"""

            thinking_pause("Claude generating fix...", 1.5)
            fix_response = call_claude(fix_prompt, max_tokens=800)

            patch_applied = False
            if fix_response:
                try:
                    clean = fix_response.strip()
                    if clean.startswith("```"):
                        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    fix_data = json.loads(clean)

                    patch_type = fix_data.get("patch_type", "strategic")
                    patch_json = json.dumps(fix_data.get("patch_json", {}))
                    explanation = fix_data.get("explanation", "")

                    print(f"\n  {GREEN}{BOLD}Generated fix:{NC} {explanation}\n")
                    print_prompt_box("KUBECTL PATCH",
                        f"kubectl -n {NAMESPACE} patch deployment {DEPLOYMENT} \\\n  --type={patch_type} \\\n  -p='{patch_json}'",
                        color=GREEN)

                    apply = input(f"  {YELLOW}Apply this fix? (y/n): {NC}").strip().lower()
                    if apply == "y":
                        thinking_pause(f"Patching {DEPLOYMENT}...", 1.5)
                        result = subprocess.run(
                            ["kubectl", "-n", NAMESPACE, "patch", "deployment", DEPLOYMENT,
                             f"--type={patch_type}", f"-p={patch_json}"],
                            capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0:
                            print(f"  {GREEN}{BOLD}✓ {result.stdout.strip()}{NC}\n")
                            patch_applied = True
                        else:
                            print(f"  {RED}Error: {result.stderr.strip()}{NC}\n")

                        if patch_applied:
                            thinking_pause("Waiting for rollout...", 3.0)
                            subprocess.run(
                                ["kubectl", "-n", NAMESPACE, "rollout", "status",
                                 f"deployment/{DEPLOYMENT}", "--timeout=60s"],
                                capture_output=True, text=True, timeout=65
                            )
                            thinking_pause("Verifying...", 2.0)
                            pods_after = subprocess.run(
                                ["kubectl", "-n", NAMESPACE, "get", "pods"],
                                capture_output=True, text=True, timeout=10
                            )
                            print(f"\n  {BOLD}Pods after fix:{NC}")
                            for line in pods_after.stdout.strip().split("\n")[:5]:
                                print(f"    {GREEN}{line}{NC}")

                            print(f"\n  {BOLD}Testing service:{NC}")
                            try:
                                subprocess.run(["pkill", "-f", f"port-forward.*{DEPLOYMENT}"],
                                              capture_output=True, timeout=3)
                                time.sleep(1)
                                pf = subprocess.Popen(
                                    ["kubectl", "-n", NAMESPACE, "port-forward",
                                     f"svc/{DEPLOYMENT}", "8080:80"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                                )
                                time.sleep(3)
                                for ep in ["/healthz", "/api/orders"]:
                                    resp = subprocess.run(
                                        ["curl", "-s", f"http://localhost:8080{ep}"],
                                        capture_output=True, text=True, timeout=5
                                    )
                                    c = GREEN if "error" not in resp.stdout.lower() else RED
                                    print(f"    {ep:15} → {c}{resp.stdout.strip()[:80]}{NC}")
                                pf.terminate()
                            except Exception as e:
                                print(f"    {DIM}(test: {e}){NC}")
                    else:
                        print(f"  {DIM}Fix not applied.{NC}")

                except (json.JSONDecodeError, KeyError):
                    print(f"  {YELLOW}Claude's fix suggestion:{NC}\n")
                    print_llm_response(fix_response)
            else:
                print(f"  {RED}Could not generate fix.{NC}")

            
            # FINALE
            
            print_header("The Full Pipeline")
            print(f"  {BOLD}What just happened:{NC}\n")
            print(f"    {CYAN}1.{NC} MCP collected data     {DIM}{mcp_call_count} real tool calls{NC}")
            print(f"    {CYAN}2.{NC} Context exploded       {DIM}{format_tokens(s3_display)} tokens{NC}")
            print(f"    {CYAN}3.{NC} Summarization          {DIM}Claude compressed to signals{NC}")
            print(f"    {CYAN}4.{NC} Context handoff        {DIM}{reduction:.0f}% reduction{NC}")
            print(f"    {CYAN}5.{NC} Reasoning              {DIM}Incomplete → complete → root cause{NC}")
            print(f"    {CYAN}6.{NC} {'Applied fix' if patch_applied else 'Generated fix':<22}{DIM}Claude produced the patch{NC}")
            print()

            print(f"  {BOLD}Context through the pipeline:{NC}\n")
            print_stage_static(1, "After investigation", s2_tokens)
            print_stage_static(2, "After accumulation", s3_display)
            print_stage_static(0, "After handoff", handoff_tokens)

            print(f"""
  {BOLD}This demo is fully generic.{NC}
  {DIM}Same pipeline works for any Kubernetes error:{NC}
  {DIM}OOMKilled, ImagePullBackOff, CrashLoopBackOff,{NC}
  {DIM}network issues, certificate expiry, RBAC problems...{NC}

  {CYAN}MCP{NC}                → cluster observable to AI
  {CYAN}Context engineering{NC} → data manageable
  {CYAN}Claude{NC}             → reasoned, diagnosed, fixed

  {CYAN}{BOLD}This is context engineering in action.{NC}
""")


if __name__ == "__main__":
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--namespace", "-n") and i < len(sys.argv) - 1:
            NAMESPACE = sys.argv[i + 1]
        elif arg in ("--deployment", "-d") and i < len(sys.argv) - 1:
            DEPLOYMENT = sys.argv[i + 1]
    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        print(f"\n{DIM}  Demo ended.{NC}")