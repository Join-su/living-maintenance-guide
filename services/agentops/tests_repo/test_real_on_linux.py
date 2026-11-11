"""This repository's one change to the frozen AgentOps code (FROZEN-DIFF.md): on Linux real mode stays refused unless
LLMOPS_ALLOW_REAL_ON_LINUX=1 is set (this repository's compose sets it). macOS is as before. Standard library only:
  docker compose run --rm --no-deps -v ./services/agentops/tests_repo:/t:ro agentops python -m unittest discover -s /t -v"""
import tempfile
import unittest

from llmops import config


def load(platform, **env):
    with tempfile.TemporaryDirectory() as d:
        return config.load_settings(env={"LLMOPS_DATA_DIR": d, **env}, platform=platform)


class RealOnLinux(unittest.TestCase):
    def test_linux_real_is_refused_without_the_setting(self):
        with self.assertRaises(SystemExit):
            load("linux", LLMOPS_LLM_MODE="real")

    def test_linux_real_is_refused_when_the_setting_is_not_1(self):
        for v in ("", "0", "yes", "true"):
            with self.assertRaises(SystemExit):
                load("linux", LLMOPS_LLM_MODE="real", LLMOPS_ALLOW_REAL_ON_LINUX=v)

    def test_linux_real_starts_with_the_setting(self):
        self.assertEqual(load("linux", LLMOPS_LLM_MODE="real", LLMOPS_ALLOW_REAL_ON_LINUX="1").mode, "real")

    def test_linux_fake_and_macos_real_are_as_before(self):
        self.assertEqual(load("linux", LLMOPS_LLM_MODE="fake").mode, "fake")
        self.assertEqual(load("darwin", LLMOPS_LLM_MODE="real").mode, "real")


if __name__ == "__main__":
    unittest.main()
