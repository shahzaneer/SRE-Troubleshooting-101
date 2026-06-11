# CI/CD Pipeline Failures
> **Category:** CI/CD | DevOps | Pipeline
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#ci-cd` `#pipeline` `#devops` `#oncall`

---

## Flaky Tests

### Definition
A test that passes and fails intermittently without any code changes. Flaky tests are the #1 cause of "broken window" CI culture — when engineers see red builds that "always fail," they stop looking at build results entirely. Real bugs ship because nobody checks.

### Impact
- Erodes trust in CI — engineers ignore red builds
- "Retry until green" wastes developer time (average 5 min per retry × 10 engineers × 5 retries/day = 4+ hours lost daily)
- Real failures hidden among noise
- Increases median time to merge (MTTM) by 30-50%

### Detection

```bash
# Count test failures per test over last N builds
# Assumes JUnit XML test reports in target/test-reports/
python3 << 'PYEOF'
import os, xml.etree.ElementTree as ET, glob
from collections import defaultdict

test_results = defaultdict(list)  # test_name -> [pass/fail per build]

for report_dir in sorted(glob.glob("target/test-reports/build-*")):
    for xml_file in glob.glob(f"{report_dir}/*.xml"):
        root = ET.parse(xml_file).getroot()
        for testcase in root.iter("testcase"):
            test_name = f"{testcase.get('classname')}.{testcase.get('name')}"
            failed = bool(testcase.find("failure") or testcase.find("error"))
            test_results[test_name].append("FAIL" if failed else "PASS")

flaky_tests = {}
for name, results in test_results.items():
    if len(results) >= 5:
        fail_rate = results.count("FAIL") / len(results)
        if 0 < fail_rate < 1.0:  # Fails sometimes, passes sometimes
            flaky_tests[name] = fail_rate

for name, rate in sorted(flaky_tests.items(), key=lambda x: -x[1]):
    print(f"  {rate*100:.0f}% flaky — {name} ({results.count('FAIL')}/{len(results)} failures)")
PYEOF
```

### Common Root Causes

| Cause | Example | Fix |
|-------|---------|-----|
| **Race conditions** | Two tests create user "test-user"; second fails on UNIQUE constraint | UUID-based unique test data |
| **Order dependency** | Test A creates DB record, Test B depends on it, Test A gets removed | Each test creates its own fixtures |
| **Shared mutable state** | Singleton cache from previous test leaks into next test | `@BeforeEach` reset all caches |
| **Time/date dependent** | Test checks "created within last second" — pass on fast CI, fail on slow | Inject `Clock` interface, use fixed clock in tests |
| **Network timeout** | Test calls external API, API is slow 2% of the time | Mock external dependencies |
| **Async timing** | Waits 5s for job to finish, job takes 5.1s sometimes | Poll with condition: `await().atMost(30, SECONDS).until(jobDone)` |
| **Resource leak** | `Too many open files` after 100th test | Close resources in `@AfterEach` |
| **Random data collision** | Random string occasionally duplicates across tests | Use UUID or test-specific prefixes |

### Scenario: E2E Test Failing 30% of the Time

**Symptoms:**
```
Tests run: 150, Failures: 1, Errors: 0
  OrderConfirmationE2E.shouldSendEmail: FAILED
    Expected: "Email sent"
    Actual: "Order processing..." (still in progress)
```

**Diagnosis:**
```bash
# Check test logs — always shows "waited 5 seconds for email"
grep -A5 "FAILED" target/test-reports/build-*/TEST-*.xml

# Test code:
# Thread.sleep(5000);  // Wait for async email job
# assertThat(emailService.getSentEmails()).hasSize(1);

# Root cause: async job takes 4-12 seconds depending on CI runner load.
# Fixed sleep of 5s fails ~30% of the time.

# Fix: Replace Thread.sleep() with Awaitility
# await().atMost(Duration.ofSeconds(30))
#   .pollInterval(Duration.ofMillis(500))
#   .untilAsserted(() -> assertThat(emailService.getSentEmails()).hasSize(1));
```

---

## Test Quarantine Process

### Step 1: Identify Flaky Tests

