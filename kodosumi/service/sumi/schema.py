"""
MIP-003 Input Schema Conversion.

Provides bidirectional conversion between Kodosumi form elements and MIP-003 InputField format.
"""

from typing import Any, Dict, List, Optional, Union

from kodosumi.service.sumi.models import InputField, InputSchemaResponse


# =============================================================================
# Type Mappings (Bidirectional)
# =============================================================================

# Kodosumi type → MIP-003 type (1:1 where possible)
TYPE_MAP_TO_MIP003 = {
    # Text inputs
    "text": "text",
    "textarea": "textarea",
    "password": "password",
    "search": "search",
    # Numeric
    "number": "number",
    "range": "range",
    # Selection
    "select": "option",      # Kodosumi select → MIP-003 option
    "radio": "radio",
    "boolean": "boolean",    # Kodosumi Checkbox → MIP-003 boolean
    # Date/Time
    "date": "date",
    "time": "time",
    "datetime-local": "datetime-local",
    "month": "month",
    "week": "week",
    # Web-based
    "email": "email",
    "url": "url",
    "tel": "tel",
    # Media
    "color": "color",
    "file": "file",
    "file_url": "file",      # Forward-only: rendered as url input in Kodosumi,
                             # projected as file upload in MIP-003.
    # Special
    "hidden": "hidden",
    "none": "none",
}

# MIP-003 type → Kodosumi type
TYPE_MAP_FROM_MIP003 = {
    # Text inputs
    "text": "text",
    "textarea": "textarea",
    "password": "password",
    "search": "search",
    # Numeric
    "number": "number",
    "range": "range",
    # Selection
    "option": "select",      # MIP-003 option → Kodosumi select
    "radio": "radio",
    "boolean": "boolean",
    "checkbox": "boolean",   # MIP-003 checkbox → Kodosumi boolean
    # Date/Time
    "date": "date",
    "time": "time",
    "datetime-local": "datetime-local",
    "month": "month",
    "week": "week",
    # Web-based
    "email": "email",
    "url": "url",
    "tel": "tel",
    # Media
    "color": "color",
    # MIP-003 'file' always maps back to InputFiles. Kodosumi's InputFileUrl
    # (file_url type) is a forward-only convenience and cannot be reconstructed
    # from a MIP-003 schema alone.
    "file": "file",
    # Special
    "hidden": "hidden",
    "none": "none",
}

# Non-input element types (skip during conversion)
NON_INPUT_TYPES = {
    "html", "submit", "cancel", "action", "errors", "break", "hr"
}

# Display-only types converted to MIP-003 type: "none"
DISPLAY_TEXT_TYPES = {"markdown"}

# Types that use min/max for length validation
LENGTH_VALIDATION_TYPES = {
    "text", "textarea", "password", "email", "url", "tel", "search"
}

# Types that use min/max for numeric value validation
NUMERIC_VALIDATION_TYPES = {"number", "range"}

# Types that use min/max for date/time validation
DATE_VALIDATION_TYPES = {"date", "time", "datetime-local", "month", "week"}


# =============================================================================
# Kodosumi → MIP-003 Conversion
# =============================================================================

