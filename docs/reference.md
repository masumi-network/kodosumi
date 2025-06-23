# API Reference

**Table of Contents**

**Authentication**
- [`GET /login`](#get-login)
- [`POST /login`](#post-login)
- [`POST /api/login`](#post-apilogin)
- [`GET` and `POST /logout`](#get-and-post-logout)

**Role Management**
- [`POST /role`](#post-role)
- [`GET /role`](#get-role)
- [`GET /role/{name}`](#get-rolename)
- [`PUT /role/{rid}`](#put-rolerid)
- [`DELETE /role/{rid}`](#delete-rolerid)

**Deployment Management**
- [`POST /deploy/{name}`](#post-deployname)
- [`GET /deploy`](#get-deploy)
- [`GET /deploy/{name}`](#get-deployname)
- [`DELETE /deploy/{name}`](#delete-deployname)
- [`POST /serve`](#post-serve)

**Flow Management**
- [`POST /flow/register`](#post-flowregister)
- [`GET /flow`](#get-flow)
- [`GET /flow/tags`](#get-flowtags)
- [`POST /flow/unregister`](#post-flowunregister)
- [`GET /flow/register`](#get-flowregister)
- [`PUT /flow/register`](#put-flowregister)


## Authentication

Use the `/login` or `/api/login` endpoint to authenticate, retrieve an API key or a cookie for further API interaction. The default username and password is _admin_ and _admin_. Endpoint `/login` authenticates with `GET` plus URL parameters. `POST` authenticates with URL-encoded form data (`application/x-www-form-urlencoded`). The API endpoint `/api/login` authenticates with `POST` and a JSON body (`application/json`). 


### `GET /login`

Authenticates a user with username and password via URL parameters.

**Parameters:**
- `name` (string, required): Username
- `password` (string, required): Password

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "name": "admin",
    "id": "user-uuid",
    "KODOSUMI_API_KEY": "jwt-token"
  }
  ```
- Sets a cookie with the JWT token

**Error Responses:**
- 401 Unauthorized: Invalid credentials or inactive user
- 400 Bad Request: Missing required parameters

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/login",
    params={
        "name": "admin",
        "password": "admin"
    }
)

if response.status_code == 200:
    api_key = response.json().get("KODOSUMI_API_KEY")
    cookies = response.cookies
```

Use the API key in the `KODOSUMI_API_KEY` header for subsequent requests. Alternatively, use the session cookies.


### `POST /login`

Authenticates a user with username and password via form data.

**Content-Type:** `application/x-www-form-urlencoded`

**Parameters:**
- `name` (string, required): Username
- `password` (string, required): Password
- `redirect` (string, optional): URL to redirect after successful authentication

**Response:**
- Status Code: 200 (Success)
- If redirect is provided:
  - Redirects to the specified URL
- If no redirect:
  - Content-Type: application/json
  - Body:
    ```json
    {
      "name": "admin",
      "id": "user-uuid",
      "KODOSUMI_API_KEY": "jwt-token"
    }
    ```
- Sets a cookie with the JWT token

**Error Responses:**
- 401 Unauthorized: Invalid credentials or inactive user
- 400 Bad Request: Missing required parameters

**Example:**
```python
import httpx

response = httpx.post(
    "http://localhost:3370/login",
    data={
        "name": "admin",
        "password": "admin"
    }
)

if response.status_code == 200:
    api_key = response.json().get("KODOSUMI_API_KEY")
    cookies = response.cookies
```

Use the API key in the `KODOSUMI_API_KEY` header for subsequent requests. Alternatively, use the session cookies.


### `POST /api/login`

Authenticates a user with username and password via JSON body.

**Content-Type:** `application/json`

**Request Body:**
```json
{
  "name": "admin",
  "password": "admin"
}
```

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "name": "admin",
    "id": "user-uuid",
    "KODOSUMI_API_KEY": "jwt-token"
  }
  ```
- Sets a cookie with the JWT token

**Error Responses:**
- 401 Unauthorized: Invalid credentials or inactive user
- 400 Bad Request: Invalid JSON or missing required fields

**Example:**
```python
import httpx

response = httpx.post(
    "http://localhost:3370/api/login", 
    json={
        "name": "admin", 
        "password": "admin"
    }
)

if response.status_code == 200:
    api_key = response.json().get("KODOSUMI_API_KEY")
    cookies = response.cookies
```

Use the API key in the `KODOSUMI_API_KEY` header for subsequent requests. Alternatively, use the session cookies.


### `GET` and `POST /logout`

Logs out the current user by removing the session cookie.

**Authentication:**
- Requires valid session cookie or API key

**Response:**
- Status Code: 200 (Success)
- Content-Type: text/plain
- Body: Empty string
- Removes the session cookie

**Error Responses:**
- 401 Unauthorized: No valid session found

**Example:**
```python
import httpx

# Using cookies
response = httpx.get(
    "http://localhost:3370/logout",
    cookies=cookies
)

# Or using API key
response = httpx.get(
    "http://localhost:3370/logout",
    headers={"KODOSUMI_API_KEY": api_key}
)

# POST method works the same way
response = httpx.post(
    "http://localhost:3370/logout",
    cookies=cookies
)
```

After logout, you'll need to authenticate again to access protected endpoints.


## Role Management

The role management endpoints allow operators to manage user roles. All endpoints require operator privileges.


### `POST /role`

Creates a new role.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Content-Type:** `application/json`

**Parameters:**
- `name` (string, required): Username for the new role
- `email` (string, required): Email address for the new role
- `password` (string, required): Password for the new role
- `active` (boolean, optional): Whether the role is active, defaults to true
- `operator` (boolean, optional): Whether the role has operator privileges, defaults to false

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "id": "uuid",
    "name": "string",
    "email": "string",
    "active": true,
    "operator": false
  }
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 400 Bad Request: Missing required parameters or invalid parameter values

**Example:**
```python
import httpx

response = httpx.post(
    "http://localhost:3370/role",
    headers={"KODOSUMI_API_KEY": api_key},
    json={
        "name": "newuser",
        "email": "user@example.com",
        "password": "securepassword",
        "active": True,
        "operator": False
    }
)
```

### `GET /role`

Lists all roles.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Parameters:**
- None

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body: Array of roles sorted by name
  ```json
  [
    {
      "id": "uuid",
      "name": "string",
      "email": "string",
      "active": true,
      "operator": false
    }
  ]
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/role",
    headers={"KODOSUMI_API_KEY": api_key}
)
roles = response.json()
```

### `GET /role/{name}`

Retrieves a specific role by name or UUID.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Parameters:**
- `name` (string, required): Role name or UUID to retrieve

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "id": "uuid",
    "name": "string",
    "email": "string",
    "active": true,
    "operator": false
  }
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 404 Not Found: Role with specified name or UUID not found

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/role/admin",
    headers={"KODOSUMI_API_KEY": api_key}
)
role = response.json()
```

### `PUT /role/{rid}`

Updates an existing role.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Parameters:**
- `rid` (uuid, required): UUID of the role to update

**Content-Type:** `application/json`

**Request Body Parameters:**
- `name` (string, optional): New username for the role
- `email` (string, optional): New email address for the role
- `password` (string, optional): New password for the role
- `active` (boolean, optional): New active status for the role
- `operator` (boolean, optional): New operator status for the role

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "id": "uuid",
    "name": "string",
    "email": "string",
    "active": true,
    "operator": false
  }
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 404 Not Found: Role with specified UUID not found
- 400 Bad Request: Invalid parameter values

**Example:**
```python
import httpx

response = httpx.put(
    "http://localhost:3370/role/123e4567-e89b-12d3-a456-426614174000",
    headers={"KODOSUMI_API_KEY": api_key},
    json={
        "name": "updateduser",
        "email": "updated@example.com",
        "active": True
    }
)
```

### `DELETE /role/{rid}`

Deletes a role.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Parameters:**
- `rid` (uuid, required): UUID of the role to delete

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body: Empty

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 404 Not Found: Role with specified UUID not found

**Example:**
```python
import httpx

response = httpx.delete(
    "http://localhost:3370/role/123e4567-e89b-12d3-a456-426614174000",
    headers={"KODOSUMI_API_KEY": api_key}
)
```

## Deployment Management

The deployment management endpoints allow operators to manage applications. All endpoints require operator privileges. Note that all management of deployments with endpoint `/deploy` defines the target status _to-be_. Each change requires a `POST /serve` which triggers _Ray serve_ to apply the changes.


### `POST /deploy/{name}`

Creates or updates a deployment configuration.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Content-Type:** `text/plain`

**Parameters:**
- `name` (string, required): Name of the deployment configuration

**Request Body:**
YAML configuration for the deployment. Example for the Prime Gap Service:
```yaml
name: prime
route_prefix: /prime
import_path: kodosumi.examples.prime.app:fast_app
runtime_env:
  py_modules:
    - https://github.com/masumi-network/kodosumi-examples/archive/main.zip
```

**Response:**
- Status Code: 201 (Created)
- Content-Type: text/plain
- Body: The stored YAML configuration

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 400 Bad Request: Invalid YAML configuration

**Example:**
```python
import httpx

yaml_config = """
name: prime
route_prefix: /prime
import_path: kodosumi_examples.prime.app:fast_app
runtime_env:
  py_modules:
    - https://github.com/masumi-network/kodosumi-examples.git/archive/2db907d955de65bed5dde6513f6359aeb18ebff1.zip
"""

response = httpx.post(
    "http://localhost:3370/deploy/prime",
    headers={"KODOSUMI_API_KEY": api_key},
    content=yaml_config
)
```


### `GET /deploy`

Lists all deployment configurations and their status.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "prime": "to-deploy"
  }
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/deploy",
    headers={"KODOSUMI_API_KEY": api_key}
)
deployments = response.json()
```


### `GET /deploy/{name}`

Reads a specific deployment configuration.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Parameters:**
- `name` (string, required): Name of the deployment configuration

**Response:**
- Status Code: 200 (Success)
- Content-Type: text/plain
- Body: The YAML configuration

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 404 Not Found: Deployment configuration not found

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/deploy/prime",
    headers={"KODOSUMI_API_KEY": api_key}
)
config = response.text
```


### `DELETE /deploy/{name}`

Deletes a deployment configuration.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Parameters:**
- `name` (string, required): Name of the deployment configuration

**Response:**
- Status Code: 200 (Success)
- Content-Type: text/plain
- Body: Empty

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 404 Not Found: Deployment configuration not found

**Example:**
```python
import httpx

response = httpx.delete(
    "http://localhost:3370/deploy/prime",
    headers={"KODOSUMI_API_KEY": api_key}
)
```


### `POST /serve`

Activates all configured deployments.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Response:**
- Status Code: 201 (Created)
- Content-Type: text/plain
- Body: Empty

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges

**Example:**
```python
import httpx

response = httpx.post(
    "http://localhost:3370/serve",
    headers={"KODOSUMI_API_KEY": api_key}
)
```


### `DELETE /serve`

Deactivates all active deployments.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Response:**
- Status Code: 200 (Success)
- Content-Type: text/plain
- Body: Empty

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges

**Example:**
```python
import httpx

response = httpx.delete(
    "http://localhost:3370/serve",
    headers={"KODOSUMI_API_KEY": api_key}
)
```


## Flow Management

The flow management endpoints allow operators to register, unregister, and manage flow sources. These endpoints handle the registration of OpenAPI specifications and their associated endpoints. _Ray serve_ populates all available deployments with `/-/routes`. Both the OpenAPI endpoint `/openapi.json` as well as `/-/routes` are valid registers.

### `POST /flow/register`

Registers one or more flow sources by their OpenAPI or `Ray serve` routes specification URLs.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Content-Type:** `application/json`

**Request Body:**
```json
{
  "url": [
    "http://localhost:8000/openapi.json",
    "http://localhost:8001/-/routes"
  ]
}
```

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body: Array of endpoint responses
  ```json
  [
    {
      "url": "http://localhost:8000/openapi.json",
      "endpoints": [
        {
          "path": "/api/endpoint1",
          "method": "GET",
          "summary": "Endpoint 1 description"
        }
      ]
    }
  ]
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges
- 400 Bad Request: Invalid request body

**Example:**
```python
import httpx

response = httpx.post(
    "http://localhost:3370/flow/register",
    headers={"KODOSUMI_API_KEY": api_key},
    json={
        "url": [
            "http://localhost:8000/openapi.json",
            "http://localhost:8001/-/routes",
        ]
    }
)
endpoints = response.json()
```

### `GET /flow`

Retrieves a paginated list of registered flows.

**Authentication:**
- No authentication required

**Parameters:**
- `q` (string, optional): Search query to filter flows
- `pp` (integer, optional): Items per page, defaults to 10
- `offset` (string, optional): Pagination offset

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "items": [
      {
        "uid": "unique-id",
        "url": "http://localhost:8000/openapi.json",
        "endpoints": [...]
      }
    ],
    "offset": "next-page-offset"
  }
  ```

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/flow",
    headers={"KODOSUMI_API_KEY": api_key},
    params={
        "q": "search-term",
        "pp": 20
    }
)
flows = response.json()
```

### `GET /flow/tags`

Retrieves a list of all tags used in registered flows with their counts.

**Authentication:**
- No authentication required

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "tag1": 5,
    "tag2": 3
  }
  ```

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/flow/tags",
    headers={"KODOSUMI_API_KEY": api_key},
)
tags = response.json()
```

### `POST /flow/unregister`

Unregisters previously registered flow sources.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Content-Type:** `application/json`

**Request Body:**
```json
{
  "url": [
    "http://localhost:8000/openapi.json"
  ]
}
```

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "deletes": ["http://localhost:8000/openapi.json"]
  }
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges

**Example:**
```python
import httpx

response = httpx.post(
    "http://localhost:3370/flow/unregister",
    headers={"KODOSUMI_API_KEY": api_key},
    json={
        "url": ["http://localhost:8000/openapi.json"]
    }
)
result = response.json()
```

### `GET /flow/register`

Retrieves the list of registered flow sources and their configuration.

**Authentication:**
- No authentication required

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "routes": ["http://localhost:8000/openapi.json"],
    "registers": ["http://localhost:8000"]
  }
  ```

**Example:**
```python
import httpx

response = httpx.get(
    "http://localhost:3370/flow/register",
    headers={"KODOSUMI_API_KEY": api_key},
)
register = response.json()
```

### `PUT /flow/register`

Refreshes all registered flow sources by retrieving their OpenAPI specifications.

**Authentication:**
- Requires operator privileges
- Use API key in `KODOSUMI_API_KEY` header or session cookies

**Response:**
- Status Code: 200 (Success)
- Content-Type: application/json
- Body:
  ```json
  {
    "summaries": ["Endpoint 1 description"],
    "urls": ["http://localhost:8000/openapi.json"],
    "deletes": [],
    "sources": ["http://localhost:8000"],
    "connected": ["http://localhost:8000"]
  }
  ```

**Error Responses:**
- 401 Unauthorized: Not authenticated or insufficient privileges

**Example:**
```python
import httpx

response = httpx.put(
    "http://localhost:3370/flow/register",
    headers={"KODOSUMI_API_KEY": api_key}
)
result = response.json()
```

