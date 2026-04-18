import pathlib
import xml.etree.ElementTree as ET

from validator.base import ValidationResult, Validator


class XmlValidator(Validator):
    name = "xml"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        try:
            ET.parse(file)
        except ET.ParseError as e:
            return ValidationResult(ok=False, messages=(str(e),))
        return ValidationResult(ok=True)
