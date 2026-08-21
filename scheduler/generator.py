"""
Generator: OR-Tools CP-SAT.

Model: one base week + alternating lessons.
  xb[a_i, d, p]  -- lesson in BOTH weeks (repeats every week)
  xa[a_i, d, p]  -- lesson in WEEK A only (0.5h part)
  xb2[a_i, d, p] -- lesson in WEEK B only (0.5h part)

For assignment a with hours_per_week = H:
  base_count[a] = floor(H)  -- same slots both weeks
  alt_count[a]  = 1 if H%1  -- one alternating lesson (either week A or B)

Two different 0.5h subjects of the same class can share the same slot:
  week A: subject P1 (xa=1), week B: subject P2 (xb2=1) -- slot always filled.

Constraints use:
  Week A occupancy: xb + xa
  Week B occupancy: xb + xb2

Saving:
  xb=1  --> Lesson(week=0) + Lesson(week=1) same slot
  xa=1  --> Lesson(week=0) only
  xb2=1 --> Lesson(week=1) only
"""
from collections import defaultdict
from math import ceil
from ortools.sat.python import cp_model

from .models import Schedule, Lesson, Teacher, SchoolClass, TeacherSubject, Room


def _summary(schedule, assignments, base_count, alt_count, canonical,
             class_total_A, teachers, teacher_assignments, classes, D, P) -> str:
    cls_by_pk = {c.pk: c for c in classes}
    lines = [f'[{D} dn/tyj, {P} ur/den]']
    lines.append('Klasy (urokiv u tyzhni A):')
    for cls_pk, total in sorted(class_total_A.items()):
        cls = cls_by_pk.get(cls_pk)
        if cls is None:
            continue
        avg = f'{total/D:.1f}'
        lines.append(f'  {cls}: {total} ur/tyj (~{avg}/den, max {P})')
    lines.append('Vchyteli (urokiv u tyzhni A / limit):')
    for t in teachers:
        t_total = sum(base_count[a_i] + alt_count[a_i] for a_i in teacher_assignments[t.pk])
        if t_total == 0:
            continue
        mask = t.available_days[:D].ljust(D, '0')
        avail = mask.count('1')
        lim = avail * t.max_lessons_per_day
        lines.append(f'  {t}: {t_total}/{lim} ({avail} dn x {t.max_lessons_per_day} ur)')
    return '\n'.join(lines)


def _diagnose(schedule, assignments, base_count, alt_count, canonical,
              class_total_A, teachers, teacher_assignments, classes, rooms,
              specialized_capacity, D, P) -> list:
    issues = []
    total_slots = D * P

    for c in classes:
        total = class_total_A[c.pk]
        if total > total_slots:
            issues.append(
                f'Клас {c}: {total} уроків/тиж > {total_slots} слотів ({D} дн x {P} ур)'
            )

    for t in teachers:
        mask = t.available_days[:D].ljust(D, '0')
        avail_days = mask.count('1')
        max_possible = avail_days * t.max_lessons_per_day
        t_total = sum(base_count[a_i] + alt_count[a_i] for a_i in teacher_assignments[t.pk])
        if t_total > max_possible:
            issues.append(
                f'Вчитель {t}: {t_total} уроків/тиж > максимум '
                f'{max_possible} ({avail_days} дн x {t.max_lessons_per_day} ур/день)'
            )

    for t in teachers:
        mask = t.available_days[:D].ljust(D, '0')
        if '1' not in mask and teacher_assignments[t.pk]:
            names = ', '.join(str(assignments[a_i]) for a_i in teacher_assignments[t.pk][:3])
            issues.append(f'Вчитель {t}: немає доступних днів, але є навантаження ({names}...)')

    for c in classes:
        sc = canonical[c.pk]
        if not sc:
            continue
        hi = ceil(class_total_A[c.pk] / D)
        if hi > P:
            issues.append(
                f'Клас {c}: потрібно мінімум {hi} ур/день, але лише {P} слотів/день'
            )

    for subj_id, cap in specialized_capacity.items():
        demand_ais = [a_i for a_i, a in enumerate(assignments) if a.subject_id == subj_id]
        if not demand_ais:
            continue
        subj_obj = assignments[demand_ais[0]].subject
        effective_cap = cap if subj_obj.allow_shared_room else sum(
            1 for r in rooms if r.subject_id == subj_id
        )
        total_lessons = sum(base_count[a_i] + alt_count[a_i] for a_i in demand_ais)
        min_slots_needed = ceil(total_lessons / effective_cap)
        if min_slots_needed > total_slots:
            n_classes = len({assignments[a_i].school_class_id for a_i in demand_ais})
            issues.append(
                f'Предмет {subj_obj.name}: {n_classes} класів, {total_lessons} ур/тиж, '
                f'лише {effective_cap} кабінет(ів) -- потрібно {min_slots_needed} > {total_slots} слотів'
            )

    group_map: dict = defaultdict(dict)
    for a_i, a in enumerate(assignments):
        if a.group is not None:
            group_map[(a.school_class_id, a.subject_id)][a.group] = a_i
    for (cls_pk, subj_pk), gmap in group_map.items():
        group_ais = list(gmap.values())
        if len(group_ais) < 2:
            continue
        teacher_ids = {assignments[a_i].teacher_id for a_i in group_ais}
        if len(teacher_ids) <= 1:
            continue
        rep_a = assignments[group_ais[0]]
        for t_id in teacher_ids:
            t_obj = next(t for t in teachers if t.pk == t_id)
            t_total = sum(base_count[a_i] + alt_count[a_i] for a_i in teacher_assignments[t_id])
            mask = t_obj.available_days[:D].ljust(D, '0')
            avail = mask.count('1') * t_obj.max_lessons_per_day
            if t_total > avail:
                issues.append(
                    f'Вчитель {t_obj} (co-scheduling {rep_a.subject}/{rep_a.school_class}): '
                    f'перевантажений ({t_total} > {avail} ур/тиж)'
                )

    # Check group balance: each student group must have the same total hours per class
    # so that no-window and uniform-load constraints are symmetric.
    for c in classes:
        by_group: dict = defaultdict(int)
        for a_i in range(len(assignments)):
            a = assignments[a_i]
            if a.school_class_id != c.pk or a.group is None:
                continue
            by_group[a.group] += base_count[a_i] + alt_count[a_i]
        if len(by_group) < 2:
            continue
        totals = {g: h for g, h in by_group.items()}
        ref_g, ref_h = next(iter(totals.items()))
        for g, h in totals.items():
            if h != ref_h:
                issues.append(
                    f'Клас {c}: дисбаланс груп — гр.{ref_g} має {ref_h} ур/тиж, '
                    f'гр.{g} має {h} ур/тиж. Виправте навантаження щоб суми збіглися.'
                )

    return issues


