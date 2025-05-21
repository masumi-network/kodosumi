import sys
from pathlib import Path

import fastapi
import uvicorn
from ray import serve

import kodosumi.core as core
from kodosumi.core import ServeAPI
from kodosumi.core import forms as F

app = ServeAPI()


@app.enter(
        path="/", 
        model=core.forms.Model(
            F.Markdown("# Form Elements Example"),
            F.Errors(),
            F.Break(),
            F.InputText(label="InputText", name="text"),
            F.InputNumber(label="InputNumber", name="number"),
            F.InputNumber(label="InputNumber (0, 10, 0.5)", name="float", 
                          min_value=0, max_value=10, step=0.5),
            F.Checkbox(label="Checkbox", name="checkbox", value=True, option="Go for it"),
            F.Select(label="Select", name="select", option=[
                F.InputOption(label="Item 1", name="item1"),
                F.InputOption(label="Item 2", name="item2"),
                F.InputOption(label="Item 3", name="item3"),
            ], value="item3"),
            F.Select(label="Select", name="select2", option=[
                F.InputOption(label="Select on of these", name=""),
                F.InputOption(label="Item 1", name="item1"),
                F.InputOption(label="Item 2", name="item2"),
                F.InputOption(label="Item 3", name="item3"),
            ]),
            F.InputArea(label="InputArea", name="area"),
            F.InputDate(label="InputDate", name="date", min_date="2025-01-01", 
                        max_date="2025-12-31"),
            F.InputTime(label="InputTime", name="time"),
            F.InputDateTime(label="InputDateTime", name="datetime", 
                            min_datetime="2025-01-01T12:00", 
                            max_datetime="2025-12-31T23:59"),
            F.Action(text="Something else", value="other", name="action"),
            F.Submit("Submit"),
            F.Cancel("Cancel"),
        ),
        summary="Form Elements Example",
        description="This services renders all available form elements as an exmaple.",
        version="1.0.0",
        author="m.rau@house-of-communication.com",
        tags=["Test"]
    )
async def enter(request: fastapi.Request, inputs: dict):
    inputs.pop("_global_")
    error = core.InputsError(f"Something went wrong with this input:<br/>{inputs}")
    error.add(text="This is a test error")
    error.add(number="This is a test error")
    error.add(float="This is a test error")
    error.add(checkbox="This is a test error")
    error.add(select="This is a test error")
    error.add(select2="This is a test error")
    error.add(area="This is a test error")
    raise error


@serve.deployment
@serve.ingress(app)
class FormText: pass

fast_app = FormText.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent.parent))
    uvicorn.run("apps.form.app:app", host="0.0.0.0", port=8003, reload=True)
