# Chaos Engineering
> **Category:** 10x SRE | Chaos | Resilience
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#chaos` `#resilience` `#netflix` `#10x`

---

## The Netflix Model — Principles of Chaos Engineering

Netflix created Chaos Engineering as a discipline. Their principles form the foundation of all modern chaos practices.

### 1. Define "Steady State"

Before you break anything, you MUST know what "normal" looks like. Chaos experiments without a baseline are just vandalism.

```promql
# Steady state metrics (record for at least 1 hour before experiment)
# These are your canary — if they deviate, the experiment is "interesting"

rate(http_requests_total{service="order"}[5m])    # Throughput: ~500 RPS
histogram_quantile(0.95, ...)                      # p95 latency: 45ms
sum(rate(http_requests_total{status="500"}[5m]))   # Error rate: 0.01%

# System health:
cpu_utilization_percent                            # ~35%
memory_used_percent                                # ~55%
hikaricp_connections_active                        # ~8 of 20
```

**Steady state is NOT "no errors."** It's "the normal pattern of metrics." Normal includes: 0.01% sporadic 500s, periodic GC pauses, occasional connection pool recycling. If your "steady state" assumes zero errors, your baseline is wrong.

### 2. Hypothesize

State explicitly what you expect to happen. Write it down BEFORE the experiment.

```
Example hypothesis:
  "If Redis becomes unavailable, the checkout service will:
   1. Detect Redis failure within 3 seconds (health check timeout)
   2. Return 200 with X-Cache: STALE header (serving from local cache)
   3. Error rate will NOT increase (no 500s)
   4. Latency p95 will increase from 45ms to 60ms (local cache is slower)
   5. NOT crash or restart"
```

**If you can't write the hypothesis, you don't understand your system well enough to do chaos engineering.**

### 3. Vary Real-World Events

Inject events that actually happen in production — not exotic, unlikely scenarios.

```
Realistic events (do these first):
  ✓ Instance killed (spot instance reclaim, OOM, node failure)
  ✓ Network latency increase (cross-AZ, network congestion)
  ✓ DNS failure (Route 53 outage, bad caching)
  ✓ Disk full (logs filled the volume)
  ✓ DB connection pool exhaustion (config drift, traffic spike)

Unrealistic events (skip these):
  ✗ Entire region disappears simultaneously (you have bigger problems)
  ✗ 5 cascading failures at once (test single failures first, then combinations)
  ✗ Clock skew of >1 hour (your NTP would have to die along with everything else)
```

### 4. Run in Production

Staging is a toy. It has 2 instances, test data, no real traffic. Your circuit breaker that works in staging will fail in production because the timeout is too short for real network latency.

```
Progression:
  1. Run in isolated test environment (proof of concept)
  2. Run in staging (integration testing)
  3. Run in production with minimal blast radius (single instance, 1% traffic)
  4. Run in production with full traffic
  5. Run continuously (chaos is automated, part of normal operations)
```

### 5. Automate

Chaos should be continuous, not a quarterly event. Systems evolve. What's resilient today might be fragile tomorrow after a code change.

---

## Chaos Tools — From CLI to Orchestrated

### tc netem — Network Chaos from CLI

```
WARNING: tc rules affect ALL traffic on the interface.
         Only run on isolated instances or test containers.
         ALWAYS know how to delete rules: tc qdisc del dev <iface> root
```

```bash
# Add 100ms latency to all packets
tc qdisc add dev eth0 root netem delay 100ms

# Add latency with jitter (random variation)
tc qdisc add dev eth0 root netem delay 100ms 20ms
# 100ms base delay ± 20ms jitter (80ms-120ms)

# Add latency distribution (normal distribution — more realistic)
tc qdisc add dev eth0 root netem delay 100ms 20ms distribution normal

# Packet loss
tc qdisc add dev eth0 root netem loss 10%       # 10% random loss
tc qdisc add dev eth0 root netem loss 0.5%       # 0.5% (more realistic — subtle)
tc qdisc add dev eth0 root netem loss 5% 25%     # 5% loss with 25% correlation (bursty)

# Packet corruption (simulates bad hardware)
tc qdisc add dev eth0 root netem corrupt 1%      # 1% random corruption

# Packet duplication
tc qdisc add dev eth0 root netem duplicate 2%    # 2% of packets duplicated

# Bandwidth limit
tc qdisc add dev eth0 root tbf rate 1mbit burst 32kbit latency 400ms
# Limit to 1 Mbit/s

# Combined: latency + loss + bandwidth
tc qdisc add dev eth0 root handle 1: prio
tc qdisc add dev eth0 parent 1:1 handle 10: netem delay 50ms loss 1%
# Complex rules — use with caution

# REMOVE ALL RULES (your escape hatch — know this by heart)
tc qdisc del dev eth0 root
```

