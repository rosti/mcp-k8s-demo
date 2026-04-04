#!/bin/bash
##############################################################
# Traffic simulator for the demo
# Shows the "everything looks fine... until it doesn't" pattern
#
# Usage: ./simulate-traffic.sh [SERVICE_URL]
# Default: http://localhost:8080
##############################################################

SERVICE_URL="${1:-http://localhost:8080}"
TOTAL=0
PASS=0
FAIL=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Order Service — Traffic Simulation         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Phase 1: Health checks — everything looks fine
echo -e "${YELLOW}▶ Phase 1: Health checks (what Kubernetes sees)${NC}"
echo ""
for i in $(seq 1 5); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/healthz" 2>/dev/null)
    echo -e "  /healthz → ${GREEN}${STATUS} OK${NC}  ✓ Pod is healthy"
    sleep 0.3
done
echo ""
echo -e "${GREEN}  Kubernetes says: all pods Ready, all probes passing${NC}"
echo ""
sleep 1

# Phase 2: Real traffic — the failure appears
echo -e "${YELLOW}▶ Phase 2: Real user traffic (what users see)${NC}"
echo ""
for i in $(seq 1 20); do
    TOTAL=$((TOTAL + 1))
    RESPONSE=$(curl -s -w "\n%{http_code}" "$SERVICE_URL/api/orders" 2>/dev/null)
    STATUS=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | head -1)

    if [ "$STATUS" = "200" ]; then
        PASS=$((PASS + 1))
        ORDER_ID=$(echo "$BODY" | grep -o '"order_id":"[^"]*"' | cut -d'"' -f4)
        echo -e "  Request #${TOTAL} → ${GREEN}${STATUS}${NC}  order=${ORDER_ID}"
    else
        FAIL=$((FAIL + 1))
        ERROR=$(echo "$BODY" | grep -o '"message":"[^"]*"' | cut -d'"' -f4)
        echo -e "  Request #${TOTAL} → ${RED}${STATUS}${NC}  error: ${ERROR}"
    fi
    sleep 0.4
done

# Summary
echo ""
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo -e "  Total:  ${TOTAL}"
echo -e "  Pass:   ${GREEN}${PASS}${NC}"
echo -e "  Fail:   ${RED}${FAIL}${NC}"
RATE=$(( FAIL * 100 / TOTAL ))
echo -e "  Error rate: ${RED}${RATE}%${NC}"
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}  100% of /api/orders requests failed${NC}"
    echo -e "${GREEN}  But 100% of /healthz probes passed${NC}"
    echo ""
    echo -e "${YELLOW}This is the gap MCP bridges:${NC}"
    echo -e "  Kubernetes sees    -> healthy pod"
    echo -e "  Users experience   -> broken service"
    echo -e "  The truth lives in -> logs + config + code expectations"
fi
