import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import opsdesk  # noqa: E402

INV = opsdesk.load_inventory(ROOT / "data/inventory.csv")
TODAY = date(2026, 10, 25)


def t(text, created="2026-10-25"):
    return opsdesk.triage_rules(opsdesk.Ticket("X", created, "Team", text), INV, TODAY)


class Rules(unittest.TestCase):
    def test_access_request_detects_tool(self):
        r = t("Can I get a Claude seat for next week?")
        self.assertEqual(r.category, "access_request")
        self.assertIn("Claude", r.tools)
        self.assertIn("tool::Claude", r.labels)

    def test_urgent_issue_is_p1(self):
        r = t("Glean is down and it's blocking my customer call today")
        self.assertEqual((r.category, r.priority), ("issue", "P1"))

    def test_old_access_request_escalates(self):
        self.assertEqual(t("Need access to Glean please", created="2026-10-20").priority, "P2")

    def test_governance_flagged_for_review(self):
        r = t("Is it OK to export customer data from Gong into Claude?")
        self.assertEqual(r.category, "governance")
        self.assertTrue(r.needs_review)
        self.assertIn("review::data-governance", r.labels)

    def test_unknown_goes_to_human(self):
        r = t("Something odd happened")
        self.assertEqual(r.category, "other")
        self.assertTrue(r.needs_review)

    def test_email_redacted_in_summary(self):
        self.assertNotIn("@", t("Glean error, reach me at a.b@example.com").summary)

    def test_llm_hook_off_by_default(self):
        r = opsdesk.triage_llm(t("Need a Claude seat"))
        self.assertEqual(r.engine, "rules")


class Reports(unittest.TestCase):
    def test_adoption_flags_and_overlap(self):
        a = {r["tool"]: r for r in opsdesk.adoption(ROOT / "data/usage.csv", INV)}
        self.assertEqual(a["Relevance.ai"]["flag"], "underused")
        self.assertEqual(a["Claude"]["flag"], "healthy")
        self.assertIn(("meeting notes", ["Notetaker X", "Recap Bot"]), opsdesk.overlaps(INV))

    def test_end_to_end_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            opsdesk.main(["--out", d, "--today", "2026-10-25"])
            for f in ["report.md", "dashboard.html", "triaged.csv", "triaged.json", "async-update.md"]:
                self.assertTrue((Path(d) / f).exists(), f)
            data = json.loads((Path(d) / "triaged.json").read_text())
            self.assertEqual(len(data), 9)


class GitLabFlavor(unittest.TestCase):
    def test_label_plan_is_scoped_and_dry(self):
        tk = t("Can I get a Claude seat?")
        tk.id = "#42"
        calls = opsdesk.label_calls([tk], "group/ai-ops", base="https://gitlab.example.com")
        self.assertEqual(calls[0]["method"], "PUT")
        self.assertTrue(calls[0]["url"].endswith("/projects/group%2Fai-ops/issues/42"))
        self.assertIn("type::access_request", calls[0]["body"]["add_labels"])

    def test_prompt_registry_flags_orphans_and_stale(self):
        rows = {r["name"]: r["status"] for r in opsdesk.prompt_health(ROOT / "data/prompts.csv", TODAY)}
        self.assertEqual(rows["Account plan first draft"], "ok")
        self.assertIn("no owner", rows["Outbound sequence agent"])
        self.assertIn("review overdue", rows["Outbound sequence agent"])
        self.assertIn("unversioned", rows["QBR notes summarizer"])
        self.assertIn("never reviewed", rows["Competitive talk track"])

    def test_async_update_has_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            opsdesk.main(["--out", d, "--today", "2026-10-25"])
            text = (Path(d) / "async-update.md").read_text()
        self.assertIn("## Decisions needed", text)
        self.assertIn("Consolidate meeting notes?", text)


if __name__ == "__main__":
    unittest.main()
