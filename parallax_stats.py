#!/usr/bin/env python3
"""
Re-analysis of the Gauntlet human study in response to CAL reviewer comments.

Input : Parallax_Scores_-_Sheet1.csv  (one row per comparison, 20 rows)
Output: stats_report.txt, table1_revised.tex, fig_per_comparison.pdf/.png

Score columns 1-5 are, in order: Mechanistic Accuracy, Insight Depth,
Critical Rigor, Calibration, Usefulness (verified: reproduces Table I exactly).

Methods
-------
* Pooled analysis of all 20 comparisons (no Paper 1 / Paper 2 split).
* Effect size: mean paired difference (Gauntlet - human), with a 95% CI from
  a bootstrap that resamples whole judges (clustered bootstrap), cross-checked
  against a cluster-robust t interval (CR1, df = #judges - 1). The table
  reports whichever interval is wider.
* p-values: two-sided Wilcoxon signed-rank test, zero differences dropped,
  tied ranks averaged, p-value by exact enumeration of all sign flips
  (same test family as the original analysis, which used SciPy's default).
  Holm correction across the five dimensions.
* Robustness: sign-flip permutation tests that flip all comparisons scored
  by one judge together (and, separately, by one analyst); judge-level
  aggregation (each judge collapsed to one mean difference); sign tests on
  win/loss counts; crossed random-effects model (judge + analyst); exclusion
  of the senior reader.
* Overall preference: sign test (tie dropped), Wilson 95% CI, plus a
  judge-clustered bootstrap CI.

Every number cited in the revised manuscript and the response letter is
printed by this script into stats_report.txt. Run:

    python3 parallax_stats.py <scores.csv> <output_dir>

Requires numpy, pandas, scipy, statsmodels, matplotlib. Results are
deterministic given SEED.
"""
import itertools
import sys
import warnings
from math import comb, sqrt

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

IN = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/Parallax_Scores_-_Sheet1.csv"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/mnt/user-data/outputs"
SEED, B = 2026, 20000
SENIOR = "Karu (senior)"

DIMS = ["MechAcc", "Insight", "Rigor", "Calib", "Useful"]
LABEL = {"MechAcc": "Mechanistic Accuracy", "Insight": "Insight Depth",
         "Rigor": "Critical Rigor", "Calib": "Calibration",
         "Useful": "Usefulness", "Total": "Total (/25)"}
PREF_SCORE = {"AI Clearly": 2, "AI Somewhat": 1, "Tie": 0,
              "Human Somewhat": -1, "Human Clearly": -2}

lines = []
def say(s=""):
    print(s)
    lines.append(s)

# ----------------------------------------------------------------------------
# Load
# ----------------------------------------------------------------------------
df = pd.read_csv(IN)
df.columns = (["paper", "analyst", "judge"] + [f"H_{d}" for d in DIMS]
              + [f"G_{d}" for d in DIMS] + ["pref"])
df["pref"] = df["pref"].str.strip()
df["round"] = df.groupby("analyst").cumcount() + 1          # original Paper 1/2
df["H_Total"] = df[[f"H_{d}" for d in DIMS]].sum(axis=1)
df["G_Total"] = df[[f"G_{d}" for d in DIMS]].sum(axis=1)
for d in DIMS + ["Total"]:
    df[f"d_{d}"] = df[f"G_{d}"] - df[f"H_{d}"]
df["pref_score"] = df["pref"].map(PREF_SCORE)
assert df["pref_score"].notna().all(), "unrecognised preference label"
df["senior"] = df["judge"] == SENIOR
first = lambda s: s.split()[0].lower()

# ----------------------------------------------------------------------------
# Statistical helpers
# ----------------------------------------------------------------------------
def wilcoxon_exact(d):
    """Two-sided Wilcoxon signed-rank p via exhaustive sign flips (handles ties)."""
    d = np.asarray(d, float)
    d = d[d != 0]
    n = len(d)
    if n == 0:
        return 1.0, 0
    r2 = (2 * stats.rankdata(np.abs(d))).astype(int)        # doubled midranks
    total = int(r2.sum())
    dist = np.zeros(total + 1, dtype=object)                 # exact counts
    dist[0] = 1
    for r in r2:
        new = dist.copy()
        new[r:] += dist[:-r] if r > 0 else 0
        dist = new
    obs = int(r2[d > 0].sum())
    dev_obs = abs(2 * obs - total)
    k = np.arange(total + 1)
    extreme = np.abs(2 * k - total) >= dev_obs
    return float(dist[extreme].sum() / (2 ** n)), n

