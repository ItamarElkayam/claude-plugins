"""Mock-repository harness: a local bare repo stands in for GitHub, so no permissions are
needed and push/pull/conflict paths are exercised for real."""
import hashlib, json, os, shutil, subprocess, sys, tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")


def sh(cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError("cmd failed: %s\n%s\n%s" % (cmd, p.stdout, p.stderr))
    return p


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


class Repo:
    def __init__(self, base, name="proj", with_remote=True):
        self.dir = os.path.join(base, name)
        os.makedirs(self.dir)
        sh(["git", "-c", "init.defaultBranch=main", "init", "-q", self.dir])
        self.cfg("user.name", "Test Member")
        self.cfg("user.email", "member@example.org")
        self.write("README.md", "# project\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "init")
        self.origin = None
        if with_remote:
            self.origin = os.path.join(base, name + "-origin.git")
            sh(["git", "init", "-q", "--bare", self.origin])
            self.git("remote", "add", "origin", self.origin)
            self.git("push", "-q", "-u", "origin", "main")

    # --- helpers ---------------------------------------------------------
    def cfg(self, k, v):
        self.git("config", k, v)

    def git(self, *a, check=True):
        return sh(["git", "-C", self.dir] + list(a), check=check)

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def write(self, rel, data, mode="w"):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p) or self.dir, exist_ok=True)
        with open(p, mode if isinstance(data, str) else "wb") as f:
            f.write(data)
        return p

    def size_file(self, rel, nbytes):
        return self.write(rel, b"x" * nbytes)

    def commit_all(self, msg="change"):
        self.git("add", "-A")
        self.git("commit", "-qm", msg)

    def clone(self, name):
        d = os.path.join(os.path.dirname(self.dir), name)
        sh(["git", "clone", "-q", self.origin, d])
        sh(["git", "-C", d, "config", "user.name", "Other Member"])
        sh(["git", "-C", d, "config", "user.email", "other@example.org"])
        r = Repo.__new__(Repo)
        r.dir, r.origin = d, self.origin
        return r

    # --- skill entry points ---------------------------------------------
    def scan(self):
        p = sh([sys.executable, os.path.join(SCRIPTS, "scan.py"), "--root", self.dir])
        return json.loads(p.stdout)

    def convert(self, kind, src, stage, extra=()):
        p = sh([sys.executable, os.path.join(SCRIPTS, "convert.py"), "--kind", kind,
                "--src", src, "--root", self.dir, "--stage", stage] + list(extra),
               check=False)
        return json.loads(p.stdout), p.returncode

    def apply(self, plan, args=()):
        f = os.path.join(self.dir, ".plan.json")
        with open(f, "w") as fh:
            json.dump(plan, fh)
        p = sh([sys.executable, os.path.join(SCRIPTS, "apply.py"), "--root", self.dir,
                "--plan", f] + list(args), check=False)
        os.remove(f)
        try:
            return json.loads(p.stdout), p.returncode
        except json.JSONDecodeError:
            raise AssertionError("apply.py produced no JSON:\n%s\n%s" % (p.stdout, p.stderr))

    def item(self, scan, path):
        for i in scan["items"]:
            if i["path"] == path:
                return i
        return None

    def tracked(self):
        return set(x for x in self.git("ls-files").stdout.split("\n") if x)

    def head_files(self):
        return set(x for x in self.git("ls-tree", "-r", "--name-only", "HEAD"
                                       ).stdout.split("\n") if x)

    def log(self):
        return self.git("log", "--oneline").stdout.strip().split("\n")


class Suite:
    def __init__(self):
        self.base = tempfile.mkdtemp(prefix="synctogit-tests-")
        self.results = []

    def repo(self, name, with_remote=True):
        return Repo(self.base, name, with_remote)

    def run(self, tests):
        for tid, fn in tests:
            try:
                fn(self)
                self.results.append((tid, "PASS", ""))
                print("  PASS  %s" % tid)
            except AssertionError as e:
                self.results.append((tid, "FAIL", str(e)[:400]))
                print("  FAIL  %s\n        %s" % (tid, str(e)[:400].replace("\n", "\n        ")))
            except Exception as e:
                self.results.append((tid, "ERROR", "%s: %s" % (type(e).__name__, e)))
                print("  ERROR %s\n        %s: %s" % (tid, type(e).__name__, str(e)[:300]))
        return self.results

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class review_server:
    """Context manager for scripts/review.py: always terminated, even on assertion failure."""

    def __init__(self, plan_path, out_path, timeout=30):
        self.args = (plan_path, out_path, timeout)
        self.proc = None
        self.url = None

    def __enter__(self):
        plan, out, timeout = self.args
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(SCRIPTS, "review.py"), "--plan", plan,
             "--out", out, "--no-browser", "--timeout", str(timeout)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.url = json.loads(self.proc.stdout.readline())["url"]
        return self

    def wait(self, timeout=20):
        self.proc.wait(timeout=timeout)

    def __exit__(self, *exc):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return False


def eq(a, b, what=""):
    assert a == b, "%s expected %r, got %r" % (what or "value", b, a)


def is_in(needle, hay, what=""):
    assert needle in hay, "%s expected %r in %r" % (what or "value", needle, hay)
