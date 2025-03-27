import asyncio
from typing import Optional

import ray
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response
from ray import serve

from kodosumi import dtypes
from kodosumi.runner.tracer import Tracer
from kodosumi.serve import Launch, ServeAPI

app = ServeAPI()


async def find_armstrong_numbers(inputs: dict, tracer: Tracer):
    start = inputs.get("start", 1)
    end = inputs.get("end", 999)
    armstrong_numbers = []
    tracer.init()
    await tracer.html(f"<b>search</b> ...")
    divisor = max(1, (end - start) // 25)
    for num in range(start, end + 1):
        num_str = str(num)
        num_digits = len(num_str)
        sum_of_powers = sum(int(digit) ** num_digits for digit in num_str)
        if num == sum_of_powers:
            armstrong_numbers.append(num)
            await tracer.html(f" {len(armstrong_numbers)} ... ")
        if num % divisor == 0:
            await tracer.debug(f"at {num} with {len(armstrong_numbers)} found")
        await asyncio.sleep(0)
    # done calculating
    await tracer.html(f"<p>found {len(armstrong_numbers)}</p>")
    # prep result
    md = ["#### Armstrong Numbers"]
    for num in armstrong_numbers:
         md.append(f"* {num}")
    md.append(
        f"\n**Found total of {len(armstrong_numbers)} Armstrong numbers**")
    # deliver result
    return dtypes.Markdown(body="\n".join(md))


@ray.remote
def check_armstrong_range(start: int, end: int, tracer: Tracer) -> list[int]:
    armstrong_numbers = []
    tracer.init()
    for num in range(start, end + 1):
        num_str = str(num)
        num_digits = len(num_str)
        sum_of_powers = sum(int(digit) ** num_digits for digit in num_str)
        if num == sum_of_powers:
            armstrong_numbers.append(num)
            tracer.debug_sync(f"found {num}")
    return armstrong_numbers


async def find_armstrong_numbers_parallel(inputs: dict, tracer: Tracer):
    start = inputs.get("start", 100)
    end = inputs.get("end", 999)
    tracer.init()
    
    chunk_size = (end - start) // 6
    chunks = [(i, min(i + chunk_size, end)) 
              for i in range(start, end + 1, chunk_size)]
    await tracer.html("<b>Starting parallel search with Ray...</b>")
    futures = [check_armstrong_range.remote(chunk_start, chunk_end, tracer) 
              for chunk_start, chunk_end in chunks]
    all_results = []
    while futures:
        done_id, futures = ray.wait(futures)
        result = ray.get(done_id[0])
        all_results.extend(result)
        await tracer.markdown(f"* Found {len(all_results)} Armstrong numbers so far...")
    await tracer.markdown(f"**Found total of {len(all_results)} Armstrong numbers**")
    
    md = ["#### Armstrong Numbers", "###### (Parallel Distributed Ray Version)"]
    for num in sorted(all_results):
        md.append(f"* {num}")
    return dtypes.Markdown(body="\n".join(md))


@app.get("/", tags=["test"], entry=True,
         summary="Calculate Armstrong numbers",
         description="This service calculates Armstrong numbers within a given range.",
         version="0.1.0",
         author="m.rau@house-of-communication.com")
@app.post("/", entry=True)
async def process(request: Request,
                  n: Optional[str] = Form(None),
                  use_ray: Optional[bool] = Form(False)) -> Response:
    error = n is not None and (not n.isdigit() or int(n) <= 0)
    if n and not error:
        if use_ray:
            func = find_armstrong_numbers_parallel 
        else:
            func = find_armstrong_numbers
        return Launch(request, func, inputs={"start": 0, "end": int(n)})
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Armstrong Numbers</h1>
                <p>This is a simple kodosumi example service.</p>
                <p>The service calculates Armstrong Numbers.</p>
                { "<p><b>Error:</b> value must be a positive integer</p>" if error else "" }
                <form method="POST">
                    <input type="text" name="n" value="{n if n else 1000000}"/><br/>
                    <input type="checkbox" id="use_ray" name="use_ray" value="true"/>
                    <label for="use_ray">use ray concurrency</label>
                    <br/>
                    <input type="submit"/>
                </form>
            </body>
        </html>
    """)
    

@serve.deployment
@serve.ingress(app)
class Example2: pass

fast_app = Example2.bind()  # type: ignore

if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example2:app", host="0.0.0.0", port=8001, reload=True)
