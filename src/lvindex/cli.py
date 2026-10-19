"""Command line entry point: `lvindex [--project P] <command> ...` (console script, python -m lvindex, lvindex.py)."""
import json
import os
import sys
from urllib.parse import urlparse
from urllib.request import url2pathname

from . import __version__

PKG = os.path.dirname(os.path.abspath(__file__))


def _url_path(url):
    p = urlparse(url)
    if p.scheme != "file":
        return url
    unc = "\\\\" + p.netloc if p.netloc and p.netloc.lower() != "localhost" else ""
    return os.path.normpath(unc + url2pathname(p.path))


def _checkout(pkg):
    """The checkout a package folder belongs to: <checkout>\\src\\lvindex -> <checkout>."""
    parent = os.path.dirname(pkg)
    return os.path.dirname(parent) if os.path.basename(parent) == "src" else pkg


def install_source(direct_url, pkg_in_dist, pkg=PKG):
    """Where the running lvindex comes from, after the install record pip / pipx / uv write (direct_url.json, PEP 610):
    ("git", commit, url) | ("editable", folder) | ("folder", folder) | ("index", "") | ("checkout", folder).
    `direct_url` = the parsed record or None; `pkg_in_dist` = the running package is the installed one."""
    d = direct_url or {}
    if (d.get("dir_info") or {}).get("editable"):    # the record points at a checkout: is it this one?
        folder = _url_path(d.get("url", "")).rstrip("\\/")
        real = os.path.normcase(os.path.realpath(folder)) if folder else ""
        if real and os.path.normcase(os.path.realpath(pkg)).startswith(real + os.sep):
            return ("editable", folder)
        return ("checkout", _checkout(pkg))
    site = {"site-packages", "dist-packages"} & set(os.path.normcase(pkg).split(os.sep))
    if not pkg_in_dist or not (d or site):           # no record and not installed: a checkout's own .egg-info
        return ("checkout", _checkout(pkg))
    if "vcs_info" in d:
        return ("git", d["vcs_info"].get("commit_id", "")[:12], d.get("url", ""))
    if "dir_info" in d or "archive_info" in d:       # a folder, or a wheel / sdist file or URL
        return ("folder", _url_path(d.get("url", "")))
    return ("index", "")


def _source():
    try:
        from importlib import metadata
        dist = metadata.distribution("lvindex")
    except Exception:                                # noqa: BLE001 - not installed: running from a checkout
        return ("checkout", _checkout(PKG))
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "null")
    except ValueError:
        direct = None
    if not isinstance(direct, dict):
        direct = None
    here = os.path.realpath(os.path.join(PKG, "__init__.py"))
    there = os.path.realpath(str(dist.locate_file("lvindex/__init__.py")))
    return install_source(direct, os.path.normcase(here) == os.path.normcase(there))


def version_text():
    """`lvindex 0.2.0 (git d3dd0465feaf, <url>)`: the commit is the one a Claude Code plugin version from this
    repository shows, so the CLI and the skill can be compared."""
    s = _source()
    extra = {"git": f" (git {s[1]}, {s[-1]})", "editable": f" (editable {s[1]})",
             "folder": f" (installed from {s[1]})", "checkout": f" (not installed: {s[1]})"}.get(s[0], "")
    return f"lvindex {__version__}{extra}"


def outdated_hint():
    """Added to the unknown command / option errors: a skill or a document may be newer than this lvindex."""
    s = _source()
    how = {"git": "pipx upgrade lvindex, or uv tool upgrade lvindex"}.get(s[0], "upgrade lvindex the way it was installed")
    if s[0] in ("editable", "checkout"):
        how = f"update the checkout {s[1]} (git pull)"
    return f"If a skill or a document names it, this {version_text()} is older than they are: {how}."


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--project" in argv:                      # before the configuration is loaded (cache.py loads it on import)
        i = argv.index("--project")
        if i + 1 >= len(argv):
            sys.exit("--project needs a folder or an lvindex.toml")
        os.environ["LVINDEX_PROJECT"] = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    if argv[1:2] == ["--version"]:               # also outside a project: cache.py, which needs one, is not imported
        print(version_text())
        return
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        from . import config
        kind, why = config.find()
        if not kind:                             # outside a project: the core manual, and why there is no project
            from . import manual
            sys.exit(f"{manual.TEXT}\n{why}")
    from . import cache
    cache.main(argv)
