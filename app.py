
import streamlit as st
import pandas as pd
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
import os
import calendar, re
from datetime import date, timedelta

st.set_page_config(page_title="MaintenX | Smart Maintenance Copilot", page_icon="🛠️", layout="wide")

st.markdown("""
<style>
.stApp{background:radial-gradient(circle at 15% 10%,rgba(34,197,94,.13),transparent 25%),radial-gradient(circle at 90% 15%,rgba(250,204,21,.10),transparent 25%),linear-gradient(135deg,#07111f,#101827);color:#eef2ff}
.block-container{padding-top:1rem;max-width:1450px}
.hero{padding:25px 32px;border-radius:24px;background:linear-gradient(120deg,rgba(15,23,42,.98),rgba(20,83,45,.72));border:1px solid #ffffff18;box-shadow:0 15px 45px #0005}
.hero h1{margin:0;font-size:38px}.hero p{color:#cbd5e1}
.metric{padding:14px;border-radius:16px;background:#0f172acc;border:1px solid #ffffff12}
.num{font-size:25px;font-weight:800}.muted{color:#94a3b8;font-size:12px}
.cal{min-height:72px;padding:6px;border-radius:10px;background:#0f172acc;border:1px solid #ffffff0d}
.task{font-size:10px;margin-top:3px}
.safe{color:#22c55e;font-weight:800}.ok{color:#facc15;font-weight:800}.risk{color:#ef4444;font-weight:800}
.hover-card{padding:15px;border-radius:16px;background:#0f172a;border:1px solid #ffffff12;position:relative;cursor:pointer}
.hover-details{display:none;position:absolute;z-index:10;left:0;top:100%;width:300px;padding:12px;background:#111827;border:1px solid #ffffff22;border-radius:12px;box-shadow:0 15px 40px #0008}
.hover-card:hover .hover-details{display:block}
.small-title{font-size:13px;font-weight:800;letter-spacing:.3px}
</style>
""", unsafe_allow_html=True)

REQ=["equipment_id","equipment_type","runtime_hours","last_service_date","failure_count","maintenance_cost","downtime_cost","criticality","maintenance_hours","parts_status"]

def sample():
    t=date.today()
    return pd.DataFrame([
    ["EQ-001","CNC Machine",1850,t-timedelta(days=58),4,8500,18000,4,8,"Available"],
    ["EQ-002","Air Compressor",950,t-timedelta(days=34),2,4200,9000,3,5,"Available"],
    ["EQ-003","Hydraulic Pump",2250,t-timedelta(days=82),7,6200,25000,5,7,"Limited"],
    ["EQ-004","Conveyor Motor",720,t-timedelta(days=19),1,2800,7000,2,4,"Available"],
    ["EQ-005","Cooling System",2010,t-timedelta(days=67),5,5100,16000,4,6,"Available"],
    ["EQ-006","Generator",1520,t-timedelta(days=95),8,7500,30000,5,8,"Limited"],
    ["EQ-007","Packaging Unit",690,t-timedelta(days=27),1,3500,6000,2,5,"Available"],
    ["EQ-008","Industrial Fan",1180,t-timedelta(days=47),3,2400,5000,3,3,"Available"],
    ["EQ-009","Boiler",2380,t-timedelta(days=88),6,9000,32000,5,10,"Available"],
    ["EQ-010","Water Pump",1320,t-timedelta(days=61),4,3900,11000,3,5,"Limited"]],columns=REQ)

def score(df):
    d=df.copy()
    d["last_service_date"]=pd.to_datetime(d["last_service_date"],errors="coerce").dt.date
    today=date.today()
    d["days_since_service"]=d["last_service_date"].apply(lambda x:max(0,(today-x).days) if pd.notna(x) else 0)
    def n(s): return pd.Series(50.,index=s.index) if s.max()==s.min() else ((s-s.min())/(s.max()-s.min())*100).clip(0,100)
    d["risk_score"]=(.25*n(d.runtime_hours)+.25*n(d.failure_count)+.20*n(d.days_since_service)+.20*((d.criticality-1)/4*100)+.10*n(d.downtime_cost)).round(1)
    d["risk_level"]=d.risk_score.apply(lambda x:"HIGH" if x>=65 else ("OK" if x>=35 else "SAFE"))
    return d.reset_index(drop=True)

def priority(r):
    # Base operational priority used by the Balanced strategy.
    return (
        0.30 * r.risk_score
        + 0.25 * min(100, r.downtime_cost / 300.0)
        + 0.20 * min(100, r.maintenance_cost / 100.0)
        + 0.25 * ((r.criticality - 1) / 4 * 100)
    )

