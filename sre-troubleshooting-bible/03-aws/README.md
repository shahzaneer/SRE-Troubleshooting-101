# 03 — AWS

> **Diagnosing cloud infrastructure failures: EC2, RDS, S3, IAM, networking, and account-level issues.**
> AWS abstracts the hardware, but the abstractions themselves fail in predictable ways.

---

## Quick Navigation

| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [EC2 Troubleshooting](ec2/ec2-troubleshooting.md) | SSH debug flow, IMDS, CloudWatch agent, EBS performance, burst credits, ENI limits, placement groups | 25 min |
| 2 | [RDS Troubleshooting](rds/rds-troubleshooting.md) | Connection exhaustion, slow queries, failover, read replica lag, IOPS throttling, deadlocks, parameter groups | 25 min |
| 3 | [S3 Troubleshooting](s3/s3-troubleshooting.md) | 403 anatomy, 503 Slow Down, consistency model, cross-account access, presigned URLs, lifecycle policies | 20 min |

---

## AWS-Specific Diagnostic Mindset

Cloud infrastructure debugging differs from traditional systems in key ways:

1. **You can't "walk up to the machine"** — everything is API-driven. Your first tool is the AWS CLI, not SSH.
2. **Many failures are permission-related** — "It doesn't work" often means "the IAM policy doesn't allow it."
3. **Limits and quotas exist everywhere** — API rate limits, instance limits, IOPS limits, connection limits. Always check quotas first.
4. **The shared responsibility model matters** — AWS manages the hypervisor, you manage the OS. Know where the boundary is.
5. **Billing can be the root cause** — A service stops working because the account hit a spending limit or the card expired.

---

## First 30 Seconds: The Universal AWS Diagnostic

```bash
# Is the resource there?
aws ec2 describe-instances --instance-ids i-xxx
aws rds describe-db-instances --db-instance-identifier mydb
aws s3 ls s3://my-bucket/

# What changed recently?
aws cloudtrail lookup-events --lookup-attributes \
  AttributeKey=ResourceName,AttributeValue=i-xxx \
  --max-results 10

# Are there any service health issues?
curl -s https://health.aws.amazon.com/health/status

# What about CloudWatch — any anomalies?
aws cloudwatch describe-alarms --state-value ALARM
```

---

## Key AWS CLI Commands Cheat Sheet

| Command | Purpose | Quick Example |
|---------|---------|---------------|
| `aws ec2 describe-instances` | Get instance details, status, IPs | `aws ec2 describe-instances --instance-ids i-xxx` |
| `aws ec2 get-console-screenshot` | See what's on the VM's screen | `aws ec2 get-console-screenshot --instance-id i-xxx` |
| `aws ec2 describe-instance-status` | System + instance status checks | `aws ec2 describe-instance-status --instance-ids i-xxx` |
| `aws rds describe-db-instances` | DB status, endpoint, parameter group | `aws rds describe-db-instances --db-instance-identifier mydb` |
| `aws rds describe-db-log-files` | List available DB log files | `aws rds describe-db-log-files --db-instance-identifier mydb` |
| `aws cloudwatch get-metric-statistics` | Pull any metric data | `aws cloudwatch get-metric-statistics --namespace AWS/EC2 ...` |
| `aws cloudtrail lookup-events` | Who did what when | `aws cloudtrail lookup-events --lookup-attributes ...` |
| `aws s3api get-bucket-policy` | Get bucket IAM policy | `aws s3api get-bucket-policy --bucket my-bucket` |
| `aws sts get-caller-identity` | Who am I? (Current IAM principal) | `aws sts get-caller-identity` |
| `aws iam simulate-principal-policy` | Test if an action is allowed | `aws iam simulate-principal-policy ...` |

---

## Common AWS Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **Instance limits per region** | AWS has a default of 20 running On-Demand instances per region. Launch failures with "InstanceLimitExceeded" mean you hit the quota. |
| **EIP limits** | Default 5 Elastic IPs per region. Soft limit — request increase. |
| **IAM is eventually consistent** | After creating a role and immediately using it, the first few API calls may fail with AccessDenied. Wait 5-10 seconds. |
| **Security Group rules are stateful** | If you allow outbound, the response is automatically allowed inbound. NACLs are stateless — you must allow BOTH directions. |
| **RDS maintenance windows** | RDS can reboot your database during its 30-minute maintenance window. If you didn't set one, AWS picks a random 30-min window weekly. |
| **S3 bucket names are global** | All S3 bucket names across ALL AWS accounts must be unique. "The bucket already exists" means someone else took that name. |
| **Route 53 DNS has TTLs** | Changing a DNS record in Route 53 doesn't take effect until all caching resolvers' TTLs expire. Plan 24h ahead with low TTL. |
| **CloudWatch Logs retention** | Default log group retention is "Never Expire". This costs money. Set retention per log group. |

---

## References

- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)
- [AWS Health Dashboard](https://health.aws.amazon.com/health/status)
- [AWS CLI Command Reference](https://awscli.amazonaws.com/v2/documentation/api/latest/index.html)
- [AWS Service Quotas](https://docs.aws.amazon.com/general/latest/gr/aws_service_limits.html)
- [AWS re:Post (Community Q&A)](https://repost.aws/)
