import textwrap

import markdown
import yaml
from ansi2html import Ansi2HTMLConverter

from kodosumi.dtypes import DynamicModel


class Formatter:

    def convert(self, kind: str, message: str) -> str:
        raise NotImplementedError()


class DefaultFormatter(Formatter):

    def __init__(self):
        self.ansi = Ansi2HTMLConverter()

    def md(self, text: str) -> str:
        return markdown.markdown(text, extensions=['nl2br'])

    def dict2yaml(self, message: str) -> str:
        model = DynamicModel.model_validate_json(message)
        return yaml.safe_dump(model.model_dump(), allow_unicode=True)

    def ansi2html(self, message: str) -> str:
        return self.ansi.convert(message, full=False)

    def AgentFinish(self, values) -> str:
        ret = ['<div class="info-l1">Agent Finish</div>']
        if values.get("thought", None):
            ret.append(
                f'<div class="info-l2">Thought</div>"' + self.md(
                    values['thought']))
        if values.get("text", None):
            ret.append(self.md(values['text']))
        elif values.get("output", None):
            ret.append(self.md(values['output']))
        return "\n".join(ret)

    def TaskOutput(self, values) -> str:
        ret = ['<div class="info-l1">Task Output</div>']
        agent = values.get("agent", "unnamed agent")
        if values.get("name", None):
            ret.append(
                f'<div class="info-l2">{values["name"]} ({agent})</div>')
        else:
            ret.append(f'<div class="info-l2">{agent}</div>')
        if values.get("description", None):
            ret.append(
                f'<div class="info-l3">Task Description: </div>'
                f'<em>{values["description"]}</em>')
        if values.get("raw", None):
            ret.append(self.md(values['raw']))
        return "\n".join(ret)

    def CrewOutput(self, values) -> str:
        ret = ['<div class="info-l1">Crew Output</div>']
        if values.get("raw", None):
            ret.append(self.md(values['raw']))
        else:
            ret.append("no output found")
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
                ret.append(f'<div class="info-l1">{elem}</div>')
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