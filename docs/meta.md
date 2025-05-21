# Service Meta Data

## Agentic Service Annotations

The following attributes are defined with an extended openapi specification.

| attribute    | type    | comment                                            |
| ------------ | ------- | -------------------------------------------------- |
| tags         | `[str]` | rendered as chips                                  |
| summary      | `str`   | rendered as service name                           |
| description  | `str`   | rendered as descriptive text                       |
| deprecated   | `bool`  | declares this operation to be deprecated.          |
| entry        | `bool`  | `False` hides the end point                        |
| author       | `str`   |                                                    |
| organization | `str`   |                                                    |
| version      | `str`   | use semantic versioning with _major, minor, patch_ |

**Example:**

```python
@app.enter("/", 
         tags=["Test"], 
         summary="Hello World Example",
         description="Say hello world.",
         author="m.rau@house-of-communication.com",
         version="0.1.0")
...
```

The following additional meta data is associated with flow execution:

| attribute   | type  | comment                               |
| ----------- | ----- | ------------------------------------- |
| fid         | `str` | flow execution identifier             |
| username    | `str` | user identifier owning flow execution |
| base_url    | `str` | endpoint URL                          |
| entry_point | `str` | entry point                           |
