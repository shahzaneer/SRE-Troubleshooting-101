# Container Debugging

> **Category:** Containers | Docker | Debugging
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#docker` `#containers` `#debugging` `#oncall`

---

## Table of Contents

1. [Essential Docker Commands](#essential-docker-commands)
2. [Container Exit Codes](#container-exit-codes)
3. [Dockerfile Best Practices](#dockerfile-best-practices)
4. [Multi-Stage Build Debugging](#multi-stage-build-debugging)
5. [nsenter — Host-Level Container Access](#nsenter--host-level-container-access)
6. [Docker System Cleanup](#docker-system-cleanup)
7. [Debugging Running Containers](#debugging-running-containers)

---

## Essential Docker Commands

```bash
# Process and status
docker ps                           # running containers
docker ps -a                        # all containers (including stopped)
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"  # custom format
docker stats                        # live resource usage (CPU, MEM, NET, IO)
docker stats --no-stream            # one-shot stats output

# Inspection
docker inspect CONTAINER            # full JSON config: mounts, networks, env, limits
docker inspect CONTAINER --format '{{.State.Pid}}'                # host PID
docker inspect CONTAINER --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'  # IP
docker inspect CONTAINER | jq '.[0].State.Health'                 # health check status

# Logs
docker logs --tail 100 -f CONTAINER  # follow tail
docker logs --since 5m CONTAINER     # last 5 min
docker logs --timestamps CONTAINER   # with timestamps

# Exec into container
docker exec -it CONTAINER /bin/sh   # shell (alpine/busybox)
docker exec -it CONTAINER /bin/bash # shell (debian/ubuntu)
docker exec CONTAINER cat /proc/1/status  # read process info

# Events stream
docker events --since 10m           # what happened recently?
docker events --filter 'event=die'  # only container deaths
docker events --filter 'container=myapp'  # specific container

# Resource usage (detailed)
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"

# Image inspection
docker image inspect IMAGE --format '{{.RootFS.Layers}}'  # image layers
docker image history IMAGE          # layer sizes and commands
docker image history IMAGE --no-trunc  # full commands
```

---

## Container Exit Codes

### Quick Reference

```text
0   → Success / normal exit.
      App completed its task and exited cleanly.
      Check: Is this a batch/one-shot container? CMD may be a short-lived script.

1   → Application error.
      App crashed, threw an unhandled exception, or exited with non-zero.
      Check: docker logs CONTAINER for stack traces.

126 → Permission denied.
      Can't execute the binary. ENTRYPOINT/CMD file doesn't have +x.
      Check: chmod +x in Dockerfile? COPY preserves permissions?

127 → Command not found.
      Binary doesn't exist at specified path or not in $PATH.
      Example: CMD ["gunicorn"] but gunicorn wasn't installed by pip.

137 → SIGKILL (exit code = 128 + signal_number, 128 + 9 = 137).
      Container was forcibly killed. Most commonly:
      - OOMKilled (container exceeded memory limit)
      - User ran `docker kill` or `kubectl delete --grace-period=0`
      Check: docker inspect CONTAINER | jq '.[0].State.OOMKilled'

139 → SIGSEGV (segfault, 128 + 11 = 139).
      Native code crashed. Check core dumps. Often a C/C++ extension bug.

143 → SIGTERM (128 + 15 = 143).
      Graceful termination requested. Container was asked to stop.
      App may be ignoring SIGTERM — check signal handling.

1/255 → Docker daemon error.
      docker run failed. Container never started. Check image exists, port already in use.
```

### Calculating Exit Codes

```text
exit_code = 128 + signal_number

Signal numbers:
  SIGHUP  (1)   = 129
  SIGINT  (2)   = 130
  SIGQUIT (3)   = 131
  SIGILL  (4)   = 132
  SIGABRT (6)   = 134
  SIGKILL (9)   = 137  ← OOM
  SIGSEGV (11)  = 139  ← segfault
  SIGTERM (15)  = 143  ← graceful stop
```

### Scenario: "Container exits with code 127, works on my machine"

```text
Symptom: Container starts and immediately exits with code 127.
         docker logs myapp: "/bin/sh: 1: [gunicorn]: not found"

Debugging:
  1. docker run -it --entrypoint /bin/sh myapp
  2. Inside: which gunicorn → not found
  3. ls /usr/local/bin/ → no gunicorn
  4. pip list | grep gunicorn → gunicorn IS installed (pip says so)
  5. But pip installs to /home/myuser/.local/bin/, not /usr/local/bin/
  6. Dockerfile uses USER 1000, and /home/myuser/.local/bin is NOT in PATH

  Root cause: pip's --user flag or the USER directive changed the
  Python binary install path. The binary is installed but not discoverable.

  Fix: Add to PATH in Dockerfile:
    ENV PATH="/home/myuser/.local/bin:${PATH}"
  Or install system-wide:
    RUN pip install --no-cache-dir gunicorn  # no --user flag
```

---

## Dockerfile Best Practices

### Layer Ordering for Cache Efficiency

```dockerfile
# BAD — all layers invalidated when ANY source file changes
FROM python:3.12-slim
WORKDIR /app
COPY . .              ← invalidated on every code change
RUN pip install -r requirements.txt  ← reruns even if requirements.txt unchanged

# GOOD — rarely-changed layers first
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .   ← only invalidated when requirements changes
RUN pip install --no-cache-dir -r requirements.txt  ← cached most of the time
COPY . .                  ← invalidated frequently, but fast (no pip rerun)
```

### Multi-Stage Builds

```dockerfile
# Stage 1: Build (large image with compilers)
FROM golang:1.21 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /app ./cmd/api/

# Stage 2: Runtime (tiny image, binary only)
FROM alpine:3.19
RUN apk add --no-cache ca-certificates tzdata
COPY --from=builder /app /app
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD wget --no-verbose --tries=1 --spider http://localhost:8080/healthz || exit 1
ENTRYPOINT ["/app"]
```

### Other Best Practices

```text
1. Pin base image versions (NOT :latest):
   FROM python:3.12.3-slim-bookworm  ← specific tag, never :latest

2. Don't run as root:
   RUN groupadd -r appuser && useradd -r -g appuser appuser
   USER appuser

3. Use COPY, not ADD (ADD has unexpected behaviors with URLs/tarballs):
   COPY --chown=appuser:appuser app/ /app/

4. .dockerignore:
   node_modules/
   .git/
   Dockerfile
   *.md
   .env*

5. Use HEALTHCHECK:
   HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
     CMD curl -f http://localhost:8080/healthz || exit 1

6. Minimize layers:
   RUN apt-get update && apt-get install -y \
       curl ca-certificates \
       && rm -rf /var/lib/apt/lists/*  ← clean up in SAME layer

7. Use exec form for CMD/ENTRYPOINT:
   CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]  ← yes
   CMD gunicorn -b 0.0.0.0:8080 app:app                 ← no (shell form, PID 1 = /bin/sh)
```

---

## Multi-Stage Build Debugging

```bash
# Build only up to a specific stage
docker build --target=builder -t debug-build .

# Inspect the intermediate stage
docker run -it debug-build /bin/sh
# Now you can check: are files where you expect? Is the binary compiled correctly?

# Check layer sizes
docker history myapp

# See what COPY brought in
docker run --rm -it builder /bin/sh -c "ls -la /src && du -sh /src/*"

# Debug a failed build by commenting out later stages
# Then run the container from the last successful stage
```

---

## nsenter — Host-Level Container Access

When `docker exec` fails (container is too broken to accept exec), use `nsenter`
to enter the container's namespaces from the host.

```bash
# Get the container's PID
PID=$(docker inspect CONTAINER --format '{{.State.Pid}}')

# Enter all namespaces (-m mount, -u UTS, -i IPC, -n network, -p PID)
sudo nsenter -t $PID -m -u -i -n -p -- /bin/bash

# Now you're "inside" the container from the host's perspective.
# Can see: file system, processes, network interfaces, environment.

# Network-only entry (debug networking without affecting anything else)
sudo nsenter -t $PID -n -- ip addr
sudo nsenter -t $PID -n -- ss -tlnp
sudo nsenter -t $PID -n -- curl localhost:8080

# Check what's eating memory inside the container
sudo nsenter -t $PID -m -p -- top -bn1

# Check environment
sudo nsenter -t $PID -m -u -- cat /proc/1/environ | tr '\0' '\n'
```

---

## Docker System Cleanup

```bash
# What's using my disk?
docker system df                # summary
docker system df -v             # detailed breakdown

# Typical output:
# TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
# Images          47        3         12.5GB    10.2GB (81%)
# Containers      12        3         450MB     120MB (26%)
# Local Volumes   8         5         3.2GB     1.8GB (56%)
# Build Cache     65        0         4.7GB     4.7GB (100%)

# Cleanup commands
docker system prune -a          # remove ALL unused (images, containers, networks)
docker system prune -a --volumes # also remove unused volumes
docker image prune -a           # remove unused images
docker container prune          # remove stopped containers
docker volume prune             # remove unused volumes
docker builder prune            # remove build cache
docker builder prune --all      # remove ALL build cache (even from current builds)

# Scenario: "CI/CD runner disk full"
# docker system df shows 47GB in dangling images from 6 months of builds.
# docker image prune -a reclaims 47GB. Add to CI cleanup cron:
#   0 3 * * * docker system prune -af --volumes
```

---

## Debugging Running Containers

### From Inside the Container (via exec)

```bash
# Process info
docker exec -it CONTAINER -- cat /proc/1/status    # container's PID 1
docker exec -it CONTAINER -- cat /proc/1/limits    # cgroup resource limits
docker exec -it CONTAINER -- top -bn1              # process list
docker exec -it CONTAINER -- ps aux                # all processes

# Memory
docker exec -it CONTAINER -- cat /proc/meminfo     # memory details
docker exec -it CONTAINER -- free -h               # human-readable memory

# Disk
docker exec -it CONTAINER -- df -h                 # filesystem usage
docker exec -it CONTAINER -- du -sh /app/*         # directory sizes

# Network
docker exec -it CONTAINER -- ss -tlnp              # listening ports
docker exec -it CONTAINER -- ss -tan               # all TCP connections
docker exec -it CONTAINER -- ip addr               # network interfaces

# Files
docker exec -it CONTAINER -- cat /etc/hosts        # hostname resolution
docker exec -it CONTAINER -- cat /etc/resolv.conf  # DNS config
docker exec -it CONTAINER -- env | sort            # environment variables

# Application
docker exec -it CONTAINER -- strace -p 1 -e trace=network  # trace PID 1 network calls
docker exec -it CONTAINER -- lsof -p 1             # open files of PID 1
```

### From the Host (without exec)

```bash
# Find container's PID
PID=$(docker inspect CONTAINER --format '{{.State.Pid}}')

# Process tree on host
ps aux | grep $PID
pstree -p $PID

# Container's filesystem on host (for any storage driver)
sudo ls -la /proc/$PID/root/          # container's root filesystem
sudo cat /proc/$PID/root/etc/hostname  # read a file inside container
sudo ls -la /proc/$PID/root/app/       # explore from host

# Network namespace from host
sudo nsenter -t $PID -n -- ss -tlnp    # listening ports
sudo nsenter -t $PID -n -- ip addr     # interfaces
sudo nsenter -t $PID -n -- tcpdump -i eth0 -c 10  # packet capture

# cgroup info
cat /proc/$PID/cgroup                  # cgroup path for this container
cat /sys/fs/cgroup/memory$(cat /proc/$PID/cgroup | grep memory | cut -d: -f3)/memory.limit_in_bytes
cat /sys/fs/cgroup/cpu$(cat /proc/$PID/cgroup | grep cpu | cut -d: -f3)/cpu.stat
```

### Docker Health Check Debugging

```bash
# Check health status
docker inspect CONTAINER --format '{{.State.Health.Status}}'
# Output: healthy, unhealthy, or starting

# See health check log
docker inspect CONTAINER --format '{{json .State.Health}}' | jq .
# Shows: last check output, consecutive failures, check history

# Manual health check test
docker exec CONTAINER curl -f http://localhost:8080/healthz && echo "OK" || echo "FAIL"

# When health check fails:
#   - Container stays running but marked unhealthy
#   - Docker swarm: task is replaced
#   - ECS: task is killed and replaced
#   - Kubernetes: pod is restarted (if liveness probe)
```

---

## References

- [Docker CLI Reference](https://docs.docker.com/engine/reference/commandline/cli/)
- [Dockerfile Best Practices](https://docs.docker.com/develop/develop-images/dockerfile_best-practices/)
- [Multi-Stage Builds](https://docs.docker.com/build/building/multi-stage/)
- [nsenter Man Page](https://man7.org/linux/man-pages/man1/nsenter.1.html)
- [Docker Healthcheck](https://docs.docker.com/engine/reference/builder/#healthcheck)
