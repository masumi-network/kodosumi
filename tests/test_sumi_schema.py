"""
Tests for Sumi Protocol Input Schema Endpoint (Phase 4).

Tests MIP-003 input schema conversion and endpoint:
- Kodosumi form element to MIP-003 InputField conversion
- GET /sumi/{expose-name}/{meta-name}/input_schema endpoint

Note: These tests have been updated to work with the MIP-003 compliant schema.
For comprehensive MIP-003 tests, see test_sumi_schema_mip003.py.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from kodosumi.service.sumi.schema import (
    convert_element_to_mip003 as convert_element_to_input_field,
    convert_model_to_schema,
    create_empty_schema,
    _convert_validations_to_mip003 as _convert_validations,
    _convert_data_to_mip003 as _convert_data,
    TYPE_MAP_TO_MIP003 as TYPE_MAP,
)
from kodosumi.service.sumi.models import InputField, InputSchemaResponse


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


class TestTypeMapping:
    """Test Kodosumi to MIP-003 type mapping."""

    def test_text_to_text(self):
        """Text should map to MIP-003 text type."""
        assert TYPE_MAP["text"] == "text"

    def test_password_to_password(self):
        """Password should map to MIP-003 password type."""
        assert TYPE_MAP["password"] == "password"

    def test_textarea_to_textarea(self):
        """Textarea should map to MIP-003 textarea type."""
        assert TYPE_MAP["textarea"] == "textarea"

    def test_number_to_number(self):
        assert TYPE_MAP["number"] == "number"

    def test_boolean_to_boolean(self):
        assert TYPE_MAP["boolean"] == "boolean"

    def test_select_to_option(self):
        assert TYPE_MAP["select"] == "option"

    def test_file_to_file(self):
        """File should map to MIP-003 file type."""
        assert TYPE_MAP["file"] == "file"


# =============================================================================
# Validation Conversion Tests
# =============================================================================


class TestConvertValidations:
    """Test _convert_validations helper."""

    def test_empty_element(self):
        """Empty element with no required field should have optional=true."""
        result = _convert_validations({})
        # MIP-003: no required means optional=true (array format)
        assert result is not None
        assert has_validation(result, "optional", "true")

    def test_required_true(self):
        """Required=true should NOT have optional in validations."""
        result = _convert_validations({"required": True})
        # MIP-003: required field should not have optional=true
        assert not has_validation(result, "optional", "true")

    def test_required_false(self):
        """Required=false should have optional=true."""
        result = _convert_validations({"required": False})
        assert has_validation(result, "optional", "true")

    def test_min_max_length_text(self):
        """Text field min/max length should convert to min/max."""
        result = _convert_validations({
            "type": "text",
            "min_length": 5,
            "max_length": 100
        })
        # MIP-003 uses min/max as strings in array format
        assert get_validation(result, "min") == "5"
        assert get_validation(result, "max") == "100"

    def test_min_max_value_number(self):
        """Number field min/max value should convert to min/max."""
        result = _convert_validations({
            "type": "number",
            "min_value": 0,
            "max_value": 1000
        })
        assert get_validation(result, "min") == "0"
        assert get_validation(result, "max") == "1000"


# =============================================================================
# Data Conversion Tests
# =============================================================================


class TestConvertData:
    """Test _convert_data helper."""

    def test_empty_element(self):
        result = _convert_data({})
        assert result is None

    def test_placeholder(self):
        result = _convert_data({"placeholder": "Enter value..."})
        assert result["placeholder"] == "Enter value..."

    def test_default_value(self):
        result = _convert_data({"value": "default"})
        assert result["default"] == "default"

    def test_select_options(self):
        """Select options should convert to values array."""
        element = {
            "type": "select",
            "option": [
                {"name": "opt1", "label": "Option 1", "value": True},
                {"name": "opt2", "label": "Option 2", "value": False},
            ]
        }
        result = _convert_data(element)
        # MIP-003 uses flat values array
        assert "values" in result
        assert result["values"] == ["opt1", "opt2"]

    def test_file_output_format(self):
        """File type should have outputFormat."""
        result = _convert_data({"type": "file"})
        assert result["outputFormat"] == "url"

    def test_boolean_option_text(self):
        """Boolean option text should become description."""
        result = _convert_data({"type": "boolean", "option": "Yes"})
        assert result["description"] == "Yes"


# =============================================================================
# Element Conversion Tests
# =============================================================================


class TestConvertElementToInputField:
    """Test convert_element_to_input_field function."""

    def test_text_input(self):
        element = {
            "type": "text",
            "name": "query",
            "label": "Search Query",
            "required": True,
            "placeholder": "Enter search term...",
        }
        result = convert_element_to_input_field(element)

        assert result is not None
        assert result.id == "query"
        assert result.type == "text"  # MIP-003 type
        assert result.name == "Search Query"
        assert result.data["placeholder"] == "Enter search term..."
        # Required field should not have optional=true
        assert not has_validation(result.validations, "optional", "true")

    def test_number_input(self):
        element = {
            "type": "number",
            "name": "count",
            "label": "Count",
            "min_value": 1,
            "max_value": 100,
            "step": 1,
        }
        result = convert_element_to_input_field(element)

        assert result is not None
        assert result.id == "count"
        assert result.type == "number"
        assert get_validation(result.validations, "min") == "1"
        assert get_validation(result.validations, "max") == "100"

    def test_checkbox(self):
        element = {
            "type": "boolean",
            "name": "agree",
            "label": "I agree to terms",
            "option": "Yes",
        }
        result = convert_element_to_input_field(element)

        assert result is not None
        assert result.id == "agree"
        assert result.type == "boolean"
        assert result.data["description"] == "Yes"

    def test_select(self):
        element = {
            "type": "select",
            "name": "language",
            "label": "Language",
            "option": [
                {"name": "en", "label": "English", "value": True},
                {"name": "de", "label": "German", "value": False},
            ],
        }
        result = convert_element_to_input_field(element)

        assert result is not None
        assert result.id == "language"
        assert result.type == "option"  # MIP-003 uses 'option' not 'select'
        assert result.data["values"] == ["en", "de"]

    def test_textarea(self):
        element = {
            "type": "textarea",
            "name": "description",
            "label": "Description",
            "rows": 5,
            "cols": 50,
            "max_length": 1000,
        }
        result = convert_element_to_input_field(element)

        assert result is not None
        assert result.id == "description"
        assert result.type == "textarea"  # MIP-003 has native textarea type

    def test_file_input(self):
        element = {
            "type": "file",
            "name": "documents",
            "label": "Upload Documents",
            "multiple": True,
        }
        result = convert_element_to_input_field(element)

        assert result is not None
        assert result.id == "documents"
        assert result.type == "file"  # MIP-003 has native file type
        assert result.data["multiple"] == True
        assert result.data["outputFormat"] == "url"

    def test_skip_non_input_elements(self):
        assert convert_element_to_input_field({"type": "html"}) is None
        assert convert_element_to_input_field({"type": "markdown"}) is None
        assert convert_element_to_input_field({"type": "submit"}) is None
        assert convert_element_to_input_field({"type": "cancel"}) is None
        assert convert_element_to_input_field({"type": "action"}) is None
        assert convert_element_to_input_field({"type": "errors"}) is None

    def test_skip_elements_without_name(self):
        result = convert_element_to_input_field({
            "type": "text",
            "label": "No name field",
        })
        assert result is None


# =============================================================================
# Model Conversion Tests
# =============================================================================


class TestConvertModelToSchema:
    """Test convert_model_to_schema function."""

    def test_empty_elements(self):
        result = convert_model_to_schema([])
        assert result.input_data is None

    def test_single_field(self):
        elements = [
            {"type": "text", "name": "query", "label": "Query"},
        ]
        result = convert_model_to_schema(elements)

        assert result.input_data is not None
        assert len(result.input_data) == 1
        assert result.input_data[0].id == "query"

    def test_multiple_fields(self):
        elements = [
            {"type": "text", "name": "name", "label": "Name"},
            {"type": "number", "name": "age", "label": "Age"},
            {"type": "boolean", "name": "subscribe", "label": "Subscribe"},
        ]
        result = convert_model_to_schema(elements)

        assert result.input_data is not None
        assert len(result.input_data) == 3
        assert result.input_data[0].id == "name"
        assert result.input_data[1].id == "age"
        assert result.input_data[2].id == "subscribe"

    def test_filters_non_input_elements(self):
        elements = [
            {"type": "html", "text": "<h1>Form</h1>"},
            {"type": "text", "name": "field1", "label": "Field 1"},
            {"type": "markdown", "text": "Some text"},
            {"type": "number", "name": "field2", "label": "Field 2"},
            {"type": "submit", "text": "Submit"},
        ]
        result = convert_model_to_schema(elements)

        assert result.input_data is not None
        assert len(result.input_data) == 2
        assert result.input_data[0].id == "field1"
        assert result.input_data[1].id == "field2"


class TestCreateEmptySchema:
    """Test create_empty_schema function."""

    def test_creates_empty_schema(self):
        result = create_empty_schema()
        assert isinstance(result, InputSchemaResponse)
        assert result.input_data is None


# =============================================================================
# InputField Model Tests
# =============================================================================


class TestInputFieldModel:
    """Test InputField model creation."""

    def test_minimal(self):
        field = InputField(id="test", type="text")
        assert field.id == "test"
        assert field.type == "text"
        assert field.name is None
        assert field.data is None
        assert field.validations is None

    def test_full(self):
        field = InputField(
            id="query",
            type="text",
            name="Search Query",
            data={"placeholder": "Enter term", "description": "Search term"},
            validations=[
                {"validation": "optional", "value": "true"},
                {"validation": "min", "value": "1"},
            ],
        )
        assert field.id == "query"
        assert field.type == "text"
        assert field.name == "Search Query"
        assert field.data["placeholder"] == "Enter term"
        assert has_validation(field.validations, "optional", "true")


# =============================================================================
# InputSchemaResponse Model Tests
# =============================================================================


class TestInputSchemaResponseModel:
    """Test InputSchemaResponse model."""

    def test_empty(self):
        schema = InputSchemaResponse()
        assert schema.input_data is None

    def test_with_input_data(self):
        fields = [
            InputField(id="f1", type="text"),
            InputField(id="f2", type="number"),
        ]
        schema = InputSchemaResponse(input_data=fields)
        assert schema.input_data is not None
        assert len(schema.input_data) == 2

    def test_serialization(self):
        field = InputField(
            id="query",
            type="text",
            name="Query",
            data={"placeholder": "Enter..."},
            validations=[{"validation": "optional", "value": "true"}],
        )
        schema = InputSchemaResponse(input_data=[field])
        data = schema.model_dump()

        assert data["input_data"] is not None
        assert len(data["input_data"]) == 1
        assert data["input_data"][0]["id"] == "query"