def make_plan(d, hours_week, horizon, mode="Balanced"):
    """
    Generate three genuinely different scheduling strategies.
    All strategies obey the same weekly maintenance-hour capacity.
    """
    x = d.copy()

    risk = x["risk_score"].clip(0, 100)
    cost = (x["maintenance_cost"] / max(float(x["maintenance_cost"].max()), 1) * 100).clip(0, 100)
    downtime = (x["downtime_cost"] / max(float(x["downtime_cost"].max()), 1) * 100).clip(0, 100)
    criticality = ((x["criticality"] - 1) / 4 * 100).clip(0, 100)
    service_gap = (x["days_since_service"] / max(float(x["days_since_service"].max()), 1) * 100).clip(0, 100)

    if mode == "Cost-first":
        # Favors lower-cost jobs while still protecting against unacceptable risk.
        x["priority_value"] = (
            0.48 * cost
            + 0.20 * downtime
            + 0.17 * risk
            + 0.10 * criticality
            + 0.05 * service_gap
        )
        x["priority_value"] = x["priority_value"] - (risk < 35).astype(int) * 18

    elif mode == "Risk-first":
        # Strongly pulls high-risk / critical assets forward.
        x["priority_value"] = (
            0.58 * risk
            + 0.22 * criticality
            + 0.15 * downtime
            + 0.05 * service_gap
        )
        x["priority_value"] = x["priority_value"] + (risk >= 65).astype(int) * 20

    else:
        # Balanced trade-off between risk, cost and operational impact.
        x["priority_value"] = (
            0.35 * risk
            + 0.20 * cost
            + 0.25 * downtime
            + 0.15 * criticality
            + 0.05 * service_gap
        )

    x = x.sort_values(
        ["priority_value", "risk_score", "equipment_id"],
        ascending=[False, False, True]
    )

    remaining = [int(hours_week)] * horizon
    start_day = date.today()
    rows = []

    for _, r in x.iterrows():
        for w in range(horizon):
            if remaining[w] >= int(r.maintenance_hours):
                remaining[w] -= int(r.maintenance_hours)

                # Deliberately vary weekdays by strategy, equipment and week.
                strategy_shift = {"Cost-first": 1, "Balanced": 3, "Risk-first": 0}.get(mode, 3)
                day_offset = (
                    sum(ord(ch) for ch in str(r.equipment_id))
                    + 2 * w
                    + strategy_shift
                ) % 5

                service_date = start_day + timedelta(days=7 * w + day_offset)
                rows.append({
                    "equipment_id": r.equipment_id,
                    "equipment_type": r.equipment_type,
                    "date": service_date,
                    "week": w + 1,
                    "hours": int(r.maintenance_hours),
                    "risk": float(r.risk_score),
                    "cost": float(r.maintenance_cost),
                    "level": r.risk_level,
                    "reason": reason(r),
                    "strategy": mode
                })
                break

    return pd.DataFrame(rows)

def reason(r):
    a=[]
    if r.risk_score>=65:a.append("high failure risk")
    elif r.risk_score>=35:a.append("moderate risk")
    else:a.append("stable condition")
    if r.failure_count>=4:a.append("failure history")
    if r.days_since_service>=60:a.append("long service gap")
    if r.criticality>=4:a.append("high criticality")
    if r.downtime_cost>=15000:a.append("high downtime impact")
    return ", ".join(a)


def repair_replace(r):
    repair=float(r.maintenance_cost)
    replacement=max(repair*4.5,repair+15000)
    return repair,replacement,replacement-repair

# state
for k,v in {"logged":False,"new":False,"fleet":sample(),"done":set(),"plan_mode":"Balanced","emergency":None,"chat":[],"scheduled_dates":{}, "overdue_alerts":set()}.items():
    st.session_state.setdefault(k,v)

# onboarding
if not st.session_state.logged:
    st.markdown('<div class="hero"><h1>🛠️ MaintenX</h1><p>Risk-aware maintenance planning • visual scheduling • human approval</p></div>',unsafe_allow_html=True)
    a,b=st.columns(2)
    with a:
        if st.button("🆕 New User",use_container_width=True): st.session_state.new=True
        if st.button("🔐 Already Registered",use_container_width=True): st.session_state.logged=True; st.rerun()
    with b:
        if st.session_state.new:
            st.subheader("Create workspace")
            st.text_input("Your name")
            st.text_input("Organization / department")
            f=st.file_uploader("Upload starting equipment CSV",type="csv")
            if st.button("Create Workspace",type="primary",use_container_width=True):
                if f is None: st.warning("Upload a CSV to create your first fleet.")
                else:
                    q=pd.read_csv(f); miss=[c for c in REQ if c not in q.columns]
                    if miss: st.error("Missing columns: "+", ".join(miss))
                    else: st.session_state.fleet=q; st.session_state.logged=True; st.rerun()
    st.stop()

fleet=st.session_state.fleet.copy()
for c in ["runtime_hours","failure_count","maintenance_cost","downtime_cost","criticality","maintenance_hours"]:
    fleet[c]=pd.to_numeric(fleet[c],errors="coerce")
fleet=fleet.dropna(subset=["runtime_hours","failure_count","maintenance_cost","downtime_cost","criticality","maintenance_hours"])
sc=score(fleet)

# control panel
st.sidebar.markdown("## 🛠️ Control Panel")
horizon=st.sidebar.slider("Planning horizon (weeks)",4,8,6)
hours_week=st.sidebar.slider("Maintenance hours / week",8,60,24)
st.sidebar.divider()
st.sidebar.caption("Recommendations only • human approval required")
if st.sidebar.button("Log out"): st.session_state.logged=False; st.rerun()

st.markdown('<div class="hero"><h1>🛠️ MaintenX Control Center</h1><p>Plan smarter. See risk earlier. Keep the final decision with the maintenance team.</p></div>',unsafe_allow_html=True)
# progress at top
plans=make_plan(sc,hours_week,horizon,st.session_state.plan_mode)
done=st.session_state.done.intersection(set(sc.equipment_id))

