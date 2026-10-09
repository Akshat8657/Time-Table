"""Phase 2 self-test on the REAL seeded School of IT data (through the backend API, in-process, as the real admin).

  python -m scripts.phase2_selftest

It builds throw-away drafts named 'SELFTEST ...', exercises every rule of the draft engine and DELETES all of them at the end.
The seeded data (accounts, subjects, assignments, locked slots, timing) is never changed - the last step runs the seed verifier again.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx

from app.config import get_settings
from app.main import app
from app.security import deps

from .seed_college import Api, SeedError, Who, _fake_authenticate, load_user, log, verify

PREFIX = "SELFTEST"
OK = 0
BAD = 0


def check(cond: bool, msg: str, extra: str = "") -> None:
    global OK, BAD
    OK += bool(cond)
    BAD += not cond
    log(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))


class Run:
    def __init__(self, api: Api, school_id: str) -> None:
        self.api = api
        self.sid = school_id
        self.classes: dict[str, dict] = {}   # label -> {"semester_id":..}
        self.drafts: list[str] = []

    async def list(self) -> dict:
        return await self.api.get(f"/api/drafts?school_id={self.sid}")

    async def new_draft(self, label: str, name: str, **kw) -> str:
        res = await self.api.post("/api/drafts", {"semester_id": self.classes[label]["semester_id"], "name": f"{PREFIX} {name}", **kw})
        self.drafts.append(res["id"])
        return res["id"]

    async def detail(self, draft: str) -> dict:
        return await self.api.get(f"/api/drafts/{draft}")

    async def options(self, draft: str, **body) -> dict:
        return await self.api.post(f"/api/drafts/{draft}/options", body)

    async def place(self, draft: str, day: int, start: int, expect: int = 201, **body) -> Any:
        r = await self.api.c.post(f"/api/drafts/{draft}/placements", json={"day": day, "start_period": start, **body})
        assert r.status_code == expect, f"place {body} @ {day}:{start} -> {r.status_code} {r.text[:300]}"
        return r.json()

    async def call(self, method: str, path: str, expect: int, **kw) -> Any:
        r = await self.api.c.request(method, path, **kw)
        assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]}"
        return r.json()


def subj(detail: dict, name: str) -> dict:
    return next(s for s in detail["subjects"] if s["name"] == name)


async def main() -> int:
    settings = get_settings()
    async with app.router.lifespan_context(app):
        Who.user = await load_user(email=settings.seed_admin_email, role="admin")
        deps.authenticate = _fake_authenticate
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost", headers={"Origin": "http://localhost:5173"}, timeout=180) as client:
            api = Api(client)
            try:
                tree = await api.get("/api/structure/tree")
                school = next(s for s in tree["schools"] if s["name"] == "School of IT")
                run = Run(api, school["id"])
                before = await run.list()
                for c in before["classes"]:
                    run.classes[c["label"]] = c
                stale = [d for c in before["classes"] for d in c["drafts"] if d["name"].startswith(PREFIX)]
                for d in stale:
                    await api.call("DELETE", f"/api/drafts/{d['id']}")
                try:
                    await scenarios(run)
                finally:
                    log("\n== cleanup: every SELFTEST draft is deleted ==")
                    for c in (await run.list())["classes"]:
                        for d in c["drafts"]:
                            if d["name"].startswith(PREFIX):
                                await api.call("DELETE", f"/api/drafts/{d['id']}")
                    left = [d for c in (await run.list())["classes"] for d in c["drafts"]]
                    check(len(left) == len([d for c in before["classes"] for d in c["drafts"] if not d["name"].startswith(PREFIX)]), "no test draft is left", str(len(left)))
                log("\n== the seeded data is untouched ==")
                bad = await verify(api)
                check(bad == 0, "seed verifier: ALL PASS")
            except (SeedError, AssertionError) as exc:
                log(f"\nSTOPPED: {exc}")
                return 1
    log(f"\nPHASE 2 SELF-TEST: {OK} checks passed, {BAD} failed")
    return 1 if BAD else 0


async def scenarios(run: Run) -> None:
    api = run.api
    mai1, mit1, bsc1, bsc3, bsc7 = "M.Sc. AI - Sem 1", "M.Sc. IT - Sem 1", "B.Sc. IT - Sem 1", "B.Sc. IT - Sem 3", "B.Sc. IT - Sem 7"

    # ------------------------------------------------------------------------------------------------ locked slots in every covered grid
    log("\n== the combined PE (Monday P5-6, 5 classes) is LOCKED in every covered class ==")
    for label in ("B.Sc. IT - Sem 3", "M.Sc. IT - Sem 1", "M.Sc. IT - Sem 3", "M.Sc. AI - Sem 1", "M.Sc. AI - Sem 3"):
        d = await run.new_draft(label, "locked check")
        det = await run.detail(d)
        pe = [x for x in det["locked"] if x["kind"] == "PE"]
        check(len(pe) == 1 and pe[0]["combined"] and len(pe[0]["classes"]) == 5 and (pe[0]["day"], pe[0]["start_period"]) == (1, 5), f"{label}: combined PE is locked on Monday P5-6 ({len(pe[0]['classes']) if pe else 0} classes)")
        plain = next(s for s in det["subjects"] if not s["group_id"] and not s["special_kind"] and not s["is_lab"])
        r = await api.c.post(f"/api/drafts/{d}/placements", json={"subject_id": plain["id"], "day": 1, "start_period": 5})
        check(r.status_code == 422 and "locked" in r.text, f"{label}: a lecture cannot be dropped on the locked PE cell", r.json().get("message", "")[:90])
        await api.call("DELETE", f"/api/drafts/{d}")

    # ------------------------------------------------------------------------------------------------ a full valid week: M.Sc. AI Sem 1
    log("\n== a full valid week for M.Sc. AI Sem 1 ==")
    d_ai = await run.new_draft(mai1, "full week")
    det = await run.detail(d_ai)
    check(det["draft"]["is_primary"], "the first draft of a class is its primary draft")
    need = [s for s in det["subjects"] if not s["special_kind"]]
    check(det["completeness"]["percent"] < 100 and det["completeness"]["placed"] > 0, "PE + SOL already count as placed (locked slots)", f"{det['completeness']['placed']}/{det['completeness']['required']}")
    order = sorted(need, key=lambda s: (not s["is_lab"], -s["required"]))        # labs first (they need 2 free periods in a row)
    placed_days: dict[str, set[int]] = {}
    for s in order:
        for _ in range(s["required"] - s["placed"]):
            opts = await run.options(d_ai, subject_id=s["id"])
            cells = opts["cells"]
            days_used = placed_days.setdefault(s["id"], set())
            pick = sorted(                                                      # a free cell first; a cell with only a WARNING is fine too
                (k for k, v in cells.items() if v["state"] != "blocked"),
                key=lambda k: (cells[k]["state"] != "ok", int(k.split(":")[0]) in days_used, int(k.split(":")[0]), int(k.split(":")[1])),
            )
            assert pick, f"no free cell for {s['name']}"
            day, start = map(int, pick[0].split(":"))
            await run.place(d_ai, day, start, subject_id=s["id"])
            days_used.add(day)
    det = await run.detail(d_ai)
    check(det["completeness"]["percent"] == 100 and det["completeness"]["missing"] == 0, "every subject has all its lectures (credits; labs = 1 block)", f"{det['completeness']['placed']}/{det['completeness']['required']}")
    check(det["problems"]["clashes"] == 0, "the full week has no clash")
    labs = [p for p in det["placements"] if p["is_lab"]]
    check(len(labs) == 3 and all(p["length"] == 2 for p in labs), "the 3 labs are 2-period blocks", str([(p["day"], p["start_period"]) for p in labs]))
    anj = [p for p in det["placements"] if any(e["teacher_name"] == "Anjana Prajapati" for e in p["entries"])]
    check(anj and all(p["day"] in (1, 2, 3) for p in anj), "Anjana Prajapati (visiting) is only placed Mon-Wed", str([p["day"] for p in anj]))
    report = await api.get(f"/api/conflicts?school_id={run.sid}&semester_id={run.classes[mai1]['semester_id']}")
    mine = [i for i in report["items"] if i["draft_name"] and i["draft_name"].startswith(PREFIX)]
    check(report["counts"]["clashes"] == 0 and not any(i["kind"] == "missing" and i["code"] != "NO_DRAFT" for i in mine), "conflict report: no clash, nothing missing for the full week")

    # ------------------------------------------------------------------------------------------------ hard rules, one by one
    log("\n== every clash type is blocked with a clear reason ==")
    d_it = await run.new_draft(mit1, "clash partner")
    it = await run.detail(d_it)
    py_ai = next(p for p in det["placements"] if any(e["subject_name"] == "Python Programming" for e in p["entries"]))
    adv_py = subj(it, "Advanced Python Programming")                                  # same teacher (Dr. Chirag Mehta) as Python Programming in MAI1
    r = await api.c.post(f"/api/drafts/{d_it}/placements", json={"subject_id": adv_py["id"], "day": py_ai["day"], "start_period": py_ai["start_period"]})
    check(r.status_code == 422 and "Chirag Mehta" in r.text and "M.Sc. AI - Sem 1" in r.text, "teacher double-booked across classes is blocked", r.json().get("message", "")[:140])
    opts = await run.options(d_it, subject_id=adv_py["id"])
    check(opts["cells"][f"{py_ai['day']}:{py_ai['start_period']}"]["state"] == "blocked", "... and the cell is coloured 'blocked' before the drop")
    # class double-booked
    first = next(p for p in det["placements"] if not p["is_lab"])
    other = next(s for s in need if s["id"] != first["entries"][0]["subject_id"] and not s["is_lab"] and not s["group_id"])
    r = await api.c.post(f"/api/drafts/{d_ai}/placements", json={"subject_id": other["id"], "day": first["day"], "start_period": first["start_period"]})
    check(r.status_code == 422 and "already has" in r.text, "class double-booked is blocked", r.json().get("message", "")[:110])
    # visiting faculty outside Mon-Wed (Anjana teaches 'AI & Machine Learning')
    d_x = await run.new_draft(mai1, "visiting probe")
    xdet = await run.detail(d_x)
    aiml = subj(xdet, "AI & Machine Learning")
    for day, name in ((4, "Thursday"), (5, "Friday")):
        r = await api.c.post(f"/api/drafts/{d_x}/placements", json={"subject_id": aiml["id"], "day": day, "start_period": 1})
        check(r.status_code == 422 and "visiting faculty" in r.text and "Anjana" in r.text, f"Anjana Prajapati on {name} is blocked", "Monday, Tuesday, Wednesday" if "Monday, Tuesday, Wednesday" in r.text else "")
    ok = await run.place(d_x, 1, 1, subject_id=aiml["id"])
    check(bool(ok["key"]), "Anjana Prajapati on Monday is fine")
    d_dv = await run.new_draft(bsc3, "falguni probe")
    dv = await run.detail(d_dv)
    viz = subj(dv, "Data Visualization")
    r = await api.c.post(f"/api/drafts/{d_dv}/placements", json={"subject_id": viz["id"], "day": 4, "start_period": 1})
    check(r.status_code == 422 and "Falguni" in r.text and "visiting" in r.text, "Falguni Thakker on Thursday is blocked")
    # OFF days / end time
    d_7 = await run.new_draft(bsc7, "off days")
    s7 = await run.detail(d_7)
    big = subj(s7, "Big Data Analytics")
    opts7 = await run.options(d_7, subject_id=big["id"])
    check(all(opts7["cells"][f"{d}:{p}"]["state"] == "blocked" for d in (4, 5, 6) for p in range(1, 7)), "B.Sc. Sem 7: Thursday, Friday and Saturday (OJT) are unplaceable")
    r = await api.c.post(f"/api/drafts/{d_7}/placements", json={"subject_id": big["id"], "day": 4, "start_period": 1})
    check(r.status_code == 422 and "OFF" in r.text, "... with the reason 'OFF'", r.json().get("message", "")[:100])
    sat = await run.options(d_ai, subject_id=need[0]["id"])
    check(all(sat["cells"][f"6:{p}"]["state"] == "blocked" and "OFF" in sat["cells"][f"6:{p}"]["hard"][0] for p in range(1, 7)), "Saturday (OFF for every class) is unplaceable")
    d_3 = await run.new_draft(bsc3, "end times")
    s3 = await run.detail(d_3)
    os_ = subj(s3, "Operating Systems")
    r = await api.c.post(f"/api/drafts/{d_3}/placements", json={"subject_id": os_["id"], "day": 3, "start_period": 5})
    check(r.status_code == 422 and "14:40" in r.text, "B.Sc. Sem 3 on Wednesday after 14:40 is blocked", r.json().get("message", "")[:110])
    # PE / SOL are fixed only
    pe = next(s for s in det["subjects"] if s["special_kind"] == "PE")
    r = await api.c.post(f"/api/drafts/{d_ai}/placements", json={"subject_id": pe["id"], "day": 2, "start_period": 1})
    check(r.status_code == 422 and "Fixed Slots" in r.text, "PE / SOL cannot be placed in a draft (they come from Fixed Slots)")
    # the elective group: one option alone is refused
    d_b1 = await run.new_draft(bsc1, "parallel tracks")
    b1 = await run.detail(d_b1)
    ai_opt = next(s for s in b1["subjects"] if s["name"] == "Intro to AI & ML with Python")
    r = await api.c.post(f"/api/drafts/{d_b1}/placements", json={"subject_id": ai_opt["id"], "day": 1, "start_period": 1})
    check(r.status_code == 422 and "elective group" in r.text, "one track option alone is refused: the whole group runs together")

    # ------------------------------------------------------------------------------------------------ parallel track placement
    log("\n== parallel track placement (AI || Cyber) is ONE combined placement ==")
    grp = next(g for g in b1["groups"] if g["name"] == "Major 1 - Track Majors")
    glab = next(g for g in b1["groups"] if g["name"] == "Major 1 LAB - Track Labs")
    res = await run.place(d_b1, 1, 1, group_id=grp["id"])
    pl = next(p for p in res["detail"]["placements"] if p["key"] == res["key"])
    names = {e["subject_name"]: e["teacher_name"] for e in pl["entries"]}
    check(pl["parallel"] and names == {"Intro to AI & ML with Python": "External Faculty (AI&ML)", "Information Security Fundamentals": "External Faculty (Cyber)"}, "AI-track + Cyber-track are placed together, each with its own External teacher", str(names))
    check(not pl["problems"] and res["detail"]["problems"]["clashes"] == 0, "no false clash between the two options")
    res = await run.place(d_b1, 2, 1, group_id=glab["id"])
    pl = next(p for p in res["detail"]["placements"] if p["key"] == res["key"])
    check(pl["parallel"] and pl["length"] == 2 and len(pl["entries"]) == 2 and not pl["problems"], "the two track LABS form one parallel 2-period block")
    gdet = res["detail"]
    check(next(g for g in gdet["groups"] if g["id"] == grp["id"])["placed"] == 1, "the group counts as 1 of 3 lectures placed")

    # ------------------------------------------------------------------------------------------------ soft rules
    log("\n== soft rules warn but allow ==")
    mj = next(p for p in gdet["placements"] if p["parallel"] and not p["is_lab"])
    again = await run.place(d_b1, 1, 3, group_id=grp["id"])
    check(any("already taught" in w for w in again["warnings"]), "the same subject twice in one day = warning, allowed", again["warnings"][0][:80])
    maths = next(s for s in gdet["subjects"] if s["name"] == "Fundamentals of Mathematics")
    dsa = next(s for s in gdet["subjects"] if s["name"] == "Fundamentals of DBMS")
    await run.place(d_b1, 4, 1, subject_id=maths["id"])
    far = await run.place(d_b1, 4, 5, subject_id=dsa["id"])
    check(any("free periods in a row" in w for w in far["warnings"]), "a long gap for the class = warning, allowed", next((w for w in far["warnings"] if "in a row" in w), "")[:90])
    lab_opts = await run.options(d_b1, subject_id=next(s for s in gdet["subjects"] if s["name"] == "Fundamentals of DBMS LAB")["id"])
    check(lab_opts["cells"]["4:3"]["state"] == "warn" and any("lunch" in w for w in lab_opts["cells"]["4:3"]["soft"]), "a lab around lunch (P3+P4) = warning, allowed")

    # ------------------------------------------------------------------------------------------------ move / swap / remove
    log("\n== move, swap and remove ==")
    d_m = await run.new_draft(mai1, "move swap")
    a = await run.place(d_m, 2, 1, subject_id=subj(await run.detail(d_m), "Real Time Operating System")["id"])
    b = await run.place(d_m, 2, 3, subject_id=subj(await run.detail(d_m), "Python Programming")["id"])
    mv = await run.call("PATCH", f"/api/drafts/{d_m}/placements/{a['key']}", 200, json={"day": 4, "start_period": 2})
    moved = next(p for p in mv["detail"]["placements"] if p["key"] == a["key"])
    check((moved["day"], moved["start_period"]) == (4, 2), "a placement can be moved")
    r = await api.c.patch(f"/api/drafts/{d_m}/placements/{a['key']}", json={"day": 1, "start_period": 5})
    check(r.status_code == 422 and "locked" in r.text, "moving onto the locked PE cell is blocked")
    sw = await run.call("POST", f"/api/drafts/{d_m}/swap", 200, json={"placement_a": a["key"], "placement_b": b["key"]})
    pos = {p["key"]: (p["day"], p["start_period"]) for p in sw["detail"]["placements"]}
    check(pos[a["key"]] == (2, 3) and pos[b["key"]] == (4, 2), "two placements can be swapped")
    lab = await run.place(d_m, 3, 1, subject_id=subj(await run.detail(d_m), "IoT LAB")["id"])
    r = await api.c.post(f"/api/drafts/{d_m}/swap", json={"placement_a": lab["key"], "placement_b": a["key"]})
    check(r.status_code == 422 and "2-period" in r.text, "a lab block and a single lecture cannot be swapped")
    rm = await run.call("DELETE", f"/api/drafts/{d_m}/placements/{a['key']}", 200)
    check(a["key"] not in {p["key"] for p in rm["detail"]["placements"]}, "a placement can be removed")

    # ------------------------------------------------------------------------------------------------ drafts: copy, primary, report after a primary switch
    log("\n== several drafts per class, copy, primary and the conflict report ==")
    d_copy = await run.new_draft(mai1, "copy of the full week", copy_from=d_ai)
    cd = await run.detail(d_copy)
    check(not cd["draft"]["is_primary"] and len(cd["placements"]) == len(det["placements"]) and cd["completeness"]["percent"] == 100, "copy-from-another-draft copies every placement", f"{len(cd['placements'])} placements")
    check(len(cd["siblings"]) >= 3 and cd["siblings"][0]["is_primary"], "a class can have several named drafts; the primary one is listed first")
    py_key = next(p for p in cd["placements"] if any(e["subject_name"] == "Python Programming" for e in p["entries"]))["key"]
    await run.call("DELETE", f"/api/drafts/{d_copy}/placements/{py_key}", 200)                    # the copy is free of Python for now
    py_subj = subj(cd, "Python Programming")["id"]
    it_opts = await run.options(d_it, subject_id=adv_py["id"])
    cp_opts = await run.options(d_copy, subject_id=py_subj)
    both = [k for k in it_opts["cells"] if it_opts["cells"][k]["state"] != "blocked" and cp_opts["cells"][k]["state"] != "blocked"]
    X, Y = both[0], both[-1]
    assert X != Y
    await run.place(d_it, *map(int, X.split(":")), subject_id=adv_py["id"])                       # MIT1 (primary): Advanced Python at X
    alt = await run.options(d_copy, subject_id=py_subj)
    check(alt["cells"][X]["state"] == "blocked" and "Chirag" in alt["cells"][X]["hard"][0], "an ALTERNATIVE draft is checked against the other classes' primary drafts too", alt["cells"][X]["hard"][0][:100])
    await run.place(d_copy, *map(int, Y.split(":")), subject_id=py_subj)                          # the copy puts Python at Y
    it_now = await run.detail(d_it)
    adv_key = next(p for p in it_now["placements"] if any(e["subject_name"] == "Advanced Python Programming" for e in p["entries"]))["key"]
    r = await api.c.patch(f"/api/drafts/{d_it}/placements/{adv_key}", json={"day": int(Y.split(":")[0]), "start_period": int(Y.split(":")[1])})
    check(r.status_code == 200, "MIT1 may use time Y while only an ALTERNATIVE draft of M.Sc. AI uses it", r.text[:110] if r.status_code != 200 else "")
    rep = await api.get(f"/api/conflicts?school_id={run.sid}")
    check(rep["counts"]["clashes"] == 0, "so far the report (primary drafts) shows no clash")
    await api.patch(f"/api/drafts/{d_copy}", {"is_primary": True})
    rep = await api.get(f"/api/conflicts?school_id={run.sid}")
    clashes = [i for i in rep["items"] if i["kind"] == "clash" and i["code"] == "TEACHER_BUSY"]
    check(len(clashes) >= 2 and {c["class_label"] for c in clashes} >= {mai1, mit1}, "making the copy primary shows the teacher clash in BOTH classes of the report", f"{len(clashes)} items")
    jump = next(c for c in clashes if c["class_label"] == mit1)
    check(jump["day"] == int(Y.split(":")[0]) and jump["start_period"] == int(Y.split(":")[1]) and jump["placement_key"] and jump["draft_id"], "each report item says where to click through (draft, day, period)")
    only = await api.get(f"/api/conflicts?school_id={run.sid}&semester_id={run.classes[bsc3]['semester_id']}")
    check(not any(i["kind"] == "clash" for i in only["items"]), "the report can be filtered by class")
    miss = [i for i in rep["items"] if i["kind"] == "missing" and i["class_label"] == mit1]
    check(miss and "of" in miss[0]["message"], "under-placed subjects are listed (MIT1 has only one placement)", miss[0]["message"][:80] if miss else "")
    await api.patch(f"/api/drafts/{d_ai}", {"is_primary": True})
    rep = await api.get(f"/api/conflicts?school_id={run.sid}")
    check(not [i for i in rep["items"] if i["kind"] == "clash"], "switching the primary draft back clears the clash")
    await api.call("DELETE", f"/api/drafts/{d_ai}")
    sib = (await run.detail(d_copy))["siblings"]
    check(any(x["id"] == d_copy and x["is_primary"] for x in sib), "deleting the primary draft hands the 'primary' role to another draft of the class")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
