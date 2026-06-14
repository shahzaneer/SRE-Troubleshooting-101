#!/bin/bash
# K8s Pod Debugger — Interactive pod inspection
#
# Usage: ./k8s_pod_debug.sh <pod-name> [namespace] [--previous] [--exec]
#   pod-name:     Name of the pod to debug
#   namespace:    Kubernetes namespace (default: "default")
#   --previous:   Show logs from previous (crashed) container instance
#   --exec:       Open an interactive shell in the pod (if possible)
#
# Exit codes:
#   0 - Debugging complete
#   1 - Pod not found or in crash loop
#   2 - kubectl not found or invalid args

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <pod-name> [namespace] [--previous] [--exec]"
    echo ""
    echo "Examples:"
    echo "  $0 api-deployment-7d8f9-abcde"
    echo "  $0 api-deployment-7d8f9-abcde production"
    echo "  $0 api-deployment-7d8f9-abcde production --previous"
    echo "  $0 api-deployment-7d8f9-abcde production --exec"
    exit 2
fi

# Check for kubectl
if ! command -v kubectl &>/dev/null; then
    echo "ERROR: kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/" >&2
    exit 2
fi

# Check for jq (optional, used for JSON parsing)
HAS_JQ=false
command -v jq &>/dev/null && HAS_JQ=true

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

POD="$1"
NAMESPACE="default"
SHOW_PREVIOUS=false
EXEC_SHELL=false

shift
while [ $# -gt 0 ]; do
    case "$1" in
        --previous) SHOW_PREVIOUS=true ;;
        --exec) EXEC_SHELL=true ;;
        --*) echo "Unknown option: $1" >&2; exit 2 ;;
        *) NAMESPACE="$1" ;;
    esac
    shift
done

echo -e "${BOLD}Debugging pod: ${CYAN}${POD}${NC} ${BOLD}(namespace: ${CYAN}${NAMESPACE}${NC})${NC}"
echo "================================================================"

# ──────────────────────────────────────
# 1. Pod Status
# ──────────────────────────────────────
echo -e "\n${BOLD}Pod Status:${NC}"
if ! kubectl get pod "$POD" -n "$NAMESPACE" -o wide 2>/dev/null; then
    echo -e "${RED}Pod not found!${NC}"
    echo ""
    echo "Similar pods in namespace '${NAMESPACE}':"
    kubectl get pods -n "$NAMESPACE" 2>/dev/null | head -20 || echo "  (cannot list pods)"
    exit 1
fi

# ──────────────────────────────────────
# 2. Pod Phase, Conditions, Container Status summary
# ──────────────────────────────────────
echo -e "\n${BOLD}Quick Health Overview:${NC}"
POD_JSON=$(kubectl get pod "$POD" -n "$NAMESPACE" -o json 2>/dev/null)

if [ -n "$POD_JSON" ] && [ "$HAS_JQ" = true ]; then
    # Phase and reason
    PHASE=$(echo "$POD_JSON" | jq -r '.status.phase // "Unknown"')
    REASON=$(echo "$POD_JSON" | jq -r '.status.reason // "N/A"')
    MESSAGE=$(echo "$POD_JSON" | jq -r '.status.message // ""')

    if [ "$PHASE" = "Running" ]; then
        echo -e "  Phase: ${GREEN}${PHASE}${NC}"
    elif [ "$PHASE" = "Pending" ]; then
        echo -e "  Phase: ${YELLOW}${PHASE}${NC}  Reason: ${REASON}  ${MESSAGE}"
    else
        echo -e "  Phase: ${RED}${PHASE}${NC}  Reason: ${REASON}  ${MESSAGE}"
    fi

    # Container statuses
    echo ""
    echo "  Container Status:"
    echo "$POD_JSON" | jq -r '.status.containerStatuses[]? | 
        "    \(.name): \(.state | keys[0]) | Ready: \(.ready) | Restarts: \(.restartCount) | Image: \(.image)"' 2>/dev/null

    # Init containers
    INIT_COUNT=$(echo "$POD_JSON" | jq '.status.initContainerStatuses | length' 2>/dev/null || echo 0)
    if [ "$INIT_COUNT" -gt 0 ]; then
        echo "  Init Container Status:"
        echo "$POD_JSON" | jq -r '.status.initContainerStatuses[]? |
            "    \(.name): \(.state | keys[0]) | Ready: \(.ready) | Restarts: \(.restartCount)"' 2>/dev/null
    fi

    # Conditions
    echo ""
    echo "  Conditions:"
    echo "$POD_JSON" | jq -r '.status.conditions[]? | "    \(.type): \(.status) (Reason: \(.reason // "N/A"))"' 2>/dev/null

    # Pod IP and Node
    POD_IP=$(echo "$POD_JSON" | jq -r '.status.podIP // "N/A"')
    NODE=$(echo "$POD_JSON" | jq -r '.spec.nodeName // "N/A"')
    SERVICE_ACCOUNT=$(echo "$POD_JSON" | jq -r '.spec.serviceAccountName // "N/A"')
    echo ""
    echo "  Pod IP: ${POD_IP} | Node: ${NODE} | ServiceAccount: ${SERVICE_ACCOUNT}"

