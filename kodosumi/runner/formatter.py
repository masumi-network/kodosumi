import textwrap

import markdown
import yaml
from ansi2html import Ansi2HTMLConverter

from kodosumi.dtypes import DynamicModel

MARKDOWN_EXTENSIONS = [
    "markdown.extensions.nl2br",
    "markdown.extensions.fenced_code"
]

class Formatter:

    def convert(self, kind: str, message: str) -> str:
        raise NotImplementedError()


class DefaultFormatter(Formatter):

    def __init__(self):
        self.ansi = Ansi2HTMLConverter()

    def md(self, text: str) -> str:
        return markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS)

    def dict2yaml(self, message: str) -> str:
        model = DynamicModel.model_validate_json(message)
        return yaml.safe_dump(model.model_dump(), allow_unicode=True)

    def ansi2html(self, message: str) -> str:
        return self.ansi.convert(message, full=False)

    def AgentFinish(self, values, title=None) -> str:
        title = title or "Agent Finish"
        ret = [f'<div class="info-l1"><span class="info-text">{title}</span></div>']
        if values.get("thought", None):
            ret.append(
                f'<div class="info-l2"><span class="info-text">Thought</span></div>"' + self.md(
                    values['thought']))
        if values.get("text", None):
            ret.append(self.md(values['text']))
        elif values.get("output", None):
            ret.append(self.md(values['output']))
        return "\n".join(ret)

    def AgentAction(self, value) -> str:
        return self.AgentFinish(value, "Agent Action")

    def ToolResult(self, values) -> str:
        ret = ['<div class="info-l1"><span class="info-text">Tool Result</span></div>']
        if values.get("result", None):
            ret.append(self.md(values['result']))
        return "\n".join(ret)

    def TaskOutput(self, values) -> str:
        ret = ['<div class="info-l1"><span class="info-text">Task Output</span></div>']
        agent = values.get("agent", "unnamed agent")
        if values.get("name", None):
            ret.append(
                f'<div class="info-l2"><span class="info-text">{values["name"]} ({agent})</span></div>')
        else:
            ret.append(f'<div class="info-l2"><span class="info-text">{agent}</span></div>')
        if values.get("description", None):
            ret.append(
                f'<div class="info-l3"><span class="info-text">Task Description:</span> </div>'
                f'<em>{values["description"]}</em>')
        if values.get("raw", None):
            ret.append(self.md(values['raw']))
        return "\n".join(ret)

    def CrewOutput(self, values) -> str:
        ret = ['<div class="info-l1"><span class="info-text">Crew Output</span></div>']
        if values.get("raw", None):
            ret.append(self.md(values['raw']))
        else:
            ret.append("no output found")
        for task in values.get("tasks_output", []):
            ret.append(self.TaskOutput(task))
        return "\n".join(ret)

    def Text(self, values) -> str:
        body = values.get("body", "")
        return f"<blockquote><code>{body}</code></blockquote>"

    def HTML(self, values) -> str:
        return values.get("body", "")

    def Markdown(self, values) -> str:
        body = values.get("body", "")
        return self.md(textwrap.dedent(body))

    def obj2html(self, message: str) -> str:
        model = DynamicModel.model_validate_json(message)
        ret = []
        for elem, values in model.root.items():
            meth = getattr(self, elem, None)
            if meth:
                ret.append(meth(values))
            else:
                ret.append(f'<div class="info-l1"><span class="info-text">{elem}</span></div>')
                ret.append(f"<pre>{values}</pre>")
        return "\n".join(ret)

    def convert(self, kind: str, message: str) -> str:
        if kind == "inputs":
            return self.dict2yaml(message)
        if kind in ("stdout", "stderr"):
            return self.ansi2html(message)
        if kind in ("action", "result", "final"):
            return self.obj2html(message)
        return message