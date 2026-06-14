# 4xx Client Error Codes
> **Category:** API | HTTP | Error Codes
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#http` `#4xx` `#client-errors` `#oncall`

---

## Overview

4xx status codes indicate that the **client** (browser, mobile app, upstream service) sent something the server cannot or will not process. The server is functioning correctly; the request is flawed. Distinguishing which 4xx helps identify whether the fix is in the client code, server validation, auth system, or network configuration.

---

## 400 Bad Request

### Technical Definition

> The server cannot or will not process the request due to something that is perceived to be a client error (e.g., malformed request syntax, invalid request message framing, or deceptive request routing). — RFC 9110 §15.5.1

The server received bytes it cannot parse into a valid request. This is **not** a business logic rejection (that's 422); the request is structurally broken.

### Common Causes (Ranked by Frequency)

1. **Malformed JSON** — trailing comma, unquoted keys, control characters, BOM in UTF-8, NaN/Infinity (not valid JSON), single quotes instead of double
2. **Missing required fields** — payload passes syntax check but fails schema validation (frameworks differ: some return 400, some 422)
3. **Type mismatch** — sending `"123"` when backend expects integer `123`, or `null` for required field
4. **Invalid encoding** — `Content-Type: application/json` header but body is `application/x-www-form-urlencoded`, or charset mismatch
5. **Request too large** — body exceeds `client_max_body_size` (Nginx), `max-http-form-post-size` (Tomcat), or app framework limit

### Diagnosis

```bash
# 1. Check server access logs for the raw status + request_time
grep " 400 " /var/log/nginx/access.log | tail -20

# 2. Enable request body logging in Nginx (debug only — PII risk!)
# nginx.conf:
#   log_format debug_body '$request_body';
#   access_log /var/log/nginx/debug.log debug_body;

# 3. Check API gateway logs (AWS API Gateway, Kong, Envoy)
# Look for "validation error" or "bad request body"

# 4. In your app: log the raw body when a 400 is emitted
# Add middleware that logs req.body BEFORE JSON parsing
```

### Real Scenario

> **"A mobile team deploys a new version that sends an integer for the `price` field when the backend expects a string — all users on that version get 400."**
>
> *Root cause:* The backend schema defines `price` as `string` (to preserve decimal precision like `"19.99"`). The mobile team's new version sends `price: 19.99` (a float). JSON parse succeeds, but schema validation rejects the type.
>
> *Detection:* 400 rate spikes on `/api/checkout`. Break down by `User-Agent` / `X-App-Version` header. The spike only appears on app version `3.14.0`.
>
> *Fix:* Either the mobile team rolls back and fixes the type, or the backend accepts both and coerces.

### Code Examples

#### Python — Pydantic v2 Validation with Detailed 400

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, ValidationError
from typing import Optional
import logging

logger = logging.getLogger(__name__)

app = FastAPI()

class CheckoutRequest(BaseModel):
    # Accept both string and numeric, but validate and store as Decimal string
    price: str
    quantity: int
    promo_code: Optional[str] = None

    @field_validator('price')
    @classmethod
    def price_must_parse_as_money(cls, v: str) -> str:
        try:
            from decimal import Decimal, InvalidOperation
            Decimal(v)
        except (InvalidOperation, ValueError):
            raise ValueError(f"price must be a valid decimal string, got: {v}")
        if Decimal(v) <= 0:
            raise ValueError("price must be positive")
        return v

    @field_validator('quantity')
    @classmethod
    def quantity_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("quantity must be at least 1")
        if v > 1000:
            raise ValueError("quantity cannot exceed 1000")
        return v

# Register a global exception handler that returns structured 400 errors
@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    logger.warning(
        "400 validation error",
        extra={
            "path": str(request.url.path),
            "errors": exc.errors(),
            "body": (await request.body()).decode("utf-8", errors="replace")[:500],
        },
    )
    return JSONResponse(
        status_code=400,
        content={
            "error": "bad_request",
            "message": "Request validation failed",
            "details": [
                {
                    "field": " -> ".join(str(loc) for loc in e["loc"]),
                    "msg": e["msg"],
                    "type": e["type"],
                }
                for e in exc.errors()
            ],
        },
    )

@app.post("/checkout")
async def checkout(body: CheckoutRequest):
    return {"status": "ok", "price": body.price}
```

#### Java — Jakarta Bean Validation with @ControllerAdvice

```java
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

// --- DTO ---
public class CheckoutRequest {

    @NotBlank(message = "price is required")
    @Pattern(regexp = "^\\d+\\.\\d{2}$", message = "price must be in format 19.99")
    private String price;

    @Min(value = 1, message = "quantity must be at least 1")
    @Max(value = 1000, message = "quantity cannot exceed 1000")
    private int quantity;

    // getters and setters omitted for brevity
}

// --- Controller ---
@RestController
@RequestMapping("/api")
public class CheckoutController {

    @PostMapping("/checkout")
    public ResponseEntity<Map<String, Object>> checkout(
            @Valid @RequestBody CheckoutRequest body) {
        Map<String, Object> response = new HashMap<>();
        response.put("status", "ok");
        return ResponseEntity.ok(response);
    }
}

// --- Global Exception Handler ---
@ControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> handleValidation(
            MethodArgumentNotValidException ex) {

        Map<String, Object> body = new HashMap<>();
        body.put("error", "bad_request");
        body.put("message", "Request validation failed");

        Map<String, String> details = new HashMap<>();
        for (FieldError err : ex.getBindingResult().getFieldErrors()) {
            details.put(err.getField(), err.getDefaultMessage());
        }
        body.put("details", details);

        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, Object>> handleIllegalArgument(
            IllegalArgumentException ex) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "bad_request");
        body.put("message", ex.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }
}
```

#### JavaScript — Zod Schema with Descriptive Errors

```javascript
import { z } from 'zod';

const checkoutSchema = z.object({
  price: z
    .string()
    .regex(/^\d+\.\d{2}$/, "price must be a decimal string like '19.99'"),
  quantity: z
    .number()
    .int("quantity must be an integer")
    .min(1, "quantity must be at least 1")
    .max(1000, "quantity cannot exceed 1000"),
  promo_code: z.string().optional(),
});

// Express middleware
function validateBody(schema) {
  return (req, res, next) => {
    const result = schema.safeParse(req.body);
    if (!result.success) {
      const details = result.error.issues.map(issue => ({
        field: issue.path.join('.'),
        message: issue.message,
        code: issue.code,
      }));
      console.warn('400 validation error', {
        path: req.path,
        errors: details,
        body: JSON.stringify(req.body).slice(0, 500),
      });
      return res.status(400).json({
        error: 'bad_request',
        message: 'Request validation failed',
        details,
      });
    }
    req.validatedBody = result.data;
    next();
  };
}

app.post('/checkout', validateBody(checkoutSchema), (req, res) => {
  res.json({ status: 'ok', price: req.validatedBody.price });
});
```

#### Node.js — Express Body Parser Limit Handling

```javascript
import express from 'express';

const app = express();

// Explicit body size limit — exceeding this triggers a 413 before our handler
app.use(express.json({ limit: '1mb' }));

