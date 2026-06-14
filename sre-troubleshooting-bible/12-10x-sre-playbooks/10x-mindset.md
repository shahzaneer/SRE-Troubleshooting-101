# The 10x SRE Mindset
> **Category:** 10x SRE | Mindset | Culture
> **Difficulty:** All Levels
> **Last Reviewed:** 2026-06
> **Tags:** `#10x` `#mindset` `#culture` `#sre`

---

## The 5 Whys — Real Production Incident Example

Surface-level fixes create repeat incidents. Root cause fixes prevent them forever. The 5 Whys is the simplest and most powerful technique for finding root cause.

### The Incident

```
Monday, 10:14 AM. Incident declared.
Symptom: Recommendation service returning HTTP 500 for ALL requests.
         All checkout pages display "Recommendations unavailable."
         All A/B test variants fail (recommendations are part of the checkout funnel).
         Revenue impact: estimated $12,000/hour.

Initial fix: Restart recommendation service → 500s stop, errors clear. MTTR: 8 minutes.
```

### The 5 Whys Analysis

```
Why #1: Why did the recommendation service return 500 errors?
Answer: The database connection pool was exhausted.
        Error message: "HikariPool-1 - Connection is not available, request timed out after 30000ms"

Why #2: Why was the database connection pool exhausted?
Answer: The new recommendation algorithm (deployed Friday at 5pm) opens 3 database
        connections per request instead of 1.
        Old algorithm: 1 query → get recommendations from materialized view.
        New algorithm: 3 queries → user profile, user purchase history, collaborative
                       filter model → JOIN in application code (N+1 style).

Why #3: Why was the algorithm deployed without updating the connection pool config?
Answer: The algorithm was developed by the ML team in a separate repository.
        The connection pool config is in the infrastructure repo.
        No one on the ML team knew connection pool limits existed.
        Deployment checklist for ML services doesn't mention database configs.

Why #4: Why doesn't the deployment checklist include connection pool sizing?
Answer: The deployment checklist was written 2 years ago when all services used
        in-memory storage. It hasn't been updated since the migration to PostgreSQL.
        It lives in a Confluence page no one reads.

Why #5: Why does the organization rely on manual checklists instead of automated
        pre-deployment validation?
Answer: There's no CI pipeline step for capacity testing.
        No performance test runs before production deployments.
        No SLO-based gating of deployments.
```

### Root Cause (Not "Human Error")

```
Surface cause: "Engineer didn't update connection pool config."
Root cause:    "The deployment system allows deploying services that will
               predictably exhaust shared resources without validation."

The system should have:
  1. An integration test that verifies the number of DB connections per request
  2. A CI check: if connections/request × expected RPS > pool size → BLOCK DEPLOY
  3. A SLO-based canary: deploy to 5%, if error budget burn rate > x10 → ROLLBACK
```

### Fixes Implemented

```
Immediate (same day):
  - Increase HikariCP max pool size from 20 to 60
  - Reduce connection-per-request from 3 to 1 (batch queries)

Short-term (1 week):
  - Add connection pool impact analysis to CI: `assert(conns_per_req × peak_rps < max_pool_size)`
  - Add performance test to deployment pipeline
  - Train ML team on infrastructure constraints

Long-term (1 month):
  - Auto-scale connection pool based on request throughput
  - Migrate from per-request queries to materialized views (structured correctly)
  - Implement circuit breaker on recommendation service (fail open with empty recs)
  - Monthly SRE office hours for product teams
```

---

## Blameless Culture & Psychological Safety

> "Human error" is NEVER a root cause. The root cause is always the system that allowed the human error to cause an incident.

### The Blame Culture Spectrum

```
Blame Culture                          Blameless Culture
─────────────                         ──────────────────
"Who did this?"                       "What happened?"
"Fire the person who..."
  (person learns: hide mistakes)       (person learns: report mistakes early)
"Write them up for..."
  (team learns: don't take risks)      (team learns: take calculated risks)
"Don't let them touch production again"
  (org learns: don't report incidents) (org learns: incidents improve the system)
```

### The Blameless Post-Mortem Template

