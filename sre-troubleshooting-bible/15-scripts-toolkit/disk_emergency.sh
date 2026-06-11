#!/bin/bash
# Disk Emergency - Find and safely remove large files to buy time
#
# Usage: ./disk_emergency.sh [threshold_pct] [--clean]
#   threshold_pct: Alert when any filesystem usage exceeds this percentage (default: 90)
#   --clean:      Actually perform cleanup (without this flag, dry-run only)
#
# Exit codes:
#   0 - Disk usage below threshold
#   1 - One or more filesystems above threshold
#   2 - Invalid arguments or permission denied

set -euo pipefail

THRESHOLD="${1:-90}"
CLEAN_MODE=false
if [[ "${2:-}" == "--clean" ]]; then
    CLEAN_MODE=true
fi

# Validate threshold is a number
if ! [[ "$THRESHOLD" =~ ^[0-9]+$ ]] || [ "$THRESHOLD" -lt 1 ] || [ "$THRESHOLD" -gt 100 ]; then
    echo "ERROR: Threshold must be between 1 and 100" >&2
    exit 2
fi

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

ALERT=false

echo -e "${BOLD}Disk Emergency Scanner${NC}"
echo "========================="
echo "Threshold: ${THRESHOLD}% | Mode: $([ "$CLEAN_MODE" = true ] && echo 'CLEAN (actions will execute)' || echo 'DRY-RUN (no changes)')"
echo ""

# ──────────────────────────────────────
# 1. Show current disk usage (exclude tmpfs/devtmpfs)
# ──────────────────────────────────────
echo -e "${BOLD}Current Disk Usage:${NC}"
df -h | grep -vE '^Filesystem|tmpfs|devtmpfs|snapfuse|udev|/dev/loop' || true

echo ""
echo -e "${BOLD}Filesystems Above Threshold:${NC}"
ALERT_FS=()
while IFS= read -r line; do
    usage_pct=$(echo "$line" | awk '{print $5}' | sed 's/%//')
    mount=$(echo "$line" | awk '{print $6}')
    if [ -n "$usage_pct" ] && [ "$usage_pct" -ge "$THRESHOLD" ] 2>/dev/null; then
        ALERT_FS+=("$mount")
        echo -e "  ${RED}${mount} → ${usage_pct}%${NC} (THRESHOLD: ${THRESHOLD}%)"
        ALERT=true
    fi
done < <(df | grep -vE '^Filesystem|tmpfs|devtmpfs|snapfuse|udev|/dev/loop')