### Real Scenario: Testing Graceful Degradation Under Latency

```
Experiment: "If Redis latency increases to 500ms, checkout service should
            fall back to local cache with degraded but functional service."

Setup:
  tc qdisc add dev eth0 root netem delay 500ms 50ms

Observe:
  10:14:00 — Latency injected. Redis now 500ms away.
  10:14:03 — Redis health check fails (timeout 50ms × 3 retries = 150ms → fails).
  10:14:04 — Checkout service logs: "Redis unhealthy. Switching to LOCAL_CACHE mode."
  10:14:04 — Checkout service returns 200 with X-Cache: STALE header. ✓
  10:14:04 — Error rate: 0%. ✓
  10:14:04 — Latency p95: 15ms (local cache is actually faster than Redis!). ✓

Teardown:
  tc qdisc del dev eth0 root

Result: Hypothesis CONFIRMED. System handles Redis latency gracefully.
        Bonus finding: local cache p95 is faster than remote Redis.
        Recommend: Keep local cache as L1 always, not just failover.
```

### stress-ng — Resource Exhaustion

```bash
# CPU stress
stress-ng --cpu 4 --timeout 60s       # Stress 4 CPU cores for 60s
stress-ng --cpu 0 --timeout 60s       # Stress ALL CPU cores (0 = auto-detect)
stress-ng --cpu 4 --cpu-load 50       # Load 50% on each core (not 100%)

# Memory stress
stress-ng --vm 2 --vm-bytes 2G --timeout 60s    # Allocate 4GB (2 workers × 2GB)
stress-ng --vm 2 --vm-bytes 80% --timeout 60s   # Allocate 80% of available memory

# Disk stress
stress-ng --hdd 2 --hdd-bytes 1G --timeout 60s  # Write 2GB to disk (2 workers)
stress-ng --hdd 2 --timeout 60s                 # Continuous write/read/unlink

# Fork bomb (process creation stress)
stress-ng --fork 1000 --timeout 30s             # Continuously fork for 30s

# Combined load
stress-ng --cpu 4 --vm 2 --vm-bytes 1G --hdd 2 --timeout 60s
# CPU + memory + disk — simulates an overloaded instance

# Monitor effects while stressing
watch -n 1 'free -h; echo "---"; uptime; echo "---"; ss -s'
```

### toxiproxy — Application-Aware TCP Failure Injection

toxiproxy sits as a TCP proxy between your application and its dependencies. You inject failures into SPECIFIC connections without affecting others.

```bash
# Install
brew install toxiproxy  # macOS
# or: docker run -d -p 8474:8474 -p 20000-20100:20000-20100 shopify/toxiproxy

# Create a proxy (app → Redis via proxy)
toxiproxy-cli create redis-proxy -l localhost:20000 -u redis:6379
# -l = listen address (your app connects here)
# -u = upstream address (toxiproxy forwards here)

# Your application now uses localhost:20000 instead of redis:6379.
# Traffic flows: App → localhost:20000 (toxiproxy) → redis:6379

# --- Inject Toxics --- #

# Latency: add 500ms ± 100ms jitter
toxiproxy-cli toxic add redis-proxy -t latency -a latency=500 -a jitter=100

# Timeout: close connections after 1s (simulates Redis crashing mid-request)
toxiproxy-cli toxic add redis-proxy -t timeout -a timeout=1000

# Bandwidth limit: 100KB/s
toxiproxy-cli toxic add redis-proxy -t bandwidth -a rate=100

# Slicer: randomly cut packets (simulates network corruption)
toxiproxy-cli toxic add redis-proxy -t slicer -a average_size=64 -a size_variation=32

# Slow close: TCP connection closes but FIN/ACK delayed (simulates TCP issues)
toxiproxy-cli toxic add redis-proxy -t slow_close -a delay=2000

# Reset peer: send TCP RST randomly (simulates connection refused)
toxiproxy-cli toxic add redis-proxy -t reset_peer -a timeout=5000

# --- Management --- #
toxiproxy-cli list                      # Show all proxies
toxiproxy-cli inspect redis-proxy      # Show proxy details + active toxics
toxiproxy-cli toxic remove redis-proxy -n latency_downstream  # Remove a toxic

# Delete the proxy
toxiproxy-cli delete redis-proxy
```