def rank_biserial(d):
    d = np.asarray(d, float); d = d[d != 0]
    if len(d) == 0:
        return 0.0
    r = stats.rankdata(np.abs(d))
    tp, tm = r[d > 0].sum(), r[d < 0].sum()
    return (tp - tm) / (tp + tm)

def hodges_lehmann(d):
    d = np.asarray(d, float)
    walsh = [(d[i] + d[j]) / 2 for i in range(len(d)) for j in range(i, len(d))]
    return float(np.median(walsh))

def cluster_signflip_p(d, groups):
    """Exact two-sided permutation p flipping each cluster's differences together."""
    s = pd.Series(np.asarray(d, float)).groupby(np.asarray(groups)).sum().values
    obs = abs(s.sum())
    G = len(s)
    signs = np.array(list(itertools.product([1, -1], repeat=G)))
    null = np.abs(signs @ s)
    return float(np.mean(null >= obs - 1e-9))

def cluster_bootstrap_ci(d, groups, rng, stat=np.mean):
    d = np.asarray(d, float); groups = np.asarray(groups)
    ids = np.unique(groups)
    idx = {g: np.where(groups == g)[0] for g in ids}
    boots = np.empty(B)
    for b in range(B):
        pick = rng.choice(ids, size=len(ids), replace=True)
        boots[b] = stat(np.concatenate([d[idx[g]] for g in pick]))
    return np.percentile(boots, [2.5, 97.5])

def cluster_robust_t_ci(d, groups):
    d = np.asarray(d, float)
    n, dbar = len(d), d.mean()
    sg = pd.Series(d - dbar).groupby(np.asarray(groups)).sum().values
    G = len(sg)
    se = sqrt((G / (G - 1)) * np.sum(sg ** 2) / n ** 2)
    t = stats.t.ppf(0.975, G - 1)
    return np.array([dbar - t * se, dbar + t * se])

def wider(a, b):
    return a if (a[1] - a[0]) >= (b[1] - b[0]) else b

def analyse(sub, rng, tag):
    """Per-outcome results for a subset of comparisons."""
    rows = []
    for d in DIMS + ["Total"]:
        x = sub[f"d_{d}"].values
        p_w, n_nz = wilcoxon_exact(x)
        ci_boot = cluster_bootstrap_ci(x, sub["judge"], rng)
        ci_crt = cluster_robust_t_ci(x, sub["judge"])
        rows.append(dict(
            outcome=d, n=len(x),
            H=sub[f"H_{d}"].mean(), G=sub[f"G_{d}"].mean(), delta=x.mean(),
            ci_boot=ci_boot, ci_crt=ci_crt, ci=wider(ci_boot, ci_crt),
            wins=int((x > 0).sum()), ties=int((x == 0).sum()), losses=int((x < 0).sum()),
            hl=hodges_lehmann(x), rbc=rank_biserial(x),
            p_wilcoxon=p_w, n_nonzero=n_nz,
            p_judge=cluster_signflip_p(x, sub["judge"]),
            p_analyst=cluster_signflip_p(x, sub["analyst"]),
        ))
    res = pd.DataFrame(rows).set_index("outcome")
    for col in ["p_wilcoxon", "p_judge", "p_analyst"]:
        res[f"{col}_holm"] = np.nan
        res.loc[DIMS, f"{col}_holm"] = multipletests(res.loc[DIMS, col], method="holm")[1]
    return res

def preference(sub, rng):
    w = int((sub["pref_score"] > 0).sum()); l = int((sub["pref_score"] < 0).sum())
    t = int((sub["pref_score"] == 0).sum())
    lo, hi = proportion_confint(w, w + l, method="wilson")
    p_sign = stats.binomtest(w, w + l, 0.5).pvalue
    # judge-clustered bootstrap on win rate (ties excluded)
    groups = sub["judge"].values; ids = np.unique(groups)
    s = sub["pref_score"].values
    boots = []
    for _ in range(B):
        pick = rng.choice(ids, size=len(ids), replace=True)
        v = np.concatenate([s[groups == g] for g in pick])
        dec = v[v != 0]
        if len(dec):
            boots.append(np.mean(dec > 0))
    cb = np.percentile(boots, [2.5, 97.5])
    ps = sub["pref_score"].values
    return dict(wins=w, losses=l, ties=t, rate=w / (w + l), wilson=(lo, hi),
                p_sign=p_sign, clustered=cb, mean_score=ps.mean(),
                p_score_wilcoxon=wilcoxon_exact(ps)[0],
                p_score_judge=cluster_signflip_p(ps, groups),
                counts=sub["pref"].value_counts().to_dict())

