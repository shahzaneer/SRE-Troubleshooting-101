# WebSocket Troubleshooting

> **Category:** API | WebSocket | Real-time
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#websocket` `#realtime` `#oncall`

---

## Connection Upgrade Failure

### How the Upgrade Works

```
Client                                    Server
  │                                          │
  │── HTTP GET /ws HTTP/1.1 ──────────────▶  │
  │   Host: ws.example.com                   │
  │   Connection: Upgrade                     │
  │   Upgrade: websocket                     │
  │   Sec-WebSocket-Key: dGhlIHNhbXBsZ...   │
  │   Sec-WebSocket-Version: 13              │
  │                                          │
  │◀── HTTP/1.1 101 Switching Protocols ────  │
  │   Connection: Upgrade                     │
  │   Upgrade: websocket                     │
  │   Sec-WebSocket-Accept: s3pPLMBiTxa...  │
  │                                          │
  │◀══════ WebSocket established ═══════▶│
```

The `Sec-WebSocket-Accept` header is computed by:
1. Concatenate `Sec-WebSocket-Key` + the magic GUID `258EAFA5-E914-47DA-95CA-C5AB0DC85B11`.
2. SHA-1 hash the result.
3. Base64-encode the hash.

If the server's `Sec-WebSocket-Accept` doesn't match, the handshake fails.

### Diagnose Upgrade Failure with curl

```bash
curl -v \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  -H "Sec-WebSocket-Version: 13" \
  https://ws.example.com/ws
```

**Expected (success):**
```
< HTTP/1.1 101 Switching Protocols
< Upgrade: websocket
< Connection: Upgrade
< Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
```

**Failure modes:**

| Response | Meaning | Common Cause |
|---|---|---|
| `HTTP/1.1 200 OK` | Server didn't recognize the Upgrade request | Missing WebSocket handler on the server path |
| `HTTP/1.1 400 Bad Request` | Malformed headers | Missing or malformed `Sec-WebSocket-Key` |
| `HTTP/1.1 404 Not Found` | Wrong path | WebSocket endpoint is at `/ws` not `/` |
| `HTTP/1.1 426 Upgrade Required` | Server refuses | Server requires a specific protocol version |
| `HTTP/1.1 502 Bad Gateway` | Proxy can't reach backend | Backend service down |
| No response / timeout | Proxy/LB doesn't understand WebSocket | Check proxy config |

### Scenario: WebSocket Fails Behind Nginx

**Problem:** WebSocket connection works fine when connecting directly to the backend (e.g., `localhost:8080`), but fails with HTTP 400 or 426 when going through Nginx.

**Root Cause:** Nginx doesn't forward Websocket headers by default. The `Upgrade` and `Connection` headers are hop-by-hop by HTTP specification — proxies must be explicitly told to forward them.

**Fix — Nginx configuration:**

```nginx
location /ws {
    proxy_pass http://websocket-backend:8080;
    proxy_http_version 1.1;                         # WebSocket requires HTTP/1.1
    proxy_set_header Upgrade $http_upgrade;          # Forward the Upgrade header
    proxy_set_header Connection "upgrade";           # Forward the Connection header
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Critical: Increase timeouts for long-lived WebSocket connections
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

**Other proxy configurations:**

**Apache:**
```apache
RewriteEngine On
RewriteCond %{HTTP:Upgrade} websocket [NC]
RewriteCond %{HTTP:Connection} upgrade [NC]
RewriteRule ^/ws$ wss://websocket-backend:8080/ws [P,L]
```

**HAProxy:**
```
frontend https-in
    bind :443 ssl crt /etc/haproxy/certs/
    use_backend websocket-backend if { hdr(Upgrade) -i websocket }

backend websocket-backend
    server ws1 10.0.1.10:8080 check
    timeout tunnel 1h  # Keep WebSocket connections alive
```

---

## Proxy/LB Idle Timeout (Silent Disconnects)

### The Problem

```
ALB idle timeout: 60 seconds
Application sends pings: never

t=0s:   WebSocket established. Messages flow normally.
t=30s:  No messages. Connection is idle.
t=60s:  ALB silently drops the TCP connection.
t=61s:  Client sends a message → RST received → silent error.
t=61s:  Server still thinks the connection is open → CLOSE_WAIT state.
```

Neither the client nor the server receives a FIN or RST (because the ALB enforces idle timeout by dropping the connection without notification). This is a **half-open TCP connection**.

### CLOSE_WAIT Accumulation

```
$ netstat -an | grep :8080 | awk '{print $6}' | sort | uniq -c
  1200 CLOSE_WAIT
    50 ESTABLISHED
     3 TIME_WAIT
