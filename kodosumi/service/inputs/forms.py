from typing import Any, Dict, List, Literal, Optional, Sequence, Union
from textwrap import dedent
from pydantic import BaseModel
import re
from kodosumi import dtypes
from kodosumi.log import logger
import markdown

class Element:

    type: Optional[str] = None

    def __init__(self, text: str | None = None):
        self.text = text

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError()

    def render(self) -> str:
        raise NotImplementedError()

    def parse_value(self, value: Any) -> Any:
        raise NotImplementedError()

class HTML(Element):
    type = "html"

    def __init__(self, text: str | None = None):
        super().__init__(text=text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
        }

    def render(self) -> str:
        return self.text or ""

class Break(HTML):
    def __init__(self):
        super().__init__('<div class="space"></div>')

class Markdown(Element):

    type = "markdown"

    def __init__(self, text: str):
        super().__init__(text=text or "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
        }

    def render(self) -> str:
        text = dedent(self.text or "")
        return markdown.markdown(
            text, 
            extensions=[
                'extra',
                'codehilite',
                'toc',
                'fenced_code'
            ]
        )

class FormElement(Element):
    def __init__(self, 
                 name: Optional[str] = None,
                 label: Optional[str] = None,
                 value: Optional[str] = None,
                 required: bool = False,
                 text: Optional[str] = None,
                 error: Optional[List[str]] = None):
        super().__init__(text=text)
        self.name = name
        self.label = label
        self.value = value
        self.required = required
        self.error = error

    def parse_value(self, value: Any) -> Any:
        return value

class Errors(FormElement):
    type = "errors"

    def __init__(self, error: Optional[List[str]] = None):
        super().__init__("_global_", error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type
        }

    def render(self) -> str:
        if not self.error:
            return ""
        return f"""
            <div class="space"></div>
            <div class="error-container small-round">
            <div class="error-text bold padding">
                {"\n".join(self.error if self.error else [])}
            </div>
            </div>
            <div class="space"></div>
        """


