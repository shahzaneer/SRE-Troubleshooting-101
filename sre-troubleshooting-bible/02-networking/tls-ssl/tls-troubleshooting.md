# TLS/SSL Troubleshooting

> **Category:** Networking | TLS | Security
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#tls` `#ssl` `#security` `#oncall`

---

## Table of Contents

1. [TLS Handshake Quick Reference](#tls-handshake-quick-reference)
2. [openssl s_client — The Universal TLS Debugger](#openssl-s_client--the-universal-tls-debugger)
3. [Certificate Expiry Checking](#certificate-expiry-checking)
4. [SNI Issues and Virtual Hosting](#sni-issues-and-virtual-hosting)
5. [Certificate Chain Validation](#certificate-chain-validation)
6. [TLS Version Negotiation](#tls-version-negotiation)
7. [Cipher Suite Debugging](#cipher-suite-debugging)
8. [mTLS — Mutual TLS](#mtls--mutual-tls)
9. [curl with Certificates](#curl-with-certificates)
10. [Python Certificate Verification](#python-certificate-verification)
11. [Java Keystore and Truststore Debugging](#java-keystore-and-truststore-debugging)

---

## TLS Handshake Quick Reference

```text
CLIENT                                    SERVER
  │                                          │
  │──── ClientHello ────────────────────────>│
  │     TLS version, cipher suites, SNI,     │
  │     supported groups, signature algs     │
  │                                          │
  │<─── ServerHello ─────────────────────────│
  │     Chosen TLS version & cipher suite    │
  │<─── Certificate ─────────────────────────│
  │     Server's cert chain (leaf→root)      │
  │<─── ServerKeyExchange (optional) ────────│
  │     DH/ECDH parameters                   │
  │<─── CertificateRequest (mTLS only) ──────│
  │<─── ServerHelloDone ─────────────────────│
  │                                          │
  │──── Certificate (mTLS only) ────────────>│
  │──── ClientKeyExchange ──────────────────>│
  │──── CertificateVerify (mTLS only) ──────>│
  │──── ChangeCipherSpec ───────────────────>│
  │──── Finished (encrypted) ───────────────>│
  │                                          │
  │<─── ChangeCipherSpec ────────────────────│
  │<─── Finished (encrypted) ────────────────│
  │                                          │
  │<══════ Encrypted Application Data ══════>│
```

**Where things break (with typical symptoms):**

| Phase | Failure Point | Client Error | Server Log |
|-------|---------------|-------------|------------|
| ClientHello | Server doesn't support client's TLS version | `SSL_ERROR_UNSUPPORTED_VERSION` | Connection reset before ServerHello |
| ClientHello | SNI mismatch on virtual host | Wrong certificate presented | Server sends default cert |
| Certificate | Server cert expired | `certificate has expired` | — |
| Certificate | Intermediate CA missing | `unable to verify the first certificate` | — |
| Certificate | Hostname mismatch | `hostname verification failed` | — |
| Cipher negotiation | No common cipher | `no cipher suites in common` | `no shared cipher` |
| Client cert (mTLS) | Client cert not trusted | `alert certificate unknown` | `tls: client didn't provide a certificate` |
| Client cert (mTLS) | Client cert expired | `alert certificate expired` | `tls: failed to verify client certificate` |

---

## openssl s_client — The Universal TLS Debugger

`openssl s_client` is the single most important tool for TLS debugging. It acts as a TLS client showing every detail of the handshake.

### Basic Connection

```bash
openssl s_client -connect example.com:443
```

This connects, performs the TLS handshake, and waits for input. Send `GET / HTTP/1.1` or press Ctrl+C to exit.

### Full Diagnostic Connection

```bash
echo | openssl s_client -connect example.com:443 -servername example.com -showcerts 2>&1
```

`echo |` — close stdin immediately so s_client exits after handshake  
`-servername` — send SNI (CRITICAL for virtual hosting)  
`-showcerts` — print ALL certificates in the chain

### Decoding the Output

```text
CONNECTED(00000003)                                    ← TCP connection established
depth=2 C = US, O = Internet Security Research Group,
        CN = ISRG Root X1                              ← Root CA
verify return:1                                        ← Root CA trusted
depth=1 C = US, O = Let's Encrypt, CN = R11            ← Intermediate CA
verify return:1                                        ← Intermediate trusted
depth=0 CN = example.com                               ← Leaf (server) cert
verify return:1                                        ← Server cert trusted
---
Certificate chain
 0 s:CN = example.com                                  ← Subject of leaf cert
   i:C = US, O = Let's Encrypt, CN = R11               ← Issuer of leaf cert
   -----BEGIN CERTIFICATE-----
   MIIFazCCBFOgAwIB...
   -----END CERTIFICATE-----
 1 s:C = US, O = Let's Encrypt, CN = R11               ← Subject of intermediate
   i:C = US, O = Internet Security Research Group,
       CN = ISRG Root X1                               ← Issuer of intermediate
   -----BEGIN CERTIFICATE-----
   MIIFazCCBFOgAwIB...
   -----END CERTIFICATE-----
---
Server certificate
subject=CN = example.com
issuer=C = US, O = Let's Encrypt, CN = R11
---
No client certificate CA names sent                   ← mTLS: no CA list presented
Peer signing digest: SHA256
Peer signature type: RSA-PSS
Server Temp Key: X25519, 253 bits                     ← ECDHE key exchange
---
SSL handshake has read 3677 bytes and written 1345 bytes
Verification: OK                                       ← Chain validates!
---
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384        ← Negotiated protocol & cipher
Server public key is 2048 bit
Secure Renegotiation IS NOT supported
Compression: NONE
Expansion: NONE
No ALPN negotiated                                    ← No ALPN (HTTP/2 negotiation)
Early data was not sent
Verify return code: 0 (ok)                            ← 0 = success
---
---
Post-Handshake New Session Ticket arrived:
...                                                    ← Session resumption ticket
    Verify return code: 0 (ok)
---
DONE
```

**Key fields:**

| Field | What It Tells You |
|-------|-------------------|
| `depth=2`, `depth=1`, `depth=0` | Certificate chain depth. depth=0 is the leaf, higher numbers are closer to root. |
| `verify return:1` | This specific cert in the chain is valid. `return:0` means invalid. |
| `Verification: OK` | The complete chain validates against the system trust store. |
| `New, TLSv1.3, Cipher is ...` | The TLS version and cipher suite that was negotiated. |
| `Verify return code: 0 (ok)` | Final verification result. Non-zero means failure. |
| `No ALPN negotiated` | If you expect HTTP/2 (h2 ALPN), this tells you it wasn't offered. |

### Common s_client Options

```bash
# Force TLS version
openssl s_client -tls1_2 -connect example.com:443
openssl s_client -tls1_3 -connect example.com:443
openssl s_client -tls1   -connect example.com:443   # TLS 1.0, deprecated
openssl s_client -no_tls1_2 -no_tls1_3 -connect example.com:443  # Force TLS 1.0/1.1

# Debug specific cipher suite
openssl s_client -cipher 'ECDHE-RSA-AES256-GCM-SHA384' -connect example.com:443

# Disable certificate verification (for debugging only!)
openssl s_client -connect example.com:443 -verify_return_error

# Specify client certificate and key (mTLS)
openssl s_client -connect example.com:443 \
  -cert client.crt -key client.key \
  -CAfile ca-bundle.crt

# Use specific CA bundle
openssl s_client -connect example.com:443 -CAfile /etc/ssl/certs/ca-bundle.crt

# Check certificate details without connecting
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null \
  | openssl x509 -noout -text
```

### Behind a Proxy

```bash
# Connect through an HTTP proxy (CONNECT method)
openssl s_client -proxy proxy.company.com:8080 -connect example.com:443

# For corporate environments with interception proxies (MITM)
# The proxy presents its OWN certificate for all sites.
# s_client will flag a hostname mismatch unless you trust the proxy's CA.
openssl s_client -connect example.com:443 \
  -proxy proxy.company.com:8080 \
  -CAfile /path/to/corporate-ca.crt
```

---

## Certificate Expiry Checking

### Remote Certificate Expiry

```bash
# One-liner: check expiry dates for a remote server
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null \
  | openssl x509 -noout -dates

# Output:
# notBefore=Mar 15 00:00:00 2026 GMT
# notAfter =Jun 13 23:59:59 2026 GMT

# More detailed: check issuer, subject, dates, SANs
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates -ext subjectAltName
```

### Local Certificate Files

```bash
# Check expiry of a PEM certificate file
openssl x509 -noout -dates -in cert.pem

# Check expiry of all certs in a chain bundle
cat bundle.pem | while openssl x509 -noout -dates 2>/dev/null; do :; done

# Check expiry of JKS keystore (Java)
keytool -list -v -keystore keystore.jks -storepass changeit \
  | grep -E "Alias|Valid from|until"
```

### Production-Grade Certificate Expiry Monitor (Bash)

```bash
#!/bin/bash
# cert-check.sh — monitor certificate expiry and alert if <30 days
# Usage: ./cert-check.sh example.com:443

set -euo pipefail

THRESHOLD_DAYS=30
ENDPOINT="${1:?Usage: $0 host:port}"

HOST="${ENDPOINT%:*}"
PORT="${ENDPOINT#*:}"

END_DATE=$(echo | openssl s_client -servername "$HOST" -connect "${HOST}:${PORT}" 2>/dev/null \
  | openssl x509 -noout -enddate 2>/dev/null \
  | sed 's/notAfter=//')

if [ -z "$END_DATE" ]; then
    echo "CRITICAL: Could not retrieve certificate from $ENDPOINT"
    exit 2
fi

# Convert to epoch seconds for comparison
END_EPOCH=$(date -j -f "%b %d %T %Y %Z" "$END_DATE" +%s 2>/dev/null || \
            date -d "$END_DATE" +%s 2>/dev/null)
NOW_EPOCH=$(date +%s)
DAYS_REMAINING=$(( ($END_EPOCH - $NOW_EPOCH) / 86400 ))

if [ "$DAYS_REMAINING" -lt 0 ]; then
    echo "CRITICAL: Certificate for $ENDPOINT EXPIRED $(( -DAYS_REMAINING )) days ago!"
    exit 2
elif [ "$DAYS_REMAINING" -lt "$THRESHOLD_DAYS" ]; then
    echo "WARNING: Certificate for $ENDPOINT expires in $DAYS_REMAINING days (enddate: $END_DATE)"
    exit 1
else
    echo "OK: Certificate for $ENDPOINT valid for $DAYS_REMAINING days (enddate: $END_DATE)"
    exit 0
fi
```

### Real-World Scenario: "Everything Broke at Midnight"

```text
SYMPTOM: At 00:00 UTC, all internal microservice calls start failing
         with TLS errors. No deploys happened. Everything was fine at 23:59.

INVESTIGATION:
$ echo | openssl s_client -connect internal-api.company.com:443 2>/dev/null \
  | openssl x509 -noout -dates
notBefore=Jun 11 00:00:00 2025 GMT
notAfter=Jun 10 23:59:59 2026 GMT     ← Expired at midnight!

ROOT CAUSE: The internal TLS certificate was issued for exactly one year.
Nobody set up monitoring or auto-renewal.
At midnight UTC, every service-to-service call started failing.

LESSONS:
  1. Monitor cert expiry. Alert at 30 days for manual certs, 7 days for automated.
  2. Use cert-manager (Kubernetes) or ACM (AWS) for automatic rotation.
  3. Test with `faketime` to simulate expiry before it happens:
     faketime '2026-06-11 00:00:00' curl https://api.company.com/healthz
  4. Cert renewal is a P0 task if < 72 hours remain.
```

---

## SNI Issues and Virtual Hosting

### Why SNI Matters

Without SNI, the server doesn't know which domain the client wants until AFTER the TLS handshake (in the HTTP Host header). But the TLS handshake happens first — so the server must choose a certificate before seeing the Host header.

```text
WITHOUT SNI (TLS < 1.0 era):
  Server IP: 1.2.3.4
  Client connects to 1.2.3.4:443
  Server: sends DEFAULT certificate (probably wrong domain)
  Client: "hostname verification failed"

WITH SNI (TLS 1.0+, standard since 2006):
  Client sends: SNI = "api.example.com" in ClientHello
  Server: selects the certificate matching api.example.com
  Client: receives correct certificate
```

### Debugging SNI

```bash
# WITH SNI (correct)
echo | openssl s_client -connect 1.2.3.4:443 -servername api.example.com 2>/dev/null \
  | openssl x509 -noout -subject
# subject=CN = api.example.com       ← correct cert for this domain

# WITHOUT SNI (wrong cert)
echo | openssl s_client -connect 1.2.3.4:443 2>/dev/null \
  | openssl x509 -noout -subject
# subject=CN = *.default.com         ← server's default cert, NOT api.example.com
```

### Scenario: "Different Domain, Same IP — Wrong Cert"

```text
SYMPTOM: "We added a new domain to our ALB. It resolves (dig returns the ALB IP).
         HTTP works. HTTPS returns 'SSL_ERROR_BAD_CERT_DOMAIN'."

INVESTIGATION:
$ curl https://new-api.example.com/healthz
curl: (51) SSL: no alternative certificate subject name matches target
      host name 'new-api.example.com'

$ echo | openssl s_client -connect new-api.example.com:443 \
  -servername new-api.example.com 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName
subject=CN = *.old-api.example.com
X509v3 Subject Alternative Name:
    DNS:*.old-api.example.com, DNS:old-api.example.com
          ↑↑↑ Missing new-api.example.com!

ROOT CAUSE: The new domain's certificate doesn't list new-api.example.com
in its SAN (Subject Alternative Name) list. The ALB has the right cert
for the old domain, but the cert doesn't cover the new domain.

FIX:
  1. Add new-api.example.com to the certificate's SAN list
  2. Or use a wildcard: *.example.com (covers both)
  3. Or use AWS ACM: request a new cert, add it to ALB listener
```

---

## Certificate Chain Validation

### The Chain of Trust

```text
Root CA (self-signed, in OS/browser trust store)
  │
  └─ signs ──> Intermediate CA
                   │
                   └─ signs ──> Server Certificate (leaf)
```

For validation to succeed:
1. The **server must send** the leaf cert AND all intermediate certs (not the root — that's in the trust store)
2. The **client must trust** the root CA
3. None of the certs can be expired
4. Each cert must be properly signed by the cert above it

### Checking What the Server Sends

```bash
# Count how many certs the server sends
echo | openssl s_client -connect example.com:443 -showcerts 2>/dev/null \
  | grep -c "BEGIN CERTIFICATE"

# Should be 2+ (leaf + at least one intermediate)
# If it's 1, the server isn't sending the intermediate chain
```

### Scenario: "Browser Works, Server-to-Server Fails"

```text
SYMPTOM: "Our Node.js backend can't call an external partner API.
         curl https://partner-api.com/healthz fails with TLS error.
         But the same URL works fine in Chrome!"

INVESTIGATION:
$ curl -v https://partner-api.com/healthz
* SSL certificate problem: unable to get local issuer certificate

$ echo | openssl s_client -connect partner-api.com:443 -showcerts 2>/dev/null \
  | grep -c "BEGIN CERTIFICATE"
1                                    ← Server sent only the leaf cert!

$ openssl s_client -connect partner-api.com:443 -showcerts 2>/dev/null \
  | openssl x509 -noout -issuer
issuer=C = US, O = "Sectigo Limited", CN = "Sectigo RSA Domain Validation Secure Server CA"

ROOT CAUSE: The server only sends the leaf certificate, not the intermediate.
Browsers work because they cache intermediates from previous visits
or use AIA (Authority Information Access) to download the missing cert.
Server applications (curl, Node.js, Python requests) don't do this —
they need the full chain.

Server sends:      [leaf cert]                                    ← incomplete
Server MUST send:  [leaf cert] → [intermediate]                    ← correct
Client needs:      [leaf cert] → [intermediate] → [root in store] ← full chain

FIX:
  - Server team: concatenate the full chain in the correct order
    cat server.crt intermediate.crt > fullchain.pem
  - Or on the client side: manually add the intermediate to trust store
  - NEVER use curl -k / verify=False in production
```

### The AIA Rescue (Authority Information Access)

```bash
# See if the server cert points to the issuer's cert URL
echo | openssl s_client -connect partner-api.com:443 2>/dev/null \
  | openssl x509 -noout -text \
  | grep -A2 "Authority Information Access"
# CA Issuers - URI:http://crt.sectigo.com/SectigoRSADomainValidationSecureServerCA.crt

# Download and verify the intermediate
curl -O http://crt.sectigo.com/SectigoRSADomainValidationSecureServerCA.crt
openssl x509 -noout -subject -in SectigoRSADomainValidationSecureServerCA.crt
```

---

## TLS Version Negotiation

### Checking What Versions a Server Supports

```bash
#!/bin/bash
# tls-version-check.sh — test which TLS versions a server supports
HOST="${1:?Usage: $0 host:port}"

for version in tls1 tls1_1 tls1_2 tls1_3; do
    result=$(echo | openssl s_client -${version} -connect "$HOST" 2>&1)
    if echo "$result" | grep -q "New, TLS"; then
        echo "  ✓ TLS $(echo $version | sed 's/tls//;s/_/./') — SUPPORTED"
    else
        echo "  ✗ TLS $(echo $version | sed 's/tls//;s/_/./') — NOT SUPPORTED"
    fi
done
```

```bash
# Example output:
#   ✓ TLS 1.0 — NOT SUPPORTED
#   ✓ TLS 1.1 — NOT SUPPORTED
#   ✗ TLS 1.2 — SUPPORTED
#   ✗ TLS 1.3 — SUPPORTED
```

### Scenario: "Legacy Client Can't Connect After TLS Upgrade"

```text
SYMPTOM: "We upgraded our load balancer to require TLS 1.3 only.
         Our legacy Java 8 backend (running on JDK 8u202) can no longer
         connect to the partner API."

INVESTIGATION:
$ # Check what TLS versions the partner API supports now
$ ./tls-version-check.sh partner-api.com:443
✗ TLS 1.0 — NOT SUPPORTED
✗ TLS 1.1 — NOT SUPPORTED
✓ TLS 1.2 — NOT SUPPORTED (removed!)
✓ TLS 1.3 — SUPPORTED

$ # Check what the Java 8 runtime supports
$ java -version
openjdk version "1.8.0_202"
  → TLS 1.3 support added in JDK 8u261 (July 2020)
  → JDK 8u202 only supports up to TLS 1.2

ROOT CAUSE: The partner API removed TLS 1.2 support, requiring TLS 1.3.
Our Java 8u202 only supports TLS 1.0-1.2. No common protocol.

RESOLUTION OPTIONS:
  1. Upgrade JDK to 8u261+ (adds TLS 1.3) or JDK 11/17/21
  2. Ask partner to re-enable TLS 1.2 temporarily
  3. Put a TLS-terminating proxy in front (stunnel, haproxy)
     that speaks TLS 1.3 to the partner and plain HTTP to your app
```

### Enabling TLS 1.3 on Older JDKs

```bash
# JDK 8u261+ — TLS 1.3 is available but NOT enabled by default
# Enable it explicitly:
java -Djdk.tls.client.protocols=TLSv1.3 -jar app.jar

# JDK 11+ — TLS 1.3 is enabled by default
# But you can still restrict versions:
java -Djdk.tls.client.protocols="TLSv1.2,TLSv1.3" -jar app.jar
```

---

## Cipher Suite Debugging

### Listing Available Ciphers

```bash
# List all ciphers available in your OpenSSL installation
openssl ciphers -v
openssl ciphers -v 'TLSv1.3'
openssl ciphers -v 'HIGH'
openssl ciphers -v 'ECDHE+AESGCM'

# Format: cipher_name  protocol_version  key_exchange  authentication  encryption  mac
# ECDHE-RSA-AES256-GCM-SHA384 TLSv1.2 Kx=ECDH Au=RSA Enc=AESGCM(256) Mac=AEAD
```

### Testing Specific Ciphers Against a Server

```bash
# Test if server supports a specific cipher
echo | openssl s_client -cipher 'ECDHE-RSA-AES256-GCM-SHA384' \
  -connect example.com:443 2>&1 | grep -E "Cipher is|VERIFY|error"

# Test all ECDHE ciphers
for cipher in $(openssl ciphers 'ECDHE' | tr ':' '\n'); do
    result=$(echo | openssl s_client -cipher "$cipher" -connect example.com:443 2>&1)
    if echo "$result" | grep -q "Cipher is $cipher"; then
        echo "  ✓ $cipher"
    fi
done
```

### Scenario: "PCI DSS Scan Fails Due to Weak Cipher"

```text
SYMPTOM: Compliance scan fails: "Weak cipher suites enabled:
         TLS_RSA_WITH_AES_128_CBC_SHA and TLS_RSA_WITH_3DES_EDE_CBC_SHA"

INVESTIGATION:
$ # List ALL ciphers the server actually supports using nmap
$ nmap --script ssl-enum-ciphers -p 443 api.example.com

| ssl-enum-ciphers:
|   TLSv1.2:
|     ciphers:
|       TLS_RSA_WITH_AES_128_GCM_SHA256     — OK (AEAD)
|       TLS_RSA_WITH_AES_128_CBC_SHA        — WEAK (CBC mode, no PFS)
|       TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 — OK (ECDHE + AEAD)
|       TLS_RSA_WITH_3DES_EDE_CBC_SHA       — VERY WEAK (SWEET32 attack)

$ # Check your server config
$ grep -i cipher /etc/nginx/nginx.conf
ssl_ciphers HIGH:!aNULL:!MD5;

ROOT CAUSE: The nginx ssl_ciphers directive includes CBC-mode ciphers
and doesn't explicitly disable 3DES.

FIX (nginx):
  ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:
               ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:
               ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305';
  ssl_prefer_server_ciphers on;

FIX (Apache):
  SSLCipherSuite ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...

FIX (AWS ALB — Security Policy):
  Choose ELBSecurityPolicy-FS-1-2-Res-2020-10 (or latest FS policy)
```

---

## mTLS — Mutual TLS

In mTLS, both client and server authenticate each other with certificates. Common in service meshes (Istio, Linkerd), zero-trust architectures, and API gateways.

### mTLS Handshake Differences

```text
Standard TLS:
  Server: presents cert → Client: verifies server
  Client: (no cert required)

mTLS:
  Server: presents cert → Client: verifies server
  Server: sends CertificateRequest (list of trusted CAs)
  Client: presents cert → Server: verifies client
  Client: sends CertificateVerify (signature proving possession of private key)
```

### Debugging mTLS

```bash
# Connect with client cert
openssl s_client -connect mTLS-api.company.com:443 \
  -cert client.crt -key client.key \
  -CAfile server-ca-bundle.crt

# See which CAs the server trusts (the list it sends in CertificateRequest)
echo | openssl s_client -connect mTLS-api.company.com:443 2>/dev/null \
  | grep "Acceptable client certificate CA names"

# If no output, the server is NOT requesting client certs (no mTLS configured)
```

### Scenario: "Service Mesh mTLS Fails After Intermediate CA Rotation"

```text
SYMPTOM: "After rotating our intermediate CA, service-to-service
         calls in the mesh started failing. Envoy logs show
         'TLS error: Secret is not supplied by SDS' and
         'alert certificate unknown (46)'."

INVESTIGATION:
$ openssl s_client -connect service-b:9090 \
  -cert /etc/certs/service-a.crt -key /etc/certs/service-a.key \
  -CAfile /etc/certs/ca-bundle.pem 2>&1 | head -20
...
Acceptable client certificate CA names
    CN = Old Intermediate CA  ← Server still expects old CA!
...
Verification error: certificate unknown

$ # Check the server's trust store
$ openssl crl2pkcs7 -nocrl -certfile /etc/certs/ca-bundle.pem \
  | openssl pkcs7 -print_certs -noout

ROOT CAUSE: The server's trust bundle was NOT updated with the new
intermediate CA. The client sends its cert signed by the new intermediate,
but the server only trusts certs signed by the OLD intermediate.

RESOLUTION:
  1. Add BOTH old and new intermediate CAs to the server trust store
     during the rotation period (dual trust)
  2. Rotate server trust stores FIRST
  3. Then roll out new client certs
  4. Finally, remove old CA from trust stores

The correct rotation order is:
  Trust Store First → Client Certs Second → Cleanup Old CA
  (NEVER: new certs before servers trust the new CA)
```

---

## curl with Certificates

### Essential curl TLS Options

```bash
# Basic: connect with certificate verification (DEFAULT, always use this)
curl https://api.example.com/healthz

# Verbose: see TLS handshake details
curl -v https://api.example.com/healthz

# Use custom CA bundle (for internal/self-signed CAs)
curl --cacert /etc/ssl/internal-ca-bundle.crt https://internal-api.company.com

# mTLS: provide client cert and key
curl --cert client.crt --key client.key https://mTLS-api.company.com

# mTLS with PKCS#12 bundle (contains both cert and key)
curl --cert-type P12 --cert bundle.p12:password https://mTLS-api.company.com

# mTLS with CA cert verification
curl --cert client.crt --key client.key \
  --cacert ca-bundle.crt \
  https://mTLS-api.company.com

# Force TLS version
curl --tlsv1.3 https://api.example.com
curl --tls-max 1.2 https://legacy-api.example.com

# Use specific cipher
curl --ciphers 'ECDHE-RSA-AES256-GCM-SHA384' https://api.example.com

# Disable certificate verification (DO NOT USE IN PRODUCTION)
# curl -k https://example.com     ← ONLY for debugging
```

### Extracting Certificate Info with curl

```bash
# Get server certificate details
curl -sv https://example.com 2>&1 | grep -E "subject:|issuer:|expire date:|SSL certificate verify"

# Get the full certificate chain in PEM format
curl -v --cacert /dev/null https://example.com 2>&1 \
  | sed -n '/BEGIN CERTIFICATE/,/END CERTIFICATE/p' \
  > chain.pem

# Check which TLS version was negotiated
curl -sv https://example.com 2>&1 | grep "SSL connection using"
# SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384
```

### curl with Proxy and TLS

```bash
# TLS through an HTTP CONNECT proxy
curl --proxy http://proxy.company.com:8080 https://api.example.com

# TLS through proxy with proxy authentication
curl --proxy http://user:pass@proxy.company.com:8080 https://api.example.com

# Verify the certificate through the proxy
curl --proxy http://proxy.company.com:8080 --cacert ca-bundle.crt \
  https://api.example.com
```

---

## Python Certificate Verification

```python
#!/usr/bin/env python3
"""
Production-grade TLS configuration for Python applications.
Demonstrates proper certificate verification, mTLS, and common pitfalls.
"""

import ssl
import os
import certifi
import requests
from urllib3.exceptions import SSLError


# ── DO NOT DO THIS ─────────────────────────────────────────────
# verify=False disables ALL TLS verification.
# It's the #1 cause of production TLS "mysteries" — someone turned
# it off during debugging and never turned it back on.
# requests.get("https://api.company.com", verify=False)  # BAD


# ── Standard TLS with verification (DEFAULT) ──────────────────

def verify_with_system_certs():
    """Uses certifi + system trust store."""
    try:
        resp = requests.get("https://api.company.com/healthz")
        resp.raise_for_status()
        print(f"✓ Connected: {resp.status_code}")
    except SSLError as e:
        print(f"✗ TLS ERROR: {e}")

    # To see which CA bundle Python is using:
    print(f"CA bundle: {certifi.where()}")


# ── Custom CA Bundle (Internal/Private CAs) ────────────────────

def verify_with_custom_ca():
    """Use a custom CA bundle for internal PKI."""
    custom_ca = os.environ.get("CA_BUNDLE_PATH", "/etc/ssl/internal-ca.pem")

    if not os.path.exists(custom_ca):
        raise FileNotFoundError(f"CA bundle not found: {custom_ca}")

    resp = requests.get(
        "https://internal-api.company.com/healthz",
        verify=custom_ca
    )
    print(f"✓ Connected with custom CA: {resp.status_code}")


# ── mTLS with Custom CA ────────────────────────────────────────

def mTLS_connection():
    """Mutual TLS: both client cert AND server verification."""
    resp = requests.get(
        "https://mTLS-api.company.com/v1/data",
        cert=("/etc/certs/client.crt", "/etc/certs/client.key"),
        verify="/etc/ssl/internal-ca.pem",
        timeout=10
    )
    print(f"✓ mTLS connected: {resp.status_code}")


# ── Advanced SSL Context Configuration ─────────────────────────

def create_secure_ssl_context() -> ssl.SSLContext:
    """
    Create a hardened SSL context with explicit settings.
    This is what you use with urllib3 directly, or with
    custom socket-based clients.
    """
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

    # Load custom CA bundle (extends system certs)
    ctx.load_verify_locations(cafile='/etc/ssl/internal-ca.pem')

    # Strict minimum TLS version
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # No TLS 1.0/1.1

    # Disable insecure features
    ctx.options |= ssl.OP_NO_COMPRESSION
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT

    # For mTLS — load client certificate
    ctx.load_cert_chain(
        certfile='/etc/certs/client.crt',
        keyfile='/etc/certs/client.key'
    )

    return ctx


# ── Custom Transport with Requests ─────────────────────────────

import requests.adapters

class MTLSAdapter(requests.adapters.HTTPAdapter):
    """Requests adapter with custom SSL context for mTLS."""

    def __init__(self, cert_path, key_path, ca_bundle_path, *args, **kwargs):
        self.ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        self.ssl_context.load_verify_locations(cafile=ca_bundle_path)
        self.ssl_context.load_cert_chain(cert_path, key_path)
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = self.ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs['ssl_context'] = self.ssl_context
        return super().proxy_manager_for(*args, **kwargs)


# Usage:
# session = requests.Session()
# session.mount('https://', MTLSAdapter('/etc/certs/client.crt',
#                                        '/etc/certs/client.key',
#                                        '/etc/ssl/ca-bundle.pem'))
# session.get('https://mTLS-api.company.com/v1/data')


# ── Diagnose TLS Issues in Python ──────────────────────────────

def diagnose_tls(url: str, ca_bundle: str = None):
    """
    Attempt connection with detailed error reporting.
    Identifies certificate vs. chain vs. version issues.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = parsed.hostname
    port = parsed.port or 443

    print(f"=== TLS Diagnostic for {hostname}:{port} ===")

    try:
        ctx = ssl.create_default_context()
        if ca_bundle:
            ctx.load_verify_locations(cafile=ca_bundle)

        with socket.create_connection((hostname, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                print(f"  TLS version: {ssock.version()}")
                print(f"  Cipher: {ssock.cipher()}")
                print(f"  Cert subject: {cert.get('subject')}")
                print(f"  Cert issuer: {cert.get('issuer')}")
                print(f"  SANs: {cert.get('subjectAltName')}")
                print("  ✓ Connection successful")
                return True

    except ssl.SSLCertVerificationError as e:
        print(f"  ✗ Certificate verification failed: {e.verify_message}")
        print(f"    Error code: {e.verify_code}")
        print("    Possible: expired cert, wrong hostname, untrusted CA")
        return False
    except ssl.SSLError as e:
        print(f"  ✗ SSL protocol error: {e}")
        print("    Possible: TLS version mismatch, cipher mismatch, mTLS required")
        return False
    except socket.timeout:
        print(f"  ✗ Connection timed out")
        return False
    except ConnectionRefusedError:
        print(f"  ✗ Connection refused — nothing listening on {hostname}:{port}")
        return False


if __name__ == '__main__':
    diagnose_tls("https://api.company.com:443")
```

---

## Java Keystore and Truststore Debugging

### Keystore vs Truststore

```text
Keystore    (JKS/PKCS12):  Your identity — private key + certificate
                           -Djavax.net.ssl.keyStore=/path/keystore.jks

Truststore  (JKS/PKCS12):  Who you trust — trusted CA certificates
                           -Djavax.net.ssl.trustStore=/path/truststore.jks
```

### Examining Keystores

```bash
# List all entries in a keystore
keytool -list -v -keystore keystore.jks -storepass changeit

# Check when a certificate expires
keytool -list -v -keystore keystore.jks -storepass changeit \
  | grep -A2 "Valid from"

# List trusted CAs in truststore
keytool -list -v -keystore /path/to/cacerts -storepass changeit \
  | grep "Owner\|Valid from"

# Export a certificate from keystore to PEM (for openssl analysis)
keytool -exportcert -alias mycert -keystore keystore.jks \
  -storepass changeit -rfc -file exported.pem

# Import a CA certificate into truststore
keytool -import -trustcacerts -alias internal-ca \
  -file internal-ca.pem -keystore truststore.jks -storepass changeit
```

### Java System Properties for TLS Debugging

```bash
# Basic TLS configuration
java \
  -Djavax.net.ssl.keyStore=/etc/certs/keystore.jks \
  -Djavax.net.ssl.keyStorePassword=changeit \
  -Djavax.net.ssl.trustStore=/etc/certs/truststore.jks \
  -Djavax.net.ssl.trustStorePassword=changeit \
  -jar app.jar

# Specify keyStore type (JKS is default, PKCS12 is modern)
java \
  -Djavax.net.ssl.keyStoreType=PKCS12 \
  -Djavax.net.ssl.trustStoreType=PKCS12 \
  -jar app.jar

# Force TLS version
java -Djdk.tls.client.protocols="TLSv1.2,TLSv1.3" -jar app.jar

# Enable full SSL debug logging (VERY verbose, use for handshake debugging only)
java -Djavax.net.debug=ssl:handshake:verbose -jar app.jar

# Less verbose — just show errors and key information
java -Djavax.net.debug=ssl:handshake -jar app.jar

# Enable OCSP stapling
java -Dcom.sun.net.ssl.checkRevocation=true \
     -Dcom.sun.security.enableCRLDP=true \
     -jar app.jar
```

### Common Java TLS Errors and Solutions

```text
ERROR: "sun.security.validator.ValidatorException:
        PKIX path building failed:
        sun.security.provider.certpath.SunCertPathBuilderException:
        unable to find valid certification path to requested target"

MEANS: The server's certificate chain is not trusted.
       The CA that signed the cert (or an intermediate) is not in the truststore.
       (This is the Java equivalent of curl's "unable to get local issuer certificate")

FIX:
  1. Identify the missing CA from the server's chain
  2. Import it into the truststore:
     keytool -import -trustcacerts -alias missing-ca \
       -file missing-ca.pem -keystore $JAVA_HOME/lib/security/cacerts
  3. Or use a custom truststore:
     java -Djavax.net.ssl.trustStore=/path/to/custom-cacerts -jar app.jar

───────────────────────────────────────────────────────────────

ERROR: "javax.net.ssl.SSLHandshakeException:
        Received fatal alert: certificate_unknown"

MEANS (mTLS): The server rejected the client certificate.
       Either the client cert isn't trusted by the server,
       or the client didn't send a cert when mTLS is required.

DEBUG:
  -Djavax.net.debug=ssl:handshake:verbose
  → Look for "CertificateRequest" in output (server asked for client cert)
  → Look for the client cert chain being sent
  → Look for any "alert" messages

───────────────────────────────────────────────────────────────

ERROR: "javax.net.ssl.SSLHandshakeException:
        No appropriate protocol (protocol is disabled or
        cipher suites are inappropriate)"

MEANS: No common TLS version or cipher suite between client and server.

DEBUG:
  - Check TLS version: -Djdk.tls.client.protocols=TLSv1.2
  - Check ciphers enabled:
    SSLContext context = SSLContext.getDefault();
    SSLEngine engine = context.createSSLEngine();
    System.out.println(Arrays.toString(engine.getEnabledCipherSuites()));
```

### Java Custom SSL Socket Factory with Timeout

```java
import javax.net.ssl.*;
import java.io.IOException;
import java.net.InetAddress;
import java.net.Socket;
import java.security.KeyStore;
import java.security.cert.X509Certificate;
import java.io.FileInputStream;

public class CustomSSLSocketFactory extends SSLSocketFactory {

    private final SSLSocketFactory delegate;
    private final int connectTimeout;
    private final int readTimeout;

    public CustomSSLSocketFactory(
            String keyStorePath, String keyStorePass,
            String trustStorePath, String trustStorePass,
            int connectTimeout, int readTimeout) throws Exception {

        this.connectTimeout = connectTimeout;
        this.readTimeout = readTimeout;

        // Load key store (identity cert)
        KeyStore keyStore = KeyStore.getInstance("PKCS12");
        keyStore.load(new FileInputStream(keyStorePath), keyStorePass.toCharArray());

        // Load trust store (trusted CAs)
        KeyStore trustStore = KeyStore.getInstance("PKCS12");
        trustStore.load(new FileInputStream(trustStorePath), trustStorePass.toCharArray());

        // Create SSL context
        KeyManagerFactory kmf = KeyManagerFactory.getInstance(
            KeyManagerFactory.getDefaultAlgorithm());
        kmf.init(keyStore, keyStorePass.toCharArray());

        TrustManagerFactory tmf = TrustManagerFactory.getInstance(
            TrustManagerFactory.getDefaultAlgorithm());
        tmf.init(trustStore);

        SSLContext ctx = SSLContext.getInstance("TLSv1.3");
        ctx.init(kmf.getKeyManagers(), tmf.getTrustManagers(), null);

        this.delegate = ctx.getSocketFactory();
    }

    @Override
    public Socket createSocket(String host, int port) throws IOException {
        Socket socket = delegate.createSocket();
        socket.connect(new java.net.InetSocketAddress(host, port), connectTimeout);
        socket.setSoTimeout(readTimeout);
        return socket;
    }

    @Override
    public Socket createSocket(String host, int port, InetAddress localHost,
                               int localPort) throws IOException {
        return delegate.createSocket(host, port, localHost, localPort);
    }

    @Override
    public Socket createSocket(InetAddress host, int port) throws IOException {
        return delegate.createSocket(host, port);
    }

    @Override
    public Socket createSocket(InetAddress address, int port, InetAddress localAddress,
                               int localPort) throws IOException {
        return delegate.createSocket(address, port, localAddress, localPort);
    }

    @Override
    public Socket createSocket(Socket s, String host, int port, boolean autoClose)
            throws IOException {
        return delegate.createSocket(s, host, port, autoClose);
    }

    @Override
    public String[] getDefaultCipherSuites() {
        return delegate.getDefaultCipherSuites();
    }

    @Override
    public String[] getSupportedCipherSuites() {
        return delegate.getSupportedCipherSuites();
    }
}
```

---

## References

- [OpenSSL s_client Documentation](https://www.openssl.org/docs/manmaster/man1/openssl-s_client.html)
- [OpenSSL x509 Documentation](https://www.openssl.org/docs/manmaster/man1/openssl-x509.html)
- [Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/)
- [Qualys SSL Labs — Server Test](https://www.ssllabs.com/ssltest/)
- [Java TLS/SSL Debugging Guide (Oracle)](https://docs.oracle.com/javase/8/docs/technotes/guides/security/jsse/ReadDebug.html)
- [Python SSL Module Documentation](https://docs.python.org/3/library/ssl.html)
- [RFC 8446 — TLS 1.3](https://datatracker.ietf.org/doc/html/rfc8446)
- [cert-manager (K8s automatic cert management)](https://cert-manager.io/docs/)
