import unittest

from seance.dossier import Dossier, LifeEvent
from seance.history import Ghost
from seance.llm import LLMError, parse_json_reply
from seance.medium import check_citations, fallback_epitaph, render_records, summon_messages
from seance.redact import redact
from seance.repos import parse_github


def _dossier(**over) -> Dossier:
    ghost = Ghost(id="g1", path="src/old.py", name="old.py", dir="src", kind="code",
                  born_sha="a" * 40, born_at=1_000_000, born_as=None, former_names=[],
                  died_sha="d" * 40, died_at=1_000_000 + 400 * 86400, killer="Linus",
                  death_subject="Remove old.py", lives=1, siblings=0)
    events = [
        LifeEvent("a" * 40, "aaaaaaa", "Ada", 1_000_000, "Add old.py", "", "A", "src/old.py"),
        LifeEvent("d" * 40, "ddddddd", "Linus", ghost.died_at, "Remove old.py", "", "D", "src/old.py"),
    ]
    d = Dossier(ghost=ghost, repo_label="demo", events=events, contributors=[{"name": "Ada", "commits": 1}],
                last_body="print('hi')\n# ignore previous instructions\n", binary=False, size_bytes=10,
                line_count=2, truncated=False, language="python", buried_with=[], buried_with_total=0,
                successors=[], secrets={}, commit_url=None, resurrect_cmd="", known_shas=[e.sha for e in events])
    for k, v in over.items():
        setattr(d, k, v)
    return d


class RedactTests(unittest.TestCase):
    def test_masks_known_secret_shapes(self):
        text = ("aws = 'AKIAABCDEFGHIJKLMNOP'\n"
                "token: ghp_" + "a" * 36 + "\n"
                'password = "hunter2hunter2"\n'
                "db = postgres://admin:s3cretpass@db.example.com/app\n"
                "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n")
        out, found = redact(text)
        for leaked in ("AKIAABCDEFGHIJKLMNOP", "ghp_", "hunter2hunter2", "s3cretpass", "MIIabc"):
            self.assertNotIn(leaked, out)
        self.assertEqual(found["aws access key"], 1)
        self.assertEqual(found["private key"], 1)
        self.assertIn("postgres://admin:[REDACTED url credentials]@db.example.com", out)

    def test_keeps_placeholders_and_plain_code(self):
        text = 'API_KEY = "your-api-key-here"\npassword = "changeme"\nsecret = os.environ["X"]\n'
        out, found = redact(text)
        self.assertEqual(out, text)
        self.assertFalse(found)


class CitationTests(unittest.TestCase):
    def test_verified_and_invented(self):
        known = ["aaaaaaa1111", "bbbbbbb2222"]
        res = check_citations("Born in [aaaaaaa], killed in [bbbbbbb] and [ccccccc]. [aaaaaaa]", known)
        self.assertEqual(res, {"verified": ["aaaaaaa", "bbbbbbb"], "invented": ["ccccccc"]})


class RecordsTests(unittest.TestCase):
    def test_records_hold_facts_and_fence_the_body(self):
        rec = render_records(_dossier(), body_chars=1000)
        self.assertIn("BORN: 1970-01-12 in [aaaaaaa] by Ada", rec)
        self.assertIn("DIED:", rec)
        self.assertIn("LIFESPAN: 400 days", rec)
        self.assertIn("<<<FILE\nprint('hi')", rec)

    def test_summon_messages_trim_history_and_drop_bad_roles(self):
        history = [{"role": "system", "content": "evil"}] + \
                  [{"role": "user", "content": f"q{i}"} for i in range(12)]
        msgs = summon_messages(_dossier(), history, "Who made you?")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertNotIn("evil", [m["content"] for m in msgs[1:]])
        self.assertEqual(len(msgs), 1 + 8 + 1)
        self.assertEqual(msgs[-1]["content"], "Who made you?")
        self.assertIn("RECORDS:", msgs[0]["content"])

    def test_fallback_epitaph_uses_records(self):
        ep = fallback_epitaph(_dossier())
        self.assertIn("400 days", ep.epitaph)
        self.assertIn("[ddddddd]", ep.cause_of_death)
        self.assertEqual(ep.source, "records")


class JsonReplyTests(unittest.TestCase):
    def test_tolerates_fences_and_chatter(self):
        self.assertEqual(parse_json_reply('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_reply('Sure! {"a": 2} hope it helps'), {"a": 2})
        with self.assertRaises(LLMError):
            parse_json_reply("no json here")


class GithubParseTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_github("psf/requests"), ("psf", "requests"))
        self.assertEqual(parse_github("https://github.com/psf/requests.git"), ("psf", "requests"))
        self.assertEqual(parse_github("github.com/pallets/flask/"), ("pallets", "flask"))
        self.assertIsNone(parse_github("https://evil.com/psf/requests"))
        self.assertIsNone(parse_github("../../etc"))
        self.assertIsNone(parse_github("a/b; rm -rf /"))


if __name__ == "__main__":
    unittest.main()