```python
#!/usr/bin/env python3
"""flaky_finder.py — Parse JUnit XML from last N builds and identify flaky tests."""
import os, sys, json, glob, xml.etree.ElementTree as ET
from collections import defaultdict
from argparse import ArgumentParser

def find_flaky(report_globs: list, min_builds: int = 5, flaky_threshold: float = 0.2):
    test_history = defaultdict(list)

    for pattern in report_globs:
        for xml_file in sorted(glob.glob(pattern)):
            try:
                root = ET.parse(xml_file).getroot()
                for tc in root.iter("testcase"):
                    name = f"{tc.get('classname','')}.{tc.get('name','')}"
                    failed = bool(tc.find("failure") or tc.find("error") or tc.find("skipped"))
                    test_history[name].append(1 if failed else 0)
            except ET.ParseError:
                continue

    flaky = {}
    for name, results in test_history.items():
        if len(results) < min_builds:
            continue
        fail_rate = sum(results) / len(results)
        if 0 < fail_rate <= flaky_threshold:  # fails sometimes, but mostly passes
            flaky[name] = {"fail_rate": round(fail_rate, 3), "runs": len(results),
                           "failures": sum(results)}

    return dict(sorted(flaky.items(), key=lambda x: -x[1]["fail_rate"]))


def main():
    parser = ArgumentParser(description="Find flaky tests from JUnit XML reports")
    parser.add_argument("--reports", nargs="+", required=True, help="Glob patterns for XML reports")
    parser.add_argument("--min-builds", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.2, help="Max failure rate to flag (0.2 = 20%)")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    flaky = find_flaky(args.reports, args.min_builds, args.threshold)

    output = {"flaky_count": len(flaky), "tests": flaky,
              "quarantine_candidates": [name for name, d in flaky.items() if d["fail_rate"] > 0.1]}

    json_output = json.dumps(output, indent=2)
    if args.output == "-":
        print(json_output)
    else:
        with open(args.output, 'w') as f:
            f.write(json_output)

    sys.exit(1 if flaky else 0)


if __name__ == "__main__":
    main()
```

### Step 2: Quarantine

```java
// JUnit 5: Move flaky test to quarantine suite using @Tag
@Tag("quarantine")
@Test
void flakyPaymentTest() {
    // This test is in quarantine — runs but does not block deployment
}

// CI configuration (Gradle/Kotlin DSL):
// tasks.register<Test>("quarantineTest") {
//     useJUnitPlatform { includeTags("quarantine") }
// }
// tasks.register<Test>("unitTest") {
//     useJUnitPlatform { excludeTags("quarantine") }
// }
```

### Step 3: Root Cause Fix

Track quarantine issues in Jira/Link with `label:flaky-test`. Dedicate 10% of each sprint to fixing flaky tests. A flaky test older than 2 sprints should be deleted (unmaintained tests provide false confidence).

### Step 4: Re-integration Criteria

Test must pass 20 consecutive CI runs with 0 failures. Only then move back from `@Tag("quarantine")` to the main suite.

---

## Docker Build Cache Invalidation

### How Docker Caching Works

Docker builds layers from a Dockerfile. Each instruction creates a layer, cached by checksum. When a layer's input changes, that layer AND ALL SUBSEQUENT LAYERS are rebuilt.

```dockerfile
# BAD ORDERING — source change invalidates entire build
FROM node:18-alpine
WORKDIR /app
COPY . .                    # <-- ANY file change invalidates EVERYTHING below
RUN npm ci                  # <-- Reinstalls ALL node_modules on every source change
RUN npm run build

# GOOD ORDERING — only install dependencies when they change
FROM node:18-alpine
WORKDIR /app
COPY package.json package-lock.json ./   # <-- Only changes when deps change
RUN npm ci --production                  # <-- Cached unless package.json changes
COPY src/ ./src/                         # <-- Source changes only rebuild this + build
COPY tsconfig.json ./
RUN npm run build
```

### Scenario: Docker Build Always Takes 8 Minutes

**Diagnosis:**
```bash
# Check which layers are being rebuilt
docker build --progress=plain --no-cache=false -t app:test . 2>&1 | grep -E "^#[0-9]+ \["
# Shows every layer — if RUN npm ci is always [BUILD], your COPY ordering is wrong

# Measure cache hit rate
docker history app:latest
# Look for layers with 0B size — those are cached FROM layers
# Full-sized rebuilt layers indicate cache misses

# Check what's invalidating cache:
# 1. COPY . . copies EVERY file including .git, node_modules, .env
# 2. ADD with URL always downloads (no cache)
# 3. ARG before RUN — any ARG change busts cache
```

**Fix:**
```bash
# Use .dockerignore to exclude files that don't affect the build
cat > .dockerignore << 'EOF'
.git
.gitignore
node_modules
.env
*.md
dist
coverage
.DS_Store
docker-compose*.yml
EOF

# Reorder Dockerfile: dependencies first, then source, then build
# Build time goes from 480s → 45s (cached) for typical Node/Python app
```

