"""Tests for the static demo site: data export, savings, routing and JS/Python parity."""
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT.parent / "site"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))
import opsdesk  # noqa: E402

INV = opsdesk.load_inventory(ROOT / "data/inventory.csv")
TODAY = date(2026, 10, 25)
TEXTS = [
    "Glean is down and it's blocking my customer call today",
    "Can I get a Claude seat? I start on the Acme deal Monday.",
    "Is it OK to paste customer data from Gong into Claude for a QBR?",
    "Idea: could we automate renewal briefs with a Relevance agent?",
    "How do I write a good prompt for call summaries? Email me at sam@example.com",
    "There are duplicate, outdated decks in the drive",
    "Something odd happened",
    "Need a license for Notetaker X and Recap Bot, urgent",
]


class SiteData(unittest.TestCase):
    def test_site_data_has_every_section_and_is_marked_sample(self):
        d = opsdesk.site_data(ROOT, TODAY)
        self.assertTrue(d["sample"])
        for k in ["inventory", "requests", "adoption", "history", "overlaps", "savings", "prompts", "async_update", "rules"]:
            self.assertIn(k, d)
        self.assertEqual(len(d["requests"]), 9)
        self.assertTrue(all({"queue", "dri", "reply"} <= set(r) for r in d["requests"]))
        self.assertEqual(len(d["history"]["Claude"]), 4)

    def test_savings_consolidates_and_right_sizes(self):
        s = opsdesk.savings(INV, opsdesk.adoption(ROOT / "data/usage.csv", INV))
        kinds = {m["kind"]: m for m in s["moves"]}
        self.assertEqual((kinds["consolidate"]["keep"], kinds["consolidate"]["drop"]), ("Notetaker X", "Recap Bot"))
        self.assertEqual(kinds["right-size"]["tool"], "Relevance.ai")
        self.assertEqual(s["annual_savings"], sum(m["annual"] for m in s["moves"]))
        self.assertGreater(s["annual_savings"], 0)

    def test_routing(self):
        adopt = opsdesk.adoption(ROOT / "data/usage.csv", INV)
        tri = lambda txt: opsdesk.triage_rules(opsdesk.Ticket("X", "2026-10-25", "Team", txt), INV, TODAY)  # noqa: E731
        self.assertEqual(opsdesk.route(tri("Is it OK to export customer data from Gong?"), INV, adopt)["queue"], "Data governance review")
        r = opsdesk.route(tri("Can I get a Claude seat?"), INV, adopt)
        self.assertEqual((r["queue"], r["dri"]), ("Access desk", "Enterprise AI"))
        self.assertIn("24 unused seats", r["reply"])
        self.assertEqual(opsdesk.route(tri("Something odd happened"), INV, adopt)["queue"], "Ops triage (human)")

    def test_committed_site_data_is_fresh(self):
        import build
        self.assertEqual((SITE / "data.js").read_text(), build.render(),
                         "site/data.js is stale: run python3 site/build.py")

    def test_page_is_labeled_sample_and_unofficial(self):
        page = (SITE / "index.html").read_text()
        self.assertIn("Sample data", page)
        self.assertIn("Not affiliated with or endorsed by GitLab", page)
        for f in ["index.html", "app.js", "triage.js", "styles.css"]:
            self.assertNotIn("\u2014", (SITE / f).read_text(), f)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_browser_triage_matches_python(self):
        data = opsdesk.site_data(ROOT, TODAY)
        script = (
            "const T=require(process.argv[1]);const D=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "console.log(JSON.stringify(D.texts.map((x,i)=>{const t=T.triage({id:'X'+i,created:D.today,team:'Team',text:x},D);"
            "return Object.assign({category:t.category,priority:t.priority,tools:t.tools,labels:t.labels,summary:t.summary,needs_review:t.needs_review},T.route(t,D));})))"
        )
        out = subprocess.run(["node", "-e", script, str(SITE / "triage.js")], input=json.dumps({**data, "texts": TEXTS}),
                             capture_output=True, text=True, check=True).stdout
        adopt = data["adoption"]
        for txt, js in zip(TEXTS, json.loads(out)):
            py = opsdesk.triage_rules(opsdesk.Ticket("X", "2026-10-25", "Team", txt), INV, TODAY)
            want = {"category": py.category, "priority": py.priority, "tools": py.tools, "labels": py.labels,
                    "summary": py.summary, "needs_review": py.needs_review, **opsdesk.route(py, INV, adopt)}
            self.assertEqual(js, want, txt)


if __name__ == "__main__":
    unittest.main()
