# GraphQL Troubleshooting

> **Category:** API | GraphQL
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#graphql` `#api` `#oncall`

---

## The N+1 Problem

### What It Is

Given this schema and query:

```graphql
type User {
  id: ID!
  name: String!
  posts: [Post!]!
}

type Post {
  id: ID!
  title: String!
}

query {
  users(first: 20) {
    name
    posts {
      title
    }
  }
}
```

**Without DataLoader (N+1):**

```
1. SELECT * FROM users LIMIT 20;                     -- 1 query (returns 20 users)
2. SELECT * FROM posts WHERE author_id = 1;           -- query 2
3. SELECT * FROM posts WHERE author_id = 2;           -- query 3
4. SELECT * FROM posts WHERE author_id = 3;           -- query 4
...
21. SELECT * FROM posts WHERE author_id = 20;         -- query 21
```

**Total: 21 queries.** The resolver for `User.posts` is called once per user. If each nested level is also N+1 (e.g., `posts { comments { author }}`), queries explode combinatorially.

**With DataLoader:**

```
1. SELECT * FROM users LIMIT 20;                      -- 1 query
2. SELECT * FROM posts WHERE author_id IN (1,2,3,...,20);  -- 1 query (batched!)
```

**Total: 2 queries.**

### Detecting N+1 in Production

**Database Logs Pattern:**

```
2026-06-11T14:30:01.001Z SELECT * FROM posts WHERE author_id = 1
2026-06-11T14:30:01.003Z SELECT * FROM posts WHERE author_id = 2
2026-06-11T14:30:01.004Z SELECT * FROM posts WHERE author_id = 3
2026-06-11T14:30:01.005Z SELECT * FROM posts WHERE author_id = 4
```

A tight cluster of identical queries differing only by a parameter value within a single span is the N+1 signature. Look for sequences of identical query patterns within <50ms of each other.

**APM/Tracing:** A GraphQL trace shows many DB spans stacked inside a single GraphQL root span. Example in DataDog/Jaeger:

```
graphql.execute
  ├── db.query "SELECT * FROM users"          [5ms]
  ├── db.query "SELECT * FROM posts WHERE ..." [3ms]
  ├── db.query "SELECT * FROM posts WHERE ..." [3ms]
  ├── db.query "SELECT * FROM posts WHERE ..." [3ms]
  ├── db.query "SELECT * FROM posts WHERE ..." [3ms]
  ... (18 more identical queries) ...
  └── Total: 95ms (80ms from N queries, could be 5ms from 1 query)
```

### N+1 Detection Playbook

```
1. Check APM traces for repeated identical DB spans within a single graphql.execute span.
2. Query: db.operation="SELECT" AND resource="graphql.execute"
3. Count span repetition for patterns like:
   db.query:"SELECT * FROM posts WHERE author_id = ?"
4. If count > 3 within a single trace → N+1 confirmed.
5. Identify the field resolver causing it from span attributes (graphql.field, graphql.parentType).
6. Remediation: Implement DataLoader for that field.
```

### DataLoader — How It Works

DataLoader coalesces individual loads that occur within a single frame of execution (one event-loop tick) into a single batch request.

```
                     ┌─────────────┐
  load(1) ──────────▶│             │
  load(2) ──────────▶│  DataLoader │──▶ batchLoad([1,2,3,4])
  load(3) ──────────▶│  (collects) │──▶ SELECT * FROM posts WHERE author_id IN (1,2,3,4)
  load(4) ──────────▶│             │──▶ Returns: {1:[...], 2:[...], 3:[...], 4:[...]}
                     └─────────────┘
```

---

## Query Complexity & Depth Limiting

### The Danger

```graphql
query MaliciousQuery {
  users {
    posts {
      comments {
        author {
          posts {
            comments {
              author {
                posts {
                  comments {
                    author {
                      name
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

This 7-level-deep query can join dozens of tables, scan millions of rows, and consume gigabytes of memory server-side. A single malicious query can DoS the GraphQL server.

### Real Scenario

**Incident:** "GraphQL endpoint response time spikes from 50ms to 45,000ms. Database CPU hits 100%. Connection pool exhausted."

**Root Cause:** An attacker discovered the GraphQL endpoint and crafted a deeply nested query with 6 levels of circular relationships (`User → Post → Comment → User → Post → Comment → User`). The query resolved to 14 table joins, scanning 3 million rows. The attacker ran this query 50 times/second for 10 minutes.

**Response:**
```graphql
# Immediate mitigation (Apollo Server):
const server = new ApolloServer({
  validationRules: [
    depthLimit(4),  # Max query depth of 4
  ],
});
```

### Depth Limiting Configuration

**Apollo Server:**
```javascript
const { createServer } = require('http');
const { ApolloServer } = require('apollo-server-express');
const depthLimit = require('graphql-depth-limit');

