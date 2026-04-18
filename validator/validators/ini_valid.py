import configparser
import pathlib

from validator.base import ValidationResult, Validator


class IniValidator(Validator):
    name = "ini"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        parser = configparser.ConfigParser(strict=True)
        try:
            parser.read_string(file.read_text(), source=str(file))
        except configparser.Error as e:
            return ValidationResult(ok=False, messages=(str(e),))
        return ValidationResult(ok=True)