def _convert_validations_to_mip003(element: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """
    Convert Kodosumi element validations to MIP-003 format.

    Key differences:
    - MIP-003 uses 'optional' (default false), Kodosumi uses 'required'
    - MIP-003 uses unified 'min'/'max', Kodosumi has type-specific fields
    - MIP-003 'format' only accepts: email, url, nonempty, integer, tel-pattern
    - MIP-003 validations are array format: [{"validation": "optional", "value": "true"}]
    """
    validations = []
    elem_type = element.get("type", "")

    # Required → optional (inverted logic)
    # MIP-003: all fields required by default, set optional=true if not required
    if not element.get("required", False):
        validations.append({"validation": "optional", "value": "true"})

    # Unified min/max based on element type
    min_val = None
    max_val = None

    # Length-based validations (text types)
    if elem_type in LENGTH_VALIDATION_TYPES:
        min_val = element.get("min_length")
        max_val = element.get("max_length") or element.get("size")

    # Numeric validations
    elif elem_type in NUMERIC_VALIDATION_TYPES:
        min_val = element.get("min_value")
        max_val = element.get("max_value")

    # Date/time validations (type-specific attribute names in Kodosumi)
    elif elem_type == "date":
        min_val = element.get("min_date")
        max_val = element.get("max_date")
    elif elem_type == "time":
        min_val = element.get("min_time")
        max_val = element.get("max_time")
    elif elem_type == "datetime-local":
        min_val = element.get("min_datetime")
        max_val = element.get("max_datetime")
    elif elem_type == "month":
        min_val = element.get("min_month")
        max_val = element.get("max_month")
    elif elem_type == "week":
        min_val = element.get("min_week")
        max_val = element.get("max_week")

    # Select (dropdown) → MIP-003 option: enforce single-select
    # MIP-003 option can be multi-select, but Kodosumi doesn't support it
    # radio type has implicit min: 1, max: 1 per MIP-003 spec
    elif elem_type == "select":
        min_val = 1
        max_val = 1

    if min_val is not None:
        validations.append({"validation": "min", "value": str(min_val)})
    if max_val is not None:
        validations.append({"validation": "max", "value": str(max_val)})

    # Format mapping - MIP-003 only supports specific format values
    # Map element type to MIP-003 format where applicable
    if elem_type == "email":
        validations.append({"validation": "format", "value": "email"})
    elif elem_type == "url":
        validations.append({"validation": "format", "value": "url"})
    elif elem_type == "tel":
        validations.append({"validation": "format", "value": "tel-pattern"})
    elif element.get("pattern") and elem_type != "file_url":
        # Try to map common patterns to MIP-003 format values
        # (skipped for file_url since its MIP-003 projection is a file upload)
        pattern = element.get("pattern")
        if pattern in (r"^\S+$", r"\S+"):
            validations.append({"validation": "format", "value": "nonempty"})
        elif pattern in (r"^\d+$", r"\d+"):
            validations.append({"validation": "format", "value": "integer"})
        # Custom patterns cannot be mapped to MIP-003 format (limitation)

    return validations if validations else None


def _convert_data_to_mip003(element: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert Kodosumi element data to MIP-003 format.

    MIP-003 data fields:
    - placeholder: hint text
    - description: field description
    - default: default value
    - values: array of options (for option/radio types)
    - For file: accept, maxSize, multiple, outputFormat
    - For range: min, max, step in data (not validations)
    - For hidden: value (required)
    """
    data = {}
    elem_type = element.get("type", "")

    # Common fields
    if element.get("placeholder"):
        data["placeholder"] = element["placeholder"]

    # Default value (skip for boolean type - only description is used)
    if element.get("value") is not None and elem_type != "boolean":
        data["default"] = element["value"]

    # Select/Radio → flat values array (MIP-003 Attachment 01 compliant)
    # Note: labels are lost in MIP-003 serialization — the spec only
    # defines "values" as a flat string array, not {value, label} objects.
    if elem_type in ("select", "radio") and element.get("option"):
        values = []
        for opt in element["option"]:
            if isinstance(opt, dict):
                opt_name = opt.get("name")
                if opt_name:
                    values.append(opt_name)
        if values:
            data["values"] = values

    # Checkbox (boolean) - option text as description
    if elem_type == "boolean" and element.get("option"):
        data["description"] = element["option"]

    # Range type - min/max/step go in data, not validations
    if elem_type == "range":
        if element.get("min_value") is not None:
            data["min"] = element["min_value"]
        if element.get("max_value") is not None:
            data["max"] = element["max_value"]
        if element.get("step") is not None:
            data["step"] = element["step"]

    # File type specific fields (both file and file_url)
    if elem_type in ("file", "file_url"):
        if element.get("multiple"):
            data["multiple"] = True
        # Kodosumi uses URL-based file handling
        data["outputFormat"] = "url"
        # file_url may pass through accept/maxSize hints for Sokosumi clients
        if element.get("accept"):
            data["accept"] = element["accept"]
        if element.get("max_size") is not None:
            data["maxSize"] = element["max_size"]

    # Hidden type requires value in data
    if elem_type == "hidden":
        data["value"] = element.get("value", "")

    return data if data else None


def convert_element_to_mip003(element: Dict[str, Any]) -> Optional[InputField]:
    """
    Convert a Kodosumi form element to MIP-003 InputField.

    Args:
        element: Kodosumi form element dict (from to_dict())

    Returns:
        InputField or None if not a convertible input element
    """
    elem_type = element.get("type", "")

    # Skip non-input elements
    if elem_type in NON_INPUT_TYPES:
        return None

    # Convert display-text elements (markdown) to type: "none"
    if elem_type in DISPLAY_TEXT_TYPES:
        text = element.get("text", "")
        if not text or not text.strip():
            return None
        return InputField(
            id="info",
            type="none",
            name="Information",  # MIP-003 requires name
            data={"description": text.strip()},
            validations=None,
        )

    # Must have a name to be an input (except 'none' type)
    name = element.get("name")
    if not name and elem_type != "none":
        return None

    # Map to MIP-003 type
    mip_type = TYPE_MAP_TO_MIP003.get(elem_type)
    if not mip_type:
        # Unknown type - try to pass through
        mip_type = elem_type

    # For 'none' type, use text as id
    field_id = name if name else "info"

    return InputField(
        id=field_id,
        type=mip_type,
        name=element.get("label") or element.get("text"),  # Display label
        data=_convert_data_to_mip003(element),
        validations=_convert_validations_to_mip003(element),
    )


def convert_model_to_schema(elements: List[Dict[str, Any]]) -> InputSchemaResponse:
    """
    Convert Kodosumi Model elements to MIP-003 InputSchemaResponse.

    Args:
        elements: List of form element dicts (from Model.get_model())

    Returns:
        MIP-003 InputSchemaResponse
    """
    input_fields = []

    for elem in elements:
        field = convert_element_to_mip003(elem)
        if field:
            input_fields.append(field)

    # Ensure unique IDs — append counter to duplicates
    seen: dict = {}
    for field in input_fields:
        if field.id in seen:
            seen[field.id] += 1
            field.id = f"{field.id}_{seen[field.id]}"
        else:
            seen[field.id] = 0

    return InputSchemaResponse(
        input_data=input_fields if input_fields else None,
    )


# =============================================================================
# MIP-003 → Kodosumi Conversion
# =============================================================================

def _convert_validations_from_mip003(
    mip_field: Dict[str, Any],
    kodo_type: str
) -> Dict[str, Any]:
    """
    Convert MIP-003 validations to Kodosumi element attributes.

    Args:
        mip_field: MIP-003 InputField dict
        kodo_type: Target Kodosumi type

    Returns:
        Dict of Kodosumi validation attributes
    """
    result = {}
    validations_raw = mip_field.get("validations") or []

    # Parse array format: [{"validation": "optional", "value": "true"}]
    # Also support legacy dict format for backwards compatibility
    if isinstance(validations_raw, dict):
        # Legacy dict format: {"optional": True, "min": "8"}
        validations = validations_raw
        is_optional = validations.get("optional", False)
        min_val = validations.get("min")
        max_val = validations.get("max")
    else:
        # Array format: [{"validation": "optional", "value": "true"}]
        validations = {}
        for v in validations_raw:
            if isinstance(v, dict) and "validation" in v:
                validations[v["validation"]] = v.get("value")
        is_optional = validations.get("optional") == "true"
        min_val = validations.get("min")
        max_val = validations.get("max")

    # Optional → required (inverted)
    # MIP-003: optional=true means not required
    result["required"] = not is_optional

    # Map min/max to type-specific Kodosumi fields
    if kodo_type in LENGTH_VALIDATION_TYPES:
        if min_val is not None:
            result["min_length"] = int(min_val)
        if max_val is not None:
            result["max_length"] = int(max_val)

    elif kodo_type in NUMERIC_VALIDATION_TYPES:
        if min_val is not None:
            result["min_value"] = float(min_val)
        if max_val is not None:
            result["max_value"] = float(max_val)

    elif kodo_type == "date":
        if min_val is not None:
            result["min_date"] = min_val
        if max_val is not None:
            result["max_date"] = max_val

    elif kodo_type == "time":
        if min_val is not None:
            result["min_time"] = min_val
        if max_val is not None:
            result["max_time"] = max_val

    elif kodo_type == "datetime-local":
        if min_val is not None:
            result["min_datetime"] = min_val
        if max_val is not None:
            result["max_datetime"] = max_val

    elif kodo_type == "month":
        if min_val is not None:
            result["min_month"] = min_val
        if max_val is not None:
            result["max_month"] = max_val

    elif kodo_type == "week":
        if min_val is not None:
            result["min_week"] = min_val
        if max_val is not None:
            result["max_week"] = max_val

    return result


def _convert_data_from_mip003(
    mip_field: Dict[str, Any],
    kodo_type: str
) -> Dict[str, Any]:
    """
    Convert MIP-003 data to Kodosumi element attributes.

    Args:
        mip_field: MIP-003 InputField dict
        kodo_type: Target Kodosumi type

    Returns:
        Dict of Kodosumi data attributes
    """
    result = {}
    data = mip_field.get("data") or {}

    # Common fields
    if data.get("placeholder"):
        result["placeholder"] = data["placeholder"]

    if data.get("default") is not None:
        result["value"] = data["default"]

    # Option/Radio → convert values/options array to option list
    raw_options = data.get("options") or data.get("values")
    if kodo_type in ("select", "radio") and raw_options:
        options = []
        default_val = data.get("default")
        for item in raw_options:
            if isinstance(item, dict):
                val = item.get("value", "")
                lbl = item.get("label", val)
            else:
                val = item
                lbl = item
            options.append({
                "name": val,
                "label": lbl,
                "value": val == default_val,
            })
        result["option"] = options

    # Boolean/Checkbox - description as option text
    if kodo_type == "boolean" and data.get("description"):
        result["option"] = data["description"]

    # Range type - min/max/step from data
    if kodo_type == "range":
        if data.get("min") is not None:
            result["min_value"] = data["min"]
        if data.get("max") is not None:
            result["max_value"] = data["max"]
        if data.get("step") is not None:
            result["step"] = data["step"]

    # File type
    if kodo_type == "file":
        if data.get("multiple"):
            result["multiple"] = True

    # Hidden type - value from data
    if kodo_type == "hidden" and data.get("value") is not None:
        result["value"] = data["value"]

    return result


def convert_mip003_to_element(mip_field: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert MIP-003 InputField to Kodosumi form element dict.

    Args:
        mip_field: MIP-003 InputField dict with id, type, name, data, validations

    Returns:
        Kodosumi element dict or None if not convertible
    """
    mip_type = mip_field.get("type", "")

    # Map to Kodosumi type
    kodo_type = TYPE_MAP_FROM_MIP003.get(mip_type)
    if not kodo_type:
        # Unknown type - try to pass through
        kodo_type = mip_type

    # Build base element
    element = {
        "type": kodo_type,
        "name": mip_field.get("id"),
        "label": mip_field.get("name"),  # MIP-003 name is display label
    }

    # Add validation attributes
    validation_attrs = _convert_validations_from_mip003(mip_field, kodo_type)
    element.update(validation_attrs)

    # Add data attributes
    data_attrs = _convert_data_from_mip003(mip_field, kodo_type)
    element.update(data_attrs)

    return element


def convert_schema_to_elements(
    schema: Union[InputSchemaResponse, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Convert MIP-003 InputSchemaResponse to Kodosumi form element dicts.

    Args:
        schema: MIP-003 InputSchemaResponse or dict

    Returns:
        List of Kodosumi element dicts suitable for Model.model_validate()
    """
    elements = []

    # Handle both Pydantic model and dict
    if isinstance(schema, InputSchemaResponse):
        input_data = schema.input_data
    else:
        input_data = schema.get("input_data")

    # Process flat input fields
    if input_data:
        for field in input_data:
            if isinstance(field, InputField):
                field_dict = field.model_dump()
            else:
                field_dict = field

            elem = convert_mip003_to_element(field_dict)
            if elem:
                elements.append(elem)

    return elements


# =============================================================================
# Helper Functions
# =============================================================================

def create_empty_schema() -> InputSchemaResponse:
    """Create an empty schema response (no inputs required)."""
    return InputSchemaResponse(
        input_data=None,
    )


# String-like field types that should default to "" when missing
STRING_FIELD_TYPES = {
    "text", "textarea", "password", "search",
    "email", "url", "tel",
    "date", "time", "datetime-local", "month", "week",
    "color", "option", "radio",
}


def convert_mip003_inputs_to_kodosumi(
    input_data: Optional[Dict[str, Any]],
    schema: InputSchemaResponse,
) -> Optional[Dict[str, Any]]:
    """
    Convert MIP-003 input values to Kodosumi format.

    Conversions performed:
    - option/radio: Index arrays [1] → string values "Man"
    - boolean: true → "on" (ServeAPI checkbox format)
    - Missing optional fields: Add with type-appropriate defaults

    Args:
        input_data: Input data dict from MIP-003 start_job request
        schema: InputSchemaResponse containing field definitions

    Returns:
        Converted input_data dict for Kodosumi agents.
    """
    if not schema.input_data:
        return input_data

    # Start with input_data or empty dict
    result = dict(input_data) if input_data else {}

    # Build lookup of fields by id
    fields = {f.id: f for f in schema.input_data}

    # Convert existing values
    for field_id, value in list(result.items()):
        field = fields.get(field_id)
        if not field:
            continue

        # Boolean: true → "on", false stays as-is (ServeAPI defaults to False)
        if field.type == "boolean":
            if value is True:
                result[field_id] = "on"
            # False/None/other → leave unchanged, ServeAPI handles it
            continue

        # Option/Radio: index arrays → string values
        if field.type not in ("option", "radio"):
            continue

        # Get the values array from field data (supports both flat and labeled formats)
        field_data = field.data or {}
        raw = field_data.get("options") or field_data.get("values", [])
        values = []
        for item in raw:
            if isinstance(item, dict):
                values.append(item.get("value", ""))
            else:
                values.append(item)
        if not values:
            continue

        # Handle index arrays: [1] → "Man"
        if isinstance(value, list) and all(isinstance(i, int) for i in value):
            converted = [values[i] for i in value if 0 <= i < len(values)]
            # Single selection returns string, multiple returns list
            if len(converted) == 1:
                result[field_id] = converted[0]
            elif converted:
                result[field_id] = converted
            # If no valid indices, keep original value

    # Add missing optional fields with type-appropriate defaults
    # This ensures Sumi API behaves consistently regardless of what Masumi sends
    for field in schema.input_data:
        if field.id in result:
            continue  # Field already has a value

        # Skip display-only fields (type: "none")
        if field.type == "none":
            continue

        # Add default based on field type
        if field.type in STRING_FIELD_TYPES:
            result[field.id] = ""
        elif field.type == "boolean":
            result[field.id] = False
        elif field.type in ("number", "range"):
            result[field.id] = None  # Numbers can legitimately be None
        elif field.type == "hidden":
            # Hidden fields should have a value in schema, use it
            hidden_value = (field.data or {}).get("value", "")
            result[field.id] = hidden_value
        # file type: leave missing (agent handles file absence)

    return result


# Legacy alias for backwards compatibility
convert_mip003_indices_to_values = convert_mip003_inputs_to_kodosumi


# Legacy alias for backwards compatibility
convert_element_to_input_field = convert_element_to_mip003