# Persist planned dates so unfinished work can be detected after a new day begins.
for _, row in plans.iterrows():
    eid=str(row.equipment_id)
    if eid not in st.session_state.scheduled_dates and eid not in done:
        st.session_state.scheduled_dates[eid]=row.date

# Carry forward any unfinished job whose saved date has passed.
overdue=[]
for eid, saved_date in list(st.session_state.scheduled_dates.items()):
    if eid in done:
        continue
    if saved_date < date.today():
        overdue.append(eid)

if overdue:
    # Rebuild the active schedule and move overdue jobs to the earliest feasible slot.
    active=plans[~plans.equipment_id.isin(done)].copy()
    for eid in overdue:
        row=active[active.equipment_id==eid]
        if row.empty:
            continue
        r=row.iloc[0]
        used_by_week={w:int(active[active.week==w].hours.sum()) for w in range(1,horizon+1)}
        target_week=None
        for w in range(1,horizon+1):
            other_hours=used_by_week.get(w,0)-int(r.hours)
            if other_hours + int(r.hours) <= int(hours_week):
                target_week=w
                break
        if target_week is not None:
            idx=plans.index[plans.equipment_id==eid][0]
            plans.loc[idx,"week"]=target_week
            plans.loc[idx,"date"]=date.today()+timedelta(days=max(0, target_week-1)*7+2)
            st.session_state.scheduled_dates[eid]=plans.loc[idx,"date"]
            st.session_state.overdue_alerts.add(eid)

progress=len(done)/max(len(plans),1)
st.progress(progress,text=f"Maintenance progress  •  {len(done)} of {len(plans)} planned jobs completed")

