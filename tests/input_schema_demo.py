"""
Demo application showcasing all MIP-003 input schema types.

This app demonstrates all input types with and without validation,
including explanatory markdown sections for each element type.

Run with: python tests/input_schema_demo.py
Or: uvicorn tests.input_schema_demo:app --reload --port 8100
"""

from kodosumi.core import ServeAPI, Tracer, Launch, InputsError
from kodosumi.service.inputs.forms import (
    # Display elements
    Markdown,
    HTML,
    HR,
    Break,
    # Text inputs
    InputText,
    InputPassword,
    InputEmail,
    InputUrl,
    InputTel,
    InputSearch,
    InputArea,
    # Numeric inputs
    InputNumber,
    InputRange,
    # Date/Time inputs
    InputDate,
    InputTime,
    InputDateTime,
    InputMonth,
    InputWeek,
    # Selection inputs
    Checkbox,
    Select,
    InputRadio,
    InputOption,
    # Color
    InputColor,
    # File
    InputFiles,
    # Hidden
    InputHidden,
    # Display-only
    DisplayInfo,
    # Actions
    Submit,
    Cancel,
    Model,
)
from ray import serve


async def echo_inputs(inputs: dict, tracer: Tracer):
    """Simple runner that echoes back the inputs."""
    await tracer.debug(f"Received inputs: data={inputs}")
    return {"status": "success", "inputs": inputs}


app = ServeAPI()


# =============================================================================
# TEXT INPUTS - Basic text entry fields
# =============================================================================