fmt_p = lambda p: "<0.001" if p < 0.001 else f"{p:.3f}"
fmt_ci = lambda c: f"[{c[0]:+.2f}, {c[1]:+.2f}]"

# ----------------------------------------------------------------------------
# 0. Data checks
# ----------------------------------------------------------------------------
say("=" * 78)
say("GAUNTLET HUMAN STUDY: RE-ANALYSIS FOR CAL REVISION")
say("=" * 78)
say(f"Rows (comparisons): {len(df)}")
say(f"Analysts: {df['analyst'].nunique()}  |  Judges: {df['judge'].nunique()}")
say("Comparisons per judge: " + ", ".join(f"{k}={v}" for k, v in df['judge'].value_counts().items()))
say("")
say("Check against original Table I (per round means):")
for r in [1, 2]:
    s = df[df["round"] == r]
    say(f"  Paper {r}: human " + " ".join(f"{s[f'H_{d}'].mean():.2f}" for d in DIMS)
        + f"  | Gauntlet " + " ".join(f"{s[f'G_{d}'].mean():.2f}" for d in DIMS)
        + f"  | totals {s['H_Total'].mean():.1f} vs {s['G_Total'].mean():.1f}")
say("Original one-sided p-values reproduced with scipy.stats.wilcoxon(alternative='greater'):")
for r in [1, 2]:
    s = df[df["round"] == r]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ps = [stats.wilcoxon(s[f"G_{d}"], s[f"H_{d}"], alternative="greater").pvalue
              for d in DIMS + ["Total"]]
    say(f"  Paper {r}: " + "  ".join(f"{p:.3f}" for p in ps))
say("")
selfjudge = df[df["analyst"].map(first) == df["judge"].map(first)]
say(f"Self-judged comparisons: {len(selfjudge)}")
pairs = {(first(a), first(j)) for a, j in zip(df["analyst"], df["judge"])}
recip = sorted({tuple(sorted(p)) for p in pairs if (p[1], p[0]) in pairs})
say(f"Reciprocal judging pairs (A judged B and B judged A): {recip if recip else 'none'}")
judges_first = set(df["judge"].map(first)); analysts_first = set(df["analyst"].map(first))
say(f"Analysts who never judged: {sorted(analysts_first - judges_first)}")
say(f"Judges who were not analysts: {sorted(judges_first - analysts_first)}")
say("")

# ----------------------------------------------------------------------------
# 1. Main pooled analysis
# ----------------------------------------------------------------------------
rng = np.random.default_rng(SEED)
main = analyse(df, rng, "all")
pref = preference(df, rng)

say("-" * 78)
say("1. POOLED ANALYSIS, ALL 20 COMPARISONS")
say("-" * 78)
say(f"{'Outcome':<22}{'Human':>6}{'Gaunt':>7}{'Delta':>7}  {'95% CI (reported)':<18}{'G/T/H':>9}"
    f"{'p':>8}{'p Holm':>8}")
for o, r in main.iterrows():
    ph = "" if o == "Total" else fmt_p(r.p_wilcoxon_holm)
    say(f"{LABEL[o]:<22}{r.H:6.2f}{r.G:7.2f}{r.delta:+7.2f}  {fmt_ci(r.ci):<18}"
        f"{f'{r.wins}/{r.ties}/{r.losses}':>9}{fmt_p(r.p_wilcoxon):>8}{ph:>8}")
say("")
say("CI detail (judge-clustered bootstrap vs cluster-robust t):")
for o, r in main.iterrows():
    say(f"  {LABEL[o]:<22} bootstrap {fmt_ci(r.ci_boot)}   CR1-t {fmt_ci(r.ci_crt)}")
say("")
say("Other effect sizes:")
for o, r in main.iterrows():
    say(f"  {LABEL[o]:<22} Hodges-Lehmann {r.hl:+.1f}   rank-biserial r = {r.rbc:+.2f}"
        f"   (non-zero pairs in Wilcoxon: {r.n_nonzero})")