const server = new ApolloServer({
  typeDefs,
  resolvers,
  validationRules: [depthLimit(5)],  // Max 5 levels deep
});
```

**Strawberry (Python):**
```python
from strawberry.extensions import MaxTokensLimiter, MaxDepthLimiter

schema = strawberry.Schema(
    query=Query,
    extensions=[
        MaxDepthLimiter(max_depth=5),
        MaxTokensLimiter(max_tokens=800),
    ],
)
```

**Graphene (Python):**
```python
from graphene.validation import depth_limit_validator

# In settings or schema definition
from graphql import validate

validation_errors = validate(
    schema=schema.graphql_schema,
    document_ast=document_ast,
    rules=[depth_limit_validator(max_depth=5)],
)
```

### Cost Analysis (Advanced)

Assign a complexity cost to each field:

```javascript
const { createComplexityRule } = require('graphql-validation-complexity');

const complexityRule = createComplexityRule({
  maximumCost: 1000,
  defaultCost: 1,
  createCostCalculator: (context) => ({
    onFieldEnter: (cost, args, childComplexity, context) => {
      // connections cost more because they hit the DB
      if (context.fieldName === 'posts') return cost + childComplexity * 10;
      if (context.fieldName === 'comments') return cost + childComplexity * 5;
      return cost + childComplexity;
    },
  }),
});
```

---

## Introspection Abuse

### The Problem

```graphql
query {
  __schema {
    types {
      name
      fields { name type { name kind } }
    }
  }
}
```

Introspection exposes the entire schema, including internal-only types, deprecated fields, and relationships. Attackers use introspection to:
1. Map the entire data model.
2. Discover sensitive fields (e.g., `User.passwordHash`, `User.email`).
3. Find deeply nested relationships for DoS attacks.
4. Automate attacks (GraphQL Voyager, InQL, Clairvoyance).

### Disabling Introspection in Production

**Apollo Server:**
```javascript
const server = new ApolloServer({
  typeDefs,
  resolvers,
  introspection: process.env.NODE_ENV !== 'production',
  playground: false,
});
```

**Strawberry (Python):**
```python
schema = strawberry.Schema(
    query=Query,
    config=strawberry.config.StrawberryConfig(
        auto_camel_case=True,
    ),
)
# In views:
from strawberry.django.views import GraphQLView

view = GraphQLView.as_view(
    schema=schema,
    graphiql=settings.DEBUG,
    # Strawberry disables introspection automatically if graphiql is off
)
```

**Graphene (Python):**
```python
from graphene_django.views import GraphQLView

class ProductionGraphQLView(GraphQLView):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        content = json.loads(response.content)
        if 'errors' in content:
            # In production, strip detailed error messages
            for error in content['errors']:
                error['message'] = 'An error occurred'
        return response
```

**Hasura:**
```yaml
# Disable in production via env var
HASURA_GRAPHQL_ENABLE_CONSOLE: "false"
HASURA_GRAPHQL_DEV_MODE: "false"
```

---

## Schema Stitching / Federation

### The Architecture

```
                  GraphQL Gateway (Apollo Router / GraphQL Mesh)
                  │
        ┌─────────┼─────────┬─────────┐
        ▼         ▼         ▼         ▼
    Users       Posts    Comments  Reviews
   Service     Service   Service   Service
   (subgraph) (subgraph) (subgraph)(subgraph)
```

### Common Failure Modes

**1. Subgraph Schema Change → Gateway Errors**

```
Problem: Posts service adds a required field `publishedAt: DateTime!` to the `Post` type.
         Users service has an extended `Post` type that doesn't know about `publishedAt`.
         Gateway fails to compose schemas because the type is inconsistent across subgraphs.

