"""
Demo application showcasing all supported Kodosumi form elements.

This app demonstrates all input types that are MIP-003 compliant.
Run with: python -m uvicorn examples.form_elements_demo:app --reload --port 8100
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


async def runner1(inputs: dict, tracer: Tracer):
    return {"ok": True}


app = ServeAPI()


# =============================================================================
# Text Input Types Demo
# =============================================================================

@app.enter(
    "/text-inputs",
    model=Model(
        Markdown("""
# Text Input Types

This form demonstrates all text-based input types.
Fill in the fields below to test validation.
        """),
        HR(),

        InputText(
            name="username",
            label="Username",
            placeholder="Enter your username (3-20 chars)",
            required=True,
            size=20,
        ),

        InputPassword(
            name="password",
            label="Password",
            placeholder="Min 8 characters",
            required=True,
            min_length=8,
            max_length=100,
        ),

        InputEmail(
            name="email",
            label="Email Address",
            placeholder="user@example.com",
            required=True,
        ),

        InputUrl(
            name="website",
            label="Website URL",
            placeholder="https://example.com",
            required=False,
        ),

        InputTel(
            name="phone",
            label="Phone Number",
            placeholder="+1 (555) 123-4567",
            required=False,
        ),

        InputSearch(
            name="search_query",
            label="Search Query",
            placeholder="Type to search...",
            required=False,
        ),

        Break(),

        InputArea(
            name="bio",
            label="Biography",
            placeholder="Tell us about yourself (max 500 chars)",
            rows=4,
            max_length=500,
            required=False,
        ),

        Break(),
        Submit("Submit Text Inputs"),
        Cancel("Cancel"),
    ),
    summary="Text Input Types Demo",
    description="Demonstrates all text-based input types",
    tags=["demo"],
)
async def text_inputs_form(inputs: dict, request):
    """Handle text inputs form submission."""
    # Validate required fields
    error = InputsError()

    if not inputs.get("username"):
        error.add(username="Username is required")

    if not inputs.get("password"):
        error.add(password="Password is required")
    elif len(inputs.get("password", "")) < 8:
        error.add(password="Password must be at least 8 characters")

    if not inputs.get("email"):
        error.add(email="Email is required")

    if error:
        raise error

    return Launch(request, runner1, inputs=inputs)
    # return {
    #     "message": "Text inputs received successfully!",
    #     "data": inputs,
    # }


# =============================================================================
# Numeric Input Types Demo
# =============================================================================

@app.enter(
    "/numeric-inputs",
    model=Model(
        Markdown("""
# Numeric Input Types

This form demonstrates numeric input types including number fields and range sliders.
        """),
        HR(),

        InputNumber(
            name="age",
            label="Age",
            placeholder="Enter your age",
            min_value=0,
            max_value=150,
            required=True,
        ),

        InputNumber(
            name="quantity",
            label="Quantity",
            placeholder="1-100",
            min_value=1,
            max_value=100,
            step=1,
            required=True,
        ),

        InputNumber(
            name="price",
            label="Price ($)",
            placeholder="0.00",
            min_value=0,
            step=0.01,
            required=False,
        ),

        Break(),

        Markdown("### Range Slider"),
        DisplayInfo(
            text="Use the slider to select a value between 0 and 100.",
            label="Instructions",
        ),

        InputRange(
            name="volume",
            label="Volume Level (0-100)",
            min_value=0,
            max_value=100,
            step=5,
            value=50,
        ),

        InputRange(
            name="brightness",
            label="Brightness (%)",
            min_value=0,
            max_value=100,
            step=10,
            value=70,
        ),

        Break(),
        Submit("Submit Numeric Inputs"),
        Cancel("Cancel"),
    ),
    summary="Numeric Input Types Demo",
    description="Demonstrates number and range inputs",
    tags=["demo"],
)
async def numeric_inputs_form(inputs: dict, request):
    """Handle numeric inputs form submission."""
    return Launch(request, runner1, inputs=inputs)
    # return {
    #     "message": "Numeric inputs received successfully!",
    #     "data": inputs,
    # }


# =============================================================================
# Date/Time Input Types Demo
# =============================================================================

@app.enter(
    "/datetime-inputs",
    model=Model(
        Markdown("""
# Date & Time Input Types

This form demonstrates all date and time related input types.