say("")
say("Overall preference:")
say(f"  Counts: {pref['counts']}")
say(f"  Gauntlet {pref['wins']}, human {pref['losses']}, tie {pref['ties']}"
    f"  -> win rate {pref['rate']:.0%} (ties excluded)")
say(f"  Wilson 95% CI [{pref['wilson'][0]:.0%}, {pref['wilson'][1]:.0%}]"
    f"   judge-clustered bootstrap [{pref['clustered'][0]:.0%}, {pref['clustered'][1]:.0%}]")
say(f"  Two-sided sign test p = {pref['p_sign']:.3f}")
say(f"  Preference strength (-2 human clearly ... +2 Gauntlet clearly): mean {pref['mean_score']:+.2f},"
    f" Wilcoxon p = {fmt_p(pref['p_score_wilcoxon'])}, judge-clustered p = {fmt_p(pref['p_score_judge'])}")
say("")
two = multipletests([main.loc['Total', 'p_wilcoxon'], pref['p_sign']], method="holm")[1]
say(f"  If Total and Overall preference are treated as a family of two (Holm): "
    f"{two[0]:.3f}, {two[1]:.3f}")
say("")

# ----------------------------------------------------------------------------
# 2. Robustness to repeated judges / analysts
# ----------------------------------------------------------------------------
say("-" * 78)
say("2. ROBUSTNESS: CLUSTERING BY JUDGE AND BY ANALYST")
say("-" * 78)
say("Exact sign-flip permutation tests that flip whole clusters together (two-sided).")
say(f"{'Outcome':<22}{'Wilcoxon':>10}{'Holm':>8}{'Judge-cl.':>11}{'Holm':>8}{'Analyst-cl.':>13}{'Holm':>8}")
for o, r in main.iterrows():
    h = lambda c: "" if o == "Total" else fmt_p(r[c])
    say(f"{LABEL[o]:<22}{fmt_p(r.p_wilcoxon):>10}{h('p_wilcoxon_holm'):>8}"
        f"{fmt_p(r.p_judge):>11}{h('p_judge_holm'):>8}{fmt_p(r.p_analyst):>13}{h('p_analyst_holm'):>8}")
say(f"Note: with 10 clusters the smallest attainable cluster p is {2/1024:.4f}.")
say("")

# Judge-level aggregation: collapse each judge to one mean difference, then test
# across the 10 judges. The most conservative of the judge-level analyses,
# because every judge counts equally regardless of how many comparisons they
# scored.
say("Judge-level aggregation (each judge's differences averaged, then tested across judges):")
say(f"{'Outcome':<22}{'judges +/-/0':>14}{'mean':>8}{'Wilcoxon p':>12}{'Holm':>8}")
jrows = {}
for d in DIMS + ["Total"]:
    jm = df[f"d_{d}"].groupby(df["judge"]).mean()
    jrows[d] = (int((jm > 0).sum()), int((jm < 0).sum()), int((jm == 0).sum()),
                jm.mean(), wilcoxon_exact(jm.values)[0])
jholm = multipletests([jrows[d][4] for d in DIMS], method="holm")[1]
for i, d in enumerate(DIMS + ["Total"]):
    pos, neg, zer, mu, p = jrows[d]
    h = fmt_p(jholm[i]) if d != "Total" else ""
    say(f"{LABEL[d]:<22}{f'{pos}/{neg}/{zer}':>14}{mu:>+8.2f}{fmt_p(p):>12}{h:>8}")
jp = df["pref_score"].groupby(df["judge"]).mean()
say(f"{'Overall preference':<22}{f'{int((jp>0).sum())}/{int((jp<0).sum())}/{int((jp==0).sum())}':>14}"
    f"{jp.mean():>+8.2f}{fmt_p(wilcoxon_exact(jp.values)[0]):>12}")
say("Per-judge mean total-score differences: "
    + ", ".join(f"{k} {v:+.2f}" for k, v in
                df.groupby("judge")["d_Total"].mean().sort_values(ascending=False).items()))
say("")

# Sign tests: direction only, ignoring the size of each difference.
say("Sign tests on win/loss counts (two-sided, ties dropped):")
say(f"{'Outcome':<22}{'G/T/H':>10}{'p':>10}{'Holm':>8}")
srows = {d: (int((df[f'd_{d}'] > 0).sum()), int((df[f'd_{d}'] == 0).sum()),
             int((df[f'd_{d}'] < 0).sum())) for d in DIMS + ["Total"]}
