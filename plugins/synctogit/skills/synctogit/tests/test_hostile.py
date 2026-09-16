"""Third wave: path escapes, awkward filenames, bulk deletions, degenerate repo states."""
import json, os, re, subprocess, sys
from harness import eq, is_in, sha, SCRIPTS
from test_guardrail import make_tiff


def hostile01_path_escape_refused(s):
    r = s.repo("hostile01")
    outside = os.path.join(s.base, "outside.md")
    open(outside, "w").write("not part of the project\n")
    r.write("ok.md", "fine\n")
    rep, rc = r.apply({"approved_paths": ["../outside.md", "ok.md", "/etc/hosts"],
                       "message": "sneak"}, ["--no-push"])
    eq(rc, 0, rep)
    eq(r.git("show", "--name-only", "--format=", "HEAD").stdout.split(), ["ok.md"],
       "paths outside the project must be dropped, not committed")
    eq(open(outside).read(), "not part of the project\n", "and left untouched")


def hostile02_git_internals_refused(s):
    r = s.repo("hostile02")
    cfg = open(r.path(".git/config")).read()
    rep, rc = r.apply({"approved_paths": [".git/config"], "message": "touch internals",
                       "archive_moves": [{"src": ".git/config", "dest": "_archive/config",
                                          "consent": True}]}, ["--no-push"])
    eq(rc, 2, "anything addressing .git must be refused")
    eq(open(r.path(".git/config")).read(), cfg)


def hostile03_output_escape_refused(s):
    r = s.repo("hostile03")
    make_tiff(r, "fig.tiff", 300, 200)
    stage = os.path.join(s.base, "st-h03")
    out, _ = r.convert("image", "fig.tiff", stage)
    victim = os.path.join(s.base, "victim.jpg")
    open(victim, "w").write("someone else's file\n")
    rep, rc = r.apply({"approved_paths": [], "message": "x",
                       "outputs": [{"src": "fig.tiff", "staged": out["staged"],
                                    "output": "../victim.jpg",
                                    "source_hash": out["source_hash"],
                                    "source_size": out["source_size"]}]}, ["--no-push"])
    eq(open(victim).read(), "someone else's file\n", "an output may never escape the project")
    assert any("outside the project" in x for x in rep["refused"]), rep


def hostile04_output_collision_not_overwritten(s):
    r = s.repo("hostile04")
    make_tiff(r, "fig.tiff", 300, 200)
    r.write("fig.preview.jpg", b"a real file the user made themselves")
    victim = sha(r.path("fig.preview.jpg"))
    stage = os.path.join(s.base, "st-h04")
    out, _ = r.convert("image", "fig.tiff", stage)
    rep, rc = r.apply({"approved_paths": [], "message": "x",
                       "outputs": [{"src": "fig.tiff", "staged": out["staged"],
                                    "output": out["output"],
                                    "source_hash": out["source_hash"],
                                    "source_size": out["source_size"]}]}, ["--no-push"])
    eq(sha(r.path("fig.preview.jpg")), victim,
       "an existing file that is not our previous output must never be overwritten")
    assert any("refusing to overwrite" in x for x in rep["refused"]), rep


def hostile05_awkward_filenames(s):
    r = s.repo("hostile05")
    names = ["with space.md", "quo'te.md", 'dou"ble.md', "--force.md", "-rf.md",
             "uniçodeé.md", "semi;colon.md", "dollar$sign.md", "back\\slash.md",
             "star*.md", "brack[et].md", "new\nline.md"]
    made = []
    for n in names:
        try:
            r.write(n, "content\n")
            made.append(n)
        except OSError:
            pass
    sc = r.scan()
    paths = [i["path"] for i in sc["items"]]
    for n in made:
        assert n in paths, "scan must report %r verbatim, got %r" % (n, paths)
    rep, rc = r.apply({"approved_paths": made, "message": "add awkward names"}, ["--no-push"])
    eq(rc, 0, rep)
    committed = set(r.git("show", "--name-only", "--format=", "-z", "HEAD"
                          ).stdout.split("\0")) - {""}
    for n in made:
        assert n in committed, "%r must be committed as itself, got %r" % (n, committed)


def hostile06_flag_like_path_not_a_flag(s):
    r = s.repo("hostile06")
    r.write("--version", "not a flag\n")
    r.write("real.md", "x\n")
    rep, rc = r.apply({"approved_paths": ["--version", "real.md"], "message": "add"},
                      ["--no-push"])
    eq(rc, 0, rep)
    committed = set(r.git("show", "--name-only", "--format=", "-z", "HEAD"
                          ).stdout.split("\0")) - {""}
    eq(committed, {"--version", "real.md"},
       "a filename that looks like a flag must be passed as a path")


