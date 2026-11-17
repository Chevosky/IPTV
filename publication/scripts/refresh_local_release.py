#!/usr/bin/env python3
"""Build a Flex/Zapper release locally; it never commits, pushes, or publishes."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def run(*command, cwd=None, env=None):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def npm_grab(npm, epg, channels, output):
    run(npm, "run", "grab", "--", f"--channels={channels}", f"--output={output}",
        "--days=1", "--maxConnections=3", "--timeout=12000", cwd=epg)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epg-checkout", type=Path, required=True,
                        help="Local checkout of iptv-org/epg with npm ci already completed")
    parser.add_argument("--npm", required=True, help="Local npm executable")
    parser.add_argument("--work", type=Path, required=True, help="Empty, disposable local work directory")
    parser.add_argument("--output", type=Path, required=True, help="New local release directory")
    parser.add_argument("--resume", action="store_true", help="Continue an interrupted local run from completed batches")
    args = parser.parse_args()
    epg, work, output = args.epg_checkout.resolve(), args.work.resolve(), args.output.resolve()
    if not (epg / "package.json").is_file():
        parser.error("--epg-checkout is not an initialized iptv-org/epg checkout")
    if output.exists():
        parser.error("--output must not exist")
    if work.exists() and not args.resume:
        parser.error("--work already exists; use --resume to continue it")
    # Compilation is intentionally isolated.  The checked-out inventory and
    # currently served artifacts are never overwritten by a local run.
    repo = work / "repository"
    if args.resume:
        if not repo.is_dir():
            parser.error("--resume needs the isolated repository left by an earlier run")
    else:
        work.mkdir(mode=0o700)
        shutil.copytree(SOURCE_ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    scripts = repo / "publication/scripts"
    base_batches, expansion_inputs = work / "base-batches", work / "expansion-inputs"
    expansion_batches, official_batches = work / "expansion-batches", work / "official-batches"
    for directory in (base_batches, expansion_inputs, expansion_batches, official_batches):
        directory.mkdir(mode=0o700, exist_ok=True)
    try:
        run(sys.executable, repo / "scripts/build_iptvorg_channels.py", epg, cwd=repo)
        for channels in sorted((repo / "iptvorg-batches").glob("*.channels.xml")):
            destination = base_batches / f"{channels.stem[:-9]}.xml"
            if not destination.exists():
                npm_grab(args.npm, epg, channels, destination)
        run(sys.executable, repo / "scripts/merge_xmltv.py", base_batches, repo / "guide-iptvorg-exact.xml", cwd=repo)
        run(sys.executable, repo / "scripts/merge_freeepg_fallbacks.py", cwd=repo)
        env = {**os.environ, "ZAPPER_PLAYLIST": "zapper-flex.m3u",
               "ZAPPER_SKIP_SUPPLEMENTARY_GUIDES": "1", "ZAPPER_METADATA_TIMEOUT_SECONDS": "8"}
        run(sys.executable, scripts / "build_flex_guide.py", cwd=repo, env=env)
        run(sys.executable, scripts / "validate_zapper_guide.py", repo / "guide-flex-candidate.xml",
            "--previous", repo / "guide-flex.xml", "--coverage-report", repo / "coverage-flex-candidate.json",
            cwd=repo, env={**os.environ, "ZAPPER_DATA_DIR": str(repo)})
        bindings = repo / "publication/epg/expansion-iptvorg.channels.xml"
        run(sys.executable, scripts / "split_epg_batches.py", bindings, expansion_inputs, cwd=repo)
        for channels in sorted(expansion_inputs.glob("*.channels.xml")):
            destination = expansion_batches / f"{channels.stem[:-9]}.xml"
            if not destination.exists():
                npm_grab(args.npm, epg, channels, destination)
        code = """from filter_expansion import unpack\nfrom guia_expansion import binding\nfrom pathlib import Path\np,_=unpack(Path('publication/epg/expansion-seed.json'))\nprint('\\n'.join(' '.join(x) for x in [sorted({binding(e)[0] for e in p['expansion'] if binding(e)})[i:i+6] for i in range(0, len({binding(e)[0] for e in p['expansion'] if binding(e)}), 6)]))"""
        groups = subprocess.check_output([sys.executable, "-c", code], cwd=repo, text=True).splitlines()
        for index, group in enumerate(groups, 1):
            destination = official_batches / f"{index:02}.xml"
            if not destination.exists():
                run(sys.executable, scripts / "collect_official_expansion.py", "--seed",
                    repo / "publication/epg/expansion-seed.json", "--output", destination,
                    *group.split(), cwd=repo, env={**os.environ, "PYTHONPATH": str(scripts)})
        code = """from pathlib import Path\nfrom refresh_expansion import build\nroot=Path('.')\nr=build(root,root/'zapper-flex.m3u',root/'guide-flex-candidate.xml',Path(r'%s'),Path(r'%s'),fetch_official=False,official_batches=sorted(Path(r'%s').glob('*.xml')))\nprint({k:r[k] for k in ('expansionCount','includedCount','freshProviderChannels')})""" % (expansion_batches, output, official_batches)
        run(sys.executable, "-c", code, cwd=repo, env={**os.environ, "PYTHONPATH": str(scripts)})
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
