# Resource Allocation – Streamlit demo

Clickable demo of the Power Platform resource allocation solution: initiative owners submit
initiatives with FTE needs per skill type, approvers review them against capacity, and
reports show current, projected and unused FTE.

## Run

```
pip install -r requirements.txt
streamlit run app.py
```

## Demo script

1. **Signed in as Ingrid Haugen (Owner):** Home shows remaining capacity this month.
   Go to *New initiative*, fill in the business case and skill lines, and submit.
2. **Switch to Maria Lie (Approver)** in the sidebar. Open *Review queue*. "Document
   automation" goes over Developer capacity: lower the Approve values to see it fit,
   then approve as edited (with a comment).
3. Open *Reports* to see approved vs pipeline against capacity, and unused FTE per skill.
4. *Capacity* lets the approver change available FTE per skill and month.

Sample data lives in the browser session. *Reset demo data* in the sidebar restores it.
The logic mirrors the spec: one allocation row per skill line per month, joined to
capacity on a `YYYY-MM` month key.
