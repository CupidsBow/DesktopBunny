"""单实例闸门：同一名字只允许一个持有者。"""

from __future__ import annotations

import unittest
import uuid

from components.single_instance import SingleInstance


def unique_name() -> str:
    """每个测试用独立的互斥量名，避免撞上本机正在运行的 Bunny。"""
    return f"DesktopBunny_Alice_Test_{uuid.uuid4().hex}"


class SingleInstanceTest(unittest.TestCase):
    def test_second_instance_is_blocked(self):
        name = unique_name()
        first = SingleInstance(name)
        second = SingleInstance(name)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()

    def test_release_allows_reacquire(self):
        name = unique_name()
        guard = SingleInstance(name)
        self.assertTrue(guard.acquire())
        guard.release()
        again = SingleInstance(name)
        self.assertTrue(again.acquire())
        again.release()


if __name__ == "__main__":
    unittest.main()