### Python: Kubernetes Chaos Monkey

```python
#!/usr/bin/env python3
"""
Simple Kubernetes chaos monkey — randomly deletes pods in a namespace.

Usage:
  python chaos_monkey.py --namespace production --rate 1 --cooldown 60

This script:
  1. Lists all pods in the namespace
  2. Randomly selects a pod
  3. Deletes it
  4. Waits for cooldown_period
  5. Repeats until interrupted

Safety:
  - Rate limiting: max 1 kill per <cooldown> seconds
  - Pod blacklist: never kill critical infrastructure pods
  - Dry run mode: --dry-run prints what WOULD happen without doing it
  - Graceful shutdown: Ctrl+C prints summary and exits cleanly
  - Requires: kubectl configured, RBAC permission to delete pods
"""

import argparse
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import List, Set

# Pods that should NEVER be killed (infrastructure, stateful services)
BLACKLIST: Set[str] = {
    "etcd-",
    "kube-apiserver-",
    "kube-controller-manager-",
    "kube-scheduler-",
    "coredns-",
    "datadog-agent-",
    "prometheus-",
    "grafana-",
    "jaeger-",
    "otel-collector-",
    "cert-manager-",
    "external-dns-",
    "ingress-nginx-controller-",
    "vault-",
}

class ChaosMonkey:
    def __init__(self, namespace: str, rate: int, cooldown: int,
                 dry_run: bool = False, max_duration: int = None):
        self.namespace = namespace
        self.rate = min(rate, cooldown)  # Cannot exceed 1 kill per cooldown
        self.cooldown = cooldown
        self.dry_run = dry_run
        self.max_duration = max_duration
        self.kills = 0
        self.start_time = datetime.utcnow()
        self.running = True

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        print("\n🛑 Shutting down chaos monkey...")
        self.running = False

    def get_pods(self) -> List[str]:
        """Get list of eligible pods."""
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", self.namespace,
             "-o", "jsonpath={.items[*].metadata.name}"],
            capture_output=True, text=True, check=True
        )
        all_pods = result.stdout.strip().split()

        # Filter blacklist
        eligible = []
        for pod in all_pods:
            if any(pod.startswith(prefix) for prefix in BLACKLIST):
                continue
            eligible.append(pod)

        return eligible

    def kill_pod(self, pod: str):
        """Delete a pod."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        if self.dry_run:
            print(f"[DRY RUN] {timestamp} — Would kill: {pod}")
        else:
            print(f"💀 {timestamp} — Killing: {pod}")
            subprocess.run(
                ["kubectl", "delete", "pod", "-n", self.namespace, pod],
                capture_output=True, text=True, check=True
            )
        self.kills += 1

    def run(self):
        print(f"🐒 Chaos Monkey starting in namespace '{self.namespace}'")
        print(f"   Rate: {self.rate} kill(s) every {self.cooldown}s")
        print(f"   Blacklist: {len(BLACKLIST)} patterns")
        print(f"   Dry run: {self.dry_run}")
        print(f"   Max duration: {self.max_duration}s" if self.max_duration else "   Max duration: unlimited")
        print(f"   Press Ctrl+C to stop\n")

        pods = self.get_pods()
        if not pods:
            print("⚠️  No eligible pods found. Check namespace and blacklist.")
            return

        print(f"🎯 {len(pods)} eligible pods found\n")

        while self.running:
            # Check max duration
            if self.max_duration:
                elapsed = (datetime.utcnow() - self.start_time).total_seconds()
                if elapsed >= self.max_duration:
                    print(f"\n⏰ Max duration ({self.max_duration}s) reached.")
                    break

            # Refresh pod list (pods come and go)
            pods = self.get_pods()
            if not pods:
                print("⚠️  No eligible pods. Waiting 30s...")
                time.sleep(30)
                continue

            # Kill N pods (rate)
            targets = random.sample(pods, min(self.rate, len(pods)))
            for pod in targets:
                if not self.running:
                    break
                self.kill_pod(pod)
                time.sleep(5)  # Small gap between kills

            # Cooldown
            print(f"⏳ Cooldown: {self.cooldown}s...")
            for _ in range(self.cooldown):
                if not self.running:
                    break
                time.sleep(1)

    def summary(self):
        elapsed = (datetime.utcnow() - self.start_time).total_seconds()
        print(f"\n📊 Chaos Monkey Summary")
        print(f"   Duration: {elapsed:.0f}s")
        print(f"   Pods killed: {self.kills}")
        print(f"   Kill rate: {self.kills / (elapsed / 3600):.1f} kills/hour")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kubernetes Chaos Monkey")
    parser.add_argument("--namespace", "-n", required=True, help="Kubernetes namespace")
    parser.add_argument("--rate", "-r", type=int, default=1,
                        help="Pods to kill per interval (default: 1)")
    parser.add_argument("--cooldown", "-c", type=int, default=120,
                        help="Cooldown between kill intervals in seconds (default: 120)")
    parser.add_argument("--max-duration", "-m", type=int, default=None,
                        help="Maximum runtime in seconds (default: unlimited)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing")
    args = parser.parse_args()

    monkey = ChaosMonkey(
        namespace=args.namespace,
        rate=args.rate,
        cooldown=args.cooldown,
        dry_run=args.dry_run,
        max_duration=args.max_duration,
    )
    monkey.run()
    monkey.summary()
```

