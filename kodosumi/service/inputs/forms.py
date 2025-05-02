from typing import Any, Dict, List, Literal, Optional, Sequence, Union
from pydantic import BaseModel
import re
from kodosumi import dtypes
import markdown  # Hinzufügen des Imports

class Element:
    def __init__(self, type: str, text: str):
        self.type = type
        self.text = text

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError()

    def render(self) -> str:
        raise NotImplementedError()

    def parse_value(self, value: Any) -> Any:
        raise NotImplementedError()

class HTML(Element):
    _id = "html"

    def __init__(self, text: str):
        super().__init__("html", text=text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
        }

    def render(self) -> str:
        return self.text

class Break(HTML):
    def __init__(self):
        super().__init__("<br/>")


class Markdown(Element):
    _id = "markdown"
    def __init__(self, text: str):
        super().__init__("markdown", text=text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
        }

    def render(self) -> str:
        # Konvertiere Markdown zu HTML mit Erweiterungen
        return markdown.markdown(
            self.text, 
            extensions=[
                'extra',
                'codehilite',
                'toc',
                'fenced_code'
            ]
        )

class FormElement(Element):
    def __init__(self, 
                 type: str, 
                 name: Optional[str] = None,
                 label: Optional[str] = None,
                 value: Optional[str] = None,
                 required: bool = False,
                 text: Optional[str] = None,
                 error: Optional[List[str]] = None):
        super().__init__(type, text=text)
        self.name = name
        self.label = label
        self.value = value
        self.required = required
        self.error = error

    def parse_value(self, value: Any) -> Any:
        return value