Error: FEDERATION_ERROR: A @key directive cannot be on a field whose type
       is different across subgraphs.
```

Fix: Coordinate schema changes across subgraph teams. Use `@inaccessible` for internal-only fields:

```graphql
# Posts subgraph
type Post @key(fields: "id") {
  id: ID!
  title: String!
  internalScore: Float! @inaccessible  # Gateway hides this from clients
}
```

**2. Circular Dependency in Federation**

```
Users → Posts → Comments → Users (circular!)
```

Each subgraph references a type from another subgraph. The gateway must resolve the entity chain without hitting infinite loops. Limit entity resolution depth.

---

## Subscriptions (WebSocket)

### WebSocket Connection Lifecycle

```
Client                                  Server
  │                                        │
  │──── connection_init ────────────────▶  │  (Auth payload, e.g., JWT)
  │◀─── connection_ack ─────────────────  │
  │                                        │
  │──── start {id:1, query: sub{...}} ──▶  │  (Subscribe)
  │◀─── data {id:1, payload: {data}} ────  │  (Events arrive)
  │◀─── data {id:1, payload: {data}} ────  │
  │                                        │
  │──── ping ───────────────────────────▶  │  (Keepalive)
  │◀─── pong ────────────────────────────  │
  │                                        │
  │──── stop {id:1} ────────────────────▶  │  (Unsubscribe)
  │◀─── complete {id:1} ─────────────────  │
  │                                        │
  │──── connection_terminate ───────────▶  │
```

### Scenario: Subscriptions Drop After 60 Seconds

**Problem:** "Real-time order status tracking works for exactly 60 seconds, then silently stops. No errors on client or server. Page refresh fixes it temporarily."

**Symptoms:**
- Client WebSocket `readyState` is still `OPEN` (1).
- Server shows the connection as established.
- But no data frames flow in either direction — it's a silent disconnect.

**Root Cause:** AWS ALB has a default idle timeout of 60 seconds. The WebSocket connection is idle (no messages flowing) for 60s, so the ALB silently terminates the TCP connection without sending RST/FIN to either side (half-open TCP connection). The server thinks the client is connected, and the client thinks the server is connected, but no packets can traverse.

**Fix:** Implement ping/pong keepalive every 30 seconds on the client:

```javascript
const ws = new WebSocket('wss://api.example.com/graphql');

ws.onopen = () => {
  const keepalive = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }));
      // If no pong in 10 seconds, consider disconnected
      pongTimeout = setTimeout(() => {
        ws.close();
        reconnect();
      }, 10000);
    }
  }, 30000);
};
```

**Server-side (Apollo):**
```javascript
const httpServer = createServer(app);
const wsServer = new WebSocketServer({ server: httpServer });

const serverCleanup = useServer(
  {
    schema,
    keepAlive: 30_000,  // Send ping every 30 seconds
  },
  wsServer
);
```

**Nginx config:**
```nginx
location /graphql {
    proxy_pass http://graphql-backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;  # Keep WebSocket alive
    proxy_send_timeout 3600s;
}
```

---

## Error Format

### GraphQL Always Returns HTTP 200

This is the most common GraphQL gotcha for monitoring. Unlike REST where `4xx` / `5xx` indicate errors:

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "data": {
    "users": [
      { "id": "1", "name": "Alice" },
      null  ← Null for Bob because of error
    ]
  },
  "errors": [
    {
      "message": "Cannot return null for non-nullable field User.email",
      "locations": [{ "line": 3, "column": 5 }],
      "path": ["users", 1, "email"],
      "extensions": {
        "code": "INTERNAL_SERVER_ERROR",
        "exception": {
          "stacktrace": "NullPointerException at UserResolver.email(UserResolver.java:42)"
        }
      }
    }
  ]
}
```

**Key point:** GraphQL returns partial data (`data` may be populated) along with errors. The HTTP status is always 200 unless the query itself is malformed (400) or the transport layer fails (5xx on connection).

**Monitoring implications:**
- Do NOT monitor GraphQL endpoint health by HTTP status code alone.
- Monitor `errors` array length in the response body.
- Track specific error codes in `extensions.code`.

### Production Error Masking

**Never leak stack traces to clients in production:**

