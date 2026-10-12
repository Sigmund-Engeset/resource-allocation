"""Project Resource Allocation - Streamlit demo.

Mirrors the Power Platform design: initiatives with skill lines are spread
into monthly allocation rows, approvers review them against capacity per
skill and month, and reporting shows current, projected and unused FTE.

Data lives in st.session_state (sample data, resets per browser session).
Run with:  streamlit run app.py
"""

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Resource Allocation (demo)", layout="wide")

SKILLS = ["Developer", "Data Analyst", "Project Manager"]
USERS = {"Ingrid Haugen": "Owner", "Lars Berg": "Owner", "Maria Lie": "Approver"}
DEPARTMENTS = ["Retail Banking", "Risk", "Operations", "Finance"]
STRATEGIC_FIT = ["Customer experience", "Efficiency", "Regulatory", "Growth"]
PRIORITIES = ["High", "Medium", "Low"]
OPEN_STATUSES = ("Submitted", "On Hold")

STATUS_COLOURS = {
    "Submitted": "background-color:#EEF0F3;color:#3D434B",
    "On Hold": "background-color:#EEF0F3;color:#3D434B",
    "Approved": "background-color:#E3EEFC;color:#174A94",
    "Partially Approved": "background-color:#FDEBD8;color:#8A3B00",
    "Rejected": "background-color:#FBE4E2;color:#8F1D14",
}


# --------------------------------------------------------------------------
# Month helpers. Months are handled as "YYYY-MM" keys (the MonthKey idea from
# the spec), so there are no time-zone or day-of-month surprises.
# --------------------------------------------------------------------------
def first_of_month(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)


def add_months(d: dt.date, n: int) -> dt.date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return dt.date(y, m, 1)


def month_keys(start: dt.date, end: dt.date) -> list[str]:
    cur, last, out = first_of_month(start), first_of_month(end), []
    while cur <= last:
        out.append(cur.strftime("%Y-%m"))
        cur = add_months(cur, 1)
    return out


def month_label(key: str) -> str:
    return dt.datetime.strptime(key, "%Y-%m").strftime("%b %Y")


def current_month_key() -> str:
    return dt.date.today().strftime("%Y-%m")


# --------------------------------------------------------------------------
# "Database" operations (equivalents of the SharePoint lists and the flows)
# --------------------------------------------------------------------------
def add_initiative(db, *, title, description, business_case, benefit, fit, department,
                   priority, owner, lines, submitted=None):
    """Create the initiative, its request lines and the monthly allocation rows
    (what the Power App plus Flow 1 'GenerateAllocations' do)."""
    iid = db["next_init"]
    db["next_init"] += 1
    db["initiatives"].append({
        "ID": iid, "Title": title, "Description": description,
        "BusinessCase": business_case, "ExpectedBenefit": benefit,
        "StrategicFit": fit, "Department": department, "Priority": priority,
        "Owner": owner,
        "StartDate": min(l["start"] for l in lines),
        "EndDate": max(l["end"] for l in lines),
        "Status": "Submitted", "Submitted": submitted or dt.date.today(),
        "DecisionBy": None, "DecisionDate": None, "DecisionComment": "",
    })
    for line in lines:
        rid = db["next_req"]
        db["next_req"] += 1
        db["requests"].append({
            "RequestID": rid, "InitiativeID": iid, "SkillType": line["skill"],
            "FTE": float(line["fte"]), "StartDate": line["start"], "EndDate": line["end"],
        })
        for key in month_keys(line["start"], line["end"]):
            db["allocations"].append({
                "InitiativeID": iid, "RequestID": rid, "SkillType": line["skill"],
                "MonthKey": key, "FTERequested": float(line["fte"]),
                "FTEApproved": 0.0, "Status": "Pending",
            })
    return iid