class InputText(FormElement):
    _id = "text"
    def __init__(
        self,
        name: str,
        label: Optional[str] = None,
        value: Optional[str] = None,
        required: bool = False,
        placeholder: Optional[str] = None,
        error: Optional[List[str]] = None):
        super().__init__("text", name, label, value, required, error=error)
        self.placeholder = placeholder

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="text"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class Checkbox(FormElement):
    _id = "boolean"

    def __init__(
        self,
        name: str,
        option: str,
        label: Optional[str] = None,
        value: bool = False,
        error: Optional[List[str]] = None):
        super().__init__("boolean", name, label, value, required=False, 
                         text=option, error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "option": self.text
        }

    def render(self) -> str:
        ret = []
        if self.label:
            ret.append(f'<legend class="inputs-label">{self.label}</legend>')
        ret.append(f'<div class="field middle-align">')
        ret.append(f'<nav>')
        ret.append(f'<label class="large checkbox">')
        attrs = [f'type="checkbox"', f'name="{self.name}"']
        if self.value is not None:
            if self.value:
                attrs.append(f'checked')
        ret.append(f'<input {" ".join(attrs)}>')
        ret.append(f'<span>{self.text}</span>')
        ret.append(f'</label>')
        ret.append(f'</nav>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)
    
    def parse_value(self, value: Any) -> Any:
        return value == "on"


class ActionElement(FormElement):

    def __init__(self, type: str, text: str, error: Optional[List[str]] = None):
        super().__init__(type, text=text, error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text
        }

class Submit(ActionElement):
    _id = "submit"

    def __init__(self, text: str, error: Optional[List[str]] = None):
        super().__init__("submit", text=text, error=error)

    def render(self) -> str:
        return f'<button type="submit">{self.text}</button>'

class Cancel(ActionElement):
    _id = "cancel"

    def __init__(self, text: str, error: Optional[List[str]] = None):
        super().__init__("cancel", text=text, error=error)

    def render(self) -> str:
        return "\n".join([
            "<button name=\"__cancel__\" value=\"__cancel__\">",
            self.text,
            '</button>'
        ])



class Action(FormElement):
    _id = "action"

    def __init__(self, 
                 type: str, 
                 name: Optional[str] = None,
                 value: Optional[str] = None,
                 required: bool = False,
                 text: Optional[str] = None,
                 error: Optional[List[str]] = None):
        super().__init__("action", name, None, value, required=False, 
                         text=text, error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "value": self.value,
            "text": self.text
        }

    def render(self) -> str:
        ret = []
        if self.name:
            attrs = [f'name="{self.name}"']
        if self.value is not None:
            if self.value:
                attrs.append(f'value="{self.value}"')
        ret.append(f'<button {" ".join(attrs)}>')
        ret.append(self.text)
        ret.append(f'</button>')
        return "\n".join(ret)

    def parse_value(self, value: Any) -> Any:
        return value


class JsonModel(BaseModel):
    elements: List[Dict[str, Any]]


class Model:
    def __init__(self, *children: FormElement):
        self.children = children

    def get_model_json(self) -> List[Dict[str, Any]]:
        elms = [child.to_dict() for child in self.children]
        model = JsonModel(elements=elms)
        return model.model_dump_json()

    def get_model(self) -> List[Dict[str, Any]]:
        elms = [child.to_dict() for child in self.children]
        return elms

    def render(self) -> str:
        html = []
        for elm in self.children:
            html.append(elm.render())
        return "\n".join(html)

    @classmethod
    def model_validate(cls, 
                       elms: List[Dict[str, Any]],
                       errors: Optional[Dict[str, List[str]]] = None,
                       **kwargs) -> "Model":
        scope = {
            HTML, 
            Markdown, 
            InputText, 
            Checkbox, 
            Submit, 
            Cancel, 
            Action
        }
        children = []
        for elm in elms:
            for chk in scope:
                if elm["type"] == chk._id:
                    elm.pop("type")
                    name = elm.get("name", None)
                    if errors and name:
                        error = errors.get(name, None)
                        if error:
                            elm["error"] = error
                    children.append(chk(**elm))
                    break
        return Model(*children, **kwargs)
    
    def set_data(self, data: Dict[str, Any]) -> None:
        for child in self.children:
            if hasattr(child, "name") and child.name in data:
                child.value = child.parse_value(data[child.name])

# class InputOption:
#     # Represents an option in CheckboxGroup, RadioGroup, or Select
#     def __init__(
#         self,
#         name: str,
#         text: str,
#         value: Optional[Union[str, bool]] = None # Value for select/radio (str), checked state for checkbox (bool)
#     ):
#         self.name: str = name
#         self.text: str = text
#         self.value: Optional[Union[str, bool]] = value

#     def to_dict(self) -> Dict[str, Any]:
#         data = {
#             "name": self.name,
#             "text": self.text,
#         }
#         if self.value is not None:
#             data["value"] = self.value
#         return data

# # --- Base Classes for DRYness ---
# class BaseChoice(FormElement):
#     # Base for elements involving multiple choices (radio, select, checkbox group)
#      def __init__(
#         self,
#         type: str,
#         name: str,
#         options: Sequence[InputOption],
#         label: Optional[str] = None,
#         required: bool = False,
#         default: Optional[str] = None, # Name of the default selected option (for radio/select)
#     ):
#         super().__init__(type)
#         self.name = name
#         self.options = options
#         self.label = label
#         self.required = required
#         self.default = default
#         # No longer need valid_option_names for parsing

#      def to_dict(self) -> Dict[str, Any]:
#         data = {
#             "type": self.type,
#             "name": self.name,
#             "value": [opt.to_dict() for opt in self.options],
#             "required": self.required
#         }
#         if self.label: data["label"] = self.label
#         if self.default: data["default"] = self.default
#         return data

#     # parse_value MUST be implemented by concrete subclasses


# class BaseButton(FormElement):
#     # Base for button-like elements (action, submit, cancel)
#     def __init__(self, type: str, text: str, value: Optional[str] = None):
#         super().__init__(type)
#         self.text = text
#         self.value = value
#         # Buttons don't have names in the context of form submission data

#     def to_dict(self) -> Dict[str, Any]:
#         data = {"type": self.type, "text": self.text}
#         if self.value: data["value"] = self.value
#         return data

#     def parse_value(self, submitted_value: Any) -> Any:
#         # Buttons don't process input data in this way
#         return None

# # --- Render-only elements ---


# --- Input elements ---


# class InputEmail(BaseInput):
#     def __init__(
#         self,
#         name: str,
#         label: Optional[str] = None,
#         placeholder: Optional[str] = None,
#         value: Optional[str] = None,
#         required: bool = False,
#         multiple: bool = False,
#     ):
#         super().__init__("email", name, label, value, required, placeholder)
#         self.multiple = multiple

#     def to_dict(self) -> Dict[str, Any]:
#         data = super().to_dict()
#         data["multiple"] = self.multiple # Always include multiple, defaults to false
#         return data

#     def parse_value(self, submitted_value: Any) -> Optional[str]:
#         # Same parsing as InputText
#         if submitted_value is None:
#             return None
#         try:
#             str_value = str(submitted_value)
#             return str_value if str_value != "" else None
#         except Exception:
#             return None


# class InputNumber(BaseInput):
#     def __init__(
#         self,
#         name: str,
#         label: Optional[str] = None,
#         placeholder: Optional[str] = None,
#         value: Optional[Union[int, float]] = None,
#         required: bool = False,
#         min_val: Optional[Union[int, float]] = None,
#         max_val: Optional[Union[int, float]] = None,
#         step: Optional[Union[int, float]] = None,
#     ):
#         super().__init__("number", name, label, value, required, placeholder)
#         self.min_val = min_val
#         self.max_val = max_val
#         self.step = step

#     def to_dict(self) -> Dict[str, Any]:
#         data = super().to_dict()
#         if self.min_val is not None: data["min"] = self.min_val
#         if self.max_val is not None: data["max"] = self.max_val
#         if self.step is not None: data["step"] = self.step
#         return data

#     def parse_value(self, submitted_value: Any) -> Optional[Union[int, float]]:
#         if submitted_value is None or submitted_value == '':
#             return None
#         try:
#             float_val = float(submitted_value)
#             # Return int if possible, otherwise float
#             return int(float_val) if float_val.is_integer() else float_val
#         except (ValueError, TypeError):
#             return None # Parsing failed


# class Checkbox(BaseInput):
#     # Represents a single, standalone checkbox
#     def __init__(
#         self,
#         name: str,
#         text: str,
#         label: Optional[str] = None,
#         value: bool = False, # Initial checked state
#         required: bool = False,
#     ):
#         super().__init__("checkbox", name, label, value, required)
#         self.text = text

#     def to_dict(self) -> Dict[str, Any]:
#         data = super().to_dict()
#         data["text"] = self.text
#         # Ensure initial value is boolean in schema
#         data["value"] = bool(self.initial_value)
#         return data
        
#     def parse_value(self, submitted_value: Any) -> bool:
#         # Returns True if the checkbox was submitted (present), False otherwise.
#         if submitted_value is None:
#             return False
#         # Check common truthy form values
#         if isinstance(submitted_value, str):
#             lower_name = self.name.lower() if self.name else ""
#             return submitted_value.lower() in ['on', 'true', 'yes', '1', lower_name]
#         # Fallback to simple boolean conversion
#         try:
#             return bool(submitted_value)
#         except Exception:
#             return False # Treat parsing errors as False


# # --- Choice elements ---

# class CheckboxGroup(BaseChoice):
#     def __init__(
#         self,
#         name: str,
#         options: Sequence[InputOption],
#         label: Optional[str] = None,
#         required: bool = False, # If at least one option must be selected
#     ):
#         super().__init__("checkbox_group", name, options, label, required, default=None)

#     def parse_value(self, submitted_value: Any) -> List[str]:
#         # Parses submitted value(s) into a list of strings. No validation.
#         if submitted_value is None:
#             return []
        
#         parsed_list = []
#         if isinstance(submitted_value, list):
#             # Convert all items to string, ignore None or empty strings
#             parsed_list = [str(v) for v in submitted_value if v is not None and str(v) != '']
#         elif submitted_value != '': # Handle single value submission
#             try:
#                 parsed_list = [str(submitted_value)]
#             except Exception:
#                  pass # Ignore if single value cannot be converted to string
        
#         return parsed_list # Return list (potentially empty)


# class RadioGroup(BaseChoice):
#     def __init__(
#         self,
#         name: str,
#         options: Sequence[InputOption],
#         label: Optional[str] = None,
#         default: Optional[str] = None, # Name of the default selected option
#         required: bool = True
#     ):
#         super().__init__("radio", name, options, label, required, default)

#     def parse_value(self, submitted_value: Any) -> Optional[str]:
#         # Parses submitted value to string. No validation.
#         if submitted_value is None:
#             return None
#         try:
#             str_value = str(submitted_value)
#             return str_value if str_value != "" else None
#         except Exception:
#             return None


# class Select(BaseChoice):
#     def __init__(
#         self,
#         name: str,
#         options: Sequence[InputOption],
#         label: Optional[str] = None,
#         default: Optional[str] = None, # Name of the default selected option
#         required: bool = True
#     ):
#        super().__init__("select", name, options, label, required, default)

#     def parse_value(self, submitted_value: Any) -> Optional[str]:
#         # Same parsing as RadioGroup
#         if submitted_value is None:
#             return None
#         try:
#             str_value = str(submitted_value)
#             return str_value if str_value != "" else None
#         except Exception:
#             return None


