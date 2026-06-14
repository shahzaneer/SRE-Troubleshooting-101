# Contributing to SRE Troubleshooting Bible

> **Living document.** Every incident teaches us something. Contribute your learnings so the next on-call engineer has a faster path to resolution.

---

## When to Contribute

- **After an incident:** Add new scenarios, failure modes, or diagnosis techniques you discovered during resolution.
- **After a successful debugging session:** Share your approach — what commands did you run, in what order, and why?
- **When you learn a new command or technique** that would have helped you 6 months ago.
- **When you find outdated or incorrect information:** The cloud moves fast. AWS APIs change, Kubernetes features evolve, commands deprecate.
- **When a runbook saved your incident:** Improve it — add the edge case you hit that wasn't documented.

---

## Contribution Guidelines

### 1. Use the Standard Template

Every troubleshooting scenario should follow this structure:

```markdown
## Scenario: [Descriptive Title]

**Symptoms:** What the engineer observes (metrics, logs, user reports, alerts)

**Impact:** User-facing effect (e.g., "checkout error rate 15%", "login completely broken")

**Diagnosis Commands:** (In order — fastest and least invasive first)
  ```bash
  # command — what this checks and what to look for
  ```
  ```python
  # diagnostic script if applicable
  ```

**Root Cause:** The actual thing that went wrong. Be specific.

**Fix:** 
  - **Immediate mitigation:** (stop the bleeding, restore service)
  - **Permanent fix:** (prevent recurrence)

**Prevention:** How to prevent this from happening again. Concrete actions.

**Code Example:** (Python/Java/Bash as applicable)
```

### 2. Include Real Error Messages

Not "an error occurred" — copy and paste actual log lines or error output (with sensitive data like IPs, usernames, and keys redacted as `REDACTED`).

Good:
```
Error: "FATAL: remaining connection slots are reserved for non-replication superuser connections"
```

Bad:
```
The database returned a connection error.
```

### 3. Every Command Must Have a Comment

Each command in a code block should have a comment explaining what it does and what to look for in the output.

```bash
# Show top 10 IPs hitting the login endpoint with 401 — look for single IPs with >100 attempts
awk '$7 ~ /\/api\/login/ && $9==401 {print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -10
```

### 4. Mark Destructive Commands

Use the ⚠️ prefix and explain the risk for any command that:
- Modifies production data (DELETE, DROP, TRUNCATE, ALTER)
- Kills processes (kill -9, pkill)
- Restarts services (systemctl restart, kubectl delete pod)
- Changes security settings (revokes permissions, closes ports)
- Can't be easily undone

Example:
```bash
# ⚠️ DESTRUCTIVE: This restarts the production database. Expect 30-60 seconds of downtime.
# Only run after confirming failover is working and during a maintenance window.
sudo systemctl restart postgresql
```

### 5. Test Your Commands

Don't contribute untested commands or scripts. Every script in 16-scripts-toolkit must:
- Use `set -euo pipefail` (Bash) or have proper error handling (Python)
- Exit with meaningful status codes (0 = healthy, 1 = problem, 2 = usage error)
- Be runnable from any directory
- Handle edge cases (empty input, missing dependencies, permission denied)
- Include a usage example at the top of the file

### 6. Use the Correct Difficulty Tag

| Tag | Criteria |
|-----|----------|
| **Basic** | Requires only common command-line tools. No domain-specific knowledge. A new SRE should be able to follow along. |
| **Intermediate** | Requires familiarity with the technology (Kubernetes, AWS, Kafka, etc.). Assumes the reader has worked with the system before. |
| **Advanced** | Requires deep domain expertise. May involve kernel internals, complex distributed systems debugging, or multi-service forensics. |

### 7. Follow Existing File Conventions

- Files use GitHub Flavored Markdown
- Headers with metadata block at the very top of every guide:
  ```
  # Title
  > **Category:** Category | Subcategory
  > **Difficulty:** Basic | Intermediate | Advanced
  > **Last Reviewed:** YYYY-MM
  > **Tags:** `#tag1` `#tag2`
  ```
- Internal links use relative paths: `[link text](relative/path.md)`
- Code blocks specify language: ` ```bash `, ` ```python `, ` ```java `, ` ```yaml `

---

## Pull Request Process

1. **Fork the repository** (or create a branch if you have write access)
2. **Create a descriptive branch:** `git checkout -b add/incident-learnings` or `fix/update-log4shell-guide`
3. **Make your changes** following the guidelines above
4. **Test any scripts** you've contributed or modified
5. **Submit a PR** with:
   - A clear description of what you're adding or changing
   - Why it's valuable (link to an incident post-mortem or relevant documentation)
   - A note on whether you've tested the commands/scripts
6. **Get review** from at least one SRE team member
7. **Address feedback** — reviewers may ask for clarification, additional scenarios, or structural changes
8. **Merge** — the knowledge base grows

---

## What NOT to Contribute

- **Proprietary information:** No internal hostnames, IP addresses, usernames, or passwords. Redact with `REDACTED`.
- **Unvalidated fixes:** Don't contribute "I think this might work" solutions. Only battle-tested fixes.
- **Opinionated tool choices:** "Use Datadog instead of Prometheus" is opinion. "Here's a PromQL query for detecting memory leaks" is fact.
- **Out-of-date content without flagging it:** If you're contributing to a guide about a rapidly-changing technology (Kubernetes, AWS), note the version you tested against.
- **Marketing or product pitches:** This is a troubleshooting reference, not a vendor comparison.

---

## Repository Structure

```
sre-troubleshooting-bible/
├── 00-foundations/
├── 01-linux-debugging/
├── 02-networking/
├── 03-aws/
├── 04-containers/
├── 05-kubernetes/
│   ├── pods/
│   ├── controllers/
│   ├── services/
│   ├── ingress/
│   ├── networking/
│   ├── config/
│   ├── storage/
│   ├── scheduling/
│   ├── autoscaling/
│   ├── security/
│   ├── probes/
│   ├── operators/
│   ├── tooling/
│   └── operations/
├── 06-databases/
│   ├── postgresql/
│   ├── mysql/
│   └── redis/
├── 07-api-troubleshooting/
├── 08-error-codes/
├── 09-observability/
├── 10-performance/
├── 11-oncall-runbooks/
├── 12-10x-sre-playbooks/
├── 13-security-incidents/
├── 14-ci-cd/
├── 15-messaging-queues/
│   ├── kafka/
│   └── sqs/
├── 16-scripts-toolkit/
├── GLOSSARY.md
├── CHANGELOG.md
└── CONTRIBUTING.md
```

Each section has its own `README.md` as an index. Individual troubleshooting guides are .md files within the section.

---

## Questions?

If you're unsure whether something belongs in the Troubleshooting Bible, ask yourself:

> "Would this have helped me resolve an incident faster at 3 AM when I was tired and stressed?"

If the answer is yes, contribute it.