class InputText(FormElement):
    type = "text"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            size: Optional[int] = None,
            pattern: Optional[str] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.size = size
        self.pattern = pattern

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "size": self.size,
            "pattern": self.pattern,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.size:
            attrs.append(f'size="{self.size}"')
        if self.pattern:
            attrs.append(f'pattern="{self.pattern}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)

class InputNumber(InputText):
    type = "number"
    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            size: Optional[int] = None,
            pattern: Optional[str] = None,
            min_value: Optional[float] = None,
            max_value: Optional[float] = None,
            step: Optional[float] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.size = size
        self.pattern = pattern
        self.min_value = min_value
        self.max_value = max_value
        self.step = step

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "size": self.size,
            "pattern": self.pattern,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "step": self.step,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.size:
            attrs.append(f'size="{self.size}"')
        if self.pattern:
            attrs.append(f'pattern="{self.pattern}"')
        if self.min_value:
            attrs.append(f'min="{self.min_value}"')
        if self.max_value:
            attrs.append(f'max="{self.max_value}"')
        if self.step:
            attrs.append(f'step="{self.step}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class InputPassword(InputText):
    type = "password"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            size: Optional[int] = None,
            pattern: Optional[str] = None,
            min_length: Optional[int] = None,
            max_length: Optional[int] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.size = size
        self.pattern = pattern
        self.min_length = min_length
        self.max_length = max_length

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "size": self.size,
            "pattern": self.pattern,
            "min_length": self.min_length,
            "max_length": self.max_length,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.size:
            attrs.append(f'size="{self.size}"')
        if self.pattern:
            attrs.append(f'pattern="{self.pattern}"')
        if self.min_length:
            attrs.append(f'minlength="{self.min_length}"')
        if self.max_length:
            attrs.append(f'maxlength="{self.max_length}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class InputDate(InputText):
    type = "date"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            min_date: Optional[str] = None,
            max_date: Optional[str] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.min_date = min_date
        self.max_date = max_date

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "min_date": self.min_date,
            "max_date": self.max_date,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.min_date:
            attrs.append(f'min="{self.min_date}"')
        if self.max_date:
            attrs.append(f'max="{self.max_date}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class InputTime(InputText):
    type = "time"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            min_time: Optional[str] = None,
            max_time: Optional[str] = None,
            step: Optional[int] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.min_time = min_time
        self.max_time = max_time
        self.step = step

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "min_time": self.min_time,
            "max_time": self.max_time,
            "step": self.step,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.min_time:
            attrs.append(f'min="{self.min_time}"')
        if self.max_time:
            attrs.append(f'max="{self.max_time}"')
        if self.step:
            attrs.append(f'step="{self.step}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class InputDateTime(InputText):
    type = "datetime-local"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            min_datetime: Optional[str] = None,
            max_datetime: Optional[str] = None,
            step: Optional[int] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.min_datetime = min_datetime
        self.max_datetime = max_datetime
        self.step = step

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "min_datetime": self.min_datetime,
            "max_datetime": self.max_datetime,
            "step": self.step,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.min_datetime:
            attrs.append(f'min="{self.min_datetime}"')
        if self.max_datetime:
            attrs.append(f'max="{self.max_datetime}"')
        if self.step:
            attrs.append(f'step="{self.step}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class InputArea(FormElement):
    type = "textarea"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            placeholder: Optional[str] = None,
            rows: Optional[int] = None,
            cols: Optional[int] = None,
            max_length: Optional[int] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.placeholder = placeholder
        self.rows = rows
        self.cols = cols
        self.max_length = max_length

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "placeholder": self.placeholder,
            "rows": self.rows,
            "cols": self.cols,
            "max_length": self.max_length,
        }
    
    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field extra textarea border fill">')
        attrs = [f'name="{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.rows:
            attrs.append(f'rows="{self.rows}"')
        if self.cols:
            attrs.append(f'cols="{self.cols}"')
        if self.max_length:
            attrs.append(f'maxlength="{self.max_length}"')
        ret.append(f'<textarea {" ".join(attrs)}>{self.value or ""}</textarea>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)


class Checkbox(FormElement):
    type = "boolean"

    def __init__(
        self,
        name: str,
        option: str,
        label: Optional[str] = None,
        value: bool = False,
        error: Optional[List[str]] = None):
        super().__init__(name, label, value, required=False, text=option, 
                         error=error)

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

class InputOption(FormElement):

    type = "option"

    def __init__(self, 
                 name: Optional[str] = None,
                 label: Optional[str] = None,
                 value: Optional[bool] = False,
                 error: Optional[List[str]] = None):
        super().__init__(name, label, value, error=error)

    def render(self) -> str:
        return f'<option {"selected" if self.value else ""} value="{self.name}">{self.label}</legend>'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value
        }
    
class Select(FormElement):
    type = "select"

    def __init__(
        self,
        name: str,
        option: List[InputOption],
        label: Optional[str] = None,
        value: Optional[str] = None,
        error: Optional[List[str]] = None):
        super().__init__(name, label, value, required=False, text=None, 
                         error=error)
        self.option = []
        for opt in option:
            if not isinstance(opt, InputOption):
                opt.pop("type")
                add = InputOption(**opt)
            else:
                add = opt
            if value and add.name == value:
                add.value = True
            else:
                add.value = False
            self.option.append(add)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "option": [opt.to_dict() for opt in self.option],
            "error": self.error
        }

    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        ret.append(f'<select name="{self.name}">')
        # if self.value:
        #     ret.append(f'value="{self.value}"')
        ret.append(f'>')
        for opt in self.option:
            ret.append(opt.render())
        ret.append(f'</select>')
        ret.append(f'<i>arrow_drop_down</i>')
        if self.error:
            ret.append(f'<span class="error error-text">{" ".join(self.error)}</span>') 
        ret.append(f'</div>')
        return "\n".join(ret)

    def parse_value(self, value: Any) -> Any:
        for opt in self.option:
            if opt.name == value:
                opt.value = True
            else:
                opt.value = False
        return value

class ActionElement(FormElement):

    def __init__(self, text: str, error: Optional[List[str]] = None):
        super().__init__(text=text, error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text
        }


class Submit(ActionElement):
    type = "submit"

    def __init__(self, text: str, error: Optional[List[str]] = None):
        super().__init__(text=text, error=error)

    def render(self) -> str:
        return f'<button type="submit">{self.text}</button>'


class Cancel(ActionElement):
    type = "cancel"

    def __init__(self, text: str, error: Optional[List[str]] = None):
        super().__init__(text=text, error=error)

    def render(self) -> str:
        return "\n".join(["<a class=\"button\" href=\"/\">", self.text, '</a>'])


class Action(FormElement):
    type = "action"

    def __init__(self, 
                 name: Optional[str] = None,
                 value: Optional[str] = None,
                 required: bool = False,
                 text: Optional[str] = None,
                 error: Optional[List[str]] = None):
        super().__init__(name, None, value, required=False, text=text, 
                         error=error)

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
    def __init__(self, *children: Any):
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
            InputNumber,
            InputArea,
            InputDate,
            InputTime,
            InputDateTime,
            Checkbox, 
            InputOption,
            Select,
            Submit, 
            Cancel, 
            Action,
            Errors
        }
        children = []
        for elm in elms:
            found = False
            for chk in scope:
                if elm["type"] == chk.type:
                    found = True
                    typ = elm.pop("type")
                    name = elm.get("name", None)
                    if errors:
                        if name:
                            error = errors.get(name, None)
                            if error:
                                elm["error"] = error
                        elif typ == "errors":
                            elm["error"] = errors.get("_global_", [])
                    children.append(chk(**elm))
                    break
            if not found:
                logger.error(f"Unknown element type: {elm['type']}")
        return Model(*children, **kwargs)
    
    def set_data(self, data: Dict[str, Any]) -> None:
        """Setzt die Werte der Formularelemente"""
        # Wenn keine Daten vorhanden sind, setze alle Checkboxen auf False
        if not data:
            for child in self.children:
                if isinstance(child, Checkbox):
                    child.value = False
            return

        # Verarbeite vorhandene Daten
        for child in self.children:
            if hasattr(child, "name"):
                if isinstance(child, Checkbox):
                    # Für Checkboxen: Setze auf False wenn nicht in Daten vorhanden
                    child.value = child.parse_value(data.get(child.name, "off"))
                elif child.name in data:
                    child.value = child.parse_value(data[child.name])


__all__ = [
    "Model", "Break", "InputText", "InputNumber", "Checkbox", "InputOption", 
    "Select", "Action", "Submit", "Cancel", "Markdown", "HTML", "Errors", "InputArea", "InputDate", "InputTime", "InputDateTime"
]
