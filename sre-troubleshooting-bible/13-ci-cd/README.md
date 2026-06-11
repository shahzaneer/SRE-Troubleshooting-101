# 13 — CI/CD

> **Section Owner:** SRE Platform On-Call
> **Last Reviewed:** 2026-06-11

CI/CD is the artery of software delivery. When the pipeline breaks, the entire engineering organization slows down. This section covers the most common pipeline failures and deployment strategy trade-offs.

---

## Files in This Section

| File | Description | Difficulty |
|------|-------------|------------|
| [pipeline-failures.md](pipeline-failures.md) | Flaky tests, Docker build cache, registry push failures, stuck deployments, GitHub Actions & Jenkins specifics | Intermediate |
| [deployment-strategies.md](deployment-strategies.md) | Blue-Green, Canary, Rolling, Feature Flags — when to use each, trade-offs, rollout procedures | Intermediate |

---

## Cross-Cutting Concerns

- **Security:** Never log secrets in CI output. Use masked environment variables or secrets managers.
- **Compliance:** Deployments must be auditable — who deployed what, when, from which commit.
- **Resilience:** CI/CD systems are production-critical infrastructure. Treat pipeline downtime as a P2 incident.

---

## Quick-Reference Commands

```bash
# GitHub Actions — list recent workflow runs
gh run list --limit 10 --workflow ci.yml

# Jenkins — restart a stuck pipeline stage
jenkins-jobs restart <job-name> <build-number> --from-stage "Deploy"

# Docker — clear all unused build cache to free space
docker builder prune -af --filter "until=72h"

# Kubernetes — rollout status with timeout
kubectl rollout status deployment/api -n production --timeout=300s

# Check if an ECR image exists
aws ecr describe-images --repository-name api --image-ids imageTag=v1.2.3
```

---

## Resources

- [Google SRE Book — Release Engineering](https://sre.google/sre-book/release-engineering/)
- [DORA Metrics](https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance)
- [Continuous Delivery by Jez Humble & Dave Farley](https://continuousdelivery.com/)
- [Kubernetes Deployments Best Practices](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