```

**Interpretation:** 1200 connections in CLOSE_WAIT means the server application never called `close()` on 1200 sockets. The ALB closed its side, but the server didn't get the memo. Each CLOSE_WAIT socket holds a file descriptor. If the process hits `ulimit -n`, new connections are refused.

**Fix:**
1. Implement application-level ping/pong. The server should close connections that don't respond to pings within a timeout.
2. Monitor CLOSE_WAIT count. Alert if `CLOSE_WAIT > 1000`.
3. Enable TCP keepalives at the OS level as a safety net:

```bash
# Linux — enable TCP keepalives on the server socket
sysctl -w net.ipv4.tcp_keepalive_time=60
sysctl -w net.ipv4.tcp_keepalive_intvl=10
sysctl -w net.ipv4.tcp_keepalive_probes=3
```

---

## Silent Disconnect Playbook

```
1. Confirm the disconnect:
   - Client: check ws.readyState (should be OPEN but no messages arrive)
   - Server: check CLOSE_WAIT count: netstat -an | grep -c CLOSE_WAIT

2. Identify the hop where connection is dropped:
   Client → [CDN] → [LB/ALB] → [Nginx/Apache] → [App Server]
   Check each hop's idle timeout config:
   - AWS ALB: idle_timeout (default 60s, max 4000s)
   - AWS NLB: idle_timeout (default 350s, max 4000s)
   - Nginx: proxy_read_timeout (default 60s)
   - HAProxy: timeout tunnel, timeout client, timeout server
   - Cloudflare: 100s for WebSocket connections (free plan)

3. Verify ping/pong is configured:
   - Client-side: setInterval(() => ws.send('ping'), 30000)
   - Server-side: add heartbeat handler that responds with 'pong'

4. If no pings: implement them. If pings exist: check if idle timeout > ping interval.

5. After fix: monitor CLOSE_WAIT count for 24 hours. Should decrease to near zero.
```

### Scenario: Mobile App Silent Disconnects on Subway

**Problem:** Order tracking via WebSocket. User enters subway tunnel → cellular connection drops. The TCP connection is severed with no FIN/RST (device leaves cell tower coverage). The server thinks the client is connected indefinitely. After 10 minutes in the tunnel, the user re-emerges but the app is stuck — no reconnection logic.

**Timeline:**
```
t=0s:    User enters subway. Cellular signal drops.
t=0-10m: Server sends 600 order status updates. All fail at TCP layer — no ACKs.
          TCP retries for ~15 minutes (tcp_retries2 = 15 on Linux) then kills connection.
          But the application doesn't know — the socket is still in ESTABLISHED at the app layer.
