"""
Tata Power - Bengaluru EV Charging Network Analysis
====================================================
Reproduces the key metrics behind the Excel dashboards (TATA_POWER_PROJECT.xlsx),
exports chart images to /images and builds the PDF insights report in /reports.

Usage (from the repository root):
    pip install -r requirements.txt
    python analysis/tata_power_ev_analysis.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Tata Power"
IMG = ROOT / "images"
REP = ROOT / "reports"
IMG.mkdir(exist_ok=True)
REP.mkdir(exist_ok=True)

# ---------------------------------------------------------------- palette
BLUE, ORANGE, AQUA, GREY, LIGHT = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984", "#c9c8c3"
INK, INK2 = "#0b0b0b", "#52514e"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": LIGHT,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": "#ecebe8", "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlecolor": INK,
    "axes.titlelocation": "left", "figure.dpi": 150, "savefig.bbox": "tight",
})

# ---------------------------------------------------------------- load
sessions = pd.read_csv(DATA / "charging_sessions.csv", parse_dates=["session_start_time"])
stations = pd.read_csv(DATA / "charging_stations.csv")
customers = pd.read_csv(DATA / "customers.csv")
districts = pd.read_csv(DATA / "districts.csv")
roi = pd.read_csv(DATA / "roi_bengaluru_districts.csv")

df = (sessions.merge(stations, on="station_id", how="left")
              .merge(customers, on="customer_id", how="left", suffixes=("_station", "_customer")))

# ---------------------------------------------------------------- data quality checks
dq = {
    "Sessions with unknown station": int(df["district_name"].isna().sum()),
    "Sessions with unknown customer": int(df["car_model"].isna().sum()),
    "Duplicate session_id": int(sessions["session_id"].duplicated().sum()),
    "total_cost != kWh x tariff (>Rs 0.01)": int(((sessions.kwh_charged * sessions.cost_per_kwh
                                                   - sessions.total_cost).abs() > 0.01).sum()),
    "Distinct plugs_count values": int(stations["plugs_count"].nunique()),
}

# ---------------------------------------------------------------- KPIs
start, end = df.session_start_time.min(), df.session_start_time.max()
days = (end.normalize() - start.normalize()).days + 1
k = {
    "sessions": len(df),
    "kwh": df.kwh_charged.sum(),
    "revenue": df.total_cost.sum(),
    "customers": df.customer_id.nunique(),
    "stations": stations.station_id.nunique(),
    "plugs": int(stations.plugs_count.sum()),
    "avg_kwh": df.kwh_charged.mean(),
    "avg_rev": df.total_cost.mean(),
    "sess_per_cust": len(df) / df.customer_id.nunique(),
    "rev_per_cust": df.total_cost.sum() / df.customer_id.nunique(),
    "days": days,
    "start": start, "end": end,
}
k["sess_per_plug"] = k["sessions"] / k["plugs"]
k["sess_per_day"] = k["sessions"] / days

# monthly (with active-day normalisation, because Aug & Nov are partial months)
df["month"] = df.session_start_time.dt.to_period("M")
monthly = df.groupby("month").agg(sessions=("session_id", "size"), revenue=("total_cost", "sum"),
                                  kwh=("kwh_charged", "sum"),
                                  active_days=("session_start_time", lambda x: x.dt.date.nunique()))
monthly["sessions_per_day"] = monthly.sessions / monthly.active_days

hourly = df.groupby(df.session_start_time.dt.hour).size()

# stations / utilisation
st_sess = df.groupby("station_id").size().rename("sessions").to_frame().join(
    stations.set_index("station_id"))
st_sess["sess_per_plug"] = st_sess.sessions / st_sess.plugs_count
avg_spp = st_sess.sess_per_plug.mean()
over = int((st_sess.sess_per_plug > avg_spp * 1.02).sum())

operators = df.groupby("operator_name").agg(stations=("station_id", "nunique"),
                                            sessions=("session_id", "size"),
                                            revenue=("total_cost", "sum"))
operators["rev_share"] = operators.revenue / operators.revenue.sum() * 100
operators["rev_per_station"] = operators.revenue / operators.stations

# income tier
tier_order = ["High", "Mid-Range", "Low-Mid"]
tier = df.groupby("income_tier_customer").agg(customers=("customer_id", "nunique"),
                                              sessions=("session_id", "size"),
                                              revenue=("total_cost", "sum"),
                                              avg_kwh=("kwh_charged", "mean")).reindex(tier_order)
tier["sess_share"] = tier.sessions / tier.sessions.sum() * 100
tier["rev_share"] = tier.revenue / tier.revenue.sum() * 100
tier["rev_per_cust"] = tier.revenue / tier.customers

tariff = df.groupby("cost_per_kwh").agg(sessions=("session_id", "size"), revenue=("total_cost", "sum"))
tariff["rev_share"] = tariff.revenue / tariff.revenue.sum() * 100

models = df.groupby("car_model").size().sort_values(ascending=False)

# district supply vs demand
dist = (stations.groupby("district_name").agg(stations=("station_id", "nunique"),
                                              existing_plugs=("plugs_count", "sum"))
        .join(districts.set_index("district_name"))
        .join(df.groupby("district_name").agg(sessions=("session_id", "size"),
                                              revenue=("total_cost", "sum"))))
dist["plug_gap"] = dist.projected_plugs - dist.existing_plugs
dist["evs_per_station"] = dist.projected_evs / dist.stations
dist = dist.sort_values("plug_gap", ascending=False)

# ROI / expansion
ct_order = ["AC Level-2 (7.4 kW)", "AC Level-2 (22 kW)", "DC Fast (50 kW)", "DC Ultra-Fast (150 kW)"]
by_ct = roi.groupby("Charger_Type").agg(
    projects=("Project_ID", "size"), capex=("Total_CAPEX_Lakh", "mean"),
    equip=("Equipment_Cost_Lakh", "mean"), civil=("Civil_Work_Lakh", "mean"),
    elec=("Electrical_Infra_Lakh", "mean"), lease=("Capitalized_Land_Lease_10yr_Lakh", "mean"),
    payback=("Payback_Period_Yrs", "mean"), payback_sub=("Adj_Payback_After_Subsidy", "mean"),
    roi=("5yr_ROI_Pct", "mean"), high=("Priority_Tier", lambda x: (x == "High").sum())
).reindex(ct_order)
by_site = roi.groupby("Proposed_Site_Type").agg(projects=("Project_ID", "size"),
                                                 roi=("5yr_ROI_Pct", "mean"),
                                                 payback=("Payback_Period_Yrs", "mean")
                                                 ).sort_values("roi", ascending=False)
by_inc = roi.groupby("Income_Tier").agg(projects=("Project_ID", "size"), roi=("5yr_ROI_Pct", "mean"),
                                        capex=("Total_CAPEX_Lakh", "mean")).reindex(tier_order)
by_dist = roi.groupby("District").agg(projects=("Project_ID", "size"), roi=("5yr_ROI_Pct", "mean"),
                                      high=("Priority_Tier", lambda x: (x == "High").sum())
                                      ).sort_values("roi", ascending=False)
prio = roi.Priority_Tier.value_counts().reindex(["High", "Medium", "Low"])
sub = roi.groupby("Govt_Subsidy_Eligible").agg(projects=("Project_ID", "size"),
                                               payback=("Payback_Period_Yrs", "mean"),
                                               payback_sub=("Adj_Payback_After_Subsidy", "mean"))
high = roi[roi.Priority_Tier == "High"]
r = {
    "capex_total": roi.Total_CAPEX_Lakh.sum(), "rev_total": roi.Annual_Revenue_Lakh.sum(),
    "ebitda_total": roi.Annual_EBITDA_Lakh.sum(), "subsidy_total": roi.Subsidy_Amount_Lakh.sum(),
    "capex_avg": roi.Total_CAPEX_Lakh.mean(), "payback_avg": roi.Payback_Period_Yrs.mean(),
    "payback_sub_avg": roi.Adj_Payback_After_Subsidy.mean(), "roi_avg": roi["5yr_ROI_Pct"].mean(),
    "high_n": len(high), "high_capex": high.Total_CAPEX_Lakh.sum(),
    "high_ebitda": high.Annual_EBITDA_Lakh.sum(), "high_roi": high["5yr_ROI_Pct"].mean(),
    "neg_roi": int((roi["5yr_ROI_Pct"] < 0).sum()),
}
r["high_payback"] = r["high_capex"] / r["high_ebitda"]
top_projects = roi.sort_values("5yr_ROI_Pct", ascending=False).head(10)

# ---------------------------------------------------------------- charts
def save(fig, name):
    fig.savefig(IMG / name, facecolor="white")
    plt.close(fig)
    return IMG / name


def label_bars(ax, bars, fmt, horizontal=False, pad=3):
    for b in bars:
        v = b.get_width() if horizontal else b.get_height()
        if horizontal:
            ax.annotate(fmt(v), (v, b.get_y() + b.get_height() / 2), xytext=(pad, 0),
                        textcoords="offset points", va="center", fontsize=8, color=INK2)
        else:
            ax.annotate(fmt(v), (b.get_x() + b.get_width() / 2, v), xytext=(0, pad),
                        textcoords="offset points", ha="center", fontsize=8, color=INK2)


charts = {}

# 1 daily-normalised demand by month
fig, ax = plt.subplots(figsize=(7, 3.4))
lbl = [f"{p.strftime('%b')}\n({d} days)" for p, d in zip(monthly.index, monthly.active_days)]
bars = ax.bar(lbl, monthly.sessions_per_day, color=BLUE, width=0.55)
label_bars(ax, bars, lambda v: f"{v:,.0f}")
ax.set_title("Avg sessions per day, by month")
ax.set_ylabel("Sessions / day")
ax.set_ylim(0, monthly.sessions_per_day.max() * 1.18)
charts["monthly"] = save(fig, "01_daily_sessions_by_month.png")

# 2 hourly profile
fig, ax = plt.subplots(figsize=(7, 3.2))
ax.plot(hourly.index, hourly.values, color=BLUE, lw=2, marker="o", ms=4)
ax.fill_between(hourly.index, hourly.values, color=BLUE, alpha=0.08)
ax.set_xticks(range(0, 24, 2))
ax.set_xlabel("Hour of day")
ax.set_ylabel("Sessions")
ax.set_title("Charging sessions by hour of day")
ax.set_ylim(0, hourly.max() * 1.15)
charts["hourly"] = save(fig, "02_sessions_by_hour.png")

# 3 income tier share
fig, ax = plt.subplots(figsize=(7, 3.4))
x = range(len(tier))
w = 0.36
b1 = ax.bar([i - w / 2 for i in x], tier.sess_share, w, color=BLUE, label="Share of sessions")
b2 = ax.bar([i + w / 2 for i in x], tier.rev_share, w, color=ORANGE, label="Share of revenue")
label_bars(ax, b1, lambda v: f"{v:.0f}%")
label_bars(ax, b2, lambda v: f"{v:.0f}%")
ax.set_xticks(list(x), tier.index)
ax.set_ylabel("% of total")
ax.set_ylim(0, 60)
ax.legend(frameon=False, loc="upper left")
ax.set_title("Sessions share vs revenue share by customer income tier")
charts["tier"] = save(fig, "03_income_tier_share.png")

# 4 plug gap by district
d4 = dist.sort_values("plug_gap")
fig, ax = plt.subplots(figsize=(7, 5.2))
ax.barh(d4.index, d4.existing_plugs, color=BLUE, label="Existing plugs", height=0.62)
bars = ax.barh(d4.index, d4.plug_gap, left=d4.existing_plugs, color=LIGHT, label="Plug gap",
               height=0.62, edgecolor="white", linewidth=1)
for yb, (_, row) in zip(bars, d4.iterrows()):
    ax.annotate(f"+{row.plug_gap:.0f}", (row.projected_plugs, yb.get_y() + yb.get_height() / 2),
                xytext=(3, 0), textcoords="offset points", va="center", fontsize=8, color=INK2)
ax.set_xlabel("Plugs (existing + gap = projected need)")
ax.set_title("Existing plugs vs projected plug need, by district")
ax.legend(frameon=False, loc="lower right")
ax.grid(axis="y", visible=False)
charts["gap"] = save(fig, "04_plug_gap_by_district.png")

# 5 operator revenue per station
o = operators.sort_values("rev_per_station")
fig, ax = plt.subplots(figsize=(7, 2.9))
cols = [ORANGE if n == "Tata Power" else GREY for n in o.index]
bars = ax.barh(o.index, o.rev_per_station / 1e5, color=cols, height=0.55)
label_bars(ax, bars, lambda v: f"Rs {v:.2f} L", horizontal=True)
ax.set_xlabel("Revenue per station (Rs lakh, Aug-Nov 2024)")
ax.set_xlim(0, o.rev_per_station.max() / 1e5 * 1.2)
ax.set_title("Revenue per station by operator (13 stations each)")
ax.grid(axis="y", visible=False)
charts["operator"] = save(fig, "05_operator_revenue_per_station.png")

# 6 ROI & payback by charger type
fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.3))
short = ["AC 7.4kW", "AC 22kW", "DC 50kW", "DC 150kW"]
c6 = [AQUA if v >= 0 else ORANGE for v in by_ct.roi]
bars = axes[0].bar(short, by_ct.roi, color=c6, width=0.6)
for b, v in zip(bars, by_ct.roi):
    axes[0].annotate(f"{v:.0f}%", (b.get_x() + b.get_width() / 2, v), xytext=(0, 3 if v >= 0 else -11),
                     textcoords="offset points", ha="center", fontsize=8, color=INK2)
axes[0].axhline(0, color=INK2, lw=0.8)
axes[0].set_title("Avg 5-yr ROI")
axes[0].set_ylim(-75, 150)
w = 0.36
xi = range(4)
bb1 = axes[1].bar([i - w / 2 for i in xi], by_ct.payback, w, color=GREY, label="No subsidy")
bb2 = axes[1].bar([i + w / 2 for i in xi], by_ct.payback_sub, w, color=BLUE, label="With subsidy")
label_bars(axes[1], bb1, lambda v: f"{v:.1f}")
axes[1].set_xticks(list(xi), short)
axes[1].set_title("Avg payback (years)")
axes[1].set_ylim(0, 12.5)
axes[1].legend(frameon=False, fontsize=8, loc="upper left")
for a in axes:
    a.tick_params(axis="x", labelsize=8)
fig.suptitle("Proposed projects: returns by charger type", x=0.01, ha="left", fontweight="bold",
             fontsize=12)
fig.tight_layout()
charts["charger"] = save(fig, "06_roi_payback_by_charger.png")

# 7 CAPEX breakdown by charger type
fig, ax = plt.subplots(figsize=(7, 3.0))
left = pd.Series(0.0, index=by_ct.index)
parts = [("equip", "Equipment", BLUE), ("civil", "Civil work", AQUA),
         ("elec", "Electrical infra", "#eda100"), ("lease", "Land lease (10-yr capitalised)", GREY)]
for col, name, colr in parts:
    ax.barh(short, by_ct[col], left=left.values, color=colr, label=name, height=0.6,
            edgecolor="white", linewidth=1.5)
    left += by_ct[col]
for i, v in enumerate(left):
    ax.annotate(f"Rs {v:.0f} L", (v, i), xytext=(3, 0), textcoords="offset points", va="center",
                fontsize=8, color=INK2)
ax.set_xlabel("Avg CAPEX per project (Rs lakh)")
ax.set_xlim(0, left.max() * 1.18)
ax.invert_yaxis()
ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.2))
ax.set_title("CAPEX breakdown by charger type")
ax.grid(axis="y", visible=False)
charts["capex"] = save(fig, "07_capex_breakdown.png")

# 8 ROI by district
d8 = by_dist.sort_values("roi")
fig, ax = plt.subplots(figsize=(7, 5.2))
bars = ax.barh(d8.index, d8.roi, color=[AQUA if v >= 0 else ORANGE for v in d8.roi], height=0.62)
for b, v in zip(bars, d8.roi):
    ax.annotate(f"{v:.0f}%", (v, b.get_y() + b.get_height() / 2), xytext=(3 if v >= 0 else -3, 0),
                textcoords="offset points", va="center", ha="left" if v >= 0 else "right",
                fontsize=8, color=INK2)
ax.axvline(0, color=INK2, lw=0.8)
ax.set_xlim(-45, 165)
ax.set_xlabel("Avg 5-yr ROI of proposed projects (%)")
ax.set_title("Average 5-year ROI by district")
ax.grid(axis="y", visible=False)
charts["roi_dist"] = save(fig, "08_roi_by_district.png")

# ---------------------------------------------------------------- PDF report
font_path = font_manager.findfont("DejaVu Sans")
pdfmetrics.registerFont(TTFont("DV", font_path))
pdfmetrics.registerFont(TTFont("DVB", font_path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")))
from reportlab.lib.fonts import addMapping
addMapping("DV", 0, 0, "DV"); addMapping("DV", 1, 0, "DVB")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontName="DVB", fontSize=16, textColor=colors.HexColor("#0b3d91"), spaceBefore=6, spaceAfter=8)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="DVB", fontSize=12.5, textColor=colors.HexColor(INK), spaceBefore=10, spaceAfter=4)
P = ParagraphStyle("P", parent=ss["BodyText"], fontName="DV", fontSize=9.5, leading=13.5, textColor=colors.HexColor("#222222"))
B = ParagraphStyle("B", parent=P, leftIndent=12, bulletIndent=2, spaceAfter=3)
SM = ParagraphStyle("SM", parent=P, fontSize=8, leading=10.5, textColor=colors.HexColor(INK2))
TITLE = ParagraphStyle("T", parent=H1, fontSize=24, leading=30, alignment=TA_CENTER, spaceAfter=6)
SUB = ParagraphStyle("S", parent=P, fontSize=12, alignment=TA_CENTER, textColor=colors.HexColor(INK2))


def bullets(items):
    return [Paragraph(t, B, bulletText="•") for t in items]


def table(data, widths, num_cols_from=1):
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "DVB", 8.5), ("FONT", (0, 1), (-1, -1), "DV", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f9")]),
        ("ALIGN", (num_cols_from, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5d8de")),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def img(path, width=16.5 * cm):
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        w, h = im.size
    return Image(str(path), width=width, height=width * h / w)


def kpi_grid(items):
    cells = [[Paragraph(f"<font size=15><b>{v}</b></font><br/><font size=8 color='{INK2}'>{l}</font>",
                        ParagraphStyle("k", parent=P, alignment=TA_CENTER, leading=17)) for v, l in row]
             for row in items]
    t = Table(cells, colWidths=[4.1 * cm] * len(items[0]))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5d8de")),
                           ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5d8de")),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8fb")),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 7.5)
    canvas.setFillColor(colors.HexColor(INK2))
    canvas.drawString(2 * cm, 1.2 * cm, "Tata Power EV Charging Network - Bengaluru | Insights Report")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


L = lambda v: f"Rs {v:,.0f} L"
cr = lambda rs: f"Rs {rs / 1e7:.2f} Cr"
gap_total = int(dist.plug_gap.sum())
top5_gap = dist.head(5)
tata = operators.loc["Tata Power"]
best_op = operators.rev_per_station.idxmax()
hi_t, lo_t = tier.loc["High"], tier.loc["Low-Mid"]
ac = roi[roi.Charger_Type.str.startswith("AC")]
dc = roi[roi.Charger_Type.str.startswith("DC")]
peak_hours = hourly.sort_values(ascending=False).head(3).index.sort_values()
low_hours = hourly.sort_values().head(3).index.sort_values()
sub_gain = sub.loc["Yes", "payback"] - sub.loc["Yes", "payback_sub"]
lease_share_ac = (ac.Capitalized_Land_Lease_10yr_Lakh / ac.Total_CAPEX_Lakh).mean() * 100

story = []
story += [Spacer(1, 4.5 * cm), Paragraph("Tata Power EV Charging Network", TITLE),
          Paragraph("Bengaluru - Utilisation, Customer Demand &amp; Expansion ROI", SUB), Spacer(1, 0.6 * cm),
          Paragraph("Data Analyst Insights Report", SUB), Spacer(1, 2.2 * cm),
          kpi_grid([[(f"{k['sessions']:,}", "Charging sessions"), (f"{k['kwh'] / 1e6:.2f} GWh", "Energy delivered"),
                     (cr(k["revenue"]), "Session revenue"), (f"{k['customers']:,}", "Customers")],
                    [(f"{k['stations']}", "Stations / 18 districts"), (f"{k['plugs']}", "Plugs"),
                     (f"{len(roi)}", "Proposed projects"), (f"{r['roi_avg']:.1f}%", "Avg 5-yr ROI")]]),
          Spacer(1, 2.5 * cm),
          Paragraph(f"Data period: {k['start']:%d %b %Y} - {k['end']:%d %b %Y} ({k['days']} days). "
                    "Tools: Excel (Power Query, Power Pivot data model, DAX measures, PivotTables, slicers, "
                    "dashboards) and Python (pandas, matplotlib) for validation and this report.",
                    ParagraphStyle("c", parent=SM, alignment=TA_CENTER)),
          PageBreak()]

story += [Paragraph("1. Executive summary", H1),
          Paragraph("Tata Power is planning the next phase of its EV charging network in Bengaluru. This analysis "
                    "combines three months of operational data from 65 stations (all five operators in the city) "
                    "with a financial model of 98 proposed sites to answer three questions: how the current network "
                    "is used, who the customers are, and where new capital should go.", P), Spacer(1, 6)]
story += bullets([
    f"<b>Demand is steady, and it exceeds capacity.</b> The network handles about <b>{k['sess_per_day']:,.0f} "
    f"sessions a day</b>, flat from August to November. Every plug serves ~{k['sess_per_plug']:.0f} sessions, "
    f"and utilisation is almost the same at every station ({st_sess.sess_per_plug.min():.0f}-{st_sess.sess_per_plug.max():.0f} per plug). "
    "That points to a network that is capacity-bound rather than location-bound.",
    f"<b>The city needs ~{dist.projected_plugs.sum():,} plugs but has {k['plugs']}.</b> The gap is <b>{gap_total} plugs</b> "
    f"(the network would need to be {dist.projected_plugs.sum() / k['plugs']:.1f}x its current size). Five districts "
    f"(Jayanagar, Rajajinagar, HSR Layout, Indiranagar, Koramangala) account for {top5_gap.plug_gap.sum() / gap_total * 100:.0f}% of it.",
    f"<b>High-income customers are worth 3x more.</b> They are {hi_t.sess_share:.0f}% of sessions but {hi_t.rev_share:.0f}% of revenue "
    f"(Rs {hi_t.rev_per_cust:,.0f} per customer vs Rs {lo_t.rev_per_cust:,.0f} for Low-Mid).",
    f"<b>AC chargers are where the returns are.</b> All {r['high_n']} High-priority projects are AC Level-2. "
    f"On average, AC projects return {ac['5yr_ROI_Pct'].mean():.0f}% over five years and DC projects {dc['5yr_ROI_Pct'].mean():.0f}%. "
    f"{r['neg_roi']} of the 98 projects lose money over five years, nearly all of them DC.",
    f"<b>Recommended first wave:</b> the {r['high_n']} High-priority projects. They cost <b>{L(r['high_capex'])}</b> "
    f"(Rs {r['high_capex'] / 100:.1f} Cr), generate {L(r['high_ebitda'])} EBITDA a year, average a "
    f"{r['high_roi']:.0f}% 5-year ROI and pay back in about {r['high_payback']:.1f} years as a portfolio.",
])

story += [Paragraph("2. Data &amp; method", H1),
          table([["Dataset", "Rows", "Grain / purpose"],
                 ["charging_sessions", f"{len(sessions):,}", "One row per charging session (fact table)"],
                 ["charging_stations", f"{len(stations)}", "Station master: district, operator, plugs, lat/long"],
                 ["customers", f"{len(customers):,}", "Customer income tier, car model, battery size"],
                 ["districts", f"{len(districts)}", "Projected EVs, plugs and regular customers per district"],
                 ["roi_bengaluru_districts", f"{len(roi)}", "CAPEX / revenue / payback / ROI per proposed project"]],
                [4.3 * cm, 2 * cm, 10.2 * cm], num_cols_from=1),
          Spacer(1, 6),
          Paragraph("<b>Workflow.</b> I loaded the CSVs through Power Query and related them in the Power Pivot data model "
                    "(sessions -> stations -> districts; sessions -> customers; ROI -> districts). DAX measures (Total Sessions, "
                    "Sessions per Plug, Revenue per Customer, Plug Gap, Avg Payback, Avg 5-yr ROI and others) feed 33 PivotTables "
                    "and four dashboards with Month, Income Tier, Charger Type and Priority Tier slicers. I added a "
                    "<i>Battery Band</i> column and Month/Year columns for segmentation. The figures were then re-computed "
                    "independently in Python to check them.", P),
          Paragraph("Data quality checks", H2),
          table([["Check", "Result"]] + [[a, f"{b:,}"] for a, b in dq.items()], [11 * cm, 5.5 * cm]),
          Spacer(1, 4)]
story += bullets([
    f"<b>Reclassified ROI tiers:</b> 23 projects with a negative 5-year ROI had been tagged 'Medium'. I moved them to 'Low', "
    "and added a <i>Capitalized_Land_Lease_10yr</i> column so the CAPEX formula can be traced.",
    "<b>plugs_count is a placeholder:</b> every station has exactly 6 plugs. Plug-based metrics depend on this value, so "
    "it should be checked in the field.",
    f"<b>Partial months:</b> the sessions cover {k['start']:%d %b} to {k['end']:%d %b %Y}. August and November are incomplete, "
    "so a raw monthly total makes November look like a collapse. Section 3 uses sessions per day instead.",
])


story += [Paragraph("3. Network utilisation", H1), img(charts["monthly"], 13 * cm), Spacer(1, 4)]
story += bullets([
    f"Daily demand is flat at ~{monthly.sessions_per_day.iloc[1]:,.0f} sessions a day in September and October. The low November "
    f"total ({monthly.sessions.iloc[-1]:,} sessions) comes from {monthly.active_days.iloc[-1]} days of data, not falling demand.",
    f"The average session delivers {k['avg_kwh']:.1f} kWh and earns Rs {k['avg_rev']:.0f}. Per station, the network averages "
    f"{k['sessions'] / k['stations']:,.0f} sessions and {k['kwh'] / k['stations']:,.0f} kWh.",
    f"Utilisation is very even: {st_sess.sess_per_plug.min():.0f}-{st_sess.sess_per_plug.max():.0f} sessions per plug, a coefficient of variation of only "
    f"{st_sess.sess_per_plug.std() / avg_spp * 100:.1f}%. No station sits idle. The best way to grow volume is to add plugs, not to rebalance existing load.",
])
story += [img(charts["hourly"], 13 * cm), Spacer(1, 4)]
story += bullets([
    f"Daytime peaks fall at {', '.join(f'{h:02d}:00' for h in peak_hours)} (~{hourly.max():,} sessions in the busiest hour). "
    f"The quietest hours are {', '.join(f'{h:02d}:00' for h in low_hours)}, at about {hourly.min() / hourly.max() * 100:.0f}% of peak.",
    "Off-peak (night) tariffs could move flexible fleet charging into the 23:00-05:00 trough and free daytime capacity.",
])
story.append(PageBreak())

story += [Paragraph("4. Customers &amp; demand behaviour", H1), img(charts["tier"], 14 * cm), Spacer(1, 4),
          table([["Income tier", "Customers", "Sessions", "Revenue", "Avg kWh", "Rev / customer"]] +
                [[t, f"{row.customers:,.0f}", f"{row.sessions:,.0f}", cr(row.revenue), f"{row.avg_kwh:.1f}",
                  f"Rs {row.rev_per_cust:,.0f}"] for t, row in tier.iterrows()],
                [3 * cm, 2.4 * cm, 2.4 * cm, 3 * cm, 2.2 * cm, 3.5 * cm]), Spacer(1, 6)]
story += bullets([
    f"Low-Mid customers are half of all sessions ({lo_t.sess_share:.0f}%) but bring in only {lo_t.rev_share:.0f}% of revenue. "
    "Their vehicles have small batteries (2- and 3-wheelers, entry-level cars), so each session draws fewer kWh at lower tariffs.",
    f"High-income customers charge {hi_t.avg_kwh:.1f} kWh per session, against {lo_t.avg_kwh:.1f} kWh for Low-Mid, "
    f"and are worth {hi_t.rev_per_cust / lo_t.rev_per_cust:.1f}x more each. Loyalty or subscription plans aimed at them would protect the most valuable revenue.",
    f"Customers average {k['sess_per_cust']:.2f} sessions each and Rs {k['rev_per_cust']:,.0f} in revenue. "
    f"The most-used models are {', '.join(models.index[:3])}, all affordable, high-frequency vehicles.",
    f"The mid tariff (Rs 18/kWh) carries {tariff.loc[18.0, 'sessions'] / k['sessions'] * 100:.0f}% of sessions and "
    f"{tariff.loc[18.0, 'rev_share']:.0f}% of revenue. It is the volume engine of the network.",
])
story.append(PageBreak())

story += [Paragraph("5. Supply gap &amp; competition", H1), img(charts["gap"], 12.5 * cm), Spacer(1, 4),
          table([["District", "Tier", "Stations", "Proj. EVs", "EVs / station", "Plug gap"]] +
                [[n, row.income_tier, f"{row.stations:.0f}", f"{row.projected_evs:,.0f}", f"{row.evs_per_station:,.0f}",
                  f"{row.plug_gap:.0f}"] for n, row in top5_gap.iterrows()],
                [3.6 * cm, 2.4 * cm, 2 * cm, 2.6 * cm, 2.9 * cm, 2.6 * cm]), Spacer(1, 6)]
story += bullets([
    f"Supply is not tracking demand. Jayanagar (3,600 projected EVs) has 5 stations, the same as Indiranagar, while "
    f"Yelahanka has ~{dist.loc['Yelahanka', 'evs_per_station']:.0f} EVs per station, the most underserved ratio in the city.",
    "Electronic City, JP Nagar, Rajarajeshwari Nagar and Byatarayanapura are close to their projected need, so new sites there have low priority.",
])
story += [img(charts["operator"], 12.5 * cm), Spacer(1, 4)]
story += bullets([
    f"The market is evenly split: each of the five operators runs 13 stations and takes 19-21% of revenue. "
    f"Tata Power's share is {tata.rev_share:.1f}%, Rs {tata.rev_per_station / 1e5:.2f} L per station, compared with "
    f"Rs {operators.loc[best_op, 'rev_per_station'] / 1e5:.2f} L for {best_op}, the leader.",
    "Tata Power has no scale advantage yet. Winning the high-gap districts first is the clearest way to build share.",
])
story.append(PageBreak())

story += [Paragraph("6. Expansion economics (98 proposed projects)", H1),
          kpi_grid([[(L(r["capex_avg"]), "Avg CAPEX / project"), (f"{r['payback_avg']:.2f} yrs", "Avg payback"),
                     (f"{r['payback_sub_avg']:.2f} yrs", "Avg payback w/ subsidy"), (f"{r['roi_avg']:.1f}%", "Avg 5-yr ROI")]]),
          Spacer(1, 8), img(charts["charger"], 15.5 * cm), Spacer(1, 4),
          table([["Charger type", "Projects", "Avg CAPEX", "Payback", "w/ subsidy", "5-yr ROI", "High tier"]] +
                [[c, f"{row.projects:.0f}", L(row.capex), f"{row.payback:.1f} y", f"{row.payback_sub:.1f} y",
                  f"{row.roi:.0f}%", f"{row.high:.0f}"] for c, row in by_ct.iterrows()],
                [4.4 * cm, 1.8 * cm, 2.4 * cm, 1.9 * cm, 2.1 * cm, 2 * cm, 1.9 * cm]), Spacer(1, 6)]
story += bullets([
    f"AC Level-2 (7.4 kW) is the clear winner: {by_ct.loc[ct_order[0], 'roi']:.0f}% average ROI and a "
    f"{by_ct.loc[ct_order[0], 'payback']:.1f}-year payback. DC Ultra-Fast costs {by_ct.loc[ct_order[3], 'capex'] / by_ct.loc[ct_order[0], 'capex']:.1f}x "
    f"as much and takes {by_ct.loc[ct_order[3], 'payback']:.1f} years to pay back.",
    f"Subsidies take ~{sub_gain:.1f} years off payback for the {sub.loc['Yes', 'projects']} eligible projects "
    f"(total subsidy {L(r['subsidy_total'])}). Every eligible project should apply.",
])
story += [KeepTogether([img(charts["capex"], 14 * cm), Spacer(1, 4)] + bullets([
    f"Ten years of land lease, capitalised under Ind AS 116, is ~{lease_share_ac:.0f}% of an AC project's CAPEX. "
    "Choosing low-lease sites (residential complexes, bus depots) has a large effect on ROI."]))]
story.append(PageBreak())

story += [Paragraph("7. Where to invest", H1), img(charts["roi_dist"], 14.5 * cm), Spacer(1, 4)]
story += bullets([
    f"Mid-Range districts give the best returns (avg ROI {by_inc.loc['Mid-Range', 'roi']:.0f}%). High-income districts "
    f"return only {by_inc.loc['High', 'roi']:.0f}%, because their proposals lean towards expensive DC chargers "
    f"(avg CAPEX {L(by_inc.loc['High', 'capex'])} vs {L(by_inc.loc['Mid-Range', 'capex'])}) on high-lease land.",
    f"By site type, Residential Complexes ({by_site.loc['Residential Complex', 'roi']:.0f}% ROI) and Bus Depots "
    f"({by_site.loc['Bus Depot', 'roi']:.0f}%) do best. Malls / Retail is the weakest ({by_site.loc['Mall / Retail', 'roi']:.0f}%).",
])
story += [Paragraph("Top 10 projects by 5-year ROI", H2),
          table([["Project", "District", "Site", "Charger", "CAPEX", "Payback", "ROI"]] +
                [[p.Project_ID, p.District, p.Proposed_Site_Type, p.Charger_Type.replace("AC Level-2 ", "AC "),
                  L(p.Total_CAPEX_Lakh), f"{p.Payback_Period_Yrs:.1f} y", f"{p['5yr_ROI_Pct']:.0f}%"]
                 for _, p in top_projects.iterrows()],
                [2.3 * cm, 2.8 * cm, 3.3 * cm, 2.6 * cm, 2 * cm, 1.7 * cm, 1.7 * cm], num_cols_from=4)]
story.append(PageBreak())

story += [Paragraph("8. Recommendations", H1)]
recs = [
    ("Fund the 54 High-priority AC projects first",
     f"{L(r['high_capex'])} CAPEX, {L(r['high_ebitda'])} EBITDA a year, about a {r['high_payback']:.1f}-year portfolio payback. "
     "Phase them by plug gap, starting with Jayanagar, Rajajinagar, HSR Layout and Yelahanka."),
    ("Put DC fast charging on hold or redesign it",
     f"The {len(dc)} DC projects average a {dc['5yr_ROI_Pct'].mean():.0f}% ROI. Keep DC only for highway or corridor sites and fleet "
     "contracts with guaranteed volume, or renegotiate the lease and equipment terms."),
    ("Fix the high-income district plan",
     "Indiranagar, Koramangala and Whitefield have strong demand but weak project ROI. Rework those proposals around AC chargers "
     "at residential and office sites instead of DC at malls and petrol stations."),
    ("Grow revenue per session",
     "Offer loyalty or subscription plans for High and Mid-Range customers, use time-of-day pricing to fill the night trough, "
     "and push fleet (2- and 3-wheeler) partnerships for volume."),
    ("Claim every subsidy",
     f"{sub.loc['Yes', 'projects']} projects are eligible (FAME-II or state EV policy). The subsidy cuts their payback by ~{sub_gain:.1f} years."),
    ("Improve the data",
     "Check plugs_count in the field, collect session duration and idle time to measure real utilisation, "
     "and extend the history to 12 months to capture seasonality."),
]
for i, (t, d) in enumerate(recs, 1):
    story += [Paragraph(f"<b>{i}. {t}</b>", P), Paragraph(d, ParagraphStyle("rd", parent=P, leftIndent=14, spaceAfter=7))]
story += [Paragraph("Limitations", H2),
          Paragraph("The session data is ~3 months long and includes all operators, not only Tata Power. ROI figures are "
                    "planning-model projections, not actuals. EBITDA-based ROI leaves out tax, depreciation and the "
                    "time value of money, so an NPV/IRR view is a natural next step.", P)]

doc = SimpleDocTemplate(str(REP / "Tata_Power_EV_Insights_Report.pdf"), pagesize=A4,
                        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=2 * cm,
                        title="Tata Power EV Charging Network - Insights Report", author="Data Analyst")
doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=footer)

# ---------------------------------------------------------------- console summary
print(f"Sessions {k['sessions']:,} | kWh {k['kwh']:,.0f} | Revenue {cr(k['revenue'])} | "
      f"Sessions/plug {k['sess_per_plug']:.1f} | Plug gap {gap_total}")
print(f"Projects {len(roi)} | Avg ROI {r['roi_avg']:.2f}% | High tier {r['high_n']} "
      f"(CAPEX {r['high_capex']:.0f} L, EBITDA {r['high_ebitda']:.0f} L)")
print("Charts ->", IMG)
print("Report ->", REP / "Tata_Power_EV_Insights_Report.pdf")
