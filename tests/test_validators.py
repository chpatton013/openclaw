import pathlib
import unittest

from validator.runner import run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestValidators(unittest.TestCase):
    def test_repo_is_clean(self):
        self.assertEqual(run(repo_root=REPO_ROOT), 0)


if __name__ == "__main__":
    unittest.main()