```
Title: [Brief description of incident]
Date: [Date of incident]
Duration: [Total duration of user impact]
Authors: [Everyone involved — all perspectives]

Summary:
  What happened in 2-3 sentences. Focus on impact to users.

Timeline (UTC):
  10:14 — Alert fired: recommendation-service error rate > 5%
  10:15 — Oncall acknowledged
  10:17 — Identified DB connection pool exhaustion in logs
  10:20 — Decision: restart service (short-term fix)
  10:22 — Service restarted, error rate back to 0%
  10:25 — Incident declared resolved

Root Cause Analysis (5 Whys):
  [See above]

Contributing Factors:
  - New algorithm deployed Friday 5pm with no capacity review
  - Deployment checklist outdated (2 years without update)
  - No automated capacity testing in CI pipeline
  - Recommendation service lacked circuit breaker

What Went Well:
  - Alert fired within 1 minute of error rate crossing threshold
  - Oncall acknowledged in <1 minute
  - Decision to restart (not investigate root cause) was correct for MTTR
  - Root cause analysis was thorough and identified systemic issues

What Went Poorly:
  - Friday 5pm deploy (danger zone — no one around to fix issues over weekend)
  - ML team unaware of infrastructure constraints
  - No canary deployment — impact was 100% of users immediately

Action Items (with owner and deadline):
  - [P0] Add connection pool check to CI pipeline (Owner: Jane, ETA: Thu)
  - [P0] Add circuit breaker to recommendation service (Owner: Mark, ETA: Fri)
  - [P1] Update deployment checklist (Owner: Sarah, ETA: Next sprint)
  - [P1] Create SRE office hours program (Owner: Amir, ETA: Ongoing)
  - [P2] Automate connection pool auto-scaling (Owner: Jane, ETA: Q3)
```

### Real Scenario: The DROP TABLE Incident

```
Incident: Engineer accidentally ran `DROP TABLE payments` in production instead of staging.
Company reaction (Blame culture): "Fire the engineer. Revoke their production access."
Outcome: Engineer fired. New hire makes SAME mistake 3 months later.
         Why? The SYSTEM still allowed it.

Company reaction (Blameless culture):
  "Why did this happen?"

  Why #1: Why was the engineer connected to production?
  Answer: Their terminal had both staging AND production credentials loaded.

  Why #2: Why did the terminal have both environments?
  Answer: The SSH config makes it easy to switch environments with `prod` and `staging`
          aliases that look identical in the shell prompt.

  Why #3: Why did the DB let you drop a table without confirmation?
  Answer: MySQL `DROP TABLE` has no confirmation prompt by default.

  Why #4: Why can staging scripts access production databases?
  Answer: Scripts use the same DB client, which reads credentials from environment
          variables that are set at shell login.

  Why #5: Why aren't production credentials isolated from staging?
  Answer: No access control system exists. All engineers have access to all databases.

  Fixes:
  1. Production access requires explicit `prod-access request` command (audit log)
  2. Shell prompt shows ENVIRONMENT in RED for production
  3. `DROP TABLE` in production requires `--i-am-sure` flag (custom DB proxy)
  4. Separate SSH keys for staging and production
  5. Production DB access only from jump host (never from dev laptop)
```

---

## Communication During Incidents

Poor communication during an incident causes more chaos than the incident itself. Stakeholders demand updates. Engineers duplicate work. Someone escalates unnecessarily.

### The Golden Rules

1. **One channel, not many**: All incident communication goes in `#inc-20260611-recommendation-outage`. Never DMs. Never the general `#engineering` channel.
2. **One voice**: The Communications Lead is the only person posting to the status page and stakeholder channels.
3. **Regular cadence, not silence**: Update every 15 minutes for P0, every 30 minutes for P1. Even if the update is "still investigating."
4. **No speculation**: Never say "it's probably the DB" if you don't know. Say "we are investigating three hypotheses: DB, deployment, network."

### Status Page Updates — Templates

```
Template 1 — Initial Investigation:
  "We are investigating elevated error rates on the recommendation service.
   Impact: Checkout pages may display 'Recommendations unavailable' messages.
   Checkout itself remains functional.
   Next update: 15 minutes. [10:15 UTC]"

Template 2 — Root Cause Identified:
  "We have identified the cause: a deployed algorithm uses more database
   connections than configured. We are increasing connection pool limits.
   ETA to resolution: approximately 10 minutes. [10:20 UTC]"

Template 3 — Resolution:
  "Error rates have returned to normal. Recommendation service is fully
   functional. We will publish a post-mortem within 48 hours.
   Monitoring will continue for the next 2 hours. [10:25 UTC]"
```

### Slack Channel Management

```
#inc-20260611-recommendation-outage

Pin the critical messages:
  📌 "Incident declared at 10:14. IC: Amir. Comm Lead: Sarah."
  📌 "Decision: Restart service. ETA 5 min."
  📌 "Incident resolved at 10:25. Duration: 11 min."

Set channel topic:
  "P0 — Recommendation service error rate 15%. IC: Amir. Started 10:14."

Use threads:
  Main channel: Status updates and major decisions ONLY.
  Threads: Technical debugging details, screenshots, log excerpts.
```

### Stakeholder Communication

```
To: exec-team@company.com
Subject: [P0] Recommendation Service Degradation — RESOLVED (10:25 UTC)

Duration: 11 minutes (10:14 - 10:25 UTC)
Impact: Recommendation widgets on checkout page displayed errors.
        Core checkout flow (payment, cart) was NOT affected.
        Revenue impact: estimated $2,200 (11 min × $12,000/hour lost recs revenue).
        No data loss. No customer data exposed.

Root Cause: Database connection pool exhausted due to new algorithm deployment.

Action: Service restarted. Pool limits increased. Post-mortem scheduled.

Next Update: Post-mortem to be published within 48 hours.
```