@app.enter(
    "/text/basic",
    model=Model(
        Markdown("""
# Text Input (Basic)

The `InputText` element creates a single-line text input field.

**MIP-003 Type:** `text`

## Without Validation
No constraints - accepts any text input.
        """),
        InputText(
            name="free_text",
            label="Free Text Input",
            placeholder="Enter anything...",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Field must have a value
- `size=N` - Display width (also used as max length hint)
- `pattern=regex` - Regex pattern for validation
        """),
        InputText(
            name="validated_text",
            label="Validated Text (required, max 50 chars)",
            placeholder="Enter text",
            required=True,
            size=50,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Text Input Demo",
    description="Basic single-line text input",
    tags=["demo", "text"],
)
async def text_basic(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/text/textarea",
    model=Model(
        Markdown("""
# Textarea Input

The `InputArea` element creates a multi-line text input field.

**MIP-003 Type:** `textarea`

## Without Validation
        """),
        InputArea(
            name="free_textarea",
            label="Free Textarea",
            placeholder="Enter multiple lines of text...",
            rows=4,
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Field must have a value
- `max_length=N` - Maximum character count
- `rows=N` - Display height (visual only, not a validation)
        """),
        InputArea(
            name="validated_textarea",
            label="Validated Textarea (max 200 chars, required)",
            placeholder="Enter up to 200 characters...",
            required=True,
            max_length=200,
            rows=5,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Textarea Input Demo",
    description="Multi-line text input",
    tags=["demo", "text"],
)
async def text_textarea(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/text/password",
    model=Model(
        Markdown("""
# Password Input

The `InputPassword` element creates a masked text input for sensitive data.

**MIP-003 Type:** `password`

## Without Validation
        """),
        InputPassword(
            name="free_password",
            label="Free Password",
            placeholder="Enter any password...",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Field must have a value
- `min_length=N` - Minimum password length (e.g., 8 for security)
- `max_length=N` - Maximum password length
        """),
        InputPassword(
            name="validated_password",
            label="Validated Password (8-100 chars, required)",
            placeholder="Enter secure password (min 8 chars)",
            required=True,
            min_length=8,
            max_length=100,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Password Input Demo",
    description="Masked password input",
    tags=["demo", "text"],
)
async def text_password(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/text/email",
    model=Model(
        Markdown("""
# Email Input

The `InputEmail` element creates an input with email format validation.

**MIP-003 Type:** `email`
**MIP-003 Validation:** `format: email`

## Without Required
Email format is always validated, but field can be empty.
        """),
        InputEmail(
            name="optional_email",
            label="Optional Email",
            placeholder="user@example.com",
        ),
        HR(),
        Markdown("""
## With Required
- `required=True` - Must enter a valid email address
        """),
        InputEmail(
            name="required_email",
            label="Required Email",
            placeholder="user@example.com",
            required=True,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Email Input Demo",
    description="Email input with format validation",
    tags=["demo", "text"],
)
async def text_email(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/text/url",
    model=Model(
        Markdown("""
# URL Input

The `InputUrl` element creates an input with URL format validation.

**MIP-003 Type:** `url`
**MIP-003 Validation:** `format: url`

## Without Required
URL format is validated when a value is entered.
        """),
        InputUrl(
            name="optional_url",
            label="Optional URL",
            placeholder="https://example.com",
        ),
        HR(),
        Markdown("""
## With Required
- `required=True` - Must enter a valid URL
        """),
        InputUrl(
            name="required_url",
            label="Required URL",
            placeholder="https://example.com",
            required=True,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="URL Input Demo",
    description="URL input with format validation",
    tags=["demo", "text"],
)
async def text_url(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/text/tel",
    model=Model(
        Markdown("""
# Telephone Input

The `InputTel` element creates an input for phone numbers.

**MIP-003 Type:** `tel`
**MIP-003 Validation:** `format: tel-pattern`

## Without Required
Phone format validation when value is entered.
        """),
        InputTel(
            name="optional_phone",
            label="Optional Phone",
            placeholder="+1 (555) 123-4567",
        ),
        HR(),
        Markdown("""
## With Required
- `required=True` - Must enter a phone number
        """),
        InputTel(
            name="required_phone",
            label="Required Phone",
            placeholder="+1 (555) 123-4567",
            required=True,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Telephone Input Demo",
    description="Telephone input with pattern validation",
    tags=["demo", "text"],
)
async def text_tel(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/text/search",
    model=Model(
        Markdown("""
# Search Input

The `InputSearch` element creates a search-style text input.

**MIP-003 Type:** `search`

Functionally similar to text input but with search-specific styling.
Some browsers show a clear button.

## Without Validation
        """),
        InputSearch(
            name="free_search",
            label="Search Query",
            placeholder="Type to search...",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must enter a search term
- `size=N` - Display width
        """),
        InputSearch(
            name="validated_search",
            label="Required Search",
            placeholder="Enter search term...",
            required=True,
            size=30,
        ),
        Break(),
        Submit("Search"),
    ),
    summary="Search Input Demo",
    description="Search-style text input",
    tags=["demo", "text"],
)
async def text_search(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# NUMERIC INPUTS - Number and range fields
# =============================================================================

@app.enter(
    "/numeric/number",
    model=Model(
        Markdown("""
# Number Input

The `InputNumber` element creates a numeric input with optional constraints.

**MIP-003 Type:** `number`

## Without Validation
Accepts any numeric value.
        """),
        InputNumber(
            name="free_number",
            label="Free Number",
            placeholder="Enter any number",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must enter a value
- `min_value=N` - Minimum allowed value
- `max_value=N` - Maximum allowed value
- `step=N` - Increment step (e.g., 0.01 for currency)
        """),
        InputNumber(
            name="age",
            label="Age (0-150, required)",
            placeholder="Enter your age",
            required=True,
            min_value=0,
            max_value=150,
        ),
        InputNumber(
            name="price",
            label="Price (0.00 - 9999.99, step 0.01)",
            placeholder="0.00",
            min_value=0,
            max_value=9999.99,
            step=0.01,
        ),
        InputNumber(
            name="quantity",
            label="Quantity (1-100, step 1)",
            placeholder="1",
            min_value=1,
            max_value=100,
            step=1,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Number Input Demo",
    description="Numeric input with min/max/step validation",
    tags=["demo", "numeric"],
)
async def numeric_number(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/numeric/range",
    model=Model(
        Markdown("""
# Range Input (Slider)

The `InputRange` element creates a slider for selecting a value within a range.

**MIP-003 Type:** `range`

Range inputs always have min/max constraints (defaults: 0-100).
The `data` field contains `min`, `max`, `step`, and `default`.

## Basic Range
        """),
        InputRange(
            name="basic_range",
            label="Basic Range (0-100)",
            min_value=0,
            max_value=100,
            value=50,
        ),
        HR(),
        Markdown("""
## Customized Range
- `min_value=N` - Minimum slider value
- `max_value=N` - Maximum slider value
- `step=N` - Increment step
- `value=N` - Initial/default value
        """),
        InputRange(
            name="volume",
            label="Volume (0-100, step 5)",
            min_value=0,
            max_value=100,
            step=5,
            value=50,
        ),
        InputRange(
            name="brightness",
            label="Brightness % (0-100, step 10)",
            min_value=0,
            max_value=100,
            step=10,
            value=70,
        ),
        InputRange(
            name="temperature",
            label="Temperature (15-30, step 0.5)",
            min_value=15,
            max_value=30,
            step=0.5,
            value=22,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Range Input Demo",
    description="Slider input for selecting values within a range",
    tags=["demo", "numeric"],
)
async def numeric_range(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# DATE/TIME INPUTS - Temporal fields
# =============================================================================

@app.enter(
    "/datetime/date",
    model=Model(
        Markdown("""
# Date Input

The `InputDate` element creates a date picker.

**MIP-003 Type:** `date`
**Format:** `YYYY-MM-DD`

## Without Validation
        """),
        InputDate(
            name="free_date",
            label="Any Date",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must select a date
- `min_date="YYYY-MM-DD"` - Earliest allowed date
- `max_date="YYYY-MM-DD"` - Latest allowed date
        """),
        InputDate(
            name="birth_date",
            label="Birth Date (1900-2010, required)",
            required=True,
            min_date="1900-01-01",
            max_date="2010-12-31",
        ),
        InputDate(
            name="future_date",
            label="Future Date (2024 onwards)",
            min_date="2024-01-01",
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Date Input Demo",
    description="Date picker with optional range constraints",
    tags=["demo", "datetime"],
)
async def datetime_date(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/datetime/time",
    model=Model(
        Markdown("""
# Time Input

The `InputTime` element creates a time picker.

**MIP-003 Type:** `time`
**Format:** `HH:MM`

## Without Validation
        """),
        InputTime(
            name="free_time",
            label="Any Time",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must select a time
- `min_time="HH:MM"` - Earliest allowed time
- `max_time="HH:MM"` - Latest allowed time
        """),
        InputTime(
            name="appointment_time",
            label="Appointment Time (09:00-17:00, required)",
            required=True,
            min_time="09:00",
            max_time="17:00",
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Time Input Demo",
    description="Time picker with optional range constraints",
    tags=["demo", "datetime"],
)
async def datetime_time(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/datetime/datetime",
    model=Model(
        Markdown("""
# DateTime Input

The `InputDateTime` element creates a combined date and time picker.

**MIP-003 Type:** `datetime-local`
**Format:** `YYYY-MM-DDTHH:MM`

## Without Validation
        """),
        InputDateTime(
            name="free_datetime",
            label="Any Date & Time",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must select date and time
- `min_datetime="YYYY-MM-DDTHH:MM"` - Earliest allowed
- `max_datetime="YYYY-MM-DDTHH:MM"` - Latest allowed
        """),
        InputDateTime(
            name="meeting_datetime",
            label="Meeting (from 2024-01-01, required)",
            required=True,
            min_datetime="2024-01-01T00:00",
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="DateTime Input Demo",
    description="Combined date and time picker",
    tags=["demo", "datetime"],
)
async def datetime_datetime(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/datetime/month",
    model=Model(
        Markdown("""
# Month Input

The `InputMonth` element creates a month/year picker.

**MIP-003 Type:** `month`
**Format:** `YYYY-MM`

## Without Validation
        """),
        InputMonth(
            name="free_month",
            label="Any Month",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must select a month
- `min_month="YYYY-MM"` - Earliest allowed month
- `max_month="YYYY-MM"` - Latest allowed month
        """),
        InputMonth(
            name="expiry_month",
            label="Card Expiry (from 2024-01, required)",
            required=True,
            min_month="2024-01",
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Month Input Demo",
    description="Month/year picker",
    tags=["demo", "datetime"],
)
async def datetime_month(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/datetime/week",
    model=Model(
        Markdown("""
# Week Input

The `InputWeek` element creates a week picker.

**MIP-003 Type:** `week`
**Format:** `YYYY-Www` (e.g., 2024-W01)

## Without Validation
        """),
        InputWeek(
            name="free_week",
            label="Any Week",
        ),
        HR(),
        Markdown("""
## With Validation
- `required=True` - Must select a week
- `min_week="YYYY-Www"` - Earliest allowed week
- `max_week="YYYY-Www"` - Latest allowed week
        """),
        InputWeek(
            name="vacation_week",
            label="Vacation Week (required)",
            required=True,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Week Input Demo",
    description="Week picker",
    tags=["demo", "datetime"],
)
async def datetime_week(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# SELECTION INPUTS - Choice fields
# =============================================================================

@app.enter(
    "/selection/checkbox",
    model=Model(
        Markdown("""
# Checkbox Input (Boolean)

The `Checkbox` element creates a single boolean toggle.

**MIP-003 Type:** `boolean`
**MIP-003 Data:** `description` contains the checkbox label text

## Without Required
Checkbox can be checked or unchecked.
        """),
        Checkbox(
            name="optional_checkbox",
            label="Optional Setting",
            option="Enable this optional feature",
            value=False,
        ),
        Checkbox(
            name="default_checked",
            label="Default Checked",
            option="This is checked by default",
            value=True,
        ),
        HR(),
        Markdown("""
## Pre-checked Checkbox
Set `value=True` to have it checked by default.

Note: For boolean type, the MIP-003 schema shows only `description`
in `data`, not `default`. Checkbox doesn't support `required` parameter.
        """),
        Checkbox(
            name="agree_terms",
            label="Terms Agreement",
            option="I agree to the terms and conditions",
            value=False,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Checkbox Input Demo",
    description="Boolean checkbox input",
    tags=["demo", "selection"],
)
async def selection_checkbox(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/selection/select",
    model=Model(
        Markdown("""
# Select Input (Dropdown)

The `Select` element creates a dropdown selection list.

**MIP-003 Type:** `option`
**MIP-003 Data:** `values` contains the list of option names

## Without Required
        """),
        Select(
            name="optional_select",
            label="Optional Selection",
            option=[
                InputOption(name="", label="-- Select --"),
                InputOption(name="a", label="Option A"),
                InputOption(name="b", label="Option B"),
                InputOption(name="c", label="Option C"),
            ],
        ),
        HR(),
        Markdown("""
## With Default Value
Use `value="option_name"` to pre-select an option.
        """),
        Select(
            name="with_default",
            label="With Default (B selected)",
            option=[
                InputOption(name="a", label="Option A"),
                InputOption(name="b", label="Option B"),
                InputOption(name="c", label="Option C"),
            ],
            value="b",
        ),
        HR(),
        Markdown("""
## Country Selection
Select doesn't have a `required` parameter. Use form-level validation instead.
        """),
        Select(
            name="country_select",
            label="Country Selection",
            option=[
                InputOption(name="", label="-- Please Select --"),
                InputOption(name="us", label="United States"),
                InputOption(name="uk", label="United Kingdom"),
                InputOption(name="de", label="Germany"),
            ],
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Select Input Demo",
    description="Dropdown selection input",
    tags=["demo", "selection"],
)
async def selection_select(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/selection/radio",
    model=Model(
        Markdown("""
# Radio Button Input

The `InputRadio` element creates a group of radio buttons.

**MIP-003 Type:** `radio`
**MIP-003 Data:** `values` contains the list of option names

## Without Required
        """),
        InputRadio(
            name="optional_radio",
            label="Optional Choice",
            option=[
                InputOption(name="opt1", label="Option 1"),
                InputOption(name="opt2", label="Option 2"),
                InputOption(name="opt3", label="Option 3"),
            ],
        ),
        HR(),
        Markdown("""
## With Default Value
Use `value="option_name"` to pre-select an option.
        """),
        InputRadio(
            name="with_default",
            label="With Default (Option 2)",
            option=[
                InputOption(name="opt1", label="Option 1"),
                InputOption(name="opt2", label="Option 2"),
                InputOption(name="opt3", label="Option 3"),
            ],
            value="opt2",
        ),
        HR(),
        Markdown("""
## With Required
- `required=True` - Must select one option
        """),
        InputRadio(
            name="required_radio",
            label="Required Choice",
            option=[
                InputOption(name="yes", label="Yes"),
                InputOption(name="no", label="No"),
                InputOption(name="maybe", label="Maybe"),
            ],
            required=True,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Radio Button Input Demo",
    description="Radio button group input",
    tags=["demo", "selection"],
)
async def selection_radio(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# SPECIAL INPUTS - Color, File, Hidden
# =============================================================================

@app.enter(
    "/special/color",
    model=Model(
        Markdown("""
# Color Input

The `InputColor` element creates a color picker.

**MIP-003 Type:** `color`
**MIP-003 Data:** `default` contains the initial color value

## Basic Color Picker
Always has a value (defaults to black if not specified).
        """),
        InputColor(
            name="basic_color",
            label="Pick a Color",
        ),
        HR(),
        Markdown("""
## With Default Color
Use `value="#RRGGBB"` to set the initial color.
        """),
        InputColor(
            name="primary_color",
            label="Primary Color",
            value="#3498db",
        ),
        InputColor(
            name="secondary_color",
            label="Secondary Color",
            value="#e74c3c",
        ),
        InputColor(
            name="accent_color",
            label="Accent Color",
            value="#2ecc71",
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Color Input Demo",
    description="Color picker input",
    tags=["demo", "special"],
)
async def special_color(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/special/file",
    model=Model(
        Markdown("""
# File Input

The `InputFiles` element creates a file upload field.

**MIP-003 Type:** `file`
**MIP-003 Data:** `outputFormat: "url"`, optionally `multiple: true`

## Single File Upload
        """),
        InputFiles(
            name="single_file",
            label="Upload Single File",
            multiple=False,
        ),
        HR(),
        Markdown("""
## Multiple File Upload
Use `multiple=True` to allow multiple files.
        """),
        InputFiles(
            name="multiple_files",
            label="Upload Multiple Files",
            multiple=True,
        ),
        HR(),
        Markdown("""
## With Required
- `required=True` - Must upload at least one file
        """),
        InputFiles(
            name="required_file",
            label="Required File Upload",
            required=True,
            multiple=False,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="File Input Demo",
    description="File upload input",
    tags=["demo", "special"],
)
async def special_file(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


@app.enter(
    "/special/hidden",
    model=Model(
        Markdown("""
# Hidden Input

The `InputHidden` element creates an invisible field that submits with the form.

**MIP-003 Type:** `hidden`
**MIP-003 Data:** `value` contains the hidden value

Useful for:
- CSRF tokens
- Form identifiers
- Tracking values
- Metadata

## Hidden Fields (not visible but submitted)
        """),
        HTML("<p><em>The hidden fields below are not visible but will be submitted:</em></p>"),
        InputHidden(name="csrf_token", value="secure-token-12345"),
        InputHidden(name="form_id", value="demo-form-001"),
        InputHidden(name="version", value="1.0"),
        Markdown("""
**Hidden values being submitted:**
- `csrf_token`: "secure-token-12345"
- `form_id`: "demo-form-001"
- `version`: "1.0"
        """),
        InputText(
            name="visible_field",
            label="Visible Field",
            placeholder="Enter something to see hidden fields in response",
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Hidden Input Demo",
    description="Hidden field input",
    tags=["demo", "special"],
)
async def special_hidden(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# DISPLAY-ONLY ELEMENTS
# =============================================================================

@app.enter(
    "/display/info",
    model=Model(
        Markdown("""
# Display Info (None Type)

The `DisplayInfo` element shows informational text without accepting input.

**MIP-003 Type:** `none`

Used for labels, instructions, or informational text within forms.

## Display Info Examples
        """),
        DisplayInfo(
            text="This is an informational message that does not accept input.",
            label="Information",
        ),
        DisplayInfo(
            text="You can use this to provide context or instructions to users.",
            label="Instructions",
        ),
        HR(),
        Markdown("""
## Combined with Inputs
Display elements can provide context for input fields.
        """),
        DisplayInfo(
            text="Please enter your full legal name as it appears on official documents.",
            label="Name Requirements",
        ),
        InputText(
            name="full_name",
            label="Full Name",
            placeholder="John Doe",
            required=True,
        ),
        Break(),
        Submit("Submit"),
    ),
    summary="Display Info Demo",
    description="Informational display element",
    tags=["demo", "display"],
)
async def display_info(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# COMPLETE EXAMPLE - All Types Combined
# =============================================================================

@app.enter(
    "/complete",
    model=Model(
        Markdown("""
# Complete Input Schema Demo

This form demonstrates all MIP-003 input types in a realistic scenario.

---
## Personal Information
        """),
        InputText(
            name="full_name",
            label="Full Name",
            placeholder="John Doe",
            required=True,
            size=100,
        ),
        InputEmail(
            name="email",
            label="Email Address",
            placeholder="john@example.com",
            required=True,
        ),
        InputPassword(
            name="password",
            label="Password",
            placeholder="Min 8 characters",
            required=True,
            min_length=8,
        ),
        InputDate(
            name="birth_date",
            label="Date of Birth",
            required=True,
            min_date="1900-01-01",
            max_date="2010-12-31",
        ),
        InputTel(
            name="phone",
            label="Phone Number",
            placeholder="+1 (555) 123-4567",
        ),

        HR(),
        Markdown("## Preferences"),

        Select(
            name="country",
            label="Country",
            option=[
                InputOption(name="", label="-- Select Country --"),
                InputOption(name="us", label="United States"),
                InputOption(name="uk", label="United Kingdom"),
                InputOption(name="de", label="Germany"),
                InputOption(name="other", label="Other"),
            ],
        ),
        InputRadio(
            name="plan",
            label="Subscription Plan",
            option=[
                InputOption(name="free", label="Free"),
                InputOption(name="basic", label="Basic - $9.99/mo"),
                InputOption(name="premium", label="Premium - $19.99/mo"),
            ],
            value="free",
            required=True,
        ),
        InputNumber(
            name="budget",
            label="Monthly Budget ($)",
            placeholder="0.00",
            min_value=0,
            max_value=10000,
            step=0.01,
        ),
        InputRange(
            name="experience",
            label="Experience Level (1-10)",
            min_value=1,
            max_value=10,
            step=1,
            value=5,
        ),
        InputColor(
            name="theme_color",
            label="Preferred Theme Color",
            value="#3498db",
        ),

        HR(),
        Markdown("## Additional Information"),

        InputArea(
            name="bio",
            label="Short Bio",
            placeholder="Tell us about yourself...",
            rows=4,
            max_length=500,
        ),
        InputUrl(
            name="website",
            label="Personal Website",
            placeholder="https://yourwebsite.com",
        ),
        InputDateTime(
            name="available_from",
            label="Available From",
        ),
        InputFiles(
            name="resume",
            label="Upload Resume (optional)",
            multiple=False,
        ),

        HR(),
        Markdown("## Terms & Conditions"),

        Checkbox(
            name="agree_terms",
            label="Agreement",
            option="I agree to the Terms of Service and Privacy Policy",
        ),
        Checkbox(
            name="subscribe",
            label="Newsletter",
            option="Subscribe to our newsletter for updates",
            value=True,
        ),

        InputHidden(name="source", value="demo_form"),
        InputHidden(name="version", value="1.0"),

        Break(),
        Submit("Create Account"),
        Cancel("Cancel"),
    ),
    summary="Complete Registration Form",
    description="Demonstrates all MIP-003 input types in a realistic form",
    tags=["demo", "complete"],
)
async def complete_form(inputs: dict, request):
    return Launch(request, echo_inputs, inputs=inputs)


# =============================================================================
# INDEX PAGE
# =============================================================================

@app.get("/")
async def index():
    """Index page with links to all demos."""
    return {
        "title": "MIP-003 Input Schema Demo",
        "description": "Demonstration of all MIP-003 compliant input types",
        "categories": {
            "text": {
                "name": "Text Inputs",
                "demos": [
                    {"path": "/text/basic", "name": "Text", "type": "text"},
                    {"path": "/text/textarea", "name": "Textarea", "type": "textarea"},
                    {"path": "/text/password", "name": "Password", "type": "password"},
                    {"path": "/text/email", "name": "Email", "type": "email"},
                    {"path": "/text/url", "name": "URL", "type": "url"},
                    {"path": "/text/tel", "name": "Telephone", "type": "tel"},
                    {"path": "/text/search", "name": "Search", "type": "search"},
                ],
            },
            "numeric": {
                "name": "Numeric Inputs",
                "demos": [
                    {"path": "/numeric/number", "name": "Number", "type": "number"},
                    {"path": "/numeric/range", "name": "Range", "type": "range"},
                ],
            },
            "datetime": {
                "name": "Date/Time Inputs",
                "demos": [
                    {"path": "/datetime/date", "name": "Date", "type": "date"},
                    {"path": "/datetime/time", "name": "Time", "type": "time"},
                    {"path": "/datetime/datetime", "name": "DateTime", "type": "datetime-local"},
                    {"path": "/datetime/month", "name": "Month", "type": "month"},
                    {"path": "/datetime/week", "name": "Week", "type": "week"},
                ],
            },
            "selection": {
                "name": "Selection Inputs",
                "demos": [
                    {"path": "/selection/checkbox", "name": "Checkbox", "type": "boolean"},
                    {"path": "/selection/select", "name": "Select", "type": "option"},
                    {"path": "/selection/radio", "name": "Radio", "type": "radio"},
                ],
            },
            "special": {
                "name": "Special Inputs",
                "demos": [
                    {"path": "/special/color", "name": "Color", "type": "color"},
                    {"path": "/special/file", "name": "File", "type": "file"},
                    {"path": "/special/hidden", "name": "Hidden", "type": "hidden"},
                ],
            },
            "display": {
                "name": "Display Elements",
                "demos": [
                    {"path": "/display/info", "name": "Display Info", "type": "none"},
                ],
            },
            "complete": {
                "name": "Complete Examples",
                "demos": [
                    {"path": "/complete", "name": "Full Registration Form", "type": "all"},
                ],
            },
        },
    }


# =============================================================================
# RAY SERVE DEPLOYMENT
# =============================================================================

@serve.deployment
@serve.ingress(app)
class InputSchemaDemo:
    pass


fast_app = InputSchemaDemo.bind()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
