# 60-second screen recording: Intake Webhook Flow

0:00 to 0:08. Terminal: `python3 flow.py`. "A tiny intake service: form or webhook in, sorted row out."
0:08 to 0:20. Browser at http://127.0.0.1:8787. Type "Need a license for the notes tool before a customer call today" and send. Show "Logged as INT-...".
0:20 to 0:32. Second terminal: `python3 send_samples.py`. Four requests come back labeled Access, Bug, Idea, Question.
0:32 to 0:45. Open `out/sheet.csv` in a spreadsheet app. "This is the Google Sheet step: one clean row per request."
0:45 to 0:53. `cat out/notifications.jsonl`. "And the channel ping. Urgent ones get the alarm icon. Set one variable and it posts to Slack."
0:53 to 1:00. `python3 -m unittest discover -s tests`. "Tests cover the form, the webhook and bad input."
