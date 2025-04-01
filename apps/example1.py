import asyncio
from typing import Optional

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response
from ray import serve

from kodosumi import response
from kodosumi.core import Launch, ServeAPI, Tracer

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
    return response.Markdown(md)


@app.get("/", tags=["Test"], entry=True,
         summary="Calculate Armstrong numbers",
         description="This service calculates Armstrong numbers within a given range.",
         author="m.rau@house-of-communication.com",
         version="0.1.0")
@app.post("/", entry=True)
async def process(request: Request,
                  n: Optional[str] = Form(None)) -> Response:
    error = n is not None and (not n.isdigit() or int(n) <= 0)
    if n and not error:
        return Launch(request, 
                      find_armstrong_numbers, 
                      inputs={"start": 0, "end": int(n)})
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <p>This is a simple kodosumi example service.</p>
                <p>The service calculates the Fibonacci sequence.</p>
                { "<p><b>Error:</b> value must be a positive integer</p>" if error else "" }
                <form method="POST">
                    <input type="text" name="n" value="{n if n else 1000000}"/>
                    <input type="submit"/>
                </form>
            </body>
        </html>
    """)
    

@serve.deployment
@serve.ingress(app)
class Example1: pass

fast_app = Example1.bind()  # type: ignore

if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example1:app", host="0.0.0.0", port=8001, reload=True)