sp = {d: stats.binomtest(srows[d][0], srows[d][0] + srows[d][2], 0.5).pvalue for d in DIMS + ["Total"]}
sholm = multipletests([sp[d] for d in DIMS], method="holm")[1]
for i, d in enumerate(DIMS + ["Total"]):
    g, t, h_ = srows[d]
    hh = fmt_p(sholm[i]) if d != "Total" else ""
    say(f"{LABEL[d]:<22}{f'{g}/{t}/{h_}':>10}{fmt_p(sp[d]):>10}{hh:>8}")
say("")

try:
    import statsmodels.formula.api as smf
    with warnings.catch_warnings(record=True) as wlist:
        warnings.simplefilter("always")
        m = smf.mixedlm("d_Total ~ 1", data=df, groups=np.ones(len(df)), re_formula="0",
                        vc_formula={"judge": "0 + C(judge)", "analyst": "0 + C(analyst)"}
                        ).fit(reml=True)
    ci = m.conf_int().loc["Intercept"].values
    say("Crossed random-effects model, Total difference ~ 1 + (1|judge) + (1|analyst):")
    say(f"  Intercept (Gauntlet advantage) = {m.params['Intercept']:+.2f}, "
        f"95% CI [{ci[0]:+.2f}, {ci[1]:+.2f}], p = {fmt_p(m.pvalues['Intercept'])}")
    vc = dict(zip(m.model.exog_vc.names, m.vcomp))
    say("  Variance components: " + ", ".join(f"{k} {v:.2f}" for k, v in vc.items())
        + f", residual {m.scale:.2f}")
    msgs = {str(w.message).split('\n')[0] for w in wlist}
    if msgs:
        say("  Fit warnings: " + "; ".join(sorted(msgs)))
except Exception as e:  # pragma: no cover
    say(f"Mixed model failed: {e}")
say("")

# ----------------------------------------------------------------------------
# 3. Sensitivity: senior reader
# ----------------------------------------------------------------------------
say("-" * 78)
say(f"3. SENSITIVITY: SENIOR READER ({SENIOR}, {int(df['senior'].sum())} comparisons)")
say("-" * 78)
for flag, name in [(True, "Senior reader only"), (False, "Students only")]:
    s = df[df["senior"] == flag]
    say(f"  {name}: n={len(s)}, mean Total delta {s['d_Total'].mean():+.2f}, "
        f"preferences {s['pref'].value_counts().to_dict()}")
say("  Senior-reader comparisons:")
for _, r in df[df["senior"]].iterrows():
    say(f"    {r.paper:<45} human {r.H_Total:>2}  Gauntlet {r.G_Total:>2}  ({r.pref})")
say("")
rng_s = np.random.default_rng(SEED)
stud = df[~df["senior"]].copy()
sres = analyse(stud, rng_s, "students")
spref = preference(stud, rng_s)
say("Re-analysis excluding the senior reader (16 comparisons, 9 judges):")
say(f"{'Outcome':<22}{'Human':>6}{'Gaunt':>7}{'Delta':>7}  {'95% CI':<18}{'G/T/H':>9}"
    f"{'p':>8}{'p Holm':>8}{'Judge-cl.':>10}")
for o, r in sres.iterrows():
    ph = "" if o == "Total" else fmt_p(r.p_wilcoxon_holm)
    say(f"{LABEL[o]:<22}{r.H:6.2f}{r.G:7.2f}{r.delta:+7.2f}  {fmt_ci(r.ci):<18}"
        f"{f'{r.wins}/{r.ties}/{r.losses}':>9}{fmt_p(r.p_wilcoxon):>8}{ph:>8}{fmt_p(r.p_judge):>10}")
say(f"  Preference: Gauntlet {spref['wins']}, human {spref['losses']}, tie {spref['ties']}"
    f" -> {spref['rate']:.0%}, Wilson [{spref['wilson'][0]:.0%}, {spref['wilson'][1]:.0%}],"
    f" sign test p = {spref['p_sign']:.3f}")
say("")