else
    echo "  (jq not available for structured output — showing kubectl describe)"
fi

# ──────────────────────────────────────
# 3. Pod Description (events and failure details)
# ──────────────────────────────────────
echo -e "\n${BOLD}Recent Events (last 20):${NC}"
kubectl get events -n "$NAMESPACE" \
    --field-selector involvedObject.name="$POD" \
    --sort-by='.lastTimestamp' 2>/dev/null | tail -20 || echo "  (no events found)"

# ──────────────────────────────────────
# 4. Describe pod (last 60 lines for debugging)
# ──────────────────────────────────────
echo -e "\n${BOLD}Pod Describe (last 60 lines):${NC}"
kubectl describe pod "$POD" -n "$NAMESPACE" 2>/dev/null | tail -60

# ──────────────────────────────────────
# 5. Image pull errors (common failure mode)
# ──────────────────────────────────────
echo -e "\n${BOLD}Checking for Image Pull Errors:${NC}"
PULL_ERRORS=$(kubectl describe pod "$POD" -n "$NAMESPACE" 2>/dev/null | grep -i "Failed to pull image\|ErrImagePull\|ImagePullBackOff\|unauthorized\|manifest unknown" || true)
if [ -n "$PULL_ERRORS" ]; then
    echo -e "  ${RED}$PULL_ERRORS${NC}"
else
    echo -e "  ${GREEN}No image pull errors detected.${NC}"
fi

# ──────────────────────────────────────
# 6. Resource constraints (OOMKilled, CPU throttling)
# ──────────────────────────────────────
echo -e "\n${BOLD}Checking for Resource Issues:${NC}"
OOM=$(kubectl describe pod "$POD" -n "$NAMESPACE" 2>/dev/null | grep -i "OOMKilled" || true)
if [ -n "$OOM" ]; then
    echo -e "  ${RED}OOMKilled detected! Pod was killed due to memory limit.${NC}"
    echo "  Review memory limits/requests and application memory usage."
else
    echo -e "  ${GREEN}No OOMKilled events.${NC}"
fi

# ──────────────────────────────────────
# 7. Logs
# ──────────────────────────────────────
echo -e "\n${BOLD}Container Logs (last 75 lines):${NC}"

if [ "$SHOW_PREVIOUS" = true ]; then
    echo -e "  ${YELLOW}(Showing previous container instance — after a crash)${NC}"
    if kubectl logs "$POD" -n "$NAMESPACE" --previous --tail=75 2>/dev/null; then
        : # Logs shown
    else
        echo "  No previous container logs available."
    fi
else
    # Try to get logs, handle multi-container pods
    CONTAINERS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.spec.containers[*].name}' 2>/dev/null)
    CONTAINER_COUNT=$(echo "$CONTAINERS" | wc -w | tr -d ' ')

    if [ "$CONTAINER_COUNT" -gt 1 ]; then
        echo "  (Multi-container pod — showing logs for each container)"
        for container in $CONTAINERS; do
            echo -e "\n  ${CYAN}--- Container: ${container} ---${NC}"
            kubectl logs "$POD" -n "$NAMESPACE" -c "$container" --tail=50 2>/dev/null || echo "  No logs for ${container}"
        done
    else
        kubectl logs "$POD" -n "$NAMESPACE" --tail=75 2>/dev/null || echo "  No logs available."

        # If no current logs, check previous (crash loop)
        CRASH_COUNT=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null)
        if [ "${CRASH_COUNT:-0}" -gt 2 ]; then
            echo -e "\n  ${YELLOW}Pod restarted ${CRASH_COUNT} times. Showing previous logs:${NC}"
            kubectl logs "$POD" -n "$NAMESPACE" --previous --tail=50 2>/dev/null || echo "  No previous logs."
        fi
    fi
fi

# ──────────────────────────────────────
# 8. Resource usage (metrics-server required)
# ──────────────────────────────────────
echo -e "\n${BOLD}Resource Usage:${NC}"
if kubectl top pod "$POD" -n "$NAMESPACE" 2>/dev/null; then
    : # Metrics shown