def decide(db, iid, decision, approver, comment="", approved=None):
    """Record an approver decision (the review screen's Patch calls).

    decision: 'full', 'edited', 'hold' or 'reject'.
    approved: {(RequestID, MonthKey): fte} for 'edited'.
    """
    allocs = [a for a in db["allocations"] if a["InitiativeID"] == iid]
    if decision == "full":
        status = "Approved"
        for a in allocs:
            a["FTEApproved"], a["Status"] = a["FTERequested"], "Approved"
    elif decision == "edited":
        partial = False
        for a in allocs:
            val = float(approved.get((a["RequestID"], a["MonthKey"]), a["FTERequested"]))
            partial = partial or val < a["FTERequested"]
            a["FTEApproved"], a["Status"] = val, "Approved"
        status = "Partially Approved" if partial else "Approved"
    elif decision == "reject":
        status = "Rejected"
        for a in allocs:
            a["FTEApproved"], a["Status"] = 0.0, "Rejected"
    else:
        status = "On Hold"
    init = get_initiative(db, iid)
    init.update({"Status": status, "DecisionBy": approver,
                 "DecisionDate": dt.date.today(), "DecisionComment": comment})
    return status


def get_initiative(db, iid):
    return next(i for i in db["initiatives"] if i["ID"] == iid)


def allocations_df(db) -> pd.DataFrame:
    cols = ["InitiativeID", "RequestID", "SkillType", "MonthKey",
            "FTERequested", "FTEApproved", "Status"]
    return pd.DataFrame(db["allocations"], columns=cols)


def summary_df(db) -> pd.DataFrame:
    """Skill x month grain: Capacity, Approved, Pipeline, Unused (the Power BI measures)."""
    cap = pd.DataFrame(db["capacity"])
    al = allocations_df(db)
    appr = (al[al.Status == "Approved"]
            .groupby(["SkillType", "MonthKey"], as_index=False)["FTEApproved"].sum())
    pipe = (al[al.Status == "Pending"]
            .groupby(["SkillType", "MonthKey"], as_index=False)["FTERequested"].sum())
    df = (cap.merge(appr, on=["SkillType", "MonthKey"], how="left")
             .merge(pipe, on=["SkillType", "MonthKey"], how="left")
             .fillna(0.0)
             .rename(columns={"AvailableFTE": "Capacity", "FTEApproved": "Approved",
                              "FTERequested": "Pipeline"}))
    df["Unused"] = df["Capacity"] - df["Approved"]
    df["Month"] = df["MonthKey"].map(month_label)
    return df


def fte_months(db, iid):
    al = allocations_df(db)
    al = al[al.InitiativeID == iid]
    approved = al.loc[al.Status == "Approved", "FTEApproved"].sum()
    return float(al["FTERequested"].sum()), float(approved)


def skills_text(db, iid):
    reqs = [r for r in db["requests"] if r["InitiativeID"] == iid]
    return " · ".join(f"{r['FTE']:g} {r['SkillType']}" for r in reqs)


