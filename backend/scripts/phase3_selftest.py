"""Phase 3 self-test on the REAL seeded School of IT data (in-process API as the real admin).

  python -m scripts.phase3_selftest

Runs a full whole-school generation, verifies every solution with the Phase 2 clash engine, exercises the
semi-automatic (pinned) mode and the weight knobs, then deletes every draft/run it made and re-runs the seed
verifier - the seeded data itself is never changed.
"""
from __future__ import annotations

import os

os.environ.setdefault("READ_CACHE_SECONDS", "0")   # these scripts also write straight to the database: always read fresh

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from time import monotonic

import httpx

from app import db
from app.config import get_settings
from app.main import app
from app.security import deps
from app.services import generator as gen

from .seed_college import Api, SeedError, Who, _fake_authenticate, load_user, log, verify

OK = BAD = 0
MSC = ["M.Sc. AI - Sem 1", "M.Sc. AI - Sem 3", "M.Sc. IT - Sem 1", "M.Sc. IT - Sem 3"]
SHORT = {"B.Sc. IT - Sem 3": 3, "B.Sc. IT - Sem 5": 5, "B.Sc. IT - Sem 7": 3}   # period-cells the schedule is short of


def check(cond: bool, msg: str, extra: str = "") -> None:
    global OK, BAD
    OK += bool(cond)
    BAD += not cond
    log(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))


async def wait_run(api: Api, run_id: str, timeout: float = 240.0) -> dict:
    t0 = monotonic()
    progress_seen: list[int] = []
    while True:
        data = await api.get(f"/api/generation/runs/{run_id}")
        progress_seen.append(data["progress"])
        if data["status"] != "running":
            data["_progress_seen"] = progress_seen
            return data
        if monotonic() - t0 > timeout:
            raise SeedError(f"run stuck at {data['progress']}% - {data['message']}")
        await asyncio.sleep(0.4)


async def main() -> int:
    settings = get_settings()
    started = datetime.now(timezone.utc)
    async with app.router.lifespan_context(app):
        Who.user = await load_user(email=settings.seed_admin_email, role="admin")
        deps.authenticate = _fake_authenticate
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                     headers={"Origin": "http://localhost:5173"}, timeout=300) as client:
            api = Api(client)
            try:
                await scenarios(api)
            except (SeedError, AssertionError) as exc:
                log(f"\nSTOPPED: {exc}")
                return 1
            finally:
                await cleanup(api, started)
            log("\n== the seeded data is untouched ==")
            bad = await verify(api)
            check(bad == 0, "seed verifier: ALL PASS")
    log(f"\nPHASE 3 SELF-TEST: {OK} checks passed, {BAD} failed")
    return 1 if BAD else 0