# ----------------------------------------------------------------------------
# 4. Descriptives reviewers asked about
# ----------------------------------------------------------------------------
say("-" * 78)
say("4. DESCRIPTIVES")
say("-" * 78)
G_all = df[[f"G_{d}" for d in DIMS]].values.ravel(); H_all = df[[f"H_{d}" for d in DIMS]].values.ravel()
say(f"Ceiling: {np.mean(G_all == 5):.0%} of Gauntlet dimension scores are 5/5 "
    f"(human: {np.mean(H_all == 5):.0%}); Gauntlet got 25/25 in {int((df['G_Total'] == 25).sum())} of 20 comparisons, "
    f"the human analysis in {int((df['H_Total'] == 25).sum())} (best human total: {int(df['H_Total'].max())}).")
say(f"Scores of 4 or 5: Gauntlet {np.mean(G_all >= 4):.0%}, human {np.mean(H_all >= 4):.0%}. "
    f"Scores of 1 or 2: Gauntlet {int((G_all <= 2).sum())}, human {int((H_all <= 2).sum())}.")
agree = ((np.sign(df["pref_score"]) == np.sign(df["d_Total"]))).sum()
say(f"Overall preference agrees in direction with the total-score difference in {agree} of 20 comparisons.")
say("")
say("Margins in the comparisons Gauntlet won (total score, of 25):")
gw = df[df["pref_score"] > 0]
gws = gw[~gw["senior"]]
say(f"  All {len(gw)} wins: mean {gw['d_Total'].mean():.2f}, median {gw['d_Total'].median():.1f}, "
    f"range {int(gw['d_Total'].min())} to {int(gw['d_Total'].max())}")
say(f"  Student-judged wins (n={len(gws)}): mean {gws['d_Total'].mean():.2f}, "
    f"median {gws['d_Total'].median():.1f}, max {int(gws['d_Total'].max())}")
for lab, sub in gw.groupby("pref"):
    say(f"  '{lab}' (n={len(sub)}): mean {sub['d_Total'].mean():.2f}, median {sub['d_Total'].median():.1f}, "
        f"range {int(sub['d_Total'].min())} to {int(sub['d_Total'].max())}")
Hw = gw[[f"H_{d}" for d in DIMS]].values; Gw = gw[[f"G_{d}" for d in DIMS]].values
say(f"  Across those wins, Gauntlet scored higher on {np.mean(Gw > Hw):.0%} of dimension scores, "
    f"tied on {np.mean(Gw == Hw):.0%}, lower on {np.mean(Gw < Hw):.0%}.")
say(f"  Human totals in comparisons Gauntlet won ranged {int(gw['H_Total'].min())} to {int(gw['H_Total'].max())}.")
say("")
say("How close were the comparisons? (R2)")
say(f"  {'Paper':<45}{'Judge':<16}{'Human':>6}{'Gaunt':>6}{'Diff':>6}  Preference")
for _, r in df.sort_values("d_Total").iterrows():
    say(f"  {r.paper:<45}{r.judge:<16}{r.H_Total:>6}{r.G_Total:>6}{r.d_Total:>+6}  {r.pref}")
say("")
hw = df[df["pref_score"] < 0]
say(f"  When the human analysis was preferred (n={len(hw)}), total-score differences were "
    f"{sorted(hw['d_Total'].tolist())}.")
say("")

# ----------------------------------------------------------------------------
# 5. Ablation (counts only; no raw ablation data available)
# ----------------------------------------------------------------------------
say("-" * 78)
say("5. ABLATION WIN RATES (from counts in the paper; ties assumed absent)")
say("-" * 78)
for name, k in [("A vs B -> B", 87), ("A vs C -> C", 97), ("B vs C -> C", 94)]:
    lo, hi = proportion_confint(k, 98, method="wilson")
    say(f"  {name}: {k}/98 = {k/98:.1%}, Wilson 95% CI [{lo:.1%}, {hi:.1%}]")
say("  (87/98 for A vs B is inferred from '89%'; confirm the exact count.)")

with open(f"{OUT}/stats_report.txt", "w") as f:
    f.write("\n".join(lines) + "\n")

# ----------------------------------------------------------------------------
# LaTeX table
# ----------------------------------------------------------------------------
def tex_p(p):
    return "$<$0.001" if p < 0.001 else f"{p:.3f}"
TEXLABEL = {"MechAcc": "Mech.\\ Accuracy"}
tex_num = lambda v: f"${v:.2f}$"
rows_tex = []
for o, r in main.iterrows():
    if o == "Total":
        continue
    rows_tex.append(f"{TEXLABEL.get(o, LABEL[o])} & {r.H:.2f} & {r.G:.2f} & {tex_num(r.delta)} [{tex_num(r.ci[0])}, {tex_num(r.ci[1])}]"
                    f" & {r.wins}/{r.ties}/{r.losses} & {tex_p(r.p_wilcoxon_holm)} \\\\")