# --------------------------------------------------------------------------
# Sample data
# --------------------------------------------------------------------------
def seed_db():
    m0 = first_of_month(dt.date.today())
    horizon = [add_months(m0, i).strftime("%Y-%m") for i in range(12)]
    base_cap = {"Developer": 8.0, "Data Analyst": 4.0, "Project Manager": 3.0}
    db = {
        "initiatives": [], "requests": [], "allocations": [],
        "capacity": [{"SkillType": s, "MonthKey": k, "AvailableFTE": base_cap[s]}
                     for s in SKILLS for k in horizon],
        "next_init": 1, "next_req": 1,
    }

    def end(n):  # last day of month m0+n
        return add_months(m0, n + 1) - dt.timedelta(days=1)

    def line(skill, fte, a, b):
        return {"skill": skill, "fte": fte, "start": add_months(m0, a), "end": end(b)}

    case = ("Manual handling today takes significant analyst time each week and creates "
            "errors that customers notice. The initiative removes the manual steps and gives "
            "the business a single, reliable process.")
    seeds = [
        ("Customer onboarding portal", "Ingrid Haugen", "Retail Banking", "High",
         "Customer experience", [line("Developer", 3, 0, 5), line("Data Analyst", 1, 1, 4)],
         ("full", "")),
        ("Core banking API upgrade", "Lars Berg", "Operations", "High", "Regulatory",
         [line("Developer", 3, 0, 8)], ("full", "")),
        ("Credit risk dashboard refresh", "Lars Berg", "Risk", "Medium", "Efficiency",
         [line("Data Analyst", 1.5, 0, 3), line("Developer", 1, 0, 2)], ("full", "")),
        ("Process mining pilot", "Ingrid Haugen", "Operations", "Medium", "Efficiency",
         [line("Data Analyst", 1, 2, 7), line("Project Manager", 0.5, 2, 7)],
         ("edited", "Data Analyst capacity is tight; approved at 0.5 FTE for now.")),
        ("Legacy report rewrite", "Lars Berg", "Finance", "Low", "Efficiency",
         [line("Developer", 2, 1, 4)],
         ("reject", "Covered by the reporting platform roadmap next year.")),
        ("Document automation", "Lars Berg", "Operations", "High", "Efficiency",
         [line("Developer", 3, 1, 6), line("Project Manager", 1, 1, 6)], None),
        ("Internal knowledge assistant", "Ingrid Haugen", "Retail Banking", "Low", "Growth",
         [line("Developer", 1, 3, 5), line("Data Analyst", 0.5, 3, 5)], None),
    ]
    for n, (title, owner, dept, prio, fit, lines, decision) in enumerate(seeds):
        iid = add_initiative(
            db, title=title, description=f"{title}: scope and deliverables.",
            business_case=case, benefit="Fewer manual hours and faster turnaround.",
            fit=fit, department=dept, priority=prio, owner=owner, lines=lines,
            submitted=dt.date.today() - dt.timedelta(days=30 - 3 * n))
        if decision:
            kind, comment = decision
            approved = None
            if kind == "edited":
                approved = {(a["RequestID"], a["MonthKey"]): (0.5 if a["SkillType"] == "Data Analyst"
                                                              else a["FTERequested"])
                            for a in db["allocations"] if a["InitiativeID"] == iid}
            decide(db, iid, kind, "Maria Lie", comment, approved)
    return db


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------
def style_status(df: pd.DataFrame, col="Status"):
    return df.style.map(lambda v: STATUS_COLOURS.get(v, ""), subset=[col])


def style_remaining(df: pd.DataFrame, cols):
    def colour(v):
        if isinstance(v, (int, float)) and v < 0:
            return "background-color:#FBE4E2;color:#8F1D14;font-weight:600"
        return ""
    return df.style.map(colour, subset=cols).format("{:.1f}", subset=cols)


def flash():
    msg = st.session_state.pop("flash", None)
    if msg:
        st.success(msg)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
def page_home(db, user, is_approver):
    st.title(f"Hi, {user.split()[0]}")
    st.caption("Submit a new initiative, follow up on your requests, or review pending ones.")
    mine = [i for i in db["initiatives"] if i["Owner"] == user]
    waiting = [i for i in db["initiatives"] if i["Status"] in OPEN_STATUSES]
    c = st.columns(4)
    c[0].metric("My open requests", sum(i["Status"] in OPEN_STATUSES for i in mine))
    c[1].metric("My approved", sum(i["Status"] in ("Approved", "Partially Approved") for i in mine))
    c[2].metric("My rejected", sum(i["Status"] == "Rejected" for i in mine))
    c[3].metric("Waiting for review (all)", len(waiting))

    st.subheader("Remaining capacity this month")
    s = summary_df(db)
    now = s[s.MonthKey == current_month_key()]
    cols = st.columns(len(SKILLS))
    for col, skill in zip(cols, SKILLS):
        row = now[now.SkillType == skill]
        if row.empty:
            col.metric(skill, "–")
        else:
            r = row.iloc[0]
            col.metric(skill, f"{r.Unused:.1f} FTE",
                       help=f"Capacity {r.Capacity:.1f} − approved {r.Approved:.1f}")