def hostile07_bulk_deletions(s):
    """50 user deletions among 200 dummy files: all 50 propagate, the rest are untouched."""
    r = s.repo("hostile07")
    for i in range(200):
        r.write("data/file%03d.md" % i, "content %d\n" % i)
    r.commit_all("200 dummy files")
    r.git("push", "-q")
    doomed = ["data/file%03d.md" % i for i in range(0, 200, 4)]
    for d in doomed:
        os.remove(r.path(d))
    sc = r.scan()
    reported = [i["path"] for i in sc["items"] if i["category"] == "deletion"]
    eq(sorted(reported), sorted(doomed), "every user deletion must be reported")
    rep, rc = r.apply({"approved_paths": doomed, "message": "remove 50 obsolete files"}, [])
    eq(rc, 0, rep)
    head = r.head_files()
    for d in doomed:
        assert d not in head, "%s should be gone from Git" % d
    for i in range(200):
        p = "data/file%03d.md" % i
        if p not in doomed:
            assert p in head, "%s must survive" % p
            assert os.path.exists(r.path(p)), "%s must still exist locally" % p
    eq(len(head), 200 - 50 + 1)      # + README.md


def hostile08_partial_deletion_approval(s):
    """Approving some deletions must not sweep up the others."""
    r = s.repo("hostile08")
    for n in ("a.md", "b.md", "c.md"):
        r.write(n, "x\n")
    r.commit_all("three")
    for n in ("a.md", "b.md", "c.md"):
        os.remove(r.path(n))
    rep, rc = r.apply({"approved_paths": ["a.md"], "message": "remove a only"}, ["--no-push"])
    eq(rc, 0, rep)
    head = r.head_files()
    assert "a.md" not in head
    assert "b.md" in head and "c.md" in head, \
        "unapproved deletions must stay out of the commit"


def hostile09_no_commits_yet(s):
    r = s.repo("empty", with_remote=False)
    r.git("update-ref", "-d", "refs/heads/main", check=False)
    r.git("rm", "-q", "--cached", "README.md", check=False)
    r.write("first.md", "hello\n")
    rep, rc = r.apply({"approved_paths": ["first.md"], "message": "first commit"},
                      ["--no-push", "--no-integrate"])
    assert rc in (0, 2), "an unborn branch must be handled, not crash: %r" % rep
    if rc == 0:
        is_in("first.md", rep["committed"])


def hostile10_detached_head_refused(s):
    r = s.repo("hostile10")
    r.write("a.md", "one\n")
    r.commit_all("one")
    r.write("a.md", "two\n")
    r.commit_all("two")
    r.git("checkout", "-q", "HEAD~1")
    r.write("b.md", "on a detached head\n")
    rep, rc = r.apply({"approved_paths": ["b.md"], "message": "b"}, ["--no-push"])
    eq(rc, 2, "a detached HEAD must be explained, not silently committed")
    is_in("detached HEAD", " ".join(rep["refused"]))
    assert os.path.exists(r.path("b.md")), "the user's file is untouched"


def hostile11_broken_symlink(s):
    r = s.repo("hostile11")
    os.symlink(os.path.join(s.base, "nowhere", "missing.md"), r.path("dangling.md"))
    sc = r.scan()
    it = r.item(sc, "dangling.md")
    assert it is not None, "a dangling symlink must be reported, not crash the scan"
    eq(it["decision"], "exclude")
    assert os.path.islink(r.path("dangling.md")), "and left alone"


def hostile12_message_metacharacters(s):
    r = s.repo("hostile12")
    r.write("a.md", "x\n")
    nasty = 'update "notes"; rm -rf $HOME && echo `whoami`\nsecond line'
    rep, rc = r.apply({"approved_paths": ["a.md"], "message": nasty}, ["--no-push"])
    eq(rc, 0, rep)
    assert os.path.isdir(os.path.expanduser("~")), "obviously"
    subject = r.git("log", "-1", "--format=%B").stdout
    is_in("rm -rf $HOME", subject, "the message is data, never executed")
    assert os.path.exists(r.path("a.md"))


