"""Exercise the publication retry with a local bare remote and synthetic guide inputs."""

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import shutil
import shlex
import subprocess
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ADVANCE = """\
import os, subprocess
from pathlib import Path
sports = Path(os.environ["TEST_SPORTS"])
guide = sports / "guide-sports-news.xml"
guide.write_text(guide.read_text() + " new")
subprocess.run(["git", "-C", str(sports), "add", "guide-sports-news.xml"], check=True)
subprocess.run(["git", "-C", str(sports), "commit", "-qm", "Sports"], check=True)
subprocess.run(["git", "-C", str(sports), "push", "-q", "origin", "main"], check=True)
"""
BASE = """\
import os, subprocess, sys
from pathlib import Path
case = os.environ["TEST_CASE"]
attempts = Path(os.environ["TEST_ATTEMPTS"])
count = int(attempts.read_text() or "0") + 1
attempts.write_text(str(count))
if case == "before" and count == 1:
    subprocess.run([sys.executable, "publication/scripts/advance.py"], check=True)
"""
BUILD = """\
import base64, hashlib, json, os, subprocess, sys
from pathlib import Path

def build(root, canonical_playlist, guide, batches, output):
    count = int(Path(os.environ["TEST_ATTEMPTS"]).read_text())
    case = os.environ["TEST_CASE"]
    sports = (root/"guide-sports-news.xml").read_text()
    if (case == "during" and count == 1) or case == "always":
        subprocess.run([sys.executable, "publication/scripts/advance.py"], check=True)
    output.mkdir()
    data = {"playlist": "flex new " + sports, "flexXML": "guide new " + sports, "sportsXML": sports}
    raw = json.dumps(data).encode()
    envelope = {"sha256": hashlib.sha256(raw).hexdigest(), "payload": base64.b64encode(raw).decode()}
    (output/"coordinated-generation.json").write_text(json.dumps(envelope))
    (output/"zapper-flex.m3u").write_text(data["playlist"])
    (output/"guide-flex.xml").write_text(data["flexXML"])
    (output/"guide-flex-20261003.xml").write_text("wrong" if case == "bad-output" else data["flexXML"])
    return {"expansionCount": 1, "includedCount": 1, "freshProviderChannels": 1,
            "youtubeRefresh": {}, "publicFallbackRefresh": {},
            "nativeYouTubeFallbackRefresh": {"inventory": 2, "attempted": 2, "renewed": 1}}
"""

def run(args, directory, env=None, check=True):
    test_env = {**(os.environ if env is None else env),
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(args, cwd=directory, env=test_env, check=check,
                          capture_output=True, text=True, timeout=30)

def prepare(root):
    repo = root / "base"
    repo.mkdir()
    run(["git", "init", "-qb", "main"], repo)
    for sub in ("publication/scripts", "publication/epg", "scripts"):
        (repo / sub).mkdir(parents=True, exist_ok=True)
    files = {
        "scripts/build_epg.py": BASE,
        "publication/scripts/advance.py": ADVANCE,
        "publication/scripts/channel_policy.py": "def filter_playlist(data, policy): return data\ndef load_policy(root): return {}",
        "publication/scripts/validate_flex.py": "def validate(root, baseline, playlist): return {}",
        "publication/scripts/validate_zapper_guide.py": "",
        "publication/scripts/build_flex_guide.py": "",
        "publication/scripts/refresh_expansion.py": BUILD,
        "catalog-flex.json": json.dumps({"channels": [None] * 237}),
        "publication/epg/base.m3u": "base",
        "publication/epg/expansion-seed.json": "{}",
        "guide-flex.xml": "old flex guide",
        "guide-flex-20261003.xml": "old flex guide",
        "zapper-flex.m3u": "old playlist",
        "guide-sports-news.xml": "sports",
    }
    for name, contents in files.items():
        (repo / name).write_text(contents)
    for name in ("refresh.py", "retry-coordinated-publication.sh"):
        shutil.copy(SCRIPTS / name, repo / "publication/scripts" / name)
    run(["git", "add", "."], repo)
    run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Initial"], repo)
    remote = root / "remote.git"
    run(["git", "clone", "-q", "--bare", str(repo), str(remote)], root)
    for name in ("runner", "sports"):
        run(["git", "clone", "-q", str(remote), name], root)
    run(["git", "config", "user.name", "Test"], root/"sports")
    run(["git", "config", "user.email", "test@example.invalid"], root/"sports")
    return remote

class CoordinatedPublicationRetryTests(unittest.TestCase):
    def verify_case(self, case, expected_exit, expected_attempts, expected_main_commits):
        with TemporaryDirectory(prefix="zapper-publication-retry-") as directory:
            root = Path(directory)
            remote = prepare(root)
            if case == "push-denied":
                hook = remote / "hooks/pre-receive"
                hook.write_text("#!/bin/sh\nexit 1\n")
                hook.chmod(0o700)
            attempts = root / "attempts"
            attempts.write_text("")
            bins = root / "bin"
            bins.mkdir()
            python = bins / "python"
            python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
            python.chmod(0o700)
            env = {
                **os.environ,
                "TEST_CASE": case,
                "TEST_SPORTS": str(root / "sports"),
                "TEST_ATTEMPTS": str(attempts),
                "REFRESH_MODE": "epg",
                "ZAPPER_EXPANSION_BATCHES": str(root / "batches"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": str(bins) + os.pathsep + os.environ.get("PATH", os.defpath),
            }
            runner = root / "runner"
            result = run(["bash", "publication/scripts/retry-coordinated-publication.sh",
                          sys.executable, "publication/scripts/refresh.py"], runner, env, check=False)
            self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
            self.assertEqual(int(attempts.read_text()), expected_attempts)
            self.assertEqual(int(run(["git", "rev-list", "--count", "main"], remote).stdout),
                             expected_main_commits)
            report = json.loads((runner / "publication-review.json").read_text())
            self.assertEqual(report["published"], expected_exit == 0)
            if expected_exit == 0:
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report.get("nativeYouTubeFallbackRefresh"),
                                 {"inventory": 2, "attempted": 2, "renewed": 1})
                changed = run(["git", "diff", "--name-only", "main^", "main"], remote).stdout.splitlines()
                self.assertCountEqual(changed, ["coordinated-generation.json", "guide-flex.xml",
                                                "guide-flex-20261003.xml", "zapper-flex.m3u"])
                sports = run(["git", "show", "main:guide-sports-news.xml"], remote).stdout
                self.assertEqual(sports, "sports" if case == "clean" else "sports new")
                envelope = json.loads(run(["git", "show", "main:coordinated-generation.json"], remote).stdout)
                self.assertEqual(json.loads(base64.b64decode(envelope["payload"]))["sportsXML"], sports)
            else:
                self.assertEqual(report["status"], "RETRY" if expected_exit == 75 else "FAIL")
                self.assertEqual(run(["git", "show", "main:guide-flex.xml"], remote).stdout, "old flex guide")

    def test_publishes_four_files_in_one_commit(self):
        self.verify_case("clean", 0, 1, 2)

    def test_regenerates_when_main_advanced_before_generation(self):
        self.verify_case("before", 0, 2, 3)

    def test_regenerates_when_main_advances_during_generation(self):
        self.verify_case("during", 0, 2, 3)

    def test_stops_after_one_retry(self):
        self.verify_case("always", 75, 2, 3)

    def test_does_not_retry_push_denied_on_unchanged_main(self):
        self.verify_case("push-denied", 1, 1, 1)

    def test_rejects_divergent_flex_guide(self):
        self.verify_case("bad-output", 1, 1, 1)