### Docker Build Cache Management

```bash
# View cache usage
docker system df

# Prune build cache older than 72 hours
docker builder prune --filter "until=72h" -f

# Prune everything not used in last 24h
docker system prune -af --filter "until=24h"

# CI-specific: clear cache when Dockerfile changes
# In CI script:
if git diff --name-only HEAD~1 | grep -q "Dockerfile"; then
    docker builder prune -af  # Full cache reset on Dockerfile change
fi
```

---

## Registry Push Failures

### Authentication Failures

```bash
# AWS ECR — token expires after 12 hours
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com

# Check if ECR repository exists
aws ecr describe-repositories --repository-names api || \
  aws ecr create-repository --repository-name api --image-scanning-configuration scanOnPush=true

# GCR (Google) — configure credential helper
gcloud auth configure-docker us-central1-docker.pkg.dev

# Docker Hub rate limit check
# Anonymous: 100 pulls / 6 hours
# Authenticated free: 200 pulls / 6 hours
# Pro/Team: 5,000 pulls / day
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:ratelimitpreview/test:pull" | jq -r .token)
curl -s --head -H "Authorization: Bearer $TOKEN" https://registry-1.docker.io/v2/ratelimitpreview/test/manifests/latest 2>&1 | grep -i ratelimit
# RateLimit-Limit: 100;w=21600
# RateLimit-Remaining: 45
```

### Disk Full on CI Runner

```bash
# Check disk usage
df -h /var/lib/docker

# Aggressive cleanup
docker system prune -af --volumes
docker builder prune -af

# GitHub Actions — cleanup action
# - name: Free disk space
#   run: |
#     sudo rm -rf /usr/share/dotnet /opt/ghc /usr/local/share/boost
#     docker system prune -af

# If still full: move Docker data dir to larger volume
# /etc/docker/daemon.json:
# { "data-root": "/mnt/large-volume/docker" }
```

### Image Too Large

```bash
# Check image size
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | sort -k3 -h

# Analyze layers
docker history --no-trunc --human app:latest

# Multi-stage builds reduce final image size dramatically
# Node example: 1.2GB → 150MB
# FROM node:18 AS build
# COPY . . && RUN npm ci && npm run build
# FROM node:18-alpine
# COPY --from=build /app/dist /app
```

---

## Deployment Stuck

### Health Check Never Passes

```bash
# K8s: Check readiness probe endpoint directly from within the cluster
kubectl run debug --rm -it --image=busybox -- wget -qO- http://api-service:8080/health

# Check which specific probe is failing
kubectl describe pod $POD_NAME | grep -A10 "Readiness\|Liveness"
# Look for: "Readiness probe failed: HTTP probe failed with statuscode: 503"

# Check if the port is actually listening
kubectl exec $POD_NAME -- netstat -tlnp
kubectl exec $POD_NAME -- ss -tlnp

# Check if dependent services are reachable
kubectl exec $POD_NAME -- nslookup db-service
kubectl exec $POD_NAME -- nc -zv db-service 5432
```

### Scenario: Deployment Stuck for 20 Minutes

**Symptoms:**
```
kubectl rollout status deployment/api -n production
# Waiting for rollout to finish: 2 of 6 new replicas updated...
# (stuck here for 15 minutes)
```

**Diagnosis:**
```bash
# New pods are created but failing readiness
kubectl get pods -n production -l app=api --sort-by=.metadata.creationTimestamp
# api-7d8f9-abcde   0/1   Running     0     5m
# api-7d8f9-fghij   0/1   Running     0     5m
# api-6c5e3-xyz12   1/1   Running     0     2h     ← old pods (working)

# Check pod events
kubectl describe pod api-7d8f9-abcde -n production | tail -20
# Events:
#   Warning  Unhealthy  5m    Readiness probe failed: HTTP probe failed with statuscode: 500

# Check pod logs
kubectl logs api-7d8f9-abcde -n production --tail=20
# ERROR: ConfigMap "api-config" not found
# ERROR: Failed to load feature flags: connection refused

# Root cause: ConfigMap api-config was updated in the same deploy,
# but it hasn't propagated to the new pod yet (eventual consistency in K8s).
# Fix: Ensure ConfigMap exists BEFORE deploying pods that depend on it.
# Or: Use immutable ConfigMaps (create api-config-v2, update deployment to reference it).
```

### Rollback Triggers

```bash
# Manual rollback
kubectl rollout undo deployment/api -n production
kubectl rollout undo deployment/api -n production --to-revision=3

# ArgoCD auto-sync rollback
argocd app rollback api --to-revision=3

# Spinnaker — rollback to previous server group
spin app rollback --application api --target "api-v042"
```

