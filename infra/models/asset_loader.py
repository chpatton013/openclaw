import functools
import pathlib


class AssetLoader:
    def __init__(self, repo_root: pathlib.Path) -> None:
        self._assets = repo_root / "assets"

    def lambda_path(self, name: str) -> pathlib.Path:
        path = self._assets / "lambdas" / name
        if not path.is_dir():
            raise FileNotFoundError(f"lambda asset not found: {path}")
        return path

    @functools.cache
    def read_text(self, *parts: str) -> str:
        return (self._assets.joinpath(*parts)).read_text()
