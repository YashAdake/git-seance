import os
import unittest

from seance.dossier import build_dossier, commit_url_template
from seance.history import Change, Commit, classify, find_ghosts, parse_log, replay, unquote_path
from tests.helpers import TempRepo


def _c(sha, *changes, at=0):
    return Commit(sha=sha, parents=[], author="a", email="", at=at, subject=sha, changes=list(changes))


class ParsingTests(unittest.TestCase):
    def test_unquote_plain_and_octal(self):
        self.assertEqual(unquote_path("src/a.py"), "src/a.py")
        self.assertEqual(unquote_path('"caf\\303\\251.py"'), "café.py")
        self.assertEqual(unquote_path('"tab\\there"'), "tab\there")
        self.assertEqual(unquote_path('"q\\"uote"'), 'q"uote')

    def test_parse_log_records(self):
        text = ("\x1eabc123\x1fp1 p2\x1fAda\x1fa@x\x1f1700000000\x1fRemove old parser\x1d\n\n"
                "D\tsrc/old.py\nR087\tsrc/a.py\tsrc/b.py\nA\tsrc/new.py\n")
        [c] = parse_log(text)
        self.assertEqual(c.sha, "abc123")
        self.assertEqual(c.parents, ["p1", "p2"])
        self.assertEqual(c.at, 1700000000)
        self.assertEqual([(x.status, x.path, x.old_path) for x in c.changes],
                         [("D", "src/old.py", None), ("R", "src/b.py", "src/a.py"), ("A", "src/new.py", None)])

    def test_classify(self):
        self.assertEqual(classify("src/app.py"), "code")
        self.assertEqual(classify("tests/test_app.py"), "test")
        self.assertEqual(classify("vendor/lib/x.js"), "vendor")
        self.assertEqual(classify("static/logo.png"), "asset")
        self.assertEqual(classify("docs/intro.md"), "docs")
        self.assertEqual(classify("setup.cfg"), "config")
        self.assertEqual(classify(".travis.yml"), "config")
        self.assertEqual(classify("jquery.min.js"), "vendor")
        self.assertEqual(classify("Pipfile"), "config")
        self.assertEqual(classify("requests/packages/urllib3/poolmanager.py"), "vendor")
        self.assertEqual(classify("packages/core/src/index.ts"), "code")
        self.assertEqual(classify(".idea/proj.iml"), "config")


class ReplayTests(unittest.TestCase):
    def test_rename_carries_identity_and_delete_makes_a_ghost(self):
        commits = [
            _c("c1", Change("A", "a.py"), at=100),
            _c("c2", Change("R", "b.py", "a.py"), at=200),
            _c("c3", Change("D", "b.py"), at=300),
        ]
        [g] = replay(commits, head_paths=set())
        self.assertEqual(g.path, "b.py")
        self.assertEqual(g.born_sha, "c1")
        self.assertEqual(g.born_as, "a.py")
        self.assertEqual(g.former_names, ["a.py"])
        self.assertEqual(g.died_sha, "c3")

    def test_resurrected_file_is_not_a_ghost(self):
        commits = [_c("c1", Change("A", "x")), _c("c2", Change("D", "x")), _c("c3", Change("A", "x"))]
        self.assertEqual(replay(commits, head_paths={"x"}), [])

    def test_twice_dead_counts_lives_and_keeps_latest_life(self):
        commits = [_c("c1", Change("A", "x"), at=1), _c("c2", Change("D", "x"), at=2),
                   _c("c3", Change("A", "x"), at=3), _c("c4", Change("D", "x"), at=4)]
        [g] = replay(commits, head_paths=set())
        self.assertEqual((g.lives, g.born_sha, g.born_at, g.reborn_at, g.died_sha), (2, "c1", 1, 3, "c4"))

    def test_siblings_counted(self):
        commits = [_c("c1", Change("A", "x"), Change("A", "y")),
                   _c("c2", Change("D", "x"), Change("D", "y"))]
        self.assertEqual({g.siblings for g in replay(commits, set())}, {1})


class RealRepoTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()

    def tearDown(self):
        self.r.cleanup()

    def test_end_to_end_graveyard_and_dossier(self):
        r = self.r
        r.write("README.md", "# demo\n")
        r.write("src/parser.py", "def parse(s):\n    return s.split(',')\n" * 5)
        r.write("café.py", "print('olé')\n")
        born = r.commit("Add the parser", author="Ada")
        r.write("src/parser.py", "def parse(s):\n    return s.split(';')\n" * 5)
        r.commit("Use semicolons", author="Grace")
        r.git("mv", "src/parser.py", "src/legacy_parser.py")
        r.commit("Rename parser to legacy", author="Grace")
        r.write("config.py", 'API_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
        r.commit("Add config")
        os.remove(os.path.join(r.path, "src", "legacy_parser.py"))
        os.remove(os.path.join(r.path, "config.py"))
        os.remove(os.path.join(r.path, "café.py"))
        r.write("src/parser_v2.py", "import csv\n")
        died = r.commit("Replace legacy parser with csv module\n\nThe hand-rolled one broke on quotes.",
                        author="Linus")

        ghosts, stats = find_ghosts(r.path)
        by_path = {g.path: g for g in ghosts}
        self.assertEqual(set(by_path), {"src/legacy_parser.py", "config.py", "café.py"})
        g = by_path["src/legacy_parser.py"]
        self.assertEqual(g.born_sha, born)
        self.assertEqual(g.former_names, ["src/parser.py"])
        self.assertEqual(g.died_sha, died)
        self.assertEqual(g.killer, "Linus")
        self.assertEqual(g.siblings, 2)
        self.assertEqual(stats["souls"], 3)
        self.assertEqual(stats["extinctions"][0]["count"], 3)

        d = build_dossier(r.path, g, "demo")
        self.assertEqual([e.status for e in d.events], ["A", "M", "R", "D"])
        self.assertEqual(d.events[-1].body, "The hand-rolled one broke on quotes.")
        self.assertIn("split(';')", d.last_body)
        self.assertEqual(d.successors[0]["path"], "src/parser_v2.py")
        self.assertEqual({c["name"] for c in d.contributors}, {"Ada", "Grace"})
        self.assertEqual(d.events[0].sha, born)

        secret = build_dossier(r.path, by_path["config.py"], "demo")
        self.assertNotIn("AKIA", secret.last_body)
        self.assertEqual(secret.secrets, {"aws access key": 1})


class RemoteUrlTests(unittest.TestCase):
    def test_commit_url_templates(self):
        self.assertEqual(commit_url_template("git@github.com:psf/requests.git"),
                         "https://github.com/psf/requests/commit/{sha}")
        self.assertEqual(commit_url_template("https://github.com/psf/requests"),
                         "https://github.com/psf/requests/commit/{sha}")
        self.assertEqual(commit_url_template("https://gitlab.com/a/b.git"),
                         "https://gitlab.com/a/b/-/commit/{sha}")
        self.assertIsNone(commit_url_template("https://example.com/a/b.git"))
        self.assertIsNone(commit_url_template(None))


if __name__ == "__main__":
    unittest.main()
