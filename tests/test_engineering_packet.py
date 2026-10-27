import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from engineering_packet import build_packet, sarif_findings


def git(folder, *args):
    return subprocess.check_output(["git", "-C", str(folder), *args], text=True).strip()


class PacketTests(unittest.TestCase):
    def test_real_exact_sha_packet_and_missing_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d) / "repo"
            root.mkdir()
            git(root, "init", "-q")
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Tester")
            (root / "a.cpp").write_text("int f() { return 1; }\n")
            git(root, "add", ".")
            git(root, "commit", "-qm", "baseline")
            base = git(root, "rev-parse", "HEAD")
            (root / "a.cpp").write_text("int f() { return 2; }\n")
            git(root, "commit", "-qam", "change")
            head = git(root, "rev-parse", "HEAD")
            output = pathlib.Path(d) / "packet"
            index = build_packet(root, base, head, "ENG-1", output)
            self.assertEqual(index["changed_file_count"], 1)
            self.assertEqual(index["incomplete_check_count"], 1)
            self.assertEqual(json.loads((output / "CHECKS.json").read_text())["checks"][0]["status"], "NOT_RUN")
            self.assertIn("a.cpp", (output / "INDEX.md").read_text())
            self.assertTrue((output / "FULL_DIFF.patch").exists())
            self.assertEqual(json.loads((output / "MANIFEST.json").read_text())["head_sha"], head)
            with self.assertRaisesRegex(ValueError, "ancestor"):
                build_packet(root, head, base, "ENG-1", pathlib.Path(d) / "invalid")

    def test_sarif_delta_and_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d)
            src = path / "source"
            src.mkdir()
            git(src, "init", "-q")
            git(src, "config", "user.email", "test@example.com")
            git(src, "config", "user.name", "Tester")
            (src / "a.cpp").write_text("int f;\n")
            git(src, "add", ".")
            git(src, "commit", "-qm", "base")
            base = git(src, "rev-parse", "HEAD")
            (src / "a.cpp").write_text("int f; int g;\n")
            git(src, "commit", "-qam", "patch")
            head = git(src, "rev-parse", "HEAD")
            sarif = path / "x.sarif"
            sarif.write_text(json.dumps({"runs":[{"results":[
                {"ruleId":"R1","level":"error","message":{"text":"broken"},
                 "locations":[{"physicalLocation":{"artifactLocation":{"uri":"a.cpp"},
                 "region":{"startLine":1}}}]}]}]}))
            checks = path / "checks.json"
            checks.write_text(json.dumps({"checks":[{"name":"build","status":"FAIL","head_sha":head}]}))
            index = build_packet(src, base, head, "ENG-2", path / "packet",
                                 checks_path=checks, sarif_path=sarif)
            self.assertEqual(index["new_static_findings"], 1)
            self.assertEqual(index["failed_check_count"], 1)
            self.assertEqual(len(sarif_findings(sarif)), 1)


if __name__ == "__main__":
    unittest.main()