# top metrics, stable etc hover
high=(sc.risk_score>=65).sum(); ok=((sc.risk_score>=35)&(sc.risk_score<65)).sum(); safe=(sc.risk_score<35).sum()
cols=st.columns(4)
for col,title,n,cls,items in [
(cols[0],"🔴 Risk",high,"risk",sc[sc.risk_score>=65]),
(cols[1],"🟡 Monitor",ok,"ok",sc[(sc.risk_score>=35)&(sc.risk_score<65)]),
(cols[2],"🟢 Stable",safe,"safe",sc[sc.risk_score<35]),
(cols[3],"🛠️ Last serviced",sc["last_service_date"].max().strftime("%d %b %Y") if not sc.empty else "—","",None)]:
    with col:
        if items is not None:
            details="<br>".join([f"{r.equipment_id} — {r.equipment_type} ({r.risk_score}/100)" for _,r in items.iterrows()]) or "None"
            st.markdown(f'<div class="hover-card"><div class="{cls}">{title}</div><div class="num">{n}</div><div class="hover-details">{details}</div></div>',unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="metric"><div class="muted">{title}</div><div class="num">{n}</div></div>',unsafe_allow_html=True)

tabs=st.tabs(["📅 Schedule","🚦 Fleet Health","📊 Risk Graph","➕ Manage Fleet"])

with tabs[0]:
    st.subheader("📅 Efficient maintenance calendar")
    st.caption("Three planning strategies use different objective weights while respecting the same weekly maintenance capacity.")

    mode=st.radio(
        "Planning strategy",
        ["Balanced","Cost-first","Risk-first"],
        horizontal=True,
        index=["Balanced","Cost-first","Risk-first"].index(st.session_state.plan_mode),
        help="Cost-first prioritizes economics; Balanced trades off risk/cost/impact; Risk-first strongly prioritizes critical and high-risk assets."
    )
    if mode!=st.session_state.plan_mode:
        st.session_state.plan_mode=mode
        st.rerun()

    plans=make_plan(sc,hours_week,horizon,mode)

    # Reapply the persisted overdue logic for the selected strategy.
    for _, row in plans.iterrows():
        eid=str(row.equipment_id)
        if eid not in st.session_state.scheduled_dates and eid not in done:
            st.session_state.scheduled_dates[eid]=row.date

    if st.session_state.emergency:
        st.warning(st.session_state.emergency)

    critical=sc[sc.risk_score>=85]
    if not critical.empty:
        names=", ".join(critical.equipment_id.astype(str).tolist())
        st.error(f"🚨 CRITICAL CONDITION: {names} have risk ≥ 85/100. Review these assets immediately. Final action requires human approval.")

    overdue_now=[eid for eid in st.session_state.overdue_alerts if eid not in done]
    if overdue_now:
        st.warning(
            "⚠️ Uncompleted previous-day task(s) were detected and carried forward: "
            + ", ".join(sorted(overdue_now))
            + ". The schedule has been recalculated around the remaining capacity."
        )

    yr=date.today().year
    mo=st.selectbox(
        "Month",
        range(1,13),
        index=date.today().month-1,
        format_func=lambda x:calendar.month_name[x],
        key="schedule_month"
    )
    weeks=calendar.Calendar(firstweekday=0).monthdatescalendar(yr,mo)

    cc=st.columns(7)
    for c,n in zip(cc,["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]):
        c.caption(n)

    for wk in weeks:
        cc=st.columns(7)
        for c,day in zip(cc,wk):
            with c:
                if day.month!=mo:
                    st.markdown('<div class="cal" style="opacity:.25">'+str(day.day)+'</div>',unsafe_allow_html=True)
                    continue

                t=plans[(plans.date==day) & (~plans.equipment_id.isin(done))]
                label="<br>".join([
                    f"{'🚨' if x.risk>=85 else ('🔴' if x.risk>=65 else ('🟡' if x.risk>=35 else '🟢'))} {x.equipment_id}"
                    for _,x in t.iterrows()
                ]) or "—"

                st.markdown(
                    f'<div class="cal"><b>{day.day}</b><div class="task">{label}</div></div>',
                    unsafe_allow_html=True
                )

    selected_date=st.date_input("Inspect a date",value=date.today(),key="inspect_date")
    tasks=plans[(plans.date==selected_date) & (~plans.equipment_id.isin(done))]

    if tasks.empty:
        st.info("No active maintenance planned for this date.")
    else:
        st.markdown("### ✅ Maintenance To-Do")
        for _,r in tasks.iterrows():
            c1,c2=st.columns([5,1])
            with c1:
                st.markdown(f"**{r.equipment_id} — {r.equipment_type}** • {r.hours}h • ₹{r.cost:,.0f}")
                cls="risk" if r.level=="HIGH" else ("ok" if r.level=="OK" else "safe")
                icon="🚨" if r.risk>=85 else ("🔴" if cls=="risk" else ("🟡" if cls=="ok" else "🟢"))
                st.markdown(
                    f'<span class="{cls}">{icon} {r.level} risk</span> — {r.reason}',
                    unsafe_allow_html=True
                )
            with c2:
                checked=st.checkbox("Done",key=f"todo_{mode}_{r.equipment_id}",value=False)
                if checked and r.equipment_id not in st.session_state.done:
                    st.session_state.done.add(r.equipment_id)
                    st.session_state.scheduled_dates.pop(r.equipment_id,None)
                    st.session_state.overdue_alerts.discard(r.equipment_id)
                    st.rerun()

    st.markdown("### Strategy difference")
    if mode=="Cost-first":
        st.info("💰 Cost-first: maintenance economics receive the strongest weight, while low-risk jobs are discouraged from being brought forward unnecessarily.")
    elif mode=="Risk-first":
        st.error("🚨 Risk-first: failure risk and criticality receive the strongest weight, so high-risk assets are pulled toward earlier feasible slots.")
    else:
        st.success("⚖️ Balanced: risk, cost, downtime impact and criticality are traded off to create a practical middle-ground schedule.")

with tabs[1]:
    st.subheader("🚦 Fleet Health")
    st.caption(
        "Risk is based on current prototype data. Human review is required before "
        "deferring high-risk equipment."
    )

    hdr=st.columns([1,1.5,1,1,1,1.3,2.3])
    for c,h in zip(
        hdr,
        ["Equipment","Type","Runtime","Failures","Risk","Status","Repair / Replace"]
    ):
        c.markdown(f"**{h}**")

    for _,r in sc.iterrows():
        if r.equipment_id in st.session_state.done:
            continue
        c=st.columns([1,1.5,1,1,1,1.3,2.3])
        c[0].write(r.equipment_id)
        c[1].write(r.equipment_type)
        c[2].write(f"{r.runtime_hours:,.0f}h")
        c[3].write(int(r.failure_count))
        c[4].write(f"{r.risk_score}/100")

        cls="risk" if r.risk_level=="HIGH" else ("ok" if r.risk_level=="OK" else "safe")
        c[5].markdown(
            f'<span class="{cls}">{"🔴" if cls=="risk" else ("🟡" if cls=="ok" else "🟢")} '
            f'{r.risk_level}</span>',
            unsafe_allow_html=True
        )

        repair,repl,saving=repair_replace(r)
        c[6].markdown(f"Repair **₹{repair:,.0f}** vs Replace **₹{repl:,.0f}**")
        c[6].caption(f"🎉 Repair saves ~₹{saving:,.0f} now" if saving>0 else "Replacement may be worth reviewing.")
        st.divider()

with tabs[2]:
    # Deliberately graph-only for the judge/demo presentation.
    graph_df=(
        sc[["equipment_id","risk_score"]]
        .sort_values("risk_score",ascending=False)
        .set_index("equipment_id")
    )
    st.bar_chart(graph_df["risk_score"],height=620)

with tabs[3]:
    st.subheader("➕ Manage Fleet")
    add_mode=st.radio("How would you like to add equipment?",["Manual entry","CSV upload"],horizontal=True)
    if add_mode=="Manual entry":
        a,b=st.columns(2)
        with a:
            eid=st.text_input("Equipment ID"); typ=st.text_input("Equipment type"); runtime=st.number_input("Runtime hours",0,100000,500); service=st.date_input("Last service",date.today()); failures=st.number_input("Failure count",0,100,0)
        with b:
            mc=st.number_input("Maintenance cost (₹)",0,10000000,3000,500); dc=st.number_input("Downtime cost (₹)",0,10000000,8000,500); crit=st.slider("Criticality",1,5,3); mh=st.number_input("Maintenance hours",1,100,4); parts=st.selectbox("Parts status",["Available","Limited","Unavailable"])
        if st.button("Add machine",type="primary"):
            if not eid or eid in st.session_state.fleet.equipment_id.astype(str).tolist(): st.error("Enter a unique equipment ID.")
            else:
                row=pd.DataFrame([[eid,typ,runtime,service,failures,mc,dc,crit,mh,parts]],columns=REQ)
                st.session_state.fleet=pd.concat([st.session_state.fleet,row],ignore_index=True); st.success(f"Added {eid}."); st.rerun()
    else:
        f=st.file_uploader("Add machines from CSV",type="csv",key="fleet_csv")
        if f and st.button("Import CSV"):
            q=pd.read_csv(f); miss=[x for x in REQ if x not in q.columns]
            if miss: st.error("Missing columns: "+", ".join(miss))
            else: st.session_state.fleet=pd.concat([st.session_state.fleet,q],ignore_index=True).drop_duplicates("equipment_id"); st.success("Fleet updated."); st.rerun()
    st.markdown("### Remove an equipment")
    rem=st.selectbox("Equipment",st.session_state.fleet.equipment_id.tolist())
    if st.button("Remove equipment"): st.session_state.fleet=st.session_state.fleet[st.session_state.fleet.equipment_id!=rem].reset_index(drop=True); st.session_state.done.discard(rem); st.rerun()

# data-aware chatbot
def _normalize_name(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")

def _find_column(frame, aliases):
    normalized={_normalize_name(c): c for c in frame.columns}
    for alias in aliases:
        if _normalize_name(alias) in normalized:
            return normalized[_normalize_name(alias)]
    return None

def _machine_ids_in_prompt(prompt, frame):
    """Detect machine IDs mentioned by the user, including IDs that do not exist.

    IMPORTANT: this function intentionally does NOT restrict detection to IDs
    already present in the dataframe. That lets the chatbot reject nonexistent
    IDs instead of silently answering for another machine or asking the AI to guess.
    """
    text=str(prompt or "").strip()
    upper=text.upper()
    found=[]

    # 1) Exact IDs currently present in the loaded dataset.
    if frame is not None and "equipment_id" in frame.columns:
        for eid in frame["equipment_id"].dropna().astype(str).unique():
            pattern=r"(?<![A-Z0-9_-])"+re.escape(eid.upper())+r"(?![A-Z0-9_-])"
            if re.search(pattern, upper):
                found.append(eid)

    # 2) Detect explicit machine/equipment references even when the ID is NOT
    #    present in the dataset. Examples: M999, EQ-999, machine M999, equipment M999.
    patterns=[
        r"(?<![A-Z0-9_-])(?:EQ|M|EQUIP|EQUIPMENT)[-_]?\d+(?![A-Z0-9_-])",
        r"\b(?:MACHINE|EQUIPMENT|EQUIP)\s*(?:ID\s*)?(?:[:#-]?\s*)?([A-Z][A-Z0-9_-]*\d+|\d+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, upper):
            value=match.group(0).strip(" .,:;?!")
            if pattern.startswith(r"\b(?:MACHINE") and match.lastindex:
                value=match.group(1).strip(" .,:;?!")
            if value and value not in found:
                found.append(value)

    # 3) Possessive forms such as "M999's risk". The first pattern already
    #    catches M999; this extra pass handles IDs like EQUIP-999 reliably.
    for match in re.finditer(r"\b([A-Z]{1,12}[-_]\d+|[A-Z]{1,12}\d+)['’]s\b", upper):
        value=match.group(1)
        if value not in found:
            found.append(value)

    return list(dict.fromkeys(found))

def _row_for_id(frame, eid):
    m=frame[frame["equipment_id"].astype(str).str.upper()==str(eid).upper()]
    return m.iloc[0] if not m.empty else None

def _money(v):
    try:
        return f"₹{float(v):,.0f}"
    except Exception:
        return "—"

def _value_for_field(row, field):
    if field not in row.index or pd.isna(row[field]):
        return None
    v=row[field]
    if isinstance(v, (pd.Timestamp, date)):
        return str(v)
    return v

def _machine_summary(row):
    lines=[f"**Machine: {row.equipment_id}**"]
    field_specs=[
        ("Risk", "risk_score", lambda v:f"{float(v):.1f}/100"),
        ("Criticality", "criticality", lambda v:f"{int(v)}/5"),
        ("Runtime", "runtime_hours", lambda v:f"{float(v):,.0f} hours"),
        ("Failure Count", "failure_count", lambda v:f"{int(v)}"),
        ("Maintenance Cost", "maintenance_cost", _money),
        ("Downtime Cost", "downtime_cost", _money),
        ("Parts", "parts_status", str),
        ("Last Service", "last_service_date", str),
        ("Maintenance Hours", "maintenance_hours", lambda v:f"{float(v):g} hours"),
    ]
    for label,col,fmt in field_specs:
        if col in row.index and pd.notna(row[col]):
            try: val=fmt(row[col])
            except Exception: val=str(row[col])
            lines.append(f"- **{label}:** {val}")
    return "\n".join(lines)

def _column_from_question(q, frame):
    aliases={
        "risk_score":["risk score","risk","risk level"],
        "criticality":["criticality","critical"],
        "runtime_hours":["runtime","runtime hours","hours run","operating hours"],
        "failure_count":["failure count","failures","failure history","number of failures"],
        "maintenance_cost":["maintenance cost","maintenance expense","service cost"],
        "downtime_cost":["downtime cost","downtime impact","downtime"],
        "last_service_date":["last service","service date","serviced"],
        "parts_status":["parts","parts status","part availability","availability"],
        "maintenance_hours":["maintenance hours","service hours"],
    }
    for col, words in aliases.items():
        if any(w in q for w in words) and col in frame.columns:
            return col
    # Support any additional columns present in a user-uploaded CSV.
    for col in frame.columns:
        label=str(col).replace("_"," ").lower()
        if label in q:
            return col
    return None

def _table(rows, columns):
    if rows.empty:
        return "No machines match that condition."
    out=rows[columns].copy()
    for c in out.columns:
        if c in ["maintenance_cost","downtime_cost"]:
            out[c]=out[c].map(_money)
        elif c=="risk_score":
            out[c]=out[c].map(lambda x:f"{float(x):.1f}")
    out=out.rename(columns={
        "equipment_id":"Machine","equipment_type":"Type","risk_score":"Risk",
        "failure_count":"Failures","runtime_hours":"Runtime",
        "maintenance_cost":"Maintenance Cost","downtime_cost":"Downtime Cost",
        "criticality":"Criticality","parts_status":"Parts"
    })
    return out.to_markdown(index=False)

def _schedule_answer(q, frame, generated_plan):
    if generated_plan is None or generated_plan.empty:
        return "No maintenance jobs are currently present in the generated schedule."
    ids=_machine_ids_in_prompt(q, frame)
    if ids:
        eid=ids[0]
        row=_row_for_id(frame,eid)
        if row is None:
            return f"Machine {eid} does not exist in the current equipment dataset.\nPlease check the equipment ID and try again."
        task=generated_plan[generated_plan.equipment_id.astype(str).str.upper()==eid.upper()]
        if task.empty:
            return f"Machine {eid} is not present in the current generated maintenance schedule."
        t=task.iloc[0]
        if "why" in q:
            return f"Machine {eid} is scheduled for Week {int(t.week)} on {t.date}. The existing optimizer prioritizes it because of {t.reason}. This explains the generated schedule; it is not a separate chatbot schedule."
        return f"Machine {eid} is scheduled for **Week {int(t.week)}**, on **{t.date}**, for about **{int(t.hours)} maintenance hours**."
    week_match=re.search(r"\bweek\s*(\d+)\b",q)
    if week_match:
        w=int(week_match.group(1))
        t=generated_plan[generated_plan.week==w]
        return f"**Week {w}** has {len(t)} scheduled machine(s).\n\n"+_table(t,["equipment_id","equipment_type","risk","hours","date"])
    if "this week" in q or "current week" in q:
        t=generated_plan[generated_plan.week==1]
        return f"The current generated planning week has **{len(t)} scheduled machine(s)** using the application's existing schedule."
    if "how many" in q and ("scheduled" in q or "jobs" in q):
        return f"The generated schedule contains **{len(generated_plan)} scheduled job(s)** across the current {horizon}-week planning horizon."
    return f"The current **{st.session_state.plan_mode}** schedule uses the application's generated plan for up to {horizon} weeks, with a limit of {hours_week} maintenance hours per week."


def _grok_chat_explain(user_question, factual_answer, frame=None, generated_plan=None):
    """Use Grok only to understand/explain facts already retrieved from the app.

    The dataframe and generated optimizer plan remain the source of truth.
    Grok is never allowed to invent IDs, values, schedules, or missing data.
    """
    if OpenAI is None:
        return factual_answer

    api_key = os.getenv("XAI_API_KEY")
    model = os.getenv("XAI_MODEL", "grok-4.5")

    # Support Streamlit Cloud secrets as well as environment variables.
    try:
        api_key = api_key or st.secrets.get("XAI_API_KEY")
        model = st.secrets.get("XAI_MODEL", model)
    except Exception:
        pass

    # If Grok is not configured, retain the deterministic dataframe answer.
    if not api_key:
        return factual_answer

    try:
        # xAI provides an OpenAI-compatible API endpoint.
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1"
        )

        system_prompt = """
You are the MaintenX maintenance-data assistant.

CRITICAL RULES:
1. The application's dataframe and generated optimizer schedule are the ONLY source of truth.
2. The supplied FACTUAL ANSWER is already retrieved/calculated from that data.
3. Rewrite/explain it concisely for the user's question. Do not change any numbers,
   machine IDs, dates, costs, risk values, schedule information, or other facts.
4. Never invent a machine, value, field, schedule, reason, or missing information.
5. If the factual answer says information is unavailable, preserve that meaning.
6. Never create a separate maintenance schedule or optimization result.
7. Do not claim that your answer is an automatic maintenance decision.
8. For recommendations, say they are based on the available dataset and the final
   decision should be made by the maintenance engineer.
9. Keep the response concise and useful. Use a small table only when it improves clarity.
10. The dataframe is the source of truth; you explain the retrieved data, not create it.
"""

        response = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=500,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content":
                    f"USER QUESTION:\n{user_question}\n\n"
                    f"FACTUAL ANSWER FROM CURRENT APPLICATION DATA:\n{factual_answer}\n\n"
                    "Return only the concise final answer. Preserve all factual values exactly."
                }
            ]
        )
        result = response.choices[0].message.content
        return result.strip() if result and result.strip() else factual_answer
    except Exception:
        # Grok failure must never break the existing chatbot/application.
        return factual_answer

def _data_chat_answer(prompt, frame, generated_plan):
    if frame is None or frame.empty:
        return "No equipment dataset is currently loaded. Please upload a CSV dataset first."

    q=prompt.lower().strip()
    ids=_machine_ids_in_prompt(prompt,frame)

    # Validate every explicitly referenced machine before any other interpretation.
    if ids:
        missing=[eid for eid in ids if _row_for_id(frame,eid) is None]
        if missing:
            eid=missing[0]
            return f"Machine {eid} does not exist in the current equipment dataset.\nPlease check the equipment ID and try again."

    if any(k in q for k in ["schedule","scheduled","calendar","maintain","maintenance timing","when should"]):
        if "capacity" not in q or "reduce" not in q:
            if any(k in q for k in ["when should","scheduled","schedule","week 1","week 2","week 3","week 4","week 5","week 6","week 7","week 8","calendar"]):
                return _schedule_answer(q,frame,generated_plan)

    if "capacity" in q and ("reduce" in q or "reduced" in q or "less" in q):
        return (
            f"The current optimizer is configured for **{hours_week} maintenance hours/week**. "
            "Reducing that capacity would leave less room for the jobs in the existing generated schedule, "
            "so some lower-priority work may be deferred. I have not created a separate hypothetical schedule; "
            "the application’s current generated plan remains the source of truth."
        )

    if len(ids)==2 and ("compare" in q or "versus" in q or " vs " in q or "difference" in q):
        a,b=(_row_for_id(frame,ids[0]),_row_for_id(frame,ids[1]))
        rows=[]
        for label,col,fmt in [
            ("Risk","risk_score",lambda v:f"{float(v):.1f}/100"),
            ("Runtime","runtime_hours",lambda v:f"{float(v):,.0f} h"),
            ("Failure Count","failure_count",lambda v:str(int(v))),
            ("Maintenance Cost","maintenance_cost",_money),
            ("Downtime Cost","downtime_cost",_money),
            ("Criticality","criticality",lambda v:f"{int(v)}/5"),
            ("Parts","parts_status",str)]:
            if col in frame.columns:
                rows.append(f"| {label} | {fmt(a[col]) if pd.notna(a[col]) else '—'} | {fmt(b[col]) if pd.notna(b[col]) else '—'} |")
        extra=""
        for eid in ids:
            task=generated_plan[generated_plan.equipment_id.astype(str).str.upper()==eid.upper()] if generated_plan is not None else pd.DataFrame()
            if not task.empty:
                t=task.iloc[0]
                extra+=f"\n- {eid}: scheduled for Week {int(t.week)} on {t.date}."
        return f"**Comparison: {ids[0]} vs {ids[1]}**\n\n| Field | {ids[0]} | {ids[1]} |\n|---|---:|---:|\n"+"\n".join(rows)+extra

    if len(ids)==1:
        row=_row_for_id(frame,ids[0])
        col=_column_from_question(q,frame)
        if col:
            v=_value_for_field(row,col)
            if col=="risk_score": v=f"{float(v):.1f}/100"
            elif col=="criticality": v=f"{int(v)}/5"
            elif col=="runtime_hours": v=f"{float(v):,.0f} hours"
            elif col=="maintenance_cost" or col=="downtime_cost": v=_money(v)
            return f"**{row.equipment_id} — {row.equipment_type}**\n\n**{col.replace('_',' ').title()}:** {v}"
        if "why" in q or "priorit" in q:
            return f"{row.equipment_id} is prioritized with a risk score of {row.risk_score:.1f}/100 because of {reason(row)}. This is a recommendation based on the available dataset; the final decision should be made by the maintenance engineer."
        return _machine_summary(row)

    # Dataset-wide aggregations.
    numeric_map={
        "runtime_hours":["runtime","runtime hours"],
        "failure_count":["failure count","failures"],
        "maintenance_cost":["maintenance cost"],
        "downtime_cost":["downtime cost"],
        "risk_score":["risk","risk score"],
        "criticality":["criticality"],
    }
    target=None
    for col,words in numeric_map.items():
        if col in frame.columns and any(w in q for w in words):
            target=col; break

    if "average" in q or "avg" in q or "mean" in q:
        if target:
            return f"The average **{target.replace('_',' ')}** is **{frame[target].mean():.1f}**."
        return "I don't have that information in the current equipment dataset."
    if any(x in q for x in ["how many","count of","number of"]) and ("machine" in q or "equipment" in q):
        if "critical" in q and "criticality" in frame.columns:
            n=int((frame.criticality==5).sum())
            return f"There are **{n} machine(s)** with criticality 5 in the current dataset."
        if "high risk" in q or "high-risk" in q:
            n=int((frame.risk_score>=65).sum())
            return f"There are **{n} high-risk machine(s)** in the current dataset."
        if "unavailable" in q and "parts_status" in frame.columns:
            n=int(frame.parts_status.astype(str).str.lower().eq("unavailable").sum())
            return f"There are **{n} machine(s)** with unavailable parts."
        return f"The current equipment dataset contains **{len(frame)} machine(s)**."

    if "critical" in q and ("which" in q or "show" in q or "list" in q):
        if "criticality" in frame.columns:
            x=frame[frame.criticality==5]
            return _table(x,["equipment_id","equipment_type","criticality","risk_score","failure_count"])

    if "unavailable" in q and "parts_status" in frame.columns:
        x=frame[frame.parts_status.astype(str).str.lower().eq("unavailable")]
        return _table(x,["equipment_id","equipment_type","parts_status","risk_score"])

    if "not been serviced" in q or "not serviced recently" in q or "long service" in q:
        if "days_since_service" in frame.columns:
            x=frame.sort_values("days_since_service",ascending=False).head(10)
            return _table(x,["equipment_id","equipment_type","last_service_date","days_since_service","risk_score"])
        return "I don't have that information in the current equipment dataset."

    top_match=re.search(r"\btop\s+(\d+)\b",q)
    n=int(top_match.group(1)) if top_match else 5
    n=min(max(n,1),10)
    if "highest" in q and target and "top" not in q:
        r=frame.loc[frame[target].idxmax()]
        value=r[target]
        if target in ["maintenance_cost","downtime_cost"]:
            value=_money(value)
        elif target=="runtime_hours":
            value=f"{float(value):,.0f} hours"
        elif target=="failure_count":
            value=f"{int(value)}"
        else:
            value=f"{float(value):.1f}"
        return f"**{r.equipment_id}** has the highest {target.replace('_',' ')} at **{value}**."

    if "lowest" in q and target and "top" not in q:
        r=frame.loc[frame[target].idxmin()]
        value=r[target]
        if target in ["maintenance_cost","downtime_cost"]:
            value=_money(value)
        elif target=="runtime_hours":
            value=f"{float(value):,.0f} hours"
        else:
            value=str(value)
        return f"**{r.equipment_id}** has the lowest {target.replace('_',' ')} at **{value}**."

    if "highest" in q or "top" in q or "lowest" in q:
        if target:
            ascending="lowest" in q and "highest" not in q
            x=frame.sort_values(target,ascending=ascending).head(n)
            return _table(x,["equipment_id","equipment_type",target])

    if "maintained first" in q or "maintain first" in q or "priority order" in q:
        x=frame.copy()
        x["priority_value"]=x.apply(priority,axis=1)
        x=x.sort_values("priority_value",ascending=False).head(n)
        return _table(x,["equipment_id","equipment_type","risk_score","failure_count","downtime_cost"])

    if "risk" in q and not target:
        x=frame.sort_values("risk_score",ascending=False).head(n)
        return _table(x,["equipment_id","equipment_type","risk_score","criticality","failure_count"])

    # Generic field request without a machine ID.
    col=_column_from_question(q,frame)
    if col:
        return "Please include a machine ID for a machine-specific value, or ask for highest, lowest, average, count, or top results."

    return "I can answer from the currently loaded equipment dataset, including machine details, risk, criticality, runtime, failures, costs, downtime, parts, service dates, comparisons, rankings, counts, averages, and the application's generated schedule. I won't invent missing data."

prompt=st.chat_input("💬 Ask MaintenX — e.g. 'What is the risk of EQ-003?'")
if prompt:
    # Always use the current dataframe and current generated optimizer plan.
    current_fleet=st.session_state.fleet.copy()
    required_present=[c for c in REQ if c in current_fleet.columns]
    if not all(c in current_fleet.columns for c in REQ):
        current_fleet=pd.DataFrame(columns=REQ)
    else:
        for c in ["runtime_hours","failure_count","maintenance_cost","downtime_cost","criticality","maintenance_hours"]:
            current_fleet[c]=pd.to_numeric(current_fleet[c],errors="coerce")
        current_fleet=current_fleet.dropna(subset=[c for c in REQ if c != "parts_status"])
    current_sc=score(current_fleet) if not current_fleet.empty else current_fleet
    current_plans=make_plan(current_sc,hours_week,horizon,st.session_state.plan_mode) if not current_sc.empty else pd.DataFrame()

    # Validate explicit machine IDs BEFORE any AI/explanation logic.
    # A nonexistent ID must always receive the deterministic rejection message.
    detected_ids=_machine_ids_in_prompt(prompt,current_sc)
    if detected_ids:
        missing_id=next((eid for eid in detected_ids if _row_for_id(current_sc,eid) is None), None)
        if missing_id is not None:
            answer=(
                f"Machine {missing_id} does not exist in the current equipment dataset.\n"
                "Please check the equipment ID and try again."
            )
        else:
            try:
                answer=_data_chat_answer(prompt,current_sc,current_plans)
            except Exception:
                answer="I don't have that information in the current equipment dataset."
    else:
        try:
            answer=_data_chat_answer(prompt,current_sc,current_plans)
        except Exception:
            answer="I don't have that information in the current equipment dataset."

    # Azure OpenAI is used only after deterministic dataframe/schedule validation.
    # Nonexistent IDs are returned above and NEVER reach the AI model.
    if detected_ids and all(_row_for_id(current_sc, eid) is not None for eid in detected_ids):
        answer=_grok_chat_explain(prompt, answer, current_sc, current_plans)
    elif not detected_ids:
        answer=_grok_chat_explain(prompt, answer, current_sc, current_plans)

    st.session_state.chat.append((prompt,answer))
    st.rerun()

if st.session_state.chat:
    st.sidebar.markdown("### 💬 Copilot")
    for q,a in st.session_state.chat[-2:]:
        st.sidebar.caption("You: "+q)
        st.sidebar.markdown(a)

st.divider()
st.caption("MaintenX recommendations are decision support only. Human approval is required. Emergency rescheduling must be reviewed by qualified maintenance staff.")