```python
# Python (Strawberry)
from strawberry.extensions import Extension

class MaskErrorsExtension(Extension):
    def on_request_end(self):
        result = self.execution_context.result
        if result.errors:
            for error in result.errors:
                if hasattr(error, 'original_error'):
                    # Log the full error server-side
                    import logging
                    logging.error("GraphQL Error", exc_info=error.original_error)
                # Mask for the client
                error.message = "An internal error occurred"
```

```javascript
// Apollo Server
const server = new ApolloServer({
  typeDefs,
  resolvers,
  formatError: (err) => {
    console.error('GraphQL Error:', err);
    if (process.env.NODE_ENV === 'production') {
      return {
        message: 'An internal error occurred',
        extensions: { code: err.extensions?.code || 'INTERNAL_SERVER_ERROR' },
      };
    }
    return err;
  },
});
```

---

## Code Examples

### Python: Strawberry Server with DataLoader and Depth Limiting

```python
import asyncio
import logging
from typing import List, Optional
from dataclasses import dataclass

import strawberry
from strawberry.types import Info
from strawberry.extensions import MaxDepthLimiter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Models ---
@dataclass
class UserDB:
    id: int
    name: str


@dataclass
class PostDB:
    id: int
    title: str
    author_id: int


# --- Mock database ---
USERS = {1: UserDB(1, "Alice"), 2: UserDB(2, "Bob"), 3: UserDB(3, "Charlie")}
POSTS = [
    PostDB(1, "Post 1", 1),
    PostDB(2, "Post 2", 1),
    PostDB(3, "Post 3", 2),
    PostDB(4, "Post 4", 2),
    PostDB(5, "Post 5", 3),
]


# --- DataLoader (batching layer) ---
class PostLoader:
    """Batches load requests for posts by author IDs."""

    def __init__(self):
        self._pending = {}
        self._loop = asyncio.get_event_loop()

    async def load(self, author_id: int) -> List[PostDB]:
        loader_id = id(self)
        info = getattr(asyncio.current_task(), '_dataloader_info', None)

        if loader_id not in self._pending:
            self._pending[loader_id] = (set(), asyncio.Future())

        ids, future = self._pending[loader_id]
        ids.add(author_id)

        if not future.done():
            self._loop.call_soon(self._batch_dispatch, loader_id)

        results = await future
        return results.get(author_id, [])

    def _batch_dispatch(self, loader_id):
        if loader_id not in self._pending:
            return
        ids, future = self._pending.pop(loader_id)
        logger.info(f"Batch loading posts for author_ids: {sorted(ids)}")

        # Single DB query instead of N queries
        posts_by_author = {}
        for post in POSTS:
            if post.author_id in ids:
                posts_by_author.setdefault(post.author_id, []).append(post)

        future.set_result(posts_by_author)


# --- GraphQL Types ---
@strawberry.type
class Post:
    id: strawberry.ID
    title: str


@strawberry.type
class User:
    id: strawberry.ID
    name: str

    @strawberry.field
    async def posts(self, info: Info) -> List[Post]:
        loader: PostLoader = info.context["post_loader"]
        db_posts = await loader.load(int(self.id))
        return [Post(id=str(p.id), title=p.title) for p in db_posts]


# --- Query ---
@strawberry.type
class Query:
    @strawberry.field
    def users(self) -> List[User]:
        return [User(id=str(u.id), name=u.name) for u in USERS.values()]

    @strawberry.field
    def user(self, id: strawberry.ID) -> Optional[User]:
        u = USERS.get(int(id))
        if u:
            return User(id=str(u.id), name=u.name)
        return None


# --- Schema with extensions ---
schema = strawberry.Schema(
    query=Query,
    extensions=[
        MaxDepthLimiter(max_depth=4),
    ],
)


# --- Context ---
class CustomContext:
    def __init__(self):
        self.post_loader = PostLoader()


async def get_context() -> CustomContext:
    return CustomContext()


# --- Run (ASGI) ---
# uvicorn example: main:schema -- or use strawberry.asgi
if __name__ == "__main__":
    import json
    from graphql import graphql_sync

    async def main():
        query = """
        query {
            users {
                name
                posts { title }
            }
        }
        """
        result = await schema.execute(
            query,
            context_value=CustomContext(),
        )
        print(json.dumps(result.data, indent=2))
        if result.errors:
            for err in result.errors:
                print(f"Error: {err.message}")

    asyncio.run(main())
```

