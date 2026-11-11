"""Publish Sports EPG only if its collected main revision is still current."""
import argparse
from pathlib import Path
from shutil import copyfile
import subprocess
import sys
from tempfile import TemporaryDirectory

FILES = ('guide-sports-news.xml', 'sports-guide-report.json')


def git(root, *args, check=True):
    return subprocess.run(['git', *args], cwd=root, check=check, text=True, capture_output=True)


def latest_main(root):
    git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
    return git(root, 'rev-parse', 'origin/main').stdout.strip()


def collect(root, staging):
    bindings = Path('publication/sports/guide-bindings.json')
    (staging / bindings).parent.mkdir(parents=True)
    copyfile(root / bindings, staging / bindings)
    prior = root / FILES[0]
    if prior.is_file():
        copyfile(prior, staging / FILES[0])
    for test in ('test_sports_guide.py', 'test_movistar_espn.py', 'test_guide_history.py'):
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'publication/tests', '-p', test],
                       cwd=root, check=True)
    subprocess.run([sys.executable, str(root / 'publication/scripts/build_sports_guide.py'),
                    '--root', str(staging)], cwd=root, check=True)
    (staging / 'guide-sports-news-candidate.xml').replace(staging / FILES[0])


def publish(root, report, collector=collect):
    """Recollect once after a main race. Never publish a candidate from a stale base."""
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout:
        raise ValueError('Sports publication requires a clean checkout')
    for attempt in (1, 2):
        base = latest_main(root)
        git(root, 'checkout', '--detach', base)
        with TemporaryDirectory(prefix='zapper-sports-publication-') as directory:
            staging = Path(directory)
            collector(root, staging)
            report.parent.mkdir(parents=True, exist_ok=True)
            copyfile(staging / FILES[1], report)
            if latest_main(root) != base:
                print(f'main advanced while collecting Sports data (attempt {attempt})', flush=True)
                continue
            for name in FILES:
                copyfile(staging / name, root / name)
        git(root, 'add', '--', *FILES)
        git(root, 'diff', '--cached', '--check')
        if not git(root, 'diff', '--cached', '--name-only').stdout:
            return 0
        git(root, 'commit', '-m', 'Refresh verified Sports guide', '--', *FILES)
        pushed = git(root, 'push', 'origin', 'HEAD:refs/heads/main', check=False)
        if pushed.returncode == 0:
            return 0
        if latest_main(root) == base:
            raise RuntimeError(pushed.stderr.strip())
        print(f'main advanced before the Sports push (attempt {attempt})', flush=True)
    return 75


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    return publish(args.root.resolve(), args.report.resolve())


if __name__ == '__main__':
    sys.exit(main())