---

## Toil Identification and Elimination

Toil is the SRE's enemy. It's work that's manual, repetitive, automatable, and has no enduring value. Every hour spent on toil is an hour NOT spent on making the system more reliable.

### The Toil Test

Ask four questions. If the answer to ALL is yes, it's toil:

```
1. Is it MANUAL?          (A human must do it — not scripted)
2. Is it REPETITIVE?      (You do it regularly, not a one-off)
3. Is it AUTOMATABLE?     (A machine COULD do it — no human judgment needed)
4. Is it TACTICAL?        (No enduring value — doing it today doesn't prevent it tomorrow)
```

### Toil Examples

```
TOIL (all four criteria met):
  - Manually restarting a service that crashes every 2 days.
  - SSHing into each instance to rotate log files.
  - Manually updating DNS entries across 50 domains.
  - Copy-pasting deployment commands into 20 terminal windows.

NOT TOIL (fails at least one criteria):
  - Investigating a novel crash (not repetitive).
  - Writing a post-mortem (not automatable — requires human analysis).
  - 1:1 meetings with team members (not repeatable in a mechanized way).
  - Architectural planning (has enduring value — shapes future reliability).
```

### Toil Budget

```
Track toil hours per sprint.
  If toil > 50% of sprint → STOP FEATURE WORK.
  Team spends the next sprint ONLY on automation to reduce toil below 25%.

  This is not optional. Toil compounds. The more toil you have, the more toil
  you generate (manual fixes break more things). Without a hard limit, toil
  grows until your SRE team is just ops.
```

### Toil Elimination Patterns

```
Current Toil                        | Automation
------------------------------------|------------------------------------------
Restart flaky service every 2 days  | Kubernetes liveness probe + auto-restart
Rotate log files on instances       | Log agent (Fluentd/Filebeat) + log rotation config
Update DNS entries                  | ExternalDNS + Kubernetes Ingress annotations
Deploy to 20 instances manually     | CI/CD pipeline + rolling update
Provision new instances             | Terraform / Pulumi + auto-scaling groups
Certificate renewal                 | cert-manager + Let's Encrypt
```

---

## SLO as Decision-Making Framework

Error budgets aren't just for alerting — they're the primary decision-making tool for SRE leadership.

### The Error Budget Decision Matrix

```
Error Budget Remaining | Feature Deploys | Risky Changes | Focus
-----------------------|-----------------|---------------|--------------------------
> 50%                  | Normal velocity | Allowed       | Features (business wants)
30-50%                 | Normal velocity | Review req'd  | Features + reliability balance
10-30%                 | Slowed           | Restricted    | Reliability focus
< 10%                  | FROZEN           | BLOCKED       | Reliability ONLY (SRE wants)
== 0% (exhausted)      | FROZEN           | BLOCKED       | Emergency reliability work
```

### Real Scenario: The Refactoring Debate

```
Context:
  Team wants to deploy a major refactoring of the payment service.
  Refactoring is a rewrite of the charge processing pipeline.
  No new features. No user-visible changes.
  Engineering says: "This will make future development 3x faster."
  PM says: "Users are waiting for the Apple Pay integration. That's revenue."

  Error budget remaining: 30%.
  Apple Pay integration: "low risk" → tested, incremental change, behind feature flag.
  Refactoring: "high risk" → complete rewrite of core payment logic.

  Decision: Apple Pay first. Refactoring deferred until error budget > 50%.
  Rationale: With 30% budget, we can afford ONE risky change per quarter.
            Apple Pay is revenue-positive. Refactoring is debt-payoff.
            When budget is low, choose revenue. When budget is high, pay debt.
```

---

## The SRE's Decision-Making Heuristic

When faced with a difficult decision during an incident, ask these questions in order:

```
1. "What is the SLO?" (What did we promise users?)
2. "How fast are we burning error budget?" (Minutes? Hours? Days?)
3. "Does this decision reduce MTTR or increase it?"
4. "What's the worst case if I'm wrong?" (Blast radius assessment)
5. "Who else needs to know this decision?" (Communication)
```

---

## The 10x SRE Reading List

1. **Site Reliability Engineering** (Google) — The bible. Chapters: 4 (Service Level Objectives), 14 (Managing Incidents), 15 (Postmortem Culture)
2. **The SRE Workbook** (Google) — Chapter 7 (Simplicity), Chapter 11 (On-call)
3. **Seeking SRE** — Chapter 4 (Monitoring), Chapter 10 (Incident Management)
4. **The Phoenix Project** — The novel that explains why DevOps/SRE thinking matters
5. **Thinking in Systems** (Donella Meadows) — How complex systems fail, and why
6. **The Field Guide to Understanding Human Error** (Sidney Dekker) — Why "human error" is never the root cause

---

*See also: [Advanced Debugging Tricks](advanced-debugging-tricks.md) | [Chaos Engineering](chaos-engineering.md) | [Capacity Planning](capacity-planning.md) | [Incident Command](incident-command.md)*
