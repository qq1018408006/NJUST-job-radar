import tarfile
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from tools.package_release import build_release


class ReleaseTests(SimpleTestCase):
    def test_release_excludes_local_state_and_retains_deployment_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            root.mkdir()
            for name in ("README.md", "Dockerfile", ".env.example", ".env", "db.sqlite3", "private.tar.gz"):
                (root / name).write_text("test data")
            (root / "tools").mkdir()
            (root / "tools" / "worker.py").write_text("print('ok')")
            (root / "tools" / "runtime.jsonl").write_text("private log")
            (root / "tools" / ".env.local").write_text("private token")
            folder, archive, count = build_release(Path(temp_dir) / "out", root=root)
            self.assertEqual(count, 4)
            self.assertTrue((folder / ".env.example").is_file())
            self.assertTrue((folder / "tools" / "worker.py").is_file())
            with tarfile.open(archive) as package:
                names = [member.name for member in package.getmembers()]
            self.assertFalse(any(name.endswith((".env", ".sqlite3", ".jsonl", ".env.local", "private.tar.gz")) for name in names))
            with self.assertRaises(FileExistsError):
                build_release(Path(temp_dir) / "out", root=root)

    def test_known_env_secret_stops_packaging_before_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            root.mkdir()
            (root / ".env").write_text("APP_API_TOKEN=private-test-secret-value")
            (root / "README.md").write_text("accidentally copied private-test-secret-value")
            with self.assertRaisesRegex(ValueError, "密钥"):
                build_release(Path(temp_dir) / "out", root=root)
            self.assertFalse((Path(temp_dir) / "out").exists())

    def test_nested_private_state_is_excluded_by_source_allowlist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            approved = {
                "research.schema.json", "radar/views.py", "templates/base.html",
                "docs/assets/readme-poster.png",
                ".github/workflows/ci.yml", "prompts/employer_research.txt",
                "deploy/job-radar-ai-worker.service.template",
                "deploy/job-radar-ai-worker.timer",
            }
            private = {
                "config/credentials.json", "radar/recruitment.json",
                "tools/response.txt", "tools/debug.yaml", "tools/.env.local",
                "radar/.codex/auth.json", "radar/.claude/settings.json",
                "tools/.opencode/config.py", "config/.aws/credentials",
                "config/.ssh/id_rsa", "radar/logs/trace.py",
                "tools/private/session.py", "radar/snapshots/employer.html",
                "templates/backups/account.html", "radar/data/db.sqlite3-wal",
                "config/credentials/local.py", "tools/Secrets/token.py",
                ".github/.hidden/private.yml", "prompts/real_response.txt",
                "deploy/private.service", "radar/secret.pem", "radar/db.sqlite",
                "docs/assets/private-screenshot.png", "docs/assets/.codex/auth.json",
            }
            for name in approved | private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic test fixture")
            (root / "tools" / "linked.py").symlink_to(root / "radar" / "views.py")
            folder, archive, count = build_release(Path(temp_dir) / "out", root=root)
            self.assertEqual(count, len(approved))
            actual = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
            self.assertEqual(actual, approved)
            with tarfile.open(archive) as package:
                self.assertTrue(all(member.uid == member.gid == 0 for member in package.getmembers()))
                self.assertTrue(all(not member.uname and not member.gname for member in package.getmembers()))