**Supported formats:**
- Date: YYYY-MM-DD
- Time: HH:MM
- DateTime: YYYY-MM-DDTHH:MM
- Month: YYYY-MM
- Week: YYYY-Www
        """),
        HR(),

        InputDate(
            name="birth_date",
            label="Birth Date",
            required=True,
            min_date="1900-01-01",
            max_date="2024-12-31",
        ),

        InputTime(
            name="appointment_time",
            label="Appointment Time",
            required=True,
            min_time="09:00",
            max_time="17:00",
        ),

        InputDateTime(
            name="meeting_datetime",
            label="Meeting Date & Time",
            required=False,
        ),

        InputMonth(
            name="expiry_month",
            label="Card Expiry Month",
            required=False,
            min_month="2024-01",
        ),

        InputWeek(
            name="vacation_week",
            label="Vacation Week",
            required=False,
        ),

        Break(),
        Submit("Submit Date/Time Inputs"),
        Cancel("Cancel"),
    ),
    summary="Date/Time Input Types Demo",
    description="Demonstrates date and time inputs",
    tags=["demo"],
)
async def datetime_inputs_form(inputs: dict, request):
    """Handle date/time inputs form submission."""
    return Launch(request, runner1, inputs=inputs)
    # return {
    #     "message": "Date/Time inputs received successfully!",
    #     "data": inputs,
    # }


# =============================================================================
# Selection Input Types Demo
# =============================================================================

@app.enter(
    "/selection-inputs",
    model=Model(
        Markdown("""
# Selection Input Types

This form demonstrates selection inputs: checkboxes, radio buttons, and dropdowns.
        """),
        HR(),

        Markdown("### Checkbox"),
        Checkbox(
            name="agree_terms",
            label="Terms & Conditions",
            option="I agree to the terms and conditions",
            value=False,
        ),

        Checkbox(
            name="subscribe_newsletter",
            label="Newsletter",
            option="Subscribe to our newsletter",
            value=True,
        ),

        Break(),

        Markdown("### Dropdown Select"),
        Select(
            name="country",
            label="Country",
            option=[
                InputOption(name="us", label="United States"),
                InputOption(name="uk", label="United Kingdom"),
                InputOption(name="de", label="Germany"),
                InputOption(name="fr", label="France"),
                InputOption(name="jp", label="Japan"),
            ],
            value="us",
        ),

        Select(
            name="language",
            label="Preferred Language",
            option=[
                InputOption(name="en", label="English"),
                InputOption(name="de", label="German"),
                InputOption(name="fr", label="French"),
                InputOption(name="es", label="Spanish"),
            ],
        ),

        Break(),

        Markdown("### Radio Buttons"),
        InputRadio(
            name="gender",
            label="Gender",
            option=[
                InputOption(name="male", label="Male"),
                InputOption(name="female", label="Female"),
                InputOption(name="other", label="Other"),
                InputOption(name="prefer_not", label="Prefer not to say"),
            ],
            required=True,
        ),

        InputRadio(
            name="plan",
            label="Subscription Plan",
            option=[
                InputOption(name="free", label="Free - $0/month"),
                InputOption(name="basic", label="Basic - $9.99/month"),
                InputOption(name="pro", label="Pro - $19.99/month"),
                InputOption(name="enterprise", label="Enterprise - Contact us"),
            ],
            value="free",
        ),

        Break(),
        Submit("Submit Selection Inputs"),
        Cancel("Cancel"),
    ),
    summary="Selection Input Types Demo",
    description="Demonstrates checkbox, radio, and select inputs",
    tags=["demo"],
)
async def selection_inputs_form(inputs: dict, request):
    """Handle selection inputs form submission."""
    return Launch(request, runner1, inputs=inputs)
    # return {
    #     "message": "Selection inputs received successfully!",
    #     "data": inputs,
    # }


# =============================================================================
# Special Input Types Demo
# =============================================================================

@app.enter(
    "/special-inputs",
    model=Model(
        Markdown("""
# Special Input Types