### JavaScript: Apollo Server with DataLoader, Depth Limiting, Error Formatting

```javascript
const { ApolloServer, gql } = require('apollo-server');
const DataLoader = require('dataloader');
const depthLimit = require('graphql-depth-limit');

// --- Mock data ---
const USERS = new Map([
  [1, { id: 1, name: 'Alice' }],
  [2, { id: 2, name: 'Bob'   }],
  [3, { id: 3, name: 'Charlie' }],
]);

const POSTS = [
  { id: 1, title: 'Post 1', authorId: 1 },
  { id: 2, title: 'Post 2', authorId: 1 },
  { id: 3, title: 'Post 3', authorId: 2 },
  { id: 4, title: 'Post 4', authorId: 2 },
  { id: 5, title: 'Post 5', authorId: 3 },
];

// --- Type Definitions ---
const typeDefs = gql`
  type User {
    id: ID!
    name: String!
    posts: [Post!]!
  }

  type Post {
    id: ID!
    title: String!
  }

  type Query {
    users: [User!]!
    user(id: ID!): User
  }
`;

// --- DataLoader batch function ---
async function batchPostsByAuthor(authorIds) {
  console.log(`[DataLoader] Batch loading posts for authorIds: [${authorIds}]`);
  // Single DB query instead of N
  const postsByAuthor = {};
  for (const post of POSTS) {
    if (authorIds.includes(post.authorId)) {
      if (!postsByAuthor[post.authorId]) postsByAuthor[post.authorId] = [];
      postsByAuthor[post.authorId].push(post);
    }
  }
  // Must return in same order as authorIds
  return authorIds.map(id => postsByAuthor[id] || []);
}

// --- Resolvers ---
const resolvers = {
  Query: {
    users: () => Array.from(USERS.values()),
    user: (_, { id }) => USERS.get(Number(id)) || null,
  },
  User: {
    posts: (parent, _, { postLoader }) => {
      return postLoader.load(parent.id);
    },
  },
};

// --- Server ---
const server = new ApolloServer({
  typeDefs,
  resolvers,
  introspection: process.env.NODE_ENV !== 'production',
  validationRules: [depthLimit(5)],
  formatError: (err) => {
    console.error('[GraphQL Error]', {
      message: err.message,
      path: err.path,
      locations: err.locations,
      extensions: err.extensions,
    });

    if (process.env.NODE_ENV === 'production') {
      return {
        message: 'An internal error occurred',
        extensions: {
          code: err.extensions?.code || 'INTERNAL_SERVER_ERROR',
        },
      };
    }
    return err;
  },
  context: () => ({
    postLoader: new DataLoader(batchPostsByAuthor),
  }),
});

// --- Start ---
server.listen({ port: 4000 }).then(({ url }) => {
  console.log(`GraphQL server ready at ${url}`);
});
```

### Java: Netflix DGS Framework Error Handling

