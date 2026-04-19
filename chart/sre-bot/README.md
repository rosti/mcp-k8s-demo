# sre-bot Helm Chart

This chart packages the existing `OpenClawInstance`, PVC, and skill ConfigMap from this repository into a Helm release.

The ConfigMap is fixed to `k8s-alert-runbook-skill`, its `SKILL.md` content is sourced from `chart/sre-bot/files/SKILL.md`, the Discord `systemPrompt` is sourced from `chart/sre-bot/files/system_prompt.md`, and the workspace `SOUL.md` and `AGENT.md` files are sourced from `chart/sre-bot/files/`.

Discord user allowlists are configured once under `.Values.discord.users` and reused for both the guild and channel entries in the rendered `OpenClawInstance`.

## Prerequisites

- The `OpenClawInstance` CRD and its controller must already be installed.
- The target namespace must contain the `kubeconfig-secret` and `openclaw-secrets` secrets referenced by the custom resource.

## Install

```bash
helm upgrade --install sre-bot ./chart/sre-bot -n openclaw --create-namespace
```

## Customize

Override defaults with a custom values file or `--set`, for example:

```bash
helm upgrade --install sre-bot ./chart/sre-bot \
  -n openclaw \
  --create-namespace \
  -f my-values.yaml
```
