from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Request, Form, Response
from ray import serve
import asyncio
from kodosumi.serve import ServeAPI, Launch, KODOSUMI_API
from kodosumi.runner.tracer import get_tracer
from kodosumi.runner.tracer import markdown, Tracer
from kodosumi.runner.main import Runner
from kodosumi import dtypes
import datetime
from kodosumi.helper import now, serialize
from typing import Optional, Union
from pathlib import Path
import ray


app = ServeAPI()


def is_perfect_number(num):
    if num < 2:
        return False
    sum_of_divisors = 1
    for i in range(2, int(num**0.5) + 1):
        if num % i == 0:
            sum_of_divisors += i
            if i != num // i:
                sum_of_divisors += num // i
    return sum_of_divisors == num

async def find_perfect_numbers(inputs: dict, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    # breakpoint()
    print(f"Get Going with {inputs}")
    divisor = inputs["limit"] / 25
    md = ["#### Perfect Numbers"]
    for num in range(inputs["limit"]):
        if num % divisor == 0:
            await tracer._put_async("stdout", f"this is stdout with {num}")
            # print(f"this is stdout with {num}")
            # await tracer.async_result(dtypes.HTML(
            #     body=f'... {num} '
            # ))
            #tracer.debug(f"debugging message: {num}")
        if is_perfect_number(num):
            md.append(f"* **{num}**")
        await asyncio.sleep(0)
    return dtypes.Markdown(body="\n".join(md))

async def f(start, end, fid):
    ret = []
    tracer = get_tracer(fid)
    t0 = now()
    for num in range(start, end):
        if now() > t0 + 1:
            msg = f"XYZ this is stdout with {num}"
            await tracer._runner.put.remote("stdout", msg)
            t0 = now()
        if is_perfect_number(num):
            ret.append(num)
        await asyncio.sleep(0)
    return ret

@ray.remote
def check_number_range(start, end, tracer: Tracer):
    # r = asyncio.run(f(start, end, fid))
    # return r
    ret = []
    divisor = (end - start) // 25
    print(f"divisor is {divisor} from {start} to {end}")
    if divisor == 0:
        divisor = 1
    for num in range(start, end):
        if num % divisor == 0:
            tracer._put("debug", f"* this is stdout {start} - {end} with {num}\n")
            #print(f"this is stdout with {num}")
            #tracer.result(f"this is stdout with {num}")
        if is_perfect_number(num):
            ret.append(num)
            tracer._put(
                "result", serialize(
                    dtypes.Markdown(
                        body=f"* found {start} - {end} with {num}")
                )
            )
                # tracer.debug(f"debugging message: {num}")
    return ret


def itis():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

async def find_perfect_numbers_with_ray(inputs: dict, tracer: Tracer):
    # from kodosumi import helper
    # helper.debug()
    # breakpoint()
    if not ray.is_initialized():
        ray.init()
    print(f"inputs are {inputs}")
    limit = inputs["limit"]
    #num_cpus = ray.cluster_resources().get("CPU", 1)
    num_cpus = 3
    chunk_size = max(1, limit // int(num_cpus))
    results = []
    md = ["#### Perfect Numbers"]
    n = 0
    for start in range(0, limit, chunk_size):
        n += 1
        end = min(start + chunk_size, limit)
        await tracer._put_async("stdout", f"start {n} with {start} to {end}")
        results.append(check_number_range.remote(start, end, tracer))
        await asyncio.sleep(0)
    
    # Asynchron auf die Ergebnisse warten

    all_results = []
    while results:
        done_id, results = ray.wait(results)
        result = ray.get(done_id[0])
        all_results.append(result)
        await asyncio.sleep(0)  # Gibt die Kontrolle an die Event-Loop zurück

    # Flatten the list of lists into a single list
    for num in sorted([num for sublist in all_results for num in sublist]):
        md.append(f"* **{num}**")
    return dtypes.Markdown(body="\n".join(md))
    # total_count = 0
    # for count in all_results:
    #     tracer.result(f"found {count}")
    #     total_count += count
    # return total_count


@app.get("/", summary="Get/Post combined", 
         description="no reference", entry=True)
@app.post("/")  # not in scope
@app.put("/", entry=True)  # in scope of /flow, but not in admin console
async def get1(request: Request, 
              value: Optional[int] = Form(None)) -> Response:
    if value:
        return Launch(request, find_perfect_numbers, inputs={"limit": value})
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <h2>{__file__}</h2>
                <p>This is a simple kodosumi example app</p>
                <form method="POST">
                    <input type="text" name="value" value="1000000"/>
                    <input type="submit"/>
                </form>
            </body>
        </html>
    """)

@app.get("/noentry", summary="No Kodosumi Endpoint")
async def get2() -> Response:
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <h2>{__file__}</h2>
                <p>This is a simple kodosumi example app</p>
            </body>
        </html>
    """)

@app.get("/2", summary="Get and Post combined (2)", 
         description="hello world", author="m.rau", version="1.2.3",
         organization="Plan.Net Journey", entry=True)
@app.post("/2", summary="Get and Post combined (2) POST", 
         description="hello world POST", author="m.rau POST", 
         organization="Plan.Net Journey POST", entry=True)
async def get3(request: Request, 
              value: Optional[int] = Form(None)) -> Response:
    if value:
        return Launch(request, find_perfect_numbers, inputs={"limit": value})
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <h2>{__file__}</h2>
                <p>This is a simple kodosumi example app</p>
                <form method="POST">
                    <input type="text" name="value" value="1000000"/>
                    <input type="submit"/>
                </form>
            </body>
        </html>
    """)

@app.get("/1", tags=["test"], summary="Get1 of get/post distinct",
         description="Mathematical function to find the perfect number",
         version="3.2.1", author="m.rau", organization="Plan-Net", entry=True)
async def get4() -> Response:
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <h2>{__file__}</h2>
                <p>This is a simple kodosumi example app</p>
                <form action="/1"method="POST">
                    <input type="text" name="value" value="100000"/>
                    <div>
                        <input type="checkbox" id="use_ray" name="use_ray" value="true"/>
                        <label for="use_ray">use ray concurrency</label>
                    </div>
                    <input type="submit"/>
                </form>
            </body>
        </html>
    """)

@app.post("/1", summary="Post1 Method of a dedicated endpoint")
async def post1(request: Request, 
              value: Optional[int] = Form(None),
              use_ray: bool = Form(False)) -> Response:
    func = find_perfect_numbers_with_ray if use_ray else find_perfect_numbers
    return Launch(request, func, inputs={"limit": value},
                  reference=get4)

@serve.deployment
@serve.ingress(app)
class AppTest2: pass

fast_app = AppTest2.bind()  # type: ignore

if __name__ == "__main__":
    import uvicorn
    import sys
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example2:app", host="0.0.0.0", port=8000, reload=True)
    #serve.run(fast_app, name="example2", route_prefix="/example2")
