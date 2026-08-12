# MaintenX — Smart Maintenance Scheduling Optimizer

## Run
```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Updated demo features
- Three genuinely different planning strategies:
  - Cost-first
  - Balanced
  - Risk-first
- Each strategy uses different objective weights while respecting weekly maintenance capacity.
- Calendar dates are varied by strategy/equipment instead of repeating the same service weekday.
- Critical-condition alerts for very high risk assets.
- Maintenance To-Do checklist: completed tasks disappear from the active list.
- Unfinished tasks from a previous day are detected and carried forward to the earliest feasible slot around capacity.
- Human approval remains required.
- Fleet Health with risk/status and repair-vs-replace insight.
- Graph-only Risk Graph page.
- Manage Fleet supports manual and CSV equipment entry.
- Schedule page remains the main planning interface.
