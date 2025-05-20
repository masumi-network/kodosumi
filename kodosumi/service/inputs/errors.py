from typing import Dict, List, Optional


class InputsError(Exception):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args)
        self.errors: Dict[str, List[str]] = {}
        for key, value in kwargs.items():
            self.errors.setdefault(key, []).append(value)

    def add(self, **kwargs):
        for field, message in kwargs.items():
            self.errors.setdefault(field, []).append(message)

    def flash(self, message: str):
        self.errors.setdefault("__general__", []).append(message)

    def has_errors(self) -> bool:
        return bool(self.errors)

    def __bool__(self) -> bool:
        # Allows checking the error object directly, e.g., `if error:`
        return self.has_errors() 