def _solve_group_phase(assignments, group_map, class_assignments, teacher_assignments,
                       base_count, alt_count, classes, teachers, D, P,
                       unpaired_edge: set | None = None) -> dict:
    """Phase 1: schedule group lessons only, maximising cross-teacher slot pairing.

    For each class, collects same-teacher group subjects (г1 and г2 taught by the
    same teacher) and adds a unidirectional constraint: the teacher with *fewer* group
    hours in that class is the "follower" — all their inner-period group slots must
    coincide with a group slot of the other teacher (cross-subject pairing).

    Falls back to basic feasibility (no pairing constraints) if pairing causes INFEASIBLE.
    When with_edge=True, forces unpaired same-teacher group lessons to be at period 0
    or the last occupied period of the day (not in the middle).

    Returns {a_i: {'base': [(d,p),...], 'xa': [(d,p),...], 'xb2': [(d,p),...]}}
    for group assignments only, to be used as hints in Phase 2.
    """
    grp_set = {a_i for gmap in group_map.values() for a_i in gmap.values()}
    if not grp_set:
        return {}
    edge_set = (unpaired_edge or set()) & grp_set

    def _build(with_pairing: bool, with_edge: bool = False):
        m = cp_model.CpModel()
        xb_: dict = {}; xa_: dict = {}; xb2_: dict = {}
        for a_i in grp_set:
            for d in range(D):
                for p in range(P):
                    xb_[a_i, d, p] = m.new_bool_var(f'g_xb_{a_i}_{d}_{p}')
                    if alt_count[a_i]:
                        xa_[a_i, d, p]  = m.new_bool_var(f'g_xa_{a_i}_{d}_{p}')
                        xb2_[a_i, d, p] = m.new_bool_var(f'g_xb2_{a_i}_{d}_{p}')
                    else:
                        xa_[a_i, d, p]  = 0
                        xb2_[a_i, d, p] = 0

        def vA(a_i, d, p):
            v = [xb_[a_i, d, p]]
            if alt_count[a_i]: v.append(xa_[a_i, d, p])
            return v

        def vB(a_i, d, p):
            v = [xb_[a_i, d, p]]
            if alt_count[a_i]: v.append(xb2_[a_i, d, p])
            return v

        # Lesson counts
        for a_i in grp_set:
            m.add(sum(xb_[a_i, d, p] for d in range(D) for p in range(P)) == base_count[a_i])
            if alt_count[a_i]:
                m.add(sum(xa_[a_i, d, p] + xb2_[a_i, d, p]
                          for d in range(D) for p in range(P)) == 1)
                for d in range(D):
                    for p in range(P):
                        m.add(xa_[a_i, d, p] + xb2_[a_i, d, p] <= 1)

        # Class conflict (within each group number)
        for c in classes:
            grp: dict = defaultdict(list)
            for a_i in class_assignments[c.pk]:
                if a_i in grp_set:
                    grp[assignments[a_i].group].append(a_i)
            for d in range(D):
                for p in range(P):
                    for vfn in (vA, vB):
                        for g_ais in grp.values():
                            m.add(cp_model.LinearExpr.Sum(
                                [v for a_i in g_ais for v in vfn(a_i, d, p)]) <= 1)

        # Teacher conflict
        for t in teachers:
            t_grp = [a_i for a_i in teacher_assignments[t.pk] if a_i in grp_set]
            if not t_grp: continue
            for d in range(D):
                for p in range(P):
                    m.add(cp_model.LinearExpr.Sum(
                        [v for a_i in t_grp for v in vA(a_i, d, p)]) <= 1)
                    m.add(cp_model.LinearExpr.Sum(
                        [v for a_i in t_grp for v in vB(a_i, d, p)]) <= 1)

        # Teacher availability
        for a_i in grp_set:
            mask = assignments[a_i].teacher.available_days[:D].ljust(D, '0')
            for d in range(D):
                if mask[d] != '1':
                    for p in range(P):
                        m.add(xb_[a_i, d, p] == 0)
                        if alt_count[a_i]:
                            m.add(xa_[a_i, d, p] == 0)
                            m.add(xb2_[a_i, d, p] == 0)

        # Max lessons per day
        for t in teachers:
            t_grp = [a_i for a_i in teacher_assignments[t.pk] if a_i in grp_set]
            if not t_grp: continue
            for d in range(D):
                m.add(cp_model.LinearExpr.Sum([v for a_i in t_grp
                      for p in range(P) for v in vA(a_i, d, p)]) <= t.max_lessons_per_day)
                m.add(cp_model.LinearExpr.Sum([v for a_i in t_grp
                      for p in range(P) for v in vB(a_i, d, p)]) <= t.max_lessons_per_day)

        # Co-scheduled groups (same subject, different teachers → same slot)
        for (cls_pk, subj_pk), gmap in group_map.items():
            g_ais = list(gmap.values())
            if len(g_ais) < 2: continue
            if len({assignments[a_i].teacher_id for a_i in g_ais}) <= 1: continue
            rep = g_ais[0]
            for other in g_ais[1:]:
                for d in range(D):
                    for p in range(P):
                        m.add(xb_[rep, d, p] == xb_[other, d, p])
                        r_xa = xa_[rep, d, p]; o_xa = xa_[other, d, p]
                        r_xb2 = xb2_[rep, d, p]; o_xb2 = xb2_[other, d, p]
                        if not (isinstance(r_xa, int) and isinstance(o_xa, int)):
                            m.add(r_xa == o_xa)
                        if not (isinstance(r_xb2, int) and isinstance(o_xb2, int)):
                            m.add(r_xb2 == o_xb2)

        if with_pairing:
            # Same cross-teacher pairing logic as Phase 2 constraint 6b.
            # For each teacher T: T_g1 at inner ≤ sum(non-T g2) at inner, and vice versa.
            for c in classes:
                same_t_g1_p1: dict = defaultdict(list)
                same_t_g2_p1: dict = defaultdict(list)

                for (cls_pk, subj_pk), gmap in group_map.items():
                    if cls_pk != c.pk: continue
                    g_nums = sorted(gmap.keys())
                    if len(g_nums) < 2: continue
                    a_g1 = gmap[g_nums[0]]
                    a_g2 = gmap[g_nums[1]]
                    t1 = assignments[a_g1].teacher_id
                    t2 = assignments[a_g2].teacher_id
                    if t1 != t2: continue
                    same_t_g1_p1[t1].append(a_g1)
                    same_t_g2_p1[t1].append(a_g2)

                t_ids = list(same_t_g1_p1.keys())
                if len(t_ids) < 2: continue

                all_g1_p1 = [a_i for ais in same_t_g1_p1.values() for a_i in ais]
                all_g2_p1 = [a_i for ais in same_t_g2_p1.values() for a_i in ais]

                for t_id in t_ids:
                    t_g1 = same_t_g1_p1[t_id]
                    t_g2 = same_t_g2_p1[t_id]
                    non_t_g2 = [a_i for a_i in all_g2_p1
                                 if assignments[a_i].teacher_id != t_id]
                    non_t_g1 = [a_i for a_i in all_g1_p1
                                 if assignments[a_i].teacher_id != t_id]

                    for d in range(D):
                        for p in range(1, P - 1):
                            t_g1_A = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g1 for v in vA(a_i, d, p)])
                            nt_g2_A = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g2 for v in vA(a_i, d, p)])
                            m.add(t_g1_A <= nt_g2_A)

                            t_g1_B = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g1 for v in vB(a_i, d, p)])
                            nt_g2_B = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g2 for v in vB(a_i, d, p)])
                            m.add(t_g1_B <= nt_g2_B)

                            t_g2_A = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g2 for v in vA(a_i, d, p)])
                            nt_g1_A = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g1 for v in vA(a_i, d, p)])
                            m.add(t_g2_A <= nt_g1_A)

                            t_g2_B = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g2 for v in vB(a_i, d, p)])
                            nt_g1_B = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g1 for v in vB(a_i, d, p)])
                            m.add(t_g2_B <= nt_g1_B)

        # Inner-period symmetry: same-teacher g1 and g2 must have equal counts of
        # inner-period slots — a Phase 1 proxy for the actual pairing constraint in Phase 2.
        if with_pairing and P >= 3:
            for (cls_pk, subj_pk), gmap in group_map.items():
                g_nums = sorted(gmap.keys())
                if len(g_nums) < 2:
                    continue
                a_g1 = gmap[g_nums[0]]
                a_g2 = gmap[g_nums[1]]
                if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                    continue
                if a_g1 not in grp_set or a_g2 not in grp_set:
                    continue
                inner_g1 = sum(xb_[a_g1, d, p]
                               for d in range(D) for p in range(1, P - 1))
                inner_g2 = sum(xb_[a_g2, d, p]
                               for d in range(D) for p in range(1, P - 1))
                m.add(inner_g1 == inner_g2)

        # 8b (hard, Phase 1 only): unpaired same-teacher group lessons must be at
        # period 0 (first) or period P-1 (last) — no middle periods allowed.
        # This gives Phase 2 strong hints for edge placement.
        if with_edge and edge_set and P >= 3:
            for a_i in edge_set:
                for d in range(D):
                    for p in range(1, P - 1):
                        m.add(xb_[a_i, d, p] == 0)
                        if alt_count[a_i]:
                            m.add(xa_[a_i, d, p] == 0)
                            m.add(xb2_[a_i, d, p] == 0)

        # Objective: same-teacher g1+g2 on same day (same as Phase 2 obj_same_day).
        # Without this, Phase 1 finds any feasible placement → g1/g2 land on random
        # days → Phase 2 hints are wrong → 25s isn't enough to fix them.
        same_day_obj: list = []
        for (cls_pk, subj_pk), gmap in group_map.items():
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1 = gmap[g_nums[0]]
            a_g2 = gmap[g_nums[1]]
            if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                continue  # cross-teacher → forced to same slot already
            for d in range(D):
                g1_vars = [xb_[a_g1, d, p] for p in range(P)]
                g2_vars = [xb_[a_g2, d, p] for p in range(P)]
                if alt_count[a_g1]:
                    g1_vars += [xa_[a_g1, d, p] for p in range(P)]
                if alt_count[a_g2]:
                    g2_vars += [xa_[a_g2, d, p] for p in range(P)]
                has_g1 = m.new_bool_var(f'p1_hg1_{a_g1}_{d}')
                has_g2 = m.new_bool_var(f'p1_hg2_{a_g2}_{d}')
                m.add_max_equality(has_g1, g1_vars)
                m.add_max_equality(has_g2, g2_vars)
                both = m.new_bool_var(f'p1_both_{a_g1}_{a_g2}_{d}')
                m.add_min_equality(both, [has_g1, has_g2])
                same_day_obj.append(both)

        if same_day_obj:
            m.maximize(cp_model.LinearExpr.Sum(same_day_obj))

        return m, xb_, xa_, xb2_

    solver1 = cp_model.CpSolver()
    solver1.parameters.max_time_in_seconds = 20.0
    solver1.parameters.num_search_workers = 8
    solver1.parameters.log_search_progress = False

    for with_pairing, with_edge in ((True, True), (True, False), (False, True), (False, False)):
        m, xb_, xa_, xb2_ = _build(with_pairing, with_edge)
        status = solver1.solve(m)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
    else:
        return {}

    fixed: dict = {}
    for a_i in grp_set:
        fixed[a_i] = {'base': [], 'xa': [], 'xb2': []}
        for d in range(D):
            for p in range(P):
                if solver1.value(xb_[a_i, d, p]):
                    fixed[a_i]['base'].append((d, p))
                if alt_count[a_i]:
                    if solver1.value(xa_[a_i, d, p]):
                        fixed[a_i]['xa'].append((d, p))
                    if solver1.value(xb2_[a_i, d, p]):
                        fixed[a_i]['xb2'].append((d, p))
    return fixed


