import csv
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp()
os.environ["FLOW_OUT"] = TMP
os.environ.pop("FLOW_LLM_URL", None)
os.environ.pop("NOTIFY_WEBHOOK_URL", None)
import flow  # noqa: E402


class Classify(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(flow.classify("Can I get access to the CRM?")["category"], "Access")
        self.assertEqual(flow.classify("The export is broken, error 500")["category"], "Bug")
        self.assertEqual(flow.classify("Idea: automate the weekly report")["category"], "Idea")
        self.assertEqual(flow.classify("hello there")["category"], "Other")

    def test_urgency(self):
        self.assertEqual(flow.classify("Blocked on a customer call today, error")["urgency"], "High")
        self.assertEqual(flow.classify("Idea for later")["urgency"], "Normal")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = flow.serve(0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def post(self, data, ctype="application/json"):
        body = json.dumps(data).encode() if "json" in ctype else data.encode()
        req = urllib.request.Request(self.base + "/intake", data=body, headers={"Content-Type": ctype})
        return urllib.request.urlopen(req)

    def test_json_webhook_writes_row_and_notification(self):
        with self.post({"name": "A", "team": "Sales", "message": "Need a seat for the notes tool"}) as r:
            self.assertEqual(r.status, 201)
            row = json.load(r)
        self.assertEqual(row["category"], "Access")
        with open(Path(TMP) / "sheet.csv") as f:
            rows = list(csv.DictReader(f))
        self.assertIn(row["request_id"], [x["request_id"] for x in rows])
        notes = (Path(TMP) / "notifications.jsonl").read_text()
        self.assertIn(row["request_id"], notes)

    def test_form_post(self):
        with self.post("name=B&team=CS&message=Dashboard+is+broken", "application/x-www-form-urlencoded") as r:
            self.assertIn("Logged as", r.read().decode())

    def test_missing_message_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"name": "C"})
        self.assertEqual(e.exception.code, 400)

    def test_gitlab_issue_hook(self):
        event = {"object_kind": "issue", "user": {"username": "jdoe"},
                 "project": {"path_with_namespace": "field/ai-tools"},
                 "object_attributes": {"iid": 7, "title": "Need a Claude seat", "description": "For a customer call today"}}
        req = urllib.request.Request(self.base + "/intake", data=json.dumps(event).encode(),
                                     headers={"Content-Type": "application/json", "X-Gitlab-Event": "Issue Hook"})
        with urllib.request.urlopen(req) as r:
            row = json.load(r)
        self.assertEqual(row["source"], "gitlab:field/ai-tools#7")
        self.assertEqual((row["category"], row["urgency"]), ("Access", "High"))
        self.assertEqual(row["route_to"], "IT / access requests")

    def test_gitlab_bad_token_rejected(self):
        os.environ["GITLAB_WEBHOOK_SECRET"] = "s3cret"
        try:
            req = urllib.request.Request(self.base + "/intake", data=b"{}", headers={
                "Content-Type": "application/json", "X-Gitlab-Event": "Issue Hook", "X-Gitlab-Token": "wrong"})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 401)
        finally:
            os.environ.pop("GITLAB_WEBHOOK_SECRET")

    def test_form_page(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("<form", r.read().decode())


if __name__ == "__main__":
    unittest.main()