async def scenarios(api: Api) -> None:
    tree = await api.get("/api/structure/tree")
    school = next(s for s in tree["schools"] if s["name"] == "School of IT")
    sid = school["id"]
    q = f"?school_id={sid}"

    log("\n== weights ==")
    w = await api.get(f"/api/generation/weights{q}")
    check({r["name"] for r in w["rules"]} == set(gen.RULES), "all the soft rules (8 since Phase 6) with labels + defaults are offered")
    await api.put("/api/generation/weights", {"school_id": sid, "weights": {"lab_lunch": 90}})
    check((await api.get(f"/api/generation/weights{q}"))["weights"]["lab_lunch"] == 90, "a weight can be saved per school")
    await api.put("/api/generation/weights", {"school_id": sid, "weights": gen.default_weights()})

    log("\n== the full whole-school run (8 classes together) ==")
    t0 = monotonic()
    res = await api.post("/api/generation/runs", {"school_id": sid, "name": "SELFTEST full", "time_limit_s": 120})
    check(res["classes"] == 8, "one run covers ALL 8 classes of the active term")
    caps = [f["message"] for f in res["precheck"] if f["kind"] == "class_capacity"]
    check(len(caps) == 3, "precheck: exactly the 3 over-full B.Sc. classes are flagged BEFORE solving", f"{len(caps)} findings")
    check(any("Sem 3 needs 23 lecture periods" in m and "only 20 free" in m for m in caps), "…Sem 3: needs 23, has 20 (numbers, not vague words)")
    check(any("Sem 5 needs 29 lecture periods" in m and "only 24 free" in m for m in caps), "…Sem 5: needs 29, has 24")
    check(any("Sem 7 needs 19 lecture periods" in m and "only 16 free" in m for m in caps), "…Sem 7: needs 19, has 16 (Mon 6, Tue 6, Wed 4)", "ok" if any("Mon 6, Tue 6, Wed 4" in m for m in caps) else "")
    run = await wait_run(api, res["id"])
    took = monotonic() - t0
    check(run["status"] == "done", f"the background run finished (in {took:.1f}s, minutes not hours)", run.get("error") or "")
    check(any(0 < p < 100 for p in run["_progress_seen"]), "live progress was visible while it worked", str(run["_progress_seen"][:6]))
    sols = run["solutions"]
    check(2 <= len(sols) <= 3, f"{len(sols)} scored solutions came out")
    check(len({s["score"] for s in sols}) >= 2 or len(sols) < 2, "the solutions are genuinely different (scores differ)",
          str([s["score"] for s in sols]))
    best = sols[0]
    check({r["rule"] for r in best["breakdown"]["rules"]} == set(gen.RULES), "every solution shows the per-rule score breakdown")

    log("\n== the best solution, class by class ==")
    classes = best["breakdown"]["classes"]
    for label in ["B.Sc. IT - Sem 1", *MSC]:
        info = next(v for v in classes.values() if v["label"] == label)
        check(info["percent"] == 100 and info["missing"] == 0, f"{label}: 100% complete")
    for label, short in SHORT.items():
        info = next(v for v in classes.values() if v["label"] == label)
        check(info["percent"] < 100, f"{label}: honestly incomplete (the schedule is {short} periods too small)",
              f"{info['placed']}/{info['required']}")
    un = best["breakdown"]["unplaced"]
    check(un and all("could not be placed" in u["message"] for u in un), "each missing lecture has a clear who/why message", un[0]["message"][:110] if un else "")

    # hard rules, read straight from the saved drafts
    drafts = {d["semester_id"]: d["draft_id"] for d in best["drafts"]}
    details = {}
    for sem_id, draft_id in drafts.items():
        details[sem_id] = await api.get(f"/api/drafts/{draft_id}")
    by_label = {d["draft"]["class_label"]: d for d in details.values()}
    sem7 = by_label["B.Sc. IT - Sem 7"]
    check(all(p["day"] in (1, 2, 3) for p in sem7["placements"]), "Sem 7 is scheduled on Mon-Wed only (Thu-Sat = OJT, OFF)")
    check(len(sem7["locked"]) == 0, "Sem 7 has no locked PE/SOL (marked 'no PE / SOL')")
    covered = [lbl for lbl in ("B.Sc. IT - Sem 3", "M.Sc. IT - Sem 1", "M.Sc. IT - Sem 3", "M.Sc. AI - Sem 1", "M.Sc. AI - Sem 3")]
    pe_ok = all(any(l["kind"] == "PE" and l["combined"] and len(l["classes"]) == 5 and (l["day"], l["start_period"]) == (1, 5)
                    for l in by_label[lbl]["locked"]) for lbl in covered)
    check(pe_ok, "the combined Monday PE is still locked and untouched in all 5 covered grids")
    teacher_busy: dict[tuple[str, int, int], list[str]] = {}
    for d in details.values():
        for p in d["placements"]:
            for e in p["entries"]:
                for t in (e["teacher_id"], e["co_teacher_id"]):
                    if t:
                        for per in range(p["start_period"], p["start_period"] + p["length"]):
                            teacher_busy.setdefault((t, p["day"], per), []).append(d["draft"]["class_label"])
    doubles = {k: v for k, v in teacher_busy.items() if len(v) > 1}
    check(not doubles, "no teacher (or co-teacher) is in two classes at once anywhere in the solution", str(list(doubles.values())[:1]))
    visiting = {}
    for d in details.values():
        for p in d["placements"]:
            for e in p["entries"]:
                if e["teacher_name"] in ("Anjana Prajapati", "Falguni Thakker"):
                    visiting.setdefault(e["teacher_name"], set()).add(p["day"])
    check(visiting and all(days <= {1, 2, 3} for days in visiting.values()),
          "Anjana Prajapati and Falguni Thakker are only ever placed Mon-Wed", str({k: sorted(v) for k, v in visiting.items()}))
    tracks = [p for p in by_label["B.Sc. IT - Sem 1"]["placements"] if p["parallel"]]
    check(tracks and all(len(p["entries"]) == 2 for p in tracks), "the AI || Cyber tracks sit in the SAME parallel slots with both External teachers",
          f"{len(tracks)} parallel placements")
    OK_dec = sum(1 for d in details.values() for p in d["placements"] if p["problems"])
    log(f"   (placements showing problems against today's primaries: {OK_dec} - alternatives may overlap until the set is used)")

    log("\n== use the best solution ==")
    await api.post(f"/api/generation/solutions/{best['id']}/use")
    rep = await api.get(f"/api/conflicts{q}")
    check(rep["counts"]["clashes"] == 0, "after 'Use this solution' the conflict report shows ZERO clashes for the whole school")
    missing_classes = {i["class_label"] for i in rep["items"] if i["kind"] == "missing"}
    check(set(SHORT) <= missing_classes, "the report still lists the 3 over-full B.Sc. classes as 'still to place' (honest)")

    log("\n== semi-automatic: pin one placement, regenerate around it ==")
    mai1 = next(d for d in details.values() if d["draft"]["class_label"] == "M.Sc. AI - Sem 1")
    target = next(p for p in mai1["placements"] if not p["parallel"])
    spot = (target["day"], target["start_period"])
    await api.post(f"/api/drafts/{mai1['draft']['id']}/placements/{target['key']}/lock", {"locked": True})
    res2 = await api.post("/api/generation/runs", {"school_id": sid, "name": "SELFTEST pinned", "mode": "keep_locked", "time_limit_s": 90})
    run2 = await wait_run(api, res2["id"])
    check(run2["status"] == "done", "the keep-pinned run finished")
    sol2 = run2["solutions"][0]
    d2 = await api.get(f"/api/drafts/{next(x for x in sol2['drafts'] if x['semester_id'] == mai1['draft']['semester_id'])['draft_id']}")
    kept = [p for p in d2["placements"] if p["is_locked"]]
    check(len(kept) == 1 and (kept[0]["day"], kept[0]["start_period"]) == spot and
          {e["subject_id"] for e in kept[0]["entries"]} == {e["subject_id"] for e in target["entries"]},
          f"the pinned lecture stayed exactly at day {spot[0]}, period {spot[1]} in the regenerated timetable")
    mai_info = next(v for v in sol2["breakdown"]["classes"].values() if v["label"] == "M.Sc. AI - Sem 1")
    check(mai_info["percent"] == 100, "…and that class is still 100% complete around the pin")
    await api.post(f"/api/drafts/{mai1['draft']['id']}/placements/{target['key']}/lock", {"locked": False})
    await api.call("DELETE", f"/api/generation/runs/{res2['id']}")

    log("\n== weights really steer the engine (same data, same seed) ==")
    from app.routers.drafts import load as load_school
    from app.services import terms as term_svc

    term = await term_svc.require_active_term()
    L = await load_school(uuid.UUID(sid), term)
    problem = gen.build_problem(L.ctx)
    off = {k: 0 for k in gen.RULES}
    on = gen.default_weights()
    sol_off = gen.solve(problem, off, attempts=4, time_limit=30, seed=11)[0]
    sol_on = gen.solve(problem, on, attempts=4, time_limit=30, seed=11)[0]
    check(sol_off.signature() != sol_on.signature(), "turning the weights changes where lectures land (same data, same seed)")
    rescored_off, _ = gen.score_solution(problem, sol_off.state, on)   # judge BOTH by the same yardstick
    rescored_on, _ = gen.score_solution(problem, sol_on.state, on)
    check(rescored_on >= rescored_off, f"the engine tuned for those weights really scores better: {rescored_off} (weights off) vs {rescored_on} (weights on)")
    sat_on = next(r["satisfaction"] for r in sol_on.breakdown if r["rule"] == "lab_lunch")
    check(sat_on >= 0.9, f"labs stay away from lunch when the rule has weight ({sat_on:.2f})")

    log("\n== copy-previous-term is offered (no past term has drafts yet) ==")
    terms = await api.get(f"/api/drafts/copyable-terms{q}")
    check(terms["terms"] == [], "the list is honestly empty - the feature waits for the first finished term")


