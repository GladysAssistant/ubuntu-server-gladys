from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowContractTests(unittest.TestCase):
    def test_all_external_actions_are_full_sha_pinned(self) -> None:
        files = list(WORKFLOWS.glob("*.yml"))
        self.assertEqual({path.name for path in files}, {"ci.yml", "nightly.yml", "release.yml"})
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if "uses:" not in line:
                    continue
                self.assertRegex(line, r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} +# v\d")
                self.assertNotRegex(line, r"@(main|master|v\d)(?:\s|$)")

    def test_ci_has_separate_required_jobs_and_sha_keyed_cache(self) -> None:
        text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        for job in (
            "lint:",
            "unit-tests:",
            "validate-config:",
            "build-iso:",
            "inspect-iso:",
            "qemu-install:",
            "qemu-firstboot:",
        ):
            self.assertIn(job, text)
        self.assertIn("ubuntu-26.04-amd64-${{ steps.ubuntu.outputs.sha256 }}", text)
        self.assertIn("cancel-in-progress: true", text)

    def test_media_jobs_install_the_grub_syntax_checker(self) -> None:
        expected_jobs = {
            "ci.yml": ("build-iso", "inspect-iso"),
            "release.yml": ("build-and-inspect",),
            "nightly.yml": ("full-integration",),
        }
        for filename, jobs in expected_jobs.items():
            workflow = yaml.safe_load(
                (WORKFLOWS / filename).read_text(encoding="utf-8")
            )
            for job in jobs:
                with self.subTest(filename=filename, job=job):
                    run_commands = "\n".join(
                        step.get("run", "")
                        for step in workflow["jobs"][job]["steps"]
                        if isinstance(step, dict)
                    )
                    self.assertIn("grub-common", run_commands)

    def test_nightly_is_weekly_and_runs_real_and_recovery_gates(self) -> None:
        text = (WORKFLOWS / "nightly.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r"cron: ['\"]\d+ \d+ \* \* 0['\"]")
        self.assertIn("--profile ci-real", text)
        self.assertIn("qemu-network-recovery.sh", text)
        self.assertIn("qemu-power-loss.sh", text)

    def test_release_is_tag_gated_and_only_publish_job_has_write_permissions(self) -> None:
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("tags: ['v*.*.*']", text)
        self.assertIn("^v[0-9]+\\.[0-9]+\\.[0-9]+$", text)
        self.assertEqual(text.count("contents: write"), 1)
        self.assertEqual(text.count("id-token: write"), 1)
        self.assertEqual(text.count("attestations: write"), 1)
        self.assertIn("actions/attest@", text)
        self.assertIn("gh release create", text)
        self.assertIn('[[ "$GITHUB_REF_NAME" == "v$version" ]]', text)


if __name__ == "__main__":
    unittest.main()