t=10m:   User exits subway. Cellular reconnects.
          App's WebSocket readyState is OPEN but server has no corresponding connection
          (or server's connection state is CLOSE_WAIT).
t=10m+:  User stares at stale UI. No automatic reconnection. Manual refresh needed.
```

**Fix:** Client-side heartbeat + reconnection:

```javascript
class ResilientWebSocket {
  constructor(url, options = {}) {
    this.url = url;
    this.pingInterval = options.pingIntervalMs || 30000;
    this.pongTimeout = options.pongTimeoutMs || 10000;
    this.maxReconnectDelay = options.maxReconnectDelayMs || 30000;
    this.baseReconnectDelay = options.baseReconnectDelayMs || 1000;
    this.maxReconnectAttempts = options.maxReconnectAttempts || Infinity;
    this.reconnectAttempt = 0;
    this.shouldReconnect = true;
    this.listeners = {};
    this.pongTimer = null;
    this.pingTimer = null;
    this.connect();
  }

  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.reconnectAttempt = 0;
      this.startHeartbeat();
      this.emit('open');
    };

    this.ws.onmessage = (event) => {
      const message = event.data;
      if (message === 'pong') {
        this.clearPongTimeout();
        return;
      }
      if (message === 'ping') {
        this.ws.send('pong');
        return;
      }
      this.emit('message', message);
    };

    this.ws.onclose = (event) => {
      console.log(`[WS] Closed: code=${event.code} reason=${event.reason}`);
      this.stopHeartbeat();
      this.clearPongTimeout();
      this.emit('close', event);
      this.tryReconnect();
    };

    this.ws.onerror = (error) => {
      console.error('[WS] Error:', error.message || error);
      this.emit('error', error);
    };
  }

  startHeartbeat() {
    this.pingTimer = setInterval(() => {
      if (this.ws.readyState === WebSocket.OPEN) {
        this.ws.send('ping');
        this.pongTimer = setTimeout(() => {
          console.warn('[WS] Pong timeout — closing connection');
          this.ws.close(4001, 'Heartbeat timeout');
        }, this.pongTimeout);
      }
    }, this.pingInterval);
  }

  stopHeartbeat() {
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.pongTimer) clearTimeout(this.pongTimer);
  }

  clearPongTimeout() {
    if (this.pongTimer) {
      clearTimeout(this.pongTimer);
      this.pongTimer = null;
    }
  }

  tryReconnect() {
    if (!this.shouldReconnect) return;
    if (this.reconnectAttempt >= this.maxReconnectAttempts) {
      console.error('[WS] Max reconnect attempts reached');
      this.emit('maxReconnectExceeded');
      return;
    }

    const delay = computeDelay(this.reconnectAttempt,
      this.baseReconnectDelay, this.maxReconnectDelay);
    this.reconnectAttempt++;

    console.log(
      `[WS] Reconnecting in ${Math.round(delay)}ms ` +
      `(attempt ${this.reconnectAttempt})`
    );

    setTimeout(() => this.connect(), delay);
  }

  on(event, callback) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(callback);
    return this;
  }

  emit(event, data) {
    (this.listeners[event] || []).forEach(cb => {
      try { cb(data); } catch (e) { console.error('[WS] Listener error:', e); }
    });
  }

  send(data) {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    } else {
      console.warn('[WS] Cannot send — not connected');
    }
  }

  close(code = 1000, reason = 'Normal closure') {
    this.shouldReconnect = false;
    this.stopHeartbeat();
    this.ws.close(code, reason);
  }
}

function computeDelay(attempt, baseDelayMs, maxDelayMs) {
  const exponential = Math.min(maxDelayMs, baseDelayMs * Math.pow(2, attempt));
  const jitter = Math.random() * exponential * 0.5;
  return exponential + jitter;
}

// --- Usage ---
const ws = new ResilientWebSocket('wss://ws.example.com/orders', {
  pingIntervalMs: 30000,
  pongTimeoutMs: 10000,
  maxReconnectDelayMs: 30000,
  maxReconnectAttempts: 100,
});

ws.on('open', () => {
  ws.send(JSON.stringify({ type: 'subscribe', channel: 'order_status', orderId: 'ord_123' }));
});

ws.on('message', (data) => {
  const event = JSON.parse(data);
  console.log('Order update:', event);
  // Update UI with new order status
});

ws.on('error', (err) => {
  console.error('WebSocket error — displaying offline indicator');
});

ws.on('maxReconnectExceeded', () => {
  console.error('WebSocket permanently disconnected — show full-page error');
});
```

---

## Message Frame Size

### WebSocket Frame Limits

WebSocket frames have three size thresholds:

| Threshold | Max Payload | Description |
|---|---|---|
| Small frame | ≤ 125 bytes | Payload length fits in 7-bit field |
| Medium frame | ≤ 65,535 bytes (64KB) | Payload length uses 16-bit (2-byte) length field |
| Large frame | ≤ 2^63 - 1 bytes | Payload length uses 64-bit (8-byte) length field (full 64-bit integer) |

**Default maximum message size varies by implementation:**

| Implementation | Default Max Message Size |
|---|---|
| Node.js `ws` library | 100 MB |
| Python `websockets` | 1 MB |
| Go `gorilla/websocket` | 32 KB read / 1 GB write |
| Java `javax.websocket` | Implementation-dependent (Tomcat: 8KB text / 8KB binary by default) |
| Nginx `proxy_buffer_size` | 4KB / 8KB (configurable) |

### Scenario: Large JSON Payload Fails Silently

**Problem:** "WebSocket sends order confirmation JSON (150KB payload) — the client never receives it. No error in browser console. The server logs show the message was sent successfully."

**Investigation:**
```javascript
// Client
ws.onmessage = (event) => {
  console.log('Received:', event.data); // Never fires for large messages
};

