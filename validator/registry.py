import importlib.util
import pathlib

from validator.base import Validator


def all_validators() -> dict[str, type[Validator]]:
    here = pathlib.Path(__file__).resolve().parent / "validators"
    result: dict[str, type[Validator]] = {}
    for py_file in sorted(here.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        mod_name = "validator.validators._" + py_file.stem
        spec = importlib.util.spec_from_file_location(mod_name, py_file)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load validator module {py_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Validator)
                and attr is not Validator
            ):
                name = attr.name
                if name in result and result[name] is not attr:
                    raise ValueError(
                        f"Duplicate validator name {name!r}: "
                        f"{result[name]} and {attr}"
                    )
                result[name] = attr
    return result