---

## Designing a Chaos Experiment — Template

```markdown
# Chaos Experiment: [Name]

## Hypothesis
[What do you expect to happen? Be specific about metrics.]

Example: "If the primary Redis instance becomes unavailable, the checkout
service will detect the failure within 3 seconds, switch to the replica,
and continue serving requests with no increase in error rate. Latency p95
may increase by <10ms due to replica promotion delay."

## Steady State
[Record metrics for 30 min before experiment]

| Metric | Value |
|--------|-------|
| Checkout RPS | 500 ± 25 |
| p50 latency | 15ms |
| p95 latency | 45ms |
| Error rate | 0.01% |
| Redis latency p95 | 2ms |

## Experiment Steps
1. Announce experiment in #chaos-engineering channel
2. Take Redis primary offline: `redis-cli -h redis-primary DEBUG SLEEP 60`
3. Observe for 5 minutes
4. Restore Redis primary
5. Observe recovery for 5 minutes

## Blast Radius
- Affected service: checkout-service
- Affected users: 0 (failover should be transparent)
- If error rate > 1%, ABORT immediately

## Abort Criteria
- Error rate > 1% for > 30 seconds
- Latency p95 > 200ms for > 60 seconds
- Any PagerDuty alert fires as a result of experiment

## Abort Procedure
1. toxiproxy-cli delete redis-failover-proxy  (if using toxiproxy)
2. OR: redis-cli -h redis-primary PING  (wake up Redis)
3. Verify metrics return to steady state

## Results

| Metric | Expected | Actual | Match? |
|--------|----------|--------|--------|
| Error rate | 0% | [fill] | [fill] |
| p95 latency | <55ms | [fill] | [fill] |
| Failover time | <3s | [fill] | [fill] |

## Observations
[What did you learn? What surprised you?]

## Action Items
- [ ] If hypothesis proven: document resilience behavior in runbook
- [ ] If hypothesis disproven: file bug, fix system, re-run experiment
```

---

## Chaos Engineering Maturity Model

```
Level 0 — "Let's try this in production"
  Manual chaos, no hypothesis, no abort criteria, no monitoring.
  This is not chaos engineering. This is just breaking things.

Level 1 — Controlled experiments
  Written hypothesis. Steady state monitoring. Abort criteria.
  Manual execution. Quarterly cadence.

Level 2 — Automated experiments
  Scheduled experiments. Automatic abort on budget burn.
  Monthly cadence. Single failure mode at a time.

Level 3 — Continuous chaos
  Experiments run continuously in production.
  Multiple failure modes. Automatic rollback if error budget burns.
  Team trusts the system because it's tested every day.

Level 4 — Chaos as culture
  Every new service comes with chaos experiments defined.
  Game days: cross-team failure injection exercises.
  New hires run chaos experiments in their first week.
```

---

## Common Chaos Engineering Mistakes

1. **No hypothesis**: "Let's see what happens" is not an experiment, it's gambling.
2. **No abort criteria**: Without pre-defined stop conditions, you won't stop until users complain.
3. **Too big too fast**: Killing 50% of instances at once. Start with 1 instance. Then 10%. Then 50%.
4. **No monitoring during experiment**: "We'll check dashboards after." No — watch dashboards LIVE during the experiment.
5. **Chaos only for infrastructure**: Network latency and instance killing are table stakes. Test application-level failures too: malformed JSON responses, slow SQL queries, expired SSL certs returned by upstream.
6. **No action on findings**: You find that your circuit breaker doesn't work. You write it in a doc. Nothing changes. Chaos findings MUST result in bug tickets and code changes.

---

*See also: [10x Mindset](10x-mindset.md) | [Capacity Planning](capacity-planning.md) | [Incident Command](incident-command.md)*