```java
package com.example.graphql;

import com.netflix.graphql.dgs.DgsComponent;
import com.netflix.graphql.dgs.DgsQuery;
import com.netflix.graphql.dgs.DgsData;
import com.netflix.graphql.dgs.DgsDataFetchingEnvironment;
import com.netflix.graphql.dgs.context.DgsContext;
import com.netflix.graphql.dgs.exceptions.DgsEntityNotFoundException;
import graphql.GraphQLError;
import graphql.execution.DataFetcherResult;
import graphql.execution.ResultPath;
import org.dataloader.DataLoader;
import org.dataloader.DataLoaderRegistry;

import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.stream.Collectors;

// --- Data Types ---
record User(Long id, String name) {}
record Post(Long id, String title, Long authorId) {}

// --- Repository ---
class UserRepository {
    private static final Map<Long, User> USERS = Map.of(
        1L, new User(1L, "Alice"),
        2L, new User(2L, "Bob"),
        3L, new User(3L, "Charlie")
    );

    public List<User> findAll() {
        return new ArrayList<>(USERS.values());
    }

    public User findById(Long id) {
        return USERS.get(id);
    }
}

class PostRepository {
    private static final List<Post> POSTS = List.of(
        new Post(1L, "Post 1", 1L),
        new Post(2L, "Post 2", 1L),
        new Post(3L, "Post 3", 2L),
        new Post(4L, "Post 4", 2L),
        new Post(5L, "Post 5", 3L)
    );

    public List<Post> findByAuthorIds(List<Long> authorIds) {
        System.out.printf("[DataLoader] Batch loading posts for authorIds: %s%n", authorIds);
        return POSTS.stream()
            .filter(p -> authorIds.contains(p.authorId()))
            .collect(Collectors.toList());
    }
}

// --- DataFetcher (Resolver) ---
@DgsComponent
public class UserDataFetcher {

    private final UserRepository userRepo = new UserRepository();
    private final PostRepository postRepo = new PostRepository();

    @DgsQuery
    public List<User> users() {
        return userRepo.findAll();
    }

    @DgsQuery
    public User user(DgsDataFetchingEnvironment dfe) {
        String idArg = dfe.getArgument("id");
        long id;
        try {
            id = Long.parseLong(idArg);
        } catch (NumberFormatException e) {
            throw new InvalidArgumentException("Invalid user ID format: " + idArg);
        }

        User user = userRepo.findById(id);
        if (user == null) {
            throw new DgsEntityNotFoundException(
                "User not found",
                ResultPath.parse("/user"),
                Map.of("userId", id)
            );
        }
        return user;
    }

    @DgsData(parentType = "User", field = "posts")
    public CompletableFuture<List<Post>> posts(DgsDataFetchingEnvironment dfe) {
        User user = dfe.getSource();
        DataLoader<Long, List<Post>> postLoader = dfe.getDataLoader("posts");

        return postLoader.load(user.id());
    }
}

// --- Custom Exception ---
class InvalidArgumentException extends RuntimeException {
    private final Map<String, Object> extensions;

    public InvalidArgumentException(String message) {
        super(message);
        this.extensions = Map.of("code", "INVALID_ARGUMENT");
    }

    public Map<String, Object> getExtensions() {
        return extensions;
    }
}

// --- Error Handler ---
@DgsComponent
class CustomDataFetcherExceptionHandler implements com.netflix.graphql.dgs.exceptions.DataFetcherExceptionHandler {

    @Override
    public GraphQLError handleException(
        com.netflix.graphql.dgs.exceptions.DgsException exception,
        DgsDataFetchingEnvironment dfe
    ) {
        System.err.printf(
            "[GraphQL Error] Type: %s, Message: %s, Path: %s%n",
            exception.getClass().getSimpleName(),
            exception.getMessage(),
            dfe.getExecutionStepInfo().getPath()
        );

        if (exception instanceof InvalidArgumentException iae) {
            return GraphQLError.newError()
                .message(iae.getMessage())
                .path(dfe.getExecutionStepInfo().getPath())
                .extensions(iae.getExtensions())
                .build();
        }

        if (exception instanceof DgsEntityNotFoundException) {
            return GraphQLError.newError()
                .message(exception.getMessage())
                .path(exception.getPath())
                .extensions(Map.of("code", "NOT_FOUND"))
                .build();
        }

        // Mask internal errors in production
        return GraphQLError.newError()
            .message("An internal error occurred")
            .path(dfe.getExecutionStepInfo().getPath())
            .extensions(Map.of("code", "INTERNAL_SERVER_ERROR"))
            .build();
    }
}

// --- DataLoader Registry Configuration ---
@org.springframework.context.annotation.Configuration
class DataLoaderConfig {

    @org.springframework.context.annotation.Bean
    public DataLoaderRegistry dataLoaderRegistry() {
        PostRepository postRepo = new PostRepository();

        org.dataloader.BatchLoader<Long, List<Post>> batchLoader = authorIds -> {
            List<Post> allPosts = postRepo.findByAuthorIds(new ArrayList<>(authorIds));
            Map<Long, List<Post>> postsByAuthor = allPosts.stream()
                .collect(Collectors.groupingBy(
                    Post::authorId,
                    Collectors.toList()
                ));
            return CompletableFuture.completedFuture(
                authorIds.stream()
                    .map(id -> postsByAuthor.getOrDefault(id, List.of()))
                    .collect(Collectors.toList())
            );
        };

        DataLoader<Long, List<Post>> postLoader = DataLoader.newDataLoader(batchLoader);

        DataLoaderRegistry registry = new DataLoaderRegistry();
        registry.register("posts", postLoader);
        return registry;
    }
}
```