// Handle body-parser errors (malformed JSON, too large)
app.use((err, req, res, next) => {
  if (err.type === 'entity.parse.failed') {
    console.warn('Malformed JSON received', {
      path: req.path,
      ip: req.ip,
      bodySample: err.body?.slice(0, 200),
    });
    return res.status(400).json({
      error: 'bad_request',
      message: 'Request body is not valid JSON',
      hint: 'Check for trailing commas, unquoted keys, or invalid values like NaN/Infinity',
    });
  }
  if (err.type === 'entity.too.large') {
    return res.status(413).json({
      error: 'payload_too_large',
      message: 'Request body exceeds 1MB limit',
    });
  }
  next(err);
});
```

### Related Sections
- [422 Unprocessable Entity](#422-unprocessable-entity) — When JSON is valid but business logic rejects
- [405 Method Not Allowed](#405-method-not-allowed) — Wrong HTTP verb
- [5xx/server-errors.md](../5xx/server-errors.md) — When the error is on the server side

### Monitoring Recommendations
- **Log the raw body** (sanitized) when 400 is emitted, at least for a sampling percentage
- **Break down 400 by endpoint and app version** — identifies mobile release bugs instantly
- **Alert threshold**: 400 rate > 5% on any non-health-check endpoint for 5 min → warning; > 15% → critical

---

## 401 Unauthorized

### Technical Definition

> The request has not been applied because it lacks valid authentication credentials for the target resource. — RFC 9110 §15.5.2

The server must include a `WWW-Authenticate` header containing at least one challenge applicable to the target resource. The client should retry with appropriate `Authorization` credentials.

**Key distinction:** 401 = "I don't know who you are" (authentication). 403 = "I know who you are, but you can't do that" (authorization).

### JWT Deep Dive

JSON Web Tokens fail 401 for these reasons, ranked by frequency:

#### 1. Expired Token (`exp` claim)

```
{
  "sub": "user_42",
  "iat": 1718100000,
  "exp": 1718103600,   // <-- current time is past this
  "iss": "auth-service"
}
```

The JWT library's `decode()` or `verify()` automatically rejects expired tokens. **Common pitfall**: clock skew between auth service and resource server. If the auth server's clock is 30 seconds ahead, tokens it issues are already expired from the resource server's perspective.

#### 2. Not Before (`nbf` claim) — Token Issued Too Early

The `nbf` claim says "do not accept this token before this timestamp." Rarely used, but when present, clock skew of even 1 second causes 401.

#### 3. Invalid Signature

Token was tampered with, or the public key used to verify doesn't match the private key that signed it. **The signing key rotation scenario** (see below) is the most common production cause.

#### 4. Algorithm Confusion

The `alg` header field in the JWT tells the server which algorithm to use. If the token says `"alg": "none"` and the server library doesn't reject it, an attacker can forge tokens. If the token says `"alg": "HS256"` (HMAC with secret) but the server expects `RS256` (RSA public key), verification fails.

#### 5. `aud` Claim Mismatch

The `aud` (audience) claim identifies the intended recipient. If your token's `aud` is `"payment-service"` but the checkout service validates against its own name, 401.

### OAuth 2.0 Specific Causes

| Failure Mode | Symptom |
|-------------|---------|
| Access token expired; refresh token also expired | 401 — user must re-login |
| Access token expired; refresh failure silent | 401 — check refresh endpoint logs |
| Scope mismatch — token has `read:profile` but endpoint needs `write:profile` | Many gateways return 403 instead — verify your auth service behavior |
| Client credentials grant — `client_id` or `client_secret` wrong | 401 from token endpoint |
| Token introspection endpoint unreachable | 401 timeout → check network |

### Real Scenario

> **"After rotating signing keys, 50% of users get 401 — half the fleet still has the old public key cached."**
>
> *Root cause:* The auth service rotates its RSA key pair. The new private key signs tokens going forward. Resource servers cache the public key in memory with a 5-minute TTL. During the 5-minute window, instances with stale cache reject tokens signed by the new key. In a fleet of 100 pods, roughly 50 have the old key and 50 have the new one. Users get 401 at random based on which pod handles their request.
>
> *Detection:* 401 rate spikes to exactly 50% of authenticated requests. Break down by pod → exactly half the pods show 0% 401, other half show 100% 401. Check JWKS endpoint → new key present. Check pod startup time → some pods started 2 minutes ago.
>
> *Fix:* Always use overlapping key rotation. Publish the new public key to the JWKS endpoint **before** starting to use the new private key to sign. Wait twice the cache TTL. Then start signing with the new key. Then, after another TTL wait, remove the old key. This is called "key rollover with overlap."

### Diagnosis Commands

```bash
# 1. Decode JWT without verification (just to read claims)
echo "$TOKEN" | cut -d'.' -f2 | base64 -d | jq .
# Look at: exp, iat, nbf, aud, iss

# 2. Verify JWT with public key
echo "$TOKEN" | jwt verify --public-key public.pem -

# 3. Test against JWKS endpoint directly
curl -s https://auth.example.com/.well-known/jwks.json | jq '.keys[].kid'

# 4. Check actual app's token verification by calling with verbose curl
curl -v -H "Authorization: Bearer $TOKEN" https://api.example.com/me
# Look for: WWW-Authenticate header in response for clues
# "error=\"invalid_token\", error_description=\"The token expired\""
```

### Code Examples

#### Python — JWT Decode with Full Validation

```python
import jwt
import logging
from functools import wraps
from flask import Flask, request, jsonify
import requests
from cachetools import TTLCache

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Cache JWKS for 5 minutes
jwks_cache = TTLCache(maxsize=1, ttl=300)

def get_public_key(kid):
    """Fetch JWKS from auth server, cache it, find key by kid."""
    if 'jwks' not in jwks_cache:
        resp = requests.get(
            "https://auth.example.com/.well-known/jwks.json",
            timeout=5,
        )
        resp.raise_for_status()
        jwks_cache['jwks'] = resp.json()

    for key in jwks_cache['jwks']['keys']:
        if key['kid'] == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    raise ValueError(f"No key found for kid={kid}")

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({
                "error": "unauthorized",
                "message": "Missing or malformed Authorization header",
            }), 401, {'WWW-Authenticate': 'Bearer'}

        token = auth_header[7:]

        try:
            # Read header without verifying to get kid
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get('kid')
            if not kid:
                return jsonify({
                    "error": "unauthorized",
                    "message": "Token missing 'kid' header",
                }), 401, {'WWW-Authenticate': 'Bearer'}

            public_key = get_public_key(kid)

            # Full verification
            payload = jwt.decode(
                token,
                key=public_key,
                algorithms=['RS256'],
                audience='api-service',
                issuer='https://auth.example.com',
                options={
                    'require': ['exp', 'iat', 'sub', 'aud', 'iss'],
                    'verify_exp': True,
                    'verify_iat': True,
                    'verify_aud': True,
                    'verify_iss': True,
                },
                leeway=30,  # Allow 30s clock skew
            )
            request.user = payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT expired", extra={"token_exp": "check logs"})
            return jsonify({
                "error": "unauthorized",
                "message": "Token has expired",
                "action": "refresh your token",
            }), 401, {'WWW-Authenticate': 'Bearer error="invalid_token" error_description="token expired"'}
        except jwt.InvalidAudienceError:
            return jsonify({
                "error": "unauthorized",
                "message": "Token audience mismatch",
            }), 401, {'WWW-Authenticate': 'Bearer'}
        except jwt.InvalidIssuerError:
            return jsonify({
                "error": "unauthorized",
                "message": "Token issuer not recognized",
            }), 401, {'WWW-Authenticate': 'Bearer'}
        except jwt.InvalidTokenError as e:
            logger.error("JWT validation failed", extra={"error": str(e)})
            return jsonify({
                "error": "unauthorized",
                "message": f"Invalid token: {str(e)}",
            }), 401, {'WWW-Authenticate': 'Bearer'}
        except Exception as e:
            logger.exception("Unexpected auth error")
            return jsonify({
                "error": "internal_error",
                "message": "Authentication service temporarily unavailable",
            }), 500

        return f(*args, **kwargs)
    return decorated

@app.route('/me')
@require_auth
def me():
    return jsonify({"user": request.user['sub']})
```

#### Java — Spring Security Filter Chain Debug

```java
import io.jsonwebtoken.*;
import io.jsonwebtoken.security.Keys;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.security.PublicKey;
import java.util.Collections;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final PublicKey publicKey; // injected from config

    public JwtAuthenticationFilter(PublicKey publicKey) {
        this.publicKey = publicKey;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain chain)
            throws ServletException, IOException {

        String authHeader = request.getHeader("Authorization");

        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            chain.doFilter(request, response);
            return;
        }

        String token = authHeader.substring(7);

        try {
            Jws<Claims> jws = Jwts.parserBuilder()
                .setSigningKey(publicKey)
                .requireAudience("api-service")
                .requireIssuer("https://auth.example.com")
                .setAllowedClockSkewSeconds(30)  // Allow 30s clock skew
                .build()
                .parseClaimsJws(token);

            Claims claims = jws.getBody();
            String userId = claims.getSubject();

            UsernamePasswordAuthenticationToken auth =
                new UsernamePasswordAuthenticationToken(
                    userId, null, Collections.emptyList());

            SecurityContextHolder.getContext().setAuthentication(auth);

        } catch (ExpiredJwtException e) {
            logger.warn("JWT expired: {}", e.getClaims().getSubject());
            response.setStatus(401);
            response.setHeader("WWW-Authenticate",
                "Bearer error=\"invalid_token\", error_description=\"token expired\"");
            response.getWriter().write("{\"error\":\"unauthorized\",\"message\":\"Token expired\"}");
            return;
        } catch (SignatureException e) {
            logger.error("JWT signature verification failed");
            response.setStatus(401);
            response.setHeader("WWW-Authenticate", "Bearer");
            response.getWriter().write("{\"error\":\"unauthorized\",\"message\":\"Invalid signature\"}");
            return;
        } catch (IncorrectClaimException e) {
            logger.warn("JWT claim mismatch: {}", e.getMessage());
            response.setStatus(401);
            response.setHeader("WWW-Authenticate", "Bearer");
            response.getWriter().write("{\"error\":\"unauthorized\",\"message\":\"Claim mismatch\"}");
            return;
        } catch (JwtException e) {
            logger.error("JWT validation error: {}", e.getMessage());
            response.setStatus(401);
            response.setHeader("WWW-Authenticate", "Bearer");
            response.getWriter().write("{\"error\":\"unauthorized\",\"message\":\"Invalid token\"}");
            return;
        }

        chain.doFilter(request, response);
    }
}
```

#### JavaScript — express-jwt Error Handler

```javascript
import { expressjwt } from 'express-jwt';
import jwksRsa from 'jwks-rsa';

