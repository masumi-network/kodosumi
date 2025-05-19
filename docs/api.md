# kodosumi panel API

The purpose of this document is to demonstrate the interaction with kodosumi panel API. 

## Authentication

Use the `/login` endpoint to authenticate and to retrieve an API key and a set of cookies for further API interaction. The default username and password is _admin_ and _admin_.

```python
base_url = ""

resp = httpx.post(
    "http://localhost:3370/login", 
    data={
        "name": "admin", 
        "password": "admin"
    }
)
api_key = resp.json().get("KODOSUMI_API_KEY")
cookies = resp.cookies
```

Use the `api_key` or `cookies` with further requests. The following example retrieves the list of flows using `api_key`.

```python
resp = httpx.get(
    "http://localhost:3370/flow", 
    headers={"KODOSUMI_API_KEY": api_key})
resp.json()
```

The response is the first page of an offset paginated list of flows.

```python
{
    'items': [
        {
            'uid': 'ea8dda36eca73636542b1f6acd682574',
            'method': 'GET',
            'url': '/-/127.0.0.1/8002/-/',
            'source': 'http://127.0.0.1:8002/openapi.json',
            'summary': 'Hymn Creator',
            'description': 'This agent creates a short hymn about a given topic...',
            'deprecated': None,
            'author': 'm.rau@house-of-communication.com',
            'organization': None,
            'tags': ['CrewAi', 'Test']
        }
    ],
    'offset': None
 }
 ```

You can also simply use the `cookies`. This demo uses this approach.

```python
resp = httpx.get(
    "http://localhost:3370/flow", cookies=cookies)
resp.json()
```

## Retrieve Inputs Scheme

Retrieve _Hymn Creator_ input schema at `GET /-/127.0.0.1/8002/-/` and launch flow execution with `POST /-/127.0.0.1/8002/-/` and appropriate _inputs_  data.

```python
resp = httpx.get(
    "http://localhost:3370/-/127.0.0.1/8002/-", cookies=cookies)
resp.json()
```

The response contains the _openapi.json_ fields for _summary_ (title), _description_ and _tags_. Some extra fields are in `openapi_extra`. Key `elements` delivers the list of _inputs_ element.

```python
{
    'summary': 'Hymn Creator',
    'description': 'This agent creates a short hymn about a given topic of...',
    'tags': ['Test', 'CrewAi'],
    'openapi_extra': {
        'x-kodosumi': True,
        'x-author': 'm.rau@house-of-communication.com',
        'x-version': '1.0.1'},
    'elements': [
        {
            'type': 'markdown',
            'text': '# Hymn Creator\nThis agent creates a short hymn...'
        },
        {
            'type': 'html', 
            'text': '<div class="space"></div>'
        },
        {
            'type': 'text',
            'name': 'topic',
            'label': 'Topic',
            'value': 'A Du Du Du and A Da Da Da.',
            'required': False,
            'placeholder': None
        },
        {
            'type': 'submit', 
            'text': 'Submit'},
        {
            'type': 'cancel', 'text': 'Cancel'
        }
    ]
}
```

kodosumi rendering engine translates all inputs `elements` into a form to post and trigger flow execution at http://localhost:3370/inputs/-/127.0.0.1/8002/-/

[![Hymn](./panel/thumb/form.png)](./panel/form.png)

To directly `POST` follow the _inputs_ scheme as in the following example:

```python
resp = httpx.post(
    "http://localhost:3370/-/127.0.0.1/8002/-", 
    cookies=cookies,
    json={
        "topic": "Ich wollte ich wäre ein Huhn. In deutscher Sprache die Hymne!"
    }
)
resp
```

In case of success the result contains the `fid` (flow identifier). Use this `fid` for further requests.

```python
fid = resp.json().get("result")
fid
```

In case of failure the result is empty. The response has `errors` as a key/value pair with error information.

```python
resp = httpx.post(
    "http://localhost:3370/-/127.0.0.1/8002/-", 
    cookies=cookies,
    json={"topic": ""})
resp.json()
```

Example error output on _empty_ `topic`:

```python
{
    'errors': {
        'topic': ['Please give me a topic.']
    },
    elements: ...
}
```

## Status Monitoring

Request and poll for status updates at `/outputs/status`.

```python
resp = httpx.get(
    f"http://localhost:3370/outputs/status/{fid}", 
    cookies=cookies)
resp.json()
```

The result after _starting_ but some time before _finish_ looks similar to:

```python
{
    'status': 'running',
    'timestamp': 1747497556.658355,
    'final': None,
    'fid': '6828b2476dd6591e71630987',
    'summary': 'Hymn Creator',
    'description': 'This agent creates a short hymn about a given topic of...',
    'tags': ['Test', 'CrewAi'],
    'deprecated': None,
    'author': 'm.rau@house-of-communication.com',
    'organization': None,
    'version': '1.0.1',
    'kodosumi_version': None,
    'base_url': '/-/127.0.0.1/8002/-/',
    'entry_point': 'apps.hymn.app:crew',
    'username': 'c7eb70a5-0c26-407e-b785-f1ef5e7c4486'
}
 ```

 After completion the status request contains the final result:

 ```python
{
    'status': 'finished',
    'timestamp': 1747497575.1804101,
    'final': '{"CrewOutput":{"raw":..,"token_usage":{"total_tokens":1797,...',
    'fid': '6828b2476dd6591e71630987',
    'summary': 'Hymn Creator',
    'description': 'This agent creates a short hymn about ...',
    'tags': ['Test', 'CrewAi'],
    'deprecated': None,
    'author': 'm.rau@house-of-communication.com',
    'organization': None,
    'version': '1.0.1',
    'kodosumi_version': None,
    'base_url': '/-/127.0.0.1/8002/-/',
    'entry_point': 'apps.hymn.app:crew',
    'username': 'c7eb70a5-0c26-407e-b785-f1ef5e7c4486'
}
 ```

 Since the plain `/status` request might fail due to Ray latencies you should harden the intial request past flow launch with `?extended=true` as in the following example:

 ```python
 resp = httpx.get(
    f"http://localhost:3370/outputs/status/{fid}?extended=true", 
    cookies=cookies)
resp.json()
```

The complete event stream is available at `/outputs/stream`.

```python
with httpx.stream("GET", f"http://localhost:3370/outputs/stream/{fid}", cookies=cookies) as r:
    for text in r.iter_text():
        print(text)
```

