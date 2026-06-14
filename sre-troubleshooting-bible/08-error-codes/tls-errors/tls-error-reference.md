# TLS/SSL Error Reference
> **Category:** Security | TLS | Error Codes
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#tls` `#ssl` `#security` `#oncall`

---

## Table of Contents
1. [SSL_ERROR_RX_RECORD_TOO_LONG](#1-ssl_error_rx_record_too_long)
2. [CERTIFICATE_VERIFY_FAILED](#2-certificate_verify_failed)
3. [SSL: WRONG_VERSION_NUMBER](#3-ssl-wrong_version_number)
4. [ERR_CERT_COMMON_NAME_INVALID](#4-err_cert_common_name_invalid)
5. [ERR_CERT_DATE_INVALID](#5-err_cert_date_invalid)
6. [DEPTH_ZERO_SELF_SIGNED_CERT](#6-depth_zero_self_signed_cert)
7. [UNABLE_TO_GET_ISSUER_CERT_LOCALLY](#7-unable_to_get_issuer_cert_locally)
8. [ERR_SSL_VERSION_OR_CIPHER_MISMATCH](#8-err_ssl_version_or_cipher_mismatch)
9. [Diagnostic Scripts](#9-diagnostic-scripts)
10. [Certificate Expiry Monitoring](#10-certificate-expiry-monitoring)
11. [Related Sections](#11-related-sections)

---

## 1. SSL_ERROR_RX_RECORD_TOO_LONG

### What It Means
The client received a TLS record that exceeds the maximum allowed length (16,384 bytes). This almost always means **the server sent plain HTTP to a client expecting HTTPS**, or vice versa. The TLS handshake bytes have a specific structure; raw HTTP (`HTTP/1.1 200 OK\r\n...`) looks like a garbage-length record.

### Classic Scenario
> **3:14 AM Page:** "API healthcheck failing — SSL error across all instances."
>
> A developer was testing locally with `curl localhost:8443` (no `https://` prefix). curl defaults to HTTP over port 8443, so it sends a plain `GET / HTTP/1.1` request. The server, expecting a TLS ClientHello, interprets `GET` as a TLS record with a nonsensical length, and responds with an alert, which curl reports as `SSL_ERROR_RX_RECORD_TOO_LONG`.
>
> Meanwhile, the production load balancer was reconfigured during a deploy: the backend port was changed from 8080 (HTTP) to 8443 (where the app listens with TLS), but the health check was left as plain HTTP. Same error — plain health-check HTTP hitting the TLS port.

### Diagnostic Commands

```bash
# Test what the server actually expects on a port
openssl s_client -connect host:443 -servername host.example.com
# If this connects and shows a certificate, the port expects TLS.

# If s_client fails with "wrong version number" or hangs, try plain HTTP:
curl -v http://host:443

# Check if your curl is sending TLS (https://) or not (http://)
curl -v https://host:8443 2>&1 | head -20
curl -v http://host:8443 2>&1 | head -20

# Quick check: just see if a port speaks TLS
echo | timeout 5 openssl s_client -connect host:8443 2>&1 | grep -E "(CONNECTED|verify error|Verify return code)"
```

### Fix
- Use `https://` in URLs when connecting to TLS-enabled ports.
- In load balancer / reverse proxy configs, ensure health checks use the correct protocol.
- In Nginx: `proxy_pass https://backend:8443;` (not `http://`).
- In HAProxy: `server s1 10.0.1.5:8443 ssl verify none`.
- For curl scripts, always explicitly use `https://`.

### Code That Triggers This

```python
# BUG: plain HTTP to TLS port
import requests
r = requests.get("http://localhost:8443")  # Sends HTTP, server expects TLS

# FIX
r = requests.get("https://localhost:8443", verify="/path/to/ca.pem")
```

```java
// BUG: wrong protocol
URL url = new URL("http://service.internal:8443/api");  // TLS port, HTTP protocol
HttpURLConnection conn = (HttpURLConnection) url.openConnection();

// FIX
URL url = new URL("https://service.internal:8443/api");
HttpsURLConnection conn = (HttpsURLConnection) url.openConnection();
```

```javascript
// BUG: plain http to TLS port
const https = require('https');
// Wrong module — using http instead of https
const http = require('http');
http.get('http://localhost:8443/health', (res) => { ... });  // Won't work

// FIX
const https = require('https');
https.get('https://localhost:8443/health', { rejectUnauthorized: true }, (res) => { ... });
```

---

## 2. CERTIFICATE_VERIFY_FAILED

### What It Means
The TLS handshake completed but the client could not validate the server's certificate chain. This has three common root causes:
1. **Missing intermediate certificate** (most common in production)
2. **Self-signed certificate** (common in development)
3. **Expired certificate**

### Classic Scenario: The Missing Intermediate
> **New internal service deployed.** The cert is signed by the corporate CA (e.g., `corp-root` → `corp-issuing-2025` → `service-cert`). The server has only `service-cert` and `corp-root` in its cert bundle. The intermediate `corp-issuing-2025` is missing.
>
> **Machines that already have `corp-issuing-2025` in their system trust store** (most dev laptops, configured via MDM) verify the cert fine because the OS fills in the missing intermediate.
>
> **Machines that DON'T have the intermediate** (fresh containers, CI runners, third-party services) fail with `CERTIFICATE_VERIFY_FAILED` because they can't construct a trust chain to a known root.
>
> **The fix is on the server side:** the server must send the full chain (server cert + intermediate) in the TLS handshake. Never rely on clients having intermediates installed.

### Diagnostic Commands

```bash
# Show the full certificate chain the server sends — check if intermediate is present
openssl s_client -connect host:443 -showcerts 2>&1 | grep -E "(s:|i:|depth|verify error|Verify return code)"

# The output will look like:
# depth=0 CN = service.example.com        # leaf cert
# depth=1 CN = Corp Issuing CA 2025      # intermediate (MISSING if only depth=0 shows)
# depth=2 CN = Corp Root CA              # root (should be in trust store)
# verify error:num=20:unable to get local issuer certificate  # <-- the error

# Check each cert in the chain
openssl s_client -connect host:443 -showcerts 2>/dev/null | \
  awk '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/' | \
  while cert=$(awk '1'); do
    echo "$cert" | openssl x509 -noout -subject -issuer -dates
    echo "---"
  done

# Verify a local certificate bundle
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt server-cert.pem
# Expected: server-cert.pem: OK
# If error: server-cert.pem: C = US, ST = CA, L = SF, O = Corp, CN = service.example.com
#            error 20 at 0 depth lookup: unable to get local issuer certificate

# Check if a specific CA is in your trust store
ls -la /etc/ssl/certs/ | grep -i corp
awk -v cmd='openssl x509 -noout -subject' '/BEGIN/{close(cmd)};{print | cmd}' < /etc/ssl/certs/ca-certificates.crt | grep -i corp
```

### Fix: Server-Side (Correct Way)

```bash
# The cert bundle the server should serve must include the intermediate:
cat server-cert.pem intermediate-ca.pem > fullchain.pem

# For Nginx:
# ssl_certificate /etc/nginx/ssl/fullchain.pem;  # combined file

# Verify the server now sends the full chain
openssl s_client -connect host:443 -showcerts 2>&1 | grep "depth="
```

### Fix: Client-Side (Workaround — Only for Testing)

```bash
# Explicitly provide the CA bundle
curl --cacert /path/to/ca-bundle.pem https://host/

# Python
requests.get("https://host/", verify="/path/to/ca-bundle.pem")

# Skip verification (DO NOT USE IN PRODUCTION)
curl -k https://host/
```

---

## 3. SSL: WRONG_VERSION_NUMBER

### What It Means
The TLS protocol version negotiated during the handshake is incompatible. The client proposed versions the server doesn't support, or the server responded with a version the client doesn't understand.

### Classic Scenario: Legacy Client, Modern Server
> **Financial institution.** A legacy batch processing system, running Java 7 (TLS 1.0 only), connects to an upstream API that was recently upgraded to require TLS 1.2 minimum. The Java 7 client sends a ClientHello with `supported_versions: TLSv1.0, SSLv3.0`. The server, configured with `ssl_protocols TLSv1.2 TLSv1.3;`, rejects the handshake. The Java client gets `SSLHandshakeException: Received fatal alert: protocol_version`.
>
> The fix is upgrading the client JVM. Interim workaround: deploy an TLS-terminating proxy (haproxy, nginx, stunnel) in front of the legacy app that speaks TLS 1.2+ upstream but accepts TLS 1.0 from the legacy client on localhost.

### Diagnostic Commands

```bash
# Check which TLS versions a server supports
nmap --script ssl-enum-ciphers -p 443 host

# Quick test: try each version explicitly
for v in tls1 tls1_1 tls1_2 tls1_3; do
  echo -n "$v: "
  echo | timeout 3 openssl s_client -connect host:443 -"$v" 2>&1 | grep -E "(CONNECTED|alert|Protocol)"
done

# Check server's supported protocols
openssl s_client -connect host:443 -tls1_3 2>&1 | grep -E "Protocol|Cipher"
openssl s_client -connect host:443 -tls1_2 2>&1 | grep -E "Protocol|Cipher"
openssl s_client -connect host:443 -tls1_1 2>&1 | grep -E "Protocol|Cipher|alert"

# Nginx misconfiguration cheatsheet:
# TOO RESTRICTIVE:
#   ssl_protocols TLSv1.3;    # Only 1.3 — older clients fail
# CORRECT:
#   ssl_protocols TLSv1.2 TLSv1.3;
# BACKWARDS-COMPAT (not recommended unless required):
#   ssl_protocols TLSv1.1 TLSv1.2 TLSv1.3;
```

### Java Debugging

```bash
# Launch JVM with SSL debugging to see protocol negotiation
java -Djavax.net.debug=ssl:handshake:verbose \
     -Dhttps.protocols=TLSv1.2,TLSv1.3 \
     -jar myapp.jar

# The debug output will show:
# *** ClientHello, TLSv1.2
# RandomCookie: ...
# Cipher Suites: [TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384, ...]
# ---
# Ignoring unsupported cipher suite: TLS_RSA_WITH_AES_128_CBC_SHA for TLSv1.3
```

### Python Version Override

```python
import ssl
import urllib.request

# Explicitly set the TLS version
ctx = ssl.create_default_context()
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.maximum_version = ssl.TLSVersion.TLSv1_3
ctx.check_hostname = True
ctx.verify_mode = ssl.CERT_REQUIRED

response = urllib.request.urlopen("https://host/", context=ctx)
```

---

## 4. ERR_CERT_COMMON_NAME_INVALID

### What It Means
The hostname used in the connection URL does not match any of the names (CN or SAN — Subject Alternative Name) in the server's certificate. This was historically `CN` only; modern browsers and libraries require a match in the **SAN extension**.

### Classic Scenario: IP Address Instead of Hostname
> **Microservice migration.** The `user-service` pod gets a new IP `10.0.4.12` in Kubernetes. The on-call engineer SSHs to a jump host and runs:
> ```
> curl https://10.0.4.12/health
> ```
> The cert is issued for `user-service.internal.example.com`, not `10.0.4.12`. `curl` rightfully rejects the connection because the IP doesn't match any SAN entry. The health check fails.
>
> **Correct command:** `curl --resolve user-service.internal.example.com:443:10.0.4.12 https://user-service.internal.example.com/health`
>
> This tells curl to resolve the DNS name locally and connect to the IP, but still use the DNS name for SNI and hostname verification.

### Diagnostic Commands

```bash
# Check what names are in the certificate
echo | openssl s_client -connect host:443 2>/dev/null | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"
# Output: DNS:api.example.com, DNS:*.example.com, DNS:api-east.example.com

# The CN is in the Subject:
echo | openssl s_client -connect host:443 2>/dev/null | openssl x509 -noout -subject

# Test with explicit SNI
openssl s_client -servername actualhostname.example.com -connect 10.0.1.5:443

# If the cert is for *.example.com but you're connecting to sub.domain.example.com:
# *.example.com matches sub.example.com but NOT sub.domain.example.com
# Wildcards only match ONE level of subdomain.

# Curl with custom DNS resolution (use hostname, connect to IP)
curl --resolve app.example.com:443:10.0.1.5 https://app.example.com/

# Override hostname verification for testing only
curl -k --resolve app.example.com:443:10.0.1.5 https://app.example.com/
```

### Fix

The certificate must include the hostname you're connecting to. Options:
1. Use the correct DNS name that matches the cert
2. Reissue the cert with the correct SAN entries
3. Add a DNS record that points to the IP and use that hostname
4. For internal services, use a wildcard cert: `*.internal.example.com`

### Node.js Handling

```javascript
const https = require('https');

// When connecting via IP, set the servername for SNI
const options = {
  host: '10.0.1.5',          // actual connection target
  port: 443,
  servername: 'app.example.com',  // SNI hostname (must match cert)
  method: 'GET',
  path: '/health',
  rejectUnauthorized: true,
};

const req = https.request(options, (res) => {
  res.on('data', (d) => process.stdout.write(d));
});
req.end();
```

---

## 5. ERR_CERT_DATE_INVALID

### What It Means
The certificate's `notBefore` and `notAfter` validity window does not include the current system time. Either the cert is **expired** (most common) or the cert is **not yet valid** (e.g., clock skew, premature deployment), or the **system clock is wrong** (NTP drift).

### Classic Scenario: Midnight Expiry
> **Monday 00:01 AM.** All payments API traffic returns `ERR_CERT_DATE_INVALID`. The wildcard cert expired at exactly 00:00 UTC. The cert renewal cron that runs every 30 days didn't trigger because the certbot timer unit had been accidentally disabled during last month's maintenance window. Nobody noticed because cert expiry alerts went to an unmonitored mailing list.
>
> **Root cause:** No monitoring on cert expiry. The cert had a 90-day validity. Renewals were manual. No alerting on days-remaining.

### Diagnostic Commands

```bash
# Check cert dates (local file)
openssl x509 -noout -dates -in /path/to/cert.pem
# notBefore=Jan 15 00:00:00 2026 GMT
# notAfter=Apr 15 23:59:59 2026 GMT

# Check cert dates (remote server)
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | \
  openssl x509 -noout -dates

# Check how many days until expiry (one-liner)
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | \
  openssl x509 -noout -enddate | cut -d= -f2 | \
  xargs -I{} sh -c 'echo $(( ($(date -j -f "%b %d %T %Y %Z" "{}" +%s) - $(date +%s)) / 86400 )) days remaining'

# Check if system clock is correct (cert validity depends on it)
timedatectl status
# If NTP is not synchronized, certs will fail
# Fix: systemctl restart systemd-timesyncd && timedatectl set-ntp true

# NTP drift check
chronyc tracking
ntpq -p
```

### Expiry One-Liner for Multiple Hosts

```bash
#!/bin/bash
# check-certs.sh — check certs for a list of hosts
HOSTS=("example.com" "api.example.com" "admin.example.com")
THRESHOLD_DAYS=30
TODAY=$(date +%s)

for host in "${HOSTS[@]}"; do
  enddate_str=$(echo | timeout 5 openssl s_client -servername "$host" -connect "$host":443 2>/dev/null | \
    openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [ -z "$enddate_str" ]; then
    echo "[ERROR] $host — failed to connect or get cert"
    continue
  fi
  if [[ "$OSTYPE" == "darwin"* ]]; then
    enddate_ts=$(date -j -f "%b %d %T %Y %Z" "$enddate_str" +%s 2>/dev/null)
  else
    enddate_ts=$(date -d "$enddate_str" +%s)
  fi
  days_left=$(( (enddate_ts - TODAY) / 86400 ))
  if [ "$days_left" -lt "$THRESHOLD_DAYS" ]; then
    echo "[WARN] $host — EXPIRES IN $days_left DAYS"
  else
    echo "[OK]   $host — $days_left days left"
  fi
done
```

### Python: Certificate Expiry Check With urllib

```python
import ssl
import socket
from datetime import datetime, timezone
from cryptography import x509
from cryptography.hazmat.backends import default_backend

def get_cert_expiry(hostname, port=443, timeout=5):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            cert_der = ssock.getpeercert(binary_form=True)
            cert = x509.load_der_x509_certificate(cert_der, default_backend())
            return cert.not_valid_after_utc

hosts = ["example.com", "api.example.com", "admin.example.com"]
now = datetime.now(timezone.utc)

for host in hosts:
    try:
        expiry = get_cert_expiry(host)
        days_left = (expiry - now).days
        status = "WARN" if days_left < 30 else "OK"
        print(f"[{status:4s}] {host:30s} — {days_left:3d} days until expiry ({expiry.isoformat()})")
    except Exception as e:
        print(f"[ERROR] {host:30s} — {e}")
```

---

## 6. DEPTH_ZERO_SELF_SIGNED_CERT

### What It Means
The server is presenting a self-signed certificate (depth 0 = the leaf cert, not chained to any CA). The client's trust store does not recognize the issuer because there is no issuer — the cert signed itself.

### Classic Scenario: Development Cert in Staging
> **Staging environment down.** A developer generated a self-signed cert with `openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes` for local testing. The Terraform config was accidentally pointed at the same cert files for the staging deployment. The monitoring system (Prometheus blackbox exporter) started failing all staging probes because it doesn't trust self-signed certs. The developer's local env passed because they had `NODE_EXTRA_CA_CERTS` set.

### Diagnostic Commands

```bash
# Identify if a cert is self-signed: issuer == subject
echo | openssl s_client -connect host:443 2>/dev/null | openssl x509 -noout -subject -issuer
# If subject and issuer are identical → self-signed

# Check the certificate chain depth
echo | openssl s_client -connect host:443 2>/dev/null | openssl s_client -showcerts | \
  awk '/BEGIN CERTIFICATE/{n++} /END CERTIFICATE/{print "depth=" n-1}'

# Verify locally
openssl verify -verbose cert.pem
# error 18 at 0 depth lookup: self signed certificate

# Quick fix for internal systems: create a proper CA and sign certs
# 1. Create internal CA
openssl genrsa -out internal-ca.key 4096
openssl req -new -x509 -days 3650 -key internal-ca.key -out internal-ca.crt \
  -subj "/C=US/O=MyOrg/CN=Internal Root CA"

# 2. Create server key and CSR
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/C=US/O=MyOrg/CN=service.internal"

# 3. Sign with internal CA (add SANs!)
cat > san.cnf <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
[v3_req]
subjectAltName = @alt_names
[alt_names]
DNS.1 = service.internal
DNS.2 = *.service.internal
IP.1 = 10.0.1.50
EOF

openssl x509 -req -in server.csr -CA internal-ca.crt -CAkey internal-ca.key \
  -CAcreateserial -out server.crt -days 365 -extfile san.cnf -extensions v3_req

# 4. Distribute internal-ca.crt to all clients' trust stores
# Debian/Ubuntu:
sudo cp internal-ca.crt /usr/local/share/ca-certificates/internal-ca.crt
sudo update-ca-certificates
# RHEL/CentOS:
sudo cp internal-ca.crt /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

### Python: Bypassing Self-Signed Certs (Testing Only)

```python
import ssl
import urllib.request

# NEVER do this in production code
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# For production, use proper CA:
ctx = ssl.create_default_context(cafile="/etc/ssl/certs/internal-ca.crt")
ctx.check_hostname = True
ctx.verify_mode = ssl.CERT_REQUIRED

response = urllib.request.urlopen("https://service.internal/", context=ctx)
```

---

## 7. UNABLE_TO_GET_ISSUER_CERT_LOCALLY

### What It Means
The client received the server's certificate but cannot find the issuing CA certificate in its local trust store. This differs from `DEPTH_ZERO_SELF_SIGNED_CERT` in that the cert IS signed by a CA — the client just doesn't have that CA.

### Classic Scenario: Private CA in Container
> **Kubernetes cluster.** The platform team issues internal service certs from a private CA (HashiCorp Vault PKI). A new application container (built from `scratch` or a minimal `alpine` base) starts up and tries to call `https://auth.internal/`. It fails with `UNABLE_TO_GET_ISSUER_CERT_LOCALLY` because the minimal container image doesn't include the private CA certificate.
>
> **Fix:** The Dockerfile was updated to `COPY` the internal CA cert and run `update-ca-certificates`, or the app was configured to load the CA cert at runtime from a mounted secret.

### Diagnostic Commands

```bash
# Check what CAs are in your trust store
awk -v cmd='openssl x509 -noout -subject' '/BEGIN/{close(cmd)};{print | cmd}' < \
  /etc/ssl/certs/ca-certificates.crt 2>/dev/null | head -20

# Find the issuer of a cert (what CA should we have?)
echo | openssl s_client -connect host:443 2>/dev/null | openssl x509 -noout -issuer
# issuer=C = US, ST = CA, L = SF, O = MyOrg, CN = MyOrg Internal Root CA
# ^ This is the CA we need in our trust store

# Check if we have that specific CA
ls /etc/ssl/certs/ | grep -i myorg

# MacOS trust store location
security find-certificate -a -c "MyOrg" /Library/Keychains/System.keychain
security find-certificate -a -c "MyOrg" ~/Library/Keychains/login.keychain-db

# Java trust store (cacerts)
keytool -list -keystore $JAVA_HOME/lib/security/cacerts -storepass changeit | grep -i myorg
```

### Java: Adding a CA to Trust Store

```bash
# Add private CA to Java's default trust store
keytool -import -trustcacerts -file internal-ca.crt \
  -alias myorg-internal-ca -keystore $JAVA_HOME/lib/security/cacerts \
  -storepass changeit -noprompt

# Or use a custom trust store at runtime
keytool -import -trustcacerts -file internal-ca.crt \
  -alias myorg-internal-ca -keystore custom-cacerts.jks \
  -storepass changeit -noprompt
java -Djavax.net.ssl.trustStore=custom-cacerts.jks \
     -Djavax.net.ssl.trustStorePassword=changeit \
     -jar app.jar
```

### Node.js: Custom CA Loading

```javascript
const https = require('https');
const fs = require('fs');

// Load internal CA cert
const internalCA = fs.readFileSync('/etc/ssl/certs/internal-ca.crt');

const agent = new https.Agent({
  ca: internalCA,  // Append to (or replace) the default CAs
  keepAlive: true,
});

const options = {
  hostname: 'auth.internal',
  port: 443,
  path: '/health',
  method: 'GET',
  agent: agent,
};

https.request(options, (res) => {
  res.on('data', (d) => process.stdout.write(d));
}).end();
```

---

## 8. ERR_SSL_VERSION_OR_CIPHER_MISMATCH

### What It Means
The client and server could not agree on a common TLS version AND cipher suite. No overlap in their supported cipher sets. This typically happens when:
- Server only allows modern ciphers (e.g., ECDHE + AES-GCM) and client only supports older ciphers (RSA + AES-CBC).
- Server requires TLS 1.3 but client maxes out at TLS 1.2.
- Server cipher list is too restrictive (e.g., `ssl_ciphers HIGH:!aNULL:!MD5;` which is actually fine, but client uses very old ciphers).

### Classic Scenario: Hardened PCI-DSS Server vs Legacy Banking Client
> **Compliance-induced outage.** After a PCI-DSS audit, the security team hardened the API server's TLS config:
> ```nginx
> ssl_protocols TLSv1.3;
> ssl_ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
> ```
> A legacy partner integration, running on Windows Server 2008 with an ancient .NET framework (SChannel that only supports TLS 1.0 with RSA-AES-CBC ciphers), suddenly can't connect. The cipher suites have zero overlap. The partner gets `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`.
>
> **Resolution:** A TLS-terminating proxy (HAProxy with TLS 1.0-1.3 and broader cipher support) was placed between the legacy partner and the hardened API. The proxy handles the old TLS on the external side and forwards via modern TLS internally.

### Diagnostic Commands

```bash
# List all ciphers supported by a server
nmap --script ssl-enum-ciphers -p 443 host.example.com

# Test each cipher individually
CIPHERS=$(openssl ciphers 'ALL:eNULL' | tr ':' ' ')
for cipher in $CIPHERS; do
  echo -n "$cipher: "
  result=$(echo | timeout 3 openssl s_client -cipher "$cipher" -connect host:443 2>&1)
  if echo "$result" | grep -q "BEGIN CERTIFICATE"; then
    echo "OK"
  else
    echo "FAIL"
  fi
done

# Check which ciphers the client supports
openssl ciphers -v 'ALL:COMPLEMENTOFALL' | head -30

# Show enabled ciphers on the current system's OpenSSL
openssl ciphers -v | awk '{print $1, $3}' | head -20

# Server-side: check what Nginx is actually configured with
nginx -T 2>/dev/null | grep -E "(ssl_protocols|ssl_ciphers)"

# Benchmark: what cipher is actually negotiated?
echo | openssl s_client -connect host:443 -tls1_3 2>/dev/null | grep -E "Cipher\s+:|Protocol\s+:"
```

### Python: Check Negotiated Cipher

```python
import ssl
import socket

hostname = "example.com"
ctx = ssl.create_default_context()

with socket.create_connection((hostname, 443)) as sock:
    with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
        print(f"Protocol: {ssock.version()}")
        print(f"Cipher  : {ssock.cipher()}")
        # Output: Cipher: ('TLS_AES_256_GCM_SHA384', 'TLSv1.3', 256)
```

### SSLLabs-Style Cipher Audit Script

```python
#!/usr/bin/env python3
"""ssl-cipher-audit.py — check what ciphers a server accepts"""

import ssl
import socket
import sys

def test_cipher(hostname, port, cipher_name):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers(cipher_name)
    try:
        with socket.create_connection((hostname, port), timeout=3) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname):
                return True
    except Exception:
        return False

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "example.com"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 443

    common_ciphers = [
        "TLS_AES_256_GCM_SHA384", "TLS_AES_128_GCM_SHA256",
        "TLS_CHACHA20_POLY1305_SHA256",
        "ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384", "ECDHE-ECDSA-AES128-GCM-SHA256",
        "DHE-RSA-AES256-GCM-SHA384", "DHE-RSA-AES128-GCM-SHA256",
        "AES256-GCM-SHA384", "AES128-GCM-SHA256",
    ]
    for cipher in common_ciphers:
        status = "OK" if test_cipher(host, port, cipher) else "FAIL"
        print(f"[{status:4s}] {cipher}")
```

---

## 9. Diagnostic Scripts

### Python: Full TLS Diagnostic Script

```python
#!/usr/bin/env python3
"""
tls-diagnostics.py — comprehensive TLS/SSL endpoint diagnostic tool
Usage: python3 tls-diagnostics.py example.com 443
"""

import ssl
import socket
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from cryptography import x509
from cryptography.hazmat.backends import default_backend


class TLSDiagnostic:
    def __init__(self, hostname, port=443, timeout=5):
        self.hostname = hostname
        self.port = port
        self.timeout = timeout

    def run(self):
        print(f"=" * 70)
        print(f"TLS Diagnostic Report: {self.hostname}:{self.port}")
        print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        print(f"=" * 70)
        print()

        self._check_connectivity()
        self._check_tls_versions()
        self._check_certificate()
        self._check_cipher()
        self._check_chain()

    def _check_connectivity(self):
        print("[1] Connectivity Check")
        try:
            sock = socket.create_connection((self.hostname, self.port), timeout=self.timeout)
            sock.close()
            print(f"    TCP connect: OK")
        except socket.timeout:
            print(f"    TCP connect: TIMEOUT — port {self.port} not reachable")
        except socket.gaierror:
            print(f"    TCP connect: DNS FAILURE — cannot resolve {self.hostname}")
        except Exception as e:
            print(f"    TCP connect: FAILED — {e}")
        print()

    def _check_tls_versions(self):
        print("[2] TLS Version Support")
        versions = [
            (ssl.TLSVersion.TLSv1_3, "TLSv1.3"),
            (ssl.TLSVersion.TLSv1_2, "TLSv1.2"),
            (ssl.TLSVersion.TLSv1_1, "TLSv1.1"),
            (ssl.TLSVersion.TLSv1,   "TLSv1.0"),
        ]
        for ver, label in versions:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.minimum_version = ver
                ctx.maximum_version = ver
                sock = socket.create_connection((self.hostname, self.port), timeout=self.timeout)
                ssock = ctx.wrap_socket(sock, server_hostname=self.hostname)
                negotiated = ssock.version()
                ssock.close()
                print(f"    {label:8s}: OK (negotiated {negotiated})")
            except Exception as e:
                error_msg = str(e).split('\n')[0]
                print(f"    {label:8s}: FAIL — {error_msg}")
        print()

    def _check_certificate(self):
        print("[3] Certificate Information")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = socket.create_connection((self.hostname, self.port), timeout=self.timeout)
            ssock = ctx.wrap_socket(sock, server_hostname=self.hostname)

            cert_der = ssock.getpeercert(binary_form=True)
            cert = x509.load_der_x509_certificate(cert_der, default_backend())

            subject = cert.subject.rfc4514_string()
            issuer = cert.issuer.rfc4514_string()
            not_before = cert.not_valid_before_utc
            not_after = cert.not_valid_after_utc
            serial = f"{cert.serial_number:040x}"
            fingerprint = cert.fingerprint(x509.hashes.SHA256()).hex(':')

            now = datetime.now(timezone.utc)
            days_remaining = (not_after - now).days
            is_valid = not_before <= now <= not_after
            is_self_signed = (subject == issuer)

            print(f"    Subject:       {subject}")
            print(f"    Issuer:        {issuer}")
            print(f"    Serial:        {serial}")
            print(f"    SHA256 FP:     {fingerprint}")
            print(f"    Valid from:    {not_before.isoformat()}")
            print(f"    Valid until:   {not_after.isoformat()}")
            print(f"    Days left:     {days_remaining}")
            print(f"    Currently valid: {'YES' if is_valid else 'NO — CERT IS INVALID'}")
            print(f"    Self-signed:   {'YES' if is_self_signed else 'NO'}")

            # SANs
            try:
                san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                sans = san_ext.value.get_values_for_type(x509.DNSName)
                print(f"    SANs (DNS):    {', '.join(sans[:10])}")
            except x509.ExtensionNotFound:
                print(f"    SANs (DNS):    (none)")

            ssock.close()

            # Verify with default trust store
            print()
            print(f"    Trust store verification:")
            try:
                ctx2 = ssl.create_default_context()
                sock2 = socket.create_connection((self.hostname, self.port), timeout=self.timeout)
                ssock2 = ctx2.wrap_socket(sock2, server_hostname=self.hostname)
                ssock2.close()
                print(f"        PASS — verified against system trust store")
            except ssl.SSLCertVerificationError as e:
                print(f"        FAIL — {e.verify_message}")
        except Exception as e:
            print(f"    ERROR: {e}")
        print()

    def _check_cipher(self):
        print("[4] Negotiated Cipher")
        try:
            ctx = ssl.create_default_context()
            sock = socket.create_connection((self.hostname, self.port), timeout=self.timeout)
            ssock = ctx.wrap_socket(sock, server_hostname=self.hostname)
            cipher, protocol_version, bits = ssock.cipher()
            ssock.close()
            print(f"    Cipher:   {cipher}")
            print(f"    Protocol: {protocol_version}")
            print(f"    Bits:     {bits}")
            if "GCM" in cipher or "CHACHA20" in cipher:
                print(f"    AEAD:     YES (authenticated encryption)")
            else:
                print(f"    AEAD:     NO — consider upgrading to GCM/CHACHA20")
        except Exception as e:
            print(f"    ERROR: {e}")
        print()

    def _check_chain(self):
        print("[5] Certificate Chain")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = socket.create_connection((self.hostname, self.port), timeout=self.timeout)
            ssock = ctx.wrap_socket(sock, server_hostname=self.hostname)
            chain = ssock.getpeercertchain()
            if chain:
                for i, cert_info in enumerate(chain):
                    cert_der = ssl.DER_cert_to_PEM_cert(cert_info).encode()
                    cert = x509.load_pem_x509_certificate(cert_der, default_backend())
                    print(f"    depth={i}:      CN={cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value}")
                    print(f"    depth={i} issuer: {cert.issuer.rfc4514_string()}")
            else:
                print(f"    Chain: only leaf cert returned by server")
            ssock.close()
        except Exception as e:
            print(f"    ERROR: {e}")
        print()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "example.com"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 443
    TLSDiagnostic(host, port).run()
```

### Java: SSL Debugging Flags

```bash
# Full SSL debugging — produces very verbose output
java -Djavax.net.debug=all -jar app.jar

# Recommended: handshake debugging only (most useful for troubleshooting)
java -Djavax.net.debug=ssl:handshake -jar app.jar

# Key Java SSL debug flags:
# ssl:handshake        — handshake messages, cipher negotiation, cert exchange
# ssl:record           — record-layer details (encrypted data, not usually useful)
# ssl:keymanager       — keystore/truststore interactions
# ssl:trustmanager     — trust decisions, cert path validation
# ssl:session          — session caching and resumption
# ssl:defaultctx       — default SSL context initialization
# ssl:handshake:verbose — even MORE detail on the handshake

# What to look for in the output:
# "trustStore is: /usr/lib/jvm/java-17/lib/security/cacerts" — confirm trust store
# "keyStore is: /path/to/keystore.jks" — confirm key store
# "*** ClientHello, TLSv1.3" — what protocol version is being proposed
# "Cipher Suites: [TLS_AES_256_GCM_SHA384, ...]" — ciphers being offered
# "*** ServerHello, TLSv1.3" — server's chosen protocol
# "Cipher Suite: TLS_AES_256_GCM_SHA384" — negotiated cipher
# "*** Certificate chain" — the cert chain from the server
# "Found trusted certificate" or "trustStore provider is:" — trust validation
# "Fatal (HANDSHAKE_FAILURE)" — negotiation failure
# "Fatal (CERTIFICATE_UNKNOWN)" — cert not trusted
# "Fatal (HANDSHAKE_FAILURE): Couldn't kickstart handshaking" — no cipher overlap

# Java code to enable SSL debugging programmatically:
# System.setProperty("javax.net.debug", "ssl:handshake");

# For HttpClient (Java 11+):
# HttpClient client = HttpClient.newBuilder()
#     .sslContext(mySSLContext)
#     .build();
```

### JavaScript: Node.js TLS Debugging

```javascript
// Enable Node.js internal TLS debugging
// Run: NODE_DEBUG=tls node app.js
// Or programmatically turn it on for specific modules
// process.env.NODE_DEBUG = 'tls';

// Alternatively, capture TLS errors with full context:
const https = require('https');
const tls = require('tls');

const options = {
  hostname: 'api.internal.example.com',
  port: 443,
  path: '/health',
  method: 'GET',
  rejectUnauthorized: true,
  timeout: 5000,
  ca: undefined,  // use system defaults
  checkServerIdentity: (host, cert) => {
    // Custom certificate validation with detailed logging
    console.log('--- Certificate Info ---');
    console.log('Subject:', cert.subject.CN);
    console.log('Issuer:', cert.issuer.CN);
    console.log('Valid from:', cert.valid_from);
    console.log('Valid to:', cert.valid_to);
    console.log('Fingerprint:', cert.fingerprint);
    console.log('SAN:', cert.subjectaltname);

    // Perform default hostname verification
    const err = tls.checkServerIdentity(host, cert);
    if (err) {
      console.error('Hostname verification failed:', err.message);
      return err;
    }
    return undefined;
  },
};

const req = https.request(options, (res) => {
  console.log('Status:', res.statusCode);
  let data = '';
  res.on('data', (chunk) => data += chunk);
  res.on('end', () => console.log('Response:', data.substring(0, 200)));
});

req.on('error', (err) => {
  console.error('TLS Error:', err.code, err.message);
  if (err.code === 'CERT_HAS_EXPIRED') {
    console.error('→ Certificate has expired!');
  } else if (err.code === 'UNABLE_TO_VERIFY_LEAF_SIGNATURE') {
    console.error('→ Cannot verify certificate chain. Missing intermediate or untrusted CA.');
  } else if (err.code === 'ERR_TLS_CERT_ALTNAME_INVALID') {
    console.error('→ Hostname does not match certificate SAN/CN.');
  } else if (err.code === 'SELF_SIGNED_CERT_IN_CHAIN') {
    console.error('→ Self-signed certificate in the chain.');
  } else if (err.code === 'ECONNRESET') {
    console.error('→ Connection reset — possibly protocol mismatch (HTTP vs HTTPS).');
  }
});

req.setTimeout(5000, () => {
  req.destroy();
  console.error('Request timed out after 5s');
});

req.end();
```

---

## 10. Certificate Expiry Monitoring

### OpenSSL One-Liner for Cron

```bash
#!/bin/bash
# /etc/cron.daily/cert-expiry-check
# Monitors cert expiry and sends alerts

HOSTS="example.com:443 api.example.com:443"
THRESHOLD=30
ALERT_EMAIL="sre-alerts@example.com"

for entry in $HOSTS; do
  host=${entry%:*}
  port=${entry#*:}

  enddate=$(echo | timeout 5 openssl s_client -servername "$host" -connect "$host":"$port" 2>/dev/null | \
    openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)

  if [ -z "$enddate" ]; then
    echo "ERROR: Could not get cert for $host:$port" >> /tmp/cert-alerts.txt
    continue
  fi

  expiry_ts=$(date -d "$enddate" +%s)
  now_ts=$(date +%s)
  days_left=$(( (expiry_ts - now_ts) / 86400 ))

  if [ "$days_left" -le "$THRESHOLD" ]; then
    echo "ALERT: $host:$port expires in $days_left days ($enddate)" >> /tmp/cert-alerts.txt
  fi
done

if [ -f /tmp/cert-alerts.txt ]; then
  mail -s "CERT EXPIRY WARNING" "$ALERT_EMAIL" < /tmp/cert-alerts.txt
  rm /tmp/cert-alerts.txt
fi
```

### Prometheus Blackbox Exporter Config

```yaml
# prometheus-blackbox-cert.yml
modules:
  tls_connect_with_cert_check:
    prober: tcp
    timeout: 10s
    tcp:
      tls: true
      tls_config:
        insecure_skip_verify: false
    tls:
      fail_if_not_seconds_before_expiry: 2592000  # 30 days = 30 * 24 * 3600

# Prometheus scrape config
scrape_configs:
  - job_name: 'tls-cert-monitoring'
    metrics_path: /probe
    params:
      module: [tls_connect_with_cert_check]
    static_configs:
      - targets:
          - 'example.com:443'
          - 'api.example.com:443'
          - 'auth.internal:8443'
        labels:
          environment: 'production'
          team: 'platform'
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115

# Alerting rule
groups:
  - name: tls_cert_expiry
    rules:
      - alert: TLSCertExpiringSoon
        expr: probe_ssl_earliest_cert_expiry - time() < 86400 * 14
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "TLS cert for {{ $labels.instance }} expires in {{ $value | humanizeDuration }}"
          description: "The certificate will expire at {{ $labels.instance }}. Renew immediately."

      - alert: TLSCertExpiringCritical
        expr: probe_ssl_earliest_cert_expiry - time() < 86400 * 3
        for: 15m
        labels:
          severity: critical
        annotations:
          summary: "TLS cert for {{ $labels.instance }} expires in < 3 DAYS"
```

### Certbot Auto-Renewal Setup

```bash
# Install certbot
apt-get install -y certbot python3-certbot-nginx  # for nginx
# or: apt-get install -y certbot python3-certbot-apache  # for apache

# Initial setup (wildcard cert requires DNS challenge)
certbot certonly --manual --preferred-challenges dns \
  -d "*.example.com" -d "example.com"

# Automated with DNS plugin (Route53 example)
certbot certonly --dns-route53 \
  -d "*.example.com" -d "example.com" \
  --non-interactive --agree-tos -m ops@example.com

# Verify auto-renewal timer
systemctl status certbot.timer
systemctl list-timers | grep certbot

# Enable if not already
systemctl enable --now certbot.timer

# Test renewal (dry run)
certbot renew --dry-run

# Post-renewal hook in /etc/letsencrypt/renewal-hooks/deploy/
# Reload nginx after cert renewal
cat > /etc/letsencrypt/renewal-hooks/deploy/01-reload-nginx.sh <<'EOF'
#!/bin/bash
systemctl reload nginx
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/01-reload-nginx.sh

# Cron-based fallback renewal (if not using systemd timer)
# 0 3 * * * root certbot renew --quiet --deploy-hook "systemctl reload nginx"

# Manual cert renewal troubleshooting
journalctl -u certbot -n 50
certbot certificates  # list all managed certs
certbot renew --force-renewal  # force renew (don't use in cron)
```

---

## 11. Related Sections

- [Linux Network Debugging](../../01-linux-debugging/network/linux-network-debugging.md) — TCP dump, curl timing, SS cipher checks
- [RCA Template](../../02-incident-response/rca-template.md) — Write postmortems for cert outages
- [Load Balancer Deep Dive](../../04-networking/load-balancers.md) — TLS termination best practices
- [Prometheus Monitoring](../../03-observability/prometheus/prometheus-recipes.md) — Alert on cert expiry
