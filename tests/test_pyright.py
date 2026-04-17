import pathlib
import unittest

import pyright

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestPyright(unittest.TestCase):
    def test_repo_is_clean(self):
        self.assertEqual(pyright.main([str(REPO_ROOT)]), 0)


if __name__ == "__main__":
    unittest.main()