// Server (Node.js)
ws.send(bigJsonString, (err) => {
  if (err) console.error('Send error:', err);
  else console.log('Sent successfully'); // Logs "Sent successfully"
});
```

**Root Cause:** An Nginx proxy between the client and the server buffers messages. Default `proxy_buffer_size` is 4KB (one memory page). Messages larger than the buffer are silently dropped.

**Nginx fix:**
```nginx
location /ws {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_buffering off;                     # Disable buffering for WebSocket
    proxy_buffer_size 256k;                  # Or increase buffer size
}
```

**Application-level fix — fragment large messages:**
```javascript
// Server: send large message in chunks
const MAX_FRAME = 65536; // 64KB
function sendLargeMessage(ws, data) {
  const str = JSON.stringify(data);
  for (let i = 0; i < str.length; i += MAX_FRAME) {
    const chunk = str.substring(i, i + MAX_FRAME);
    ws.send(chunk, i + MAX_FRAME >= str.length ? undefined : { fin: false });
  }
}

// Client: reassemble chunks
let buffer = '';
ws.onmessage = (event) => {
  buffer += event.data;
  if (event.lastEventId === '') { // fin=true
    const fullMessage = JSON.parse(buffer);
    buffer = '';
    processMessage(fullMessage);
  }
};
```

---

## Reconnection Strategy

### Exponential Backoff with Jitter (WebSocket-specific)

```javascript
function computeReconnectDelay(attempt) {
  const base = 1000;        // 1 second
  const cap = 30000;        // 30 seconds max
  const exponential = Math.min(cap, base * Math.pow(2, attempt));
  const jitter = Math.random() * exponential * 0.5;
  return exponential + jitter;
}

// Attempt    Delay range
//     0      1.0s  - 1.5s
//     1      2.0s  - 3.0s
//     2      4.0s  - 6.0s
//     3      8.0s  - 12.0s
//     4     16.0s  - 24.0s
//     5+    30.0s  - 45.0s (capped)
```

### Session Resumption

After a reconnect, the client must resubscribe to topics and replay missed messages:

```javascript
class ResilientWebSocket {
  constructor(url) {
    // ... (from above)
    this.subscriptions = new Set();
    this.lastSequenceNumber = 0;
  }

  subscribe(channel) {
    this.subscriptions.add(channel);
    if (this.ws.readyState === WebSocket.OPEN) {
      this._sendSubscription(channel);
    }
  }

  _sendSubscription(channel) {
    this.ws.send(JSON.stringify({
      type: 'subscribe',
      channel,
      since: this.lastSequenceNumber, // Replay missed messages
    }));
  }

  // Called after reconnect
  _resubscribeAll() {
    for (const channel of this.subscriptions) {
      this._sendSubscription(channel);
    }
  }

  onmessage(event) {
    const msg = JSON.parse(event.data);
    if (msg.sequence) {
      this.lastSequenceNumber = Math.max(this.lastSequenceNumber, msg.sequence);
    }
    // Process message...
  }
}
```

---

## Monitoring WebSocket Health

### Key Metrics

| Metric | How to Collect | Alert Threshold |
|---|---|---|
| Active connections | Server-side counter (increment on open, decrement on close) | ±20% change in 5 min |
| Messages/sec | Server-side counter per connection | >2x normal rate (potential attack) |
| Connection error rate | Client-side `onerror` events reported to analytics | >5% of connection attempts fail |
| CLOSE_WAIT count | `netstat -an \| grep CLOSE_WAIT \| wc -l` | >500 |
| Reconnect rate | Client-side counter incremented on close | >10% of connections reconnect in 5 min |
| Round-trip latency | Ping/pong timing: `Date.now() - sentAt` | >5s (indicates backpressure or network issue) |

### Server-Side Heartbeat Implementation (Node.js)

```javascript
const WebSocket = require('ws');

const wss = new WebSocket.Server({ port: 8080 });

function heartbeat() {
  this.isAlive = true;
}

wss.on('connection', (ws) => {
  ws.isAlive = true;
  ws.on('pong', heartbeat);

  ws.on('message', (data) => {
    if (data.toString() === 'ping') {
      ws.send('pong');
      return;
    }
    // Process application message...
  });
});

const heartbeatInterval = setInterval(() => {
  wss.clients.forEach((ws) => {
    if (ws.isAlive === false) {
      console.log(`[WS] Terminating unresponsive connection`);
      return ws.terminate();
    }
    ws.isAlive = false;
    ws.ping(); // Built-in WebSocket ping frame
  });
}, 30000);

wss.on('close', () => {
  clearInterval(heartbeatInterval);
});
```