// Configure JWT verification with JWKS
const jwtCheck = expressjwt({
  secret: jwksRsa.expressJwtSecret({
    cache: true,          // Cache the signing key
    rateLimit: true,      // Rate limit JWKS requests
    jwksRequestsPerMinute: 5,
    jwksUri: 'https://auth.example.com/.well-known/jwks.json',
  }),
  audience: 'api-service',
  issuer: 'https://auth.example.com',
  algorithms: ['RS256'],
  clockTolerance: 30,    // 30 seconds clock skew tolerance
});

// Comprehensive JWT error handler
function jwtErrorHandler(err, req, res, next) {
  if (err.name === 'UnauthorizedError') {
    const response = { error: 'unauthorized' };

    switch (err.code) {
      case 'credentials_required':
        response.message = 'No authorization token was found';
        res.set('WWW-Authenticate', 'Bearer');
        break;
      case 'credentials_bad_format':
        response.message = 'Authorization header format must be: Bearer <token>';
        res.set('WWW-Authenticate', 'Bearer');
        break;
      case 'invalid_token':
        if (err.inner?.name === 'TokenExpiredError') {
          response.message = 'Token has expired';
          response.expiredAt = err.inner.expiredAt;
          res.set(
            'WWW-Authenticate',
            'Bearer error="invalid_token" error_description="The token expired"'
          );
        } else if (err.inner?.name === 'JsonWebTokenError') {
          response.message = err.inner.message;
          res.set('WWW-Authenticate', 'Bearer error="invalid_token"');
        } else if (err.inner?.name === 'NotBeforeError') {
          response.message = 'Token not active yet';
          response.notBefore = err.inner.date;
          res.set('WWW-Authenticate', 'Bearer error="invalid_token"');
        } else {
          response.message = 'Token is invalid';
          res.set('WWW-Authenticate', 'Bearer');
        }
        break;
      default:
        response.message = 'Authentication failed';
        res.set('WWW-Authenticate', 'Bearer');
    }

    console.warn('401 JWT error', {
      code: err.code,
      path: req.path,
      ip: req.ip,
      inner_error: err.inner?.name,
    });

    return res.status(401).json(response);
  }
  next(err);
}

// Usage
app.use(jwtCheck.unless({ path: ['/health', '/public'] }));
app.use(jwtErrorHandler);
```

### Related Sections
- [403 Forbidden](#403-forbidden) — AuthZ failure (authenticated but not allowed)
- [TLS/SSL Errors](../tls-errors/tls-error-reference.md) — Certificate-level auth issues

### Monitoring Recommendations
- **Track 401 rate by endpoint** — different endpoints may have different auth providers
- **Log `kid` header from failing tokens** — identifies key rotation issues instantly
- **Alert**: 401 rate > 5% of authenticated traffic → warning; spike that correlates with key rotation event → critical
- **Correlate 401 with JWKS endpoint errors** — if your JWKS endpoint returns 5xx, 401s will follow

---

## 403 Forbidden

### Technical Definition

> The server understood the request but refuses to fulfill it. Authorization will not help and the request SHOULD NOT be repeated. — RFC 9110 §15.5.4

Unlike 401, the server knows who you are; you just don't have permission. Re-authenticating with the same credentials will produce the same 403.

### Key Distinction from 401

| Aspect | 401 Unauthorized | 403 Forbidden |
|--------|-----------------|---------------|
| Authentication state | Missing or invalid | Valid and accepted |
| Retry with new credentials | May succeed | Will still fail |
| `WWW-Authenticate` header | **Required** by RFC | Optional |
| Typical cause | Expired JWT, bad password | Missing role, IP block, policy denial |

### RBAC Explained

Role-Based Access Control rejection flows:
1. **Missing role entirely** — user has `role: []` or `role: null`
2. **Wrong role** — user has `role: viewer` but endpoint requires `role: editor`
3. **Wrong scope/permission** — fine-grained: user has `product:read` but endpoint needs `product:write`
4. **Permission boundary** — user's org has access, but user's individual policy denies it (AWS IAM)
5. **Resource-level access** — user can access `/api/orders` but not `/api/orders/999` because they don't own order 999

### CORS Preflight Causing 403

**This is the #1 confusion point in browser-based APIs.**

A CORS preflight request is an `OPTIONS` request the browser sends before the real request to check if the server allows cross-origin access. If the server doesn't handle `OPTIONS` or doesn't return the right headers, the browser reports a 403 (even though technically the CORS failure is a network error — browsers conflate it).

```bash
# The preflight that triggers the 403:
OPTIONS /api/orders HTTP/1.1
Host: api.example.com
Origin: https://app.example.com
Access-Control-Request-Method: POST
Access-Control-Request-Headers: Authorization, Content-Type
```

Required response for CORS to work:

```
HTTP/1.1 204 No Content
Access-Control-Allow-Origin: https://app.example.com
Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type
Access-Control-Max-Age: 86400
```

**Diagnosis:**
```bash
# Test CORS preflight directly
curl -v -X OPTIONS \
  -H "Origin: https://app.example.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Authorization, Content-Type" \
  https://api.example.com/api/orders

# If you get 403 or no Access-Control-* headers, your CORS config is broken
```

### AWS IAM: How 403 Deny Works

In AWS IAM, the evaluation logic is: **an explicit Deny always wins.** Even if 10 policies say Allow, a single explicit Deny in any policy attached to the principal (or resource) denies the action.

Evaluation order:
1. **Explicit Deny** → 403, stop. No further evaluation.
2. **Organizational SCP** — if SCP denies, 403, stop.
3. **Resource-based policy** — if allows, continue.
4. **IAM identity policy** — if allows, continue.
5. **Permissions boundary** — if allows, 200.
6. **Implicit Deny (default)** → if no Allow is found, 403.

### Real Scenario

> **"New service has correct IAM role but gets 403 on S3 — turns out bucket policy explicitly denies unless the request comes from a VPC endpoint."**
>
> *Root cause:* The S3 bucket has a policy:
> ```json
> {
>   "Effect": "Deny",
>   "Principal": "*",
>   "Action": "s3:*",
>   "Resource": "arn:aws:s3:::sensitive-data/*",
>   "Condition": {
>     "StringNotEquals": {
>       "aws:SourceVpce": "vpce-0abc123def456"
>     }
>   }
> }
> ```
> The service has an IAM role with `s3:GetObject` Allow, but the bucket policy's Deny on `SourceVpce` condition wins because explicit Deny always overrides Allow. The service was deployed in a VPC without the approved VPC endpoint.
>
> *Detection:* AWS CloudTrail logs show `errorCode: AccessDenied`, `errorMessage: "Access Denied"`. The IAM Policy Simulator shows Allow. The bucket policy reveals the Deny.
>
> *Fix:* Either deploy the service to a VPC with the approved VPC endpoint, or update the bucket policy to allow the service's VPC.

### Code Examples

#### Python — RBAC Decorator

```python
from functools import wraps
from flask import request, jsonify

# Simulated user context (set by auth middleware)
# request.user = {"sub": "user_42", "roles": ["viewer"], "permissions": ["product:read"]}