if [ ${#ALERT_FS[@]} -eq 0 ]; then
    echo -e "  ${GREEN}All filesystems below threshold.${NC}"
fi

# ──────────────────────────────────────
# 2. Top 10 largest directories
# ──────────────────────────────────────
echo ""
echo -e "${BOLD}Top 10 Largest Directories (root level):${NC}"
if [ "$(id -u)" -eq 0 ] || [ -r / ]; then
    du -sh /* 2>/dev/null | sort -rh | head -10 || echo "  (could not scan root directories)"
else
    echo "  (not running as root — scanning common writable directories)"
    for d in /var/log /var/lib /tmp /home /opt /usr/local; do
        [ -d "$d" ] && du -sh "$d" 2>/dev/null || true
    done | sort -rh | head -10
fi

# ──────────────────────────────────────
# 3. Inode usage (can be full even with free space)
# ──────────────────────────────────────
echo ""
echo -e "${BOLD}Inode Usage (filesystem can be full if inodes exhausted):${NC}"
df -i | grep -vE '^Filesystem|tmpfs|devtmpfs' || true

# Check for high inode usage
while IFS= read -r line; do
    iuse=$(echo "$line" | awk '{print $5}' | sed 's/%//')
    mount=$(echo "$line" | awk '{print $6}')
    if [ -n "$iuse" ] && [ "$iuse" -ge "$THRESHOLD" ] 2>/dev/null; then
        echo -e "  ${YELLOW}WARNING: ${mount} inodes at ${iuse}%${NC}"
        # Check for directories with huge numbers of files
        echo "  Largest directories by file count on ${mount}:"
        find "$mount" -xdev -type d -maxdepth 4 2>/dev/null | while read -r dir; do
            count=$(find "$dir" -maxdepth 1 -type f 2>/dev/null | wc -l)
            [ "$count" -gt 1000 ] && echo "    ${count} files → ${dir}"
        done | sort -t' ' -k1 -rn | head -5
    fi
done < <(df -i | grep -vE '^Filesystem|tmpfs|devtmpfs')

# ──────────────────────────────────────
# 4. Find deleted files still held open (lsof +L1)
# ──────────────────────────────────────
echo ""
echo -e "${BOLD}Deleted Files Still Held Open (space freed by restarting process):${NC}"
if command -v lsof &>/dev/null; then
    OPEN_DELETED=$(lsof +L1 2>/dev/null | awk 'NR>1 {sum+=$7} END {printf "%.1f MB", sum/1024/1024}')
    echo "  Total space recoverable: $OPEN_DELETED"
    lsof +L1 2>/dev/null | awk 'NR>1 {printf "  PID %-8s %-20s %10.1f MB  %s\n", $2, $1, $7/1024/1024, $9}' | sort -k4 -rn | head -15 || echo "  (none found)"
else
    echo "  (lsof not available — install with: apt-get install lsof / brew install lsof)"
fi

# ──────────────────────────────────────
# 5. Large log files and temporary files
# ──────────────────────────────────────
echo ""
echo -e "${BOLD}Large Candidate Files for Cleanup (>100MB, modified >7 days ago):${NC}"

CLEANUP_TARGETS=()
for base_dir in /var/log /tmp /var/tmp /var/lib/docker/containers /var/cache; do
    [ ! -d "$base_dir" ] && continue
    while IFS= read -r -d '' file; do
        size_mb=$(du -m "$file" 2>/dev/null | cut -f1)
        mtime=$(stat -f "%Sm" -t "%Y-%m-%d" "$file" 2>/dev/null || stat -c "%y" "$file" 2>/dev/null | cut -d' ' -f1)
        echo "  ${size_mb}MB  ${mtime}  ${file}"
        CLEANUP_TARGETS+=("$file")
    done < <(find "$base_dir" -xdev -type f -size +100M -mtime +7 2>/dev/null | head -20 | tr '\n' '\0')
done

if [ ${#CLEANUP_TARGETS[@]} -eq 0 ]; then
    echo "  (no large old files found in standard locations)"
fi

# ──────────────────────────────────────
# 6. Docker disk usage (if Docker is available)
# ──────────────────────────────────────
if command -v docker &>/dev/null; then
    echo ""
    echo -e "${BOLD}Docker Disk Usage:${NC}"
    docker system df 2>/dev/null || echo "  (Docker not accessible)"

    # Show large dangling images
    echo ""
    echo -e "${BOLD}Large Docker Images (>500MB):${NC}"
    docker images --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}" 2>/dev/null | sort -k2 -h | tail -10 || true
fi

# ──────────────────────────────────────
# 7. journald logs (if systemd)
# ──────────────────────────────────────
if command -v journalctl &>/dev/null; then
    echo ""
    echo -e "${BOLD}Journald Log Disk Usage:${NC}"
    journalctl --disk-usage 2>/dev/null || echo "  (journalctl not available)"
fi

# ──────────────────────────────────────
# 8. Suggested Cleanup Commands
# ──────────────────────────────────────
echo ""
echo -e "${BOLD}Recommended Cleanup Commands:${NC}"
echo ""

# Always show suggested commands, mark with * if they'd actually help
cmds=(
    "sudo journalctl --vacuum-size=500M                                   # Limit journald logs to 500MB"
    "sudo docker system prune -af --volumes                               # Remove all unused Docker data"
    "sudo docker builder prune -af --filter 'until=72h'                  # Remove old build cache"
    "sudo apt-get clean && sudo apt-get autoremove --purge -y             # Clean apt package cache (Debian/Ubuntu)"
    "sudo yum clean all && sudo dnf autoremove -y                         # Clean yum/dnf package cache (RHEL/Fedora)"
    "sudo find /tmp -xdev -type f -mtime +1 -delete                       # Delete temp files older than 1 day"
    "sudo find /var/log -xdev -type f -name '*.log.*' -mtime +30 -delete # Delete rotated logs older than 30 days"
    "sudo find /var/log -xdev -type f -name '*.gz' -mtime +7 -delete     # Delete compressed logs older than 7 days"
    "sudo truncate -s 0 /var/log/syslog /var/log/messages                 # Truncate large active log files"
    "sudo npm cache clean --force && sudo yarn cache clean                # Clean Node.js package caches"
    "pip cache purge                                                     # Clean Python pip cache"
    "sudo find /var/cache -xdev -type f -mtime +30 -delete                # Delete old cached files"
)

for cmd in "${cmds[@]}"; do
    echo "  ${cmd}"
    echo ""
done

# ──────────────────────────────────────
# 9. Execute cleanup if requested
# ──────────────────────────────────────
if [ "$CLEAN_MODE" = true ] && [ "$ALERT" = true ]; then
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  CLEAN MODE ACTIVE — Executing safe cleanup operations...    ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo "→ Vacuuming journald logs to 500MB..."
    sudo journalctl --vacuum-size=500M 2>/dev/null || echo "  (journalctl not available or failed)"

    echo "→ Pruning Docker system (unused containers, images, networks)..."
    docker system prune -af 2>/dev/null || echo "  (Docker not available)"

    echo "→ Deleting files from /tmp older than 1 day..."
    sudo find /tmp -xdev -type f -mtime +1 -delete 2>/dev/null || echo "  (none or permission denied)"

    echo "→ Deleting rotated log files older than 30 days..."
    sudo find /var/log -xdev -type f \( -name '*.log.*' -o -name '*.gz' \) -mtime +30 -delete 2>/dev/null || echo "  (none or permission denied)"

    echo ""
    echo -e "${GREEN}Cleanup operations complete.${NC}"

    # Show disk usage after cleanup
    echo ""
    echo -e "${BOLD}Disk Usage After Cleanup:${NC}"
    df -h | grep -vE '^Filesystem|tmpfs|devtmpfs|snapfuse|udev|/dev/loop' || true
fi

# ──────────────────────────────────────
# Final summary
# ──────────────────────────────────────
echo ""
echo "========================="
if [ "$ALERT" = true ]; then
    echo -e "${RED}ACTION REQUIRED: ${#ALERT_FS[@]} filesystem(s) above ${THRESHOLD}% threshold${NC}"
    echo ""
    echo "Affected mounts:"
    for fs in "${ALERT_FS[@]}"; do
        echo "  - $fs"
    done
    echo ""
    if [ "$CLEAN_MODE" = false ]; then
        echo "Run with --clean to execute safe cleanup operations automatically."
        echo "  $0 $THRESHOLD --clean"
    fi
    exit 1
else
    echo -e "${GREEN}All filesystems below ${THRESHOLD}% threshold.${NC}"
    exit 0
fi