else
    echo "  Metrics not available. Install metrics-server:"
    echo "    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml"
fi

# ──────────────────────────────────────
# 9. Referenced ConfigMaps and Secrets
# ──────────────────────────────────────
echo -e "\n${BOLD}Configuration References:${NC}"

if [ "$HAS_JQ" = true ] && [ -n "$POD_JSON" ]; then
    # ConfigMaps
    CONFIGMAPS=$(echo "$POD_JSON" | jq -r '[.spec.volumes[]? | select(.configMap) | .configMap.name] | unique | .[]' 2>/dev/null || true)
    if [ -n "$CONFIGMAPS" ]; then
        echo "  ConfigMaps:"
        echo "$CONFIGMAPS" | while read -r cm; do
            echo "    - $cm"
        done
    else
        echo "  ConfigMaps: (none)"
    fi

    # Secrets
    SECRETS=$(echo "$POD_JSON" | jq -r '[.spec.volumes[]? | select(.secret) | .secret.secretName] | unique | .[]' 2>/dev/null || true)
    if [ -n "$SECRETS" ]; then
        echo "  Secrets:"
        echo "$SECRETS" | while read -r secret; do
            echo "    - $secret"
        done
    else
        echo "  Secrets: (none)"
    fi

    # EnvFrom references
    ENVFROM_CM=$(echo "$POD_JSON" | jq -r '[.spec.containers[].envFrom[]? | select(.configMapRef) | .configMapRef.name] | unique | .[]' 2>/dev/null || true)
    ENVFROM_SECRET=$(echo "$POD_JSON" | jq -r '[.spec.containers[].envFrom[]? | select(.secretRef) | .secretRef.name] | unique | .[]' 2>/dev/null || true)
    if [ -n "$ENVFROM_CM" ]; then
        echo "  EnvFrom ConfigMaps:"
        echo "$ENVFROM_CM" | while read -r cm; do echo "    - $cm"; done
    fi
    if [ -n "$ENVFROM_SECRET" ]; then
        echo "  EnvFrom Secrets:"
        echo "$ENVFROM_SECRET" | while read -r s; do echo "    - $s"; done
    fi
else
    echo "  (jq not available — manual inspection needed)"
fi

# ──────────────────────────────────────
# 10. Network policies and service bindings
# ──────────────────────────────────────
echo -e "\n${BOLD}Network & Service Info:${NC}"

# Labels (useful for finding matching services)
LABELS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.metadata.labels}' 2>/dev/null | jq -r 'to_entries | map("\(.key)=\(.value)") | join(", ")' 2>/dev/null || echo "N/A")
echo "  Labels: $LABELS"

# Find services matching pod labels (simplified)
echo "  Matching Services (by app label):"
APP_LABEL=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.metadata.labels.app}' 2>/dev/null || true)
if [ -n "$APP_LABEL" ]; then
    kubectl get svc -n "$NAMESPACE" -l "app=${APP_LABEL}" 2>/dev/null | tail -n +2 || echo "    (none found)"
fi

# ──────────────────────────────────────
# 11. Interactive shell (if --exec)
# ──────────────────────────────────────
if [ "$EXEC_SHELL" = true ]; then
    echo -e "\n${BOLD}Opening Interactive Shell...${NC}"
    echo -e "  ${YELLOW}Type 'exit' to leave the pod shell.${NC}"
    echo ""

    # Determine shell availability
    SHELL_CMD="sh"
    if kubectl exec "$POD" -n "$NAMESPACE" -- which bash &>/dev/null 2>&1; then
        SHELL_CMD="bash"
    fi

    kubectl exec -it "$POD" -n "$NAMESPACE" -- "$SHELL_CMD" || echo -e "  ${RED}Failed to open shell (pod may not have sh/bash, or may be in CrashLoopBackOff).${NC}"
fi

echo ""
echo "================================================================"
echo -e "${BOLD}Debugging complete.${NC}"

# Final diagnostic hint
POD_STATUS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null)
if [ "$POD_STATUS" != "Running" ]; then
    echo ""
    echo -e "${YELLOW}Pod is not running (status: ${POD_STATUS}). Common causes:${NC}"
    echo "  - Pending:   Insufficient resources, PVC not bound, node selector/affinity mismatch"
    echo "  - CrashLoopBackOff: Application exits immediately, check logs above"
    echo "  - ImagePullBackOff: Wrong image name/tag, ECR/Docker Hub auth issue"
    echo "  - ErrImagePull: Image does not exist or registry unreachable"
fi

exit 0
