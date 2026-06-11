#!/bin/bash
# TCP Connection Audit - Show all connections grouped by state
#
# Usage: ./tcp_connection_audit.sh [--watch] [--interval 5]
#   --watch:    Continuous monitoring mode (Ctrl+C to exit)
#   --interval: Refresh interval in seconds (default: 5)
#
# Exit codes:
#   0 - Audit complete
#   1 - Potential connection leaks detected (CLOSE_WAIT or excess TIME_WAIT)
#   2 - Required tools missing

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

WATCH_MODE=false
INTERVAL=5
POTENTIAL_LEAK=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --watch) WATCH_MODE=true ;;
        --interval) INTERVAL="${2:-5}"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# Check for required tools
if ! command -v ss &>/dev/null; then
    echo "ERROR: 'ss' command not found. Install: apt-get install iproute2" >&2
    exit 2
fi

audit() {
    local leak=false

    # Clear screen in watch mode
    [ "$WATCH_MODE" = true ] && clear

    echo -e "${BOLD}TCP Connection Audit — $(date '+%Y-%m-%d %H:%M:%S')${NC}"
    echo "========================================="

    # ──────────────────────────────────────
    # 1. Connection summary (ss -s)
    # ──────────────────────────────────────
    echo -e "\n${BOLD}Connection Summary:${NC}"
    ss -s

    # ──────────────────────────────────────
    # 2. Connection counts by state with thresholds
    # ──────────────────────────────────────
    TIME_WAIT=$(ss -tan state time-wait 2>/dev/null | wc -l | tr -d ' ')
    CLOSE_WAIT=$(ss -tan state close-wait 2>/dev/null | wc -l | tr -d ' ')
    ESTABLISHED=$(ss -tan state established 2>/dev/null | wc -l | tr -d ' ')
    SYN_SENT=$(ss -tan state syn-sent 2>/dev/null | wc -l | tr -d ' ')
    LAST_ACK=$(ss -tan state last-ack 2>/dev/null | wc -l | tr -d ' ')
    FIN_WAIT=$(ss -tan state fin-wait-1 state fin-wait-2 2>/dev/null | wc -l | tr -d ' ')
    LISTEN=$(ss -tln state listening 2>/dev/null | wc -l | tr -d ' ')

    # Subtract header lines
    [ "$TIME_WAIT" -ge 1 ] && TIME_WAIT=$((TIME_WAIT - 1))
    [ "$CLOSE_WAIT" -ge 1 ] && CLOSE_WAIT=$((CLOSE_WAIT - 1))
    [ "$ESTABLISHED" -ge 1 ] && ESTABLISHED=$((ESTABLISHED - 1))
    [ "$SYN_SENT" -ge 1 ] && SYN_SENT=$((SYN_SENT - 1))
    [ "$LAST_ACK" -ge 1 ] && LAST_ACK=$((LAST_ACK - 1))
    [ "$FIN_WAIT" -ge 2 ] && FIN_WAIT=$((FIN_WAIT - 2))  # Two state filters = 2 header lines
    [ "$LISTEN" -ge 1 ] && LISTEN=$((LISTEN - 1))

    echo -e "\n${BOLD}Connection State Counts:${NC}"
    echo -e "  ESTABLISHED:  ${GREEN}${ESTABLISHED}${NC}"

    # TIME_WAIT > 5000 is problematic
    if [ "$TIME_WAIT" -gt 5000 ]; then
        echo -e "  TIME_WAIT:    ${RED}${TIME_WAIT} (HIGH — potential performance issue)${NC}"
        leak=true
    elif [ "$TIME_WAIT" -gt 2000 ]; then
        echo -e "  TIME_WAIT:    ${YELLOW}${TIME_WAIT} (elevated)${NC}"
    else
        echo -e "  TIME_WAIT:    ${TIME_WAIT}"
    fi

    # CLOSE_WAIT > 50 usually indicates a connection leak
    if [ "$CLOSE_WAIT" -gt 100 ]; then
        echo -e "  CLOSE_WAIT:   ${RED}${CLOSE_WAIT} (CRITICAL — connection leak detected!)${NC}"
        leak=true
    elif [ "$CLOSE_WAIT" -gt 50 ]; then
        echo -e "  CLOSE_WAIT:   ${YELLOW}${CLOSE_WAIT} (elevated — possible leak)${NC}"
        leak=true
    elif [ "$CLOSE_WAIT" -gt 0 ]; then
        echo -e "  CLOSE_WAIT:   ${YELLOW}${CLOSE_WAIT}${NC}"
    else
        echo -e "  CLOSE_WAIT:   ${GREEN}${CLOSE_WAIT}${NC}"
    fi

    echo -e "  SYN_SENT:     ${SYN_SENT}"
    echo -e "  LAST_ACK:     ${LAST_ACK}"
    echo -e "  FIN_WAIT:     ${FIN_WAIT}"
    echo -e "  LISTEN:       ${LISTEN}"

    # ──────────────────────────────────────
    # 3. Top remote IPs for ESTABLISHED connections
    # ──────────────────────────────────────
    echo -e "\n${BOLD}Top 15 Remote IPs (ESTABLISHED):${NC}"
    ss -tan state established | awk 'NR>1 {print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -15 | while read -r count ip; do
        printf "  %-6s %s\n" "$count" "$ip"
    done

    # ──────────────────────────────────────
    # 4. CLOSE_WAIT detail — show implicated remote IPs and listen ports
    # ──────────────────────────────────────
    if [ "$CLOSE_WAIT" -gt 0 ]; then
        echo -e "\n${BOLD}CLOSE_WAIT Connections (potential leaks):${NC}"
        ss -tan state close-wait | awk 'NR>1 {printf "  RecvQ: %-8s SendQ: %-8s Local: %-30s Remote: %s\n", $2, $3, $4, $5}' | head -20

        echo -e "\n${BOLD}CLOSE_WAIT by Remote IP:${NC}"
        ss -tan state close-wait | awk 'NR>1 {print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -10 | while read -r count ip; do
            printf "  %-6s %s\n" "$count" "$ip"
        done

        echo -e "\n${BOLD}CLOSE_WAIT by Local Port (which services are affected?):${NC}"
        ss -tan state close-wait | awk 'NR>1 {print $4}' | awk -F: '{print $NF}' | sort | uniq -c | sort -rn | head -10 | while read -r count port; do
            # Try to map port to service name
            service_name=$(awk -v p="$port" '$2==p{print $1; exit}' /etc/services 2>/dev/null || echo "unknown")
            printf "  %-6s port %-6s (%s)\n" "$count" "$port" "$service_name"
        done
    fi

    # ──────────────────────────────────────
    # 5. Listening services
    # ──────────────────────────────────────
    echo -e "\n${BOLD}Listening TCP Services:${NC}"
    ss -tlnp | while IFS= read -r line; do
        if [[ "$line" == "State"* ]]; then
            echo "  $line"
        else
            # Highlight non-standard ports
            port=$(echo "$line" | awk -F: '{print $NF}' | awk '{print $1}')
            if [ "$port" -gt 1024 ] 2>/dev/null; then
                echo -e "  ${YELLOW}$line${NC}"
            else
                echo "  $line"
            fi
        fi
    done

    # ──────────────────────────────────────
    # 6. TIME_WAIT tuning recommendations
    # ──────────────────────────────────────
    if [ "$TIME_WAIT" -gt 2000 ]; then
        echo -e "\n${BOLD}TIME_WAIT Tuning:${NC}"
        echo "  Current kernel settings:"
        sysctl net.ipv4.tcp_tw_reuse 2>/dev/null || echo "    (not available on this OS)"
        sysctl net.ipv4.tcp_fin_timeout 2>/dev/null || echo "    (not available on this OS)"
        echo ""
        echo "  To reduce TIME_WAIT connections:"
        echo "    sudo sysctl -w net.ipv4.tcp_tw_reuse=1"
        echo "    sudo sysctl -w net.ipv4.tcp_fin_timeout=15   # Default: 60s"
    fi

    # ──────────────────────────────────────
    # 7. CLOSE_WAIT debug recommendations
    # ──────────────────────────────────────
    if [ "$CLOSE_WAIT" -gt 50 ]; then
        echo -e "\n${BOLD}CLOSE_WAIT Leak Debugging:${NC}"
        echo "  CLOSE_WAIT means the remote side sent FIN, but the local application"
        echo "  has NOT called close() on the socket. This is a BUG in the application."
        echo ""
        echo "  To find the responsible process:"
        echo "    sudo ss -tanp state close-wait | grep -v '127.0.0.1'"
        echo ""
        echo "  Or using lsof:"
        echo "    sudo lsof -i TCP -s TCP:CLOSE_WAIT"
        echo ""
        echo "  Common causes:"
        echo "    • Application NOT reading from socket (threadpool exhausted)"
        echo "    • Application calls shutdown() instead of close()"
        echo "    • Connection pool never returns connections (pool leak)"
        echo "    • HTTP client without connection/read timeout"
    fi

    # ──────────────────────────────────────
    # 8. Conntrack table usage (if available)
    # ──────────────────────────────────────
    if command -v conntrack &>/dev/null; then
        CONNTRACK_COUNT=$(conntrack -C 2>/dev/null || echo "N/A")
        CONNTRACK_MAX=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo "N/A")
        echo -e "\n${BOLD}Conntrack Table:${NC}"
        if [ "$CONNTRACK_MAX" != "N/A" ] && [ "$CONNTRACK_COUNT" != "N/A" ]; then
            PCT=$((CONNTRACK_COUNT * 100 / CONNTRACK_MAX))
            if [ "$PCT" -gt 80 ]; then
                echo -e "  ${RED}${CONNTRACK_COUNT}/${CONNTRACK_MAX} (${PCT}%) — TABLE NEAR FULL${NC}"
            elif [ "$PCT" -gt 50 ]; then
                echo -e "  ${YELLOW}${CONNTRACK_COUNT}/${CONNTRACK_MAX} (${PCT}%)${NC}"
            else
                echo -e "  ${GREEN}${CONNTRACK_COUNT}/${CONNTRACK_MAX} (${PCT}%)${NC}"
            fi
        else
            echo "  Not available"
        fi
    fi

    echo ""
    echo "========================================="

    POTENTIAL_LEAK=$leak
}

# Main execution
if [ "$WATCH_MODE" = true ]; then
    echo "Running in watch mode (${INTERVAL}s interval). Press Ctrl+C to exit."
    while true; do
        audit
        sleep "$INTERVAL"
    done
else
    audit
fi

if [ "$POTENTIAL_LEAK" = true ]; then
    echo -e "${RED}WARNING: Potential connection issues detected!${NC}" >&2
    exit 1
fi

exit 0
