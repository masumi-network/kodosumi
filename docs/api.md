# kodosumi panel API

The purpose of this document is to demonstrate the interaction with kodosumi panel API. To run the example requests below ensure you have an agentic service `Hymn Creator` up and running on your _localhost_. See [README](../README.md) on installation, setup and deployment of Ray, kodosumi and the _Hymn Creator_ agentic service.

## Authentication

Use the `/login` or `/api/login` endpoint to authenticate, retrieve an API key and a set of cookies for further API interaction. The default username and password is _admin_ and _admin_. Endpoint `/login` authenticates with `GET` plus URL parameters. `POST` authenticates with URL-encoded form data (`application/x-www-form-urlencoded`). The API endpoint `/api/login` authenticates with `POST` and a JSON body (`application/json`). 

```python
import httpx

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

The corresponding request to `/api/login` is
    
```python
httpx.post(
    "http://localhost:3370/api/login", 
    json={
        "name": "admin", 
        "password": "admin"
    }
)
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

{'items': [{'author': 'm.rau@house-of-communication.com',
            'deprecated': None,
            'description': 'This agent creates a short hymn about a given '
                           'topic of your choice using openai and crewai.',
            'method': 'GET',
            'organization': None,
            'source': 'http://localhost:8001/hymn/openapi.json',
            'summary': 'Hymn Creator',
            'tags': ['CrewAi', 'Test'],
            'uid': '48ff6c11855aceed7f16ab190328c53c',
            'url': '/-/localhost/8001/hymn/-/'}],
 'offset': None}
```

You can also simply use the `cookies`. This demo uses this approach.

```python
resp = httpx.get("http://localhost:3370/flow", cookies=cookies)
resp.json()
```

## Retrieve Inputs Scheme

Retrieve _Hymn Creator_ input schema at [`GET /-/localhost/8001/hymn/-`](http://localhost:3370/-/localhost/8001/hymn/-) and launch flow execution with `POST /-/localhost/8001/hymn/-` and appropriate _inputs_  data.

```python
resp = httpx.get(
    "http://localhost:3370/-/localhost/8001/hymn/-", cookies=cookies)
resp.json()
```

The response contains the _openapi.json_ fields for _summary_ (title), _description_ and _tags_. Some extra fields are in `openapi_extra`. Key `elements` delivers the list of _inputs_ element.

```python
{
    'summary': 'Hymn Creator',
    'description': 'This agent creates a short hymn about a given topic...',
    'tags': ['Test', 'CrewAi'],
    'openapi_extra': {
        'x-kodosumi': True,
        'x-author': 'm.rau@house-of-communication.com',
        'x-version': '1.0.1'
    },
    'elements': [
        {
            'type': 'markdown',
            'text': '# Hymn Creator\nThis agent creates a short hymn...
        '},
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
            'placeholder': None,
            'size': None,
            'pattern': None
        },
        {
            'type': 'submit', 
            'text': 'Submit'
        },
        {
            'type': 'cancel', 
            'text': 'Cancel'
        }
    ]
}
```

## Launch

kodosumi rendering engine translates all inputs `elements` into a form to post and trigger flow execution at [http://localhost:3370/inputs/-/localhost/8001/hymn/-/](http://localhost:3370/inputs/-/localhost/8001/hymn/-/).

[![Hymn](./panel/thumb/form.png)](./panel/form.png)

To directly `POST` follow the _inputs_ scheme as in example:

```python
resp = httpx.post(
    "http://localhost:3370/-/localhost/8001/hymn/-/", 
    cookies=cookies,
    json={
        "topic": "Ich wollte ich wäre ein Huhn."
    }
)
```

In case of success the result contains the `fid` (flow identifier). Use this `fid` for further requests.

```python
fid = resp.json().get("result")
```

## Error Handling

In case of failure the result is empty. The response has `errors` as a key/value pair with error information.

```python
resp = httpx.post(
    "http://localhost:3370/-/localhost/8001/hymn/-/", 
    cookies=cookies,
    json={"topic": ""})  # not accepted !
assert resp.status_code == 200
assert resp.json().get("result") is None
```

Example error output on _empty_ `topic`:

```python
{
    'errors': {
        'topic': ['Please give me a topic.'], 
        '_global_': []
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
    'timestamp': 1747813976.091786,
    'final': None,
    'fid': '682d86536dd659324a5c8901',
    'summary': 'Hymn Creator',
    'description': 'This agent creates a short hymn about a given topic...',
    'tags': ['Test', 'CrewAi'],
    'deprecated': None,
    'author': 'm.rau@house-of-communication.com',
    'organization': None,
    'version': '1.0.1',
    'kodosumi_version': None,
    'base_url': '/-/localhost/8001/hymn/-/',
    'entry_point': 'hymn.app:crew',
    'username': '35a04fc4-4442-4b24-b109-614b45d52de1'
}
```

After completion the status request contains the final result:

```python
{
   'status': 'finished',
   'timestamp': 1747813996.8025322,
   'final': '{"CrewOutput":{"raw":"**Hymn Title: \\"Ich wollte ich wäre ein...',
   'fid': '682d86536dd659324a5c8901',
   'summary': 'Hymn Creator',
   'description': 'This agent creates a short hymn about a given topic...',
   'tags': ['Test', 'CrewAi'],
   'deprecated': None,
   'author': 'm.rau@house-of-communication.com',
   'organization': None,
   'version': '1.0.1',
   'kodosumi_version': None,
   'base_url': '/-/localhost/8001/hymn/-/',
   'entry_point': 'hymn.app:crew',
   'username': '35a04fc4-4442-4b24-b109-614b45d52de1'

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