def page_new(db, user):
    st.title("New initiative")
    m0 = first_of_month(dt.date.today())
    if st.session_state.pop("clear_form", False):
        for k in ["f_title", "f_desc", "f_case", "f_benefit", "lines_init", "lines_editor"]:
            st.session_state.pop(k, None)
    if "lines_init" not in st.session_state:
        st.session_state.lines_init = pd.DataFrame([
            {"Skill type": "Developer", "FTE": 1.0, "Start": add_months(m0, 1),
             "End": add_months(m0, 4) - dt.timedelta(days=1)},
        ])

    left, right = st.columns([5, 6], gap="large")
    with left:
        st.subheader("Initiative and business case")
        title = st.text_input("Initiative name *", key="f_title")
        desc = st.text_area("Description", key="f_desc", height=80)
        case = st.text_area("Business case *", key="f_case", height=150,
                            placeholder="Problem, proposed solution, cost of not doing it")
        st.caption(f"{len(case.strip())} / 100 characters minimum")
        benefit = st.text_area("Expected benefit", key="f_benefit", height=80)
        a, b, c = st.columns(3)
        fit = a.selectbox("Strategic fit", STRATEGIC_FIT)
        dept = b.selectbox("Department", DEPARTMENTS)
        prio = c.selectbox("Priority *", PRIORITIES, index=1)

    with right:
        st.subheader("Resource needs")
        st.caption("One line per skill type. FTE can be decimal (0.5 = half a person). "
                   "Add rows at the bottom of the table.")
        edited = st.data_editor(
            st.session_state.lines_init, key="lines_editor", num_rows="dynamic",
            hide_index=True, width="stretch",
            column_config={
                "Skill type": st.column_config.SelectboxColumn(options=SKILLS, required=True),
                "FTE": st.column_config.NumberColumn(min_value=0.0, max_value=50.0,
                                                     step=0.5, format="%.1f", required=True),
                "Start": st.column_config.DateColumn(format="DD.MM.YYYY", required=True),
                "End": st.column_config.DateColumn(format="DD.MM.YYYY", required=True),
            },
        )
        lines, errors = [], []
        for n, r in edited.reset_index(drop=True).iterrows():
            if any(pd.isna(r.get(k)) for k in ["Skill type", "FTE", "Start", "End"]):
                errors.append(f"Line {n + 1}: fill in skill, FTE, start and end.")
                continue
            start, end = pd.Timestamp(r["Start"]).date(), pd.Timestamp(r["End"]).date()
            if r["FTE"] <= 0:
                errors.append(f"Line {n + 1}: FTE must be above 0.")
            elif end < start:
                errors.append(f"Line {n + 1}: end date is before start date.")
            else:
                lines.append({"skill": r["Skill type"], "fte": float(r["FTE"]),
                              "start": start, "end": end})
        total = sum(l["fte"] * len(month_keys(l["start"], l["end"])) for l in lines)
        m1, m2 = st.columns(2)
        m1.metric("Total request", f"{total:.1f} FTE-months")
        if lines:
            m2.metric("Initiative period",
                      f"{min(l['start'] for l in lines):%b %Y} – {max(l['end'] for l in lines):%b %Y}")
        if lines:
            prev = pd.DataFrame([{"Skill": l["skill"], "Month": k, "FTE": l["fte"]}
                                 for l in lines for k in month_keys(l["start"], l["end"])])
            prev = prev.pivot_table(index="Skill", columns="Month", values="FTE", aggfunc="sum")
            prev.columns = [month_label(c) for c in prev.columns]
            with st.expander("Monthly breakdown (what gets stored as allocation rows)"):
                st.dataframe(prev.style.format("{:.1f}", na_rep=""), width="stretch")

    if not title.strip():
        errors.insert(0, "Initiative name is required.")
    if len(case.strip()) < 100:
        errors.insert(0, "Business case needs at least 100 characters.")
    if not lines and not any("Line" in e for e in errors):
        errors.append("Add at least one skill line.")

    st.divider()
    b1, b2 = st.columns([4, 1])
    with b1:
        for e in errors:
            st.caption(f"• {e}")
    if b2.button("Submit for approval", type="primary", disabled=bool(errors), width="stretch"):
        iid = add_initiative(db, title=title.strip(), description=desc, business_case=case,
                             benefit=benefit, fit=fit, department=dept, priority=prio,
                             owner=user, lines=lines)
        n_rows = sum(1 for a in db["allocations"] if a["InitiativeID"] == iid)
        st.session_state.flash = (f"“{title.strip()}” submitted. {n_rows} monthly allocation rows "
                                  f"created and sent for review.")
        st.session_state.clear_form = True
        st.session_state.goto = "My initiatives"
        st.rerun()


