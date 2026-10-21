"""单实例闸门：同一名字只允许一个持有者。"""

from __future__ import annotations

import unittest

from components.single_instance import SingleInstance


class SingleInstanceTest(unittest.TestCase):
    def test_second_instance_is_blocked(self):
        first = SingleInstance()
        second = SingleInstance()
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()

    def test_release_allows_reacquire(self):
        guard = SingleInstance()
        self.assertTrue(guard.acquire())
        guard.release()
        again = SingleInstance()
        self.assertTrue(again.acquire())
        again.release()


if __name__ == "__main__":
    unittest.main()