def generate(schedule_id: int) -> tuple:
    import time
    _t0 = time.time()
    def _log(msg): print(f'[GEN +{time.time()-_t0:.1f}s] {msg}', flush=True)

    schedule = Schedule.objects.get(pk=schedule_id)
    D = schedule.days_per_week
    P = schedule.lessons_per_day

    assignments = list(
        TeacherSubject.objects.select_related(
            'teacher', 'subject', 'school_class', 'school_class__home_room'
        ).all()
    )
    teachers = list(Teacher.objects.all())
    classes  = list(SchoolClass.objects.select_related('home_room').all())
    rooms    = list(Room.objects.select_related('subject').all())

    specialized_capacity: dict = defaultdict(int)
    for r in rooms:
        if r.subject_id is not None:
            specialized_capacity[r.subject_id] += r.max_simultaneous

    class_assignments   = {c.pk: [] for c in classes}
    teacher_assignments = {t.pk: [] for t in teachers}
    for a_i, a in enumerate(assignments):
        class_assignments[a.school_class_id].append(a_i)
        teacher_assignments[a.teacher_id].append(a_i)

    group_map: dict = defaultdict(dict)
    for a_i, a in enumerate(assignments):
        if a.group is not None:
            group_map[(a.school_class_id, a.subject_id)][a.group] = a_i

    # canonical[c.pk]: one representative assignment per (class, subject).
    # For grouped subjects: one rep regardless of same/different teacher.
    # Used for uniform-load and no-window constraints (student-perspective count).
    seen_subject_keys: set = set()
    canonical: dict = {c.pk: [] for c in classes}
    for a_i, a in enumerate(assignments):
        if a.group is None:
            canonical[a.school_class_id].append(a_i)
        else:
            key = (a.school_class_id, a.subject_id)
            if key not in seen_subject_keys:
                seen_subject_keys.add(key)
                canonical[a.school_class_id].append(a_i)

    base_count: dict = {}
    alt_count:  dict = {}
    for a_i, a in enumerate(assignments):
        h = float(a.hours_per_week)
        base_count[a_i] = int(h)
        alt_count[a_i]  = 1 if (h - int(h)) >= 0.5 else 0

    class_total_A = {
        c.pk: sum(base_count[a_i] + alt_count[a_i] for a_i in canonical[c.pk])
        for c in classes
    }
    # -------------------------------------------------------------------------
    model = cp_model.CpModel()

    # xb[a_i, d, p]  -- base lesson, both weeks
    # xa[a_i, d, p]  -- alt lesson, week A only  (0 int if no alt)
    # xb2[a_i, d, p] -- alt lesson, week B only  (0 int if no alt)
    xb:  dict = {}
    xa:  dict = {}
    xb2: dict = {}
    for a_i in range(len(assignments)):
        for d in range(D):
            for p in range(P):
                xb[a_i, d, p] = model.new_bool_var(f'xb{a_i}_{d}_{p}')
                if alt_count[a_i]:
                    xa[a_i, d, p]  = model.new_bool_var(f'xa{a_i}_{d}_{p}')
                    xb2[a_i, d, p] = model.new_bool_var(f'xb2{a_i}_{d}_{p}')
                else:
                    xa[a_i, d, p]  = 0
                    xb2[a_i, d, p] = 0

    # Return list of BoolVars for assignment a_i at slot (d,p) in week A / B.
    # Always returns at least [xb_var]; appends xa or xb2 when present.
    def vars_A(a_i, d, p):
        v = [xb[a_i, d, p]]
        if alt_count[a_i]:
            v.append(xa[a_i, d, p])
        return v

    def vars_B(a_i, d, p):
        v = [xb[a_i, d, p]]
        if alt_count[a_i]:
            v.append(xb2[a_i, d, p])
        return v

    # Scalar LinearExpr helpers for single-assignment occupancy checks.
    def occ_A(a_i, d, p):
        return cp_model.LinearExpr.Sum(vars_A(a_i, d, p))

    def occ_B(a_i, d, p):
        return cp_model.LinearExpr.Sum(vars_B(a_i, d, p))

    # 1. Lesson counts
    for a_i in range(len(assignments)):
        model.add(sum(xb[a_i, d, p] for d in range(D) for p in range(P)) == base_count[a_i])
        if alt_count[a_i]:
            # alt lesson goes to exactly one of (week A, week B) in exactly one slot
            model.add(sum(xa[a_i, d, p] + xb2[a_i, d, p]
                          for d in range(D) for p in range(P)) == 1)
            # xa and xb2 can't both be 1 in the same slot
            for d in range(D):
                for p in range(P):
                    model.add(xa[a_i, d, p] + xb2[a_i, d, p] <= 1)

    # 2. Class conflict (week A and week B separately)
    for c in classes:
        whole = [a_i for a_i in class_assignments[c.pk] if assignments[a_i].group is None]
        grp: dict = defaultdict(list)
        for a_i in class_assignments[c.pk]:
            if assignments[a_i].group is not None:
                grp[assignments[a_i].group].append(a_i)

        for d in range(D):
            for p in range(P):
                for vfn in (vars_A, vars_B):
                    if whole:
                        wvars = [v for a_i in whole for v in vfn(a_i, d, p)]
                        model.add(cp_model.LinearExpr.Sum(wvars) <= 1)
                    for g_ais in grp.values():
                        gvars = [v for a_i in g_ais for v in vfn(a_i, d, p)]
                        model.add(cp_model.LinearExpr.Sum(gvars) <= 1)
                    for wc in whole:
                        for g_ai in (ai for g_ais in grp.values() for ai in g_ais):
                            mixed = vfn(wc, d, p) + vfn(g_ai, d, p)
                            model.add(cp_model.LinearExpr.Sum(mixed) <= 1)

    # 3. Teacher <= 1 lesson per slot per week
    for t in teachers:
        st = teacher_assignments[t.pk]
        for d in range(D):
            for p in range(P):
                tvA = [v for a_i in st for v in vars_A(a_i, d, p)]
                tvB = [v for a_i in st for v in vars_B(a_i, d, p)]
                model.add(cp_model.LinearExpr.Sum(tvA) <= 1)
                model.add(cp_model.LinearExpr.Sum(tvB) <= 1)

    # 4. Teacher availability
    for a_i, a in enumerate(assignments):
        mask = a.teacher.available_days[:D].ljust(D, '0')
        for d in range(D):
            if mask[d] != '1':
                for p in range(P):
                    model.add(xb[a_i, d, p] == 0)
                    if alt_count[a_i]:
                        model.add(xa[a_i, d, p] == 0)
                        model.add(xb2[a_i, d, p] == 0)

    # 5. max_lessons_per_day (both weeks)
    for t in teachers:
        st = teacher_assignments[t.pk]
        for d in range(D):
            tdA = [v for a_i in st for p in range(P) for v in vars_A(a_i, d, p)]
            tdB = [v for a_i in st for p in range(P) for v in vars_B(a_i, d, p)]
            model.add(cp_model.LinearExpr.Sum(tdA) <= t.max_lessons_per_day)
            model.add(cp_model.LinearExpr.Sum(tdB) <= t.max_lessons_per_day)

    # 6. Group co-scheduling (same slot for all groups, both xb and xa/xb2)
    for (cls_pk, subj_pk), gmap in group_map.items():
        group_ais = list(gmap.values())
        if len(group_ais) >= 2:
            teacher_ids = {assignments[a_i].teacher_id for a_i in group_ais}
            if len(teacher_ids) > 1:
                rep = group_ais[0]
                for other in group_ais[1:]:
                    for d in range(D):
                        for p in range(P):
                            model.add(xb[rep, d, p] == xb[other, d, p])
                            r_xa  = xa[rep, d, p];   o_xa  = xa[other, d, p]
                            r_xb2 = xb2[rep, d, p];  o_xb2 = xb2[other, d, p]
                            if not (isinstance(r_xa, int) and isinstance(o_xa, int)):
                                model.add(r_xa == o_xa)
                            if not (isinstance(r_xb2, int) and isinstance(o_xb2, int)):
                                model.add(r_xb2 == o_xb2)

    # 6b. Subject-level pairing consistency (hard):
    # Cross-teacher g1↔g2 pairing must be symmetric per subject: slots where teacher T's
    # g1 is co-scheduled with a non-T g2 must equal slots where T's g2 is co-scheduled
    # with a non-T g1.  Prevents g1 of one subject being paired while g2 is not.

    # Pre-index group_map by class to avoid O(classes × group_map) scan.
    cls_group_map: dict = defaultdict(dict)
    for (cls_pk, subj_pk), gmap in group_map.items():
        cls_group_map[cls_pk][subj_pk] = gmap

    for c in classes:
        t_subj_pairs: dict = defaultdict(list)  # t_id → [(a_g1, a_g2), ...]
        for subj_pk, gmap in cls_group_map[c.pk].items():
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1, a_g2 = gmap[g_nums[0]], gmap[g_nums[1]]
            if assignments[a_g1].teacher_id == assignments[a_g2].teacher_id:
                t_subj_pairs[assignments[a_g1].teacher_id].append((a_g1, a_g2))

        for t_id, subj_pairs in t_subj_pairs.items():
            t_g1_nums = {assignments[a_g1].group for a_g1, _ in subj_pairs}
            t_g2_nums = {assignments[a_g2].group for _, a_g2 in subj_pairs}

            cross_g2_ais: list = []  # non-T g2 → partner when T teaches g1
            cross_g1_ais: list = []  # non-T g1 → partner when T teaches g2
            for a_i in class_assignments[c.pk]:
                a = assignments[a_i]
                if a.teacher_id == t_id or a.group is None:
                    continue
                if a.group in t_g2_nums:
                    cross_g2_ais.append(a_i)
                elif a.group in t_g1_nums:
                    cross_g1_ais.append(a_i)

            if not cross_g2_ais and not cross_g1_ais:
                continue

            # Per-slot cross-partner presence (shared across all subjects of this teacher)
            has_cg2: dict = {}
            has_cg1: dict = {}
            for d in range(D):
                for p in range(P):
                    if cross_g2_ais:
                        v = model.new_bool_var(f'hcg2_{c.pk}_{t_id}_{d}_{p}')
                        model.add_max_equality(v, [xb[a_j, d, p] for a_j in cross_g2_ais])
                        has_cg2[d, p] = v
                    if cross_g1_ais:
                        v = model.new_bool_var(f'hcg1_{c.pk}_{t_id}_{d}_{p}')
                        model.add_max_equality(v, [xb[a_j, d, p] for a_j in cross_g1_ais])
                        has_cg1[d, p] = v

            for a_g1, a_g2 in subj_pairs:
                pc_g1: list = []
                pc_g2: list = []
                for d in range(D):
                    for p in range(P):
                        cg2 = has_cg2.get((d, p))
                        if cg2 is not None:
                            pv = model.new_bool_var(f'pair_g1_{a_g1}_{d}_{p}')
                            model.add_min_equality(pv, [xb[a_g1, d, p], cg2])
                            pc_g1.append(pv)
                        cg1 = has_cg1.get((d, p))
                        if cg1 is not None:
                            pv = model.new_bool_var(f'pair_g2_{a_g2}_{d}_{p}')
                            model.add_min_equality(pv, [xb[a_g2, d, p], cg1])
                            pc_g2.append(pv)

                if pc_g1 or pc_g2:
                    model.add(
                        cp_model.LinearExpr.Sum(pc_g1) ==
                        cp_model.LinearExpr.Sum(pc_g2)
                    )

    # obj_anchor: reward co-scheduling of same-teacher groups from DIFFERENT teachers.
    # For each class with 2+ teachers that each have same-teacher g1+g2 subjects:
    # reward slot (d,p) where T_A has any g1 AND T_B has any g2, and vice versa.
    # This is the main driver for cross-subject cross-teacher pairing optimization.
    obj_anchor: list = []
    for c in classes:
        # Collect same-teacher group subjects per teacher in this class
        t_g1s: dict = defaultdict(list)  # teacher_id → [a_i for g1 assignments]
        t_g2s: dict = defaultdict(list)  # teacher_id → [a_i for g2 assignments]
        for (cls_pk, subj_pk), gmap in group_map.items():
            if cls_pk != c.pk:
                continue
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1 = gmap[g_nums[0]]
            a_g2 = gmap[g_nums[1]]
            if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                continue  # cross-teacher → already co-scheduled (constraint 6)
            t_id = assignments[a_g1].teacher_id
            t_g1s[t_id].append(a_g1)
            t_g2s[t_id].append(a_g2)

        t_ids = list(t_g1s.keys())
        if len(t_ids) < 2:
            continue

        for i in range(len(t_ids)):
            for j in range(i + 1, len(t_ids)):
                ta, tb = t_ids[i], t_ids[j]
                for d in range(D):
                    for p in range(P):
                        for wk_label, week_vars in (('A', vars_A), ('B', vars_B)):
                            # T_A g1 at slot AND T_B g2 at slot → reward
                            v_ta_g1 = [v for a_i in t_g1s[ta] for v in week_vars(a_i, d, p)]
                            v_tb_g2 = [v for a_i in t_g2s[tb] for v in week_vars(a_i, d, p)]
                            if v_ta_g1 and v_tb_g2:
                                has_a = model.new_bool_var(f'anc_ag1_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                has_b = model.new_bool_var(f'anc_bg2_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_max_equality(has_a, v_ta_g1)
                                model.add_max_equality(has_b, v_tb_g2)
                                both = model.new_bool_var(f'anc_ab_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_min_equality(both, [has_a, has_b])
                                obj_anchor.append(both)
                            # T_A g2 at slot AND T_B g1 at slot → reward
                            v_ta_g2 = [v for a_i in t_g2s[ta] for v in week_vars(a_i, d, p)]
                            v_tb_g1 = [v for a_i in t_g1s[tb] for v in week_vars(a_i, d, p)]
                            if v_ta_g2 and v_tb_g1:
                                has_c = model.new_bool_var(f'anc_ag2_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                has_d_ = model.new_bool_var(f'anc_bg1_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_max_equality(has_c, v_ta_g2)
                                model.add_max_equality(has_d_, v_tb_g1)
                                both2 = model.new_bool_var(f'anc_ba_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_min_equality(both2, [has_c, has_d_])
                                obj_anchor.append(both2)

    # 6c. Same-day scheduling for same-teacher groups — SOFT preference via objective.
    #
    #  Hard equality (g1_day == g2_day) was INFEASIBLE when combined with anchor
    #  pairing and teacher load constraints.  Instead: reward the solver for placing
    #  g1 and g2 on the same days; let it skip same-day when there is no room.
    #
    #  obj_same_day accumulates one bool per (group_pair, day, week) that equals 1
    #  when both g1 AND g2 have a lesson on that day in that week.
    obj_same_day: list = []
    for (cls_pk, subj_pk), gmap in group_map.items():
        g_nums = sorted(gmap.keys())
        if len(g_nums) < 2:
            continue
        a_g1 = gmap[g_nums[0]]
        a_g2 = gmap[g_nums[1]]
        if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
            continue  # different teachers → already same-slot (constraint 6)

        for d in range(D):
            g1_A = [v for p in range(P) for v in vars_A(a_g1, d, p)]
            g2_A = [v for p in range(P) for v in vars_A(a_g2, d, p)]
            has_g1_A = model.new_bool_var(f'hasG1A_{a_g1}_{d}')
            has_g2_A = model.new_bool_var(f'hasG2A_{a_g2}_{d}')
            model.add_max_equality(has_g1_A, g1_A)
            model.add_max_equality(has_g2_A, g2_A)
            both_A = model.new_bool_var(f'bothA_{a_g1}_{a_g2}_{d}')
            model.add_min_equality(both_A, [has_g1_A, has_g2_A])
            obj_same_day.append(both_A)

            g1_B = [v for p in range(P) for v in vars_B(a_g1, d, p)]
            g2_B = [v for p in range(P) for v in vars_B(a_g2, d, p)]
            has_g1_B = model.new_bool_var(f'hasG1B_{a_g1}_{d}')
            has_g2_B = model.new_bool_var(f'hasG2B_{a_g2}_{d}')
            model.add_max_equality(has_g1_B, g1_B)
            model.add_max_equality(has_g2_B, g2_B)
            both_B = model.new_bool_var(f'bothB_{a_g1}_{a_g2}_{d}')
            model.add_min_equality(both_B, [has_g1_B, has_g2_B])
            obj_same_day.append(both_B)

    # 7. Uniform class load per day.
    #    lo  = base-lessons-only // D  (alt lessons belong to exactly one week,
    #          so requiring them in *both* week-A and week-B lower bounds is
    #          infeasible when alt_count ≥ 3 — each alt xa+xb2=1, can't double-count)
    #    hi  = ceil((base + alt) / D)  (upper bound uses total since at most all
    #          alt lessons land in the same week)
    for c in classes:
        sc = canonical[c.pk]
        if not sc:
            continue
        base_total = sum(base_count[a_i] for a_i in sc)
        alt_total  = sum(alt_count[a_i]  for a_i in sc)
        grand_total = base_total + alt_total
        if grand_total == 0:
            continue
        lo = base_total // D
        hi = ceil(grand_total / D)
        for d in range(D):
            dA = ([xb[a_i, d, p] for a_i in sc for p in range(P)]
                  + [xa[a_i, d, p] for a_i in sc for p in range(P) if alt_count[a_i]])
            s = cp_model.LinearExpr.Sum(dA)
            model.add(s >= lo)
            model.add(s <= hi)
            dB = ([xb[a_i, d, p] for a_i in sc for p in range(P)]
                  + [xb2[a_i, d, p] for a_i in sc for p in range(P) if alt_count[a_i]])
            s = cp_model.LinearExpr.Sum(dB)
            model.add(s >= lo)
            model.add(s <= hi)

    # 8. No windows: lessons must be consecutive starting from period 0.
    #    Uses OR-based combined occupancy over ALL assignments for the class
    #    (including both g1 and g2 of same-teacher groups), so every lesson
    #    visible in the class schedule is forced into a contiguous block.
    #    Previously this caused INFEASIBLE when combined with hard constraints
    #    6b and 9; those are now soft, so OR-based is safe again.
    #
    # 8b. Unpaired group lessons (only one group has a teacher assigned) must be
    #    placed FIRST or LAST in the day.  A group lesson without a partner creates
    #    a free period for the other half of the class; putting it at the edge
    #    lets those students arrive late or leave early instead of sitting idle.
    #    Implementation: if an unpaired lesson lands at period p (1 ≤ p ≤ P-2),
    #    force slot[d, p+1] = 0 (no class lesson after it).  Constraint 8
    #    compactness then guarantees p is the last slot of the day.

    unpaired_all: set = set()
    for a_i, a in enumerate(assignments):
        if a.group is None:
            continue
        cls_ais = class_assignments[a.school_class_id]
        can_pair = any(
            assignments[a_j].group is not None
            and assignments[a_j].group != a.group
            and assignments[a_j].teacher_id != a.teacher_id
            for a_j in cls_ais
            if a_j != a_i
        )
        if not can_pair:
            unpaired_all.add(a_i)

    # Build slot_A/slot_B per class (no-window constraint 8).
    # Saved in cls_slot_A/B so 8b can be built AFTER Phase 1 (see below).
    cls_slot_A: dict = {}
    cls_slot_B: dict = {}
    for c in classes:
        all_ais = class_assignments[c.pk]
        if not all_ais:
            continue
        slot_A: dict = {}
        slot_B: dict = {}
        for d in range(D):
            for p in range(P):
                a_vars = [v for a_i in all_ais for v in vars_A(a_i, d, p)]
                b_vars = [v for a_i in all_ais for v in vars_B(a_i, d, p)]
                slot_A[d, p] = model.new_bool_var(f'occA_{c.pk}_{d}_{p}')
                slot_B[d, p] = model.new_bool_var(f'occB_{c.pk}_{d}_{p}')
                model.add_max_equality(slot_A[d, p], a_vars)
                model.add_max_equality(slot_B[d, p], b_vars)
        for d in range(D):
            for p in range(1, P):
                model.add(slot_A[d, p] <= slot_A[d, p - 1])
                model.add(slot_B[d, p] <= slot_B[d, p - 1])
        cls_slot_A[c.pk] = slot_A
        cls_slot_B[c.pk] = slot_B
        # 8b is built AFTER Phase 1 (below) so we know which slots are truly unpaired

    # 9. No same-day repeats — hard "at most 1/day" for non-double subjects.
    for a_i, a in enumerate(assignments):
        if a.subject.can_be_double:
            continue
        total_lessons = base_count[a_i] + alt_count[a_i]
        if total_lessons <= 1:
            continue  # only 1 lesson per week → doubling impossible
        for d in range(D):
            day_A = [v for p in range(P) for v in vars_A(a_i, d, p)]
            day_B = [v for p in range(P) for v in vars_B(a_i, d, p)]
            model.add(cp_model.LinearExpr.Sum(day_A) <= 1)
            model.add(cp_model.LinearExpr.Sum(day_B) <= 1)

    # 10. Specialized room capacity
    subj_assignments: dict = defaultdict(list)
    for a_i, a in enumerate(assignments):
        if a.subject_id in specialized_capacity:
            subj_assignments[a.subject_id].append(a_i)
    for subj_pk, ais in subj_assignments.items():
        subj_obj = assignments[ais[0]].subject
        cap = (specialized_capacity[subj_pk] if subj_obj.allow_shared_room
               else sum(1 for r in rooms if r.subject_id == subj_pk))
        for d in range(D):
            for p in range(P):
                model.add(sum(occ_A(a_i, d, p) for a_i in ais) <= cap)
                model.add(sum(occ_B(a_i, d, p) for a_i in ais) <= cap)

    # 11. Grade-incompatible classes can't share a specialized room
    for subj_pk, ais in subj_assignments.items():
        subj_obj = assignments[ais[0]].subject
        if not subj_obj.allow_shared_room:
            continue

        grade_ais: dict = defaultdict(list)
        for a_i in ais:
            grade_ais[assignments[a_i].school_class.grade].append(a_i)
        grades = sorted(grade_ais)
        if len(grades) < 2:
            continue

        has_A: dict = {}
        has_B: dict = {}
        for g in grades:
            g_ais = grade_ais[g]
            for d in range(D):
                for p in range(P):
                    vA = model.new_bool_var(f'hgA_{subj_pk}_{g}_{d}_{p}')
                    has_A[g, d, p] = vA
                    model.add(vA <= sum(occ_A(a_i, d, p) for a_i in g_ais))
                    for a_i in g_ais:
                        model.add(vA >= xb[a_i, d, p])
                        if alt_count[a_i]:
                            model.add(vA >= xa[a_i, d, p])

                    vB = model.new_bool_var(f'hgB_{subj_pk}_{g}_{d}_{p}')
                    has_B[g, d, p] = vB
                    model.add(vB <= sum(occ_B(a_i, d, p) for a_i in g_ais))
                    for a_i in g_ais:
                        model.add(vB >= xb[a_i, d, p])
                        if alt_count[a_i]:
                            model.add(vB >= xb2[a_i, d, p])

        for i, g1 in enumerate(grades):
            for g2 in grades[i + 1:]:
                if abs(g1 - g2) > subj_obj.max_grade_diff:
                    for d in range(D):
                        for p in range(P):
                            model.add(has_A[g1, d, p] + has_A[g2, d, p] <= 1)
                            model.add(has_B[g1, d, p] + has_B[g2, d, p] <= 1)

    # -------------------------------------------------------------------------
    # Phase 1: schedule group lessons with cross-teacher pairing, then hint Phase 2.
    _log(f'{len(assignments)} assignments, '
         f'obj_anchor={len(obj_anchor)} obj_same_day={len(obj_same_day)} '
         f'unpaired_structural={len(unpaired_all)}')

    # Phase 1a: edge constraints only for structurally-unpaired lessons.
    # Running with edge for unpaired_all (even if small) prevents the solver from
    # masking future-unpaired slots via opportunistic cross-subject pairing.
    _log('phase1a start')
    grp_hints1 = _solve_group_phase(
        assignments, group_map, class_assignments, teacher_assignments,
        base_count, alt_count, classes, teachers, D, P,
        unpaired_edge=unpaired_all,
    )
    _log('phase1a done')

    # Detect actually_unpaired: group assignments where at least one slot in Phase 1a
    # has no cross-teacher partner (different group, different teacher) in the same class.
    actually_unpaired: set = set(unpaired_all)  # start with structural unpaired
    if P >= 3 and grp_hints1:
        grp_slots: dict = {}   # a_i -> set of (d,p) scheduled in Phase 1a
        for a_i, slots in grp_hints1.items():
            grp_slots[a_i] = set(slots['base'])

        # Reverse index: (cls_pk, d, p) → list of group a_i scheduled there
        slot_to_grp: dict = defaultdict(list)
        for a_i, dps in grp_slots.items():
            a = assignments[a_i]
            if a.group is not None:
                for dp in dps:
                    slot_to_grp[a.school_class_id, dp].append(a_i)

        for a_i, slots_i in grp_slots.items():
            if a_i in actually_unpaired:
                continue
            a = assignments[a_i]
            if a.group is None:
                continue
            cls_pk = a.school_class_id
            for dp in slots_i:
                paired = any(
                    assignments[a_j].group != a.group
                    and assignments[a_j].teacher_id != a.teacher_id
                    for a_j in slot_to_grp.get((cls_pk, dp), [])
                    if a_j != a_i
                )
                if not paired:
                    actually_unpaired.add(a_i)
                    break

    _log(f'actually_unpaired={len(actually_unpaired)} after phase1a')
    for a_i in sorted(actually_unpaired):
        a = assignments[a_i]
        _log(f'  unp #{a_i}: {a.school_class} | {a.subject} гр.{a.group} | {a.teacher}')

    # Phase 1b: with hard edge constraints for actually_unpaired → stronger hints for Phase 2.
    _log('phase1b start')
    grp_hints = _solve_group_phase(
        assignments, group_map, class_assignments, teacher_assignments,
        base_count, alt_count, classes, teachers, D, P,
        unpaired_edge=actually_unpaired,
    )
    _log('phase1b done, applying hints')

    # Phase 1b hints help Phase 2 find a good pairing arrangement quickly.
    # Edge placement for unpaired lessons is done in post-processing (see below).
    for a_i, slots in grp_hints.items():
        for d, p in slots['base']:
            model.add_hint(xb[a_i, d, p], 1)
        if alt_count[a_i]:
            for d, p in slots['xa']:
                model.add_hint(xa[a_i, d, p], 1)
            for d, p in slots['xb2']:
                model.add_hint(xb2[a_i, d, p], 1)

    # -------------------------------------------------------------------------
    # Objective — three tiers (PAIR >> CLUSTER >> EDGE):
    #
    # 1. PAIR_WEIGHT (1000): pairing bonuses — never sacrifice a grouping bond.
    #    + obj_same_day: split groups from same teacher on same day
    #    + obj_anchor:   follower-slot aligned with anchor teacher
    #
    # 2. CLUSTER_WEIGHT (100): anti-clustering for unpaired lessons.
    #    When a class has unpaired lessons from multiple subjects (e.g. 4А:
    #    Укр.мова, ЯДС, Укр.літ — all taught by one teacher to both groups),
    #    reward NOT scheduling two different subject-groups on the same day.
    #    This spreads them so each day has at most one g1+g2 pair, leaving
    #    both edge slots free for that pair.
    #
    # 3. EDGE_WEIGHT (100): V-shaped edge reward for each unpaired lesson.
    #    Reward = (P-1 − 2·min(p, P-1−p)) — max at period 0 and P−1, zero
    #    at centre.  Naturally pushes g1 → period 0, g2 → period P−1.
    #    Must be large enough that the 2-point gap between adjacent periods
    #    outweighs any residual slack in FEASIBLE (non-OPTIMAL) solutions.
    PAIR_WEIGHT    = 1000
    CLUSTER_WEIGHT = 100
    EDGE_WEIGHT    = 100

    all_obj_vars: list = list(obj_same_day) + list(obj_anchor)
    all_obj_wts:  list = ([PAIR_WEIGHT] * len(obj_same_day)
                          + [PAIR_WEIGHT] * len(obj_anchor))

    if actually_unpaired and P >= 3:
        # --- tier 2: anti-clustering ---
        cls_subj_unp: dict = defaultdict(lambda: defaultdict(list))
        for a_i in actually_unpaired:
            a = assignments[a_i]
            cls_subj_unp[a.school_class_id][a.subject_id].append(a_i)

        for cls_pk, subj_dict in cls_subj_unp.items():
            subj_keys = list(subj_dict.keys())
            if len(subj_keys) < 2:
                continue  # only one subject → no clustering possible

            # "subj_on_day[subj, d]" = 1 iff class cls_pk has any lesson of this
            # unpaired subject on day d.
            sod: dict = {}
            for subj_pk, ais in subj_dict.items():
                for d in range(D):
                    v = model.new_bool_var(f'sod_{cls_pk}_{subj_pk}_{d}')
                    model.add(v <= sum(xb[ai, d, p] for ai in ais for p in range(P)))
                    for ai in ais:
                        for p in range(P):
                            model.add(v >= xb[ai, d, p])
                    sod[subj_pk, d] = v

            # For every pair of subjects and every day: reward NOT being together.
            # not_tog = 1  iff  NOT (subj_i on day d  AND  subj_j on day d)
            # Constraints: not_tog >= 1 − gi
            #              not_tog >= 1 − gj
            #              not_tog + gi + gj <= 2
            for i in range(len(subj_keys)):
                for j in range(i + 1, len(subj_keys)):
                    sk_i, sk_j = subj_keys[i], subj_keys[j]
                    for d in range(D):
                        gi = sod[sk_i, d]
                        gj = sod[sk_j, d]
                        nt = model.new_bool_var(f'ntog_{cls_pk}_{i}_{j}_{d}')
                        model.add(nt >= 1 - gi)
                        model.add(nt >= 1 - gj)
                        model.add(nt + gi + gj <= 2)
                        all_obj_vars.append(nt)
                        all_obj_wts.append(CLUSTER_WEIGHT)

        # --- tier 3: edge reward ---
        # Binary reward: only period 0 (always first) or the ACTUAL last occupied
        # period of the class day earns EDGE_WEIGHT.  The old V-shaped formula was
        # wrong: for P=7 it scores p=1 (reward=4) higher than p=4 (reward=2), so
        # when a class has 5 lessons (0–4) the solver preferred ур.2 over the real
        # last period ур.5 — the opposite of what we want.
        for a_i in actually_unpaired:
            a = assignments[a_i]
            slot_A = cls_slot_A.get(a.school_class_id)
            if slot_A is None:
                continue
            for d in range(D):
                # Period 0 — always the first lesson of the day
                all_obj_vars.append(xb[a_i, d, 0])
                all_obj_wts.append(EDGE_WEIGHT)
                # Periods 1..P-2 — reward only if this is the actual last of the day
                # (no class lesson at period p+1 → this lesson is last)
                for p in range(1, P - 1):
                    is_last = model.new_bool_var(f'el_{a_i}_{d}_{p}')
                    model.add_min_equality(is_last, [xb[a_i, d, p], slot_A[d, p + 1].Not()])
                    all_obj_vars.append(is_last)
                    all_obj_wts.append(EDGE_WEIGHT)
                # Period P-1 — always the last possible slot
                all_obj_vars.append(xb[a_i, d, P - 1])
                all_obj_wts.append(EDGE_WEIGHT)

    if all_obj_vars:
        model.maximize(cp_model.LinearExpr.WeightedSum(all_obj_vars, all_obj_wts))

    # -------------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 40.0
    solver.parameters.num_search_workers = 8
    solver.parameters.log_search_progress = False

    _log('phase2 solve start')
    status = solver.solve(model)
    _log(f'phase2 done: {solver.status_name(status)} in {solver.wall_time:.1f}s')

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        try:
            issues = _diagnose(schedule, assignments, base_count, alt_count, canonical,
                               class_total_A, teachers, teacher_assignments, classes, rooms,
                               specialized_capacity, D, P)
        except Exception as e:
            issues = [f'(помилка діагностики: {e})']
        lines = [f'Розвязок не знайдено ({solver.status_name(status)})']
        if issues:
            lines.append('Можливі причини:')
            lines.extend(f'- {i}' for i in issues)
        else:
            lines.append('Базові перевірки пройшли -- причина у взаємодії обмежень.')
            lines.append(_summary(schedule, assignments, base_count, alt_count, canonical,
                                  class_total_A, teachers, teacher_assignments, classes, D, P))
        return False, '\n'.join(lines)

    # -------------------------------------------------------------------------
    # Debug: show where each unpaired lesson landed and whether it's at an edge.
    if actually_unpaired:
        # Build cls_periods: for each (cls_pk, day) → sorted list of scheduled periods
        cls_periods_dbg: dict = defaultdict(list)
        for a_i2, a2 in enumerate(assignments):
            for d2 in range(D):
                for p2 in range(P):
                    if solver.value(xb[a_i2, d2, p2]):
                        cls_periods_dbg[a2.school_class_id, d2].append(p2)
        for key in cls_periods_dbg:
            cls_periods_dbg[key].sort()

        day_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Нд']
        for a_i in sorted(actually_unpaired):
            a = assignments[a_i]
            for d in range(D):
                for p in range(P):
                    if solver.value(xb[a_i, d, p]):
                        day_slots = cls_periods_dbg[a.school_class_id, d]
                        p_first = day_slots[0] if day_slots else -1
                        p_last  = day_slots[-1] if day_slots else -1
                        edge = '✓' if p in (p_first, p_last) else f'✗ (треба {p_first} або {p_last})'
                        _log(f'  unp #{a_i} {a.school_class} {a.subject} гр.{a.group} '
                             f'| {day_names[d]} ур.{p+1} {edge}')

    # -------------------------------------------------------------------------
    # Room assignment
    # -------------------------------------------------------------------------
    room_usage_A: dict = {}   # (d,p) -> {room_pk: [grades]}
    room_usage_B: dict = {}
    home_room_ids = {c.home_room_id for c in classes if c.home_room_id}

    # Precompute room lookup structures to avoid O(rooms) scan per lesson
    rooms_by_subject: dict = defaultdict(list)
    general_rooms: list = []
    for r in rooms:
        if r.subject_id is not None:
            rooms_by_subject[r.subject_id].append(r)
        else:
            general_rooms.append(r)

    def _can_use(r, subject, grade, usage_dp):
        grades_here = list(dict.fromkeys(usage_dp.get(r.pk, [])))
        if len(grades_here) >= r.max_simultaneous:
            return False
        if grades_here:
            if not subject.allow_shared_room:
                return False
            if any(abs(grade - g) > subject.max_grade_diff for g in grades_here):
                return False
        return True

    def _mark(r, grade, usage_dp):
        usage_dp.setdefault(r.pk, []).append(grade)

    def find_room(subject, school_class, usage_dp):
        grade = school_class.grade
        specialized = rooms_by_subject.get(subject.pk, [])
        for r in specialized:
            if _can_use(r, subject, grade, usage_dp):
                return r
        if specialized:
            return None
        if school_class.home_room_id:
            hr = school_class.home_room
            if hr.subject_id is None and _can_use(hr, subject, grade, usage_dp):
                return hr
        for r in general_rooms:
            if r.pk not in home_room_ids and _can_use(r, subject, grade, usage_dp):
                return r
        return None

    shared_subj_ids = {
        pk for pk, ais in subj_assignments.items()
        if assignments[ais[0]].subject.allow_shared_room
    }

    # Collect scheduled slots
    base_sched  = []   # (d, p, grade_key, a_i)
    alt_a_sched = []   # week A only
    alt_b_sched = []   # week B only

    for a_i, a in enumerate(assignments):

        gk = a.school_class.grade if a.subject_id in shared_subj_ids else 0
        for d in range(D):
            for p in range(P):
                if solver.value(xb[a_i, d, p]):
                    base_sched.append((d, p, gk, a_i))
                if alt_count[a_i]:
                    if solver.value(xa[a_i, d, p]):
                        alt_a_sched.append((d, p, gk, a_i))
                    if solver.value(xb2[a_i, d, p]):
                        alt_b_sched.append((d, p, gk, a_i))

    sort_key = lambda t: (t[0] * P + t[1], t[2])
    base_sched.sort(key=sort_key)
    alt_a_sched.sort(key=sort_key)
    alt_b_sched.sort(key=sort_key)

    Lesson.objects.filter(schedule=schedule).delete()
    to_create = []

    # Base lessons (weeks 0 and 1, same slot)
    for d, p, _, a_i in base_sched:
        a = assignments[a_i]
        uA = room_usage_A.setdefault((d, p), {})
        uB = room_usage_B.setdefault((d, p), {})
        room = find_room(a.subject, a.school_class, uA)
        if room:
            _mark(room, a.school_class.grade, uA)
            _mark(room, a.school_class.grade, uB)
        for wk in (0, 1):
            to_create.append(Lesson(
                schedule=schedule, school_class=a.school_class,
                subject=a.subject, teacher=a.teacher, room=room,
                day=d, period=p, week=wk, group=a.group,
            ))

    # Alt-A lessons (week 0 only)
    for d, p, _, a_i in alt_a_sched:
        a = assignments[a_i]
        uA = room_usage_A.setdefault((d, p), {})
        room = find_room(a.subject, a.school_class, uA)
        if room:
            _mark(room, a.school_class.grade, uA)
        to_create.append(Lesson(
            schedule=schedule, school_class=a.school_class,
            subject=a.subject, teacher=a.teacher, room=room,
            day=d, period=p, week=0, group=a.group,
        ))

    # Alt-B lessons (week 1 only)
    for d, p, _, a_i in alt_b_sched:
        a = assignments[a_i]
        uB = room_usage_B.setdefault((d, p), {})
        room = find_room(a.subject, a.school_class, uB)
        if room:
            _mark(room, a.school_class.grade, uB)
        to_create.append(Lesson(
            schedule=schedule, school_class=a.school_class,
            subject=a.subject, teacher=a.teacher, room=room,
            day=d, period=p, week=1, group=a.group,
        ))

    Lesson.objects.bulk_create(to_create)
    n_base  = len(base_sched)
    n_alt_a = len(alt_a_sched)
    n_alt_b = len(alt_b_sched)
    total   = n_base * 2 + n_alt_a + n_alt_b
    return (True,
            f'Готово! {total} уроків '
            f'({n_base} базових x2 + {n_alt_a} черг.А + {n_alt_b} черг.Б) '
            f'за {solver.wall_time:.1f}с')