def page_mine(db, user):
    st.title("My initiatives")
    flash()
    mine = [i for i in db["initiatives"] if i["Owner"] == user]
    if not mine:
        st.info("You have no initiatives yet. Use “New initiative” to submit one.")
        return
    rows = []
    for i in sorted(mine, key=lambda x: x["Submitted"], reverse=True):
        req, appr = fte_months(db, i["ID"])
        rows.append({"Initiative": i["Title"], "Submitted": i["Submitted"], "Status": i["Status"],
                     "Requested (FTE-months)": req,
                     "Approved (FTE-months)": appr if i["Status"] not in OPEN_STATUSES else None})
    df = pd.DataFrame(rows)
    st.dataframe(style_status(df).format({"Requested (FTE-months)": "{:.1f}",
                                          "Approved (FTE-months)": "{:.1f}"}, na_rep="–"),
                 hide_index=True, width="stretch")

    st.subheader("Details")
    sel = st.selectbox("Initiative", [i["ID"] for i in mine],
                       format_func=lambda x: get_initiative(db, x)["Title"])
    init = get_initiative(db, sel)
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        st.markdown(f"**Status:** {init['Status']}")
        al = allocations_df(db)
        al = al[al.InitiativeID == sel]
        per = (al.groupby("SkillType")
                 .agg(**{"Requested FTE-months": ("FTERequested", "sum"),
                         "Approved FTE-months": ("FTEApproved", "sum")})
                 .reset_index().rename(columns={"SkillType": "Skill"}))
        st.dataframe(per.style.format({"Requested FTE-months": "{:.1f}",
                                       "Approved FTE-months": "{:.1f}"}),
                     hide_index=True, width="stretch")
    with c2:
        st.markdown("**Decision comment**")
        if init["DecisionBy"]:
            st.info(init["DecisionComment"] or "No comment.")
            st.caption(f"Decided by {init['DecisionBy']} on {init['DecisionDate']:%d.%m.%Y}")
        else:
            st.caption("Waiting for review.")


def page_review(db, user):
    st.title("Review queue")
    flash()
    pend = [i for i in db["initiatives"] if i["Status"] in OPEN_STATUSES]
    if not pend:
        st.success("Nothing waiting for review.")
        return
    pend.sort(key=lambda i: (PRIORITIES.index(i["Priority"]), i["Submitted"]))
    rows = [{"Initiative": i["Title"], "Owner": i["Owner"], "Department": i["Department"],
             "Priority": i["Priority"], "Status": i["Status"],
             "Skills requested": skills_text(db, i["ID"]),
             "FTE-months": fte_months(db, i["ID"])[0], "Submitted": i["Submitted"]}
            for i in pend]
    st.caption(f"{len(pend)} initiatives waiting · sorted by priority, then oldest first")
    st.dataframe(style_status(pd.DataFrame(rows)).format({"FTE-months": "{:.1f}"}),
                 hide_index=True, width="stretch")

    sel = st.selectbox("Review initiative", [i["ID"] for i in pend],
                       format_func=lambda x: get_initiative(db, x)["Title"])
    st.divider()
    review_detail(db, user, sel)


