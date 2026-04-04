#!/bin/bash
##############################################################
# Phase 1 — Setup & Test Prometheus Monitoring
#
# This script:
#   1. Deploys Prometheus + AlertManager to the cluster
#   2. Generates traffic to trigger the bug
#   3. Verifies Prometheus scrapes metrics
#   4. Verifies AlertManager fires the alert
#
# Prerequisites:
#   - kind cluster running (kind create cluster --name mcp-demo)
#   - order-service deployed (kubectl apply -f k8s/deployment.yaml)
#
# Usage:
#   chmod +x scripts/setup-monitoring.sh
#   ./scripts/setup-monitoring.sh
##############################################################

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}${BOLD}  Phase 1: Prometheus + AlertManager Setup${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""

# ── Step 1: Check prerequisites ─────────────────────────
echo -e "${YELLOW}▶ Step 1: Checking prerequisites${NC}"

if ! kubectl cluster-info &>/dev/null; then
    echo -e "${RED}✗ No cluster found. Run: kind create cluster --name mcp-demo${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓ Cluster is running${NC}"

if ! kubectl get namespace demo &>/dev/null; then
    echo -e "${RED}✗ Namespace 'demo' not found. Deploy the order-service first.${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓ Namespace 'demo' exists${NC}"

PODS=$(kubectl get pods -n demo -l app=order-service --no-headers 2>/dev/null | wc -l)
if [ "$PODS" -eq 0 ]; then
    echo -e "${RED}✗ No order-service pods found. Run: kubectl apply -f k8s/deployment.yaml${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓ order-service pods found (${PODS} running)${NC}"
echo ""

# ── Step 2: Deploy monitoring stack ─────────────────────
echo -e "${YELLOW}▶ Step 2: Deploying Prometheus + AlertManager${NC}"

kubectl apply -f k8s/monitoring.yaml
echo -e "  ${GREEN}✓ Monitoring stack deployed${NC}"

echo -e "  ${DIM}Waiting for pods to be ready...${NC}"
kubectl wait --for=condition=ready pod -l app=prometheus -n monitoring --timeout=60s 2>/dev/null || true
kubectl wait --for=condition=ready pod -l app=alertmanager -n monitoring --timeout=60s 2>/dev/null || true

echo ""
kubectl get pods -n monitoring
echo ""

# ── Step 3: Generate traffic to trigger the bug ─────────
echo -e "${YELLOW}▶ Step 3: Generating traffic to trigger alert${NC}"

# Port-forward order-service
kubectl port-forward -n demo svc/order-service 8080:80 &>/dev/null &
PF_PID=$!
sleep 2

echo -e "  ${DIM}Sending 10 requests to /api/orders...${NC}"
for i in $(seq 1 10); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/orders 2>/dev/null || echo "000")
    if [ "$STATUS" = "500" ]; then
        echo -e "  Request $i → ${RED}${STATUS}${NC}"
    else
        echo -e "  Request $i → ${GREEN}${STATUS}${NC}"
    fi
    sleep 0.3
done

kill $PF_PID 2>/dev/null || true
echo ""

# ── Step 4: Check Prometheus is scraping ────────────────
echo -e "${YELLOW}▶ Step 4: Checking Prometheus scrape${NC}"

kubectl port-forward -n monitoring svc/prometheus 9090:9090 &>/dev/null &
PF_PID=$!
sleep 2

# Query Prometheus for our metrics
METRICS=$(curl -s "http://localhost:9090/api/v1/query?query=order_service_failed_requests" 2>/dev/null)
if echo "$METRICS" | grep -q '"result":\[{'; then
    FAILED=$(echo "$METRICS" | grep -o '"value":\[.*\]' | head -1)
    echo -e "  ${GREEN}✓ Prometheus is scraping order-service metrics${NC}"
    echo -e "  ${DIM}  order_service_failed_requests = ${FAILED}${NC}"
else
    echo -e "  ${YELLOW}⚠ Metrics not yet available. This may take 15-30 seconds.${NC}"
    echo -e "  ${DIM}  Check manually: http://localhost:9090/targets${NC}"
fi

# Check targets
TARGETS=$(curl -s "http://localhost:9090/api/v1/targets" 2>/dev/null)
if echo "$TARGETS" | grep -q "order-service"; then
    echo -e "  ${GREEN}✓ order-service target discovered${NC}"
else
    echo -e "  ${YELLOW}⚠ order-service target not yet discovered${NC}"
    echo -e "  ${DIM}  Check: http://localhost:9090/targets${NC}"
fi

kill $PF_PID 2>/dev/null || true
echo ""

# ── Step 5: Check AlertManager ──────────────────────────
echo -e "${YELLOW}▶ Step 5: Checking AlertManager${NC}"

kubectl port-forward -n monitoring svc/alertmanager 9093:9093 &>/dev/null &
PF_PID=$!
sleep 2

ALERTS=$(curl -s "http://localhost:9093/api/v2/alerts" 2>/dev/null)
ALERT_COUNT=$(echo "$ALERTS" | grep -o '"alertname"' | wc -l)

if [ "$ALERT_COUNT" -gt 0 ]; then
    echo -e "  ${RED}🔔 ${ALERT_COUNT} alert(s) firing!${NC}"
    echo "$ALERTS" | python3 -m json.tool 2>/dev/null | head -20
else
    echo -e "  ${YELLOW}⚠ No alerts yet. May need more time (10-30 seconds).${NC}"
    echo -e "  ${DIM}  Alerts fire after 10s of errors. Check: http://localhost:9093${NC}"
fi

kill $PF_PID 2>/dev/null || true
echo ""

# ── Summary ─────────────────────────────────────────────
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}${BOLD}  Phase 1 Summary${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Components deployed:${NC}"
echo -e "    ${GREEN}✓${NC} Prometheus   — scrapes order-service /metrics"
echo -e "    ${GREEN}✓${NC} AlertManager — fires webhook on errors"
echo -e "    ${GREEN}✓${NC} Alert rules  — HighErrorRate + OrdersEndpointDown"
echo ""
echo -e "  ${BOLD}Manual verification:${NC}"
echo -e "    Prometheus UI:    kubectl port-forward -n monitoring svc/prometheus 9090:9090"
echo -e "                      → http://localhost:9090/targets"
echo -e "                      → http://localhost:9090/alerts"
echo -e ""
echo -e "    AlertManager UI:  kubectl port-forward -n monitoring svc/alertmanager 9093:9093"
echo -e "                      → http://localhost:9093"
echo -e ""
echo -e "  ${BOLD}Next steps (Phase 2):${NC}"
echo -e "    → Install OpenClaw"
echo -e "    → Configure mcp-server-kubernetes"
echo -e "    → Build alert bridge webhook"
echo ""