t = main.loc["Total"]
tex = rf"""% Requires no extra packages. Numbers generated by parallax_stats.py.
\begin{{table}}[t]
\centering
\caption{{Human-judged scores over all 20 comparisons (1--5 scale). $\Delta$: mean
paired difference (Gauntlet $-$ human) with a 95\% CI that accounts for repeated
judges. G/T/H: comparisons where Gauntlet scored higher, tied, or lower.
Per-dimension $p$: two-sided exact Wilcoxon signed-rank, Holm-corrected across the
five dimensions. Total and overall preference (win rate excluding the tie, Wilson
95\% CI, two-sided sign test) are uncorrected primary outcomes.}}
\label{{tab:human}}
\footnotesize
\setlength{{\tabcolsep}}{{2.5pt}}
\begin{{tabular}}{{lccccc}}
\hline
Dimension & Human & Gauntl. & $\Delta$ [95\% CI] & G/T/H & $p$ \\
\hline
{chr(10).join(rows_tex)}
\hline
Total (/25) & {t.H:.2f} & {t.G:.2f} & {tex_num(t.delta)} [{tex_num(t.ci[0])}, {tex_num(t.ci[1])}] & {t.wins}/{t.ties}/{t.losses} & {tex_p(t.p_wilcoxon)} \\
Preferred & {pref['losses']} & {pref['wins']} & {pref['rate']*100:.0f}\% [{pref['wilson'][0]*100:.0f}, {pref['wilson'][1]*100:.0f}] & {pref['wins']}/{pref['ties']}/{pref['losses']} & {tex_p(pref['p_sign'])} \\
\hline
\end{{tabular}}
\end{{table}}
"""
with open(f"{OUT}/table1_revised.tex", "w") as f:
    f.write(tex)

# ----------------------------------------------------------------------------
# Figure: per-comparison totals (answers R2 "how close")
# ----------------------------------------------------------------------------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

COL = {"AI Clearly": "#1f5fa8", "AI Somewhat": "#6fa3d8", "Tie": "#8c8c8c",
       "Human Somewhat": "#e39b4a", "Human Clearly": "#b8520f"}
fd = df.sort_values(["d_Total", "G_Total"]).reset_index(drop=True)
fig, ax = plt.subplots(figsize=(3.5, 4.8))
for i, r in fd.iterrows():
    c = COL[r.pref]
    ax.plot([r.H_Total, r.G_Total], [i, i], color=c, lw=1.6, zorder=1)
    ax.scatter(r.H_Total, i, s=22, facecolor="white", edgecolor=c, lw=1.2, zorder=2)
    ax.scatter(r.G_Total, i, s=22, color=c, zorder=3)
labels = [p.replace("Proccess", "Process") + (" \u2020" if s else "") for p, s in zip(fd["paper"], fd["senior"])]
ax.set_yticks(range(len(fd)), labels, fontsize=6)
ax.set_xlabel("Total score (of 25)", fontsize=7)
ax.set_xlim(12, 25.5)
ax.tick_params(axis="x", labelsize=6)
ax.grid(axis="x", lw=0.3, alpha=0.5)
for sp in ["top", "right"]:
    ax.spines[sp].set_visible(False)
handles = [Line2D([], [], marker="o", ls="", markerfacecolor="white", markeredgecolor="k", label="Human"),
           Line2D([], [], marker="o", ls="", color="k", label="Gauntlet")]
handles += [Line2D([], [], color=COL[k], lw=2, label=k.replace("AI", "Gauntlet")) for k in COL]
fig.legend(handles=handles, fontsize=5.5, loc="lower center", frameon=False, ncol=3, columnspacing=0.8, handlelength=1.5)
ax.set_title("\u2020 judged by senior reader", fontsize=6, loc="right")
fig.tight_layout(rect=[0, 0.07, 1, 1])
fig.savefig(f"{OUT}/fig_per_comparison.pdf")
fig.savefig(f"{OUT}/fig_per_comparison.png", dpi=250)
print(f"\nWrote stats_report.txt, table1_revised.tex, fig_per_comparison.pdf/.png to {OUT}")