def review_detail(db, user, iid):
    init = get_initiative(db, iid)
    req_total, _ = fte_months(db, iid)
    left, right = st.columns([4, 7], gap="large")
    with left:
        st.subheader(init["Title"])
        st.caption(f"{init['Owner']} · {init['Department']} · {init['Priority']} priority · "
                   f"submitted {init['Submitted']:%d.%m.%Y}")
        st.markdown("**Business case**")
        st.write(init["BusinessCase"])
        st.markdown("**Expected benefit**")
        st.write(init["ExpectedBenefit"] or "–")
        st.markdown("**Description**")
        st.write(init["Description"] or "–")
        st.markdown(f"**Strategic fit:** {init['StrategicFit']}  \n"
                    f"**Period:** {init['StartDate']:%b %Y} – {init['EndDate']:%b %Y}  \n"
                    f"**Total request:** {req_total:.1f} FTE-months")

    with right:
        st.subheader("Capacity check")
        st.caption("Edit **Approve** to approve less than requested. "
                   "Remaining = capacity − already approved − approve.")
        al = allocations_df(db)
        mine = al[al.InitiativeID == iid]
        others = al[(al.Status == "Approved") & (al.InitiativeID != iid)]
        already = others.groupby(["SkillType", "MonthKey"])["FTEApproved"].sum()
        cap = {(c["SkillType"], c["MonthKey"]): c["AvailableFTE"] for c in db["capacity"]}
        check = pd.DataFrame([{
            "RequestID": a.RequestID, "MonthKey": a.MonthKey,
            "Skill": a.SkillType, "Month": month_label(a.MonthKey),
            "Capacity": float(cap.get((a.SkillType, a.MonthKey), 0.0)),
            "Already approved": float(already.get((a.SkillType, a.MonthKey), 0.0)),
            "Requested": a.FTERequested, "Approve": a.FTERequested,
        } for a in mine.itertuples()])
        edited = st.data_editor(
            check, key=f"approve_{iid}", hide_index=True, width="stretch",
            disabled=["Skill", "Month", "Capacity", "Already approved", "Requested"],
            column_config={
                "RequestID": None, "MonthKey": None,
                "Capacity": st.column_config.NumberColumn(format="%.1f"),
                "Already approved": st.column_config.NumberColumn(format="%.1f"),
                "Requested": st.column_config.NumberColumn(format="%.1f"),
                "Approve": st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.1f"),
            },
        )
        edited["Approve"] = edited["Approve"].fillna(0.0)
        edited["Remaining"] = edited["Capacity"] - edited["Already approved"] - edited["Approve"]
        over = int((edited["Remaining"] < 0).sum())
        if over:
            st.error(f"Over capacity in {over} of {len(edited)} skill-months")
        else:
            st.success("Fits within capacity")
        rem = edited.pivot_table(index="Skill", columns="MonthKey", values="Remaining", aggfunc="sum")
        rem.columns = [month_label(c) for c in rem.columns]
        st.caption("Remaining FTE after this decision")
        st.dataframe(style_remaining(rem, list(rem.columns)), width="stretch")

    too_high = edited[edited["Approve"] > edited["Requested"]]
    comment = st.text_area("Decision comment (required for reject, partial approval and hold)",
                           key=f"comment_{iid}", height=80)
    b = st.columns(4)
    action = None
    if b[0].button("Approve in full", type="primary", width="stretch", key=f"full_{iid}"):
        action = "full"
    if b[1].button("Approve as edited", width="stretch", key=f"edit_{iid}"):
        action = "edited"
    if b[2].button("Put on hold", width="stretch", key=f"hold_{iid}"):
        action = "hold"
    if b[3].button("Reject", width="stretch", key=f"reject_{iid}"):
        action = "reject"

    if action:
        is_partial = action == "edited" and (edited["Approve"] < edited["Requested"]).any()
        if action == "edited" and not too_high.empty:
            st.warning("Approve cannot be higher than requested.")
        elif (action in ("hold", "reject") or is_partial) and not comment.strip():
            st.warning("Add a decision comment first.")
        else:
            approved = {(r.RequestID, r.MonthKey): r.Approve for r in edited.itertuples()}
            status = decide(db, iid, action, user, comment.strip(), approved)
            st.session_state.flash = f"“{init['Title']}” marked as {status}."
            st.rerun()


def page_capacity(db):
    st.title("Capacity")
    flash()
    st.caption("Available FTE per skill type and month. This is the Capacity list the pool owner maintains.")
    cap = pd.DataFrame(db["capacity"])
    grid = cap.pivot(index="SkillType", columns="MonthKey", values="AvailableFTE")
    keys = list(grid.columns)
    grid.columns = [month_label(k) for k in keys]
    edited = st.data_editor(grid, key="cap_editor", width="stretch",
                            column_config={c: st.column_config.NumberColumn(min_value=0.0, step=0.5,
                                                                            format="%.1f")
                                           for c in grid.columns})
    if st.button("Save capacity", type="primary"):
        label_to_key = dict(zip(grid.columns, keys))
        db["capacity"] = [{"SkillType": skill, "MonthKey": label_to_key[col],
                           "AvailableFTE": float(edited.loc[skill, col] or 0.0)}
                          for skill in edited.index for col in edited.columns]
        st.session_state.flash = "Capacity saved."
        st.rerun()