---

## GitHub Actions Specific

### Common Failure Patterns

```yaml
# GITHUB_TOKEN permissions insufficient
# Error: "Resource not accessible by integration"
# Fix: Grant permissions in workflow or repo settings
permissions:
  contents: write
  packages: write
  issues: read
  pull-requests: write

# actions/checkout fails on protected branches
# Error: "refs/heads/main is protected"
# Fix: Use a deploy key or GitHub App token with bypass permissions

# Self-hosted runner offline
# Check runner status:
# systemctl status actions-runner
# journalctl -u actions-runner --since "10 min ago"
# Restart:
# sudo systemctl restart actions-runner

# Matrix strategy: one failure doesn't stop others by default
# Use fail-fast: false to run all matrix jobs even if one fails
strategy:
  fail-fast: false
  matrix:
    node-version: [16, 18, 20]

# Secret not available in PR from fork
# For security, secrets are not passed to workflows triggered by fork PRs
# Workaround: use pull_request_target event (careful: exposes secrets to PR code!)
on:
  pull_request_target:
    types: [labeled]
```

### GitHub Actions Debugging

```bash
# Enable debug logging (set secret ACTIONS_STEP_DEBUG=true)
# Or re-run with debug enabled

# View raw logs
gh run view <run-id> --log

# Download all artifacts from a run
gh run download <run-id>

# Check runner disk space
df -h
du -sh /home/runner/work/*

# SSH into a failed run (add tmate action for interactive debugging)
# - name: Setup tmate session
#   uses: mxschmitt/action-tmate@v3
#   if: failure()
```

---

## Jenkins Specific

### Pipeline Syntax Issues

```groovy
// Declarative Pipeline (recommended for most pipelines)
pipeline {
    agent any
    parameters {
        string(name: 'VERSION', defaultValue: '', description: 'Version to deploy')
    }
    environment {
        DOCKER_REGISTRY = '123456789.dkr.ecr.us-east-1.amazonaws.com'
    }
    stages {
        stage('Build') {
            steps {
                sh 'docker build -t $DOCKER_REGISTRY/api:$VERSION .'
            }
        }
        stage('Deploy') {
            when { branch 'main' }
            steps {
                sh 'kubectl set image deployment/api api=$DOCKER_REGISTRY/api:$VERSION'
            }
        }
    }
    post {
        always { cleanWs() }  // Prevent disk full
        failure { slackSend(color: 'danger', message: "Pipeline failed: ${env.BUILD_URL}") }
    }
}

// Scripted Pipeline (more flexible, harder to maintain — avoid unless necessary)
// Groovy sandbox restrictions:
// - No System.exit(), no Runtime.exec()
// - No reflection (Class.forName)
// - Limited file I/O (only within workspace)
// - No network calls except approved libraries

// Approve sandbox exceptions in:
// Manage Jenkins → In-process Script Approval
```

### Agent Connection Lost

```bash
# On the Jenkins master:
# Check agent status
java -jar jenkins-cli.jar -s https://jenkins.internal list-nodes

# Disconnect and reconnect agent
java -jar jenkins-cli.jar -s https://jenkins.internal disconnect-node worker-1
java -jar jenkins-cli.jar -s https://jenkins.internal connect-node worker-1

# On the agent machine:
# Check agent process
ps aux | grep remoting
systemctl status jenkins-agent

# Common causes of agent disconnect:
# 1. OutOfMemoryError — increase heap: -Xmx2g in agent JVM args
# 2. Network timeout — check firewall between agent and master
# 3. Disk full on agent — clean workspace

# Workspace cleanup (add to post block)
# cleanWs()  # Deletes entire workspace
# cleanWs(patterns: [[pattern: '**/*.log', type: 'INCLUDE']])
# deleteDir()  # Simpler, deletes workspace dir
```

### Jenkins Performance Issues

```bash
# Check Jenkins JVM memory
ps aux | grep jenkins | grep Xmx

# Heap dump if OOM
jmap -dump:live,format=b,file=/tmp/jenkins.hprof $(pgrep -f jenkins)

# Clean up old builds (discard old builds in job config)
# Or via script console:
# Jenkins.instance.getAllItems(Job).each { job ->
#   job.builds.findAll { it.number < job.lastSuccessfulBuild?.number - 50 }.each {
#     it.delete()
#   }
# }

# Check plugins for memory leaks
# Manage Jenkins → System Information → Memory Usage
```
