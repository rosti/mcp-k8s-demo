---
name: k8s_alert_runbook
description: Triage Alertmanager alerts received through Discord by using Kubernetes MCP tools in read-only mode. Track context window usage.
---

# Kubernetes Alert Runbook

Use this skill when:
- a Discord message contains an Alertmanager alert
- the message includes labels such as alertname, severity, namespace, pod, deployment, service, cluster, node, job
- the user asks to investigate a Kubernetes production issue

## Primary objective

Turn a Discord-supplied alert into a fast, safe, evidence-based Kubernetes investigation.

## Required behavior

1. Parse the alert message first.
2. Extract as many of these as possible:
  - alertname
  - status (firing or resolved)
  - severity
  - cluster
  - namespace
  - workload kind
  - workload name
  - pod
  - container
  - node
  - service
  - summary
  - description
  - runbook_url
  - startsAt
3. Prefer read-only investigation.
4. Narrow scope aggressively: cluster, then namespace, then workload or pod, then recent events, then logs.
5. Never invent missing labels or names.
6. If the alert is ambiguous, say exactly what is missing and investigate only what is supported by the message.

## Investigation order

### A. Restate the alert
Produce a one-paragraph restatement: what is failing, where, how severe, whether firing or resolved.

### B. Check object health
Investigate the most specific object available: pod first if pod is present, otherwise deployment/statefulset/daemonset, otherwise namespace-level resources.

### C. Check recent Kubernetes events
Look for: FailedScheduling, BackOff, CrashLoopBackOff, OOMKilled, ImagePullBackOff, FailedMount, probe failures, eviction, node disruption.

### D. Check current status details
Focus on: restart counts, readiness, container waiting/terminated reason, replica mismatch, unavailable replicas, pending state, node assignment.

### E. Check logs
If a pod or container is identified: inspect recent logs for the impacted container, prefer current container first, inspect previous logs too when restart loops are suspected.

### F. Form a verdict
Give: the most likely cause, the confidence level, exact evidence collected, the smallest safe next action.

## Output format

ALWAYS answer using EXACTLY this structure with ALL sections. Do not skip any section.

### 🔍 Alert
- alertname:
- severity:
- status:
- namespace:
- target:

### 📊 Investigation & Context Tracking
After completing your MCP tool calls, show a summary table of all the data you collected. For each tool call, show one line with an emoji bar showing cumulative context size. Use 🟩 for under 8k tokens, 🟨 for 8k-20k, 🟥 for over 20k. Use ⬜ for empty. Always 10 slots. Estimate tokens as: approximate word count × 1.3.

Example of what this section must look like:

🔍 Step 1 — pods:        🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜  ~100 tokens · 2 pods Running
🔍 Step 2 — events:      🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜  ~1k tokens · no warnings
🔍 Step 3 — logs:        🟩🟩🟩⬜⬜⬜⬜⬜⬜⬜  ~2k tokens · CONFIG ERROR found
🔍 Step 4 — deployment:  🟩🟩🟩🟩🟩⬜⬜⬜⬜⬜  ~4k tokens · mount at /etc/secrets
🔍 Step 5 — describe:    🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜  ~5k tokens · confirmed mount path

⚠️ At production scale (multiple pods × restarts × all resources):
                          🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥  ~20k+ tokens — context explosion risk

🔄 After summarization:  🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜  ~200 tokens — 96% reduction

You MUST include this section with your actual data. Replace the example values with real values from your investigation.

### 📋 Findings
A concise summary of what is broken right now.

### 🔎 Evidence
- bullet list of concrete observations from Kubernetes

### 🎯 Likely Cause
One short paragraph.

### ✅ Recommended Next Action
- safest immediate operator action
- next diagnostic step if confidence is low
- escalation note if needed

## Safety rules

- Default to read-only.
- Do not roll out, restart, patch, delete, cordon, drain, or scale unless the user explicitly asks.
- Do not expose secret values.
- Do not claim a root cause without evidence from status, events, or logs.
- If tools fail, state that clearly.

## Alert parsing hints

Common alert patterns include:
- [FIRING:x] AlertName
- labels: { alertname="...", namespace="...", pod="..." }
- annotations: { summary="...", description="..." }
- inline key=value lines

When the alert includes a runbook URL, mention it in the response but still investigate with live cluster data.

## Preferred investigation examples

- PodCrashLooping:
  inspect pod status, restart count, recent events, current logs, previous logs
- KubeDeploymentReplicasMismatch:
  inspect deployment status, replica counts, related ReplicaSets, pod readiness, events
- KubePodNotReady:
  inspect pod conditions, readiness probes, events, container logs
- KubeNodeNotReady:
  inspect node conditions, recent node events, affected pods if visible