def page_reports(db):
    st.title("Reports")
    s = summary_df(db)
    order = list(dict.fromkeys(s.sort_values("MonthKey")["Month"]))
    skill = st.selectbox("Skill type", ["All skills"] + SKILLS)
    f = s if skill == "All skills" else s[s.SkillType == skill]
    agg = f.groupby(["MonthKey", "Month"], as_index=False)[["Capacity", "Approved", "Pipeline", "Unused"]].sum()
    now = agg[agg.MonthKey == current_month_key()]
    c = st.columns(4)
    if not now.empty:
        r = now.iloc[0]
        c[0].metric("Capacity this month", f"{r.Capacity:.1f} FTE")
        c[1].metric("Approved this month", f"{r.Approved:.1f} FTE")
        c[2].metric("Unused this month", f"{r.Unused:.1f} FTE")
    c[3].metric("Pipeline (pending)", f"{f['Pipeline'].sum():.1f} FTE-months")

    st.subheader("Projected FTE use, next 12 months")
    long = agg.melt(id_vars=["Month"], value_vars=["Approved", "Pipeline"],
                    var_name="Type", value_name="FTE")
    bars = alt.Chart(long).mark_bar().encode(
        x=alt.X("Month:O", sort=order, title=None),
        y=alt.Y("FTE:Q", stack="zero", title="FTE"),
        color=alt.Color("Type:N", scale=alt.Scale(domain=["Approved", "Pipeline", "Capacity"],
                                                  range=["#1F5FBF", "#9DB9E6", "#B54708"]),
                        legend=alt.Legend(title=None, orient="top")),
        order=alt.Order("Type:N", sort="ascending"),
        tooltip=["Month", "Type", alt.Tooltip("FTE:Q", format=".1f")],
    )
    cap_line = alt.Chart(agg.assign(Type="Capacity")).mark_line(point=True, strokeWidth=2).encode(
        x=alt.X("Month:O", sort=order), y="Capacity:Q",
        color=alt.Color("Type:N"),
        tooltip=["Month", alt.Tooltip("Capacity:Q", format=".1f")],
    )
    st.altair_chart((bars + cap_line).properties(height=340), width="stretch")
    st.caption("Bars above the capacity line mean approving the pipeline would over-allocate.")

    st.subheader("Unused FTE by skill and month")
    st.caption("Capacity minus approved. Red = over-allocated.")
    m = s.pivot_table(index="SkillType", columns="MonthKey", values="Unused", aggfunc="sum")
    m.columns = [month_label(k) for k in m.columns]
    m.index.name = "Skill"
    st.dataframe(style_remaining(m, list(m.columns)), width="stretch")

    st.subheader("Initiatives")
    rows = []
    for i in db["initiatives"]:
        req, appr = fte_months(db, i["ID"])
        rows.append({"Initiative": i["Title"], "Owner": i["Owner"], "Priority": i["Priority"],
                     "Status": i["Status"], "Requested (FTE-months)": req,
                     "Approved (FTE-months)": appr})
    st.dataframe(style_status(pd.DataFrame(rows)).format({"Requested (FTE-months)": "{:.1f}",
                                                          "Approved (FTE-months)": "{:.1f}"}),
                 hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# App shell
# --------------------------------------------------------------------------
def main():
    if "db" not in st.session_state:
        st.session_state.db = seed_db()
    db = st.session_state.db

    with st.sidebar:
        st.markdown("### Resource Allocation")
        st.caption("Demo · sample data")
        user = st.selectbox("Signed in as", list(USERS),
                            format_func=lambda u: f"{u} ({USERS[u]})", key="user")
        is_approver = USERS[user] == "Approver"
        pages = ["Home", "New initiative", "My initiatives"]
        if is_approver:
            pages += ["Review queue", "Capacity"]
        pages += ["Reports"]
        if "goto" in st.session_state:
            st.session_state.page = st.session_state.pop("goto")
        if st.session_state.get("page") not in pages:
            st.session_state.page = "Home"
        page = st.radio("Navigate", pages, key="page")
        st.divider()
        if st.button("Reset demo data"):
            for k in list(st.session_state.keys()):
                if k != "user":
                    del st.session_state[k]
            st.rerun()

    if page == "Home":
        page_home(db, user, is_approver)
    elif page == "New initiative":
        page_new(db, user)
    elif page == "My initiatives":
        page_mine(db, user)
    elif page == "Review queue":
        page_review(db, user)
    elif page == "Capacity":
        page_capacity(db)
    else:
        page_reports(db)


main()