This form demonstrates special input types: color picker, file upload, and hidden fields.
        """),
        HR(),

        Markdown("### Color Picker"),
        DisplayInfo(
            text="Click the color box to open the color picker.",
            label="Tip",
        ),

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

        Break(),

        Markdown("### File Upload"),
        DisplayInfo(
            text="You can upload one or multiple files.",
            label="Instructions",
        ),

        InputFiles(
            name="single_file",
            label="Upload Single File",
            required=False,
            multiple=False,
        ),

        InputFiles(
            name="multiple_files",
            label="Upload Multiple Files",
            required=False,
            multiple=True,
        ),

        Break(),

        Markdown("### Hidden Field"),
        HTML("<p><em>Hidden fields are not visible but are submitted with the form.</em></p>"),
        InputHidden(name="csrf_token", value="demo-token-12345"),
        InputHidden(name="form_version", value="1.0"),

        Break(),
        Submit("Submit Special Inputs"),
        Cancel("Cancel"),
    ),
    summary="Special Input Types Demo",
    description="Demonstrates color, file, and hidden inputs",
    tags=["demo"],
)
async def special_inputs_form(inputs: dict, request):
    """Handle special inputs form submission."""
    return Launch(request, runner1, inputs=inputs)
    # return {
    #     "message": "Special inputs received successfully!",
    #     "data": inputs,
    # }


# =============================================================================
# Complete Form Demo (All Types)
# =============================================================================

@app.enter(
    "/complete-form",
    model=Model(
        Markdown("""
# Complete Registration Form

This form demonstrates a realistic use case combining multiple input types.
Fill out all required fields (marked with *) to submit.
        """),
        HR(),

        Markdown("## Personal Information"),

        InputText(
            name="full_name",
            label="Full Name *",
            placeholder="John Doe",
            required=True,
        ),

        InputEmail(
            name="email",
            label="Email Address *",
            placeholder="john@example.com",
            required=True,
        ),

        InputPassword(
            name="password",
            label="Password *",
            placeholder="Min 8 characters",
            required=True,
            min_length=8,
        ),

        InputDate(
            name="birth_date",
            label="Date of Birth *",
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
            label="Country *",
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
            label="Subscription Plan *",
            option=[
                InputOption(name="free", label="Free"),
                InputOption(name="premium", label="Premium ($9.99/mo)"),
            ],
            value="free",
            required=True,
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
            rows=3,
            max_length=300,
        ),

        InputUrl(
            name="website",
            label="Personal Website",
            placeholder="https://yourwebsite.com",
        ),

        HR(),
        Markdown("## Terms"),

        Checkbox(
            name="agree_terms",
            label="Agreement *",
            option="I agree to the Terms of Service and Privacy Policy",
        ),

        Checkbox(
            name="subscribe",
            option="Subscribe to newsletter for updates",
            value=True,
        ),

        InputHidden(name="registration_source", value="web_form"),

        Break(),
        Submit("Create Account"),
        Cancel("Cancel"),
    ),
    summary="Complete Registration Form",
    description="A complete registration form demonstrating all input types",
    tags=["demo"],
)
async def complete_form(inputs: dict, request):
    """Handle complete form submission."""
    return Launch(request, runner1, inputs=inputs)
    # return {
    #     "message": "Registration successful!",
    #     "user": {
    #         "name": inputs.get("full_name"),
    #         "email": inputs.get("email"),
    #         "country": inputs.get("country"),
    #         "plan": inputs.get("plan"),
    #     },
    #     "all_data": inputs,
    # }


# =============================================================================
# Index Page
# =============================================================================

@app.get("/")
async def index():
    """Index page with links to all demos."""
    return {
        "title": "Kodosumi Form Elements Demo",
        "description": "Demonstration of all supported MIP-003 compliant form elements",
        "demos": [
            {"name": "Text Inputs", "path": "/text-inputs", "description": "Text, password, email, URL, tel, search, textarea"},
            {"name": "Numeric Inputs", "path": "/numeric-inputs", "description": "Number and range slider inputs"},
            {"name": "Date/Time Inputs", "path": "/datetime-inputs", "description": "Date, time, datetime, month, week"},
            {"name": "Selection Inputs", "path": "/selection-inputs", "description": "Checkbox, radio buttons, dropdown select"},
            {"name": "Special Inputs", "path": "/special-inputs", "description": "Color picker, file upload, hidden fields"},
            {"name": "Complete Form", "path": "/complete-form", "description": "Full registration form combining all types"},
        ],
    }


@serve.deployment
@serve.ingress(app)
class FormDeployment: pass

fast_app = FormDeployment.bind()



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