def hostile13_no_forbidden_git_commands_in_source(s):
    """Static guard: destructive git arguments must not appear in executable code.

    Checks the argument-list form the scripts actually use (git(root, "reset", "--hard")),
    not just prose, and ignores docstrings and comments so documenting the ban is allowed.
    """
    import ast as _ast, io as _io, tokenize as _tok
    banned_args = {"reset", "clean", "restore", "checkout", "filter-branch",
                   "--hard", "--force", "-f", "-A", "--all"}
    offenders = []
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(SCRIPTS, name)
        src = open(path).read()
        tree = _ast.parse(src)
        # blank out docstrings and comments: naming a forbidden command in prose is fine
        skip = set()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                                 _ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], _ast.Expr) and \
                        isinstance(getattr(body[0], "value", None), _ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    skip.update(range(body[0].lineno, (body[0].end_lineno or
                                                       body[0].lineno) + 1))
        for t in _tok.generate_tokens(_io.StringIO(src).readline):
            if t.type == _tok.COMMENT:
                skip.add(t.start[0])
        for node in _ast.walk(tree):
            # only arguments actually handed to a git invocation count
            if not isinstance(node, _ast.Call):
                continue
            fn = node.func
            target = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if target not in ("git", "sh", "run", "Popen", "check_output", "call"):
                continue
            strings = []
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, _ast.Constant) and isinstance(arg.value, str):
                    strings.append((arg.lineno, arg.value))
                elif isinstance(arg, (_ast.List, _ast.Tuple)):
                    for el in arg.elts:
                        if isinstance(el, _ast.Constant) and isinstance(el.value, str):
                            strings.append((el.lineno, el.value))
                elif isinstance(arg, _ast.BinOp):        # ["git", ...] + list(args)
                    for side in (arg.left, arg.right):
                        if isinstance(side, (_ast.List, _ast.Tuple)):
                            for el in side.elts:
                                if isinstance(el, _ast.Constant) and \
                                        isinstance(el.value, str):
                                    strings.append((el.lineno, el.value))
            for lineno, val in strings:
                if lineno in skip:
                    continue
                if val in banned_args:
                    offenders.append("%s:%d passes %r to a git invocation"
                                     % (name, lineno, val))
    eq(offenders, [], "destructive git arguments must not appear in executable code")


def hostile14_scan_never_writes(s):
    """A scan must be side-effect free, including on a repo full of odd content."""
    r = s.repo("hostile14")
    r.size_file("reads.fastq", 1000)
    make_tiff(r, "fig.tiff", 200, 150)
    r.write("notes.md", "x\n")
    r.commit_all("base")
    before = {}
    for dirpath, dirs, files in os.walk(r.dir):
        if ".git" in dirpath:
            continue
        for f in files:
            p = os.path.join(dirpath, f)
            before[p] = sha(p)
    head = r.git("rev-parse", "HEAD").stdout
    r.scan()
    r.scan()
    after = {}
    for dirpath, dirs, files in os.walk(r.dir):
        if ".git" in dirpath:
            continue
        for f in files:
            p = os.path.join(dirpath, f)
            after[p] = sha(p)
    eq(after, before, "scan must not create, delete, or modify any file")
    eq(r.git("rev-parse", "HEAD").stdout, head, "and must not move HEAD")


def hostile15_dry_run_changes_nothing(s):
    r = s.repo("hostile15")
    r.write("a.md", "x\n")
    head = r.git("rev-parse", "HEAD").stdout
    rep, rc = r.apply({"approved_paths": ["a.md"], "message": "a"}, ["--dry-run"])
    eq(rc, 0, rep)
    eq(r.git("rev-parse", "HEAD").stdout, head, "--dry-run must not commit")
    assert "a.md" not in r.tracked()


TESTS = [
    ("HOSTILE-01 path escape refused", hostile01_path_escape_refused),
    ("HOSTILE-02 .git internals refused", hostile02_git_internals_refused),
    ("HOSTILE-03 output escape refused", hostile03_output_escape_refused),
    ("HOSTILE-04 output collision not overwritten", hostile04_output_collision_not_overwritten),
    ("HOSTILE-05 awkward filenames", hostile05_awkward_filenames),
    ("HOSTILE-06 flag-like path", hostile06_flag_like_path_not_a_flag),
    ("HOSTILE-07 bulk deletions (200 files)", hostile07_bulk_deletions),
    ("HOSTILE-08 partial deletion approval", hostile08_partial_deletion_approval),
    ("HOSTILE-09 repository with no commits", hostile09_no_commits_yet),
    ("HOSTILE-10 detached HEAD refused", hostile10_detached_head_refused),
    ("HOSTILE-11 broken symlink", hostile11_broken_symlink),
    ("HOSTILE-12 message metacharacters", hostile12_message_metacharacters),
    ("HOSTILE-13 no destructive commands in source", hostile13_no_forbidden_git_commands_in_source),
    ("HOSTILE-14 scan never writes", hostile14_scan_never_writes),
    ("HOSTILE-15 dry run changes nothing", hostile15_dry_run_changes_nothing),
]
