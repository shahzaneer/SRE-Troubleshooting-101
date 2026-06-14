# 04 — Containers

> **Debugging container runtimes: Docker, containerd, image builds, and container internals.**
> Containers are the packaging format of modern infrastructure. When they break, you need to debug from the host up.

---

## Quick Navigation

| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [Container Debugging](container-debugging.md) | Docker commands, exit codes, Dockerfile best practices, nsenter, health checks | 15 min |

---

## Container First 30 Seconds

```bash
# What's running?
docker ps

# What's broken?
docker ps -a --filter "status=exited"
docker ps -a --filter "status=created"   # never started

# What's using resources?
docker stats --no-stream

# What just happened?
docker events --since 10m

# What's eating disk?
docker system df
```

---

## Common Container Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **Exit code 127** | Binary not found. pip installed to user-local path not in $PATH. |
| **Exit code 126** | Permission denied. Missing +x on ENTRYPOINT/CMD. |
| **Exit code 137** | OOMKilled. Container exceeded memory cgroup limit. |
| **Exit code 139** | SIGSEGV. Native code crash (C/C++ extension segfault). |
| **Exit code 143** | SIGTERM. Graceful stop requested, app may be ignoring it. |
| **Image pull fails** | Registry unreachable, bad credentials, or manifest not found. |
| **Build fails at COPY** | .dockerignore not excluding large dirs, or missing source files. |
| **Health check failing** | Endpoint returns non-200, timeout too low, or wrong port. |
| **Disk full on host** | Dangling images/volumes from CI builds. `docker system prune -a`. |
| **Can't exec into container** | Container too broken (PID 1 died). Use `nsenter` from host. |

---

## References

- [Docker Documentation](https://docs.docker.com/)
- [Dockerfile Best Practices](https://docs.docker.com/develop/develop-images/dockerfile_best-practices/)
- [containerd Debugging](https://github.com/containerd/containerd/blob/main/docs/ops.md)
- [nsenter Man Page](https://man7.org/linux/man-pages/man1/nsenter.1.html)