async def cleanup(api: Api, started) -> None:
    log("\n== cleanup: every SELFTEST draft, run and audit row goes away ==")
    tree = await api.get("/api/structure/tree")
    sid = next(s for s in tree["schools"] if s["name"] == "School of IT")["id"]
    listing = await api.get(f"/api/drafts?school_id={sid}")
    removed = 0
    for c in listing["classes"]:
        for d in c["drafts"]:
            await api.call("DELETE", f"/api/drafts/{d['id']}")
            removed += 1
    runs = await api.get(f"/api/generation/runs?school_id={sid}")
    for r in runs["runs"]:
        if r["status"] == "running":
            await api.post(f"/api/generation/runs/{r['id']}/cancel")
            await wait_run(api, r["id"])
        await api.call("DELETE", f"/api/generation/runs/{r['id']}")
    await db.execute("delete from public.generation_settings where school_id = $1", uuid.UUID(sid))
    await db.execute(
        "delete from public.audit_log where at >= $1 and entity in "
        "('timetable_draft', 'draft_placement', 'generation_run', 'generation_solution', 'generation_weights')",
        started,
    )
    left = await db.fetchval("select (select count(*) from public.timetable_drafts) + (select count(*) from public.generation_runs)")
    check(left == 0 and removed > 0, f"{removed} drafts and every run removed; nothing is left behind")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
