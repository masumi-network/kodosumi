from textwrap import dedent
from typing import Any, Dict, List, Optional
import html
import markdown
from pydantic import BaseModel
import json

from kodosumi.log import logger


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
    type = "break"
    def __init__(self, *args, **kwargs):
        super().__init__('<div class="space"></div>')


class HR(HTML):
    type = "hr"
    def __init__(self, *args, **kwargs):
        super().__init__('<hr class="medium"/>')


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
        error = "\n".join(self.error if self.error else [])
        return f"""
            <div class="space"></div>
            <div class="error-container small-round">
            <div class="error-text bold padding">
                {error}
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
        option: Optional[str] = None,
        label: Optional[str] = None,
        value: bool = False,
        error: Optional[List[str]] = None):
        if option is None:
            option = "on"
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
        required: bool = False,
        error: Optional[List[str]] = None):
        super().__init__(name, label, value, required=required, text=None,
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
            "required": self.required,
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
        # ret = []
        # attrs = [f'name="__cancel__"']
        # attrs.append(f'value="__cancel__"')
        # ret.append(f'<button {" ".join(attrs)}>')
        # ret.append(self.text or "")
        # ret.append(f'</button>')
        # return "\n".join(ret)
        return "\n".join([
            "<a class=\"button\" href=\"javascript:history.back()\">", self.text or "", '</a>'])


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
        attrs = []
        if self.name:
            attrs = [f'name="{self.name}"']
        if self.value is not None:
            if self.value:
                attrs.append(f'value="{self.value}"')
        ret.append(f'<button {" ".join(attrs)}>')
        ret.append(self.text or "")
        ret.append(f'</button>')
        return "\n".join(ret)

    def parse_value(self, value: Any) -> Any:
        return value


class InputFiles(FormElement):
    type = "file"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            multiple: bool = False,
            directory: bool = False,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.multiple = multiple
        self.directory = directory

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "multiple": self.multiple,
            "directory": self.directory
        }

    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<span id="_files-{self.name}">')
        ret.append(f'<div id="_items-{self.name}"></div>')
        ret.append(f'<div class="space"></div></span>')
        ret.append(f'<button id="_button-{self.name}" class="fileInput medium circle">')
        ret.append(f'<i>attach_file</i>')
        attrs = [f'type="{self.type}" name="_dialog-{self.name}" id="_dialog-{self.name}"']
        if self.required:
            attrs.append(f'required')
        if self.multiple:
            attrs.append(f'multiple')
        if self.directory:
            attrs.append(f'webkitdirectory')
        files = None
        if self.value:
            attrs.append(f'value="{self.value}"')
            files = [f["filename"] for f in json.loads(
                self.value).get("items").values()]            
        ret.append(f'<input {" ".join(attrs)}>')
        ret.append(f'</button>')
        value = html.escape(self.value) if self.value else ""
        ret.append(f'<input type="hidden" name="{self.name}" id="_list-{self.name}" value="{value}">')
        if files:
            ret.append(f'<span class="primary">{len(files)} files have been uploaded</span>')
        ret.append(f'<div class="space"></div>')
        return "\n".join(ret)


# =============================================================================
# New MIP-003 Compatible Input Types
# =============================================================================


class InputEmail(InputText):
    """Email input field with email validation."""
    type = "email"

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
        super().__init__(name, label, value, required, placeholder, size,
                         pattern, error=error)
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
            attrs.append('required')
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
        ret.append('</div>')
        return "\n".join(ret)


class InputUrl(InputText):
    """URL input field with URL validation."""
    type = "url"

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
        super().__init__(name, label, value, required, placeholder, size,
                         pattern, error=error)
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
            attrs.append('required')
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
        ret.append('</div>')
        return "\n".join(ret)


class InputFileUrl(InputUrl):
    """File URL input field.

    Kodosumi behavior: accepts a URL string, rendered as an HTML url input.
    MIP-003 projection: advertised to external clients as 'file' with
    outputFormat='url'. Sokosumi uploads the file and sends the resulting
    URL back as a string, which this field consumes directly.

    Note: reverse conversion (MIP-003 file -> Kodosumi) always produces
    InputFiles; InputFileUrl is a forward-only convenience type.
    """
    type = "file_url"

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
            accept: Optional[str] = None,
            max_size: Optional[int] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, placeholder, size,
                         pattern, min_length, max_length, error=error)
        self.accept = accept
        self.max_size = max_size

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
            "accept": self.accept,
            "max_size": self.max_size,
        }

    def render(self) -> str:
        # Emit type="url" (valid HTML) regardless of the "file_url" kodosumi tag.
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = ['type="url"', f'name="{self.name}"']
        if self.required:
            attrs.append('required')
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
        ret.append('</div>')
        return "\n".join(ret)


class InputTel(InputText):
    """Telephone number input field."""
    type = "tel"

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
        super().__init__(name, label, value, required, placeholder, size,
                         pattern, error=error)
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
            attrs.append('required')
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
        ret.append('</div>')
        return "\n".join(ret)


class InputSearch(InputText):
    """Search input field."""
    type = "search"

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
        super().__init__(name, label, value, required, placeholder, size,
                         pattern, error=error)
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
            attrs.append('required')
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
        ret.append('</div>')
        return "\n".join(ret)


class InputMonth(FormElement):
    """Month picker input field."""
    type = "month"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            min_month: Optional[str] = None,
            max_month: Optional[str] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.min_month = min_month
        self.max_month = max_month

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "min_month": self.min_month,
            "max_month": self.max_month,
        }

    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append('required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.min_month:
            attrs.append(f'min="{self.min_month}"')
        if self.max_month:
            attrs.append(f'max="{self.max_month}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>')
        ret.append('</div>')
        return "\n".join(ret)


class InputWeek(FormElement):
    """Week picker input field."""
    type = "week"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            min_week: Optional[str] = None,
            max_week: Optional[str] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.min_week = min_week
        self.max_week = max_week

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
            "min_week": self.min_week,
            "max_week": self.max_week,
        }

    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append('required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.min_week:
            attrs.append(f'min="{self.min_week}"')
        if self.max_week:
            attrs.append(f'max="{self.max_week}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>')
        ret.append('</div>')
        return "\n".join(ret)


class InputColor(FormElement):
    """Color picker input field."""
    type = "color"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
        }

    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field border fill">')
        attrs = [f'type="{self.type}"', f'name="{self.name}"']
        if self.required:
            attrs.append('required')
        if self.value:
            attrs.append(f'value="{self.value}"')
        else:
            attrs.append('value="#000000"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>')
        ret.append('</div>')
        return "\n".join(ret)


class InputRange(FormElement):
    """Range slider input field."""
    type = "range"

    def __init__(
            self,
            name: str,
            label: Optional[str] = None,
            value: Optional[float] = None,
            required: bool = False,
            min_value: Optional[float] = None,
            max_value: Optional[float] = None,
            step: Optional[float] = None,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.min_value = min_value if min_value is not None else 0
        self.max_value = max_value if max_value is not None else 100
        self.step = step

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "required": self.required,
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
            attrs.append('required')
        if self.value is not None:
            attrs.append(f'value="{self.value}"')
        attrs.append(f'min="{self.min_value}"')
        attrs.append(f'max="{self.max_value}"')
        if self.step:
            attrs.append(f'step="{self.step}"')
        ret.append(f'<input {" ".join(attrs)}>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>')
        ret.append('</div>')
        return "\n".join(ret)

    def parse_value(self, value: Any) -> Any:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return value


class InputHidden(FormElement):
    """Hidden input field for server-side values."""
    type = "hidden"

    def __init__(
            self,
            name: str,
            value: str,
            error: Optional[List[str]] = None):
        super().__init__(name, None, value, required=False, error=error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "value": self.value,
        }

    def render(self) -> str:
        return f'<input type="hidden" name="{self.name}" value="{self.value or ""}">'


class InputRadio(FormElement):
    """Radio button group input field."""
    type = "radio"

    def __init__(
            self,
            name: str,
            option: List[InputOption],
            label: Optional[str] = None,
            value: Optional[str] = None,
            required: bool = False,
            error: Optional[List[str]] = None):
        super().__init__(name, label, value, required, error=error)
        self.option = []
        for opt in option:
            if not isinstance(opt, InputOption):
                opt_copy = dict(opt)
                opt_copy.pop("type", None)
                add = InputOption(**opt_copy)
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
            "required": self.required,
            "option": [opt.to_dict() for opt in self.option],
        }

    def render(self) -> str:
        ret = []
        ret.append(f'<legend class="inputs-label">{self.label or ""}</legend>')
        ret.append(f'<div class="field">')
        for opt in self.option:
            checked = 'checked' if opt.value else ''
            required_attr = 'required' if self.required else ''
            ret.append(f'<label class="radio">')
            ret.append(f'<input type="radio" name="{self.name}" value="{opt.name}" {checked} {required_attr}>')
            ret.append(f'<span>{opt.label or opt.name}</span>')
            ret.append(f'</label>')
        if self.error:
            ret.append(f'<span class="error">{" ".join(self.error)}</span>')
        ret.append('</div>')
        return "\n".join(ret)

    def parse_value(self, value: Any) -> Any:
        for opt in self.option:
            if opt.name == value:
                opt.value = True
            else:
                opt.value = False
        return value


class DisplayInfo(Element):
    """Display-only information field (MIP-003 'none' type)."""
    type = "none"

    def __init__(
            self,
            text: str,
            label: Optional[str] = None):
        super().__init__(text=text)
        self.label = label

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "label": self.label,
        }

    def render(self) -> str:
        ret = []
        if self.label:
            ret.append(f'<legend class="inputs-label">{self.label}</legend>')
        ret.append(f'<div class="field">')
        ret.append(f'<p>{self.text or ""}</p>')
        ret.append('</div>')
        return "\n".join(ret)


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
            InputPassword,
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
            Errors,
            InputFiles,
            HR,
            Break,
            # MIP-003 compatible types
            InputEmail,
            InputUrl,
            InputFileUrl,
            InputTel,
            InputSearch,
            InputMonth,
            InputWeek,
            InputColor,
            InputRange,
            InputHidden,
            InputRadio,
            DisplayInfo,
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
        if not data:
            for child in self.children:
                if isinstance(child, Checkbox):
                    child.value = False
            return

        for child in self.children:
            if hasattr(child, "name"):
                if isinstance(child, Checkbox):
                    child.value = child.parse_value(data.get(child.name, "off"))
                elif child.name in data:
                    child.value = child.parse_value(data[child.name])


__all__ = [
    "Model", "Break", "HR", "InputText", "InputNumber", "Checkbox",
    "InputOption", "Select", "Action", "Submit", "Cancel", "Markdown", "HTML",
    "Errors", "InputArea", "InputDate", "InputTime", "InputDateTime",
    "InputFiles", "InputPassword",
    # MIP-003 compatible types
    "InputEmail", "InputUrl", "InputFileUrl", "InputTel", "InputSearch",
    "InputMonth", "InputWeek", "InputColor", "InputRange",
    "InputHidden", "InputRadio", "DisplayInfo",
]