def require_role(*roles):
    """Decorator: require at least one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = getattr(request, 'user', None)
            if not user:
                # No user = no auth at all → this is 401 territory
                return jsonify({"error": "unauthorized"}), 401

            user_roles = set(user.get('roles', []))
            if not user_roles.intersection(roles):
                return jsonify({
                    "error": "forbidden",
                    "message": f"Requires one of roles: {roles}. You have: {user_roles}",
                }), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_permission(*perms):
    """Decorator: require all specified permissions."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = getattr(request, 'user', None)
            if not user:
                return jsonify({"error": "unauthorized"}), 401

            user_perms = set(user.get('permissions', []))
            missing = set(perms) - user_perms
            if missing:
                return jsonify({
                    "error": "forbidden",
                    "message": f"Insufficient permissions",
                    "missing": list(missing),
                }), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_owner(param_name):
    """Decorator: check resource-level ownership."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = getattr(request, 'user', None)
            if not user:
                return jsonify({"error": "unauthorized"}), 401

            resource_id = kwargs.get(param_name) or request.view_args.get(param_name)
            # Simulate DB lookup
            # owner = db.get_owner(resource_id)
            # if owner != user['sub']:
            #     return jsonify({"error": "forbidden", "message": "Not your resource"}), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

# Usage
@app.route('/api/admin/reports')
@require_role('admin', 'superadmin')
def admin_reports():
    return jsonify({"reports": [...]})

@app.route('/api/products/<product_id>/update')
@require_permission('product:write')
@require_owner('product_id')
def update_product(product_id):
    return jsonify({"status": "updated"})
```

#### Java — @PreAuthorize with Spring Security

```java
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class ResourceController {

    // Role-based
    @GetMapping("/admin/reports")
    @PreAuthorize("hasAnyRole('ADMIN', 'SUPERADMIN')")
    public ResponseEntity<?> getAdminReports() {
        return ResponseEntity.ok(Map.of("reports", List.of()));
    }

    // Permission-based
    @PutMapping("/products/{productId}")
    @PreAuthorize(
        "hasAuthority('product:write') and " +
        "@ownershipChecker.isOwner(#productId, authentication.name)"
    )
    public ResponseEntity<?> updateProduct(@PathVariable String productId) {
        return ResponseEntity.ok(Map.of("status", "updated"));
    }

    // SpEL expression
    @DeleteMapping("/users/{userId}")
    @PreAuthorize(
        "#userId == authentication.name or hasRole('ADMIN')"
    )
    public ResponseEntity<?> deleteUser(@PathVariable String userId) {
        return ResponseEntity.ok(Map.of("status", "deleted"));
    }
}

// Global 403 handler
@ControllerAdvice
public class SecurityExceptionHandler {

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<Map<String, Object>> handleAccessDenied(
            AccessDeniedException ex) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "forbidden");
        body.put("message", ex.getMessage() != null
            ? ex.getMessage()
            : "You do not have permission to access this resource");
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(body);
    }
}
```

#### JavaScript — Express RBAC Middleware

```javascript
// rbacMiddleware.js

function requireRole(...roles) {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(401).json({ error: 'unauthorized' });
    }
    const userRoles = req.user.roles || [];
    const hasRole = roles.some(r => userRoles.includes(r));
    if (!hasRole) {
      return res.status(403).json({
        error: 'forbidden',
        message: `Requires one of roles: [${roles.join(', ')}]. You have: [${userRoles.join(', ')}]`,
      });
    }
    next();
  };
}

function requirePermission(...permissions) {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(401).json({ error: 'unauthorized' });
    }
    const userPerms = new Set(req.user.permissions || []);
    const missing = permissions.filter(p => !userPerms.has(p));
    if (missing.length > 0) {
      return res.status(403).json({
        error: 'forbidden',
        message: 'Insufficient permissions',
        missing,
      });
    }
    next();
  };
}

function requireOwner(paramName) {
  return async (req, res, next) => {
    if (!req.user) {
      return res.status(401).json({ error: 'unauthorized' });
    }
    const resourceId = req.params[paramName];
    // Simulate database ownership check
    const owner = await getResourceOwner(resourceId);
    if (owner !== req.user.sub) {
      return res.status(403).json({
        error: 'forbidden',
        message: 'You do not own this resource',
      });
    }
    next();
  };
}

// CORS configuration — avoiding the most common 403 cause
import cors from 'cors';

app.use(cors({
  origin: ['https://app.example.com'],
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Authorization', 'Content-Type'],
  maxAge: 86400,
}));

// Usage
app.get('/admin/reports', requireRole('admin', 'superadmin'), (req, res) => {
  res.json({ reports: [] });
});

app.put('/products/:productId',
  requirePermission('product:write'),
  requireOwner('productId'),
  (req, res) => {
    res.json({ status: 'updated' });
  }
);
```

### Related Sections
- [401 Unauthorized](#401-unauthorized) — When auth itself is the problem
- [405 Method Not Allowed](#405-method-not-allowed) — CORS preflight without OPTIONS handler
- [5xx/502 Bad Gateway](../5xx/server-errors.md#502-bad-gateway) — If auth service returning errors causes cascading 403s

### Monitoring Recommendations
- **Tag 403s by cause**: CORS (missing headers), RBAC (role check), ownership (per-resource), IAM (AWS)
- **Log the identity and requested resource**: "User X denied access to order Y" — this is critical for audit
- **Alert**: 403 rate sudden increase on a specific endpoint → check recent ACL/role changes
- **CORS-specific**: Monitor `OPTIONS` request volume — if it drops while POST/PUT volume stays same, CORS config may have changed

---

## 404 Not Found

### Technical Definition

> The origin server did not find a current representation for the target resource or is not willing to disclose that one exists. — RFC 9110 §15.5.5

### Three Distinct Meanings of 404

| Type | Meaning | How to diagnose |
|------|---------|-----------------|
| **Route not registered** | No handler matches the URL path | Check route table / registered handlers |
| **Resource deleted** | Route exists, but the resource (ID) doesn't | Log resource ID, check DB for soft-delete |
| **Resource never existed** | Route exists, ID never created | Distinguish from deleted — this is important for idempotency |

### Trailing Slash Hell

Different frameworks handle trailing slashes differently:

```
GET /api/users    → FastAPI (strict): 404
GET /api/users/   → FastAPI (strict): 404 unless redirect_slashes=True

GET /api/users    → Express: depends on route definition
GET /api/users/   → Express: depends on route definition

GET /api/users    → Flask (default): redirects /api/users/ to /api/users
GET /api/users/   → Flask (default): 404 if route is /api/users
```

**Diagnosis command:**
```bash
# See if trailing slash changes the behavior
curl -v https://api.example.com/api/users
curl -v https://api.example.com/api/users/
# If one returns 404 and the other 200, it's a trailing slash problem
```

### Case Sensitivity — Windows vs Linux

```
GET /api/Users   → Windows server (IIS): 200 (case-insensitive by default)
GET /api/users   → Windows server (IIS): 200

GET /api/Users   → Linux server (Nginx, Apache): 404
GET /api/users   → Linux server (Nginx, Apache): 200
```

This bites teams that develop on macOS (case-insensitive by default) but deploy to Linux containers.

### 404 vs 410

| Aspect | 404 | 410 |
|--------|-----|-----|
| Meaning | Not found — may exist later | Permanently gone — will never return |
| Search engines | Will retry | Removes from index faster |
| Client behavior | May retry (polling, backoff) | Should not retry |
| Audit trail | Just missing | Implies existence was recorded |

### Real Scenario

> **"API returns 404 intermittently — new instance in ASG has an older code version without the new endpoint."**
>
> *Root cause:* A deployment rolls out a new API version with an added endpoint. An Auto Scaling Group spans instances running two different AMIs. The new AMI has the endpoint; the old AMI doesn't. When the ALB routes to an old instance, the request gets 404. When it routes to a new instance, 200. The 404 rate is proportional to the percentage of old instances.
>
> *Detection:* 404 rate on a specific endpoint is not 0% or 100%, but some fraction (e.g., 33%). Break down 404s by `upstream_addr` (Nginx) or trace ID. Instances returning 404 have a different `Server` header or build version in health response.
>
> *Fix:* Speed up deployment rollout, or add the route to the old instances as a hotfix, or configure ALB to drain old instances faster.

### Code Examples

#### Python — FastAPI Route Debugging

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging

app = FastAPI(redirect_slashes=False)  # Strict: no automatic trailing slash redirect

logger = logging.getLogger(__name__)

@app.middleware("http")
async def log_all_requests(request: Request, call_next):
    response = await call_next(request)
    method = request.method
    path = request.url.path
    status = response.status_code

    if status == 404:
        # Log which path generated the 404
        logger.warning(
            "404 returned",
            extra={
                "method": method,
                "path": path,
                "matched_route": getattr(request.scope.get('route'), 'path', 'unknown'),
            },
        )
    return response

# Catch-all for unregistered routes (returns structured 404)
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "path": str(request.url.path),
            "method": request.method,
            "hint": "Check the path spelling, trailing slash, and case sensitivity",
        },
    )

# Debug: print all registered routes at startup
@app.on_event("startup")
async def startup():
    logger.info("Registered routes:")
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            logger.info(f"  {route.methods} {route.path}")

@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    # Simulate resource-level 404
    # user = db.find(user_id)
    # if user is None:
    #     raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return {"id": user_id}

@app.get("/api/users")
async def list_users():
    return {"users": []}
```

#### Python — Flask Route Debugging

```python
from flask import Flask, jsonify, request
import logging

app = Flask(__name__)
app.url_map.strict_slashes = False  # Auto-redirect trailing slashes

logger = logging.getLogger(__name__)

@app.before_request
def log_404():
    # This runs before the route handler
    # If the URL doesn't match any route, this still runs
    pass

@app.errorhandler(404)
def not_found(e):
    logger.warning(
        "404 not found",
        extra={
            "method": request.method,
            "path": request.path,
            "url": request.url,
            "endpoint": request.endpoint,  # None if no route matched
        },
    )
    return jsonify({
        "error": "not_found",
        "path": request.path,
        "method": request.method,
    }), 404

# Print all routes on startup
with app.app_context():
    logger.info("Registered routes:")
    for rule in app.url_map.iter_rules():
        logger.info(f"  {rule.methods} {rule.rule} -> {rule.endpoint}")
```

#### JavaScript — Express Route Listing

```javascript
import express from 'express';

const app = express();

// Debug: list all routes
function listRoutes(app) {
  const routes = [];
  app._router.stack.forEach((middleware) => {
    if (middleware.route) {
      // Route directly on app
      const methods = Object.keys(middleware.route.methods).join(',').toUpperCase();
      routes.push(`${methods} ${middleware.route.path}`);
    } else if (middleware.name === 'router' && middleware.handle.stack) {
      // Router middleware
      middleware.handle.stack.forEach((handler) => {
        if (handler.route) {
          const methods = Object.keys(handler.route.methods).join(',').toUpperCase();
          const prefix = middleware.regexp.source
            .replace('\\/?(?=\\/|$)', '')
            .replace('^', '')
            .replace(/\\\//g, '/');
          routes.push(`${methods} ${prefix}${handler.route.path}`);
        }
      });
    }
  });
  return routes;
}

app.get('/api/health', (req, res) => res.json({ status: 'ok' }));

app.get('/api/users', (req, res) => res.json({ users: [] }));
app.get('/api/users/:id', (req, res) => {
  // if (!user) return res.status(404).json({ error: 'User not found' });
  res.json({ id: req.params.id });
});

// Catch all undefined routes
app.use((req, res) => {
  console.warn('404 — no route matched', {
    method: req.method,
    path: req.path,
    baseUrl: req.baseUrl,
    originalUrl: req.originalUrl,
  });
  res.status(404).json({
    error: 'not_found',
    path: req.originalUrl,
    method: req.method,
    hint: 'Check path spelling, trailing slash, and case sensitivity',
  });
});

// Print routes at startup
console.log('Registered routes:');
listRoutes(app).forEach(r => console.log(`  ${r}`));

app.listen(3000);
```

### Related Sections
- [405 Method Not Allowed](#405-method-not-allowed) — Path exists but wrong HTTP verb
- [410 Gone](#410-gone) — Resource permanently removed (vs temporarily missing)
- [5xx/502 Bad Gateway](../5xx/server-errors.md#502-bad-gateway) — Upstream returning 404 on health check

### Monitoring Recommendations
- **Track 404s by path** — identify typos in client code, stale links
- **Distinguish "route 404" from "resource 404"** — tag with `reason: route_not_found | resource_not_found`
- **Alert**: Sudden spike in 404s on a path that previously had traffic → check deployment, DNS, CDN config
- **SLI**: 404 rate on critical paths (e.g., `/api/orders/{id}`) — high rate may indicate data corruption

---

## 405 Method Not Allowed

### Technical Definition

> The method received in the request-line is known by the origin server but not supported by the target resource. — RFC 9110 §15.5.6

The server MUST generate an `Allow` header containing a list of the supported methods for the target resource.

### CORS Preflight OPTIONS Not Handled — #1 Cause

The browser sends an `OPTIONS` request before cross-origin `POST`/`PUT`/`DELETE`. If your server doesn't handle `OPTIONS` on that route, it returns 405. The browser then blocks the actual request and reports a CORS error.

```
Browser sends:  OPTIONS /api/orders
Server returns: 405 Method Not Allowed
Browser: blocks POST /api/orders, reports CORS error in console
```

**Fix:**
```python
# FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

# Express
app.options('*', cors());  // Handle OPTIONS for all routes
```

### Real Scenario

> **"Frontend team reports 405 on PUT — backend only has POST handler for that path."**
>
> *Root cause:* API contract says `POST /api/config` for both create and update. Frontend team decides to use RESTful semantics: `PUT /api/config` for idempotent update. Backend never registered a PUT handler. All PUT requests get 405.
>
> *Diagnosis:* `curl -X PUT https://api.example.com/api/config -v` returns 405. Check `Allow` header — it says `Allow: POST`. Backend code search for `@app.put("/api/config")` or equivalent — doesn't exist.
>
> *Fix:* Either the frontend switches to POST (faster), or backend adds PUT handler. The `Allow` header tells you exactly which methods exist.

### Code Examples

```python
# FastAPI — ensure all methods are handled
@app.route("/api/config", methods=["GET", "POST", "PUT"])
async def config_handler(request: Request):
    if request.method == "GET":
        return {"config": ...}
    elif request.method == "POST":
        return {"status": "created"}
    elif request.method == "PUT":
        return {"status": "updated"}
```

```javascript
// Express — proper method routing
app.route('/api/config')
  .get((req, res) => res.json({ config: {} }))
  .post((req, res) => res.status(201).json({ status: 'created' }))
  .put((req, res) => res.json({ status: 'updated' }))
  .all((req, res) => {
    // Unsupported method
    res.set('Allow', 'GET, POST, PUT');
    res.status(405).json({
      error: 'method_not_allowed',
      allowed: ['GET', 'POST', 'PUT'],
    });
  });
```

### Related Sections
- [403 Forbidden](#403-forbidden) — CORS errors misreported as 403
- [404 Not Found](#404-not-found) — Route doesn't exist at all vs method doesn't exist

### Monitoring Recommendations
- **Track 405 by method requested vs methods allowed** — informs API contract gaps
- **Monitor OPTIONS request rates** — if they drop to 0, CORS might be broken

---

## 408 Request Timeout

### Technical Definition

> The server did not receive a complete request message within the time that it was prepared to wait. — RFC 9110 §15.5.9

The server timed out waiting for the client to finish sending the HTTP request (headers or body).

### Common Causes

1. **`client_header_timeout` (Nginx)** — Client didn't send complete headers within this time (default: 60s)
2. **`client_body_timeout` (Nginx)** — Client didn't send subsequent body chunks within this time (default: 60s)
3. **LB idle timeout** — AWS ALB idle timeout is 60s (default). If the client establishes a TCP connection but takes >60s to send the full request, ALB drops it
4. **Slow POST upload from mobile** — Poor network, large payload, device backgrounded mid-upload
5. **Expect: 100-continue stalls** — Client sends `Expect: 100-continue`, server doesn't respond with 100 Continue promptly

### Timeout Hierarchy Mismatch

```
Mobile app sets socket timeout: 120s (waiting for network)
LB idle timeout:                  60s (connection without data)
├── Drops connection if client takes >60s to send full body
└── Client was prepared to wait 120s → never gets to finish sending
```

### Code Examples

#### Python — Setting Appropriate Timeouts

```python
import httpx
import asyncio

# Client-side: send a large payload with proper timeouts
async def upload_file(url: str, file_path: str):
    # timeout param in httpx is a tuple: (connect_timeout, read_timeout, write_timeout, pool_timeout)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=10.0,   # TCP handshake
            read=30.0,      # Waiting for response
            write=60.0,     # Sending request body — IMPORTANT for uploads
            pool=5.0,
        ),
    ) as client:
        with open(file_path, 'rb') as f:
            try:
                resp = await client.put(url, content=f)
                resp.raise_for_status()
                return resp.json()
            except httpx.WriteTimeout:
                logger.error("408-like: timed out while sending request body")
                raise
            except httpx.ReadTimeout:
                logger.error("504-like: timed out waiting for response")
                raise
```

#### Java — HttpClient Timeout Configuration

```java
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public class UploadClient {
    private final HttpClient client = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(10))      // TCP connection
        .build();

    public void upload(String url, byte[] body) {
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofSeconds(60))           // Total request timeout
            .header("Content-Type", "application/octet-stream")
            .PUT(HttpRequest.BodyPublishers.ofByteArray(body))
            .build();

        try {
            HttpResponse<String> response = client.send(
                request,
                HttpResponse.BodyHandlers.ofString()
            );

            if (response.statusCode() == 408) {
                logger.warn("Server timed out waiting for our request body");
                // Implement retry with smaller chunk size or faster upload
            }
        } catch (HttpTimeoutException e) {
            logger.error("Client-side timeout — possible 408 on server", e);
        }
    }
}
```

### Related Sections
- [504 Gateway Timeout](../5xx/server-errors.md#504-gateway-timeout) — Server timed out waiting for upstream
- [499 Client Closed](#499-client-closed-request) — Client disconnected before response

### Monitoring Recommendations
- **Correlate 408 with client IP geolocation** — high-latency regions may need edge acceleration
- **Track average request body size for 408 requests** — if payloads are large, suggest chunked upload
- **Alert**: 408 rate > 2% of POST/PUT requests → investigate client network quality or LB timeout config

---

## 409 Conflict

### Technical Definition

> The request could not be completed due to a conflict with the current state of the target resource. — RFC 9110 §15.5.10

The client should resolve the conflict and resubmit. 409 is used in situations where the user might be able to resolve the conflict themselves.

### Optimistic Locking

Optimistic locking assumes conflicts are rare. Instead of locking a row, it tracks a version number:

```sql
-- Read
SELECT id, quantity, version FROM inventory WHERE id = 'SKU-123';
-- Returns: SKU-123, quantity=100, version=5

-- Write (by Worker A)
UPDATE inventory
SET quantity = 99, version = 6
WHERE id = 'SKU-123' AND version = 5;
-- Updates 1 row → success

-- Write (by Worker B, who also read version=5)
UPDATE inventory
SET quantity = 98, version = 6
WHERE id = 'SKU-123' AND version = 5;
-- Updates 0 rows → version already 6 → return 409
```

### Duplicate Key on Insert

- **UUID collision**: astronomically unlikely but possible with bad PRNG
- **User retry**: network timeout, user clicks "Submit" twice, backend processes both; one creates, second gets 409
- **Idempotency key re-use**: client reuses a key that was already processed

### Real Scenario

> **"Two warehouse workers scan the same item simultaneously — 409 on inventory update because version number incremented by first scan."**
>
> *Root cause:* Warehouse workers use handheld scanners. Worker A scans box SKU-123, the app reads `version=5, quantity=100`, decrements to 99, and writes back with `WHERE version=5`. Simultaneously (within milliseconds), Worker B scans the same SKU from across the warehouse, reads `version=5, quantity=100` (hasn't seen Worker A's update yet), decrements to 99, and writes back with `WHERE version=5`. Worker B's update returns 0 rows affected → 409 Conflict.
>
> *Detection:* 409 rate spikes on `PUT /api/inventory/{sku}`. Worker B's scanner shows an error. The system log shows `version mismatch: expected 5, got 6`.
>
> *Fix:* The client gets a 409, re-reads the current state (`GET /api/inventory/SKU-123` → `version=6, quantity=99`), and retries with the updated version. The second scan correctly decrements to 98. This is "read-modify-write with retry."
>
> *Mitigation:* For warehouse scenarios where physical inventory scanning is inherently serial, consider using FIFO queues or single-writer-per-SKU patterns. But optimistic locking + retry is the general solution.

### Code Examples

#### Python — Retry-on-409 with Fresh GET

```python
import httpx
import time
from typing import Optional

class OptimisticLockClient:
    """Client that handles 409 by re-fetching and retrying."""

    def __init__(self, base_url: str, max_retries: int = 3):
        self.base_url = base_url
        self.max_retries = max_retries

    async def update_resource(
        self, resource_id: str, update_fn, max_retries: int = None
    ) -> dict:
        """
        Fetch resource, apply update_fn, save with version check.
        On 409, re-fetch and retry.
        """
        retries = max_retries or self.max_retries

        for attempt in range(retries + 1):
            # 1. GET current state (includes version)
            async with httpx.AsyncClient() as client:
                get_resp = await client.get(f"{self.base_url}/{resource_id}")
                get_resp.raise_for_status()
                resource = get_resp.json()

            # 2. Apply the transformation
            updated = update_fn(resource.copy())

            # 3. PUT with version for optimistic lock check
            async with httpx.AsyncClient() as client:
                put_resp = await client.put(
                    f"{self.base_url}/{resource_id}",
                    json=updated,
                    headers={
                        "If-Match": str(resource['version']),  # ETag-style
                    },
                )

                if put_resp.status_code == 409:
                    if attempt < retries:
                        backoff = 0.1 * (2 ** attempt)  # Exponential backoff
                        logger.warning(
                            f"409 conflict on {resource_id}, "
                            f"attempt {attempt + 1}, retrying in {backoff}s"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        raise ConflictError(
                            f"Failed to update {resource_id} after {retries + 1} attempts"
                        )

                put_resp.raise_for_status()
                return put_resp.json()

    async def create_with_idempotency(
        self, resource_id: str, data: dict
    ) -> dict:
        """Prevent duplicate creates with idempotency key."""
        # Send idempotency key so server can detect duplicate creates
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/{resource_id}",
                json=data,
                headers={
                    "Idempotency-Key": f"create-{resource_id}-{int(time.time())}",
                },
            )
            if resp.status_code == 409:
                # Resource already exists — that's OK for idempotent create
                return resp.json()  # Server returned the existing resource
            resp.raise_for_status()
            return resp.json()
```

#### Java — JPA @Version (Optimistic Locking)

```java
import jakarta.persistence.*;

@Entity
@Table(name = "inventory")
public class InventoryItem {

    @Id
    private String sku;

    private int quantity;

    @Version  // JPA automatically increments on update and checks on merge
    private Long version;

    // getters and setters
}

@Service
@Transactional
public class InventoryService {

    @PersistenceContext
    private EntityManager em;

    @Retryable(
        retryFor = OptimisticLockException.class,
        maxAttempts = 3,
        backoff = @Backoff(delay = 100, multiplier = 2)
    )
    public InventoryItem decrementQuantity(String sku, int amount) {
        InventoryItem item = em.find(InventoryItem.class, sku);

        if (item == null) {
            throw new EntityNotFoundException("SKU not found: " + sku);
        }
        if (item.getQuantity() < amount) {
            throw new InsufficientQuantityException(
                "Only " + item.getQuantity() + " available"
            );
        }

        item.setQuantity(item.getQuantity() - amount);
        // No explicit save — JPA dirty checking handles it
        // On flush, JPA checks version. If it changed, throws OptimisticLockException
        return item;
    }
}

// Global 409 handler for OptimisticLockException
@ControllerAdvice
public class ConcurrencyExceptionHandler {

    @ExceptionHandler(OptimisticLockException.class)
    @ResponseStatus(HttpStatus.CONFLICT)
    public ResponseEntity<Map<String, Object>> handleOptimisticLock(
            OptimisticLockException ex) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "conflict");
        body.put("message", "Resource was modified by another request. "
            + "Re-fetch the resource and retry.");
        body.put("retryable", true);
        return ResponseEntity.status(HttpStatus.CONFLICT).body(body);
    }
}
```

### Related Sections
- [422 Unprocessable Entity](#422-unprocessable-entity) — Semantic error vs state conflict
- [gRPC ALREADY_EXISTS / ABORTED](../grpc-status-codes/grpc-errors.md) — gRPC equivalents of 409

### Monitoring Recommendations
- **Track 409 retry success rate** — if retries succeed, the system is self-healing; if they fail, data model needs redesign
- **Alert**: 409 rate > 5% of writes → heavy contention, consider pessimistic locking or redesign
- **Log the resource ID and version numbers** to trace contention patterns

---

## 410 Gone

### Technical Definition

> The target resource is no longer available at the origin server and this condition is likely to be permanent. — RFC 9110 §15.5.11

### When to Use 410 Instead of 404

| Use 410 when... | Use 404 when... |
|-----------------|-----------------|
| Resource was intentionally deleted with an audit trail | Resource might have been moved or recreated |
| Deprecated API version (e.g., `/v1/deprecated-endpoint`) | Resource was never created |
| User account deleted (GDPR right to erasure) | Typo in URL |
| Product discontinued permanently | Temporary outage of downstream service |

### SEO Implications

Search engines treat 410 differently from 404:
- **404**: Spider will retry the URL on subsequent crawls, waiting for it to come back
- **410**: Spider removes the URL from the index on the next crawl. This is faster and cleaner for deprecated content.

### Code Examples

```python
# FastAPI — returning 410 for deprecated endpoints
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/v1/deprecated-feature")
async def deprecated_v1():
    raise HTTPException(
        status_code=410,
        detail={
            "error": "gone",
            "message": "This API version has been permanently removed.",
            "migration_link": "/docs/migration/v1-to-v2",
            "sunset_date": "2025-12-31",
        },
    )

# Express
app.get('/v1/old-endpoint', (req, res) => {
  res.status(410).json({
    error: 'gone',
    message: 'This endpoint has been permanently removed.',
    migration: '/docs/migration',
  });
});
```

### Related Sections
- [404 Not Found](#404-not-found) — Temporary absence vs permanent removal
- [5xx/500](../5xx/server-errors.md#500-internal-server-error) — Ensure 410s aren't accidental (bug causing deletions)

---

## 422 Unprocessable Entity

### Technical Definition

> The server understands the content type of the request entity, and the syntax of the request entity is correct, but was unable to process the contained instructions. — RFC 9110 §15.5.21 (WebDAV; widely adopted for REST APIs)

**Key distinction from 400**: The request is syntactically valid (valid JSON, correct types), but semantically wrong (business rule violation).

### Common Causes

1. **Business rule violations** — password too short, email invalid format, order quantity=0, negative withdrawal
2. **Field interdependencies** — `start_date` is after `end_date`, `shipping_address` required when `is_physical_good=true`
3. **Data integrity rules** — referenced entity doesn't exist (foreign key), circular dependency in tree structure
4. **Custom validation beyond schema** — coupon code expired, product out of stock for requested quantity

### Real Scenario

> **"User submits an order with quantity=0 — JSON is valid but business logic rejects."**
>
> A frontend developer accidentally sets `quantity: 0` as a default when the cart is empty. The JSON is perfectly valid: `{"items": [{"sku": "ABC", "quantity": 0}], "payment": {...}}`. The Pydantic/Zod schema says `quantity: int` — it passes type checking. But the business logic says "you can't order zero items." This is a 422, not a 400, because the syntax is fine but the semantics are wrong.
>
> *Detection:* 422 rate spikes on `/api/orders`. Log the request body — every failing request has `"quantity": 0`.
>
> *Fix:* Either the frontend fixes the default, or the schema adds `min(1)` validation (which would make it a 400 in some frameworks that validate at the schema layer).

### Code Examples

#### Python — Pydantic Custom Validators

```python
from pydantic import BaseModel, field_validator, model_validator
from datetime import date, datetime
from typing import Optional, List

class OrderItem(BaseModel):
    sku: str
    quantity: int

    @field_validator('quantity')
    @classmethod
    def quantity_must_be_valid(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("quantity must be at least 1 — you can't order zero items")
        if v > 10000:
            raise ValueError("quantity exceeds maximum order limit of 10000")
        return v

class CreateOrderRequest(BaseModel):
    items: List[OrderItem]
    delivery_date: Optional[date] = None
    coupon_code: Optional[str] = None
    shipping_address: Optional[str] = None

    @model_validator(mode='after')
    def validate_business_rules(self):
        # Items must not be empty
        if len(self.items) == 0:
            raise ValueError("order must contain at least one item")

        # delivery_date must be in the future
        if self.delivery_date and self.delivery_date < date.today():
            raise ValueError("delivery_date must be today or in the future")

        # Coupon must be valid
        if self.coupon_code and not self._validate_coupon(self.coupon_code):
            raise ValueError(f"coupon_code '{self.coupon_code}' is expired or invalid")

        return self

    def _validate_coupon(self, code: str) -> bool:
        # Simulate coupon validation
        valid_codes = {"SAVE10", "FREESHIP", "WELCOME2025"}
        return code.upper() in valid_codes

# FastAPI endpoint
@app.post("/orders")
async def create_order(body: CreateOrderRequest):
    # By the time we get here, all Pydantic validators passed
    # Do further business logic checks that need DB access
    for item in body.items:
        in_stock = await inventory_service.check_stock(item.sku)
        if in_stock < item.quantity:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unprocessable_entity",
                    "field": f"items.{item.sku}",
                    "message": f"Insufficient stock: requested {item.quantity}, available {in_stock}",
                },
            )
    return {"order_id": "ord_12345"}
```

#### Java — Custom ConstraintValidator

```java
import jakarta.validation.Constraint;
import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;

// --- Custom annotation ---
@Constraint(validatedBy = ValidCouponCodeValidator.class)
@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
public @interface ValidCouponCode {
    String message() default "Invalid or expired coupon code";
    Class<?>[] groups() default {};
    Class<?>[] payload() default {};
}

// --- Validator ---
public class ValidCouponCodeValidator
        implements ConstraintValidator<ValidCouponCode, String> {

    private static final Set<String> VALID_CODES =
        Set.of("SAVE10", "FREESHIP", "WELCOME2025");

    @Override
    public boolean isValid(String code, ConstraintValidatorContext context) {
        if (code == null) return true;  // Optional field
        return VALID_CODES.contains(code.toUpperCase());
    }
}

// --- DTO ---
public class CreateOrderRequest {

    @NotEmpty(message = "order must contain at least one item")
    private List<OrderItem> items;

    @Future(message = "delivery_date must be in the future")
    private LocalDate deliveryDate;

    @ValidCouponCode
    private String couponCode;

    // getters, setters, etc.
}

// --- Handling 422 in @ControllerAdvice ---
@ControllerAdvice
public class BusinessRuleExceptionHandler {

    @ExceptionHandler(InsufficientStockException.class)
    public ResponseEntity<Map<String, Object>> handleStock(
            InsufficientStockException ex) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "unprocessable_entity");
        body.put("message", ex.getMessage());
        body.put("sku", ex.getSku());
        body.put("requested", ex.getRequested());
        body.put("available", ex.getAvailable());
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(body);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> handleValidation(
            MethodArgumentNotValidException ex) {
        // Some frameworks return 400 here; 422 is more semantically correct
        Map<String, Object> body = new HashMap<>();
        body.put("error", "unprocessable_entity");
        body.put("message", "Validation failed");
        Map<String, String> fields = new HashMap<>();
        ex.getBindingResult().getFieldErrors().forEach(
            e -> fields.put(e.getField(), e.getDefaultMessage())
        );
        body.put("fields", fields);
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(body);
    }
}
```

### Related Sections
- [400 Bad Request](#400-bad-request) — Syntactic errors vs semantic errors
- [409 Conflict](#409-conflict) — State conflict vs business rule conflict

### Monitoring Recommendations
- **Log the specific validation error path** — identifies which business rules are most frequently hit
- **Track 422 by field** — if one field generates 80% of 422s, it's a UX problem
- **Alert**: 422 rate > 10% of writes → either bad client code deploy or business rule too aggressive

---

## 429 Too Many Requests

### Technical Definition

> The user has sent too many requests in a given amount of time. — RFC 6585 §4

The server MUST include a `Retry-After` header indicating how long the client should wait.

### Rate Limiting Algorithms

| Algorithm | Behavior | Burst Handling | Memory | Best For |
|-----------|----------|----------------|--------|----------|
| **Fixed Window** | Counts requests in discrete time buckets (e.g., 100 req/min from 12:00:00 to 12:00:59) | Allows 2x burst at window boundary (99 at 12:00:59 + 100 at 12:01:00) | Low — one counter per key | Simple API limits |
| **Sliding Window Log** | Timestamped log of each request; count in window [now - interval, now] | Perfectly smooth, no boundary burst | High — stores timestamps | Precision-needed APIs |
| **Sliding Window (counter)** | Approximate: weighted sum of current window + previous window | Smooth, near-accurate | Medium — two counters per key | Balanced approach |
| **Token Bucket** | Bucket has max capacity; tokens added at steady rate; each request costs 1 token | Allows bursts up to bucket capacity, then smooth throttling | Low — one counter + timestamp | Burst-tolerant APIs |
| **Leaky Bucket** | Queue; requests processed at fixed rate; overflow = 429 | Smooth, constant output rate | Medium — queue | Traffic shaping |

### The `Retry-After` Header

Two formats, both valid:

```
Retry-After: 120                    ← seconds from now (delta-seconds)
Retry-After: Fri, 31 Dec 2026 23:59:59 GMT  ← absolute HTTP-date
```

### Distinguishing Source of 429

| Source | How to identify |
|--------|----------------|
| **Your API Gateway** (Kong, Apigee) | `X-RateLimit-*` headers, specific response body format |
| **Your Load Balancer** (AWS ALB) | ALB access logs, no app-level rate limit headers |
| **Your Application** | Custom `X-RateLimit-Reset`, `X-RateLimit-Remaining` headers |
| **Upstream dependency** | 429 in your logs from outbound calls, not inbound |

### Real Scenario

> **"Black Friday — Redis rate limiter key expires at exactly T=0 for all users simultaneously, creating a thundering herd of allowed requests."**
>
> *Root cause:* The rate limiter uses Redis `SET key 0 EX 60 NX` for a fixed-window counter. All keys are set at server startup (T=0). After 60 seconds, every key expires simultaneously. In the next millisecond, 10,000 users make requests. Since all counters reset to 0 simultaneously, every request is allowed until the counters fill again. This creates a thundering herd: a massive burst every 60 seconds followed by 429s for the rest of the window.
>
> *Detection:* 429 rate oscillates: near 0% at the start of each window, then spikes to 90% at the end. Redis keys expire in lockstep.
>
> *Fix 1:* Use sliding window instead of fixed window — no boundary burst.
> *Fix 2 (fixed window):* Add random jitter to key expiration: `EX randint(55, 65)` instead of `EX 60`. This staggers the resets.
> *Fix 3:* Token bucket — allows bursts but smooths them.

### Code Examples

#### Python — Retry with Retry-After Using Tenacity

```python
import httpx
import asyncio
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_result,
    before_sleep_log,
)
import logging

logger = logging.getLogger(__name__)

def is_429(response):
    return response.status_code == 429

def parse_retry_after(response):
    """Extract Retry-After seconds from response headers."""
    retry_after = response.headers.get('Retry-After')
    if retry_after is None:
        return 5  # Default guess
    try:
        # It might be a delta-seconds integer
        return int(retry_after)
    except ValueError:
        # It might be an HTTP-date
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        retry_time = parsedate_to_datetime(retry_after)
        now = datetime.now(timezone.utc)
        return max(0, (retry_time - now).total_seconds())

@retry(
    retry=retry_if_result(is_429),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def api_call_with_retry(url: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        if resp.status_code == 429:
            wait_seconds = parse_retry_after(resp)
            logger.warning(
                f"Rate limited on {url}, Retry-After={wait_seconds}s, "
                f"Limit={resp.headers.get('X-RateLimit-Limit')}, "
                f"Remaining={resp.headers.get('X-RateLimit-Remaining')}"
            )
            # Tenacity will back off, but we can also respect Retry-After
            await asyncio.sleep(wait_seconds)
        return resp
```

#### Java — Resilience4j RateLimiter

```java
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RateLimiterConfig;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.github.resilience4j.ratelimiter.annotation.RateLimiter;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;

import java.time.Duration;

@Configuration
public class ResilienceConfig {

    @Bean
    public RateLimiterRegistry rateLimiterRegistry() {
        RateLimiterConfig config = RateLimiterConfig.custom()
            .limitRefreshPeriod(Duration.ofSeconds(60))
            .limitForPeriod(100)  // 100 requests per 60 seconds
            .timeoutDuration(Duration.ofSeconds(5))  // Max wait for permission
            .build();
        return RateLimiterRegistry.of(config);
    }

    @Bean
    public RateLimiter apiRateLimiter(RateLimiterRegistry registry) {
        return registry.rateLimiter("api-limiter");
    }

    // Retry config for 429 responses from upstream
    @Bean
    public Retry upstreamRetry() {
        RetryConfig config = RetryConfig.custom()
            .maxAttempts(3)
            .waitDuration(Duration.ofSeconds(1))
            .retryOnResult(response -> {
                if (response instanceof ResponseEntity) {
                    return ((ResponseEntity<?>) response).getStatusCode()
                        == HttpStatus.TOO_MANY_REQUESTS;
                }
                return false;
            })
            .build();
        return Retry.of("upstreamRetry", config);
    }
}

// Usage in service
@Service
public class ApiService {

    @RateLimiter(name = "api-limiter")
    public ResponseEntity<String> callUpstream(String url) {
        ResponseEntity<String> response = restTemplate.getForEntity(
            url, String.class
        );

        if (response.getStatusCode() == HttpStatus.TOO_MANY_REQUESTS) {
            String retryAfter = response.getHeaders()
                .getFirst("Retry-After");
            logger.warn("Rate limited by upstream. Retry-After: {}s", retryAfter);
        }
        return response;
    }
}
```

#### JavaScript — axios-retry with 429 Handler

```javascript
import axios from 'axios';
import axiosRetry from 'axios-retry';

const client = axios.create({
  baseURL: 'https://api.example.com',
  timeout: 10000,
});

// Configure retry behavior
axiosRetry(client, {
  retries: 3,
  retryDelay: (retryCount, error) => {
    // If server gave us a Retry-After header, use it
    if (error.response?.headers?.['retry-after']) {
      const retryAfter = parseInt(error.response.headers['retry-after'], 10);
      if (!isNaN(retryAfter)) {
        return retryAfter * 1000; // Convert seconds to ms
      }
    }
    // Otherwise exponential backoff
    return axiosRetry.exponentialDelay(retryCount);
  },
  retryCondition: (error) => {
    // Retry on 429 and network errors (not 4xx/5xx generally)
    return (
      axiosRetry.isNetworkOrIdempotentRequestError(error) ||
      error.response?.status === 429
    );
  },
  onRetry: (retryCount, error, requestConfig) => {
    console.warn(`Retry attempt ${retryCount} for ${requestConfig.url}`, {
      status: error.response?.status,
      retryAfter: error.response?.headers?.['retry-after'],
      limit: error.response?.headers?.['x-ratelimit-limit'],
      remaining: error.response?.headers?.['x-ratelimit-remaining'],
      reset: error.response?.headers?.['x-ratelimit-reset'],
    });
  },
});

// Request interceptor to add rate limit awareness
let rateLimitReset = 0;
let requestsQueued = 0;

client.interceptors.request.use(async (config) => {
  if (Date.now() < rateLimitReset) {
    const waitMs = rateLimitReset - Date.now();
    console.warn(`Rate limit active, delaying request by ${waitMs}ms`);
    await new Promise(resolve => setTimeout(resolve, waitMs));
  }
  return config;
});

// Response interceptor to track rate limit state
client.interceptors.response.use(
  (response) => {
    const remaining = response.headers['x-ratelimit-remaining'];
    const reset = response.headers['x-ratelimit-reset'];
    if (remaining !== undefined && parseInt(remaining) < 5) {
      console.warn('Rate limit nearly exhausted', { remaining, reset });
    }
    return response;
  },
  (error) => {
    if (error.response?.status === 429) {
      const retryAfter = error.response.headers['retry-after'];
      if (retryAfter) {
        rateLimitReset = Date.now() + parseInt(retryAfter) * 1000;
      }
    }
    return Promise.reject(error);
  }
);

export default client;
```

### Related Sections
- [503 Service Unavailable](../5xx/server-errors.md#503-service-unavailable) — Server-side throttling vs client-side rate limiting
- [gRPC RESOURCE_EXHAUSTED](../grpc-status-codes/grpc-errors.md) — gRPC equivalent of 429

### Monitoring Recommendations
- **Track 429 by client (IP/API key/User-Agent)** — identifies abusive or misconfigured clients
- **Visualize rate limit consumption** — time-series of `X-RateLimit-Remaining` per client tier
- **Alert**: 429 rate > 10% for a paid-tier client → their integration is broken or they're being DDoSed
- **Alert**: 429 rate > 5% across all traffic → limit thresholds may be too low for legitimate traffic

---

## 499 Client Closed Request (Nginx Specific)

### Technical Definition

> A client closed the connection before the server returned a response. This is **not** a standard HTTP status code; it's Nginx's custom log code.

The HTTP protocol doesn't define 499. Nginx invented it to log when `ngx_http_request_finalize()` is called with `NGX_HTTP_CLIENT_CLOSED_REQUEST`.

### Common Causes

1. **Mobile app backgrounded** — user switches apps while API call is in flight; OS terminates the TCP connection
2. **User navigated away** — clicked a link or closed the tab before the page fully loaded
3. **Load test tool timeout** — JMeter/K6 client timeout is shorter than server response time
4. **Browser retry logic** — Chrome retries requests after a timeout, closing the first connection
5. **Reverse proxy in front** — CDN or LB timed out waiting for origin, then closed the connection to the client

### When to Worry

**A spike in 499 correlates with p99 latency increasing.** If your API normally responds in 200ms and p99 suddenly goes to 3s, clients that were coded for 500ms timeouts will start disconnecting. The 499 rate is a **leading indicator** of a latency problem — you see 499s before you see timeout alerts.

### Real Scenario

> **"Mobile team reports 'random failures' — 499s spike because API p99 went from 200ms to 3s after a DB index was dropped."**
>
> *Root cause:* A DBA drops an unused-looking index during a cleanup window. The index was actually critical for a query that runs on the `/api/feed` endpoint. Query time goes from 50ms to 2500ms. The API p99 goes from 200ms to 3000ms. The mobile app has a 1-second socket timeout. Every request that hits the slow query path gets a 499 because the app times out and closes the connection before the server responds.
>
> *Detection:* 499 rate spikes on `/api/feed`. DB slow query log shows the query now takes 2500ms (was 50ms). Missing index alert triggers. Mobile crash rate (client-side timeouts) spikes simultaneously.
>
> *Fix:* Recreate the index. The mobile app should also implement graceful timeout handling with user feedback, but the root cause is the missing index.

### Code — Nginx Log Analysis for 499

```bash
# Find percentage of requests that are 499
awk '{print $9}' /var/log/nginx/access.log \
  | sort | uniq -c | sort -rn

# Find which endpoints get the most 499s
awk '$9 == 499 {print $7, $NF}' /var/log/nginx/access.log \
  | sort | uniq -c | sort -rn | head -20
# $7 = request path, $NF = request_time

# Correlate 499s with request duration
awk '$9 == 499 {
  bucket = int($NF / 0.5) * 0.5;  # Group in 500ms buckets
  count[bucket]++
}
END {
  for (b in count) print b, count[b]
}' /var/log/nginx/access.log | sort -n
```

### Related Sections
- [408 Request Timeout](#408-request-timeout) — Server timed out waiting for client (opposite direction)
- [504 Gateway Timeout](../5xx/server-errors.md#504-gateway-timeout) — Server timed out waiting for upstream

### Monitoring Recommendations
- **Track 499 as a percentage of all requests** — >1% is concerning, >5% is critical
- **Correlate 499 with p99 latency** — they should track together; if they diverge, something else is causing disconnects
- **Break down 499 by User-Agent** — are mobile clients disconnecting more than desktop?
- **Alert**: 499 rate > 2% for 5 min → warning; > 5% for 5 min → critical (latency degradation in progress)

---

*Return to [07 Error Codes Home](../README.md)*
