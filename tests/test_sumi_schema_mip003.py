"""
Tests for MIP-003 compliant schema conversion.

Tests bidirectional conversion between Kodosumi form elements and MIP-003 InputField format.
"""

import pytest

from kodosumi.service.inputs.forms import (
    Checkbox,
    DisplayInfo,
    InputArea,
    InputColor,
    InputDate,
    InputDateTime,
    InputEmail,
    InputFiles,
    InputHidden,
    InputMonth,
    InputNumber,
    InputPassword,
    InputRadio,
    InputRange,
    InputSearch,
    InputTel,
    InputText,
    InputTime,
    InputUrl,
    InputWeek,
    InputOption,
    Model,
    Select,
    Submit,
)
from kodosumi.service.sumi.models import InputField, InputSchemaResponse
from kodosumi.service.sumi.schema import (
    TYPE_MAP_FROM_MIP003,
    TYPE_MAP_TO_MIP003,
    convert_element_to_mip003,
    convert_mip003_to_element,
    convert_model_to_schema,
    convert_schema_to_elements,
    create_empty_schema,
)


def get_validation(validations, key):
    """Helper to get validation value from array format."""
    if validations is None:
        return None
    for v in validations:
        if v.get("validation") == key:
            return v.get("value")
    return None


def has_validation(validations, key, value=None):
    """Helper to check if validation exists in array format."""
    if validations is None:
        return False
    for v in validations:
        if v.get("validation") == key:
            if value is None:
                return True
            return v.get("value") == value
    return False


# =============================================================================
# Type Mapping Tests
# =============================================================================

class TestTypeMappings:
    """Test type mapping dictionaries."""

    def test_all_kodosumi_types_mapped(self):
        """All Kodosumi input types should map to MIP-003 types."""
        kodosumi_types = [
            "text", "textarea", "password", "number", "date", "time",
            "datetime-local", "boolean", "select", "file",
            # New MIP-003 types
            "email", "url", "tel", "search", "month", "week",
            "color", "range", "hidden", "radio", "none",
        ]
        for ktype in kodosumi_types:
            assert ktype in TYPE_MAP_TO_MIP003, f"Missing mapping for Kodosumi type: {ktype}"

    def test_all_mip003_types_mapped(self):
        """All MIP-003 types should map back to Kodosumi types."""
        mip003_types = [
            "text", "textarea", "password", "search", "hidden", "none",
            "number", "range",
            "option", "radio", "checkbox", "boolean",
            "date", "datetime-local", "time", "month", "week",
            "email", "url", "tel",
            "color", "file",
        ]
        for mtype in mip003_types:
            assert mtype in TYPE_MAP_FROM_MIP003, f"Missing mapping for MIP-003 type: {mtype}"

    def test_select_maps_to_option(self):
        """Kodosumi 'select' should map to MIP-003 'option'."""
        assert TYPE_MAP_TO_MIP003["select"] == "option"

    def test_option_maps_to_select(self):
        """MIP-003 'option' should map back to Kodosumi 'select'."""
        assert TYPE_MAP_FROM_MIP003["option"] == "select"


# =============================================================================
# Kodosumi → MIP-003 Conversion Tests
# =============================================================================

