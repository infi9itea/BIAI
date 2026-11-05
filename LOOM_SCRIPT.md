# Loom script (about 4:40, hard limit 5:00)

Audience: a non-technical manager. Plain words. Slow and calm. Spoken lines are in quotes, screen actions in [brackets].

## Before recording
- Tabs: the live demo (zoom in once or twice), and your GitHub repo.
- Close email, chat and anything showing .env or an API key.
- Reload the demo so Pack 01 is open and the live switch is OFF.
- Do one dry run of Pack 05 with the live AI switch ON (10-40 s).

## 1. The problem (0:00 - 0:35) [demo page header]
"Hi, I'm Shafayat. I chose Task 1, checking supplier documents. A garment company gets quotes from suppliers. Someone has to check each one against what the company asked for and against company rules: price, advance payment, delivery time, late-delivery penalty, tax numbers and VAT. It's slow, and the costly mistakes are quiet ones, like a 40% advance payment nobody noticed. So I built a checker. For each document it gives one of three answers: Approve, Reject, or Needs a person, and it shows the exact sentence from the document behind every finding."

## 2. How it works (0:35 - 1:45) [click "What the checker looks for" to open the rules list]
"The most important choice: the AI never makes the decision. These are the company's rules. The AI's only job is to read the document and pull out facts, together with the sentence it found each one in. Everything after that is ordinary code, in seven fixed steps, one after another, as the brief asked.
One, read the PDF. Two, the AI extracts the facts. Three, my code checks each quote really appears in the document, and drops the fact if it doesn't. Four, code recalculates the money, because AI is unreliable at arithmetic. Five, code applies these rules. Six, code decides. Seven, it writes the report.
The decision rule is simple. A serious rule broken means Reject. Anything unclear means Needs a person. Only a fully clean document is approved. It never guesses."
[Close the rules list.]

## 3. Live demo (1:45 - 3:40)
[Point at the four tiles.] "Here are your ten sample quotes: one Approve, five Reject, four Needs a person."

**Pack 01 (already open, Needs a person):**
"This one goes to a person. The payment terms say 'within 45 days after delivery and acceptance'. That's not clearly the same as 'after delivery', so instead of guessing, it asks a human."

**Pack 02 (Approve):** [click Pack 02]
"This one is approved. All 13 checks passed." [Open "Checks that passed", then click one "View in PDF" link.] "And every check links to the exact page of the original PDF."

**Pack 05 (Reject, live AI):** [switch ON "Use the live AI for this check", click Pack 05. Keep talking while it loads.]
"Now I'm using the real AI, so this takes a few seconds. Rejected. The supplier asked for a 40% advance payment and the company limit is 20%. Here's the sentence: '40% advance payable with the purchase order.'" [Click "View in PDF" on that quote so the PDF opens at the sentence, then close it.] [Switch the live AI OFF.]

**All ten against my answer key:** [scroll to "Compare with my answer key"]
"On your ten sample files the checker matches my hand-written answer key 10 out of 10, for the decisions and for the exact rules raised. I can also export this as a CSV." [Point at the Download CSV button.]

## 4. Why I made these choices (3:40 - 4:10)
"Three reasons. Every answer has a quote, so a person can verify it in seconds. Money maths is done by code, not AI, so totals are exact. And when unsure, it stops and asks, which beats a confident wrong answer. The hardest part was getting the AI to return facts in a reliable format. At first 7 of 40 runs failed. After I forced a fixed output format, none failed."

## 5. Honest limits and next steps (4:10 - 4:40)
"What doesn't work well: it's only ten documents, and I tuned on them, so this doesn't prove it works on new ones. The answer key is my own. It also missed one field on pack 08, although the final decision was still right. Next I'd test on unseen documents, repeat the live runs to measure consistency, and have a second person check my answer key. Thank you. I'm happy to walk through the code on the call."

## If something goes wrong
- Live run slow or errors: say "the live AI is slow right now, so I'll show the saved run of the same quote", switch live OFF and click Pack 05 again. Don't re-record.
- Lost your words: pause, breathe, continue.
- Over time: cut the Pack 01 explanation to one sentence.