class TestKodosumiToMIP003:
    """Test conversion from Kodosumi elements to MIP-003 InputFields."""

    def test_text_input_basic(self):
        """Basic text input conversion."""
        element = InputText(name="username", label="Username").to_dict()
        field = convert_element_to_mip003(element)

        assert field is not None
        assert field.id == "username"
        assert field.type == "text"
        assert field.name == "Username"
        assert has_validation(field.validations, "optional", "true")  # Not required

    def test_text_input_required(self):
        """Required text input should not have optional=true."""
        element = InputText(name="email", label="Email", required=True).to_dict()
        field = convert_element_to_mip003(element)

        assert field is not None
        # Required field: optional should not be in validations
        assert not has_validation(field.validations, "optional", "true")

    def test_text_input_with_validations(self):
        """Text input with length constraints."""
        element = InputPassword(
            name="password",
            label="Password",
            required=True,
            min_length=8,
            max_length=100,
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "password"
        assert get_validation(field.validations, "min") == "8"
        assert get_validation(field.validations, "max") == "100"

    def test_number_input(self):
        """Number input conversion."""
        element = InputNumber(
            name="age",
            label="Age",
            min_value=0,
            max_value=150,
            required=True,
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "number"
        assert get_validation(field.validations, "min") == "0"
        assert get_validation(field.validations, "max") == "150"

    def test_email_input(self):
        """Email input should have format='email'."""
        element = InputEmail(name="email", label="Email", required=True).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "email"
        assert get_validation(field.validations, "format") == "email"

    def test_url_input(self):
        """URL input should have format='url'."""
        element = InputUrl(name="website", label="Website").to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "url"
        assert get_validation(field.validations, "format") == "url"

    def test_tel_input(self):
        """Telephone input should have format='tel-pattern'."""
        element = InputTel(name="phone", label="Phone").to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "tel"
        assert get_validation(field.validations, "format") == "tel-pattern"

    def test_date_input(self):
        """Date input with constraints."""
        element = InputDate(
            name="birthday",
            label="Birthday",
            min_date="1900-01-01",
            max_date="2024-12-31",
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "date"
        assert get_validation(field.validations, "min") == "1900-01-01"
        assert get_validation(field.validations, "max") == "2024-12-31"

    def test_time_input(self):
        """Time input conversion."""
        element = InputTime(
            name="appointment",
            label="Appointment Time",
            min_time="09:00",
            max_time="17:00",
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "time"
        assert get_validation(field.validations, "min") == "09:00"
        assert get_validation(field.validations, "max") == "17:00"

    def test_datetime_input(self):
        """Datetime input conversion."""
        element = InputDateTime(
            name="meeting",
            label="Meeting",
            min_datetime="2024-01-01T09:00",
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "datetime-local"
        assert get_validation(field.validations, "min") == "2024-01-01T09:00"

    def test_month_input(self):
        """Month input conversion."""
        element = InputMonth(
            name="expiry",
            label="Expiry Month",
            min_month="2024-01",
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "month"
        assert get_validation(field.validations, "min") == "2024-01"

    def test_week_input(self):
        """Week input conversion."""
        element = InputWeek(name="week", label="Select Week").to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "week"

    def test_color_input(self):
        """Color input conversion."""
        element = InputColor(name="color", label="Favorite Color", value="#ff0000").to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "color"
        assert field.data["default"] == "#ff0000"

    def test_range_input(self):
        """Range input - min/max/step should be in data, not validations."""
        element = InputRange(
            name="volume",
            label="Volume",
            min_value=0,
            max_value=100,
            step=5,
            value=50,
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "range"
        assert field.data["min"] == 0
        assert field.data["max"] == 100
        assert field.data["step"] == 5
        assert field.data["default"] == 50

    def test_hidden_input(self):
        """Hidden input - value should be in data."""
        element = InputHidden(name="csrf_token", value="abc123").to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "hidden"
        assert field.data["value"] == "abc123"

    def test_textarea_input(self):
        """Textarea input conversion."""
        element = InputArea(
            name="description",
            label="Description",
            placeholder="Enter description",
            max_length=1000,
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "textarea"
        assert field.data["placeholder"] == "Enter description"
        assert get_validation(field.validations, "max") == "1000"

    def test_checkbox_input(self):
        """Checkbox (boolean) input conversion - no default in data."""
        element = Checkbox(
            name="agree",
            label="Terms",
            option="I agree to the terms",
            value=True,
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "boolean"
        assert field.data["description"] == "I agree to the terms"
        # Boolean type should NOT have default in data
        assert "default" not in field.data

    def test_select_input(self):
        """Select input should convert to option with values array."""
        element = Select(
            name="country",
            label="Country",
            option=[
                InputOption(name="us", label="USA"),
                InputOption(name="uk", label="UK"),
                InputOption(name="de", label="Germany"),
            ],
            value="us",
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "option"
        assert field.data["values"] == ["us", "uk", "de"]

    def test_radio_input(self):
        """Radio input should have values array."""
        element = InputRadio(
            name="gender",
            label="Gender",
            option=[
                InputOption(name="m", label="Male"),
                InputOption(name="f", label="Female"),
                InputOption(name="o", label="Other"),
            ],
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "radio"
        assert field.data["values"] == ["m", "f", "o"]

    def test_file_input(self):
        """File input should have outputFormat."""
        element = InputFiles(
            name="documents",
            label="Upload Documents",
            multiple=True,
        ).to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "file"
        assert field.data["multiple"] == True
        assert field.data["outputFormat"] == "url"

    def test_display_info(self):
        """DisplayInfo (none type) conversion."""
        element = DisplayInfo(text="This is information", label="Info").to_dict()
        field = convert_element_to_mip003(element)

        assert field.type == "none"
        assert field.name == "Info"

    def test_skip_non_input_elements(self):
        """Non-input elements should return None."""
        submit = Submit("Submit").to_dict()
        field = convert_element_to_mip003(submit)
        assert field is None


# =============================================================================
# MIP-003 → Kodosumi Conversion Tests
# =============================================================================

class TestMIP003ToKodosumi:
    """Test conversion from MIP-003 InputFields to Kodosumi elements."""

    def test_text_field_basic(self):
        """Basic text field conversion."""
        mip_field = {
            "id": "username",
            "type": "text",
            "name": "Username",
            "data": {"placeholder": "Enter username"},
            "validations": {"optional": True},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "text"
        assert element["name"] == "username"
        assert element["label"] == "Username"
        assert element["placeholder"] == "Enter username"
        assert element["required"] == False

    def test_text_field_required(self):
        """Required field (optional not set or false)."""
        mip_field = {
            "id": "email",
            "type": "email",
            "name": "Email",
            "validations": {},  # No optional means required
        }
        element = convert_mip003_to_element(mip_field)

        assert element["required"] == True

    def test_text_field_with_validations(self):
        """Text field with min/max validations."""
        mip_field = {
            "id": "password",
            "type": "password",
            "name": "Password",
            "validations": {"min": "8", "max": "100"},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["min_length"] == 8
        assert element["max_length"] == 100

    def test_number_field(self):
        """Number field with min/max."""
        mip_field = {
            "id": "age",
            "type": "number",
            "name": "Age",
            "validations": {"min": "0", "max": "150"},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "number"
        assert element["min_value"] == 0.0
        assert element["max_value"] == 150.0

    def test_option_to_select(self):
        """MIP-003 option should convert to Kodosumi select."""
        mip_field = {
            "id": "country",
            "type": "option",
            "name": "Country",
            "data": {
                "values": ["us", "uk", "de"],
                "default": "us",
            },
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "select"
        assert len(element["option"]) == 3
        assert element["option"][0]["name"] == "us"
        assert element["option"][0]["value"] == True  # Default selected

    def test_checkbox_to_boolean(self):
        """MIP-003 checkbox should convert to Kodosumi boolean."""
        mip_field = {
            "id": "agree",
            "type": "checkbox",
            "name": "Agreement",
            "data": {"description": "I agree"},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "boolean"
        assert element["option"] == "I agree"

    def test_date_field(self):
        """Date field with min/max."""
        mip_field = {
            "id": "birthday",
            "type": "date",
            "name": "Birthday",
            "validations": {"min": "1900-01-01", "max": "2024-12-31"},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "date"
        assert element["min_date"] == "1900-01-01"
        assert element["max_date"] == "2024-12-31"

    def test_range_field(self):
        """Range field - min/max/step from data."""
        mip_field = {
            "id": "volume",
            "type": "range",
            "name": "Volume",
            "data": {"min": 0, "max": 100, "step": 5, "default": 50},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "range"
        assert element["min_value"] == 0
        assert element["max_value"] == 100
        assert element["step"] == 5
        assert element["value"] == 50

    def test_hidden_field(self):
        """Hidden field - value from data."""
        mip_field = {
            "id": "token",
            "type": "hidden",
            "name": None,
            "data": {"value": "secret123"},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "hidden"
        assert element["value"] == "secret123"

    def test_file_field(self):
        """File field with multiple."""
        mip_field = {
            "id": "docs",
            "type": "file",
            "name": "Documents",
            "data": {"multiple": True, "outputFormat": "url"},
        }
        element = convert_mip003_to_element(mip_field)

        assert element["type"] == "file"
        assert element["multiple"] == True


# =============================================================================
# Full Model Conversion Tests
# =============================================================================

class TestModelConversion:
    """Test full model conversion."""

    def test_convert_model_to_schema(self):
        """Convert Kodosumi Model to MIP-003 InputSchemaResponse."""
        model = Model(
            InputText(name="name", label="Name", required=True),
            InputEmail(name="email", label="Email", required=True),
            InputNumber(name="age", label="Age", min_value=0, max_value=150),
            Submit("Submit"),
        )

        schema = convert_model_to_schema(model.get_model())

        assert schema.input_data is not None
        assert len(schema.input_data) == 3  # Submit is skipped
        assert schema.input_data[0].type == "text"
        assert schema.input_data[1].type == "email"
        assert schema.input_data[2].type == "number"

    def test_convert_schema_to_elements(self):
        """Convert MIP-003 InputSchemaResponse back to Kodosumi elements."""
        schema = InputSchemaResponse(
            input_data=[
                InputField(id="name", type="text", name="Name"),
                InputField(id="email", type="email", name="Email"),
            ]
        )

        elements = convert_schema_to_elements(schema)

        assert len(elements) == 2
        assert elements[0]["type"] == "text"
        assert elements[0]["name"] == "name"
        assert elements[1]["type"] == "email"


# =============================================================================
# Round-Trip Tests
# =============================================================================

class TestRoundTrip:
    """Test round-trip conversion (Kodosumi → MIP-003 → Kodosumi)."""

    def test_text_round_trip(self):
        """Text input should survive round-trip."""
        original = InputText(
            name="username",
            label="Username",
            placeholder="Enter username",
            required=True,
        ).to_dict()

        # Kodosumi → MIP-003
        mip_field = convert_element_to_mip003(original)
        # MIP-003 → Kodosumi
        result = convert_mip003_to_element(mip_field.model_dump())

        assert result["type"] == "text"
        assert result["name"] == "username"
        assert result["label"] == "Username"
        assert result["placeholder"] == "Enter username"
        assert result["required"] == True

    def test_select_round_trip(self):
        """Select input should survive round-trip."""
        original = Select(
            name="country",
            label="Country",
            option=[
                InputOption(name="us", label="USA"),
                InputOption(name="uk", label="UK"),
            ],
        ).to_dict()

        mip_field = convert_element_to_mip003(original)
        result = convert_mip003_to_element(mip_field.model_dump())

        assert result["type"] == "select"
        assert result["name"] == "country"
        assert len(result["option"]) == 2

    def test_range_round_trip(self):
        """Range input should survive round-trip."""
        original = InputRange(
            name="volume",
            label="Volume",
            min_value=0,
            max_value=100,
            step=5,
            value=50,
        ).to_dict()

        mip_field = convert_element_to_mip003(original)
        result = convert_mip003_to_element(mip_field.model_dump())

        assert result["type"] == "range"
        assert result["min_value"] == 0
        assert result["max_value"] == 100
        assert result["step"] == 5
        assert result["value"] == 50


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_schema(self):
        """Empty schema should work."""
        schema = create_empty_schema()
        assert schema.input_data is None

    def test_unknown_type_passthrough(self):
        """Unknown types should pass through."""
        mip_field = {
            "id": "custom",
            "type": "custom_type",
            "name": "Custom",
        }
        element = convert_mip003_to_element(mip_field)
        assert element["type"] == "custom_type"

    def test_none_validations(self):
        """None validations should be handled."""
        mip_field = {
            "id": "test",
            "type": "text",
            "name": "Test",
            "validations": None,
        }
        element = convert_mip003_to_element(mip_field)
        assert element["required"] == True  # Default when no validations

    def test_none_data(self):
        """None data should be handled."""
        mip_field = {
            "id": "test",
            "type": "text",
            "name": "Test",
            "data": None,
        }
        element = convert_mip003_to_element(mip_field)
        assert element is not None

    def test_model_with_only_display_elements(self):
        """Model with only display elements should return empty schema."""
        model = Model(
            Submit("Submit"),
        )
        schema = convert_model_to_schema(model.get_model())
        assert schema.input_data is None
